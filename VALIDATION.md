# Validation record

Validated on 2026-10-24 using project-local Python 3.13.7.

- `python -m unittest discover -s tests -q`: **41 tests passed**, including 8 native-agent tests.
- `node --test tests/dashboard.test.cjs`: **7 dashboard interaction tests passed**.
- Dashboard JavaScript syntax and template element references were checked.
- The integration test runs the dataset builder and trainer as separate CLI
  processes using synthetic sessions; it verifies the held-out run and baseline report.
- Mininet launch/order and identical physical configuration were checked with
  mocked hosts/processes. The real Mininet network was not launched on Windows.
- Controller writes were checked against a mocked HTTP endpoint, including token
  and interval. No action was sent to a real device.
- SNMP rate/reset/freshness logic was tested on synthetic counters. The actual
  firmware C codec was compiled as a native library and tested through ctypes,
  including all 19 required instances, Counter64 values above 2^63, ordered
  GETNEXT walking, read-only rejection, 5,000 packet mutations, bounded buffers,
  and a loopback UDP exchange with the Python SNMP client.
- The ESP32 gateway firmware **compiled and linked successfully** with
  PlatformIO espressif32 6.10.0 / ESP-IDF 5.4.0 for esp32dev (4 MB flash).
  The image uses 707,718 bytes of the app partition and 32,856 bytes of static RAM;
  these figures do not include runtime Wi-Fi/task/NAT heap use.
- ELF symbols confirm that the real SNMP codec/service, interface counters,
  configuration guard and NAPT implementation are linked into the firmware.
- The successful build uses unconfigured credentials and safely stops before
  starting Wi-Fi. Generate the private local headers and rebuild before flashing.
- Gateway firmware has not been flashed. Physical NAT forwarding, Wi-Fi behavior,
  collector ACLs and throughput still require board validation. The three Arduino
  sensor sketches have not been compiled/flashed in this session.

The existing server emits Python datetime deprecation warnings, and the historical
packet processor's example docstring emits an escape-sequence warning. These did
not fail validation. No measured performance improvement is claimed by these tests.

Historical captures, datasets and saved model artifacts were not retrained or
overwritten. See IMPLEMENTATION_GUIDE.md for collecting new measured outcomes.

The dashboard was redesigned with responsive light styling, a device table,
traffic/RTT/delivery charts and an optional gateway collector panel. Tests verify
Flask template/static delivery; pause/resume and failed requests; chart switching;
label escaping; unknown measurements; unavailable/stale SNMP data; partial CSV
writes; and missing saved plots. Desktop and mobile layouts were later inspected using a separate headless
Chrome browser with clearly labelled synthetic data. Those demo screenshots and
records were deleted during the subsequent cleanup.

Gateway integration additionally checks generated sensor network configuration,
NAT-aware logical identities with original peer retention, migration of existing
telemetry databases, forwarded probe labels and explicit HTTP-RTT/NAT-group labels
in the dashboard. The gateway setup and flash procedure is documented in
`firmware/esp32_gateway/README.md`.

Repository preparation removed obsolete backups and rebuildable caches. The
previous successful firmware build remains recorded above; regenerate the local
firmware image from source before flashing. Raw captures, databases, videos and
trained models remain local and are excluded from the GitHub repository.
