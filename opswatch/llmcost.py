"""Language-model cost rollups.

Pure functions over recorded calls, the same way `reporting.py` is pure over
incidents. Each call already carries the dollar cost it incurred (computed from
its token counts and a per-model price book when it was recorded), so everything
here is aggregation: what was spent, what a thousand predictions cost, what that
projects to over a month, and how the spend splits by model, by route, and by
the cheap / standard / hard routing tier.

The two monthly projections answer two different questions a buyer asks:

  * run-rate    - "at the pace of the last hour, what does a month cost?"
  * at-scale    - "at my target volume of N predictions a month, what will it cost?"

Both are derived from the same per-prediction unit cost, so they stay honest.
"""

from __future__ import annotations

_MONTH_SECONDS = 30 * 86400


def _dollars_per_1k(cost: float, count: int) -> float:
    if count <= 0:
        return 0.0
    return round(cost / count * 1000.0, 4)


def _breakdown(calls: list, field: str, total_cost: float) -> list[dict]:
    """Spend grouped by one column (model, route, or tier), richest first."""
    groups: dict[str, dict] = {}
    for c in calls:
        key = (c[field] if c[field] not in (None, "") else "unspecified")
        g = groups.setdefault(key, {"cost": 0.0, "count": 0, "errors": 0})
        g["cost"] += c["cost_usd"]
        g["count"] += 1
        g["errors"] += 0 if c["ok"] else 1
    out = []
    for key, g in groups.items():
        out.append({
            "key": key,
            "cost_usd": round(g["cost"], 6),
            "count": g["count"],
            "dollars_per_1k": _dollars_per_1k(g["cost"], g["count"]),
            "error_rate": round(g["errors"] / g["count"], 4) if g["count"] else 0.0,
            "share_pct": round(100.0 * g["cost"] / total_cost, 2) if total_cost else 0.0,
        })
    out.sort(key=lambda r: r["cost_usd"], reverse=True)
    return out


def summarize(calls: list, window_seconds: int,
              scale_predictions_per_month: int | None = None) -> dict:
    """Roll a window of calls up into the numbers the cost panel shows.

    `calls` are rows from `store.llm_calls_since`. `window_seconds` is the span
    those rows were pulled from, used for the run-rate projection. When a target
    monthly volume is given, the at-scale projection multiplies the measured
    unit cost by that volume.
    """
    total_cost = round(sum(c["cost_usd"] for c in calls), 6)
    count = len(calls)
    input_tokens = sum(c["input_tokens"] for c in calls)
    output_tokens = sum(c["output_tokens"] for c in calls)
    errors = sum(1 for c in calls if not c["ok"])
    per_1k = _dollars_per_1k(total_cost, count)

    runrate = (round(total_cost * _MONTH_SECONDS / window_seconds, 2)
               if window_seconds > 0 else 0.0)
    at_scale = (round(per_1k / 1000.0 * scale_predictions_per_month, 2)
                if scale_predictions_per_month else None)

    return {
        "window_seconds": window_seconds,
        "total_cost_usd": total_cost,
        "predictions": count,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "error_rate": round(errors / count, 4) if count else 0.0,
        "dollars_per_1k": per_1k,
        "projected_monthly_runrate_usd": runrate,
        "scale_predictions_per_month": scale_predictions_per_month,
        "projected_monthly_at_scale_usd": at_scale,
        "by_model": _breakdown(calls, "model", total_cost),
        "by_route": _breakdown(calls, "route", total_cost),
        "by_tier": _breakdown(calls, "tier", total_cost),
    }
