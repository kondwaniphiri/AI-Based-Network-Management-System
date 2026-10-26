"""
Detailed Results Analysis & Comparison
========================================
Reads detailed_results.csv and generates:
  - Before/after comparison table
  - Detailed charts with min/avg/max bounds
  - Improvement percentages
  - Statistical summary

Usage
-----
    python datasets/analyze_detailed_results.py
    python datasets/analyze_detailed_results.py --results datasets/detailed_results.csv
"""

import argparse
import os
import sys
from typing import Dict, List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RESULTS = os.path.join(_HERE, "controlled_results.csv")
DEFAULT_OUT = os.path.join(_HERE, "..", "graphs")

SCENARIO_COLORS = {"baseline": "#e50914", "optimized": "#4ade80"}
HOST_COLORS = {
    "esp32_1": "#7c6dfb", "esp32_2": "#fb923c",
    "esp32_3": "#f87171", "phone1": "#22d3ee",
    "phone2": "#facc15", "server": "#a3a3a3",
}


def _dark_fig(nrows=1, ncols=1, figsize=(12, 6)):
    """Create dark-themed figure."""
    fig, ax = plt.subplots(nrows, ncols, figsize=figsize, facecolor="#0d1117")
    axes = [ax] if (nrows == 1 and ncols == 1) else list(np.array(ax).flat)
    for a in axes:
        a.set_facecolor("#161b22")
        a.tick_params(colors="#8b949e")
        a.xaxis.label.set_color("#8b949e")
        a.yaxis.label.set_color("#8b949e")
        a.title.set_color("#e6edf3")
        for spine in a.spines.values():
            spine.set_edgecolor("#30363d")
        a.grid(color="#21262d", linestyle="--", linewidth=0.6, alpha=0.5)
    return fig, axes


def _savefig(fig, path: str):
    """Save figure and close."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="#0d1117")
    plt.close(fig)
    print(f"[+] Saved → {path}")


def print_comparison_table(df: pd.DataFrame):
    """Print detailed before/after comparison."""
    print("\n" + "="*120)
    print("DETAILED RESULTS COMPARISON: BASELINE vs OPTIMIZED")
    print("="*120 + "\n")

    # Filter for each protocol type
    for protocol in ["ICMP", "TCP", "UDP"]:
        proto_data = df[df["protocol"] == protocol]
        if proto_data.empty:
            continue

        print(f"\n{'─'*120}")
        print(f"  {protocol} MEASUREMENTS")
        print(f"{'─'*120}")
        print(f"{'Host':<12} {'Metric':<20} {'Baseline':<30} {'Optimized':<30} {'Improvement':<25}")
        print(f"{'-'*120}")

        for host in proto_data["host"].unique():
            host_data = proto_data[proto_data["host"] == host]

            baseline = host_data[host_data["scenario"] == "baseline"]
            optimized = host_data[host_data["scenario"] == "optimized"]

            if baseline.empty or optimized.empty:
                continue

            baseline_row = baseline.iloc[0]
            optimized_row = optimized.iloc[0]

            # Latency
            if protocol != "UDP" or baseline_row["latency_ms_avg"] > 0:
                b_lat = baseline_row["latency_ms_avg"]
                o_lat = optimized_row["latency_ms_avg"]
                lat_imp = ((b_lat - o_lat) / b_lat * 100) if b_lat > 0 else 0

                print(f"{host:<12} {'Latency (ms)':<20} "
                      f"avg={b_lat:.3f} (min={baseline_row['latency_ms_min']:.3f}, max={baseline_row['latency_ms_max']:.3f})  "
                      f"avg={o_lat:.3f} (min={optimized_row['latency_ms_min']:.3f}, max={optimized_row['latency_ms_max']:.3f})  "
                      f"{lat_imp:+.1f}% {'↓' if lat_imp > 0 else '↑'}")

            # Throughput
            b_tp = baseline_row["throughput_kbps"]
            o_tp = optimized_row["throughput_kbps"]
            tp_imp = ((o_tp - b_tp) / b_tp * 100) if b_tp > 0 else 0

            print(f"{'':<12} {'Throughput (kbps)':<20} "
                  f"{b_tp:>28.1f}  {o_tp:>28.1f}  "
                  f"{tp_imp:+.1f}% {'↑' if tp_imp > 0 else '↓'}")

            # Packet Loss
            b_loss = baseline_row["loss_pct"]
            o_loss = optimized_row["loss_pct"]
            loss_imp = ((b_loss - o_loss) / b_loss * 100) if b_loss > 0 else 0

            print(f"{'':<12} {'Packet Loss (%)':<20} "
                  f"{b_loss:>28.2f}  {o_loss:>28.2f}  "
                  f"{loss_imp:+.1f}% {'↓' if loss_imp > 0 else '↑'}")
            print()

    print(f"{'='*120}\n")


def plot_latency_comparison(df: pd.DataFrame, out_dir: str):
    """Plot latency with min/max bounds."""
    icmp_data = df[df["protocol"] == "ICMP"].copy()
    if icmp_data.empty:
        return

    fig, (ax,) = _dark_fig(figsize=(12, 6))

    hosts = sorted(icmp_data["host"].unique())
    x = np.arange(len(hosts))
    width = 0.35

    baseline_avg = []
    baseline_err = []
    optimized_avg = []
    optimized_err = []

    for host in hosts:
        host_data = icmp_data[icmp_data["host"] == host]

        b = host_data[host_data["scenario"] == "baseline"]
        if not b.empty:
            b_row = b.iloc[0]
            baseline_avg.append(b_row["latency_ms_avg"])
            baseline_err.append([
                b_row["latency_ms_avg"] - b_row["latency_ms_min"],
                b_row["latency_ms_max"] - b_row["latency_ms_avg"]
            ])
        else:
            baseline_avg.append(0)
            baseline_err.append([0, 0])

        o = host_data[host_data["scenario"] == "optimized"]
        if not o.empty:
            o_row = o.iloc[0]
            optimized_avg.append(o_row["latency_ms_avg"])
            optimized_err.append([
                o_row["latency_ms_avg"] - o_row["latency_ms_min"],
                o_row["latency_ms_max"] - o_row["latency_ms_avg"]
            ])
        else:
            optimized_avg.append(0)
            optimized_err.append([0, 0])

    # Convert error lists to arrays for plotting
    baseline_err_lower = [e[0] for e in baseline_err]
    baseline_err_upper = [e[1] for e in baseline_err]
    optimized_err_lower = [e[0] for e in optimized_err]
    optimized_err_upper = [e[1] for e in optimized_err]

    ax.bar(x - width/2, baseline_avg, width, label="Baseline",
           color=SCENARIO_COLORS["baseline"], alpha=0.8,
           yerr=[baseline_err_lower, baseline_err_upper], capsize=5, error_kw={"linewidth": 2})
    ax.bar(x + width/2, optimized_avg, width, label="Optimized",
           color=SCENARIO_COLORS["optimized"], alpha=0.8,
           yerr=[optimized_err_lower, optimized_err_upper], capsize=5, error_kw={"linewidth": 2})

    ax.set_xlabel("Host", fontsize=11, fontweight="bold")
    ax.set_ylabel("Latency (ms)", fontsize=11, fontweight="bold")
    ax.set_title("ICMP Latency Comparison (with min/max bounds)", fontsize=12, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(hosts)
    ax.legend(fontsize=10)

    _savefig(fig, os.path.join(out_dir, "01_latency_comparison.png"))


def plot_throughput_comparison(df: pd.DataFrame, out_dir: str):
    """Plot throughput comparison."""
    data = df[df["protocol"].isin(["TCP", "UDP"])].copy()
    if data.empty:
        return

    fig, (ax,) = _dark_fig(figsize=(12, 6))

    hosts = sorted(data["host"].unique())
    x = np.arange(len(hosts))
    width = 0.35

    baseline_tp = []
    optimized_tp = []

    for host in hosts:
        host_data = data[data["host"] == host]

        b = host_data[host_data["scenario"] == "baseline"]
        baseline_tp.append(b["throughput_kbps"].values[0] if not b.empty else 0)

        o = host_data[host_data["scenario"] == "optimized"]
        optimized_tp.append(o["throughput_kbps"].values[0] if not o.empty else 0)

    ax.bar(x - width/2, baseline_tp, width, label="Baseline",
           color=SCENARIO_COLORS["baseline"], alpha=0.8)
    ax.bar(x + width/2, optimized_tp, width, label="Optimized",
           color=SCENARIO_COLORS["optimized"], alpha=0.8)

    ax.set_xlabel("Host", fontsize=11, fontweight="bold")
    ax.set_ylabel("Throughput (kbps)", fontsize=11, fontweight="bold")
    ax.set_title("Throughput Comparison (TCP/UDP)", fontsize=12, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(hosts, rotation=0)
    ax.legend(fontsize=10)

    _savefig(fig, os.path.join(out_dir, "02_throughput_comparison.png"))


def plot_packet_loss_comparison(df: pd.DataFrame, out_dir: str):
    """Plot packet loss comparison."""
    data = df[df["protocol"].isin(["TCP", "UDP"])].copy()
    if data.empty:
        return

    fig, (ax,) = _dark_fig(figsize=(12, 6))

    hosts = sorted(data["host"].unique())
    x = np.arange(len(hosts))
    width = 0.35

    baseline_loss = []
    optimized_loss = []

    for host in hosts:
        host_data = data[data["host"] == host]

        b = host_data[host_data["scenario"] == "baseline"]
        baseline_loss.append(b["loss_pct"].values[0] if not b.empty else 0)

        o = host_data[host_data["scenario"] == "optimized"]
        optimized_loss.append(o["loss_pct"].values[0] if not o.empty else 0)

    ax.bar(x - width/2, baseline_loss, width, label="Baseline",
           color=SCENARIO_COLORS["baseline"], alpha=0.8)
    ax.bar(x + width/2, optimized_loss, width, label="Optimized",
           color=SCENARIO_COLORS["optimized"], alpha=0.8)

    ax.set_xlabel("Host", fontsize=11, fontweight="bold")
    ax.set_ylabel("Packet Loss (%)", fontsize=11, fontweight="bold")
    ax.set_title("Packet Loss Comparison", fontsize=12, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(hosts, rotation=0)
    ax.legend(fontsize=10)

    _savefig(fig, os.path.join(out_dir, "03_packet_loss_comparison.png"))


def main():
    parser = argparse.ArgumentParser(description="Analyze detailed network results")
    parser.add_argument("--results", default=DEFAULT_RESULTS,
                        help="Path to detailed_results.csv")
    parser.add_argument("--out", default=DEFAULT_OUT,
                        help="Output directory for graphs")

    args = parser.parse_args()

    if not os.path.exists(args.results):
        print(f"[ERROR] Results file not found: {args.results}")
        print(f"[HINT]  Run: sudo python mininet/run_detailed_experiment.py baseline")
        sys.exit(1)

    df = pd.read_csv(args.results)

    print(f"[+] Loaded {len(df)} measurements from {args.results}")

    if "status" in df.columns:
        from analyze_controlled import summarize
        os.makedirs(args.out, exist_ok=True)
        summary = summarize(df)
        summary.to_csv(os.path.join(args.out, "controlled_summary.csv"), index=False)
        print(summary.to_string(index=False))
        return

    print("Historical file: missing/failure semantics were not recorded.")
    # Print console table
    print_comparison_table(df)

    # Generate graphs
    print("\n[+] Generating comparison graphs…")
    plot_latency_comparison(df, args.out)
    plot_throughput_comparison(df, args.out)
    plot_packet_loss_comparison(df, args.out)

    print("\n[✓] Analysis complete!")


if __name__ == "__main__":
    main()
