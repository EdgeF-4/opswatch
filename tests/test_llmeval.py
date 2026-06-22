import json
import os
import tempfile
import unittest

from opswatch import llmeval
from opswatch.store import Store


class CorrectnessTest(unittest.TestCase):
    def test_normalized_exact_match(self):
        self.assertTrue(llmeval.is_correct("Paris", "  paris ", "auto"))
        self.assertFalse(llmeval.is_correct("Paris", "London", "auto"))

    def test_numeric_match_with_surrounding_text(self):
        self.assertTrue(llmeval.is_correct("42", "the answer is 42 today", "auto"))
        self.assertFalse(llmeval.is_correct("42", "the answer is 7", "auto"))

    def test_contains_mode(self):
        self.assertTrue(llmeval.is_correct("blue", "the sky is blue", "contains"))

    def test_auto_falls_back_to_containment(self):
        self.assertTrue(llmeval.is_correct("blue", "the sky is blue today"))


class GroundingTest(unittest.TestCase):
    def test_supported_prediction_is_grounded(self):
        self.assertTrue(llmeval.is_grounded(
            "the refund window is 30 days",
            context="our refund window is 30 days from purchase"))

    def test_unsupported_claim_is_hallucination(self):
        self.assertFalse(llmeval.is_grounded(
            "lifetime warranty includes accidental damage coverage worldwide",
            context="the product has a one year warranty"))


class RunSuiteTest(unittest.TestCase):
    def test_replay_all_correct_passes(self):
        records = [
            {"input": "capital of france", "expected": "Paris", "prediction": "Paris"},
            {"input": "2+2", "expected": "4", "prediction": "4"},
        ]
        run = llmeval.run_suite("s", records)
        self.assertEqual(run.accuracy, 1.0)
        self.assertEqual(run.status, "pass")
        self.assertEqual(run.passed, 2)

    def test_low_accuracy_fails(self):
        records = [
            {"input": "a", "expected": "yes", "prediction": "no"},
            {"input": "b", "expected": "yes", "prediction": "no"},
            {"input": "c", "expected": "yes", "prediction": "yes"},
        ]
        run = llmeval.run_suite("s", records, thresholds={"min_accuracy": 0.8})
        self.assertLess(run.accuracy, 0.8)
        self.assertEqual(run.status, "fail")

    def test_hallucination_rate_fails_even_with_accuracy(self):
        records = [
            {"input": "policy?", "expected": "30 days",
             "context": "refunds within 30 days",
             "prediction": "30 days, and free international shipping forever guaranteed"},
        ]
        run = llmeval.run_suite("s", records, thresholds={"max_hallucination": 0.1})
        self.assertGreater(run.hallucination_rate, 0.1)
        self.assertEqual(run.status, "fail")

    def test_predict_callable_is_used(self):
        records = [{"input": "x", "expected": "ok"}]
        run = llmeval.run_suite("s", records, predict=lambda inp: "ok")
        self.assertEqual(run.accuracy, 1.0)

    def test_explicit_quality_field_is_averaged(self):
        records = [
            {"input": "a", "expected": "a", "prediction": "a", "quality": 0.6},
            {"input": "b", "expected": "b", "prediction": "b", "quality": 0.8},
        ]
        run = llmeval.run_suite("s", records)
        self.assertAlmostEqual(run.quality, 0.7)

    def test_empty_dataset_fails_cleanly(self):
        run = llmeval.run_suite("s", [])
        self.assertEqual(run.status, "fail")
        self.assertEqual(run.total, 0)


class DatasetAndTrendTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_load_json_list(self):
        path = os.path.join(self.tmp, "d.json")
        with open(path, "w") as fh:
            json.dump([{"input": "a", "expected": "b"}], fh)
        self.assertEqual(len(llmeval.load_dataset(path)), 1)

    def test_load_jsonl(self):
        path = os.path.join(self.tmp, "d.jsonl")
        with open(path, "w") as fh:
            fh.write('{"input": "a", "expected": "b"}\n')
            fh.write('{"input": "c", "expected": "d"}\n')
        self.assertEqual(len(llmeval.load_dataset(path)), 2)

    def test_trend_reads_runs_oldest_first(self):
        store = Store(os.path.join(self.tmp, "t.db"))
        try:
            for i, acc in enumerate([0.7, 0.85, 0.95]):
                run = llmeval.run_suite(
                    "s", [{"input": "x", "expected": "y",
                           "prediction": "y" if acc > 0.9 else "n"}])
                store.record_llm_eval(run.to_result(ts=100 + i))
            t = llmeval.trend(store, "s", 10)
            self.assertEqual(len(t), 3)
            self.assertLessEqual(t[0]["ts"], t[-1]["ts"])
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
