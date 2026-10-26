"""Collect actual HTTP request RTT. Run on the server PC against each ESP32."""
import argparse
import csv
import os
import time
from urllib.parse import urlparse

import requests


def target(value):
    """Accept SOURCE_IP=http://GATEWAY:PORT/data for NAT-forwarded probes."""
    if value.startswith(("http://", "https://")):
        source, url = urlparse(value).hostname, value
    else:
        source, separator, url = value.partition("=")
        if not separator:
            raise ValueError("Expected URL or SOURCE_IP=URL")
    parsed = urlparse(url)
    if not source or parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Invalid probe target")
    return source, url


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--targets", nargs="+", required=True, help="http://DEVICE/data URLs")
    p.add_argument("--run-id", required=True)
    p.add_argument("--output", default="datasets/probes.csv")
    p.add_argument("--interval", type=float, default=1)
    p.add_argument("--duration", type=float, default=300)
    p.add_argument("--timeout", type=float, default=2)
    args = p.parse_args()
    try:
        targets = [target(value) for value in args.targets]
    except ValueError as exc:
        p.error(str(exc))
    if min(args.interval, args.duration, args.timeout) <= 0:
        p.error("interval, duration and timeout must be positive")
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    fields = ["timestamp", "run_id", "source", "rtt_ms", "success", "error"]
    header = not os.path.exists(args.output) or os.path.getsize(args.output) == 0
    # One worker per endpoint avoids serial timeouts distorting other probes.
    from concurrent.futures import ThreadPoolExecutor
    def probe(item):
        source, url = item
        start = time.perf_counter()
        try:
            r = requests.get(url, timeout=args.timeout)
            r.raise_for_status()
            return [time.time(), args.run_id, source,
                    (time.perf_counter() - start) * 1000, 1, ""]
        except requests.RequestException as exc:
            return [time.time(), args.run_id, source, "", 0,
                    type(exc).__name__]
    with open(args.output, "a", newline="", encoding="utf-8") as f, \
            ThreadPoolExecutor(max_workers=len(args.targets)) as pool:
        writer = csv.writer(f)
        if header:
            writer.writerow(fields)
        deadline = time.monotonic() + args.duration
        while time.monotonic() < deadline:
            started = time.monotonic()
            writer.writerows(pool.map(probe, targets))
            f.flush()
            time.sleep(max(0, args.interval - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
