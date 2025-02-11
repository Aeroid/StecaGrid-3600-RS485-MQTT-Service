#!/usr/bin/env python3
# 
# Python service to serve MQTT topics based on RS485 readings from a StecaGrid 3600 inveter  
#   Uses serial connection to request inverter meter data every few seconds 
#   publishes the values to a (sub) topic via MQTT to a broker
import yaml                     # pip3 install pyyaml
import paho.mqtt.client as mqtt #  pip3 install paho-mqtt
import argparse
import sys
import re
import struct
import serial                   # pip3 install pyserial
import datetime
import time
from ctypes import c_ushort

DEBUG = False
config_file = 'config.yaml'
sys.stdout.reconfigure(encoding='latin1')

SERIAL_DEVICE   = "/dev/ttyS0"
SERIAL_BYTES    = serial.EIGHTBITS
SERIAL_PARITY   = serial.PARITY_NONE
SERIAL_SBIT     = serial.STOPBITS_ONE
SERIAL_BAUDRATE = 38400
SERIAL_TIMEOUT  = 1

# Recorded packets of StecaGrid SEM (id #123/0x7b) talking to StecaGrid 3600 (id #1) for replay
SG_NOMINAL_POWER = bytes.fromhex("02 01 00 10 01 7b b5 40 03 00 01 1d 72 30 95 03")
SG_PANEL_POWER   = bytes.fromhex("02 01 00 10 01 7b b5 40 03 00 01 22 77 12 ee 03")
SG_PANEL_VOLTAGE = bytes.fromhex("02 01 00 10 01 7b b5 40 03 00 01 23 78 78 e4 03")
SG_PANEL_CURRENT = bytes.fromhex("02 01 00 10 01 7b b5 40 03 00 01 24 79 a0 b6 03")
SG_VERSIONS      = bytes.fromhex("02 01 00 0c 01 7b c6 20 03 79 8c 03")
SG_SERIAL        = bytes.fromhex("02 01 00 10 01 7b b5 64 03 00 01 09 5e 85 6e 03")
SG_TIME          = bytes.fromhex("02 01 00 10 01 7b b5 64 03 00 01 05 5a 3a 44 03")
SG_DAILY_YIELD   = bytes.fromhex("02 01 00 10 01 7b b5 40 03 00 01 3c 91 e1 c9 03")
SG_TOTAL_YIELD   = bytes.fromhex("02 01 00 10 01 7b b5 64 03 00 01 f1 46 cc 79 03")
SG_AC_POWER      = bytes.fromhex("02 01 00 10 01 7b b5 40 03 00 01 29 7e 98 5b 03")
ID_AC_POWER      = 0x29

# ELECTRICITY_EXPORTED_TOTAL    = :2.8.0    # Total exported energy register (P-)
# CURRENT_ELECTRICITY_DELIVERY  = :2.7.0
# Q3D_EQUIPMENT_SERIALNUMBER    = :96.1.255 # Device Serialnumber

mapping = {
    "ELECTRICITY_EXPORTED_TOTAL":   SG_TOTAL_YIELD, # :2.8.0    # Total exported energy register (P-)
    "CURRENT_ELECTRICITY_DELIVERY": SG_AC_POWER,    # :2.7.0
    "Q3D_EQUIPMENT_SERIALNUMBER":   SG_SERIAL,      # :96.1.255 # Device Serialnumber
    "CURRENT_PANEL_POWER":          SG_PANEL_POWER,  
    "CURRENT_PANEL_VOLTAGE":        SG_PANEL_VOLTAGE,
    "CURRENT_PANEL_CURRENT":        SG_PANEL_CURRENT,
}

def decode_stecaFloat_a(ac_bytes):
    if ac_bytes[0] == 0x0B:
        unit = "W"
    elif ac_bytes[0] == 0x07:
        unit = "A"
    elif ac_bytes[0] == 0x05:
        unit = "V"
    elif ac_bytes[0] == 0x0D:
        unit = "Hz"
    elif ac_bytes[0] == 0x09:
        unit = "Wh"
    elif ac_bytes[0] == 0x00:
        unit = "NUL"
    else:
        unit = f'0x{ac_bytes[0]:02x}'

    iacpower = ((ac_bytes[3] << 8 | ac_bytes[1]) << 8 | ac_bytes[2]) << 7 # formula to float - conversion according to Steca
    facpower, = struct.unpack('f', struct.pack('I', iacpower))

    if DEBUG:
        print("# i: 0x%0X" % iacpower,"=", str(iacpower))
        print("# f:", facpower)

    return [facpower, unit]

def decode_stecaFloat(in_bytes):
    results = decode_stecaFloat_a(in_bytes)
    return f"{results[0]:0.2f} {results[1]}"

def decode_TotalYield_a(ba):
    #five byte array, 
    bits = ba[3] << 24 | ba[2] << 16 | ba[1] << 8 | ba[0]
    ieee , = struct.unpack('f', struct.pack('I', bits))
    return [ieee, "Wh"]
    
def decode_version(b):
    o = b'SSXSNSSNSSNSSNSSNSSNSSNSSNSSNSSNSSNSSNSSNSSNSSNSSNSSNSSSSSSSSSSSS'
    so = []
    aos = []
    for i in range(len(b)):
        if o[len(aos)] == 83 and b[i] == 0:
            aos.append(''.join(so))
            so = []
        elif o[len(aos)] == 78 and len(so)>6:
            aos.append('.'.join(so[2:5]))
            so = []
        elif o[len(aos)] == 88 and len(so)>1:
            aos.append('')
            so = []

        if o[len(aos)] == 83:
            so.append(chr(b[i]))
        elif o[len(aos)] == 78 or o[len(aos)] == 88:
            so.append(str(b[i]))

    s = ""
    for i in range(len(aos)):
        s += aos[i]
        if i<3 or (i-4)%3 == 1:
            s += '\n'
        else:
            s += '\t'
    return s
   
def process_telegram(t):
    formatted_hex_bytes = ''
    printable = ''
    for byte in t:
        hex_byte = f'{byte:02x}'
        formatted_hex_bytes += f'{hex_byte:>2} '
        if not 32 <= byte <= 126:
            printable += '.'
        else:
            printable += chr(byte)
        
    return formatted_hex_bytes +"\r\n"+ printable +"\r\n"

def format_hex_bytes(b):
    formatted_hex_bytes = ''
    for byte in b:
        hex_byte = f'{byte:02x}'
        formatted_hex_bytes += f'{hex_byte:>2} '
    return formatted_hex_bytes.strip()

def format_printable(b):
    printable = ''
    for byte in b:
        if not 32 <= byte <= 126:
            printable += '.'
        else:
            printable += chr(byte)
    return printable

def is_one_full_telegram(t):
    if not t or len(t)<1:
        return False
    if t[0] != 2:
        #print("not starting w/ 0x02")
        return False
    if t[len(t)-1] != 3:
        #print("not ending w/ 0x03")
        return False
    if len(t) != (t[2] << 8 | t[3]):
        #print("wrong length",len(t), "!=", (t[2] << 8 | t[3]))
        return False
    return True
    
def process_steca485(t):
    """
    parse telegram from StecaGrid RS485 protocol
    
    returns an array
        msg group
        msg topic
        clear text topic
        values
        or payload has hex string

    :param str telegram:
    """    
    if is_one_full_telegram(t):
        results = [t[4], t[5], t[7], t[11]]
        total_length = (t[2] << 8 | t[3])
        if DEBUG:
            print("#",format_hex_bytes(t))
            print("# dgram:","",end="")
#            print("# ",t[4:-1])
#            print("start:",t[0]," ",end="")
            print("to:",t[4]," ",end="")
            print("from:",t[5]," ",end="")
            print("len:",total_length," ",end="")
            print(f"crc1: {t[6]:02x}"," ",end="")
            print(f"crc2: {t[-3]:02x}{t[-2]:02x}"," ",end="")
            print() #        print("stop:",t[-1])
            # Payload started 7
            print("# payload:", format_hex_bytes(t[7:-3]) ,"",end="")
            print("  ", format_printable(t[7:-3]))
        if t[7] == 0x40: # 64: Requests
            topic=""
            if t[11] == 0x1d: # 29:
                topic = " (Nominal Power)"
            elif t[11] == 0x22: # 34:
                topic = " (Panel Power)"
            elif t[11] == 0x23: # 35: 
                topic = " (Panel Voltage)"
            elif t[11] == 0x24: # 36:
                topic = " (Panel Current)"
            elif t[11] == 0x29: # 41:
                topic = " (ACPower)"
            elif t[11] == 0x3c: # 60:
                topic = " (Daily Yield)"
            if DEBUG:
                print(f"# RequestA for 0x{t[11]:02x}{topic} from {t[4]}")
        elif t[7] == 0x41: # 65: Responses
            if t[8] == 0x00:
                len = (t[9] << 8 | t[10])
                if DEBUG:
                    print(f"# ReponseA for 0x{t[11]:02x} from {t[4]} len={len}")
                if t[11] == 0x51: # 81: Label Value Value Value Value byte Label Value Value Value Value byte
                    i_labelA = 15
                    i_valA1 = i_labelA + (t[i_labelA-2] << 8 | t[i_labelA-1])
                    i_valA2 = i_valA1+4
                    i_valA3 = i_valA2+4
                    i_valA4 = i_valA3+4
                    #print(i_labelA,t[i_labelA-2],t[i_labelA-1],i_valA1,i_valA2,i_valA3,i_valA4)
                    i_labelB = i_valA4+4+1+2
                    i_valB1 = i_labelB + (t[i_labelB-2] << 8 | t[i_labelB-1])
                    i_valB2 = i_valB1+4
                    i_valB3 = i_valB2+4
                    i_valB4 = i_valB3+4                    
                    #print(i_labelB,t[i_labelB-2],t[i_labelB-1],i_valB1,i_valB2,i_valB3,i_valB4)
                    #label = t[15:15+t[14]]
                    if DEBUG:
                        print("#", str(t[i_labelA:i_valA1]), 
                            decode_stecaFloat(t[i_valA1:i_valA2]), 
                            decode_stecaFloat(t[i_valA2:i_valA3]), 
                            decode_stecaFloat(t[i_valA3:i_valA4]), 
                            decode_stecaFloat(t[i_valA4:i_valA4+4])) 
                        print("#", str(t[i_labelB:i_valB1]), 
                            decode_stecaFloat(t[i_valB1:i_valB2]), 
                            decode_stecaFloat(t[i_valB2:i_valB3]), 
                            decode_stecaFloat(t[i_valB3:i_valB4]), 
                            decode_stecaFloat(t[i_valB4:i_valB4+4])) 
                elif t[11] == 0x3c: # 60:
                    label = "Daily Yield"
                    val = decode_stecaFloat_a(t[12:16])
                    results += [label, val]
                    if DEBUG:
                        print("#", label, val[0], val[1])                   
                else:
                    label = t[15:15+t[14]].decode("ascii")
                    val = decode_stecaFloat_a(t[15+t[14]:15+t[14]+5])
                    results += [label, val]
                    if DEBUG:
                        print("#", label, val[0], val[1])
        elif t[7] == 0x64: # 100: Requests
            if DEBUG:
                print(f"# RequestB for 0x{t[11]:02x} from {t[4]}")
        elif t[7] == 0x65: # 101: Responses 
            if DEBUG:
                print(f"# ReponseB for 0x{t[11]:02x} from {t[4]}")
            if t[11] == 0xF1: #  241: Total Yield
                results += ["Total Yield", decode_TotalYield_a(t[12:16])]
                if DEBUG:
                    print("# (",format_hex_bytes(t[12:17]),")")
                    print("#", decode_TotalYield_a(t[12:16]))
            elif t[11] == 0x05: # 5: Time 
                time = datetime.datetime(2000+t[12], t[13], t[14], t[15], t[16], t[17]) # ignoring final 3 byte for now. TZ, millis, ...?
                results += ["Time", [time,""]]
                if DEBUG:
                    print(f"# {time} (",format_hex_bytes(t[12:21]),")")
            elif t[11] == 0x08: # 8: ???
                results += ["???", [format_hex_bytes(t[12:17]),""]]
                if DEBUG:
                    print("# (",format_hex_bytes(t[12:17]),")")
                    print("#", decode_stecaFloat(t[12:16]))
            elif t[11] == 0x09: # 9: Serial
                results += ["Serial Number", [t[12:-4].decode("ascii"),""]]
                if DEBUG:
                    print("# (",format_hex_bytes(t[12:17]),")")
                    print("#", decode_stecaFloat(t[12:16]))
            else:
                results += ["???", [format_hex_bytes(t[12:17]),""]]
        elif t[7] == 0x21: # 33: Versions
            if t[8] == 0x00:
                len = (t[9] << 8 | t[10])
                results += ["???", [decode_version(t[11:-3]),""]]
                if DEBUG:
                    print("# ReponseC for", t[11], "from", t[4], "len=",len)
        return results
    else:
        if DEBUG:
            print("# NOT a single full Steca485 Telegram")
  
def getStecaGridResult(req):
    if DEBUG:
        print("ser write: "+process_telegram(req))
        results = process_steca485(req)
        print(results)
    steca.write(req)
    in_data = steca.read(size=1024)
    results = process_steca485(in_data)
    if DEBUG:
        print(results)
    if results and results[5][1] != "NUL":    
        return results[5]

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Feed MQTT based on RS485 from StecaGrid3600')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose output')
    parser.add_argument('-c', '--config', help='Load config from (default '+config_file+')')
    args = parser.parse_args()
    DEBUG = args.verbose
    if args.config:
        config_file = args.config
 
    with open(config_file) as file:
        config = yaml.safe_load(file)
        if config:
            if DEBUG:
                print(config) 
            ser = SERIAL_DEVICE
            if config['serial_device']:
                ser = config['serial_device']

            steca = serial.Serial(baudrate=SERIAL_BAUDRATE, port=ser, timeout=SERIAL_TIMEOUT, parity=SERIAL_PARITY, stopbits=SERIAL_SBIT, bytesize=SERIAL_BYTES, xonxoff=0, rtscts=0)
            if DEBUG:
                print(steca.get_settings())

            ac_power = getStecaGridResult(SG_AC_POWER)
            if ac_power:
                if DEBUG:
                    print(ac_power)
            else: 
                ac_power = 0
    
            if DEBUG:
                print ("Current ACPower:",ac_power)

            mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            if 'mqtt_username' in config:
                mqtt_client.username_pw_set(config['mqtt_username'], password=config['mqtt_password'])
            mqtt_client.reconnect_delay_set(min_delay=1, max_delay=120)
            try:
                mqtt_client.connect(config['mqtt_broker_address'])
            except Exception as e:
                print("Can't connect to MQTT Broker (", config['mqtt_broker_address'] ,"):", e)
                steca.close() 
                exit()
                
            mqtt_client.loop_start()

            if 'client' in config:
                if config['client'] == 'serial':
                    try:
                        while True:
                            for n in config['values_of_interest']:
                                if DEBUG:
                                    print("OBIS:", n)
                                if n in mapping:
                                    if DEBUG:
                                        print("StecaMsg:", mapping[n])
                                    v = getStecaGridResult(mapping[n])
                                    if DEBUG:
                                        print("StecaResult:", v)
                                    if not v or len(v) < 2 or v[1] == "NUL" or v[0] == "":
                                        pl = 0
                                    else:
                                        pl = v[0]
                                        
                                    if not ( n == 'ELECTRICITY_EXPORTED_TOTAL' and pl == 0 ) # don't zero counters
                                        published = mqtt_client.publish(config['topic'] + '/' + n, payload=float(pl), qos=0)
                                        if DEBUG:
                                            print("MQTT: ", config['topic'] + '/' + n,float(pl), " > ", published)
                                        published.wait_for_publish()
                            time.sleep(1)
                    except KeyboardInterrupt:
                        print()
                    finally:
                        mqtt_client.loop_stop()
                        steca.close() 
