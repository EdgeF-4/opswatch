"""Scheduler and dispatcher.

Runs jobs on a schedule, records every run, retries on failure, and raises an
alert only when a job *changes* from healthy to failing (and again when it
recovers). Two schedule kinds cover the bulk of real automation ops work:

  * interval_seconds: run every N seconds (polls, syncs, scrapers)
  * daily_at "HH:MM": run once a day at a local wall-clock time (reports, backups)

Each due job runs in its own short-lived thread so one slow job never stalls
the loop, and the same job never overlaps itself.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass

from .jobs import REGISTRY
from .notify import Alert
from .store import Run, Store

log = logging.getLogger("opswatch.scheduler")

_OUTPUT_TAIL = 500  # chars of job output kept for the dashboard


@dataclass
class Job:
    name: str
    kind: str                       # builtin | command
    target: str                     # builtin name, or a shell command
    interval_seconds: int | None = None
    daily_at: str | None = None     # "HH:MM"
    max_retries: int = 0
    timeout: int = 60
    enabled: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        return cls(
            name=d["name"],
            kind=d.get("kind", "builtin"),
            target=d["target"],
            interval_seconds=d.get("interval_seconds"),
            daily_at=d.get("daily_at"),
            max_retries=int(d.get("max_retries", 0)),
            timeout=int(d.get("timeout", 60)),
            enabled=bool(d.get("enabled", True)),
        )


def is_due(job: Job, last_started: float | None, now: float,
           localtime=time.localtime) -> bool:
    """Pure scheduling decision, isolated so it can be unit tested."""
    if job.interval_seconds is not None:
        if last_started is None:
            return True
        return (now - last_started) >= job.interval_seconds

    if job.daily_at is not None:
        hh, mm = (int(x) for x in job.daily_at.split(":"))
        lt = localtime(now)
        target_today = time.struct_time(
            (lt.tm_year, lt.tm_mon, lt.tm_mday, hh, mm, 0,
             lt.tm_wday, lt.tm_yday, lt.tm_isdst)
        )
        target_epoch = time.mktime(target_today)
        if now < target_epoch:
            return False
        # Due if we have not already run since today's target time.
        return last_started is None or last_started < target_epoch

    return False


class Scheduler:
    def __init__(self, jobs: list[Job], store: Store, notifier):
        self._jobs = jobs
        self._store = store
        self._notifier = notifier
        self._running: set[str] = set()
        self._lock = threading.Lock()

    def tick(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        for job in self._jobs:
            if not job.enabled:
                continue
            with self._lock:
                if job.name in self._running:
                    continue
            last = self._store.last_run(job.name)
            last_started = last.started_at if last else None
            if is_due(job, last_started, now):
                self._spawn(job)

    def _spawn(self, job: Job) -> None:
        with self._lock:
            self._running.add(job.name)
        threading.Thread(target=self._run_job, args=(job,), daemon=True).start()

    def _run_job(self, job: Job) -> None:
        try:
            status, exit_code, output, attempt = self._attempt_with_retries(job)
            self._handle_result(job, status, exit_code, output, attempt)
        finally:
            with self._lock:
                self._running.discard(job.name)

    def _attempt_with_retries(self, job: Job):
        attempt = 0
        while True:
            attempt += 1
            started = time.time()
            exit_code, output = self._execute(job)
            finished = time.time()
            status = "ok" if exit_code == 0 else "failed"
            self._store.record_run(Run(
                job=job.name, status=status, attempt=attempt,
                exit_code=exit_code, output_tail=output[-_OUTPUT_TAIL:],
                started_at=started, finished_at=finished,
            ))
            if status == "ok" or attempt > job.max_retries:
                return status, exit_code, output, attempt
            log.info("job %s failed (attempt %d), retrying", job.name, attempt)
            time.sleep(min(2 ** (attempt - 1), 10))

    def _execute(self, job: Job) -> tuple[int, str]:
        try:
            if job.kind == "builtin":
                fn = REGISTRY.get(job.target)
                if fn is None:
                    return 127, f"unknown builtin job '{job.target}'"
                return fn()
            proc = subprocess.run(
                job.target, shell=True, capture_output=True,
                text=True, timeout=job.timeout,
            )
            return proc.returncode, (proc.stdout + proc.stderr).strip()
        except subprocess.TimeoutExpired:
            return 124, f"timed out after {job.timeout}s"
        except Exception as exc:  # noqa: BLE001 - report, never crash the worker
            return 1, f"dispatch error: {exc}"

    def _handle_result(self, job: Job, status: str, exit_code: int,
                       output: str, attempt: int) -> None:
        new_state = "ok" if status == "ok" else "failing"
        detail = (output[-200:] or "no output").replace("\n", " ")
        changed, previous = self._store.transition("job", job.name, new_state, detail)
        if not changed:
            return
        if new_state == "failing":
            self._notifier.notify(Alert(
                source=f"job:{job.name}", severity="critical",
                title=f"Job '{job.name}' is failing",
                detail=f"exit={exit_code} after {attempt} attempt(s): {detail}",
            ))
        elif previous == "failing":
            self._notifier.notify(Alert(
                source=f"job:{job.name}", severity="recovered",
                title=f"Job '{job.name}' recovered",
                detail="completed successfully",
            ))

    def run_forever(self, stop: threading.Event, tick_seconds: int) -> None:
        log.info("scheduler started with %d job(s)", len(self._jobs))
        while not stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                log.exception("scheduler tick error: %s", exc)
            stop.wait(tick_seconds)
