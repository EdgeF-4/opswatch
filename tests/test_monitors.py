import os
import tempfile
import time
import unittest

from opswatch.monitors import Monitor, MonitorRunner, check_disk, check_job_freshness
from opswatch.store import Run, Store


class FakeNotifier:
    def __init__(self):
        self.alerts = []

    def notify(self, alert):
        self.alerts.append(alert)


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "t.db"))

    def tearDown(self):
        self.store.close()

    def test_disk_ok_and_fail_bounds(self):
        ok, _ = check_disk({"path": "/", "min_free_pct": 0})
        self.assertTrue(ok)
        bad, _ = check_disk({"path": "/", "min_free_pct": 100})
        self.assertFalse(bad)

    def test_job_freshness_no_run_is_failure(self):
        ok, detail = check_job_freshness(
            {"job": "j", "max_age_seconds": 60}, self.store, now=1000)
        self.assertFalse(ok)
        self.assertIn("no successful run", detail)

    def test_job_freshness_within_and_beyond_window(self):
        self.store.record_run(Run(job="j", status="ok", attempt=1, exit_code=0,
                                   output_tail="", started_at=1000, finished_at=1001))
        ok, _ = check_job_freshness(
            {"job": "j", "max_age_seconds": 60}, self.store, now=1030)
        self.assertTrue(ok)
        stale, _ = check_job_freshness(
            {"job": "j", "max_age_seconds": 60}, self.store, now=1200)
        self.assertFalse(stale)


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "t.db"))
        self.notifier = FakeNotifier()

    def tearDown(self):
        self.store.close()

    def test_alert_fires_once_on_state_change(self):
        mon = Monitor(name="m", type="job_freshness",
                      params={"job": "missing", "max_age_seconds": 1})
        runner = MonitorRunner([mon], self.store, self.notifier)
        runner.tick()  # transitions unknown -> failing: one alert
        runner.tick()  # still failing: no new alert
        self.assertEqual(len(self.notifier.alerts), 1)
        self.assertEqual(self.notifier.alerts[0].severity, "critical")

    def test_recovery_alert_after_failure(self):
        mon = Monitor(name="m", type="job_freshness",
                      params={"job": "j", "max_age_seconds": 60})
        runner = MonitorRunner([mon], self.store, self.notifier)
        runner.tick()  # no run yet -> failing
        self.store.record_run(Run(job="j", status="ok", attempt=1, exit_code=0,
                                  output_tail="", started_at=time.time(),
                                  finished_at=time.time()))
        runner.tick()  # fresh now -> recovered
        severities = [a.severity for a in self.notifier.alerts]
        self.assertEqual(severities, ["critical", "recovered"])

    def test_unknown_monitor_type_is_failure(self):
        mon = Monitor(name="weird", type="bogus", params={})
        runner = MonitorRunner([mon], self.store, self.notifier)
        runner.tick()
        self.assertEqual(self.store.get_state("monitor", "weird")["status"], "failing")


if __name__ == "__main__":
    unittest.main()
