#!/usr/bin/env python3
"""Seed a realistic spread of model calls into a running OpsWatch dashboard.

Posts predictions across the cheap, standard, and hard routing tiers and a few
routes, plus two versions of one prompt where the newer version runs longer and
pricier so the drift card lights up. Cost, the tier view, and drift all derive
from these calls, so the LLM panel fills in from this one script. No keys, no
real model calls: each record is synthetic metadata.

    python3 scripts/llm_demo.py <base_url> <ingest_token>
"""

from __future__ import annotations

import json
import sys
import urllib.request


def post(base: str, token: str, record: dict) -> None:
    body = json.dumps(record).encode()
    req = urllib.request.Request(
        base + "/api/llm/ingest", data=body, method="POST",
        headers={"Content-Type": "application/json", "X-OpsWatch-Token": token})
    try:
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:  # noqa: BLE001 - best effort for a demo
        pass


def seed(base: str, token: str) -> int:
    n = 0
    # A normal hour of traffic: cheap tier carries most volume, the hard tier is
    # rare but expensive. Prompt 'extract' v3 is the baseline.
    plan = [
        ("fast-mini", "classify", "extract", "v3", 700, 90, True, 0.95),
        ("fast-mini", "search", "extract", "v3", 800, 120, True, 0.93),
        ("general", "summarize", "extract", "v3", 1500, 350, True, 0.92),
        ("reasoning-large", "plan", "extract", "v3", 2200, 700, True, 0.96),
    ]
    for _ in range(8):
        for model, route, prompt, ver, itok, otok, ok, q in plan:
            post(base, token, {
                "model": model, "route": route, "prompt": prompt,
                "prompt_version": ver, "input_tokens": itok,
                "output_tokens": otok, "ok": ok, "quality": q})
            n += 1

    # A new prompt version ships: answers run noticeably longer and the standard
    # tier picks up a couple of errors. This is what drift detection catches.
    for i in range(8):
        post(base, token, {
            "model": "general", "route": "summarize", "prompt": "extract",
            "prompt_version": "v4", "input_tokens": 1500, "output_tokens": 760,
            "ok": i not in (2, 5), "quality": 0.81})
        n += 1
    return n


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: llm_demo.py <base_url> <ingest_token>")
        return 2
    count = seed(argv[1].rstrip("/"), argv[2])
    print(f"seeded {count} model calls")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
