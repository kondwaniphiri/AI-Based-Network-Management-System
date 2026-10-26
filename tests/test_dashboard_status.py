import csv
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import monitoring_status


class GatewayStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "snmp.csv"
        self.env = patch.dict(os.environ, {"IOT_SNMP_CSV": str(self.path),
                    "IOT_GATEWAY_IP": "router", "IOT_GATEWAY_INTERFACE": "2"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def write(self, **changes):
        row = dict(timestamp=time.time(), run_id="test", agent="router", interface=2,
                   status="ok", in_bps=0, out_bps=5000, in_utilization_pct=0,
                   out_utilization_pct=.5, oper_status=1)
        row.update(changes)
        with self.path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)

    def test_missing_agent_data_is_unavailable_not_zero(self):
        result = monitoring_status.snapshot("test")
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["in_bps"])

    def test_current_counter_zero_is_a_valid_measurement(self):
        self.write()
        result = monitoring_status.snapshot("test")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["in_bps"], 0)
        self.assertEqual(result["out_bps"], 5000)

    def test_stale_and_failed_samples_withhold_rates(self):
        self.write(timestamp=time.time() - 60)
        result = monitoring_status.snapshot("test")
        self.assertEqual(result["status"], "stale")
        self.assertIsNone(result["out_bps"])
        self.write(status="collection_failed")
        self.assertIsNone(monitoring_status.snapshot("test")["out_bps"])

    def test_other_run_or_interface_does_not_fill_gateway_panel(self):
        self.write(run_id="other")
        self.assertEqual(monitoring_status.snapshot("test")["status"], "unavailable")
        self.write(interface=3)
        self.assertEqual(monitoring_status.snapshot("test")["status"], "unavailable")

    def test_partial_csv_write_does_not_replace_last_complete_sample(self):
        self.write()
        with self.path.open("a") as f:
            f.write("123,test,router,")
        result = monitoring_status.snapshot("test")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["out_bps"], 5000)


if __name__ == "__main__":
    unittest.main()
