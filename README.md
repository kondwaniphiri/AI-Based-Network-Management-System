# ESP32 IoT Network Monitor

SNMP-based monitoring and predictive traffic control for an experimental ESP32 IoT network.

The system combines three sensor nodes, a dedicated ESP32 NAT gateway, a Flask server, endpoint probes, optional model-guided rate control, and repeatable Mininet experiments.

## Current status

- Gateway firmware compiles for the original ESP32 Dev Module with 4 MB flash.
- Software verification covers delivery accounting, SNMP, NAT identity, datasets, control, and dashboard behavior.
- Physical flashing, live wireless routing, and measured control benefits still require hardware validation.
- The custom gateway supports read-only SNMPv2c. It does not implement SNMPv3.

## Architecture

```text
MiFi upstream network
  |-- Server PC: Flask, collectors, model training, dashboard
  `-- Dedicated ESP32 gateway: Wi-Fi station + hotspot + NAT + SNMP
         |-- ESP32 sensor 1: HTTP and UDP
         |-- ESP32 sensor 2: HTTP
         |-- ESP32 sensor 3: UDP
         `-- Phone clients: browsing and streaming load
```

The server stays upstream so client traffic crosses the gateway. Sensor delivery observations retain both logical device identity and the original translated peer address.

## Features

- Persistent telemetry and sequence-gap accounting across independent boots/transports.
- Actual HTTP request RTT, probe failures, and forwarded probes through NAT.
- Two-interface gateway counters and a Python SNMP client without Net-SNMP installation.
- A local dashboard with traffic, RTT, delivery gaps, collector status, and exports.
- Future-window degradation prediction using complete held-out capture sessions.
- Observe, threshold, and model control with bounded interval changes and action logs.
- Repeated concurrent Mininet workloads with matched physical link conditions.

## Getting started

Use Python 3.10 or later, then install application dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Configure and flash the dedicated gateway and sensor boards using the [gateway guide](firmware/esp32_gateway/README.md). Copy the example configuration to an ignored local JSON file and supply your own Wi-Fi credentials and SNMP community. Real credentials must not be added to source control.

For a gateway station address of `192.168.8.110` and sensors on `192.168.50.0/24`:

```powershell
$env:IOT_RUN_ID = "pilot_01"
$env:IOT_USE_GATEWAY = "1"
$env:IOT_GATEWAY_UPLINK_IP = "192.168.8.110"
$env:IOT_SENSOR_SUBNET = "192.168.50.0/24"
python server/server.py
```

Open `http://localhost:5000/dashboard`. Addresses above are examples, not live deployment settings.

## Documentation

- [Implementation and experiment guide](IMPLEMENTATION_GUIDE.md)
- [Gateway build, configuration, SNMP, and flash guide](firmware/esp32_gateway/README.md)
- [Software requirements and SDLC report](output/pdf/Software_Process_Report.pdf)
- [Editable report source](reports/Software_Process_Report.md)
- [Verification record](VALIDATION.md)
- [Controlled measurements](DETAILED_MEASUREMENTS.md)

## Tests

```powershell
python -m unittest discover -s tests -q
node --test tests/dashboard.test.cjs
```

Native firmware-codec checks require a compiled library. On Windows, install `firmware/requirements-build.txt` and run `tools/test_gateway.ps1`. GitHub Actions builds the same C codec on Linux and supplies `IOT_SNMP_TEST_LIBRARY` to run those checks.

## Repository contents

| Directory | Purpose |
|---|---|
| `server/` | Flask, telemetry, dashboard, streaming and browsing |
| `esp32 code/` | Three Arduino sensor sketches |
| `firmware/esp32_gateway/` | ESP-IDF NAT router and read-only SNMP agent |
| `monitoring/` | HTTP probes, SNMP collection/client and controller |
| `datasets/` | Feature processing, model training and analysis code |
| `mininet/` | Controlled emulation and result collection |
| `tests/` | Python/native and dashboard checks |
| `tools/` | Local configuration, native tests and report generation |

Raw packet captures, local databases, trained models, CSV experiment records, private headers, bundled videos, and original research documents are intentionally not tracked. Supply your own authorized workload media locally; see the [video directory notes](server/templates/videos/README.md). Synthetic fallback traffic is available when configured files are absent.

This is an isolated-lab prototype. Gateway counters describe the software-interface boundary, not radio airtime or every packet loss. Unknown measurements remain missing. No physical performance gain is claimed by the automated tests.
