import unittest

from opswatch import llmdrift


def call(version, output_tokens=100, quality=0.9, ok=True, cost_usd=0.01):
    return {
        "prompt_version": version, "output_tokens": output_tokens,
        "quality": quality, "ok": 1 if ok else 0, "cost_usd": cost_usd,
    }


def calls(version, n, **kw):
    return [call(version, **kw) for _ in range(n)]


class DriftTest(unittest.TestCase):
    def test_no_drift_when_versions_match_behavior(self):
        data = calls("v1", 8) + calls("v2", 8)
        r = llmdrift.detect_drift(data, baseline_version="v1")
        self.assertEqual(r["candidate_version"], "v2")
        self.assertTrue(r["enough_data"])
        self.assertFalse(r["drifted"])
        self.assertEqual(r["reasons"], [])

    def test_output_length_drift_flagged(self):
        data = calls("v1", 8, output_tokens=100) + calls("v2", 8, output_tokens=200)
        r = llmdrift.detect_drift(data, baseline_version="v1")
        self.assertTrue(r["drifted"])
        self.assertTrue(any("output length" in x for x in r["reasons"]))
        self.assertAlmostEqual(r["deltas"]["output_tokens_pct"], 1.0)

    def test_quality_drop_flagged(self):
        data = calls("v1", 8, quality=0.95) + calls("v2", 8, quality=0.70)
        r = llmdrift.detect_drift(data, baseline_version="v1")
        self.assertTrue(r["drifted"])
        self.assertTrue(any("quality dropped" in x for x in r["reasons"]))

    def test_error_rate_increase_flagged(self):
        good = calls("v1", 10, ok=True)
        bad = calls("v2", 5, ok=True) + calls("v2", 5, ok=False)
        r = llmdrift.detect_drift(good + bad, baseline_version="v1")
        self.assertTrue(r["drifted"])
        self.assertTrue(any("error rate" in x for x in r["reasons"]))

    def test_cost_drift_flagged(self):
        data = calls("v1", 8, cost_usd=0.01) + calls("v2", 8, cost_usd=0.05)
        r = llmdrift.detect_drift(data, baseline_version="v1")
        self.assertTrue(r["drifted"])
        self.assertTrue(any("cost per call" in x for x in r["reasons"]))

    def test_insufficient_samples_does_not_flag(self):
        data = calls("v1", 2) + calls("v2", 2)
        r = llmdrift.detect_drift(data, baseline_version="v1")
        self.assertFalse(r["enough_data"])
        self.assertFalse(r["drifted"])
        self.assertTrue(any("not enough samples" in x for x in r["reasons"]))

    def test_explicit_candidate_version(self):
        data = calls("v1", 8, output_tokens=100) + calls("v3", 8, output_tokens=300)
        r = llmdrift.detect_drift(data, baseline_version="v1", candidate_version="v3")
        self.assertEqual(r["candidate_version"], "v3")
        self.assertTrue(r["drifted"])

    def test_missing_quality_is_skipped_not_crashed(self):
        data = (calls("v1", 8) + calls("v2", 8))
        for c in data:
            c["quality"] = None
        r = llmdrift.detect_drift(data, baseline_version="v1")
        self.assertIsNone(r["deltas"]["quality_drop"])
        self.assertFalse(r["drifted"])


if __name__ == "__main__":
    unittest.main()
