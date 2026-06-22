import os
import tempfile
import unittest

from opswatch.store import Store


class SampleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "t.db"))

    def tearDown(self):
        self.store.close()

    def test_samples_recorded_oldest_first(self):
        self.store.record_sample("m", True, "a", now=10)
        self.store.record_sample("m", False, "b", now=20)
        rows = self.store.recent_samples("m", 10)
        self.assertEqual([r["ok"] for r in rows], [1, 0])
        self.assertEqual(rows[0]["ts"], 10)

    def test_sample_uptime_counts(self):
        for i, ok in enumerate([True, True, False, True]):
            self.store.record_sample("m", ok, "", now=100 + i)
        ok, total = self.store.sample_uptime("m", since_ts=0)
        self.assertEqual((ok, total), (3, 4))

    def test_old_samples_pruned(self):
        self.store = Store(os.path.join(self.tmp, "p.db"), retention_days=1)
        self.store.record_sample("m", True, "old", now=0)            # very old
        self.store.record_sample("m", True, "new", now=10 * 86400)   # ten days later
        rows = self.store.recent_samples("m", 100)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["detail"], "new")


class IncidentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "t.db"))

    def tearDown(self):
        self.store.close()

    def test_failing_opens_incident_recovery_closes_it(self):
        self.store.transition("monitor", "m", "failing", "down", now=1000)
        incs = self.store.recent_incidents()
        self.assertEqual(len(incs), 1)
        self.assertTrue(incs[0].ongoing)
        self.assertEqual(self.store.open_incident_count(), 1)

        self.store.transition("monitor", "m", "ok", "back", now=1060)
        inc = self.store.recent_incidents()[0]
        self.assertFalse(inc.ongoing)
        self.assertEqual(inc.duration(), 60)
        self.assertEqual(self.store.open_incident_count(), 0)

    def test_repeated_failing_does_not_open_a_second_incident(self):
        self.store.transition("monitor", "m", "failing", "down", now=1000)
        # an unchanged failing tick must not stack a duplicate incident
        self.store.transition("monitor", "m", "failing", "still down", now=1005)
        self.assertEqual(len(self.store.recent_incidents()), 1)
        self.assertEqual(self.store.open_incident_count(), 1)

    def test_incidents_for_jobs_and_monitors_are_separate(self):
        self.store.transition("job", "j", "failing", "boom", now=1)
        self.store.transition("monitor", "m", "failing", "down", now=2)
        kinds = sorted(i.kind for i in self.store.recent_incidents())
        self.assertEqual(kinds, ["job", "monitor"])

    def test_incidents_since_includes_ongoing_and_recent(self):
        self.store.transition("monitor", "old", "failing", "d", now=10)
        self.store.transition("monitor", "old", "ok", "d", now=20)       # resolved early
        self.store.transition("monitor", "live", "failing", "d", now=100)  # still open
        since = self.store.incidents_since(50)
        names = {i.name for i in since}
        self.assertIn("live", names)
        self.assertNotIn("old", names)


class IngestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "t.db"))

    def tearDown(self):
        self.store.close()

    def test_last_ingest_returns_newest(self):
        self.store.record_ingest("svc", "ok", "first", now=1)
        self.store.record_ingest("svc", "fail", "second", now=2)
        row = self.store.last_ingest("svc")
        self.assertEqual(row["status"], "fail")
        self.assertEqual(row["detail"], "second")

    def test_last_ingest_none_for_unknown_source(self):
        self.assertIsNone(self.store.last_ingest("nope"))


if __name__ == "__main__":
    unittest.main()
