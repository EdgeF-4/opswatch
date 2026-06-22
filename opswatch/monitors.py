"""Health monitors.

Monitors answer the question the dashboard exists for: "is everything that is
supposed to be running actually working right now?" Each check returns a simple
(ok, detail) pair. An alert fires only when a check changes state, so a
persistent outage pages once, not every tick.

Three check types ship; each is a few lines and easy to extend:

  * http           - an endpoint returns an expected status within a timeout
  * disk           - free space stays above a percentage floor
  * job_freshness  - a named job has succeeded within the last N seconds.
                     This is the silent-failure catcher: a job can stop running
                     entirely and this is what notices.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
import urllib.request
from dataclasses import dataclass

from .notify import Alert
from .store import Store

log = logging.getLogger("opswatch.monitors")


@dataclass
class Monitor:
    name: str
    type: str                       # http | disk | job_freshness
    params: dict
    enabled: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "Monitor":
        params = {k: v for k, v in d.items()
                  if k not in ("name", "type", "enabled")}
        return cls(
            name=d["name"], type=d["type"], params=params,
            enabled=bool(d.get("enabled", True)),
        )


def check_http(params: dict) -> tuple[bool, str]:
    url = params["url"]
    expect = int(params.get("expect_status", 200))
    timeout = float(params.get("timeout", 8))
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.getcode()
        if code == expect:
            return True, f"{url} returned {code}"
        return False, f"{url} returned {code}, expected {expect}"
    except Exception as exc:  # noqa: BLE001 - an unreachable endpoint is the failure
        return False, f"{url} unreachable: {exc}"


def check_disk(params: dict) -> tuple[bool, str]:
    path = params.get("path", "/")
    min_free_pct = float(params.get("min_free_pct", 10))
    usage = shutil.disk_usage(path)
    free_pct = usage.free / usage.total * 100
    if free_pct >= min_free_pct:
        return True, f"{free_pct:.1f}% free on {path}"
    return False, f"only {free_pct:.1f}% free on {path} (floor {min_free_pct}%)"


def check_job_freshness(params: dict, store: Store,
                        now: float | None = None) -> tuple[bool, str]:
    now = time.time() if now is None else now
    job = params["job"]
    max_age = float(params["max_age_seconds"])
    last = store.last_success(job)
    if last is None:
        return False, f"job '{job}' has no successful run on record"
    age = now - last.started_at
    if age <= max_age:
        return True, f"job '{job}' last succeeded {int(age)}s ago"
    return False, f"job '{job}' last succeeded {int(age)}s ago (max {int(max_age)}s)"


class MonitorRunner:
    def __init__(self, monitors: list[Monitor], store: Store, notifier):
        self._monitors = monitors
        self._store = store
        self._notifier = notifier

    def run_check(self, monitor: Monitor) -> tuple[bool, str]:
        if monitor.type == "http":
            return check_http(monitor.params)
        if monitor.type == "disk":
            return check_disk(monitor.params)
        if monitor.type == "job_freshness":
            return check_job_freshness(monitor.params, self._store)
        return False, f"unknown monitor type '{monitor.type}'"

    def tick(self) -> None:
        for monitor in self._monitors:
            if not monitor.enabled:
                continue
            try:
                ok, detail = self.run_check(monitor)
            except Exception as exc:  # noqa: BLE001 - a broken check is a failing check
                ok, detail = False, f"check error: {exc}"
            self._record(monitor, ok, detail)

    def _record(self, monitor: Monitor, ok: bool, detail: str) -> None:
        new_state = "ok" if ok else "failing"
        changed, previous = self._store.transition(
            "monitor", monitor.name, new_state, detail
        )
        if not changed:
            return
        if new_state == "failing":
            self._notifier.notify(Alert(
                source=f"monitor:{monitor.name}", severity="critical",
                title=f"Monitor '{monitor.name}' is down",
                detail=detail,
            ))
        elif previous == "failing":
            self._notifier.notify(Alert(
                source=f"monitor:{monitor.name}", severity="recovered",
                title=f"Monitor '{monitor.name}' recovered",
                detail=detail,
            ))

    def run_forever(self, stop: threading.Event, tick_seconds: int,
                    warmup_seconds: int = 0) -> None:
        log.info("monitors started with %d check(s)", len(self._monitors))
        # Let scheduled jobs run once before the first check, so a job that has
        # simply not had its first run yet on boot is not reported as an outage.
        if warmup_seconds:
            stop.wait(warmup_seconds)
        while not stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                log.exception("monitor tick error: %s", exc)
            stop.wait(tick_seconds)
