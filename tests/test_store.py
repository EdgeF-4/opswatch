import os
import tempfile
import unittest

from opswatch.store import Run, Store


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "t.db"))

    def tearDown(self):
        self.store.close()

    def _run(self, job, status, started, attempt=1):
        return Run(job=job, status=status, attempt=attempt, exit_code=0,
                   output_tail="ok", started_at=started, finished_at=started + 1)

    def test_last_run_and_last_success(self):
        self.store.record_run(self._run("j", "failed", 100))
        self.store.record_run(self._run("j", "ok", 200))
        self.store.record_run(self._run("j", "failed", 300))
        self.assertEqual(self.store.last_run("j").started_at, 300)
        self.assertEqual(self.store.last_success("j").started_at, 200)

    def test_last_run_none_for_unknown_job(self):
        self.assertIsNone(self.store.last_run("nope"))
        self.assertIsNone(self.store.last_success("nope"))

    def test_transition_reports_change_only_once(self):
        changed, prev = self.store.transition("job", "j", "failing", "boom")
        self.assertTrue(changed)
        self.assertEqual(prev, "unknown")

        changed, prev = self.store.transition("job", "j", "failing", "still boom")
        self.assertFalse(changed)
        self.assertEqual(prev, "failing")

        changed, prev = self.store.transition("job", "j", "ok", "fixed")
        self.assertTrue(changed)
        self.assertEqual(prev, "failing")

    def test_transition_since_resets_on_change_only(self):
        self.store.transition("monitor", "m", "ok", "good", now=1000)
        row = self.store.get_state("monitor", "m")
        self.assertEqual(row["since"], 1000)
        # unchanged status keeps the original since
        self.store.transition("monitor", "m", "ok", "still good", now=1050)
        self.assertEqual(self.store.get_state("monitor", "m")["since"], 1000)
        # changed status moves since forward
        self.store.transition("monitor", "m", "failing", "down", now=1100)
        self.assertEqual(self.store.get_state("monitor", "m")["since"], 1100)

    def test_alerts_recorded_newest_first(self):
        self.store.record_alert("a", "critical", "first", "d", now=1)
        self.store.record_alert("b", "recovered", "second", "d", now=2)
        alerts = self.store.recent_alerts()
        self.assertEqual(alerts[0]["title"], "second")
        self.assertEqual(alerts[1]["title"], "first")


if __name__ == "__main__":
    unittest.main()
