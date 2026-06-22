"""Prompt and version drift detection.

When a prompt is edited, its outputs can shift in ways a unit test never sees:
answers get longer or shorter, quality slips, the error rate creeps up, the cost
per call moves. This module compares the calls made under a baseline prompt
version against the calls made under a newer (candidate) version and flags when
any of those signals has moved more than its allowed amount.

It is a pure function over recorded calls. The caller groups calls by prompt,
names the baseline version, and supplies thresholds; the result says whether the
candidate has drifted and which signals moved.
"""

from __future__ import annotations

DEFAULT_THRESHOLDS = {
    "output_tokens_pct": 0.25,     # mean output length moved more than 25%
    "quality_drop": 0.10,          # mean quality fell by more than 0.10 (absolute)
    "error_rate_increase": 0.10,   # error rate rose by more than 0.10 (absolute)
    "cost_pct": 0.50,              # mean cost per call moved more than 50%
    "min_samples": 5,              # need this many calls on each side to judge
}


def _metrics(calls: list) -> dict:
    n = len(calls)
    if n == 0:
        return {"count": 0, "mean_output_tokens": 0.0, "mean_quality": None,
                "error_rate": 0.0, "mean_cost_usd": 0.0}
    qualities = [c["quality"] for c in calls if c["quality"] is not None]
    return {
        "count": n,
        "mean_output_tokens": sum(c["output_tokens"] for c in calls) / n,
        "mean_quality": (sum(qualities) / len(qualities)) if qualities else None,
        "error_rate": sum(0 if c["ok"] else 1 for c in calls) / n,
        "mean_cost_usd": sum(c["cost_usd"] for c in calls) / n,
    }


def _rel(before: float, after: float) -> float:
    """Relative change, guarding a zero baseline."""
    if before == 0:
        return 0.0 if after == 0 else 1.0
    return (after - before) / abs(before)


def detect_drift(calls: list, baseline_version: str,
                 candidate_version: str | None = None,
                 thresholds: dict | None = None) -> dict:
    """Compare a candidate prompt version against a baseline.

    `candidate_version` defaults to the most recent version present that is not
    the baseline. Returns a dict with `drifted`, the per-signal deltas, and a
    list of human-readable reasons (empty when nothing moved enough).
    """
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    by_version: dict[str, list] = {}
    for c in calls:
        by_version.setdefault(c["prompt_version"] or "", []).append(c)

    if candidate_version is None:
        others = [v for v in by_version if v != baseline_version]
        # newest version wins; ordering follows the (ts-sorted) call stream
        candidate_version = others[-1] if others else None

    base = _metrics(by_version.get(baseline_version, []))
    cand = _metrics(by_version.get(candidate_version, []) if candidate_version else [])

    result = {
        "baseline_version": baseline_version,
        "candidate_version": candidate_version,
        "baseline": base,
        "candidate": cand,
        "drifted": False,
        "reasons": [],
        "enough_data": False,
    }

    if base["count"] < th["min_samples"] or cand["count"] < th["min_samples"]:
        result["reasons"].append(
            f"not enough samples ({base['count']} baseline, {cand['count']} candidate; "
            f"need {th['min_samples']} each)")
        return result

    result["enough_data"] = True
    reasons: list[str] = []

    out_delta = _rel(base["mean_output_tokens"], cand["mean_output_tokens"])
    if abs(out_delta) > th["output_tokens_pct"]:
        reasons.append(f"output length moved {out_delta * 100:+.0f}%")

    if base["mean_quality"] is not None and cand["mean_quality"] is not None:
        q_drop = base["mean_quality"] - cand["mean_quality"]
        if q_drop > th["quality_drop"]:
            reasons.append(f"quality dropped {q_drop:.2f}")

    err_rise = cand["error_rate"] - base["error_rate"]
    if err_rise > th["error_rate_increase"]:
        reasons.append(f"error rate up {err_rise * 100:+.0f} points")

    cost_delta = _rel(base["mean_cost_usd"], cand["mean_cost_usd"])
    if abs(cost_delta) > th["cost_pct"]:
        reasons.append(f"cost per call moved {cost_delta * 100:+.0f}%")

    result["deltas"] = {
        "output_tokens_pct": round(out_delta, 4),
        "cost_pct": round(cost_delta, 4),
        "error_rate_increase": round(err_rise, 4),
        "quality_drop": (round(base["mean_quality"] - cand["mean_quality"], 4)
                         if base["mean_quality"] is not None
                         and cand["mean_quality"] is not None else None),
    }
    result["reasons"] = reasons
    result["drifted"] = bool(reasons)
    return result
