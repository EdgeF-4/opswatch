"""Entrypoint: wire the pieces together and run until told to stop.

    python -m opswatch --config config.json

Starts the scheduler, the monitor runner and the dashboard, each on its own
thread, and shuts them all down cleanly on Ctrl-C or SIGTERM (so it behaves
under systemd).
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading

from . import __version__, auth as auth_mod, config as config_mod
from .dashboard import start_dashboard
from .llm import LLMRunner, LLMSettings, Pricing, build_llm_panel, record_call
from .monitors import Monitor, MonitorRunner
from .notify import Notifier
from .scheduler import Job, Scheduler
from .store import Store


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="opswatch", description="Self-hosted ops stack")
    p.add_argument("--config", default="config.json", help="path to config JSON")
    p.add_argument("--version", action="version", version=f"opswatch {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_argparser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = config_mod.load(args.config)

    store = Store(cfg.store_path, retention_days=cfg.retention_days)
    notifier = Notifier.from_config(cfg, store)

    jobs = [Job.from_dict(j) for j in cfg.scheduler.get("jobs", [])]
    monitors = [Monitor.from_dict(m) for m in cfg.monitors.get("checks", [])]

    scheduler = Scheduler(jobs, store, notifier)
    monitor_runner = MonitorRunner(monitors, store, notifier)

    auth = auth_mod.from_config(cfg.dashboard, cfg.env)
    ingest_token = cfg.ingest_token

    # LLM observability: optional, off unless the config turns it on.
    llm_settings = LLMSettings.from_dict(cfg.llm)
    pricing = Pricing.from_settings(llm_settings)
    llm_runner = LLMRunner(llm_settings, store, notifier, pricing)
    llm_panel = None
    llm_ingest = None
    if llm_settings.enabled:
        llm_panel = lambda: build_llm_panel(store, llm_settings, pricing)  # noqa: E731

        def llm_ingest(payload):  # noqa: F811 - wired only when enabled
            cost = record_call(
                store, pricing, model=payload["model"],
                input_tokens=int(payload.get("input_tokens", 0)),
                output_tokens=int(payload.get("output_tokens", 0)),
                route=str(payload.get("route", "")),
                prompt=str(payload.get("prompt", "")),
                prompt_version=str(payload.get("prompt_version", "")),
                tier=payload.get("tier"),
                ok=bool(payload.get("ok", True)),
                quality=payload.get("quality"),
                latency_ms=int(payload.get("latency_ms", 0)),
                detail=str(payload.get("detail", ""))[:500])
            return {"model": payload["model"], "cost_usd": cost}

    stop = threading.Event()

    def shutdown(signum, frame):
        logging.getLogger("opswatch").info("shutting down")
        stop.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    start_dashboard(
        cfg.dashboard["host"], cfg.dashboard["port"], store,
        cfg.display_brand, cfg.theme, cfg.report_windows,
        auth, ingest_token, stop, llm_panel=llm_panel, llm_ingest=llm_ingest,
    )
    threading.Thread(
        target=scheduler.run_forever,
        args=(stop, cfg.scheduler.get("tick_seconds", 2)), daemon=True,
    ).start()
    threading.Thread(
        target=monitor_runner.run_forever,
        args=(stop, cfg.monitors.get("tick_seconds", 5),
              cfg.monitors.get("warmup_seconds", 5)), daemon=True,
    ).start()
    if llm_settings.enabled:
        threading.Thread(
            target=llm_runner.run_forever,
            args=(stop, llm_settings.tick_seconds), daemon=True,
        ).start()

    logging.getLogger("opswatch").info(
        "%s is running (dashboard http://%s:%d, auth %s, ingest %s, llm %s)",
        cfg.display_brand, cfg.dashboard["host"], cfg.dashboard["port"],
        "on" if auth else "off", "on" if ingest_token else "off",
        "on" if llm_settings.enabled else "off",
    )
    stop.wait()
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
