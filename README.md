# StecaGrid 3600 RS485 MQTT Service

## The protocol

As far as I can tell its a s simple request and response protocol that Steca had implemented to talk to StecaGrid inverters (around 2013 time frame). Newer models seem to have a better XML API.
- A dynamic datagram structure is used, which starts with 0x02 and ends with 0x03.
- The second data word holds the overall length of the datagram.
- Following that the RS485 id of the recipient and of the sender are always present.
- The next byte (as well as the last word before 0x03) are obviously some CRC.
- The rest (payload) of the datagram depends on the topic and if its the request or response for it
- It typically has a header (requests start with 0x40, 0x64, 0x20, ... responses start with 0x41, 0x65, 0x21, ... respectively)
- The topics are represented in the fifth byte of the payload of both the request and the reponse. A few of the discovered topics are:
  - Grid voltage = L1 MeasurementValues ENS1 (measurement 1/2, value 1/4)
  - Grid power = AC Power (0x29)
  - Grid frequency = L1 MeasurementValues ENS1 (measurement 2/2, value 2/4)
  - Panel voltage (0x23)
  - Panel current (0x24)
  - Panel power (0x22)
  - Daily yield (0x3c)
  - Total yield = (0xf1)
  - Time (0x05)
- Data is respresented as Pascal-type strings (pre-fixed by their length as a 16-bit word), proprietary 3-byte floats (pre-fixed by a unit byte) in the payload. Pre-fixed length field are used here and there. Unit prefixes are:
  - V (0x05)
  - A (0x07)
  - Wh (0x09)
  - W (0x0B)
  - Hz (0x0D)
  - NUL (0x00) (some fields switch to this unit type when they fall to zero)
 
### To Do
- CRC: without the CRC calculation no datagram can be synthesized.

### Install
	pip3 install pyserial pyyaml paho-mqtt

### Cableing

[![Diagram](https://upload.wikimedia.org/wikipedia/commons/a/ab/StecaGrid_to_Raspberry.svg)](https://commons.wikimedia.org/wiki/File:StecaGrid_to_Raspberry.svg)

### Usage
	usage: StecaGrid3600_mqtt.py [-h] [-v] [-c CONFIG]

	Feed MQTT based on RS485 from StecaGrid3600

	optional arguments:
	  -h, --help            show this help message and exit
	  -v, --verbose         Enable verbose output
	  -c CONFIG, --config CONFIG
							Load config from (default config.yaml)

### config.yaml
	mqtt_broker_address: 'nas.ds18' # Set this to your mqtt broker address
	mqtt_username: 'mqtt_user' # Uncomment and change this to your username if required
	mqtt_password: 'xyzxyz'

	# List the (OBIS) values that you want to send to the mqtt broker
	values_of_interest:
	  - CURRENT_ELECTRICITY_DELIVERY
	  - ELECTRICITY_EXPORTED_TOTAL

	client: serial
	serial_device: /dev/ttyS0

	topic: DS18/PV/StecaGrid_3600

### Home Assistant sensor config
	mqtt:
		sensor:
		  - name: "StecaGrid 3600 Total"
			unique_id: "StecaGrid_3600_Total"
			device_class: "energy"
			state_class: "total_increasing"
			unit_of_measurement: "Wh"
			state_topic: "DS18/PV/StecaGrid_3600/ELECTRICITY_EXPORTED_TOTAL"
		  - name: "StecaGrid 3600 Power"
			unique_id: "StecaGrid_3600_Power"
			device_class: "power"
			state_class: "measurement"
			unit_of_measurement: "W"
			state_topic: "DS18/PV/StecaGrid_3600/CURRENT_ELECTRICITY_DELIVERY"

### evcc meter config
	meters:
	  - name: StecaGrid_3600
		type: custom
		power:
		  source: mqtt
		  topic: DS18/PV/StecaGrid_3600/CURRENT_ELECTRICITY_DELIVERY
		  timeout: 10s

### Steca RS485 requests for replay approach
The following telegrams are requests to extend the replay beyond AC Power. Note, that they all address the inverter with the RS485 ID #1. You will have to change your Steca to that ID until we have figured out the CRC generation to synthesize a full new telegram for a different id. Contact me of you need a replay telegram for a differnt ID, and I might be able to record one for you from the SEM.

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

### Based on versions
All of my tinkering is based on the following firmware versions. 

	python3 getStecaGridData.py -ve

	StecaGrid 3600

	HMI BFAPI       5.0.0   19.03.2013 14:38:59
	HMI FBL         2.0.3   05.04.2013 11:46:20
	HMI APP         15.0.0  26.07.2013 13:19:06
	HMI PAR         0.0.1   26.07.2013 13:19:06
	HMI OEM         0.0.1   11.06.2013 08:11:29
	PU BFAPI        5.0.0   19.03.2013_14:38:42
	PU FBL  1.0.1   19.12.2012_16:36:04
	PU APP  4.0.0   03.05.2013_09:37:55
	PU PAR  3.0.0   31.01.2013_13:47:24
	ENS1 BFAPI      5.0.0   19.03.2013_14:38:51
	ENS1 FBL        1.0.1   19.12.2012_16:34:47
	ENS1 APP        39.0.0  11.07.2013_14:39:50
	ENS1 PAR        0.0.14  11.07.2013_14:40:03
	ENS2 BFAPI      5.0.0   19.03.2013_14:38:51
	ENS2 FBL        1.0.1   19.12.2012_16:34:47
	ENS2 APP        39.0.0  11.07.2013_14:39:50
	ENS2 PAR        0.0.14  11.07.2013_14:40:03
	HMI     PU      ENS2
	Net11
 
Your milage may vary.
