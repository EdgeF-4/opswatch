"""Run the eval harness from the command line and record the results.

    python -m opswatch.evalrun --config config.json            # every suite
    python -m opswatch.evalrun --config config.json --suite qa  # one suite

This is the hook that turns a labeled dataset into a recorded pass or fail with a
trend. It runs offline in replay mode (each dataset record carries its own
`prediction`), so it needs no key and no network and is fully reproducible. Wire
it to your own model by importing `opswatch.llmeval.run_suite` with a `predict`
callable, or schedule this entrypoint as a `command` job so a regression pages
you through the same alerting as everything else.

Exit code is 0 when every suite ran passes, 1 when any fails, so it doubles as a
monitored job.
"""

from __future__ import annotations

import argparse
import os

from . import config as config_mod
from . import llmeval
from .llm import LLMSettings
from .store import Store


def _resolve(path: str, config_path: str | None) -> str:
    if os.path.isabs(path) or not config_path:
        return path
    return os.path.join(os.path.dirname(os.path.abspath(config_path)), path)


def run(config_path: str, suite_name: str | None = None,
        now: float | None = None) -> int:
    cfg = config_mod.load(config_path)
    settings = LLMSettings.from_dict(cfg.llm)
    store = Store(cfg.store_path, retention_days=cfg.retention_days)
    failures = 0
    ran = 0
    try:
        for spec in settings.eval_suites:
            name = spec.get("name")
            dataset = spec.get("dataset")
            if not name or not dataset:
                continue
            if suite_name and name != suite_name:
                continue
            records = llmeval.load_dataset(_resolve(dataset, config_path))
            thresholds = {
                k: spec[k] for k in ("min_accuracy", "max_hallucination", "min_quality")
                if k in spec
            }
            result = llmeval.run_suite(
                name, records, thresholds=thresholds,
                match_mode=spec.get("match_mode", "auto"),
                prompt=spec.get("prompt", ""),
                prompt_version=spec.get("prompt_version", ""))
            store.record_llm_eval(result.to_result(), now=now)
            ran += 1
            if result.status == "fail":
                failures += 1
            print(f"[{result.status.upper()}] {name}: {result.detail}")
        if ran == 0:
            print("no eval suites configured")
    finally:
        store.close()
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="opswatch.evalrun",
                                description="Run labeled-dataset evals and record them")
    p.add_argument("--config", default="config.json", help="path to config JSON")
    p.add_argument("--suite", default=None, help="run only this suite by name")
    args = p.parse_args(argv)
    return run(args.config, args.suite)


if __name__ == "__main__":
    raise SystemExit(main())
