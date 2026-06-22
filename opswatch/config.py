"""Configuration loading.

Reads a JSON config file and layers a few environment overrides on top.
Secrets (like a notification webhook URL) are never stored in the repo; they
are read from the environment at runtime so the config file stays safe to
commit and to hand to a client.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

DEFAULTS = {
    "brand_name": "OpsWatch",
    "dashboard": {"host": "127.0.0.1", "port": 8765},
    "store_path": "opswatch.db",
    "scheduler": {"tick_seconds": 2, "jobs": []},
    "monitors": {"tick_seconds": 5, "checks": []},
    "notifications": {
        "console": True,
        "file": "alerts.log",
        "webhook_env": "OPSWATCH_WEBHOOK_URL",
    },
}


@dataclass
class Config:
    brand_name: str
    dashboard: dict
    store_path: str
    scheduler: dict
    monitors: dict
    notifications: dict
    raw: dict = field(default_factory=dict)

    @property
    def webhook_url(self) -> str | None:
        env_key = self.notifications.get("webhook_env", "OPSWATCH_WEBHOOK_URL")
        url = os.environ.get(env_key)
        return url or None


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

    # Environment overrides for the handful of values an operator tunes per box.
    if "OPSWATCH_DASHBOARD_PORT" in os.environ:
        data["dashboard"]["port"] = int(os.environ["OPSWATCH_DASHBOARD_PORT"])
    if "OPSWATCH_DASHBOARD_HOST" in os.environ:
        data["dashboard"]["host"] = os.environ["OPSWATCH_DASHBOARD_HOST"]
    if "OPSWATCH_STORE_PATH" in os.environ:
        data["store_path"] = os.environ["OPSWATCH_STORE_PATH"]

    return Config(
        brand_name=data["brand_name"],
        dashboard=data["dashboard"],
        store_path=data["store_path"],
        scheduler=data["scheduler"],
        monitors=data["monitors"],
        notifications=data["notifications"],
        raw=data,
    )
