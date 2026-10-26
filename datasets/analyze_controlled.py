"""Summarize repeated controlled experiments without converting missing data to zero."""
import argparse
from pathlib import Path
import pandas as pd


def summarize(df):
    keys = ["scenario", "host", "protocol"]
    counts = df.groupby(keys)["status"].agg(
        attempts="size", successful=lambda s: (s == "ok").sum(),
        failed=lambda s: (s == "failed").sum(), no_reply=lambda s: (s == "no_reply").sum())
    metrics = ["latency_ms_avg", "throughput_kbps", "loss_pct", "jitter_ms", "retransmits"]
    valid = df[df["status"].isin(["ok", "no_reply"])]
    summary = valid.groupby(keys)[metrics].agg(["count", "mean", "std", "min", "max"])
    summary.columns = ["_".join(c) for c in summary.columns]
    return counts.join(summary).reset_index()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", default="datasets/controlled_results.csv")
    p.add_argument("--output", default="datasets/controlled_summary.csv")
    args = p.parse_args()
    df = pd.read_csv(args.input)
    out = summarize(df)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.output, index=False)
    print(out.to_string(index=False))
    print("std is variation between repetitions; missing values are not zero.")


if __name__ == "__main__":
    main()
