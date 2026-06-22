"""Durable state for the ops stack.

A thin SQLite layer that survives restarts. It records every job run, the
current health state of each job and monitor, and the alert history shown on
the dashboard. State transitions (ok -> failing, failing -> ok) are computed
here so the notifier only fires on a real change, never on every tick.

Standard library only. One connection guarded by a lock is plenty for the
volume a single ops box produces.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass

SCHEMA = """
CREATE TABLE IF NOT EXISTS job_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job         TEXT    NOT NULL,
    status      TEXT    NOT NULL,            -- ok | failed
    attempt     INTEGER NOT NULL DEFAULT 1,
    exit_code   INTEGER,
    output_tail TEXT,
    started_at  REAL    NOT NULL,
    finished_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_job_runs_job ON job_runs (job, started_at);

CREATE TABLE IF NOT EXISTS state (
    kind       TEXT NOT NULL,               -- job | monitor
    name       TEXT NOT NULL,
    status     TEXT NOT NULL,               -- ok | failing | unknown
    detail     TEXT,
    since      REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (kind, name)
);

CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT NOT NULL,
    severity   TEXT NOT NULL,               -- warning | critical | recovered
    title      TEXT NOT NULL,
    detail     TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts (created_at);
"""


@dataclass
class Run:
    job: str
    status: str
    attempt: int
    exit_code: int | None
    output_tail: str
    started_at: float
    finished_at: float


class Store:
    def __init__(self, path: str):
        # check_same_thread=False because the scheduler, monitor and dashboard
        # threads share one connection; every write goes through self._lock.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- job runs ---------------------------------------------------------
    def record_run(self, run: Run) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO job_runs "
                "(job, status, attempt, exit_code, output_tail, started_at, finished_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run.job, run.status, run.attempt, run.exit_code,
                 run.output_tail, run.started_at, run.finished_at),
            )
            self._conn.commit()

    def last_run(self, job: str) -> Run | None:
        row = self._conn.execute(
            "SELECT * FROM job_runs WHERE job = ? ORDER BY started_at DESC LIMIT 1",
            (job,),
        ).fetchone()
        return _row_to_run(row) if row else None

    def last_success(self, job: str) -> Run | None:
        row = self._conn.execute(
            "SELECT * FROM job_runs WHERE job = ? AND status = 'ok' "
            "ORDER BY started_at DESC LIMIT 1",
            (job,),
        ).fetchone()
        return _row_to_run(row) if row else None

    def recent_runs(self, limit: int = 50) -> list[Run]:
        rows = self._conn.execute(
            "SELECT * FROM job_runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_run(r) for r in rows]

    # -- health state + transitions --------------------------------------
    def get_state(self, kind: str, name: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM state WHERE kind = ? AND name = ?", (kind, name)
        ).fetchone()

    def all_states(self, kind: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM state WHERE kind = ? ORDER BY name", (kind,)
        ).fetchall()

    def transition(self, kind: str, name: str, status: str, detail: str,
                   now: float | None = None) -> tuple[bool, str]:
        """Upsert health state. Returns (changed, previous_status).

        `changed` is True only when the status differs from what was stored,
        which is the single signal the caller uses to decide whether to alert.
        """
        now = time.time() if now is None else now
        with self._lock:
            row = self._conn.execute(
                "SELECT status, since FROM state WHERE kind = ? AND name = ?",
                (kind, name),
            ).fetchone()
            previous = row["status"] if row else "unknown"
            changed = previous != status
            since = now if changed or row is None else row["since"]
            self._conn.execute(
                "INSERT INTO state (kind, name, status, detail, since, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(kind, name) DO UPDATE SET "
                "status = excluded.status, detail = excluded.detail, "
                "since = excluded.since, updated_at = excluded.updated_at",
                (kind, name, status, detail, since, now),
            )
            self._conn.commit()
        return changed, previous

    # -- alerts -----------------------------------------------------------
    def record_alert(self, source: str, severity: str, title: str,
                     detail: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            self._conn.execute(
                "INSERT INTO alerts (source, severity, title, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (source, severity, title, detail, now),
            )
            self._conn.commit()

    def recent_alerts(self, limit: int = 25) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()


def _row_to_run(row: sqlite3.Row) -> Run:
    return Run(
        job=row["job"], status=row["status"], attempt=row["attempt"],
        exit_code=row["exit_code"], output_tail=row["output_tail"] or "",
        started_at=row["started_at"], finished_at=row["finished_at"],
    )
