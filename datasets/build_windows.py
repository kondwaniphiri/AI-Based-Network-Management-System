"""Build causal features from telemetry exports and independently measured probes."""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

KEYS = ["run_id", "source", "window"]
FEATURES = ["packet_rate", "byte_rate", "packet_size_mean", "packet_size_std",
            "inter_arrival_mean_ms", "udp_fraction", "rtt_ms", "probe_failure_pct"]
SNMP_FEATURES = ["in_bps", "out_bps", "in_utilization_pct", "out_utilization_pct",
                 "in_discards_per_sec", "out_discards_per_sec",
                 "in_errors_per_sec", "out_errors_per_sec"]


def build(events, probes, seconds=5, rtt_limit=100, failure_limit=10,
          snmp=None, agent=None, interface=None, include_targets=True):
    if seconds <= 0 or rtt_limit <= 0 or not 0 < failure_limit <= 100:
        raise ValueError("Invalid window or outcome thresholds")
    events, probes = events.copy(), probes.copy()
    for table in (events, probes):
        table["timestamp"] = pd.to_numeric(table["timestamp"], errors="raise")
        if not np.isfinite(table["timestamp"]).all():
            raise ValueError("Timestamps must be finite epoch seconds")
        table["window"] = np.floor(table["timestamp"] / seconds).astype("int64")
    # Application-level simulated drops remain in raw logs, not receive features.
    events = events[events["received"] == 1].sort_values("timestamp")
    events["iat"] = events.groupby(["run_id", "source"])["timestamp"].diff() * 1000
    events["is_udp"] = events["protocol"].eq("UDP").astype(int)
    traffic = events.groupby(KEYS).agg(
        packet_rate=("bytes", "size"), byte_rate=("bytes", "sum"),
        packet_size_mean=("bytes", "mean"), packet_size_std=("bytes", "std"),
        inter_arrival_mean_ms=("iat", "mean"), udp_fraction=("is_udp", "mean"))
    traffic[["packet_rate", "byte_rate"]] /= seconds
    probes["rtt_ms"] = pd.to_numeric(probes["rtt_ms"], errors="coerce")
    if not probes["success"].isin([0, 1]).all():
        raise ValueError("Probe success must be 0 or 1")
    # Failed probes have no RTT; they are represented by failure percentage.
    probes.loc[probes["success"] == 0, "rtt_ms"] = np.nan
    measured = probes.groupby(KEYS).agg(rtt_ms=("rtt_ms", "mean"),
                                        probe_success=("success", "mean"))
    measured["probe_failure_pct"] = 100 * (1 - measured.pop("probe_success"))
    # The final observed bucket may still be in progress. It cannot provide a
    # complete feature or target window until a later probe closes it.
    last = probes.groupby(["run_id", "source"])["window"].max()
    measured = measured.reset_index().join(last.rename("last_window"),
                                            on=["run_id", "source"])
    measured = measured[measured["window"] < measured["last_window"]]
    measured = measured.drop(columns="last_window").set_index(KEYS)
    # Preserve measured windows with no received traffic (including outages).
    windows = measured.join(traffic).reset_index()
    for col in ["packet_rate", "byte_rate", "packet_size_mean", "packet_size_std",
                "udp_fraction"]:
        windows[col] = windows[col].fillna(0)
    windows["window_end"] = (windows["window"] + 1) * seconds
    if snmp is not None:
        if agent is None or interface is None:
            raise ValueError("Select one SNMP agent/interface explicitly")
        snmp = snmp[(snmp["agent"] == agent) & (snmp["interface"] == interface)].copy()
        if snmp.empty:
            raise ValueError("No SNMP rows match selected agent/interface")
        # Include failed observations: they break freshness instead of hiding gaps.
        snmp.loc[snmp["status"] != "ok", SNMP_FEATURES] = np.nan
        parts = []
        for run, group in windows.groupby("run_id"):
            samples = snmp[snmp["run_id"] == run].sort_values("timestamp")
            parts.append(pd.merge_asof(group.sort_values("window_end"),
                         samples[["timestamp"] + SNMP_FEATURES],
                         left_on="window_end", right_on="timestamp",
                         direction="backward", tolerance=2 * seconds))
        windows = pd.concat(parts, ignore_index=True)
    if not include_targets:
        return windows.sort_values(KEYS).reset_index(drop=True)
    # Labels come ONLY from the immediately following measured window.
    future = measured.reset_index()
    future["target"] = np.where(
        (future["rtt_ms"] > rtt_limit) |
        (future["probe_failure_pct"] >= failure_limit), "degraded", "normal")
    future["window"] -= 1
    future = future[KEYS + ["target", "rtt_ms"]].rename(
        columns={"rtt_ms": "next_rtt_ms"})
    result = windows.merge(future, on=KEYS, how="inner")
    result["window_seconds"] = seconds
    result["rtt_limit_ms"] = rtt_limit
    result["failure_limit_pct"] = failure_limit
    return result.sort_values(KEYS).reset_index(drop=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--events", nargs="+", required=True, help="/api/telemetry/export CSVs")
    p.add_argument("--probes", nargs="+", required=True)
    p.add_argument("--seconds", type=float, default=5)
    p.add_argument("--rtt-limit", type=float, default=100)
    p.add_argument("--failure-limit", type=float, default=10)
    p.add_argument("--snmp")
    p.add_argument("--agent")
    p.add_argument("--interface", type=int)
    p.add_argument("--output", default="datasets/windows.csv")
    args = p.parse_args()
    events = pd.concat([pd.read_csv(f) for f in args.events], ignore_index=True)
    # Repeated server exports may overlap. Exact duplicate log rows count once.
    events = events.drop_duplicates()
    probes = pd.concat([pd.read_csv(f) for f in args.probes], ignore_index=True).drop_duplicates()
    out = build(events, probes, args.seconds, args.rtt_limit, args.failure_limit,
                pd.read_csv(args.snmp) if args.snmp else None, args.agent, args.interface)
    if out.empty:
        p.error("No consecutive measured windows; check run IDs, source IPs and timestamps")
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.output, index=False)
    print(f"Saved {len(out)} future-outcome windows to {args.output}")


if __name__ == "__main__":
    main()
