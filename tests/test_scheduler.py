import os
import tempfile
import time
import unittest

from opswatch.scheduler import Job, Scheduler, is_due
from opswatch.store import Store


class FakeNotifier:
    def __init__(self):
        self.alerts = []

    def notify(self, alert):
        self.alerts.append(alert)


class IsDueTest(unittest.TestCase):
    def test_interval_due_when_never_run(self):
        job = Job(name="j", kind="builtin", target="heartbeat", interval_seconds=10)
        self.assertTrue(is_due(job, None, now=1000))

    def test_interval_not_due_before_window(self):
        job = Job(name="j", kind="builtin", target="heartbeat", interval_seconds=10)
        self.assertFalse(is_due(job, last_started=995, now=1000))
        self.assertTrue(is_due(job, last_started=990, now=1000))

    def test_daily_not_due_before_target_time(self):
        job = Job(name="j", kind="builtin", target="heartbeat", daily_at="23:59")
        midday = time.mktime(time.struct_time((2026, 6, 20, 12, 0, 0, 0, 0, -1)))
        self.assertFalse(is_due(job, None, now=midday))

    def test_daily_due_after_target_and_once_per_day(self):
        job = Job(name="j", kind="builtin", target="heartbeat", daily_at="08:00")
        nine_am = time.mktime(time.struct_time((2026, 6, 20, 9, 0, 0, 0, 0, -1)))
        eight_am = time.mktime(time.struct_time((2026, 6, 20, 8, 0, 0, 0, 0, -1)))
        self.assertTrue(is_due(job, None, now=nine_am))
        # already ran after today's 08:00 target -> not due again
        self.assertFalse(is_due(job, last_started=eight_am + 60, now=nine_am))


class DispatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "t.db"))
        self.notifier = FakeNotifier()

    def tearDown(self):
        self.store.close()

    def _run_sync(self, job):
        # Drive the worker body directly so the test is deterministic.
        sched = Scheduler([job], self.store, self.notifier)
        sched._run_job(job)
        return sched

    def test_command_success_records_ok_no_alert(self):
        job = Job(name="ok", kind="command", target="exit 0")
        self._run_sync(job)
        self.assertEqual(self.store.last_run("ok").status, "ok")
        self.assertEqual(self.notifier.alerts, [])

    def test_command_failure_retries_then_alerts(self):
        job = Job(name="bad", kind="command", target="exit 1", max_retries=2)
        self._run_sync(job)
        runs = [r for r in self.store.recent_runs() if r.job == "bad"]
        # one initial attempt plus two retries
        self.assertEqual(len(runs), 3)
        self.assertEqual(len(self.notifier.alerts), 1)
        self.assertEqual(self.notifier.alerts[0].severity, "critical")

    def test_recovery_emits_recovered_alert(self):
        bad = Job(name="x", kind="command", target="exit 1")
        good = Job(name="x", kind="command", target="exit 0")
        self._run_sync(bad)
        self._run_sync(good)
        severities = [a.severity for a in self.notifier.alerts]
        self.assertEqual(severities, ["critical", "recovered"])

    def test_unknown_builtin_is_failure(self):
        job = Job(name="u", kind="builtin", target="does_not_exist")
        self._run_sync(job)
        self.assertEqual(self.store.last_run("u").status, "failed")
        self.assertEqual(self.store.last_run("u").exit_code, 127)


if __name__ == "__main__":
    unittest.main()
