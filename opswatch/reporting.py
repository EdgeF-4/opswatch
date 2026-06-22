"""Uptime and SLA reporting.

Everything here is derived from the incidents the store opens and closes as
jobs and monitors break and recover. Uptime is time weighted: it is the share
of a window during which a target had no open incident, which is the number a
client actually cares about and the one an SLA is written against.

  * uptime_pct      - availability of one target over a window
  * downtime_seconds- total time a target was down inside a window
  * mttr_seconds    - mean time to recovery across resolved incidents
  * build_report    - the whole picture as a JSON-able dict for the dashboard
"""

from __future__ import annotations

import time

from .store import Incident, Store

# The windows the report rolls up to. Label and length in seconds.
DEFAULT_WINDOWS: list[tuple[str, int]] = [
    ("24h", 86400),
    ("7d", 604800),
    ("30d", 2592000),
]


def downtime_seconds(incidents: list[Incident], kind: str, name: str,
                     window_start: float, now: float) -> float:
    """Seconds a target spent down inside [window_start, now]."""
    total = 0.0
    for inc in incidents:
        if inc.kind != kind or inc.name != name:
            continue
        start = max(inc.started_at, window_start)
        end = inc.resolved_at if inc.resolved_at is not None else now
        end = min(end, now)
        if end > start:
            total += end - start
    return total


def uptime_pct(store: Store, kind: str, name: str, window_seconds: int,
               now: float | None = None) -> float:
    now = time.time() if now is None else now
    window_start = now - window_seconds
    incidents = store.incidents_since(window_start)
    down = downtime_seconds(incidents, kind, name, window_start, now)
    up = max(0.0, window_seconds - down)
    return round(100.0 * up / window_seconds, 4)


def mttr_seconds(incidents: list[Incident], kind: str, name: str,
                 window_start: float, now: float) -> float:
    durations = [
        inc.duration(now) for inc in incidents
        if inc.kind == kind and inc.name == name
        and inc.resolved_at is not None and inc.resolved_at >= window_start
    ]
    return sum(durations) / len(durations) if durations else 0.0


def _target_report(store: Store, kind: str, name: str,
                   windows: list[tuple[str, int]], now: float) -> dict:
    longest = max(w[1] for w in windows)
    incidents = store.incidents_since(now - longest)
    mine = [i for i in incidents if i.kind == kind and i.name == name]
    per_window = {}
    for label, secs in windows:
        window_start = now - secs
        opened = sum(1 for i in mine if i.started_at >= window_start)
        per_window[label] = {
            "uptime_pct": uptime_pct(store, kind, name, secs, now),
            "incidents": opened,
            "downtime_seconds": int(downtime_seconds(mine, kind, name, window_start, now)),
            "mttr_seconds": int(mttr_seconds(mine, kind, name, window_start, now)),
        }
    ongoing = any(i.ongoing for i in mine)
    return {"kind": kind, "name": name, "ongoing": ongoing, "windows": per_window}


def build_report(store: Store, windows: list[tuple[str, int]] | None = None,
                 now: float | None = None) -> dict:
    """The full uptime and SLA picture for every job and monitor."""
    now = time.time() if now is None else now
    windows = windows or DEFAULT_WINDOWS
    targets = []
    for kind in ("monitor", "job"):
        for st in store.all_states(kind):
            targets.append(_target_report(store, kind, st["name"], windows, now))
    return {
        "generated_at": now,
        "windows": [w[0] for w in windows],
        "targets": targets,
    }


def timeline(store: Store, limit: int = 50, now: float | None = None) -> list[dict]:
    """Recent incidents, newest first, with durations for the dashboard."""
    now = time.time() if now is None else now
    out = []
    for inc in store.recent_incidents(limit):
        out.append({
            "kind": inc.kind,
            "name": inc.name,
            "started_at": inc.started_at,
            "resolved_at": inc.resolved_at,
            "ongoing": inc.ongoing,
            "duration_seconds": int(inc.duration(now)),
            "detail": inc.detail,
        })
    return out
