"""
Phase 9 + 10 – Data Analysis & Comparison
==========================================
Reads experiment_results.csv (from Mininet runs) and optionally
processed_dataset.csv (from Wireshark capture), generates 5 charts,
and prints a before/after comparison table.

Usage
-----
    python datasets/analyze_results.py
    python datasets/analyze_results.py --results datasets/experiment_results.csv
                                        --capture datasets/processed_dataset.csv
                                        --out graphs/

Outputs (saved to graphs/)
--------------------------
    latency.png              Phase 9 graph 1
    packet_loss.png          Phase 9 graph 2
    throughput.png           Phase 9 graph 3
    protocol_comparison.png  Phase 9 graph 4
    comparison_table.png     Phase 10 before/after table
"""

import argparse
import os
import sys

import matplotlib
matplotlib.use("Agg")   # non-interactive backend — works without a display
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE     = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RESULTS = os.path.join(_HERE, "experiment_results.csv")
DEFAULT_CAPTURE = os.path.join(_HERE, "processed_dataset.csv")
DEFAULT_OUT     = os.path.join(_HERE, "..", "graphs")

SCENARIO_COLORS = {"baseline": "#e50914", "optimized": "#4ade80"}
HOST_COLORS     = {
    "esp32_1": "#7c6dfb", "esp32_2": "#fb923c",
    "esp32_3": "#f87171", "phone1":  "#22d3ee",
    "phone2":  "#facc15", "server":  "#a3a3a3",
}


def _check_imports():
    for pkg in ("matplotlib", "numpy", "pandas"):
        try:
            __import__(pkg)
        except ImportError:
            sys.exit(f"[ERROR] Missing package: {pkg}\n"
                     "        pip install matplotlib numpy pandas")


def _savefig(fig, path: str):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"[+] Saved → {path}")


def _dark_fig(nrows=1, ncols=1, figsize=(10, 5)):
    fig, ax = plt.subplots(nrows, ncols, figsize=figsize,
                           facecolor="#0d1117")
    axes = [ax] if (nrows == 1 and ncols == 1) else list(np.array(ax).flat)
    for a in axes:
        a.set_facecolor("#161b22")
        a.tick_params(colors="#8b949e")
        a.xaxis.label.set_color("#8b949e")
        a.yaxis.label.set_color("#8b949e")
        a.title.set_color("#e6edf3")
        for spine in a.spines.values():
            spine.set_edgecolor("#30363d")
        a.grid(color="#21262d", linestyle="--", linewidth=0.6)
    return fig, axes


# ── Graph 1: Latency Over Time (ICMP rows, grouped by scenario) ─────────
def plot_latency(df: pd.DataFrame, out_dir: str):
    icmp = df[(df["protocol"] == "ICMP") & (df["latency_ms"] > 0)].copy()
    if icmp.empty:
        print("[!] No ICMP latency rows found — skipping latency.png")
        return

    fig, (ax,) = _dark_fig(figsize=(11, 5))
    ax.set_title("Latency Over Time (ICMP RTT per Host)")
    ax.set_xlabel("Measurement index")
    ax.set_ylabel("Latency (ms)")

    for scenario, grp in icmp.groupby("scenario"):
        color = SCENARIO_COLORS.get(scenario, "#888")
        for host, hgrp in grp.groupby("host"):
            hgrp = hgrp.reset_index(drop=True)
            ax.plot(hgrp.index, hgrp["latency_ms"],
                    marker="o", markersize=4,
                    color=HOST_COLORS.get(host, color),
                    linestyle="--" if scenario == "baseline" else "-",
                    linewidth=1.6,
                    label=f"{host} ({scenario})")

    ax.legend(fontsize=8, facecolor="#21262d", edgecolor="#30363d",
              labelcolor="#e6edf3", ncol=2)
    _savefig(fig, os.path.join(out_dir, "latency.png"))


# ── Graph 2: Packet Loss per host per scenario ───────────────────────────
def plot_packet_loss(df: pd.DataFrame, out_dir: str):
    df_loss = df[df["loss_pct"] >= 0].copy()
    if df_loss.empty:
        print("[!] No loss data — skipping packet_loss.png")
        return

    hosts     = sorted(df_loss["host"].unique())
    scenarios = sorted(df_loss["scenario"].unique())
    x         = np.arange(len(hosts))
    width     = 0.35

    fig, (ax,) = _dark_fig(figsize=(11, 5))
    ax.set_title("Packet Loss per Host and Scenario")
    ax.set_xlabel("Host")
    ax.set_ylabel("Packet Loss (%)")
    ax.set_xticks(x)
    ax.set_xticklabels(hosts, color="#e6edf3")

    for i, scenario in enumerate(scenarios):
        vals = []
        for host in hosts:
            row = df_loss[(df_loss["scenario"] == scenario) & (df_loss["host"] == host)]
            vals.append(row["loss_pct"].mean() if not row.empty else 0.0)
        offset = (i - len(scenarios) / 2 + 0.5) * width
        bars = ax.bar(x + offset, vals, width,
                      label=scenario,
                      color=SCENARIO_COLORS.get(scenario, "#888"),
                      alpha=0.85, edgecolor="#0d1117")
        for bar, v in zip(bars, vals):
            if v > 0:
                ax.text(bar.get_x() + bar.get_width() / 2,
                        bar.get_height() + 0.05,
                        f"{v:.1f}%", ha="center", va="bottom",
                        fontsize=8, color="#e6edf3")

    ax.legend(facecolor="#21262d", edgecolor="#30363d", labelcolor="#e6edf3")
    _savefig(fig, os.path.join(out_dir, "packet_loss.png"))


# ── Graph 3: Throughput per host per scenario ────────────────────────────
def plot_throughput(df: pd.DataFrame, out_dir: str):
    df_tp = df[df["throughput_kbps"] > 0].copy()
    if df_tp.empty:
        print("[!] No throughput data — skipping throughput.png")
        return

    hosts     = sorted(df_tp["host"].unique())
    scenarios = sorted(df_tp["scenario"].unique())
    x         = np.arange(len(hosts))
    width     = 0.35

    fig, (ax,) = _dark_fig(figsize=(11, 5))
    ax.set_title("Throughput per Host and Scenario")
    ax.set_xlabel("Host")
    ax.set_ylabel("Throughput (Kbps)")
    ax.set_xticks(x)
    ax.set_xticklabels(hosts, color="#e6edf3")

    for i, scenario in enumerate(scenarios):
        vals = []
        for host in hosts:
            row = df_tp[(df_tp["scenario"] == scenario) & (df_tp["host"] == host)]
            vals.append(row["throughput_kbps"].mean() if not row.empty else 0.0)
        offset = (i - len(scenarios) / 2 + 0.5) * width
        bars = ax.bar(x + offset, vals, width,
                      label=scenario,
                      color=SCENARIO_COLORS.get(scenario, "#888"),
                      alpha=0.85, edgecolor="#0d1117")

    ax.legend(facecolor="#21262d", edgecolor="#30363d", labelcolor="#e6edf3")
    _savefig(fig, os.path.join(out_dir, "throughput.png"))


# ── Graph 4: Protocol Comparison ─────────────────────────────────────────
def plot_protocol_comparison(df: pd.DataFrame, out_dir: str):
    metrics = (df[df["throughput_kbps"] > 0]
               .groupby(["scenario", "protocol"])["throughput_kbps"]
               .mean()
               .reset_index())
    if metrics.empty:
        print("[!] No throughput data — skipping protocol_comparison.png")
        return

    protos    = sorted(metrics["protocol"].unique())
    scenarios = sorted(metrics["scenario"].unique())
    x         = np.arange(len(protos))
    width     = 0.35

    fig, (ax,) = _dark_fig(figsize=(10, 5))
    ax.set_title("Protocol Comparison – Avg Throughput by Scenario")
    ax.set_xlabel("Protocol")
    ax.set_ylabel("Avg Throughput (Kbps)")
    ax.set_xticks(x)
    ax.set_xticklabels(protos, color="#e6edf3")

    for i, scenario in enumerate(scenarios):
        vals = []
        for proto in protos:
            row = metrics[(metrics["scenario"] == scenario) & (metrics["protocol"] == proto)]
            vals.append(float(row["throughput_kbps"].iloc[0]) if not row.empty else 0.0)
        offset = (i - len(scenarios) / 2 + 0.5) * width
        ax.bar(x + offset, vals, width,
               label=scenario,
               color=SCENARIO_COLORS.get(scenario, "#888"),
               alpha=0.85, edgecolor="#0d1117", borderpad=2)

    ax.legend(facecolor="#21262d", edgecolor="#30363d", labelcolor="#e6edf3")
    _savefig(fig, os.path.join(out_dir, "protocol_comparison.png"))


# ── Graph 5: Before/After Comparison Table (Phase 10) ───────────────────
def plot_comparison_table(df: pd.DataFrame, out_dir: str):
    scenarios = sorted(df["scenario"].unique())
    metrics_agg = {}
    for scenario in scenarios:
        s = df[df["scenario"] == scenario]
        metrics_agg[scenario] = {
            "Avg Latency (ms)":    s[s["latency_ms"] > 0]["latency_ms"].mean(),
            "Avg Packet Loss (%)": s["loss_pct"].mean(),
            "Avg Throughput (Kbps)": s[s["throughput_kbps"] > 0]["throughput_kbps"].mean(),
            "Stability (1/loss_std)": 1.0 / max(s["loss_pct"].std(), 0.1),
        }

    rows_labels = ["Avg Latency (ms)", "Avg Packet Loss (%)",
                   "Avg Throughput (Kbps)", "Stability (1/loss_std)"]

    # Build display data
    col_labels = ["Metric"] + scenarios + (["Δ (opt − base)"] if len(scenarios) == 2 else [])
    table_data = []
    for metric in rows_labels:
        row = [metric]
        vals = [metrics_agg.get(s, {}).get(metric, float("nan")) for s in scenarios]
        for v in vals:
            row.append(f"{v:.2f}" if not (v != v) else "—")  # nan check
        if len(scenarios) == 2:
            diff = vals[1] - vals[0]
            arrow = "▼" if metric != "Avg Throughput (Kbps)" and metric != "Stability (1/loss_std)" else "▲"
            sign  = "+" if diff > 0 else ""
            color_hint = "(better)" if (
                (metric in ("Avg Latency (ms)", "Avg Packet Loss (%)") and diff < 0) or
                (metric in ("Avg Throughput (Kbps)", "Stability (1/loss_std)") and diff > 0)
            ) else "(worse)"
            row.append(f"{sign}{diff:.2f} {color_hint}")
        table_data.append(row)

    fig, (ax,) = _dark_fig(figsize=(11, 3.5))
    ax.set_title("Phase 10 – Before AI vs After AI Comparison", pad=14)
    ax.axis("off")

    tbl = ax.table(
        cellText=table_data,
        colLabels=col_labels,
        cellLoc="center",
        loc="center",
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(10)
    tbl.scale(1, 1.8)

    # Style header row
    for col_idx in range(len(col_labels)):
        cell = tbl[0, col_idx]
        cell.set_facecolor("#e50914")
        cell.set_text_props(color="white", fontweight="bold")

    # Style data rows
    for row_idx in range(1, len(table_data) + 1):
        for col_idx in range(len(col_labels)):
            cell = tbl[row_idx, col_idx]
            cell.set_facecolor("#1c2128" if row_idx % 2 == 0 else "#161b22")
            cell.set_text_props(color="#e6edf3")
            cell.set_edgecolor("#30363d")

    _savefig(fig, os.path.join(out_dir, "comparison_table.png"))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    _check_imports()

    parser = argparse.ArgumentParser(description="Phase 9+10 – Analyze Results")
    parser.add_argument("--results", default=DEFAULT_RESULTS,
                        help="Path to experiment_results.csv")
    parser.add_argument("--capture", default=DEFAULT_CAPTURE,
                        help="Path to processed_dataset.csv (optional, for extra context)")
    parser.add_argument("--out", default=DEFAULT_OUT, help="Output directory for PNG graphs")
    args = parser.parse_args()

    if not os.path.exists(args.results):
        sys.exit(
            f"[ERROR] Results file not found: {args.results}\n"
            "        Run mininet/run_baseline.py and mininet/run_optimized.py first.\n"
            "        Or create the file manually with columns:\n"
            "        scenario, host, protocol, latency_ms, throughput_kbps, loss_pct"
        )

    df = pd.read_csv(args.results)
    print(f"[+] Loaded {len(df):,} result rows from {args.results}")
    print(f"    Scenarios: {sorted(df['scenario'].unique())}")
    print(f"    Hosts:     {sorted(df['host'].unique())}")

    os.makedirs(args.out, exist_ok=True)

    plot_latency(df, args.out)
    plot_packet_loss(df, args.out)
    plot_throughput(df, args.out)
    plot_protocol_comparison(df, args.out)
    plot_comparison_table(df, args.out)

    print(f"\n[✓] All graphs saved to: {args.out}")
    print("     latency.png, packet_loss.png, throughput.png,")
    print("     protocol_comparison.png, comparison_table.png")
    print("\n     Graphs are also served at GET /api/analysis (start server.py first).")


if __name__ == "__main__":
    main()
