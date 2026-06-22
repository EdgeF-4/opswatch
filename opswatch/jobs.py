"""Built-in jobs.

Real deployments mostly run shell commands (a sync script, a backup, a poll).
These built-ins exist so the stack does something visible the moment it starts,
with no external setup and no API keys:

  * heartbeat      - writes a timestamp file, proving the scheduler is alive
  * sample_report  - appends a line of fake metrics, stands in for a real report
  * flaky          - fails part of the time on purpose, so retries and the
                     failure -> alert path are visible in the dashboard demo

A built-in returns (exit_code, output). exit_code 0 means success.
"""

from __future__ import annotations

import os
import time

_STATE_DIR = os.environ.get("OPSWATCH_STATE_DIR", ".")


def _path(name: str) -> str:
    return os.path.join(_STATE_DIR, name)


def heartbeat() -> tuple[int, str]:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(_path("heartbeat.txt"), "w", encoding="utf-8") as fh:
        fh.write(stamp + "\n")
    return 0, f"heartbeat written at {stamp}"


def sample_report() -> tuple[int, str]:
    # A deterministic, dependency-free stand-in for a real reporting job.
    line = f"{time.strftime('%H:%M:%S')} processed=42 queued=0 errors=0"
    with open(_path("report.log"), "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return 0, line


def flaky() -> tuple[int, str]:
    # Deterministic flakiness driven by the clock, not randomness, so the demo
    # is reproducible: it fails roughly one run in three.
    if int(time.time()) % 3 == 0:
        return 1, "simulated downstream timeout"
    return 0, "completed"


REGISTRY = {
    "heartbeat": heartbeat,
    "sample_report": sample_report,
    "flaky": flaky,
}
