# OpsWatch

A small, self-hosted operations stack you run on your own server. It runs your
jobs on a schedule, watches that they keep working, and alerts you the moment
something breaks, so you find out before your customers do.

No external services, no SaaS subscription, no per-task billing. Standard
library Python only, so it installs on a fresh Linux box with nothing but
`python3`.

## What you get

- **Scheduler** runs jobs on an interval or at a daily time, with retries and
  full run history.
- **Monitors** continuously check that things are healthy: an HTTP endpoint
  responds, disk space stays above a floor, and a job has actually run
  recently (the check that catches a job which silently stopped).
- **Dispatch** runs your real work: any shell command, or a built-in job.
- **Alerting** fires only when state changes (healthy to failing, and back
  again), so you get one alert per incident, not a flood. Alerts go to the
  console, an append-only log, and an optional chat webhook.
- **Dashboard** is a single status page showing every job, every monitor, and
  the live alert feed. No front-end build, no CDN, nothing external to load.

## Quick demo

No setup, no keys, nothing to sign up for:

```bash
scripts/demo.sh
```

It starts the stack on a throwaway database, runs the scheduled jobs, and shows
the dashboard catching a job that fails on purpose and then recovers. Open the
dashboard URL it prints to watch it live.

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

Put TLS and a login in front of the dashboard with `deploy/Caddyfile.example`.
The dashboard binds to `127.0.0.1` only and is read-only.

## Configuration

Everything is one JSON file. Jobs and monitors are declarative.

```json
{
  "brand_name": "OpsWatch",
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
      { "name": "api_up", "type": "http", "url": "https://example.com/health" },
      { "name": "backup_fresh", "type": "job_freshness",
        "job": "nightly_backup", "max_age_seconds": 93600 }
    ]
  }
}
```

- **Job schedules:** `interval_seconds` (every N seconds) or `daily_at` ("HH:MM").
- **Job kinds:** `command` (any shell command) or `builtin` (a function shipped
  in `opswatch/jobs.py`).
- **Monitor types:** `http`, `disk`, `job_freshness`.

### Notifications

The console and log-file channels are on by default. To send alerts to a chat
channel (Slack, Telegram, Discord, Teams all accept incoming webhooks), set the
URL in the environment so it never lives in the config file or the repo:

```bash
export OPSWATCH_WEBHOOK_URL="https://hooks.example.com/your/endpoint"
```

## Extending it

- **Add a built-in job:** write a function returning `(exit_code, output)` and
  register it in `opswatch/jobs.py`.
- **Add a monitor type:** write a `check_*` function returning `(ok, detail)`
  and wire it into `MonitorRunner.run_check` in `opswatch/monitors.py`.

## Tests

```bash
python3 -m unittest discover -s tests
```

## License

MIT. See `LICENSE`.
