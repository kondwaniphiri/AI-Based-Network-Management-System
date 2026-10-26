"""
Phase 4 – Data Processing Pipeline
====================================
Reads the dataset CSV (datasets/dataset (esp32 x3, phone x 3).csv) and produces
a clean, AI-ready feature dataset (datasets/processed_dataset.csv).

Usage
-----
    python datasets/process_packets.py
    python datasets/process_packets.py --input datasets/dataset\ \(esp32\ x3\,\ phone\ x\ 3\).csv
                                        --output datasets/processed_dataset.csv

Expected CSV columns:
    No., Time, Source, Destination, Protocol, Length, Info

Output columns:
    time, protocol, packet_size, latency_ms, source, destination,
    inter_arrival_ms, rate_per_sec, label
"""

import argparse
import os
import re
import sys

import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE   = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INPUT  = os.path.join(_HERE, "dataset (esp32 x3, phone x 3).csv")
DEFAULT_OUTPUT = os.path.join(_HERE, "processed_dataset.csv")

# ---------------------------------------------------------------------------
# Protocol normalisation map
# ---------------------------------------------------------------------------
PROTO_MAP = {
    "HTTP": "HTTP", "TCP": "TCP", "UDP": "UDP",
    "ICMP": "ICMP", "ICMPV6": "ICMP", "DNS": "UDP",
    "TLSV1.2": "TCP", "TLSV1.3": "TCP", "SSL": "TCP",
}
# Keys are compared after normalise_proto() uppercases the input. Add new
# protocol aliases here in uppercase and map them to the desired class.

def normalise_proto(p: str) -> str:
    p = str(p).strip().upper()
    return PROTO_MAP.get(p, p)

# ---------------------------------------------------------------------------
# Label rules
# ---------------------------------------------------------------------------
# Recalibrate this threshold when capture duration or source count changes.
# LARGE_PKT_THRESHOLD is reserved for future packet-size labelling.
HIGH_RATE_THRESHOLD   = 30   # packets/sec from one source → congested
LARGE_PKT_THRESHOLD   = 900  # bytes — large frames
RETRANS_PATTERN       = re.compile(r"Retransmission|Dup ACK|Out-Of-Order", re.I)

def assign_label(row, source_rate: float) -> str:
    """
    Three classes:
      high_loss   – TCP retransmission or duplicate ACK seen in Info field
      congested   – source emitting > HIGH_RATE_THRESHOLD packets/sec
      normal      – everything else
    """
    if RETRANS_PATTERN.search(str(row.get("Info", ""))):
        return "high_loss"
    if source_rate > HIGH_RATE_THRESHOLD:
        return "congested"
    return "normal"

# ---------------------------------------------------------------------------
# ICMP RTT extraction
# ---------------------------------------------------------------------------
RTT_PATTERN = re.compile(r"time=(\d+(?:\.\d+)?)\s*ms", re.I)

def extract_icmp_rtt(info: str) -> float | None:
    m = RTT_PATTERN.search(str(info))
    return float(m.group(1)) if m else None

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def process(input_path: str, output_path: str) -> None:
    if not os.path.exists(input_path):
        sys.exit(
            f"[ERROR] Input file not found: {input_path}\n"
            "        Ensure you have the dataset file:\n"
            f"        {os.path.join(_HERE, 'dataset (esp32 x3, phone x 3).csv')}"
        )

    print(f"[+] Reading {input_path} …")
    raw = pd.read_csv(input_path, dtype=str)

    # Rename columns to lowercase and strip whitespace
    raw.columns = [c.strip().lower().replace(".", "").replace(" ", "_")
                   for c in raw.columns]

    # Minimum required columns
    required = {"time", "source", "destination", "protocol", "length"}
    if not required.issubset(raw.columns):
        missing = required - set(raw.columns)
        sys.exit(f"[ERROR] CSV missing required columns: {missing}\n"
                 f"        Found columns: {list(raw.columns)}")

    # Drop empty rows
    raw = raw.dropna(subset=["time", "source", "destination"]).reset_index(drop=True)

    print(f"[+] {len(raw):,} raw packets loaded.")

    # Parse numeric types
    raw["time"]     = pd.to_numeric(raw["time"],   errors="coerce")
    raw["length"]   = pd.to_numeric(raw["length"], errors="coerce").fillna(0).astype(int)
    raw             = raw.dropna(subset=["time"]).reset_index(drop=True)

    # Normalise protocol
    raw["proto_norm"] = raw["protocol"].apply(normalise_proto)

    # Inter-arrival time per source (ms)
    raw = raw.sort_values("time").reset_index(drop=True)
    raw["inter_arrival_ms"] = (
        raw.groupby("source")["time"]
           .diff()
           .fillna(0) * 1000
    ).round(3)

    # Packets-per-second rate for labelling. time_sec creates fixed integer
    # second buckets (not a true rolling window), so change this logic if a
    # sliding one-second rate is required.
    raw["time_sec"] = raw["time"].apply(int)
    rate_df = (
        raw.groupby(["source", "time_sec"])
           .size()
           .reset_index(name="rate_per_sec")
    )
    raw = raw.merge(rate_df, on=["source", "time_sec"], how="left")
    raw["rate_per_sec"] = raw["rate_per_sec"].fillna(1)

    # Latency (ms): ICMP RTT where available; otherwise this is an inter-arrival
    # proxy, not a direct network-latency measurement.
    if "info" in raw.columns:
        raw["latency_ms"] = raw.apply(
            lambda r: extract_icmp_rtt(r["info"])
                      if r["proto_norm"] == "ICMP"
                      else None,
            axis=1,
        )
    else:
        raw["latency_ms"] = None

    # Never substitute inter-arrival timing for RTT.
    # Missing measured RTT remains missing; inter-arrival is a separate feature.
    raw["latency_ms"] = pd.to_numeric(raw["latency_ms"], errors="coerce")

    # Labels
    raw["label"] = raw.apply(
        lambda r: assign_label(
            {"Info": r.get("info", "")},
            r["rate_per_sec"]
        ),
        axis=1,
    )

    # Build output dataframe
    out = raw.rename(columns={
        "time":     "time",
        "length":   "packet_size",
        "source":   "source",
        "destination": "destination",
        "proto_norm": "protocol",
    })[["time", "protocol", "packet_size", "latency_ms",
        "source", "destination", "inter_arrival_ms", "rate_per_sec", "label"]]

    # Remove duplicates (same time + source + dest + size)
    before = len(out)
    out = out.drop_duplicates(subset=["time", "source", "destination", "packet_size"])
    print(f"[+] Removed {before - len(out):,} duplicate rows.")

    # Keep project subnets. Extend this regex when experiments use another
    # private range; otherwise valid packets will be discarded as noise.
    ip_filter = out["source"].str.match(r"^(192\.168\.|10\.|172\.|127\.)")
    removed_noise = (~ip_filter).sum()
    out = out[ip_filter].reset_index(drop=True)
    print(f"[+] Removed {removed_noise:,} non-project-IP rows.")

    # Normalise packet_size (min-max) — add as extra column
    mn, mx = out["packet_size"].min(), out["packet_size"].max()
    out["packet_size_norm"] = ((out["packet_size"] - mn) / (mx - mn + 1e-9)).round(4)

    label_counts = out["label"].value_counts().to_dict()
    print(f"[+] Label distribution: {label_counts}")
    print(f"[+] Total clean rows: {len(out):,}")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    out.to_csv(output_path, index=False)
    print(f"[+] Saved → {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 4 – Process Wireshark CSV")
    parser.add_argument("--input",  default=DEFAULT_INPUT,  help="Path to raw Wireshark CSV")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Path for processed CSV output")
    args = parser.parse_args()
    process(args.input, args.output)
