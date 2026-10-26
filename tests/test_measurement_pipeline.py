"""Regression checks for measurement truth, causal alignment and held-out evaluation."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for directory in ["server", "datasets", "monitoring", "mininet"]:
    sys.path.insert(0, str(ROOT / directory))

import pandas as pd
import telemetry
from build_windows import build, FEATURES, SNMP_FEATURES
from train_windows import split_sessions, evaluate
from snmp_collect import rates, COUNTERS
from run_detailed_experiment import _extract_iperf3_metrics, parse_ping
from analyze_controlled import summarize
import run_detailed_experiment as experiment


def observations(times=(1, 6, 11, 16), rtts=(10, 200, 10, 10)):
    events = pd.DataFrame([dict(timestamp=t, run_id="a", source="host", protocol="UDP",
                                bytes=80, received=1) for t in times])
    probes = pd.DataFrame([dict(timestamp=t, run_id="a", source="host", rtt_ms=rtt,
                                success=1) for t, rtt in zip(times, rtts)])
    return events, probes


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(telemetry, "PATH", str(Path(self.tmp.name) / "events.db"))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def record(self, seq, boot="a", protocol="UDP", received=True):
        telemetry.record(dict(device="esp32_1", seq=seq, boot_id=boot),
                         "host", protocol, 80, received)

    def test_late_packet_repairs_gap_and_duplicate_does_not_repair_twice(self):
        self.record(0)
        self.record(2)
        self.record(2)
        self.assertEqual(telemetry.delivery_summary()[0]["observed_missing"], 1)
        self.record(1)
        row = telemetry.delivery_summary()[0]
        self.assertEqual(row["observed_missing"], 0)
        self.assertEqual(row["unique_received"], 3)
        self.assertEqual(row["received"], 4)

    def test_boots_transports_and_legacy_are_separate(self):
        self.record(0)
        self.record(0, boot="b")
        self.record(0, protocol="HTTP")
        self.record(None)
        self.assertEqual(len(telemetry.delivery_summary()), 3)

    def test_explicit_application_drops_do_not_count_as_delivered(self):
        self.record(0)
        self.record(1, received=False)
        self.record(2)
        self.assertEqual(telemetry.delivery_summary()[0]["observed_missing"], 1)
        rows = pd.read_csv(io.StringIO(telemetry.export_csv()))
        self.assertEqual(rows["received"].tolist(), [1, 0, 1])


class WindowTests(unittest.TestCase):
    def test_legacy_processor_keeps_missing_rtt_separate_from_interarrival(self):
        from process_packets import process, normalise_proto
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            pd.DataFrame([{"Time": t, "Source": "192.168.8.101",
                           "Destination": "192.168.8.100", "Protocol": "UDP",
                           "Length": 80, "Info": "sensor"} for t in [1, 1.5]]).to_csv(
                               folder / "raw.csv", index=False)
            with redirect_stdout(io.StringIO()):
                process(str(folder / "raw.csv"), str(folder / "processed.csv"))
            out = pd.read_csv(folder / "processed.csv")
            self.assertTrue(out["latency_ms"].isna().all())
            self.assertEqual(out["inter_arrival_ms"].tolist(), [0, 500])
            self.assertEqual(normalise_proto("TLSv1.3"), "TCP")
    def test_target_is_next_measured_outcome(self):
        events, probes = observations()
        out = build(events, probes)
        self.assertEqual(out["target"].tolist(), ["degraded", "normal"])
        self.assertEqual(out["rtt_ms"].tolist(), [10, 200])
        self.assertEqual(out["next_rtt_ms"].tolist(), [200, 10])

    def test_no_label_across_missing_window(self):
        events, probes = observations((1, 11), (10, 200))
        self.assertTrue(build(events, probes).empty)

    def test_failures_have_missing_rtt_and_outage_windows_are_retained(self):
        events, probes = observations()
        probes.loc[1, ["success", "rtt_ms"]] = [0, 999]
        events = events.drop(index=1)
        out = build(events, probes)
        self.assertEqual(out.iloc[0]["target"], "degraded")
        self.assertTrue(pd.isna(out.iloc[1]["rtt_ms"]))
        self.assertEqual(out.iloc[1]["packet_rate"], 0)
        self.assertEqual(out.iloc[1]["probe_failure_pct"], 100)

    def test_rate_does_not_define_target(self):
        events, probes = observations(rtts=(10, 10, 10, 10))
        events = pd.concat([events] * 500)
        self.assertEqual(set(build(events, probes)["target"]), {"normal"})

    def test_snmp_asof_uses_only_available_matching_interface(self):
        events, probes = observations()
        snmp = pd.DataFrame([dict(timestamp=t, run_id="a", agent="router", interface=1,
                                  status="ok", **{k: value for k in SNMP_FEATURES})
                             for t, value in [(4, 1), (6, 999)]])
        out = build(events, probes, snmp=snmp, agent="router", interface=1)
        self.assertEqual(out.iloc[0]["in_bps"], 1)
        self.assertEqual(out.iloc[1]["in_bps"], 999)

    def test_failed_snmp_breaks_freshness(self):
        events, probes = observations()
        snmp = pd.DataFrame([dict(timestamp=t, run_id="a", agent="router", interface=1,
                                  status=status, **{k: 42 for k in SNMP_FEATURES})
                             for t, status in [(1, "ok"), (6, "collection_failed")]])
        out = build(events, probes, snmp=snmp, agent="router", interface=1)
        self.assertTrue(pd.isna(out.iloc[1]["in_bps"]))

    def test_separate_session_split_and_training_only_imputation(self):
        rows = []
        for run in ["train", "test"]:
            for i in range(30):
                rows.append(dict(run_id=run, target="normal" if i < 15 else "degraded",
                                 **{k: (1 if run == "train" else 999) for k in FEATURES}))
        df = pd.DataFrame(rows)
        tr, te = split_sessions(df, ["test"])
        self.assertFalse(set(tr.run_id) & set(te.run_id))
        model, _, _ = evaluate(tr, te, FEATURES)
        self.assertTrue((model.steps[0][1].statistics_ == 1).all())
        with self.assertRaises(ValueError):
            split_sessions(df, ["train", "test"])


class CounterTests(unittest.TestCase):
    def sample(self, value):
        return dict(uptime=100, discontinuity=0, speed_mbps=100,
                    **{k: value for k in COUNTERS})

    def test_counter_rates_and_independent_full_duplex_utilization(self):
        previous, current = self.sample(100), self.sample(1100)
        result = rates(previous, current, 2)
        self.assertEqual(result["in_bps"], 4000)
        self.assertAlmostEqual(result["in_utilization_pct"], .004)

    def test_reset_wrap_or_discontinuity_has_no_derived_rate(self):
        previous, current = self.sample(100), self.sample(90)
        self.assertNotIn("in_bps", rates(previous, current, 2))
        current = self.sample(200)
        current["discontinuity"] = 1
        self.assertEqual(rates(previous, current, 2)["status"], "counter_reset_or_wrap")
        self.assertEqual(rates(None, current, 2)["status"], "warmup")


class ExperimentTests(unittest.TestCase):
    def test_workloads_start_concurrently_and_only_udp_rate_changes(self):
        launches = []
        class Process:
            returncode = 0
            done = False
            def __init__(self, cmd):
                self.cmd = cmd
            def communicate(self, timeout=None):
                launches.append("collect")
                self.done = True
                if self.cmd[0] == "ping":
                    return "0% packet loss\nrtt min/avg/max/mdev = 1/2/3/1 ms", None
                return json.dumps(dict(start=dict(test_start=dict(protocol="TCP")),
                    end=dict(sum_received=dict(bits_per_second=1000000)))), None
            def poll(self):
                return 0 if self.done else None
            def terminate(self):
                self.done = True
            def wait(self, timeout=None):
                return 0
        class Host:
            name = "s1"
            def IP(self):
                return "10.0.0.1"
            def cmd(self, cmd):
                return "iperf 3.16"
            def popen(self, cmd, **kwargs):
                launches.append(cmd)
                return Process(cmd)
        class Network:
            switches = [Host()]
            def start(self):
                pass
            def stop(self):
                pass
            def get(self, name):
                return Host()
        args = SimpleNamespace(udp_rate=3, udp_cap=.5, bandwidth=5, delay="5ms",
                               loss=2, duration=10, seed=42)
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(experiment, "build_network", side_effect=lambda **kw: Network()) as factory, \
                patch.object(experiment.time, "sleep"):
            configs = []
            for scenario in ["baseline", "optimized"]:
                launches.clear()
                folder = Path(tmp) / scenario
                folder.mkdir()
                experiment.run_sample(scenario, 1, args, folder)
                # Four servers + four traffic clients + five probes before collection.
                self.assertEqual(launches.index("collect"), 13)
                configs.append(json.loads((folder / "config.json").read_text()))
            self.assertEqual(factory.call_args_list[0], factory.call_args_list[1])
            self.assertEqual(configs[0]["phone_parallel"], configs[1]["phone_parallel"])
            self.assertNotEqual(configs[0]["udp_rate_mbps"], configs[1]["udp_rate_mbps"])

    def test_failed_iperf_never_becomes_loss_or_zero_latency(self):
        for text in ["bad json", json.dumps({"error": "connection refused"})]:
            row = _extract_iperf3_metrics(text)
            self.assertEqual(row["status"], "failed")
            self.assertIsNone(row["loss_pct"])
            self.assertIsNone(row["latency_ms_avg"])

    def test_udp_jitter_is_not_latency(self):
        row = _extract_iperf3_metrics(json.dumps(dict(start=dict(test_start=dict(protocol="UDP")),
            end=dict(sum=dict(bits_per_second=500000, jitter_ms=2.5, lost_percent=3, sender=False)))))
        self.assertEqual(row["throughput_kbps"], 500)
        self.assertEqual(row["jitter_ms"], 2.5)
        self.assertIsNone(row["latency_ms_avg"])

    def test_tcp_rtt_units_and_unknown_loss(self):
        row = _extract_iperf3_metrics(json.dumps(dict(start=dict(test_start=dict(protocol="TCP")),
            end=dict(sum_received=dict(bits_per_second=1000000), sum_sent=dict(retransmits=2),
                     streams=[dict(sender=dict(mean_rtt=3500))]))))
        self.assertEqual(row["latency_ms_avg"], 3.5)
        self.assertIsNone(row["loss_pct"])

    def test_ping_no_reply_preserves_measured_loss(self):
        row = parse_ping("5 packets transmitted, 0 received, 100% packet loss")
        self.assertEqual(row["status"], "no_reply")
        self.assertEqual(row["loss_pct"], 100)
        self.assertIsNone(row["latency_ms_avg"])

    def test_summary_does_not_average_failed_runs_as_zero(self):
        df = pd.DataFrame([dict(scenario="baseline", host="host", protocol="UDP", status=s,
            latency_ms_avg=None, throughput_kbps=t, loss_pct=None, jitter_ms=None,
            retransmits=None) for s, t in [("ok", 500), ("failed", None)]])
        out = summarize(df).iloc[0]
        self.assertEqual(out["attempts"], 2)
        self.assertEqual(out["failed"], 1)
        self.assertEqual(out["throughput_kbps_mean"], 500)


class ServerTests(unittest.TestCase):
    def test_gateway_http_sources_remain_separate_after_nat(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
                "IOT_METRICS_DB":str(Path(tmp)/"metrics.db"),
                "IOT_VIDEO_LOG":str(Path(tmp)/"video.csv"),
                "IOT_SNMP_CSV":str(Path(tmp)/"snmp.csv"),
                "IOT_USE_GATEWAY":"1", "IOT_GATEWAY_UPLINK_IP":"192.168.8.110",
                "IOT_SENSOR_SUBNET":"192.168.50.0/24"}), \
                patch.object(telemetry,"PATH",str(Path(tmp)/"telemetry.db")):
            spec = importlib.util.spec_from_file_location("iot_gateway_server_test",ROOT/"server/server.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            client = module.app.test_client()
            for device in ["esp32_1","esp32_2"]:
                result = client.post("/data",json=dict(device=device,seq=0,boot_id="test"),
                                     environ_overrides={"REMOTE_ADDR":"192.168.8.110"})
                self.assertEqual(result.status_code,200)
            exported = pd.read_csv(io.StringIO(client.get("/api/telemetry/export").data.decode()))
            self.assertEqual(exported["source"].tolist(),["192.168.50.101","192.168.50.102"])
            self.assertEqual(set(exported["peer_ip"]),{"192.168.8.110"})
            data = client.get("/api/dashboard").get_json()
            self.assertEqual(data["latency_kind"],"http_rtt")
            self.assertEqual(data["devices"]["esp32_1"]["ip"],"192.168.50.101")
            with patch.object(module.subprocess,"run") as ping:
                module.ping_worker()
                ping.assert_not_called()

    def test_http_input_validation_and_delivery_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"IOT_METRICS_DB": str(Path(tmp)/"metrics.db"),
                                         "IOT_VIDEO_LOG": str(Path(tmp)/"video.csv")}):
                spec = importlib.util.spec_from_file_location("iot_server_test", ROOT / "server/server.py")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            with patch.object(telemetry, "PATH", str(Path(tmp)/"telemetry.db")), \
                    patch.dict(os.environ, {"IOT_SNMP_CSV": str(Path(tmp)/"snmp.csv")}):
                client = module.app.test_client()
                self.assertEqual(client.post("/data", json=[1, 2]).status_code, 400)
                response = client.post("/data", json=dict(device="esp32_1", seq=0, boot_id="test"))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(client.get("/api/delivery").get_json()), 1)
                self.assertIn(b"boot_id", client.get("/api/telemetry/export").data)
                self.assertEqual(module._current_drop_rate(), 0)
                dashboard = client.get("/api/dashboard").get_json()
                self.assertIsNone(dashboard["devices"]["esp32_2"]["packet_loss_pct"])
                self.assertEqual(dashboard["gateway"]["status"], "unavailable")
                page = client.get("/dashboard")
                self.assertEqual(page.status_code, 200)
                self.assertIn(b"Network monitor", page.data)
                self.assertIn(b"/static/dashboard.css", page.data)
                self.assertIn(b"/static/dashboard.js", page.data)
                with client.get("/static/dashboard.css") as asset:
                    self.assertEqual(asset.status_code, 200)
                with client.get("/static/dashboard.js") as asset:
                    self.assertEqual(asset.status_code, 200)
                self.assertEqual(client.get("/api/telemetry/export?since=nan").status_code, 400)


class WorkflowTests(unittest.TestCase):
    def test_build_and_train_cli_preserves_separate_test_run(self):
        events, probes = [], []
        for run in ["train", "held_out"]:
            for i in range(85):
                timestamp = 1700000001 + i * 5
                events.append(dict(timestamp=timestamp, run_id=run, source="host",
                                   protocol="UDP", bytes=80, received=1))
                probes.append(dict(timestamp=timestamp, run_id=run, source="host",
                                   rtt_ms=200 if (i // 8) % 2 else 10, success=1))
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            pd.DataFrame(events).to_csv(folder / "events.csv", index=False)
            pd.DataFrame(probes).to_csv(folder / "probes.csv", index=False)
            commands = [
                [str(ROOT / "datasets/build_windows.py"), "--events", str(folder / "events.csv"),
                 "--probes", str(folder / "probes.csv"), "--output", str(folder / "windows.csv")],
                [str(ROOT / "datasets/train_windows.py"), "--input", str(folder / "windows.csv"),
                 "--test-runs", "held_out", "--output-dir", str(folder / "model")]]
            for command in commands:
                result = subprocess.run([sys.executable] + command, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((folder / "model/evaluation.json").read_text())
            self.assertEqual(report["train_runs"], ["train"])
            self.assertEqual(report["test_runs"], ["held_out"])
            self.assertIn("threshold_baseline", report)

    def test_controller_applies_bounded_interval_with_token(self):
        import time
        import control
        base = int(time.time() // 5) * 5
        events, probes = observations(tuple(base - 19 + i * 5 for i in range(4)),
                                      (200, 200, 200, 200))
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            probes.to_csv(folder / "probes.csv", index=False)
            argv = ["control.py", "--server", "http://server:5000", "--device", "http://stress",
                    "--source", "host", "--run-id", "a", "--strategy", "threshold", "--apply",
                    "--duration", "0.05", "--probes", str(folder / "probes.csv"),
                    "--output", str(folder / "actions.jsonl")]
            response = SimpleNamespace(text=events.to_csv(index=False), raise_for_status=lambda: None)
            with patch.object(sys, "argv", argv), patch.dict(os.environ, {"IOT_CONTROL_TOKEN": "test-token"}), \
                    patch.object(control.requests, "get", return_value=response), \
                    patch.object(control.requests, "post", return_value=response) as post, \
                    patch.object(control.time, "sleep"):
                control.main()
            self.assertEqual(post.call_count, 1)
            self.assertEqual(post.call_args.kwargs["data"]["udp_interval_ms"], 3000)
            self.assertEqual(post.call_args.kwargs["headers"]["X-Control-Token"], "test-token")


if __name__ == "__main__":
    unittest.main()
