"""Health monitors.

Monitors answer the question the dashboard exists for: "is everything that is
supposed to be running actually working right now?" Each check returns a simple
(ok, detail) pair. An alert fires only when a check changes state, so a
persistent outage pages once, not every tick. Every check also drops a sample
into the store, which is what the uptime percentages, the history strips, and
the SLA report are built from.

The check types that ship:

  * http           - an endpoint returns an expected status within a timeout,
                     optionally containing an expected string, optionally inside
                     a latency ceiling. This is the uptime and response-time check.
  * heartbeat      - a dead-man's switch. An external job pings the ingest
                     endpoint on its own schedule; if a ping does not arrive
                     inside the window, the job has gone silent and this fires.
  * log_pattern    - scans new lines appended to a log file for an error pattern.
                     Edge triggered: it watches from where it last read, so a
                     fresh error fires and a clean tick clears.
  * resource       - disk, memory, CPU, or load average against a threshold.
  * webhook        - the latest event an external automation pushed for a source.
                     When that automation reports its own failure, this is failing.
  * job_freshness  - a named scheduled job has succeeded within the last N seconds.
  * disk           - free space stays above a percentage floor (kept as a thin
                     alias of the disk resource check).
"""

from __future__ import annotations

import logging
import os
import re
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
    type: str                       # http | heartbeat | log_pattern | resource | webhook | job_freshness | disk
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
    contains = params.get("contains")
    max_latency_ms = params.get("max_latency_ms")
    method = params.get("method", "GET")
    start = time.time()
    try:
        req = urllib.request.Request(url, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.getcode()
            body = resp.read(65536).decode("utf-8", "replace") if contains else ""
        ms = int((time.time() - start) * 1000)
        if code != expect:
            return False, f"{url} returned {code}, expected {expect} ({ms}ms)"
        if contains and contains not in body:
            return False, f"{url} returned {code} but body missing '{contains}' ({ms}ms)"
        if max_latency_ms is not None and ms > float(max_latency_ms):
            return False, f"{url} returned {code} but took {ms}ms (ceiling {int(max_latency_ms)}ms)"
        return True, f"{url} returned {code} in {ms}ms"
    except Exception as exc:  # noqa: BLE001 - an unreachable endpoint is the failure
        ms = int((time.time() - start) * 1000)
        return False, f"{url} unreachable after {ms}ms: {exc}"


def check_disk(params: dict) -> tuple[bool, str]:
    path = params.get("path", "/")
    min_free_pct = float(params.get("min_free_pct", 10))
    usage = shutil.disk_usage(path)
    free_pct = usage.free / usage.total * 100
    if free_pct >= min_free_pct:
        return True, f"{free_pct:.1f}% free on {path}"
    return False, f"only {free_pct:.1f}% free on {path} (floor {min_free_pct}%)"


def _cpu_used_pct(interval: float = 0.1) -> float:
    """Instantaneous CPU utilization from two /proc/stat reads."""
    def read() -> tuple[int, int]:
        with open("/proc/stat", "r", encoding="utf-8") as fh:
            parts = fh.readline().split()
        vals = [int(x) for x in parts[1:]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)  # idle + iowait
        return idle, sum(vals)
    idle1, total1 = read()
    time.sleep(interval)
    idle2, total2 = read()
    d_total = total2 - total1
    if d_total <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * (1 - (idle2 - idle1) / d_total)))


def _memory_free_pct() -> float:
    info: dict[str, int] = {}
    with open("/proc/meminfo", "r", encoding="utf-8") as fh:
        for line in fh:
            key, _, rest = line.partition(":")
            info[key.strip()] = int(rest.split()[0])  # kB
    total = info.get("MemTotal", 0)
    if total <= 0:
        return 100.0
    avail = info.get("MemAvailable")
    if avail is None:
        avail = info.get("MemFree", 0) + info.get("Buffers", 0) + info.get("Cached", 0)
    return 100.0 * avail / total


def check_resource(params: dict) -> tuple[bool, str]:
    metric = params.get("metric", "disk")
    if metric == "disk":
        return check_disk(params)
    if metric == "cpu":
        ceiling = float(params.get("max_used_pct", 90))
        try:
            used = _cpu_used_pct()
        except Exception:  # noqa: BLE001 - fall back where /proc is absent
            n = os.cpu_count() or 1
            used = min(100.0, 100.0 * os.getloadavg()[0] / n)
        ok = used <= ceiling
        return ok, f"CPU at {used:.0f}% (ceiling {int(ceiling)}%)"
    if metric == "memory":
        floor = float(params.get("min_free_pct", 10))
        try:
            free = _memory_free_pct()
        except Exception as exc:  # noqa: BLE001
            return False, f"memory unreadable: {exc}"
        ok = free >= floor
        return ok, f"{free:.0f}% memory free (floor {int(floor)}%)"
    if metric == "load":
        per_cpu = float(params.get("max_load_per_cpu", 2.0))
        n = os.cpu_count() or 1
        load1 = os.getloadavg()[0]
        ratio = load1 / n
        ok = ratio <= per_cpu
        return ok, f"load {load1:.2f} over {n} cpu = {ratio:.2f}/cpu (ceiling {per_cpu})"
    return False, f"unknown resource metric '{metric}'"


def check_heartbeat(params: dict, store: Store,
                    now: float | None = None) -> tuple[bool, str]:
    """Dead-man's switch driven by inbound pings on the ingest endpoint."""
    now = time.time() if now is None else now
    source = params["source"]
    max_age = float(params["max_age_seconds"])
    last = store.last_ingest(source)
    if last is None:
        return False, f"'{source}' has never checked in"
    age = now - last["ts"]
    if age <= max_age:
        return True, f"'{source}' checked in {int(age)}s ago"
    return False, f"'{source}' last checked in {int(age)}s ago (max {int(max_age)}s)"


def check_webhook(params: dict, store: Store,
                  now: float | None = None) -> tuple[bool, str]:
    """Reflect the latest event an external automation pushed for a source.

    The automation posts 'ok' on a clean run and 'fail' when it errors. This
    monitor goes failing the moment a failure is reported, and recovers on the
    next ok. An optional max_age_seconds also fails the check when even the last
    ok is too old, folding in a freshness guard.
    """
    now = time.time() if now is None else now
    source = params["source"]
    last = store.last_ingest(source)
    if last is None:
        return True, f"'{source}' has reported no events yet"
    detail = last["detail"] or ""
    if last["status"] == "fail":
        return False, f"'{source}' reported a failure: {detail}".rstrip(": ")
    max_age = params.get("max_age_seconds")
    if max_age is not None:
        age = now - last["ts"]
        if age > float(max_age):
            return False, f"'{source}' last reported ok {int(age)}s ago (max {int(float(max_age))}s)"
    return True, f"'{source}' last reported ok"


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


def check_log_pattern(params: dict, offsets: dict,
                      key: str | None = None) -> tuple[bool, str]:
    """Scan newly appended log lines for an error pattern.

    `offsets` is a caller-owned dict that remembers how far into the file the
    last check read, so only fresh lines are considered. On the first sight of a
    file the check starts at the current end and reports ok, so existing history
    never triggers a startup alert. A shrunk file (log rotation or truncation)
    resets the read position to the start.
    """
    path = params["path"]
    pattern = re.compile(params["pattern"])
    ignore = params.get("ignore_pattern")
    ignore_re = re.compile(ignore) if ignore else None
    max_matches = int(params.get("max_matches", 0))
    key = key if key is not None else path
    from_start = bool(params.get("from_start", False))

    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return False, f"log '{path}' unreadable: {exc}"

    if key not in offsets:
        offsets[key] = 0 if from_start else size
        if not from_start:
            return True, f"watching '{path}' from end ({size} bytes)"

    start = offsets[key]
    if start > size:  # file rotated or truncated
        start = 0
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        fh.seek(start)
        chunk = fh.read()
        offsets[key] = fh.tell()

    matches = [ln for ln in chunk.splitlines()
               if pattern.search(ln) and not (ignore_re and ignore_re.search(ln))]
    if len(matches) > max_matches:
        sample = matches[-1].strip()[:160]
        return False, f"{len(matches)} match(es) in '{path}': {sample}"
    return True, f"no new matches in '{path}'"


class MonitorRunner:
    def __init__(self, monitors: list[Monitor], store: Store, notifier):
        self._monitors = monitors
        self._store = store
        self._notifier = notifier
        self._log_offsets: dict[str, int] = {}

    def run_check(self, monitor: Monitor) -> tuple[bool, str]:
        t = monitor.type
        if t == "http":
            return check_http(monitor.params)
        if t == "disk":
            return check_disk(monitor.params)
        if t == "resource":
            return check_resource(monitor.params)
        if t == "heartbeat":
            return check_heartbeat(monitor.params, self._store)
        if t == "webhook":
            return check_webhook(monitor.params, self._store)
        if t == "job_freshness":
            return check_job_freshness(monitor.params, self._store)
        if t == "log_pattern":
            return check_log_pattern(monitor.params, self._log_offsets, monitor.name)
        return False, f"unknown monitor type '{monitor.type}'"

    def tick(self) -> None:
        for monitor in self._monitors:
            if not monitor.enabled:
                continue
            try:
                ok, detail = self.run_check(monitor)
            except Exception as exc:  # noqa: BLE001 - a broken check is a failing check
                ok, detail = False, f"check error: {exc}"
            self._store.record_sample(monitor.name, ok, detail)
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
