"""Read optional collector output for the dashboard; never invent gateway data."""
import csv
import io
import math
import os
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]


def snapshot(run_id):
    agent = os.environ.get("IOT_GATEWAY_IP", "")
    result = dict(agent=agent or None, status="unavailable", timestamp=None,
                  age_seconds=None, interface=None, in_bps=None, out_bps=None,
                  in_utilization_pct=None, out_utilization_pct=None,
                  oper_status=None)
    path = Path(os.environ.get("IOT_SNMP_CSV", str(ROOT / "datasets/snmp.csv")))
    try:
        # Bound file reads even during long experiments. Collector rows have no
        # multiline fields; discard the potentially partial first/last tail row.
        with path.open("rb") as f:
            header = f.readline().decode("utf-8-sig")
            header_end = f.tell()
            start = max(header_end, path.stat().st_size - 65536)
            f.seek(start)
            if start > header_end:
                f.readline()
            tail = f.read().decode("utf-8")
        if tail and not tail.endswith("\n"):
            tail = tail.rsplit("\n", 1)[0] + "\n" if "\n" in tail else ""
        rows = [r for r in csv.DictReader(io.StringIO(header + tail))
                if r.get("run_id") == run_id and (not agent or r.get("agent") == agent)]
        interface = os.environ.get("IOT_GATEWAY_INTERFACE")
        if interface:
            rows = [r for r in rows if r.get("interface") == interface]
        if not rows:
            return result
        row = rows[-1]
        timestamp = float(row["timestamp"])
        if not math.isfinite(timestamp):
            return result
        age = time.time() - timestamp
        result.update(agent=row.get("agent"), timestamp=timestamp,
                      interface=row.get("interface"), age_seconds=max(0, round(age, 1)))
        if age < -5 or age > 15:
            result["status"] = "stale"
            return result
        result["status"] = row.get("status", "collection_failed")
        if result["status"] == "ok":
            for key in ["in_bps", "out_bps", "in_utilization_pct", "out_utilization_pct", "oper_status"]:
                try:
                    value = float(row[key])
                    result[key] = value if math.isfinite(value) and value >= 0 else None
                except (ValueError, TypeError, KeyError):
                    pass
    except (OSError, ValueError, UnicodeError, csv.Error):
        pass
    return result
