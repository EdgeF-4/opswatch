import os
import tempfile
import unittest

from opswatch.store import EvalResult, LLMCall, Store


class LLMCallStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "t.db"))

    def tearDown(self):
        self.store.close()

    def test_call_recorded_and_read_in_window(self):
        self.store.record_llm_call(LLMCall(
            model="model-a", route="search", tier="cheap",
            input_tokens=100, output_tokens=20, cost_usd=0.001, ts=1000))
        rows = self.store.llm_calls_since(0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["model"], "model-a")
        self.assertEqual(rows[0]["tier"], "cheap")
        self.assertAlmostEqual(rows[0]["cost_usd"], 0.001)
        self.assertEqual(rows[0]["ok"], 1)

    def test_window_excludes_older_rows(self):
        self.store.record_llm_call(LLMCall(model="m", ts=10))
        self.store.record_llm_call(LLMCall(model="m", ts=200))
        rows = self.store.llm_calls_since(100)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ts"], 200)

    def test_recent_calls_newest_first(self):
        self.store.record_llm_call(LLMCall(model="old", ts=1))
        self.store.record_llm_call(LLMCall(model="new", ts=2))
        rows = self.store.recent_llm_calls(10)
        self.assertEqual(rows[0]["model"], "new")

    def test_ok_false_and_quality_persist(self):
        self.store.record_llm_call(LLMCall(model="m", ok=False, quality=0.4, ts=5))
        row = self.store.recent_llm_calls(1)[0]
        self.assertEqual(row["ok"], 0)
        self.assertAlmostEqual(row["quality"], 0.4)

    def test_old_calls_pruned_to_retention(self):
        self.store = Store(os.path.join(self.tmp, "p.db"), retention_days=1)
        self.store.record_llm_call(LLMCall(model="m", ts=1))            # very old
        self.store.record_llm_call(LLMCall(model="m", ts=10 * 86400))   # ten days later
        self.assertEqual(len(self.store.llm_calls_since(0)), 1)


class LLMEvalStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "t.db"))

    def tearDown(self):
        self.store.close()

    def _eval(self, ts, accuracy=0.9, status="pass"):
        return EvalResult(
            suite="s", total=10, passed=int(accuracy * 10), accuracy=accuracy,
            hallucination_rate=0.0, quality=accuracy, status=status, ts=ts)

    def test_latest_eval_returns_newest(self):
        self.store.record_llm_eval(self._eval(1, accuracy=0.7))
        self.store.record_llm_eval(self._eval(2, accuracy=0.95))
        latest = self.store.latest_eval("s")
        self.assertAlmostEqual(latest["accuracy"], 0.95)

    def test_latest_eval_none_for_unknown_suite(self):
        self.assertIsNone(self.store.latest_eval("nope"))

    def test_evals_for_returns_oldest_first_for_trend(self):
        self.store.record_llm_eval(self._eval(1, accuracy=0.7))
        self.store.record_llm_eval(self._eval(2, accuracy=0.8))
        self.store.record_llm_eval(self._eval(3, accuracy=0.9))
        trend = self.store.evals_for("s", 10)
        self.assertEqual([round(e["accuracy"], 2) for e in trend], [0.7, 0.8, 0.9])


if __name__ == "__main__":
    unittest.main()
