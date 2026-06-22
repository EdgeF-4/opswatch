import os
import tempfile
import unittest

from opswatch import reporting
from opswatch.store import Store


class ReportingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "t.db"))

    def tearDown(self):
        self.store.close()

    def _incident(self, kind, name, start, end=None):
        self.store.transition(kind, name, "failing", "down", now=start)
        if end is not None:
            self.store.transition(kind, name, "ok", "up", now=end)

    def test_uptime_for_a_clean_target_is_full(self):
        # never went failing -> no incidents -> 100%
        self.store.transition("monitor", "clean", "ok", "fine", now=1000)
        up = reporting.uptime_pct(self.store, "monitor", "clean", 600, now=1600)
        self.assertEqual(up, 100.0)

    def test_uptime_reflects_a_closed_incident(self):
        self._incident("monitor", "m", start=1000, end=1060)  # 60s down
        up = reporting.uptime_pct(self.store, "monitor", "m", 600, now=1600)
        self.assertAlmostEqual(up, 90.0, places=2)

    def test_uptime_counts_ongoing_downtime(self):
        self._incident("monitor", "m", start=1000)  # still down
        up = reporting.uptime_pct(self.store, "monitor", "m", 600, now=1600)
        self.assertAlmostEqual(up, 0.0, places=2)

    def test_downtime_clipped_to_window(self):
        # incident started before the window; only the in-window part counts
        self._incident("monitor", "m", start=1000, end=2000)
        incs = self.store.incidents_since(1700)
        down = reporting.downtime_seconds(incs, "monitor", "m", 1700, 2000)
        self.assertEqual(down, 300)

    def test_mttr_average_of_resolved(self):
        self._incident("monitor", "m", start=100, end=140)   # 40s
        self._incident("monitor", "m", start=200, end=280)   # 80s
        incs = self.store.incidents_since(0)
        mttr = reporting.mttr_seconds(incs, "monitor", "m", 0, 300)
        self.assertEqual(mttr, 60.0)

    def test_build_report_shape(self):
        self._incident("monitor", "m", start=1000, end=1060)
        self.store.transition("job", "j", "ok", "ran", now=1000)
        report = reporting.build_report(self.store, now=1600)
        self.assertEqual(report["windows"], ["24h", "7d", "30d"])
        names = {t["name"] for t in report["targets"]}
        self.assertEqual(names, {"m", "j"})
        m = next(t for t in report["targets"] if t["name"] == "m")
        self.assertIn("24h", m["windows"])
        self.assertEqual(m["windows"]["24h"]["incidents"], 1)

    def test_timeline_newest_first_with_durations(self):
        self._incident("monitor", "a", start=100, end=160)
        self._incident("monitor", "b", start=200)  # ongoing
        tl = reporting.timeline(self.store, now=300)
        self.assertEqual(tl[0]["name"], "b")
        self.assertTrue(tl[0]["ongoing"])
        self.assertEqual(tl[1]["duration_seconds"], 60)


if __name__ == "__main__":
    unittest.main()
