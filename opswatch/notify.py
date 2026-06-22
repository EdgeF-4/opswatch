"""Notification channels.

An alert is delivered to every enabled channel. Three channels ship by
default:

  * console  - prints the alert (also captured by journald under systemd)
  * file     - appends one JSON line per alert, easy to tail or ship to a log
  * webhook  - POSTs the alert as JSON to a URL, off unless a URL is provided
               in the environment. This is how a chat channel (Slack, Telegram,
               Discord, Teams) gets wired in without putting a secret in the repo.

The webhook channel is the only one that touches the network, and only when an
operator opts in by setting the URL. The default demo run never makes an
outbound call.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from dataclasses import asdict, dataclass

log = logging.getLogger("opswatch.notify")


@dataclass
class Alert:
    source: str
    severity: str          # warning | critical | recovered
    title: str
    detail: str
    created_at: float = 0.0

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()


class ConsoleChannel:
    def send(self, alert: Alert) -> None:
        log.warning("[%s] %s :: %s", alert.severity.upper(), alert.title, alert.detail)


class FileChannel:
    def __init__(self, path: str):
        self.path = path

    def send(self, alert: Alert) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(alert)) + "\n")


class WebhookChannel:
    """POST a compact JSON payload to a chat or alerting webhook.

    Failure to deliver is logged, never raised: a notification outage must not
    take down the scheduler that produced the alert.
    """

    def __init__(self, url: str, timeout: float = 8.0):
        self.url = url
        self.timeout = timeout

    def send(self, alert: Alert) -> None:
        payload = json.dumps({
            "text": f"[{alert.severity.upper()}] {alert.title}\n{alert.detail}",
            "alert": asdict(alert),
        }).encode("utf-8")
        req = urllib.request.Request(
            self.url, data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp.read()
        except Exception as exc:  # noqa: BLE001 - never let delivery crash a loop
            log.error("webhook delivery failed: %s", exc)


class Notifier:
    def __init__(self, store, channels):
        self._store = store
        self._channels = channels

    def notify(self, alert: Alert) -> None:
        self._store.record_alert(
            alert.source, alert.severity, alert.title, alert.detail, alert.created_at
        )
        for channel in self._channels:
            channel.send(alert)

    @classmethod
    def from_config(cls, config, store) -> "Notifier":
        channels = []
        notif = config.notifications
        if notif.get("console", True):
            channels.append(ConsoleChannel())
        if notif.get("file"):
            channels.append(FileChannel(notif["file"]))
        if config.webhook_url:
            channels.append(WebhookChannel(config.webhook_url))
            log.info("webhook channel enabled")
        return cls(store, channels)
