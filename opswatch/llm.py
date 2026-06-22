"""Language-model observability: pricing, the watch loop, and the panel.

This is the integrator for the LLM side of the stack, the counterpart to
`monitors.py` on the infrastructure side. It holds:

  * `LLMSettings` - the `llm` block of the config, parsed once.
  * `Pricing`     - a per-model price book that turns token counts into dollars
                    and resolves a model to its routing tier. Provider-agnostic:
                    list any Claude or OpenAI-compatible model with its rates.
  * `record_call` - the seam an application (or the ingest endpoint) uses to log
                    one prediction; it computes cost and tier, then stores it.
  * `LLMRunner`   - a loop, like the monitor runner, that watches cost, drift,
                    and eval health and raises an alert through the shared
                    notifier the moment any of them crosses its threshold. It
                    reuses the store's state-transition machinery, so an LLM
                    problem opens and closes an incident exactly like any other.
  * `build_llm_panel` - the JSON the dashboard's LLM view renders.

No model is ever called from here and no key is read. Cost is arithmetic over
token counts; quality and accuracy come from the eval harness; drift is a
comparison of recorded calls. Everything is config-driven.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from . import llmcost, llmdrift, llmeval
from .notify import Alert
from .store import LLMCall, Store

log = logging.getLogger("opswatch.llm")


# --- config ---------------------------------------------------------------

@dataclass
class LLMSettings:
    enabled: bool = False
    tick_seconds: int = 30
    cost_window_seconds: int = 3600
    scale_predictions_per_month: int | None = None
    pricing: dict = field(default_factory=dict)
    tiers: dict = field(default_factory=dict)
    prompts: list = field(default_factory=list)
    eval_suites: list = field(default_factory=list)
    thresholds: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict | None) -> "LLMSettings":
        d = d or {}
        return cls(
            enabled=bool(d.get("enabled", False)),
            tick_seconds=int(d.get("tick_seconds", 30)),
            cost_window_seconds=int(d.get("cost_window_seconds", 3600)),
            scale_predictions_per_month=d.get("scale_predictions_per_month"),
            pricing=d.get("pricing", {}),
            tiers=d.get("tiers", {}),
            prompts=d.get("prompts", []),
            eval_suites=d.get("eval_suites", []),
            thresholds=d.get("thresholds", {}),
        )


# --- pricing --------------------------------------------------------------

class Pricing:
    """A per-model price book. Rates are dollars per one million tokens."""

    def __init__(self, pricing: dict, tiers: dict | None = None):
        self._pricing = pricing or {}
        # reverse a {tier: [models]} map so a model resolves to its tier even
        # when the per-model entry does not name one.
        self._model_tier: dict[str, str] = {}
        for tier, models in (tiers or {}).items():
            for m in models:
                self._model_tier[m] = tier

    def known(self, model: str) -> bool:
        return model in self._pricing

    def cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        entry = self._pricing.get(model)
        if not entry:
            return 0.0
        cost = (input_tokens / 1_000_000.0) * float(entry.get("input_per_million", 0))
        cost += (output_tokens / 1_000_000.0) * float(entry.get("output_per_million", 0))
        return round(cost, 8)

    def tier(self, model: str) -> str:
        entry = self._pricing.get(model)
        if entry and entry.get("tier"):
            return entry["tier"]
        return self._model_tier.get(model, "")

    @classmethod
    def from_settings(cls, settings: LLMSettings) -> "Pricing":
        return cls(settings.pricing, settings.tiers)


def record_call(store: Store, pricing: Pricing, model: str,
                input_tokens: int = 0, output_tokens: int = 0,
                route: str = "", prompt: str = "", prompt_version: str = "",
                tier: str | None = None, ok: bool = True,
                quality: float | None = None, latency_ms: int = 0,
                detail: str = "", ts: float = 0.0) -> float:
    """Record one prediction, computing its cost and tier. Returns the cost.

    This is the single place a call enters the system, whether from an app that
    imports the package or from the ingest endpoint. The price book is the only
    thing that knows about money, so the rest of the stack never does.
    """
    cost = pricing.cost(model, input_tokens, output_tokens)
    resolved_tier = tier if tier is not None else pricing.tier(model)
    if not pricing.known(model):
        log.warning("llm call for unpriced model '%s' recorded at zero cost", model)
    store.record_llm_call(LLMCall(
        model=model, route=route, tier=resolved_tier, prompt=prompt,
        prompt_version=prompt_version, input_tokens=input_tokens,
        output_tokens=output_tokens, cost_usd=cost, latency_ms=latency_ms,
        ok=ok, quality=quality, detail=detail, ts=ts))
    return cost


# --- the watch loop -------------------------------------------------------

class LLMRunner:
    """Watch cost, drift, and eval health; alert on a threshold crossing.

    Mirrors `MonitorRunner`: each concern resolves to an ok/failing state, the
    store decides whether that is a real change, and only a change produces an
    alert and an incident.
    """

    def __init__(self, settings: LLMSettings, store: Store, notifier,
                 pricing: Pricing | None = None):
        self._s = settings
        self._store = store
        self._notifier = notifier
        self._pricing = pricing or Pricing.from_settings(settings)

    def tick(self, now: float | None = None) -> None:
        if not self._s.enabled:
            return
        now = time.time() if now is None else now
        try:
            self._check_cost(now)
            self._check_drift(now)
            self._check_evals(now)
        except Exception as exc:  # noqa: BLE001 - a broken check is a failing check
            log.exception("llm tick error: %s", exc)

    def _check_cost(self, now: float) -> None:
        th = self._s.thresholds
        if "cost_per_window_usd" not in th and "projected_monthly_usd" not in th:
            return
        window = self._s.cost_window_seconds
        calls = self._store.llm_calls_since(now - window)
        summary = llmcost.summarize(calls, window,
                                    self._s.scale_predictions_per_month)
        projected = (summary["projected_monthly_at_scale_usd"]
                     if summary["projected_monthly_at_scale_usd"] is not None
                     else summary["projected_monthly_runrate_usd"])
        breaches = []
        ceiling = th.get("cost_per_window_usd")
        if ceiling is not None and summary["total_cost_usd"] > ceiling:
            breaches.append(
                f"${summary['total_cost_usd']:.2f} spent in the last "
                f"{window}s exceeds ${float(ceiling):.2f}")
        monthly_ceiling = th.get("projected_monthly_usd")
        if monthly_ceiling is not None and projected > monthly_ceiling:
            breaches.append(
                f"projected ${projected:,.0f}/mo exceeds ${float(monthly_ceiling):,.0f}/mo")
        if breaches:
            self._record("cost", False, "; ".join(breaches), "LLM spend")
        else:
            ok_detail = (f"${summary['total_cost_usd']:.2f} in {window}s, "
                         f"${projected:,.0f}/mo projected")
            self._record("cost", True, ok_detail, "LLM spend")

    def _check_drift(self, now: float) -> None:
        th = self._s.thresholds.get("drift")
        for spec in self._s.prompts:
            name = spec.get("name")
            baseline = spec.get("baseline_version")
            if not name or not baseline:
                continue
            window = int(spec.get("window_seconds", self._s.cost_window_seconds))
            calls = [c for c in self._store.llm_calls_since(now - window)
                     if c["prompt"] == name]
            result = llmdrift.detect_drift(
                calls, baseline, spec.get("candidate_version"), th)
            target = f"drift:{name}"
            if result["drifted"]:
                detail = (f"prompt '{name}' {result['candidate_version']} vs "
                          f"{baseline}: " + "; ".join(result["reasons"]))
                self._record(target, False, detail, "LLM drift")
            elif result["enough_data"]:
                self._record(target, True,
                             f"prompt '{name}' stable vs {baseline}", "LLM drift")

    def _check_evals(self, now: float) -> None:
        for spec in self._s.eval_suites:
            name = spec.get("name")
            if not name:
                continue
            latest = self._store.latest_eval(name)
            if latest is None:
                continue
            target = f"eval:{name}"
            if latest["status"] == "fail":
                self._record(target, False,
                             f"eval '{name}' failing: {latest['detail']}", "LLM eval")
            else:
                self._record(target, True,
                             f"eval '{name}' passing: {latest['detail']}", "LLM eval")

    def _record(self, name: str, ok: bool, detail: str, label: str) -> None:
        new_state = "ok" if ok else "failing"
        changed, previous = self._store.transition("llm", name, new_state, detail)
        if not changed:
            return
        if new_state == "failing":
            self._notifier.notify(Alert(
                source=f"llm:{name}", severity="critical",
                title=f"{label} alert ({name})", detail=detail))
        elif previous == "failing":
            self._notifier.notify(Alert(
                source=f"llm:{name}", severity="recovered",
                title=f"{label} recovered ({name})", detail=detail))

    def run_forever(self, stop: threading.Event, tick_seconds: int | None = None) -> None:
        tick_seconds = tick_seconds or self._s.tick_seconds
        log.info("llm observability started (%d prompt(s), %d eval suite(s))",
                 len(self._s.prompts), len(self._s.eval_suites))
        while not stop.is_set():
            self.tick()
            stop.wait(tick_seconds)


# --- the dashboard panel --------------------------------------------------

def build_llm_panel(store: Store, settings: LLMSettings,
                    pricing: Pricing | None = None, now: float | None = None) -> dict:
    """The full LLM picture as a JSON-able dict for the dashboard."""
    now = time.time() if now is None else now
    pricing = pricing or Pricing.from_settings(settings)
    window = settings.cost_window_seconds

    cost = llmcost.summarize(
        store.llm_calls_since(now - window), window,
        settings.scale_predictions_per_month)

    drift = []
    th_drift = settings.thresholds.get("drift")
    for spec in settings.prompts:
        name = spec.get("name")
        baseline = spec.get("baseline_version")
        if not name or not baseline:
            continue
        pwindow = int(spec.get("window_seconds", window))
        calls = [c for c in store.llm_calls_since(now - pwindow)
                 if c["prompt"] == name]
        r = llmdrift.detect_drift(calls, baseline, spec.get("candidate_version"), th_drift)
        drift.append({
            "name": name,
            "baseline_version": r["baseline_version"],
            "candidate_version": r["candidate_version"],
            "drifted": r["drifted"],
            "enough_data": r["enough_data"],
            "reasons": r["reasons"],
            "deltas": r.get("deltas", {}),
        })

    evals = []
    for spec in settings.eval_suites:
        name = spec.get("name")
        if not name:
            continue
        latest = store.latest_eval(name)
        evals.append({
            "name": name,
            "latest": ({
                "accuracy": latest["accuracy"],
                "hallucination_rate": latest["hallucination_rate"],
                "quality": latest["quality"],
                "status": latest["status"],
                "passed": latest["passed"],
                "total": latest["total"],
                "ago_seconds": int(now - latest["ts"]),
                "detail": latest["detail"],
            } if latest else None),
            "trend": [e["accuracy"] for e in llmeval.trend(store, name, 20)],
        })

    states = []
    failing = 0
    for st in store.all_states("llm"):
        states.append({
            "name": st["name"], "status": st["status"], "detail": st["detail"],
            "since_seconds": int(now - st["since"]),
        })
        if st["status"] == "failing":
            failing += 1

    return {
        "enabled": settings.enabled,
        "generated_at": now,
        "overall": "failing" if failing else "ok",
        "failing_count": failing,
        "cost": cost,
        "drift": drift,
        "evals": evals,
        "states": states,
        "thresholds": settings.thresholds,
    }
