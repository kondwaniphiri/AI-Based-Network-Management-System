"""Optional Net-SNMP collector. Credentials come from the local snmp.conf.

Supports SNMPv3 or explicitly selected v2c. No credentials are written to CSV.
Uses IF-MIB high-capacity octet counters and discontinuity/uptime checks.
"""
import argparse
import csv
import os
import subprocess
import time

OIDS = {
    "uptime": ".1.3.6.1.2.1.1.3.0",
    "in_octets": ".1.3.6.1.2.1.31.1.1.1.6.{i}",
    "out_octets": ".1.3.6.1.2.1.31.1.1.1.10.{i}",
    "speed_mbps": ".1.3.6.1.2.1.31.1.1.1.15.{i}",
    "discontinuity": ".1.3.6.1.2.1.31.1.1.1.19.{i}",
    "in_discards": ".1.3.6.1.2.1.2.2.1.13.{i}",
    "out_discards": ".1.3.6.1.2.1.2.2.1.19.{i}",
    "in_errors": ".1.3.6.1.2.1.2.2.1.14.{i}",
    "out_errors": ".1.3.6.1.2.1.2.2.1.20.{i}",
    "oper_status": ".1.3.6.1.2.1.2.2.1.8.{i}",
}
COUNTERS = ["in_octets", "out_octets", "in_discards", "out_discards",
            "in_errors", "out_errors"]


def rates(previous, current, elapsed):
    """Reset/ambiguous wrap intervals are missing, never negative or fabricated."""
    if previous is None or elapsed <= 0:
        return {"status": "warmup"}
    if (current["uptime"] < previous["uptime"] or
            current["discontinuity"] != previous["discontinuity"] or
            any(current[k] < previous[k] for k in COUNTERS)):
        return {"status": "counter_reset_or_wrap"}
    result = {"status": "ok"}
    for k in COUNTERS:
        result[k + "_per_sec"] = (current[k] - previous[k]) / elapsed
    for direction in ("in", "out"):
        bps = result[direction + "_octets_per_sec"] * 8
        result[direction + "_bps"] = bps
        result[direction + "_utilization_pct"] = (
            100 * bps / (current["speed_mbps"] * 1e6)
            if current["speed_mbps"] > 0 else None)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--agent", required=True, help="Net-SNMP agent address")
    p.add_argument("--port", type=int, default=161)
    p.add_argument("--interface", type=int, required=True, help="IF-MIB ifIndex")
    p.add_argument("--version", choices=["3", "2c"], default="3")
    p.add_argument("--backend", choices=["net-snmp", "python"], default="net-snmp",
                   help="python supports the ESP32's read-only SNMPv2c without snmpget")
    p.add_argument("--community-env", default="IOT_SNMP_COMMUNITY")
    p.add_argument("--run-id", required=True)
    p.add_argument("--interval", type=float, default=5)
    p.add_argument("--duration", type=float, default=300)
    p.add_argument("--output", default="datasets/snmp.csv")
    args = p.parse_args()
    if min(args.interval, args.duration, args.interface) <= 0:
        p.error("interval, duration and interface must be positive")
    if not 1 <= args.port <= 65535:
        p.error("port must be 1..65535")
    if args.backend == "python" and (args.version != "2c" or not os.environ.get(args.community_env)):
        p.error("python backend requires --version 2c and a community environment variable")
    command = ["snmpget", "-v", args.version, "-Oqv", "-Ot", "-Oe",
               "-t", "1", "-r", "0", args.agent if args.port == 161 else f"udp:{args.agent}:{args.port}"]
    command += [oid.format(i=args.interface) for oid in OIDS.values()]
    fields = ["timestamp", "run_id", "agent", "interface", "status"] + list(OIDS)
    fields += [k + "_per_sec" for k in COUNTERS]
    fields += [d + suffix for d in ("in", "out")
               for suffix in ("_bps", "_utilization_pct")]
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    header = not os.path.exists(args.output) or os.path.getsize(args.output) == 0
    previous, previous_time = None, None
    with open(args.output, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if header:
            writer.writeheader()
        deadline = time.monotonic() + args.duration
        while time.monotonic() < deadline:
            started = time.monotonic()
            row = dict(timestamp=time.time(), run_id=args.run_id,
                       agent=args.agent, interface=args.interface)
            try:
                if args.backend == "python":
                    from snmp_wire import get
                    values = get(args.agent, [oid.format(i=args.interface) for oid in OIDS.values()],
                                 os.environ[args.community_env], port=args.port)
                else:
                    result = subprocess.run(command, capture_output=True, text=True,
                                            timeout=5, check=True)
                    values = result.stdout.strip().splitlines()
                if len(values) != len(OIDS):
                    raise ValueError("incomplete SNMP response")
                current = dict(zip(OIDS, [int(str(v).strip()) for v in values]))
                sampled = time.monotonic()
                row["timestamp"] = time.time()
                row.update(current)
                row.update(rates(previous, current,
                                 sampled - previous_time if previous_time else 0))
                previous, previous_time = current, sampled
            except (OSError, subprocess.SubprocessError, ValueError):
                row["status"] = "collection_failed"
                previous, previous_time = None, None
            writer.writerow(row)
            f.flush()
            time.sleep(max(0, args.interval - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
