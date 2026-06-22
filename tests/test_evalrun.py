import json
import os
import tempfile
import unittest

from opswatch import evalrun
from opswatch.store import Store


class EvalRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        self.dataset = os.path.join(self.tmp, "ds.json")

    def _write_config(self, suite, min_accuracy=0.85):
        cfg = {
            "store_path": self.db,
            "llm": {
                "enabled": True,
                "eval_suites": [{
                    "name": suite, "dataset": "ds.json",
                    "min_accuracy": min_accuracy, "max_hallucination": 0.5,
                }],
            },
        }
        path = os.path.join(self.tmp, "config.json")
        with open(path, "w") as fh:
            json.dump(cfg, fh)
        return path

    def _write_dataset(self, records):
        with open(self.dataset, "w") as fh:
            json.dump(records, fh)

    def test_passing_suite_records_and_returns_zero(self):
        self._write_dataset([
            {"input": "a", "expected": "yes", "prediction": "yes"},
            {"input": "b", "expected": "no", "prediction": "no"},
        ])
        config_path = self._write_config("qa")
        rc = evalrun.run(config_path, now=1000)
        self.assertEqual(rc, 0)
        store = Store(self.db)
        try:
            latest = store.latest_eval("qa")
            self.assertEqual(latest["status"], "pass")
            self.assertEqual(latest["total"], 2)
        finally:
            store.close()

    def test_failing_suite_returns_one(self):
        self._write_dataset([
            {"input": "a", "expected": "yes", "prediction": "no"},
            {"input": "b", "expected": "yes", "prediction": "no"},
        ])
        config_path = self._write_config("qa", min_accuracy=0.9)
        rc = evalrun.run(config_path, now=1000)
        self.assertEqual(rc, 1)

    def test_named_suite_filter(self):
        self._write_dataset([{"input": "a", "expected": "a", "prediction": "a"}])
        config_path = self._write_config("qa")
        rc = evalrun.run(config_path, suite_name="does-not-exist", now=1000)
        self.assertEqual(rc, 0)
        store = Store(self.db)
        try:
            self.assertIsNone(store.latest_eval("qa"))
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
