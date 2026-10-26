# ESP32 lab router and read-only SNMP agent

This is firmware for a **dedicated fourth original ESP32 Dev Module** (4 MB flash),
separate from the three sensor boards. It uses ESP-IDF 5.4.0 through the pinned
PlatformIO `espressif32@6.10.0` environment. ESP32-C3/S3 boards require a different
build target and have not been validated by this configuration.

## What it implements

- WPA2 station connection to your existing MiFi.
- A WPA2 downstream hotspot, DHCP for phone clients and IPv4 NAT.
- Stable downstream sensor IPs `.101`, `.102` and `.103` on a separate `/24`.
- Three TCP port forwards for HTTP probes and existing token-protected sensor control.
- Read-only SNMPv2c GET/GETNEXT on UDP 161, restricted to one collector IPv4 address.
- Two software-interface counter sets: `ifIndex 1 = wifi_sta`, `ifIndex 2 = wifi_ap`.
- Reconnection with backoff, explicit subnet-overlap rejection and bounded SNMP parsing.
- No web-admin password, write-enabled SNMP, automatic Internet exposure or cloud dependency.

The SNMP agent is a small project-owned codec, not ESP-IDF's optional lwIP SNMPv3
agent. SNMPv1, SNMPv3, GETBULK and traps are not implemented. SET requests return
`notWritable`. Use `snmpwalk`, not `snmpbulkwalk`, for discovery.

## Network arrangement

```text
                     MiFi Wi-Fi: 192.168.8.0/24
                         /                  \
         Server PC: 192.168.8.100       ESP32 gateway STA: DHCP address
         Flask :5000 / UDP :12345       e.g. 192.168.8.110
                                             |
                                      NAT + Wi-Fi hotspot
                                      192.168.50.1/24
                                             |
                      +----------------------+----------------------+
                      |                      |                      |
                 ESP32 sensor 1        ESP32 sensor 2         ESP32 sensor 3
                 192.168.50.101         192.168.50.102          192.168.50.103
                      +----------------------+
                         Phones: DHCP .10-.30
```

Keep the PC on the **upstream MiFi network**. Sensor-to-server and phone streaming
traffic must cross the ESP32 to exercise its routing path. Putting the server and
sensors on the same downstream subnet could bypass that path and would change what
the counters measure. Disable MiFi client isolation if it prevents gateway/PC access.
The MiFi itself does not need SNMP support or firmware changes.

Upstream and downstream must be different subnets. The example uses `.8.0/24` and
`.50.0/24`; adapt the upstream server/collector addresses to your actual MiFi.
Only private lab devices should join this hotspot. SNMPv2c communities are sent
without encryption; the collector ACL limits access but does not add encryption.

## 1. Configure local credentials once

From the project root, copy the example and edit **the local copy**:

```powershell
Copy-Item firmware/esp32_gateway/gateway_config.example.json firmware/esp32_gateway/gateway_config.local.json
```

Set the upstream MiFi SSID/password, your chosen hotspot password, a random SNMP
community (16–64 printable characters), and the PC's upstream address as both
`server_ip` and `collector_ip`. Optionally set a different random `control_token`
for the sensor controller; empty disables sensor interval writes.

Generate ignored local headers:

```powershell
python tools/configure_gateway.py --config firmware/esp32_gateway/gateway_config.local.json
```

The generator validates address separation and writes:

- `main/gateway_config.local.h` for this gateway.
- `gateway_network.local.h` inside each existing sensor sketch directory.

The generated sensor configuration selects the gateway SSID, downstream `/24`,
upstream server address and optional control token. Reflash all three sensors
using their existing Arduino sketches. Their original network settings remain
available when no local header is present.

Local JSON/headers are ignored by Git. Credentials are compiled into firmware;
do not distribute configured binaries. An unconfigured gateway build exits before
starting Wi-Fi rather than creating an open hotspot.

## 2. Build and flash the dedicated gateway

Install PlatformIO Core into your development Python environment:

```powershell
python -m pip install platformio==6.1.18
python -m platformio run --project-dir firmware/esp32_gateway
```

Or use the local build environment prepared in this workspace:

```powershell
$env:PLATFORMIO_CORE_DIR = "$PWD\.tools\platformio"
.\.tools\python-full\python.exe -m platformio run --project-dir firmware/esp32_gateway
```

Connect the **dedicated gateway board**, select its actual serial port, and flash:

```powershell
python -m platformio run --project-dir firmware/esp32_gateway --target upload --upload-port COM5
python -m platformio device monitor --port COM5 --baud 115200
```

Replace COM5 with your board's port. Do not overwrite a sensor board accidentally.
Watch the serial log for the upstream DHCP address and both counter-installation
messages. The gateway does not print passwords or the SNMP community.
Reserve that upstream DHCP address on the MiFi if supported; otherwise update
the server/collector configuration whenever it changes.

## 3. Start the server in gateway mode

For an example upstream gateway address of `192.168.8.110`:

```powershell
$env:IOT_RUN_ID = "gateway_pilot_01"
$env:IOT_USE_GATEWAY = "1"
$env:IOT_GATEWAY_UPLINK_IP = "192.168.8.110"
$env:IOT_SENSOR_SUBNET = "192.168.50.0/24"
$env:IOT_GATEWAY_IP = "192.168.8.110"
$env:IOT_GATEWAY_INTERFACE = "2"
$env:IOT_SNMP_CSV = "$PWD\datasets\snmp_ap.csv"
python server/server.py
```

Gateway mode preserves the logical sensor source in telemetry using the known
device identifier **only when the observed peer equals the configured gateway**.
The new `peer_ip` column retains the real on-wire/NAT peer; existing databases are
migrated without deleting observations. This is a lab identity mapping, not
cryptographic device authentication. Wireshark will still see the translated IP.

The server uses forwarded HTTP probes for downstream RTT and labels them HTTP
RTT in the dashboard. ICMP ping to individual downstream addresses is disabled
because a TCP port forward cannot forward an individual ICMP probe. Phone clients
are grouped by their translated source address; the dashboard labels that grouping.

## 4. Poll the agent without installing Net-SNMP

In another terminal, set the community to the same value as the private JSON:

```powershell
$env:IOT_SNMP_COMMUNITY = "YOUR_CONFIGURED_COMMUNITY"
python monitoring/snmp_collect.py --backend python --version 2c --agent 192.168.8.110 --interface 2 --run-id gateway_pilot_01 --output datasets/snmp_ap.csv --duration 300
```

The community is read from the environment and is not placed on the command line
or written to CSV. First sample is `warmup`; rates appear after the second poll.
The dashboard reads that file for the same run, agent and interface. Use a polling
interval of five seconds; the dashboard marks samples older than 15 seconds stale.

Optionally run a second collector for the upstream interface, using a different file:

```powershell
python monitoring/snmp_collect.py --backend python --version 2c --agent 192.168.8.110 --interface 1 --run-id gateway_pilot_01 --output datasets/snmp_sta.csv --duration 300
```

Use separate files for concurrent collectors. Select which interface's file the
dashboard and model should consume. The existing Net-SNMP backend and SNMPv3
support for other devices remain available; SNMPv3 is not supported by this gateway.

## 5. Probe and control the downstream sensors

TCP forwards on the gateway's upstream address:

| Upstream port | Downstream destination |
|---|---|
| 8101 | 192.168.50.101:80 |
| 8102 | 192.168.50.102:80 |
| 8103 | 192.168.50.103:80 |

Collect HTTP RTT with explicit logical source identities so probe and event
windows join correctly:

```powershell
python monitoring/probe.py --run-id gateway_pilot_01 --duration 300 --targets 192.168.50.101=http://192.168.8.110:8101/data 192.168.50.102=http://192.168.8.110:8102/data 192.168.50.103=http://192.168.8.110:8103/data
```

The controller targets the same forwarded port, e.g.:

```powershell
python monitoring/control.py --server http://192.168.8.100:5000 --device http://192.168.8.110:8103 --source 192.168.50.101 --run-id gateway_pilot_01 --strategy threshold
```

It remains dry-run until `--apply`. Applying requires a configured sensor
`control_token` and matching `IOT_CONTROL_TOKEN` in the controller environment.
Use the existing implementation guide for training and model comparison.

## MIB objects and measurement meaning

The collector's required objects are implemented on both interfaces: one shared
`sysUpTime.0` and nine interface instances each, **19 instances total**.

| Name | OID (`i` is 1 or 2) | Meaning |
|---|---|---|
| sysUpTime | 1.3.6.1.2.1.1.3.0 | Boot uptime in hundredths of a second |
| ifHCInOctets | 1.3.6.1.2.1.31.1.1.1.6.i | Bytes entering lwIP at that software interface |
| ifHCOutOctets | 1.3.6.1.2.1.31.1.1.1.10.i | Bytes successfully handed to that interface's driver |
| ifHighSpeed | 1.3.6.1.2.1.31.1.1.1.15.i | 0: no measured fixed Wi-Fi capacity |
| ifCounterDiscontinuityTime | 1.3.6.1.2.1.31.1.1.1.19.i | Uptime when software counters were installed/reset |
| ifInDiscards | 1.3.6.1.2.1.2.2.1.13.i | Input callback returned memory/buffer error |
| ifOutDiscards | 1.3.6.1.2.1.2.2.1.19.i | Output callback returned memory/buffer error |
| ifInErrors | 1.3.6.1.2.1.2.2.1.14.i | Other input callback errors |
| ifOutErrors | 1.3.6.1.2.1.2.2.1.20.i | Other output callback errors |
| ifOperStatus | 1.3.6.1.2.1.2.2.1.8.i | Software interface up/down |

Additional discovery objects (`sysDescr`, `sysName`, `ifNumber`, `ifIndex`,
`ifDescr`, `ifName`, `ifType`, `ifMtu`, `ifSpeed`, and 32-bit octet counters) bring
the supported catalog to **38 concrete instances**. GETNEXT walks them in numeric
OID order. Unknown GET objects return `noSuchObject`; walking past the catalog
returns `endOfMibView`. Optional Net-SNMP discovery uses your locally configured
community and `snmpwalk -v 2c 192.168.8.110 .1.3.6.1.2.1`.

These are **software-interface** counters at the lwIP/driver boundary. They include
the gateway's own management traffic and successful forwarding. They do not count
802.11 retries, airtime, packets lost before reaching the driver, or every drop
inside the IP/NAT stack. An error counter of zero does not prove loss-free Wi-Fi.
`ifHighSpeed=0` makes utilization unavailable in the collector; no advertised PHY
rate is substituted for measured capacity. Packet-loss claims must still use
endpoint sequence numbers and probe outcomes.

## Validation and remaining hardware checks

Install native-test tooling if needed and run:

```powershell
python -m pip install -r firmware/requirements-build.txt
powershell -ExecutionPolicy Bypass -File tools/test_gateway.ps1
python -m unittest discover -s tests -q
node --test tests/dashboard.test.cjs
```

The native suite calls the **same C codec compiled into the gateway**, covering
all required objects, 64-bit precision, walking, read-only rejection, malformed
packets, 5,000 mutated requests, buffer limits and a real loopback UDP exchange
with the Python client. Configuration, NAT identity and database migration are
tested separately. This does not simulate the actual Wi-Fi driver or RF channel.

After flashing, verify in order: hotspot join and DHCP; server reachability;
three sensor sources in telemetry; successful forwarded HTTP probes; SNMP warmup
then valid rates on both interfaces; activity rising under load; reconnection;
and baseline/threshold/model comparisons. No physical-board routing throughput or
wireless-loss improvement is claimed until those measurements are collected.

Reference APIs: [Espressif AP+STA/NAPT example](https://github.com/espressif/esp-idf/tree/v5.4.2/examples/wifi/softap_sta)
and [Espressif lwIP NAPT API](https://github.com/espressif/esp-lwip/blob/2.1.3-esp/src/include/lwip/lwip_napt.h).
