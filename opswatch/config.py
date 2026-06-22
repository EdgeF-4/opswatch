"""Configuration loading.

Reads a JSON config file and layers a few environment overrides on top.
Everything the stack does is declared here: the brand and theme, the dashboard
and its optional login, the jobs, the monitors, the notification channels, the
inbound ingest endpoint, and how far back history is kept.

Secrets are never stored in the config or the repo. Anything sensitive (a
webhook URL, a bot token, an SMTP or dashboard password, the ingest token) is
referenced by the name of an environment variable and read at runtime, so the
config file stays safe to commit and to hand to a client.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

DEFAULTS = {
    "brand_name": "OpsWatch",
    "theme": {
        "brand_name": None,            # falls back to brand_name when unset
        "tagline": "Self-hosted operations monitoring",
        "logo": "",                    # emoji/text shown by the title, or an image URL
        "accent": "#3b82f6",
        "ok_color": "#3fb950",
        "fail_color": "#f85149",
        "warn_color": "#d29922",
        "footer": "Self-hosted operations monitoring.",
    },
    "dashboard": {
        "host": "127.0.0.1",
        "port": 8765,
        "auth": {
            "enabled": False,
            "username": "admin",
            "password_env": "OPSWATCH_DASHBOARD_PASSWORD",
            "password_hash_env": "OPSWATCH_DASHBOARD_PASSWORD_SHA256",
            "realm": "OpsWatch",
        },
    },
    "store_path": "opswatch.db",
    "retention_days": 30,
    "scheduler": {"tick_seconds": 2, "jobs": []},
    "monitors": {"tick_seconds": 5, "warmup_seconds": 5, "checks": []},
    "ingest": {"enabled": False, "token_env": "OPSWATCH_INGEST_TOKEN"},
    "notifications": {
        "console": True,
        "file": "alerts.log",
        "webhook_env": "OPSWATCH_WEBHOOK_URL",
        "channels": [],
    },
    "reporting": {
        "windows": [["24h", 86400], ["7d", 604800], ["30d", 2592000]],
    },
}


@dataclass
class Config:
    brand_name: str
    theme: dict
    dashboard: dict
    store_path: str
    retention_days: int
    scheduler: dict
    monitors: dict
    ingest: dict
    notifications: dict
    reporting: dict
    raw: dict = field(default_factory=dict)

    @property
    def env(self) -> os._Environ:
        return os.environ

    @property
    def webhook_url(self) -> str | None:
        env_key = self.notifications.get("webhook_env", "OPSWATCH_WEBHOOK_URL")
        url = os.environ.get(env_key)
        return url or None

    @property
    def display_brand(self) -> str:
        return self.theme.get("brand_name") or self.brand_name

    @property
    def ingest_token(self) -> str | None:
        if not self.ingest.get("enabled"):
            return None
        env_key = self.ingest.get("token_env", "OPSWATCH_INGEST_TOKEN")
        return os.environ.get(env_key) or None

    @property
    def report_windows(self) -> list[tuple[str, int]]:
        return [(w[0], int(w[1])) for w in self.reporting.get("windows", [])]


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load(path: str | None) -> Config:
    data = dict(DEFAULTS)
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            data = _merge(DEFAULTS, json.load(fh))

    # Environment overrides for the handful of values you tune per box.
    if "OPSWATCH_DASHBOARD_PORT" in os.environ:
        data["dashboard"]["port"] = int(os.environ["OPSWATCH_DASHBOARD_PORT"])
    if "OPSWATCH_DASHBOARD_HOST" in os.environ:
        data["dashboard"]["host"] = os.environ["OPSWATCH_DASHBOARD_HOST"]
    if "OPSWATCH_STORE_PATH" in os.environ:
        data["store_path"] = os.environ["OPSWATCH_STORE_PATH"]

    return Config(
        brand_name=data["brand_name"],
        theme=data["theme"],
        dashboard=data["dashboard"],
        store_path=data["store_path"],
        retention_days=int(data.get("retention_days", 30)),
        scheduler=data["scheduler"],
        monitors=data["monitors"],
        ingest=data["ingest"],
        notifications=data["notifications"],
        reporting=data["reporting"],
        raw=data,
    )
