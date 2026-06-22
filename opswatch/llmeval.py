"""Eval harness for labeled datasets.

Runs a prompt against a dataset of labeled examples and scores three things a
buyer cares about: did it get the right answer (accuracy), did it make claims
the source does not support (hallucination), and an overall quality gate that
combines the two. A run produces a pass or fail against thresholds, and the
store keeps every run so the dashboard can show the trend.

It is provider-agnostic. A dataset is a list of records, each with an `input`
and an `expected` answer, optionally a grounding `context` and an explicit
`quality` score. Predictions come from one of two places:

  * a `predict(input) -> str` callable you pass in, wired to whatever model you
    use (Claude, an OpenAI-compatible endpoint, anything), or
  * a `prediction` field already on each record (replay mode), so a run is fully
    offline and reproducible with no key and no network.

The accuracy and grounding checks are deliberate, documented heuristics, not a
model grading itself: normalized matching for accuracy, and a salient-token
coverage check for grounding. They are good enough to catch a regression and
they never need an external call.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .store import EvalResult

DEFAULT_THRESHOLDS = {
    "min_accuracy": 0.80,
    "max_hallucination": 0.10,
    "min_quality": 0.0,
}

_WORD = re.compile(r"[a-z0-9]+")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _norm(text: str) -> str:
    return " ".join(_WORD.findall(str(text).lower()))


def is_correct(expected: str, prediction: str, mode: str = "auto") -> bool:
    """Whether a prediction matches the expected answer.

    auto      - numeric compare when the expected answer is a number, else a
                normalized exact match falling back to containment
    exact     - normalized exact match
    contains  - expected appears inside the prediction (normalized)
    numeric   - numeric equality within a small tolerance
    """
    exp, pred = str(expected), str(prediction)
    if mode in ("numeric", "auto") and _NUMBER.fullmatch(exp.strip()):
        nums = _NUMBER.findall(pred)
        return bool(nums) and any(abs(float(n) - float(exp)) < 1e-9 for n in nums)
    if mode == "contains":
        return _norm(exp) in _norm(pred)
    ne, np = _norm(exp), _norm(pred)
    if ne == np:
        return True
    return mode == "auto" and bool(ne) and ne in np


def is_grounded(prediction: str, context: str, expected: str = "") -> bool:
    """Heuristic: are the salient tokens of the prediction supported?

    A salient token is a longer word or any number. If too many salient tokens
    in the prediction appear in neither the context nor the expected answer, the
    prediction is asserting unsupported content and is treated as a
    hallucination. With no context there is nothing to ground against, so the
    record is excluded from the hallucination rate by the caller.
    """
    supported = set(_WORD.findall((context + " " + expected).lower()))
    tokens = [t for t in _WORD.findall(prediction.lower())
              if len(t) >= 4 or t.isdigit()]
    if not tokens:
        return True
    unsupported = [t for t in tokens if t not in supported]
    return (len(unsupported) / len(tokens)) <= 0.5


@dataclass
class EvalRun:
    suite: str
    total: int
    passed: int
    accuracy: float
    hallucination_rate: float
    quality: float
    status: str
    prompt: str = ""
    prompt_version: str = ""
    detail: str = ""

    def to_result(self, ts: float = 0.0) -> EvalResult:
        return EvalResult(
            suite=self.suite, total=self.total, passed=self.passed,
            accuracy=self.accuracy, hallucination_rate=self.hallucination_rate,
            quality=self.quality, status=self.status, prompt=self.prompt,
            prompt_version=self.prompt_version, detail=self.detail, ts=ts)


def load_dataset(path: str) -> list[dict]:
    """Read a dataset from JSON (a list) or JSONL (one record per line)."""
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read().strip()
    if not text:
        return []
    if text[0] == "[":
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def run_suite(suite: str, records: list[dict], predict=None,
              thresholds: dict | None = None, match_mode: str = "auto",
              prompt: str = "", prompt_version: str = "") -> EvalRun:
    """Score a dataset and decide pass or fail.

    `predict` is an optional `input -> output` callable; without it each record's
    own `prediction` field is replayed. Accuracy is the share of correct answers;
    the hallucination rate is measured only over records that carry a `context`.
    Quality is an explicit per-record `quality` when present, otherwise 1.0 for a
    record that is both correct and grounded and 0.0 otherwise.
    """
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    total = len(records)
    if total == 0:
        return EvalRun(suite, 0, 0, 0.0, 0.0, 0.0, "fail", prompt,
                       prompt_version, "empty dataset")

    correct = 0
    grounded_checked = 0
    hallucinated = 0
    quality_sum = 0.0
    for rec in records:
        inp = rec.get("input", "")
        expected = rec.get("expected", "")
        prediction = predict(inp) if predict else rec.get("prediction", "")
        ok = is_correct(expected, prediction, match_mode)
        correct += 1 if ok else 0

        context = rec.get("context")
        grounded = True
        if context is not None:
            grounded_checked += 1
            grounded = is_grounded(str(prediction), str(context), str(expected))
            if not grounded:
                hallucinated += 1

        if "quality" in rec:
            quality_sum += float(rec["quality"])
        else:
            quality_sum += 1.0 if (ok and grounded) else 0.0

    accuracy = round(correct / total, 4)
    hallucination_rate = round(hallucinated / grounded_checked, 4) if grounded_checked else 0.0
    quality = round(quality_sum / total, 4)
    passed = correct

    ok_status = (accuracy >= th["min_accuracy"]
                 and hallucination_rate <= th["max_hallucination"]
                 and quality >= th["min_quality"])
    status = "pass" if ok_status else "fail"
    detail = (f"accuracy {accuracy:.0%}, hallucination {hallucination_rate:.0%}, "
              f"quality {quality:.2f} over {total} examples")
    return EvalRun(suite, total, passed, accuracy, hallucination_rate,
                   quality, status, prompt, prompt_version, detail)


def trend(store, suite: str, limit: int = 20) -> list[dict]:
    """Recent runs of a suite, oldest first, for the dashboard sparkline."""
    out = []
    for row in store.evals_for(suite, limit):
        out.append({
            "ts": row["ts"],
            "accuracy": row["accuracy"],
            "hallucination_rate": row["hallucination_rate"],
            "quality": row["quality"],
            "status": row["status"],
        })
    return out
