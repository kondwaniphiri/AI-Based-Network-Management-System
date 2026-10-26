"""Observe, threshold-control or model-control one UDP source using completed windows.

Reads probe CSV written by probe.py. Dry-run by default. --apply requires the
same IOT_CONTROL_TOKEN environment variable as CONTROL_TOKEN in ESP32 firmware.
"""
import argparse
import io
import json
import os
import pickle
import sys
import time
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "datasets"))
from build_windows import build


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--server", required=True, help="http://SERVER:5000")
    p.add_argument("--device", required=True, help="http://UDP_DEVICE")
    p.add_argument("--source", required=True, help="Observed source IP whose service is protected")
    p.add_argument("--run-id", required=True)
    p.add_argument("--probes", default="datasets/probes.csv")
    p.add_argument("--strategy", choices=["observe", "threshold", "model"], default="observe")
    p.add_argument("--model", default="datasets/window_model/classifier.pkl")
    p.add_argument("--seconds", type=float, default=5)
    p.add_argument("--rtt-limit", type=float, default=100)
    p.add_argument("--failure-limit", type=float, default=10)
    p.add_argument("--duration", type=float, default=300)
    p.add_argument("--normal-interval", type=int, default=1100)
    p.add_argument("--limited-interval", type=int, default=3000)
    p.add_argument("--cooldown", type=float, default=15)
    p.add_argument("--snmp")
    p.add_argument("--agent")
    p.add_argument("--interface", type=int)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--output", default="datasets/control.jsonl")
    args = p.parse_args()
    if min(args.seconds, args.duration, args.rtt_limit) <= 0 or args.cooldown < 0:
        p.error("Invalid timing parameters")
    if not 50 <= args.normal_interval <= args.limited_interval <= 10000:
        p.error("Intervals must satisfy 50 <= normal <= limited <= 10000 ms")
    token = os.environ.get("IOT_CONTROL_TOKEN", "")
    if args.apply and not token:
        p.error("--apply requires IOT_CONTROL_TOKEN")
    bundle = None
    if args.strategy == "model":
        with open(args.model, "rb") as f:
            bundle = pickle.load(f)
        args.seconds = bundle["metadata"]["window_seconds"]
        args.rtt_limit = bundle["metadata"]["rtt_limit_ms"]
        args.failure_limit = bundle["metadata"]["failure_limit_pct"]
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    deadline, last_change, seen, healthy = time.monotonic() + args.duration, -float("inf"), None, 0
    current = None
    with open(args.output, "a", encoding="utf-8") as log:
        while time.monotonic() < deadline:
            record = dict(timestamp=time.time(), run_id=args.run_id, strategy=args.strategy)
            try:
                response = requests.get(args.server.rstrip("/") + "/api/telemetry/export",
                    params={"run_id": args.run_id, "since": time.time() - 10 * args.seconds}, timeout=5)
                response.raise_for_status()
                events = pd.read_csv(io.StringIO(response.text))
                probes = pd.read_csv(args.probes)
                events = events[events["run_id"] == args.run_id]
                probes = probes[(probes["run_id"] == args.run_id) & (probes["source"] == args.source)]
                snmp = pd.read_csv(args.snmp) if args.snmp else None
                windows = build(events, probes, args.seconds, args.rtt_limit,
                                args.failure_limit, snmp, args.agent, args.interface,
                                include_targets=False)
                windows = windows[(windows["source"] == args.source) &
                                  (windows["window_end"] <= time.time())]
                if windows.empty:
                    raise ValueError("Waiting for a complete measured window")
                latest = windows.iloc[-1:]
                end = float(latest["window_end"].iloc[0])
                if time.time() - end > 2 * args.seconds:
                    raise ValueError("Probe data is stale; no action")
                if seen == end:
                    time.sleep(min(args.seconds, 1))
                    continue
                if seen is not None and end - seen > args.seconds * 1.5:
                    healthy = 0
                seen = end
                degraded = bool((latest["rtt_ms"].iloc[0] > args.rtt_limit) or
                                (latest["probe_failure_pct"].iloc[0] >= args.failure_limit))
                if bundle:
                    degraded = bundle["model"].predict(latest[bundle["features"]])[0] == "degraded"
                healthy = 0 if degraded else healthy + 1
                desired = args.limited_interval if degraded else args.normal_interval
                record.update(window_end=end, predicted_degraded=degraded, desired_interval_ms=desired,
                              status="observed")
                # Three healthy windows to restore traffic; one bad window to limit.
                if (args.strategy != "observe" and desired != current and
                        (degraded or healthy >= 3) and time.monotonic() - last_change >= args.cooldown):
                    if args.apply:
                        result = requests.post(args.device.rstrip("/") + "/config",
                            data={"udp_interval_ms": desired},
                            headers={"X-Control-Token": token}, timeout=3)
                        result.raise_for_status()
                    current, last_change = desired, time.monotonic()
                    record["status"] = "applied" if args.apply else "dry_run"
            except (requests.RequestException, OSError, ValueError, KeyError) as exc:
                healthy = 0
                record.update(status="error", error=str(exc))
            log.write(json.dumps(record) + "\n")
            log.flush()
            print(json.dumps(record))
            time.sleep(min(args.seconds, 1))


if __name__ == "__main__":
    main()
