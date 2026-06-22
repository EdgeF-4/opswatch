import os
import tempfile
import unittest

from opswatch import llm, llmeval
from opswatch.store import Store

PRICING = {
    "cheap-1": {"input_per_million": 0.50, "output_per_million": 1.50, "tier": "cheap"},
    "hard-1": {"input_per_million": 15.0, "output_per_million": 75.0, "tier": "hard"},
}


class FakeNotifier:
    def __init__(self):
        self.alerts = []

    def notify(self, alert):
        self.alerts.append(alert)


class PricingTest(unittest.TestCase):
    def test_cost_from_token_counts(self):
        p = llm.Pricing(PRICING)
        # 1,000,000 input @ $0.50 + 1,000,000 output @ $1.50 = $2.00
        self.assertAlmostEqual(p.cost("cheap-1", 1_000_000, 1_000_000), 2.0)

    def test_unknown_model_is_zero_and_unknown(self):
        p = llm.Pricing(PRICING)
        self.assertEqual(p.cost("mystery", 1000, 1000), 0.0)
        self.assertFalse(p.known("mystery"))

    def test_tier_from_entry(self):
        p = llm.Pricing(PRICING)
        self.assertEqual(p.tier("hard-1"), "hard")

    def test_tier_from_reverse_map_when_entry_has_none(self):
        p = llm.Pricing({"m": {"input_per_million": 1, "output_per_million": 1}},
                        tiers={"standard": ["m"]})
        self.assertEqual(p.tier("m"), "standard")


class RecordCallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "t.db"))
        self.pricing = llm.Pricing(PRICING)

    def tearDown(self):
        self.store.close()

    def test_record_computes_cost_and_tier(self):
        cost = llm.record_call(self.store, self.pricing, "cheap-1",
                               input_tokens=1_000_000, output_tokens=0, route="search")
        self.assertAlmostEqual(cost, 0.5)
        row = self.store.recent_llm_calls(1)[0]
        self.assertEqual(row["tier"], "cheap")
        self.assertEqual(row["route"], "search")
        self.assertAlmostEqual(row["cost_usd"], 0.5)


class RunnerCostAlertTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "t.db"))
        self.notifier = FakeNotifier()
        self.pricing = llm.Pricing(PRICING)

    def tearDown(self):
        self.store.close()

    def _settings(self, **over):
        base = dict(enabled=True, cost_window_seconds=3600,
                    thresholds={"cost_per_window_usd": 1.0})
        base.update(over)
        return llm.LLMSettings.from_dict(base)

    def test_cost_spike_fires_once_and_recovers(self):
        s = self._settings()
        runner = llm.LLMRunner(s, self.store, self.notifier, self.pricing)
        # two pricey calls inside the window blow past the $1 ceiling
        llm.record_call(self.store, self.pricing, "hard-1",
                        input_tokens=1_000_000, output_tokens=0, ts=1000)  # $15
        runner.tick(now=1001)
        runner.tick(now=1002)  # unchanged: must not re-alert
        crit = [a for a in self.notifier.alerts if a.severity == "critical"]
        self.assertEqual(len(crit), 1)
        self.assertIn("LLM spend", crit[0].title)

        # window moves past the spike; spend falls back under the ceiling
        runner.tick(now=1000 + 3600 + 10)
        rec = [a for a in self.notifier.alerts if a.severity == "recovered"]
        self.assertEqual(len(rec), 1)

    def test_no_threshold_means_no_alert(self):
        s = self._settings(thresholds={})
        runner = llm.LLMRunner(s, self.store, self.notifier, self.pricing)
        llm.record_call(self.store, self.pricing, "hard-1",
                        input_tokens=1_000_000, output_tokens=0, ts=1000)
        runner.tick(now=1001)
        self.assertEqual(self.notifier.alerts, [])

    def test_disabled_runner_is_inert(self):
        s = self._settings(enabled=False)
        runner = llm.LLMRunner(s, self.store, self.notifier, self.pricing)
        llm.record_call(self.store, self.pricing, "hard-1",
                        input_tokens=1_000_000, output_tokens=0, ts=1000)
        runner.tick(now=1001)
        self.assertEqual(self.notifier.alerts, [])


class RunnerDriftEvalAlertTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "t.db"))
        self.notifier = FakeNotifier()
        self.pricing = llm.Pricing(PRICING)

    def tearDown(self):
        self.store.close()

    def test_drift_alert_fires(self):
        s = llm.LLMSettings.from_dict(dict(
            enabled=True, cost_window_seconds=86400,
            prompts=[{"name": "extract", "baseline_version": "v1"}]))
        runner = llm.LLMRunner(s, self.store, self.notifier, self.pricing)
        for _ in range(6):
            self.store.record_llm_call(_call("extract", "v1", 100, ts=1000))
        for _ in range(6):
            self.store.record_llm_call(_call("extract", "v2", 400, ts=1500))
        runner.tick(now=2000)
        crit = [a for a in self.notifier.alerts if a.severity == "critical"]
        self.assertEqual(len(crit), 1)
        self.assertIn("LLM drift", crit[0].title)

    def test_failing_eval_alerts(self):
        s = llm.LLMSettings.from_dict(dict(
            enabled=True, eval_suites=[{"name": "qa"}]))
        runner = llm.LLMRunner(s, self.store, self.notifier, self.pricing)
        run = llmeval.run_suite("qa", [
            {"input": "a", "expected": "yes", "prediction": "no"}])
        self.store.record_llm_eval(run.to_result(ts=1000))
        runner.tick(now=1001)
        crit = [a for a in self.notifier.alerts if a.severity == "critical"]
        self.assertEqual(len(crit), 1)
        self.assertIn("LLM eval", crit[0].title)


class PanelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "t.db"))
        self.pricing = llm.Pricing(PRICING)

    def tearDown(self):
        self.store.close()

    def test_empty_panel_is_well_formed(self):
        s = llm.LLMSettings.from_dict({"enabled": True})
        panel = llm.build_llm_panel(self.store, s, self.pricing, now=10_000)
        self.assertTrue(panel["enabled"])
        self.assertEqual(panel["cost"]["predictions"], 0)
        self.assertEqual(panel["overall"], "ok")
        self.assertEqual(panel["evals"], [])

    def test_panel_reports_cost_and_tiers(self):
        s = llm.LLMSettings.from_dict({
            "enabled": True, "cost_window_seconds": 3600,
            "scale_predictions_per_month": 1_000_000})
        llm.record_call(self.store, self.pricing, "cheap-1",
                        input_tokens=1_000_000, output_tokens=0, ts=9000)  # $0.50
        llm.record_call(self.store, self.pricing, "hard-1",
                        input_tokens=1_000_000, output_tokens=0, ts=9000)  # $15
        panel = llm.build_llm_panel(self.store, s, self.pricing, now=9500)
        self.assertEqual(panel["cost"]["predictions"], 2)
        self.assertAlmostEqual(panel["cost"]["total_cost_usd"], 15.5)
        tiers = {t["key"] for t in panel["cost"]["by_tier"]}
        self.assertEqual(tiers, {"cheap", "hard"})
        self.assertIsNotNone(panel["cost"]["projected_monthly_at_scale_usd"])

    def test_panel_surfaces_eval_and_trend(self):
        s = llm.LLMSettings.from_dict({
            "enabled": True, "eval_suites": [{"name": "qa"}]})
        for i, pred in enumerate(["no", "yes"]):
            run = llmeval.run_suite("qa", [
                {"input": "a", "expected": "yes", "prediction": pred}])
            self.store.record_llm_eval(run.to_result(ts=1000 + i))
        panel = llm.build_llm_panel(self.store, s, self.pricing, now=2000)
        ev = panel["evals"][0]
        self.assertEqual(ev["name"], "qa")
        self.assertEqual(ev["latest"]["status"], "pass")  # newest run was correct
        self.assertEqual(len(ev["trend"]), 2)


def _call(prompt, version, output_tokens, ts):
    from opswatch.store import LLMCall
    return LLMCall(model="cheap-1", prompt=prompt, prompt_version=version,
                   output_tokens=output_tokens, quality=0.9, cost_usd=0.01, ts=ts)


if __name__ == "__main__":
    unittest.main()
