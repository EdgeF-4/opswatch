# Architecture

OpsWatch is a single Python process that runs three loops over one shared SQLite
store, plus a dashboard that reads from it. Everything is standard library, so
there is nothing to install beyond `python3` and nothing external to reach at
runtime unless you wire up an outbound alert channel.

```
                         +------------------+
   config.json  ----->   |  config.load()   |
   env vars     ----->   +------------------+
                                  |
        +-------------------------+--------------------------+
        |                         |                          |
   +---------+              +-----------+              +-------------+
   |Scheduler|              |MonitorRun |              | Dashboard   |
   | thread  |              | thread    |              | HTTP thread |
   +----+----+              +-----+-----+              +------+------+
        |                         |                           |
        |  record_run/transition  |  record_sample/transition |  reads
        +-----------+-------------+-------------+-------------+
                    |                           |
               +----v---------------------------v----+
               |              Store (SQLite)         |
               |  job_runs  state  alerts            |
               |  monitor_samples  incidents  ingest |
               |  llm_calls  llm_evals               |
               +----------------+--------------------+
                                |
                          on a state change
                                |
                          +-----v-----+
                          | Notifier  |  console, file, slack,
                          +-----------+  telegram, email, webhook
```

## Repository layout

```
opswatch/            the package (one process, standard library only)
  __main__.py        entrypoint: wires the loops together and runs them
  config.py          loads one JSON file, layers env overrides on top
  store.py           the only stateful component (SQLite, one lock)
  scheduler.py       runs due jobs with retries, records every attempt
  monitors.py        runs the checks, records a sample per check
  reporting.py       time-weighted uptime, MTTR, incident timeline
  notify.py          fans an alert out to every configured channel
  auth.py            optional constant-time dashboard basic-auth gate
  dashboard.py       the self-contained page plus the JSON and ingest APIs
  jobs.py            built-in jobs (heartbeat, sample_report, flaky)
  llm.py             LLM integrator: price book, ingest seam, watch loop, panel
  llmcost.py         cost, cost-per-1k, projections, and the tier/model/route rollup
  llmdrift.py        prompt and version drift detection
  llmeval.py         labeled-dataset accuracy and hallucination scoring
  evalrun.py         CLI that runs the eval suites and records them
tests/               unit tests, one file per module
deploy/              install.sh, the systemd unit, and a Caddy reverse-proxy example
scripts/             demo.sh (self-contained demo), llm_demo.py, demo_target.py, publish.sh
datasets/            sample labeled dataset for the eval harness
config.example.json  full configuration reference
config.docker.json   small self-contained config baked into the container image
Dockerfile           standard-library image; docker-compose.yml runs it
```

## Modules

- **`config.py`** loads one JSON file, deep-merges it over defaults, and applies
  a few environment overrides. Secrets are never values in the file; the config
  holds the *name* of an environment variable and the value is read at runtime.
- **`store.py`** is the only stateful component. One SQLite connection guarded by
  a lock serves all threads. It records job runs, the current health state of
  every target, the alert log, a rolling per-monitor sample history, the
  incidents opened and closed as things break and recover, inbound ingest
  events, and the recorded model calls and eval runs behind the LLM panel.
  `transition()` is the heart of it: it computes whether a status
  actually changed (the single signal that gates alerting) and, on a real
  change, opens or closes the matching incident.
- **`scheduler.py`** decides which jobs are due (`is_due`, a pure function that is
  unit tested in isolation), runs each due job in its own short-lived thread with
  retries, records every attempt, and alerts on a healthy-to-failing transition
  and again on recovery.
- **`monitors.py`** runs the checks. Each check is a small function returning
  `(ok, detail)`. The runner records a sample for every check (which is what
  uptime and the history strips are built from) and alerts on state changes.
- **`reporting.py`** derives time-weighted uptime, downtime, mean time to
  recovery, and the incident timeline from the incidents table. Uptime is the
  share of a window with no open incident, which is the figure an SLA is written
  against.
- **LLM observability** is four modules that sit beside the infrastructure side.
  `llmcost.py` rolls recorded calls up into dollar cost, cost per 1000
  predictions, monthly projections, and the breakdown by model, route, and tier
  (pure, the way `reporting.py` is pure). `llmdrift.py` compares a candidate
  prompt version against a baseline and flags shifts in output length, quality,
  error rate, or cost. `llmeval.py` scores a labeled dataset for accuracy and
  hallucination/quality and decides pass or fail. `llm.py` is the integrator: a
  `Pricing` book, the `record_call` ingestion seam, an `LLMRunner` loop that
  watches cost, drift, and eval health and alerts through the shared notifier
  (the LLM counterpart to `MonitorRunner`), and `build_llm_panel` for the
  dashboard. `evalrun.py` is a CLI that runs the eval suites and records them.
- **`notify.py`** fans an alert out to every configured channel. Each channel is
  independent and a delivery failure is logged, never raised, so a broken
  notification path can never take down the loop that produced the alert.
- **`auth.py`** is the optional dashboard basic-auth gate, comparing credentials
  in constant time against a password or SHA-256 hash read from the environment.
- **`dashboard.py`** serves one self-contained page and a handful of JSON APIs,
  plus the token-gated ingest endpoints. The page is themed from the config at
  request time, so the same binary white-labels to any brand. The LLM view reads
  `/api/llm`, and `/api/llm/ingest` accepts one prediction's metadata; both are
  wired only when LLM observability is enabled, so the dashboard stays decoupled
  from the LLM internals (it is handed a panel callable and an ingest callable).
- **`__main__.py`** wires the pieces together, starts the loop threads (and the
  LLM watch loop when enabled), and shuts them down cleanly on a signal so it
  behaves under `systemd`.

## Why alerting only fires on a change

A naive monitor pages on every failing tick, which trains you to ignore it. Here
the store computes a real state transition, and only a transition produces an
alert and an incident. A five hour outage is one critical alert and one
recovered alert, with a single incident that carries the full duration.

## The two inbound monitor types

`heartbeat` and `webhook` are push based. An external job posts to
`/api/ingest` with a source name and an `ok` or `fail` status. A heartbeat
monitor goes failing when no event has arrived inside its window (the dead-man's
switch); a webhook monitor reflects the latest reported status. Both read the
same `ingest_events` table. The endpoint is gated by a token so only your own
jobs can post to it.

## Concurrency and durability

Three loops share one SQLite connection through a single lock. Writes are small
and infrequent (a tick every few seconds), so contention is a non-issue at the
volume one ops box produces. State lives in SQLite, so a restart loses nothing:
job history, current health, open incidents, and the sample history all survive.

## Testing

Every module has unit tests under `tests/`, run with
`python3 -m unittest discover -s tests`. Pure decision functions (`is_due`,
uptime math, auth checks) are tested directly; the dashboard, ingest, and the
network-touching pieces are tested against throwaway loopback servers so the
suite stays fast and self-contained with no external dependencies.
