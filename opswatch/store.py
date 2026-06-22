"""Durable state for the ops stack.

A thin SQLite layer that survives restarts. It records every job run, the
current health state of each job and monitor, the alert history, a rolling
sample history per monitor (the data behind uptime and the history strips on
the dashboard), the incidents opened and closed as things break and recover,
and inbound events pushed in over the ingest endpoint (heartbeats and
webhook-reported failures).

State transitions (ok -> failing, failing -> ok) are computed here so the
notifier only fires on a real change, never on every tick. The same transition
also opens and closes incidents, which is what the timeline and the uptime and
SLA report are built from.

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

-- One row per monitor check. Powers uptime percentages and the rolling
-- history strip on the dashboard. Pruned to a retention window on write.
CREATE TABLE IF NOT EXISTS monitor_samples (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    monitor TEXT    NOT NULL,
    ok      INTEGER NOT NULL,               -- 1 ok | 0 failing
    detail  TEXT,
    ts      REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_samples_monitor ON monitor_samples (monitor, ts);

-- One row per incident. Opened when a job or monitor goes failing, closed when
-- it recovers. The incident timeline, mean time to recovery, and the SLA report
-- are all computed from this table.
CREATE TABLE IF NOT EXISTS incidents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,              -- job | monitor
    name        TEXT NOT NULL,
    started_at  REAL NOT NULL,
    resolved_at REAL,                       -- NULL while ongoing
    detail      TEXT
);
CREATE INDEX IF NOT EXISTS idx_incidents_target ON incidents (kind, name, started_at);
CREATE INDEX IF NOT EXISTS idx_incidents_open ON incidents (kind, name, resolved_at);

-- Inbound events pushed over the ingest endpoint. A heartbeat is a row with
-- status 'ok'; a webhook-reported failure is a row with status 'fail'. The
-- heartbeat and webhook monitor types read the latest event per source.
CREATE TABLE IF NOT EXISTS ingest_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    source  TEXT    NOT NULL,
    status  TEXT    NOT NULL,               -- ok | fail
    detail  TEXT,
    ts      REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ingest_source ON ingest_events (source, ts);

-- One row per language-model call (a single prediction). This is the data
-- behind dollar cost per call and per 1000 predictions, the projected monthly
-- spend, the breakdown by model, route and tier, and prompt-version drift. Cost
-- is computed from the token counts and a per-model price book at write time, so
-- the dashboard never has to know about pricing. Pruned to the retention window.
CREATE TABLE IF NOT EXISTS llm_calls (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             REAL    NOT NULL,
    model          TEXT    NOT NULL,
    route          TEXT    NOT NULL DEFAULT '',  -- engine/feature this call served
    tier           TEXT    NOT NULL DEFAULT '',  -- cheap | standard | hard
    prompt         TEXT    NOT NULL DEFAULT '',  -- prompt name, for drift tracking
    prompt_version TEXT    NOT NULL DEFAULT '',  -- prompt version, for drift tracking
    input_tokens   INTEGER NOT NULL DEFAULT 0,
    output_tokens  INTEGER NOT NULL DEFAULT 0,
    cost_usd       REAL    NOT NULL DEFAULT 0,
    latency_ms     INTEGER NOT NULL DEFAULT 0,
    ok             INTEGER NOT NULL DEFAULT 1,   -- 1 success | 0 error
    quality        REAL,                         -- optional 0..1 quality score
    detail         TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_ts ON llm_calls (ts);
CREATE INDEX IF NOT EXISTS idx_llm_calls_prompt ON llm_calls (prompt, prompt_version, ts);

-- One row per eval-harness run against a labeled dataset. Powers the eval health
-- card and its accuracy trend on the dashboard. Pruned to the retention window.
CREATE TABLE IF NOT EXISTS llm_evals (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                 REAL    NOT NULL,
    suite              TEXT    NOT NULL,
    prompt             TEXT    NOT NULL DEFAULT '',
    prompt_version     TEXT    NOT NULL DEFAULT '',
    total              INTEGER NOT NULL DEFAULT 0,
    passed             INTEGER NOT NULL DEFAULT 0,
    accuracy           REAL    NOT NULL DEFAULT 0,
    hallucination_rate REAL    NOT NULL DEFAULT 0,
    quality            REAL    NOT NULL DEFAULT 0,
    status             TEXT    NOT NULL DEFAULT 'pass',  -- pass | fail
    detail             TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_llm_evals_suite ON llm_evals (suite, ts);
"""

# Keep this many days of monitor samples and resolved incidents. Old rows are
# pruned opportunistically so the database never grows without bound.
_DEFAULT_RETENTION_DAYS = 30


@dataclass
class Run:
    job: str
    status: str
    attempt: int
    exit_code: int | None
    output_tail: str
    started_at: float
    finished_at: float


@dataclass
class Incident:
    id: int
    kind: str
    name: str
    started_at: float
    resolved_at: float | None
    detail: str

    @property
    def ongoing(self) -> bool:
        return self.resolved_at is None

    def duration(self, now: float | None = None) -> float:
        end = self.resolved_at if self.resolved_at is not None else (
            time.time() if now is None else now)
        return max(0.0, end - self.started_at)


@dataclass
class LLMCall:
    model: str
    route: str = ""
    tier: str = ""
    prompt: str = ""
    prompt_version: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    ok: bool = True
    quality: float | None = None
    detail: str = ""
    ts: float = 0.0


@dataclass
class EvalResult:
    suite: str
    total: int
    passed: int
    accuracy: float
    hallucination_rate: float
    quality: float
    status: str                      # pass | fail
    prompt: str = ""
    prompt_version: str = ""
    detail: str = ""
    ts: float = 0.0


class Store:
    def __init__(self, path: str, retention_days: int = _DEFAULT_RETENTION_DAYS):
        # check_same_thread=False because the scheduler, monitor and dashboard
        # threads share one connection; every write goes through self._lock.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._retention_seconds = max(1, retention_days) * 86400
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

    def runs_for(self, job: str, limit: int = 50) -> list[Run]:
        rows = self._conn.execute(
            "SELECT * FROM job_runs WHERE job = ? ORDER BY started_at DESC LIMIT ?",
            (job, limit),
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
        On a real change this also opens an incident (going failing) or closes
        the open one (recovering), so the timeline and SLA report stay in sync
        with the alert stream.
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
            if changed:
                if status == "failing":
                    self._open_incident(kind, name, detail, now)
                elif previous == "failing":
                    self._close_incident(kind, name, now)
            self._conn.commit()
        return changed, previous

    # -- incidents --------------------------------------------------------
    def _open_incident(self, kind: str, name: str, detail: str, now: float) -> None:
        # Guard against a duplicate open incident if state was reset out of band.
        existing = self._conn.execute(
            "SELECT id FROM incidents WHERE kind = ? AND name = ? "
            "AND resolved_at IS NULL LIMIT 1",
            (kind, name),
        ).fetchone()
        if existing:
            return
        self._conn.execute(
            "INSERT INTO incidents (kind, name, started_at, resolved_at, detail) "
            "VALUES (?, ?, ?, NULL, ?)",
            (kind, name, now, detail),
        )

    def _close_incident(self, kind: str, name: str, now: float) -> None:
        self._conn.execute(
            "UPDATE incidents SET resolved_at = ? "
            "WHERE id = (SELECT id FROM incidents WHERE kind = ? AND name = ? "
            "AND resolved_at IS NULL ORDER BY started_at DESC LIMIT 1)",
            (now, kind, name),
        )

    def recent_incidents(self, limit: int = 50) -> list[Incident]:
        rows = self._conn.execute(
            "SELECT * FROM incidents ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_incident(r) for r in rows]

    def incidents_since(self, since_ts: float) -> list[Incident]:
        # Any incident that was open at some point at or after the window start:
        # it started in the window, or it started earlier and is still open or
        # resolved inside the window.
        rows = self._conn.execute(
            "SELECT * FROM incidents WHERE started_at >= ? "
            "OR resolved_at IS NULL OR resolved_at >= ? "
            "ORDER BY started_at DESC",
            (since_ts, since_ts),
        ).fetchall()
        return [_row_to_incident(r) for r in rows]

    def open_incident_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM incidents WHERE resolved_at IS NULL"
        ).fetchone()
        return int(row["n"])

    # -- monitor samples (history + uptime) -------------------------------
    def record_sample(self, monitor: str, ok: bool, detail: str,
                      now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            self._conn.execute(
                "INSERT INTO monitor_samples (monitor, ok, detail, ts) "
                "VALUES (?, ?, ?, ?)",
                (monitor, 1 if ok else 0, detail, now),
            )
            self._conn.execute(
                "DELETE FROM monitor_samples WHERE ts < ?",
                (now - self._retention_seconds,),
            )
            self._conn.commit()

    def recent_samples(self, monitor: str, limit: int = 60) -> list[sqlite3.Row]:
        rows = self._conn.execute(
            "SELECT ok, detail, ts FROM monitor_samples WHERE monitor = ? "
            "ORDER BY ts DESC LIMIT ?",
            (monitor, limit),
        ).fetchall()
        return list(reversed(rows))

    def sample_uptime(self, monitor: str, since_ts: float) -> tuple[int, int]:
        """Return (ok_count, total_count) of samples since a timestamp."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(ok), 0) AS ok "
            "FROM monitor_samples WHERE monitor = ? AND ts >= ?",
            (monitor, since_ts),
        ).fetchone()
        return int(row["ok"]), int(row["total"])

    # -- inbound ingest (heartbeats + webhook failures) -------------------
    def record_ingest(self, source: str, status: str, detail: str,
                      now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            self._conn.execute(
                "INSERT INTO ingest_events (source, status, detail, ts) "
                "VALUES (?, ?, ?, ?)",
                (source, status, detail, now),
            )
            self._conn.execute(
                "DELETE FROM ingest_events WHERE ts < ?",
                (now - self._retention_seconds,),
            )
            self._conn.commit()

    def last_ingest(self, source: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT source, status, detail, ts FROM ingest_events "
            "WHERE source = ? ORDER BY ts DESC LIMIT 1",
            (source,),
        ).fetchone()

    # -- llm calls (cost, routing tiers, drift) ---------------------------
    def record_llm_call(self, call: "LLMCall", now: float | None = None) -> None:
        ts = call.ts or (time.time() if now is None else now)
        with self._lock:
            self._conn.execute(
                "INSERT INTO llm_calls "
                "(ts, model, route, tier, prompt, prompt_version, input_tokens, "
                " output_tokens, cost_usd, latency_ms, ok, quality, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, call.model, call.route, call.tier, call.prompt,
                 call.prompt_version, int(call.input_tokens), int(call.output_tokens),
                 float(call.cost_usd), int(call.latency_ms), 1 if call.ok else 0,
                 call.quality, call.detail),
            )
            self._conn.execute(
                "DELETE FROM llm_calls WHERE ts < ?",
                (ts - self._retention_seconds,),
            )
            self._conn.commit()

    def llm_calls_since(self, since_ts: float) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM llm_calls WHERE ts >= ? ORDER BY ts", (since_ts,)
        ).fetchall()

    def recent_llm_calls(self, limit: int = 50) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM llm_calls ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()

    # -- llm evals (accuracy + hallucination/quality) ---------------------
    def record_llm_eval(self, result: "EvalResult", now: float | None = None) -> None:
        ts = result.ts or (time.time() if now is None else now)
        with self._lock:
            self._conn.execute(
                "INSERT INTO llm_evals "
                "(ts, suite, prompt, prompt_version, total, passed, accuracy, "
                " hallucination_rate, quality, status, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, result.suite, result.prompt, result.prompt_version,
                 int(result.total), int(result.passed), float(result.accuracy),
                 float(result.hallucination_rate), float(result.quality),
                 result.status, result.detail),
            )
            self._conn.execute(
                "DELETE FROM llm_evals WHERE ts < ?",
                (ts - self._retention_seconds,),
            )
            self._conn.commit()

    def latest_eval(self, suite: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM llm_evals WHERE suite = ? ORDER BY ts DESC LIMIT 1",
            (suite,),
        ).fetchone()

    def evals_for(self, suite: str, limit: int = 20) -> list[sqlite3.Row]:
        rows = self._conn.execute(
            "SELECT * FROM llm_evals WHERE suite = ? ORDER BY ts DESC LIMIT ?",
            (suite, limit),
        ).fetchall()
        return list(reversed(rows))

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


def _row_to_incident(row: sqlite3.Row) -> Incident:
    return Incident(
        id=row["id"], kind=row["kind"], name=row["name"],
        started_at=row["started_at"], resolved_at=row["resolved_at"],
        detail=row["detail"] or "",
    )
