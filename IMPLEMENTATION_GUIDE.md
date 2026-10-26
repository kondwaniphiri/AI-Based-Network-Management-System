# Measurement, prediction, SNMP and controlled optimization

This workflow supplements packet captures with persistent delivery observations,
actual HTTP round-trip probes, optional router counters, and repeatable experiments.
The existing dataset/models remain historical artifacts; do not interpret their
rule-label accuracy or inter-arrival regression as validated latency prediction.

The dedicated ESP32 NAT router and read-only SNMP agent are now implemented in
[`firmware/esp32_gateway`](firmware/esp32_gateway/README.md). Follow that guide
for the separate sensor subnet, generated local network headers, forwarded probes
and gateway-mode server settings. It uses SNMPv2c; the default SNMPv3 examples below
apply to other devices, not this gateway.

## 1. Install and configure

Use Python 3.10+ and install:

```powershell
python -m pip install -r requirements.txt
```

This workspace also has an isolated portable Python for validation:
`.tools\python\python.exe` (use `.\.tools\python\python.exe` from the project root).
It does not change your system Python. `.tools` is ignored by Git.

The current firmware and server addresses use **192.168.8.0/24**; older README
examples use 192.168.0.0/24. Match the server, gateway and device addresses to your
actual network before flashing. Keep passwords and control tokens out of shared
copies of firmware; the existing Wi-Fi constants need local configuration.

Reflash all three sketches. Each adds a boot identifier and independent HTTP/UDP
sequence counters. Original sensor fields still work. Devices with older firmware
remain accepted, but cannot provide sequence-based loss measurements.

Start each capture session with a unique run ID:

```powershell
$env:IOT_RUN_ID = "normal_01"
$env:IOT_DROP_RATE = "0"
python server/server.py
```

Random application discards are now disabled by default. Set `IOT_DROP_RATE` to
an explicit probability, e.g. `0.05`, only for a documented impairment experiment.
This is an application discard after receipt, not proof of network packet loss.
Use a new run ID when changing scenarios, and use the same ID in every collector.
Do not restart unrelated capture sessions with the same ID.

## 2. Collect real endpoint outcomes

In another terminal on the server PC:

```powershell
python monitoring/probe.py --run-id normal_01 --duration 300 --interval 1 --targets http://192.168.8.101/data http://192.168.8.102/data http://192.168.8.103/data
```

Run browser/video and ESP32 traffic together while this runs. Collect quiet,
mixed-load, overload and impaired scenarios, including repetitions on another day.
The probe file appends across sessions with their run IDs.

`rtt_ms` measures the complete HTTP request/response duration, including device
processing and connection setup. It is not one-way delay or ICMP RTT. Failed
requests have missing RTT plus a failure flag; their failure rate is not UDP loss.
Inter-arrival timing remains a separate traffic feature.

Export the server observations after each run:

```powershell
Invoke-WebRequest "http://192.168.8.100:5000/api/telemetry/export?run_id=normal_01" -OutFile datasets/normal_01_events.csv
```

- `GET /api/delivery?run_id=normal_01`: cumulative observed gaps by boot and protocol.
- `GET /api/telemetry/export`: all persistent observations in CSV.
- Optional `since=<epoch seconds>` bounds the export.
- `server/telemetry.db`: persistent events, including explicitly simulated discards.

Late arrivals repair observed gaps, and duplicates do not increase unique delivery.
The loss denominator is the sequence span between the first and last observed
message. Missing prefixes/tails and completely silent boots cannot be inferred.
The dashboard's device loss series now shows cumulative observed UDP sequence gaps;
unknown measurements remain missing. This is not a per-second physical-link loss rate.

## 3. Add optional SNMP infrastructure monitoring

For the new ESP32 gateway, use `monitoring/snmp_collect.py --backend python
--version 2c` with `IOT_SNMP_COMMUNITY`, as shown in its firmware guide. No Net-SNMP
installation is required for that backend. The instructions below remain available
for other SNMP-capable infrastructure.

First verify that your router/switch supports an accessible SNMP agent. The code
does not enable or reconfigure the router. Linux host counters describe that host,
not an unsupported MiFi's internal wireless queues.

Install **Net-SNMP tools** on the collector. Linux example:

```bash
sudo apt install snmp
```

Configure credentials in your local Net-SNMP `snmp.conf`, outside the repository.
Example SNMPv3 settings (replace placeholders locally):

```text
defSecurityName YOUR_READ_ONLY_USER
defSecurityLevel authPriv
defAuthType SHA
defAuthPassphrase YOUR_AUTH_PASSPHRASE
defPrivType AES
defPrivPassphrase YOUR_PRIV_PASSPHRASE
```

Find the monitored interface's ifIndex and check agent access:

```bash
snmpwalk -v 3 192.168.8.1 .1.3.6.1.2.1.31.1.1.1.1
```

Then collect (replace interface 2 with the actual index):

```powershell
python monitoring/snmp_collect.py --agent 192.168.8.1 --interface 2 --run-id normal_01 --interval 5 --duration 300
```

SNMPv3 is the default. An existing lab agent can be selected with `--version 2c`,
using its locally configured default community. Credentials are never stored in CSV.
See the [Net-SNMP configuration manual](https://www.net-snmp.org/docs/man/snmp.conf.html)
for platform configuration locations and supported authentication options.

The collector reads IF-MIB 64-bit octet counters, speed, status, discard/error
counters, uptime and counter discontinuity. Rates use elapsed monotonic time.
Input/output utilization is separate, so full-duplex directions are not summed.
Counter decreases, uptime rollovers and discontinuities invalidate that interval;
ambiguous wraps are deliberately not guessed. Failed polls are retained as failures.
Unsupported required OIDs produce `collection_failed`, rather than invented values.
CPU/memory OIDs are vendor-specific and are not collected by this generic adapter.

## 4. Build a future-outcome dataset

Use matching event exports and probe logs from several distinct runs:

```powershell
python datasets/build_windows.py --events datasets/normal_01_events.csv datasets/overload_01_events.csv datasets/test_01_events.csv --probes datasets/probes.csv --seconds 5
```

Add SNMP features with:

```powershell
python datasets/build_windows.py --events datasets/normal_01_events.csv datasets/overload_01_events.csv datasets/test_01_events.csv --probes datasets/probes.csv --seconds 5 --snmp datasets/snmp.csv --agent 192.168.8.1 --interface 2
```

Both commands produce `datasets/windows.csv`. Choose one selected infrastructure
interface deliberately; its counters describe a shared link, not individual flows.
Samples join backward by measurement completion time and run ID. They expire
after two window lengths; failed polls clear their features.

Features contain current-window application message/byte rate, size statistics,
inter-arrival timing, UDP fraction, measured HTTP RTT and probe failure percentage.
These are application messages, not every TCP/IP packet visible in Wireshark.
Windows with failed probes but no delivered messages remain in the dataset.

The target is the **immediately following** window's service degradation:
mean successful HTTP RTT over 100 ms, or probe failures at least 10%, by default.
Use `--rtt-limit` and `--failure-limit` to set application requirements before
evaluating results. A missing window is not bridged. The last incomplete bucket
is excluded. The first bucket may be partial; allow a warm-up period in each run.
This predicts service degradation; it does not establish that congestion is its cause.

The legacy Wireshark processor is still available. It no longer substitutes
inter-arrival values for missing RTT. Old captures have no boot sequence metadata
and cannot reconstruct these application outcomes retrospectively.

## 5. Train and evaluate on held-out runs

Choose complete test runs before tuning:

```powershell
python datasets/train_windows.py --test-runs test_01
```

The other sessions train the model. Training must include both normal and degraded
outcomes. Use test sessions with both classes as well, to measure useful recall
and false-alarm performance. Keep threshold definitions and window duration equal
across sessions. Do not repeatedly tune against the final held-out sessions.

Outputs under `datasets/window_model/`:

- `classifier.pkl`: model pipeline, feature names and outcome/window settings.
- `evaluation.json`: per-class precision/recall/F1, confusion matrix, run IDs,
  prediction horizon and a fixed-threshold persistence baseline.
- `classifier_snmp.pkl`: optional matched evaluation with SNMP features.
- `rtt_regressor.pkl`: optional next-window actual HTTP RTT regression and MAE.

Imputation is fitted only on training sessions. No random packet split or
future-window value enters model features. Compare base and SNMP models using
the same held-out rows. The legacy trainer now requires `--legacy-heuristics`
to make its historical rule-reproduction purpose explicit.

## 6. Observe or control the real UDP source

Keep the server and probe collector running. For observation only:

```powershell
python monitoring/control.py --server http://192.168.8.100:5000 --device http://192.168.8.103 --source 192.168.8.101 --run-id normal_01 --strategy observe
```

`--source` selects the endpoint whose service is protected. `--device` selects the
UDP source to slow down; these may be different devices. Select `--strategy threshold`
or `--strategy model` to compare decisions. Both remain dry-run until `--apply`.
Use `--model datasets/window_model/classifier_snmp.pkl` plus `--snmp`, `--agent`
and `--interface` for a model trained with infrastructure features.

To apply changes, set a nonempty `CONTROL_TOKEN` in the UDP device sketch and reflash.
Set the same value as `IOT_CONTROL_TOKEN` in the controller terminal, then add `--apply`.
The controller posts to `http://DEVICE/config`; an empty firmware token disables
writes. Intervals are bounded to 50..10000 ms. Defaults are 1100 ms normal and
3000 ms limited, with a 15-second cooldown and three healthy windows before
restoration. Actions and errors append to `datasets/control.jsonl` without the token.
Missing/stale probe data prevents new changes. The device retains its last setting
if the controller stops; restart/reconfigure it before a new experimental run.

Model artifacts use pickle: load only files generated by your own workflow.
Evaluate this controller on the same network/probe type as its training data.
Do not apply an HTTP-trained model directly to unrelated Mininet iperf features.

## 7. Controlled Mininet comparisons

On Linux with Mininet, Open vSwitch and iperf3:

```bash
sudo apt install mininet openvswitch-switch iperf3
sudo python3 mininet/run_detailed_experiment.py both --duration 20 --samples 5
python datasets/analyze_controlled.py
```

All TCP/UDP workloads and ping probes run concurrently. Independent iperf servers
use separate ports to avoid server-busy failures. Baseline and optimized share
bandwidth, delay and configured loss. Optimized changes only offered UDP rate
from 3 to 0.5 Mbps. `stream_cap` separately tests reduced phone parallelism.
This is a controlled fixed-action experiment, not a learned policy.

Scenario order is shuffled reproducibly with `--seed`. Results append to
`datasets/controlled_results.csv`; each run's settings and raw output live under
`datasets/runs/`. Existing historical results are preserved. The old baseline and
optimized commands now forward to this runner; obsolete script backups have been removed during repository cleanup.

Missing TCP loss and UDP RTT stay blank. UDP jitter is reported separately.
TCP RTT, when available, comes from sender RTT in microseconds converted to ms;
its min/max span stream means, not individual packet RTTs. ICMP RTT is measured
under load. A confirmed no-reply ping has measured 100% probe loss and missing RTT;
a command/JSON failure has status `failed` and missing metrics.

The summary reports attempts, successes, failures and variation across repetitions.
It never converts failed runs to zero latency or 100% link loss. Compare sensor
RTT/loss and phone throughput together to expose the cost of limiting traffic.
The topology's per-link shaping and intentional host differences remain fixed
between scenarios; they are not a calibrated Wi-Fi airtime simulation.

## 8. Validation and next experiments

### Server dashboard

Open `/dashboard` for the redesigned network monitor. The light interface includes
a device table, sensor traffic/RTT/delivery charts, pause/resume, CSV exports,
chart PNG downloads and saved experiment plots. All assets are local; no external
font, analytics or CDN request is required. Phone stream heartbeats are excluded
from sensor message-rate plots, and client RTT/loss are left unmeasured.

The ESP32 gateway panel reads actual SNMP collector CSV output for the current
run. It does not implement an SNMP agent on the ESP32. Configure it before starting
the Flask server, for example:

```powershell
$env:IOT_GATEWAY_IP = "192.168.8.1"
$env:IOT_GATEWAY_INTERFACE = "2"
$env:IOT_SNMP_CSV = "C:\Users\PC\Documents\Hman_project\datasets\snmp.csv"
```

Use the actual gateway address/interface, and run the collector with the server's
`IOT_RUN_ID`. Samples older than 15 seconds are marked stale and rates are withheld;
use the collector's default five-second polling interval. Without collector data,
the panel explicitly displays “No agent data.” Its presence does not mean the
ESP32 router or its SNMP firmware has been configured.

The dashboard styling and behavior live in `server/static/dashboard.css` and
`server/static/dashboard.js`; the template is `server/templates/dashboard.html`.
The obsolete dashboard backup was removed during repository cleanup.

### Checks

```powershell
python -m unittest discover -s tests -v
node --test tests/dashboard.test.cjs
```

Tests cover delivery gaps, reordering, duplicate messages, independent boots and
protocols, future targets, missing windows, SNMP reset/staleness, held-out sessions,
training-only imputation, measurement parsing, concurrent workload launch,
HTTP APIs, the build/train CLIs and a mocked token-authenticated control action.
They do not substitute for router, ESP32 or Linux Mininet validation.

Suggested next steps:

1. Confirm actual SNMP support and interface identity; run one quiet pilot capture.
2. Check that overload produces measured RTT/failure changes before training.
3. Use several repetitions of no-control, threshold-control and model-control,
   keeping offered workloads comparable and recording the intervention cost.
4. Add grouped/chronological validation on development sessions; reserve separate
   final sessions for reporting. Measure event warning lead time and false alarms/hour.
5. Add queue occupancy or Wi-Fi radio counters only when the device exposes them;
   validate whether they improve held-out performance over generic interface counters.
6. Add a device-side controller lease so a lost controller automatically expires
   a rate limit; the current controller logs actions but leaves the last setting active.
