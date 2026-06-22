# OpsWatch

A self-hosted operations stack you run on your own server. It runs your jobs on
a schedule, watches that everything keeps working, and alerts you the moment
something breaks, so you find out before your customers do.

No external services, no SaaS subscription, no per-task billing. Standard
library Python only, so it installs on a fresh Linux box with nothing but
`python3` and keeps running on its own.

## What you get

- **Scheduler** runs jobs on an interval or at a daily time, with retries and a
  full history of every run.
- **Six monitor types** that check the things that actually break:
  - `http` endpoint uptime, with status, body content, and a latency ceiling
  - `heartbeat` dead-man's switch: an external job checks in on its own
    schedule, and if it goes silent you hear about it
  - `webhook` failure: an external automation reports its own success or failure
    and the dashboard reflects it live
  - `resource` thresholds for disk, memory, CPU, and load average
  - `log_pattern` scanning for errors appearing in a log file
  - `job_freshness`, the check that catches a scheduled job which silently
    stopped running
- **Multi-channel alerting** to console, an append-only log file, Slack,
  Telegram, email, or any generic webhook. Alerts fire only on a state change
  (healthy to failing, and back), so you get one alert per incident, not a flood.
- **Uptime and SLA reporting** with time-weighted uptime across 24h, 7d, and 30d
  windows, incident counts, and mean time to recovery per target.
- **A polished dashboard** with a status board, per-monitor history strips, an
  incident timeline, and an SLA report, all on one self-contained page. No
  front-end build, no CDN, nothing external to load.
- **Optional built-in login** so the dashboard is safe to expose, and an
  optional token-gated ingest endpoint for the heartbeat and webhook monitors.
- **White-label theming**: brand name, logo, tagline, colors, and footer all
  come from the config, so the same install ships under any name.

## Quick demo

No setup, no keys, nothing to sign up for:

```bash
scripts/demo.sh
```

It starts the stack on a throwaway database and drives a realistic incident: a
monitored API goes down, a data pipeline reports a failure, and a scheduled sync
stops checking in. You watch all three get caught, alert, and then clear, with
each one recorded as an incident that dents the uptime number. Open the
dashboard URL it prints to follow along live.

## Run it directly

```bash
cp config.example.json config.json
python3 -m opswatch --config config.json
# dashboard on http://127.0.0.1:8765
```

## Install on a VPS

```bash
sudo ./deploy/install.sh
```

That creates an unprivileged service user, installs the code under `/opt`, drops
an editable config at `/etc/opswatch/config.json`, and registers a `systemd`
service that restarts on failure and starts on boot. Logs and alerts stream to
`journalctl -u opswatch -f`.

The dashboard binds to `127.0.0.1` only. Turn on the built-in login, put TLS and
auth in front with `deploy/Caddyfile.example`, or both.

## Configuration

Everything is one JSON file. See `config.example.json` for the full reference.
Jobs, monitors, channels, theme, and the report windows are all declarative.

```json
{
  "scheduler": {
    "jobs": [
      { "name": "nightly_backup", "kind": "command",
        "target": "/usr/local/bin/backup.sh", "daily_at": "02:30", "max_retries": 1 },
      { "name": "crm_sync", "kind": "command",
        "target": "python3 /opt/scripts/sync.py", "interval_seconds": 300 }
    ]
  },
  "monitors": {
    "checks": [
      { "name": "api_up", "type": "http", "url": "https://example.com/health",
        "contains": "ok", "max_latency_ms": 2000 },
      { "name": "disk", "type": "resource", "metric": "disk", "min_free_pct": 10 },
      { "name": "nightly_etl", "type": "heartbeat",
        "source": "nightly-etl", "max_age_seconds": 90000 }
    ]
  }
}
```

- **Job schedules:** `interval_seconds` (every N seconds) or `daily_at` ("HH:MM").
- **Job kinds:** `command` (any shell command) or `builtin` (a function shipped
  in `opswatch/jobs.py`).
- **Monitor types:** `http`, `heartbeat`, `webhook`, `resource` (disk, memory,
  cpu, load), `log_pattern`, `job_freshness`, and `disk`.

### Notifications

Console and log-file channels are on by default. Add chat and email channels in
the `notifications.channels` list. Every secret is read from the environment by
name, so nothing sensitive lives in the config or the repo:

```bash
export OPSWATCH_SLACK_URL="https://hooks.slack.com/services/..."
export OPSWATCH_TELEGRAM_TOKEN="..."   export OPSWATCH_TELEGRAM_CHAT_ID="..."
export OPSWATCH_SMTP_PASSWORD="..."     # host/port/from/to live in the config
export OPSWATCH_WEBHOOK_URL="https://hooks.example.com/your/endpoint"
```

### Dashboard login

Set `dashboard.auth.enabled` to `true` and provide a password in the
environment. A SHA-256 hash works too if you prefer not to store the plaintext:

```bash
export OPSWATCH_DASHBOARD_PASSWORD="choose-a-strong-one"
```

### Pushing heartbeats and failures in

Turn on `ingest.enabled` and set a token. External jobs then post to the ingest
endpoint, which feeds the `heartbeat` and `webhook` monitors:

```bash
export OPSWATCH_INGEST_TOKEN="long-random-string"

# at the end of a cron job, prove it ran:
curl -fsS -X POST "$DASH/api/ingest" -H "X-OpsWatch-Token: $TOKEN" \
  -d '{"source": "nightly-etl", "status": "ok"}'

# or report a failure from a script's error path:
curl -fsS -X POST "$DASH/api/ingest" -H "X-OpsWatch-Token: $TOKEN" \
  -d '{"source": "nightly-etl", "status": "fail", "detail": "exit 1"}'
```

## Extending it

- **Add a built-in job:** write a function returning `(exit_code, output)` and
  register it in `opswatch/jobs.py`.
- **Add a monitor type:** write a `check_*` function returning `(ok, detail)` and
  wire it into `MonitorRunner.run_check` in `opswatch/monitors.py`.

## Tests

```bash
python3 -m unittest discover -s tests
```

## License

MIT. See `LICENSE`.
