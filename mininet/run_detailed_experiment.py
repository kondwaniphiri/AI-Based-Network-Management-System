"""Concurrent repeated workloads; optimized changes ONLY offered UDP rate.

Physical bandwidth, delay and loss stay identical. Raw outputs and configuration
are preserved per run; failed and unsupported measurements stay missing.
"""
import argparse
import csv
import json
import math
import random
import re
import subprocess
import time
import uuid
from pathlib import Path

from topology import build_network, _require_mininet

FIELDS = ["run_id", "scenario", "repeat", "host", "protocol", "status", "error",
          "samples", "latency_ms_avg", "latency_ms_min", "latency_ms_max",
          "throughput_kbps", "loss_pct", "jitter_ms", "retransmits", "latency_kind"]


def _extract_iperf3_metrics(output):
    result = dict(throughput_kbps=None, loss_pct=None, jitter_ms=None,
                  latency_ms_avg=None, latency_ms_min=None, latency_ms_max=None,
                  retransmits=None, latency_kind=None, status="failed", error="invalid_or_incomplete_json")
    try:
        data = json.loads(output)
        if data.get("error"):
            result["error"] = str(data["error"])
            return result
        end = data["end"]
        protocol = data["start"]["test_start"]["protocol"]
        if protocol == "UDP":
            summary = end.get("sum_received") or end.get("sum")
            if not summary or "bits_per_second" not in summary:
                raise ValueError("missing UDP summary")
            if summary.get("sender") is True:
                raise ValueError("receiver UDP summary unavailable")
            result["loss_pct"] = summary.get("lost_percent")
            result["jitter_ms"] = summary.get("jitter_ms")
        else:
            summary = end["sum_received"]
            result["retransmits"] = end.get("sum_sent", {}).get("retransmits")
            rtts = [s["sender"]["mean_rtt"] / 1000 for s in end.get("streams", [])
                    if s.get("sender", {}).get("mean_rtt") is not None]
            if rtts:
                result.update(latency_ms_avg=sum(rtts) / len(rtts),
                              latency_ms_min=min(rtts), latency_ms_max=max(rtts),
                              latency_kind="tcp_mean_rtt_across_streams")
        result["throughput_kbps"] = summary["bits_per_second"] / 1000
        if not math.isfinite(result["throughput_kbps"]) or result["throughput_kbps"] < 0:
            raise ValueError("invalid throughput")
        result.update(status="ok", error="")
    except (ValueError, KeyError, TypeError):
        pass
    return result


def parse_ping(output):
    result = dict(status="failed", error="no_ping_summary", loss_pct=None,
                  latency_ms_avg=None, latency_ms_min=None, latency_ms_max=None,
                  latency_kind="icmp_rtt")
    loss = re.search(r"([\d.]+)% packet loss", output)
    if loss:
        result.update(loss_pct=float(loss[1]), status="no_reply", error="")
    rtt = re.search(r"(?:rtt|round-trip)[^=]*=\s*([\d.]+)/([\d.]+)/([\d.]+)/", output)
    if rtt:
        result.update(status="ok", error="", latency_ms_min=float(rtt[1]),
                      latency_ms_avg=float(rtt[2]), latency_ms_max=float(rtt[3]))
    return result


def run_sample(scenario, repeat, args, directory):
    udp_rate = args.udp_cap if scenario == "optimized" else args.udp_rate
    parallel = 2 if scenario == "stream_cap" else 4
    config = dict(scenario=scenario, repeat=repeat, bandwidth_mbps=args.bandwidth,
                  delay=args.delay, configured_loss_pct=args.loss, duration=args.duration,
                  udp_rate_mbps=udp_rate, phone_parallel=parallel, seed=args.seed)
    (directory / "config.json").write_text(json.dumps(config, indent=2))
    net = build_network(bw=args.bandwidth, delay=args.delay, loss=args.loss)
    servers, processes, rows = [], [], []
    try:
        net.start()
        for switch in net.switches:
            switch.cmd(f"ovs-vsctl set-fail-mode {switch.name} standalone")
        server = net.get("server")
        if "iperf" not in server.cmd("iperf3 --version"):
            raise RuntimeError("iperf3 is not installed")
        workloads = [("esp32_1", "TCP", []), ("esp32_2", "TCP", []),
                     ("esp32_3", "UDP", ["-u", "-b", f"{udp_rate}M"]),
                     ("phone2", "TCP", ["-P", str(parallel)])]
        for index in range(len(workloads)):
            servers.append(server.popen(["iperf3", "-s", "-p", str(5201 + index)],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        time.sleep(1)
        for index, (name, protocol, extra) in enumerate(workloads):
            process = net.get(name).popen(
                ["iperf3", "-c", server.IP(), "-p", str(5201 + index),
                 "-t", str(args.duration), "-J", "--connect-timeout", "3000"] + extra,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            processes.append((name, protocol, process))
        for name in ["esp32_1", "esp32_2", "esp32_3", "phone1", "phone2"]:
            process = net.get(name).popen(
                ["ping", "-n", "-i", "0.2", "-w", str(args.duration), server.IP()],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            processes.append((name, "ICMP", process))
        for name, protocol, process in processes:
            try:
                output, _ = process.communicate(timeout=args.duration + 10)
                metrics = parse_ping(output) if protocol == "ICMP" else _extract_iperf3_metrics(output)
                if process.returncode and protocol != "ICMP":
                    metrics.update(status="failed", error=f"exit_{process.returncode}")
            except subprocess.TimeoutExpired:
                process.kill()
                output, _ = process.communicate()
                metrics = dict(status="failed", error="timeout")
            (directory / f"{name}_{protocol}.txt").write_text(output, encoding="utf-8")
            if metrics["status"] == "failed":
                for key in FIELDS[8:-1]:
                    metrics[key] = None
            rows.append(dict(run_id=directory.name, scenario=scenario, repeat=repeat,
                             host=name, protocol=protocol,
                             samples=int(metrics["status"] == "ok"), **metrics))
    finally:
        for process in [p for _, _, p in processes] + servers:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        net.stop()
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("scenario", choices=["baseline", "optimized", "stream_cap", "both"])
    p.add_argument("--duration", type=int, default=10)
    p.add_argument("--samples", type=int, default=5)
    p.add_argument("--bandwidth", type=float, default=5)
    p.add_argument("--delay", default="5ms")
    p.add_argument("--loss", type=float, default=2)
    p.add_argument("--udp-rate", type=float, default=3)
    p.add_argument("--udp-cap", type=float, default=0.5)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--output", default=str(Path(__file__).resolve().parents[1] / "datasets/controlled_results.csv"))
    args = p.parse_args()
    if min(args.duration, args.samples, args.bandwidth, args.udp_rate, args.udp_cap) <= 0 or not 0 <= args.loss <= 100:
        p.error("Durations, rates and samples must be positive; loss must be 0..100")
    _require_mininet()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    header = not output.exists() or output.stat().st_size == 0
    if not header:
        with output.open(newline="") as f:
            if next(csv.reader(f)) != FIELDS:
                p.error("Output schema differs; choose a new output path")
    schedule = [(s, r) for r in range(1, args.samples + 1) for s in
                (["baseline", "optimized"] if args.scenario == "both" else [args.scenario])]
    random.Random(args.seed).shuffle(schedule)
    with output.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if header:
            writer.writeheader()
        for scenario, repeat in schedule:
            directory = output.parent / "runs" / f"{scenario}_{repeat}_{uuid.uuid4().hex[:10]}"
            directory.mkdir(parents=True)
            rows = run_sample(scenario, repeat, args, directory)
            writer.writerows(rows)
            f.flush()
            print(f"{directory.name}: {sum(r['status'] == 'ok' for r in rows)}/{len(rows)} measurements OK")


if __name__ == "__main__":
    main()
