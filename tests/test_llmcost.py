import unittest

from opswatch import llmcost


def call(cost, model="m", route="r", tier="standard", ok=True,
         input_tokens=0, output_tokens=0):
    return {
        "cost_usd": cost, "model": model, "route": route, "tier": tier,
        "ok": 1 if ok else 0, "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


class SummarizeTest(unittest.TestCase):
    def test_empty_is_all_zero_and_safe(self):
        s = llmcost.summarize([], window_seconds=3600)
        self.assertEqual(s["total_cost_usd"], 0.0)
        self.assertEqual(s["predictions"], 0)
        self.assertEqual(s["dollars_per_1k"], 0.0)
        self.assertEqual(s["projected_monthly_runrate_usd"], 0.0)
        self.assertEqual(s["by_model"], [])

    def test_totals_and_dollars_per_1k(self):
        calls = [call(0.01), call(0.02), call(0.03), call(0.04)]
        s = llmcost.summarize(calls, window_seconds=3600)
        self.assertAlmostEqual(s["total_cost_usd"], 0.10)
        self.assertEqual(s["predictions"], 4)
        # 0.10 over 4 calls -> $25 per 1000 predictions
        self.assertAlmostEqual(s["dollars_per_1k"], 25.0)

    def test_runrate_projection_scales_to_a_month(self):
        # $1 in an hour -> 30*24 = 720 hours in a month -> $720
        s = llmcost.summarize([call(1.0)], window_seconds=3600)
        self.assertAlmostEqual(s["projected_monthly_runrate_usd"], 720.0)

    def test_at_scale_projection_uses_unit_cost(self):
        # $0.10 over 4 calls = $0.025 each -> 1,000,000/mo = $25,000
        calls = [call(0.01), call(0.02), call(0.03), call(0.04)]
        s = llmcost.summarize(calls, window_seconds=3600,
                              scale_predictions_per_month=1_000_000)
        self.assertAlmostEqual(s["projected_monthly_at_scale_usd"], 25000.0)

    def test_at_scale_is_none_without_target(self):
        s = llmcost.summarize([call(0.01)], window_seconds=3600)
        self.assertIsNone(s["projected_monthly_at_scale_usd"])

    def test_error_rate(self):
        s = llmcost.summarize([call(0.01, ok=True), call(0.01, ok=False)],
                              window_seconds=3600)
        self.assertAlmostEqual(s["error_rate"], 0.5)


class BreakdownTest(unittest.TestCase):
    def test_by_model_sorted_richest_first_with_shares(self):
        calls = [
            call(0.01, model="cheap-model", tier="cheap"),
            call(0.09, model="pricey-model", tier="hard"),
        ]
        s = llmcost.summarize(calls, window_seconds=3600)
        by_model = s["by_model"]
        self.assertEqual(by_model[0]["key"], "pricey-model")
        self.assertAlmostEqual(by_model[0]["share_pct"], 90.0)
        self.assertAlmostEqual(by_model[1]["share_pct"], 10.0)

    def test_by_tier_groups_spend(self):
        calls = [
            call(0.02, tier="cheap"), call(0.02, tier="cheap"),
            call(0.10, tier="hard"),
        ]
        s = llmcost.summarize(calls, window_seconds=3600)
        tiers = {t["key"]: t for t in s["by_tier"]}
        self.assertAlmostEqual(tiers["cheap"]["cost_usd"], 0.04)
        self.assertEqual(tiers["cheap"]["count"], 2)
        self.assertAlmostEqual(tiers["hard"]["cost_usd"], 0.10)

    def test_blank_field_becomes_unspecified(self):
        s = llmcost.summarize([call(0.01, route="")], window_seconds=3600)
        self.assertEqual(s["by_route"][0]["key"], "unspecified")


if __name__ == "__main__":
    unittest.main()
