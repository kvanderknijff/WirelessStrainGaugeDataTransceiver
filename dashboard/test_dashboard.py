"""Regression tests; run with python3 -m unittest discover -s dashboard."""
import csv
import io
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from wireless_strain_gauge_data_transceiver_dashboard import ReceiverDashboard
from TX_simulator import ReceiverSimulator, GAUGE_IDS


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.app = ReceiverDashboard()
        self.app.withdraw()
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        for callback in self.app.tk.splitlist(self.app.tk.call("after", "info")):
            self.app.after_cancel(callback)
        self.app.destroy()

    def receive(self, line):
        self.app.handle_line(line, self.now)

    def test_interleaving_wrap_reset_and_invalid_packets(self):
        for line in ("LC,A,65535,100,0,4294967290", "LC,B,8,200,0,5000",
                     "LC,C,42,-300,1,900", "LC,A,0,101,0,10"):
            self.receive(line)
        a, b, c = self.app.gauges
        self.assertEqual([len(g.points) for g in (a, b, c)], [2, 1, 1])
        self.assertAlmostEqual(a.points[-1].device_elapsed_s, 0.016)
        self.assertNotIn("sequence gap", self.app.notice_var.get())
        self.receive("LC,B,0,201,0,0")
        self.assertEqual(len(b.points), 1)
        self.assertEqual(len(a.points), 2)
        for line in ("LC,D,0,0,0,0", "LC,A,65536,0,0,20", "LC,A,1,bad,0,20", "LC,A,1,0,0,-1"):
            self.receive(line)
        self.assertEqual(len(a.points), 2)
        self.assertEqual(a.last_tx_sample_ms, 10)
        self.app.draw_plots()

    def test_calibration_and_csv(self):
        self.receive("LC,A,0,100,0,0")
        self.receive("LC,B,0,200,0,0")
        a, b, _ = self.app.gauges
        widgets = self.app.gauge_widgets[0]
        widgets["gain"].set("0.01")
        widgets["offset"].set("2")
        widgets["unit"].set("kN")
        self.assertTrue(self.app.apply_calibration(0))
        self.assertEqual(a.converted(100), 3)
        self.app.zero_balance(0)
        self.assertEqual(a.converted(100), 0)
        self.assertEqual(b.converted(200), 200)
        widgets["offset"].set("2")
        self.app.apply_calibration(0)
        output = io.StringIO()
        self.app.data_writer = csv.writer(output)
        self.receive("LC,A,1,200,0,100")
        row = next(csv.reader(io.StringIO(output.getvalue())))
        self.assertEqual(row[5], "200")
        self.assertEqual(row[7:10], ["A", "3.0", "kN"])
        for invalid in ("nan", "inf", "0", "abc"):
            widgets["gain"].set(invalid)
            self.assertFalse(self.app.apply_calibration(0))
            self.assertEqual(a.gain, 0.01)
        self.app.clear_graph()
        self.assertEqual(a.zero, 100)
        self.assertEqual(a.strain_gauge_id, "A")
        self.app.reset_calibration(0)
        self.assertEqual(a.converted(200), 200)

    def test_simulator_packets_are_received_for_all_three_gauges(self):
        simulator = ReceiverSimulator("test", 10, 82500, 1800, 0, True)
        class FakePort:
            is_open = True
            def write(port, payload):
                self.receive(payload.decode().strip())
                if payload.startswith(b"LC,SG_3,"):
                    simulator.running = False
            def flush(port):
                pass
            def close(port):
                port.is_open = False
        with patch("TX_simulator.serial.Serial", return_value=FakePort()), patch("TX_simulator.threading.Thread"):
            simulator.run()
        self.assertEqual([g.strain_gauge_id for g in self.app.gauges], list(GAUGE_IDS))
        self.assertEqual([len(g.points) for g in self.app.gauges], [1, 1, 1])
        self.assertEqual(len({g.points[-1].value for g in self.app.gauges}), 3)


if __name__ == "__main__":
    unittest.main()
