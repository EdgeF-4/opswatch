"""Notification channels.

An alert is delivered to every enabled channel. Each channel is independent and
none of them can crash the loop that produced the alert: a delivery failure is
logged and swallowed, never raised.

Channels that ship:

  * console  - prints the alert (also captured by journald under systemd)
  * file     - appends one JSON line per alert, easy to tail or ship to a log
  * webhook  - POSTs the alert as JSON to any endpoint
  * slack    - posts a colored message to a Slack incoming webhook
  * telegram - sends a message through a Telegram bot to a chat
  * email    - sends an alert email over SMTP

Every secret a channel needs (a webhook URL, a bot token, an SMTP password) is
read from the environment by name, so nothing sensitive lives in the config
file or the repo. A channel whose secret is missing is skipped at startup with a
log line, rather than half configured.
"""

from __future__ import annotations

import json
import logging
import smtplib
import time
import urllib.request
from dataclasses import asdict, dataclass
from email.message import EmailMessage

log = logging.getLogger("opswatch.notify")

# severity -> (slack color, leading symbol) for the richer channels.
_STYLE = {
    "critical": ("danger", "⚠"),     # warning sign
    "warning": ("warning", "⚠"),
    "recovered": ("good", "✅"),      # check mark
}


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


def _post_json(url: str, payload: dict, timeout: float, label: str) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
    except Exception as exc:  # noqa: BLE001 - never let delivery crash a loop
        log.error("%s delivery failed: %s", label, exc)


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
    """POST a compact JSON payload to any endpoint.

    Failure to deliver is logged, never raised: a notification outage must not
    take down the scheduler that produced the alert.
    """

    def __init__(self, url: str, timeout: float = 8.0):
        self.url = url
        self.timeout = timeout

    def send(self, alert: Alert) -> None:
        _post_json(self.url, {
            "text": f"[{alert.severity.upper()}] {alert.title}\n{alert.detail}",
            "alert": asdict(alert),
        }, self.timeout, "webhook")


class SlackChannel:
    """Post a colored message to a Slack incoming webhook."""

    def __init__(self, url: str, timeout: float = 8.0):
        self.url = url
        self.timeout = timeout

    def send(self, alert: Alert) -> None:
        color, symbol = _STYLE.get(alert.severity, ("#cccccc", ""))
        _post_json(self.url, {
            "text": f"{symbol} *{alert.title}*".strip(),
            "attachments": [{
                "color": color,
                "text": alert.detail,
                "fields": [
                    {"title": "severity", "value": alert.severity, "short": True},
                    {"title": "source", "value": alert.source, "short": True},
                ],
            }],
        }, self.timeout, "slack")


class TelegramChannel:
    """Send a message through a Telegram bot to a chat id."""

    def __init__(self, token: str, chat_id: str, timeout: float = 8.0):
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = str(chat_id)
        self.timeout = timeout

    def send(self, alert: Alert) -> None:
        _, symbol = _STYLE.get(alert.severity, ("", ""))
        text = (f"{symbol} <b>{alert.title}</b>\n{alert.detail}\n"
                f"<i>{alert.severity} | {alert.source}</i>").strip()
        _post_json(self.url, {
            "chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
        }, self.timeout, "telegram")


class EmailChannel:
    """Send an alert email over SMTP, with optional STARTTLS and login."""

    def __init__(self, host: str, port: int, sender: str, recipients: list[str],
                 username: str | None = None, password: str | None = None,
                 use_tls: bool = True, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.sender = sender
        self.recipients = recipients
        self.username = username
        self.password = password
        self.use_tls = use_tls
        self.timeout = timeout

    def send(self, alert: Alert) -> None:
        msg = EmailMessage()
        msg["Subject"] = f"[{alert.severity.upper()}] {alert.title}"
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.recipients)
        msg.set_content(
            f"{alert.title}\n\n{alert.detail}\n\n"
            f"severity: {alert.severity}\nsource: {alert.source}"
        )
        try:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
                if self.use_tls:
                    smtp.starttls()
                if self.username and self.password:
                    smtp.login(self.username, self.password)
                smtp.send_message(msg)
        except Exception as exc:  # noqa: BLE001 - never let delivery crash a loop
            log.error("email delivery failed: %s", exc)


class Notifier:
    def __init__(self, store, channels):
        self._store = store
        self._channels = channels

    def notify(self, alert: Alert) -> None:
        self._store.record_alert(
            alert.source, alert.severity, alert.title, alert.detail, alert.created_at
        )
        for channel in self._channels:
            try:
                channel.send(alert)
            except Exception as exc:  # noqa: BLE001 - one bad channel never blocks the rest
                log.error("channel %s failed: %s", type(channel).__name__, exc)

    @classmethod
    def from_config(cls, config, store) -> "Notifier":
        channels: list = []
        notif = config.notifications
        if notif.get("console", True):
            channels.append(ConsoleChannel())
        if notif.get("file"):
            channels.append(FileChannel(notif["file"]))

        # Legacy single-webhook setting, still honored so older configs keep working.
        if config.webhook_url:
            channels.append(WebhookChannel(config.webhook_url))
            log.info("webhook channel enabled (legacy setting)")

        for entry in notif.get("channels", []):
            built = _build_channel(entry, config.env)
            if built is not None:
                channels.append(built)
                log.info("%s channel enabled", entry.get("type"))
        return cls(store, channels)


def _build_channel(entry: dict, env: dict):
    """Build one channel from its config, reading secrets from `env` by name.

    Returns None (and logs why) when a required secret is missing, so a channel
    that is configured but not yet provisioned simply stays off.
    """
    kind = entry.get("type")

    def secret(key: str) -> str | None:
        name = entry.get(key)
        return env.get(name) if name else None

    if kind == "webhook":
        url = secret("url_env") or entry.get("url")
        if not url:
            log.warning("webhook channel skipped: no url configured")
            return None
        return WebhookChannel(url, float(entry.get("timeout", 8)))

    if kind == "slack":
        url = secret("url_env") or entry.get("url")
        if not url:
            log.warning("slack channel skipped: webhook url not set in env")
            return None
        return SlackChannel(url, float(entry.get("timeout", 8)))

    if kind == "telegram":
        token = secret("token_env") or entry.get("token")
        chat_id = secret("chat_id_env") or entry.get("chat_id")
        if not token or not chat_id:
            log.warning("telegram channel skipped: token or chat id not set")
            return None
        return TelegramChannel(token, chat_id, float(entry.get("timeout", 8)))

    if kind == "email":
        host = entry.get("host")
        sender = entry.get("from")
        recipients = entry.get("to") or []
        if isinstance(recipients, str):
            recipients = [recipients]
        if not host or not sender or not recipients:
            log.warning("email channel skipped: host, from, or to missing")
            return None
        return EmailChannel(
            host=host, port=int(entry.get("port", 587)),
            sender=sender, recipients=recipients,
            username=secret("username_env") or entry.get("username"),
            password=secret("password_env"),
            use_tls=bool(entry.get("use_tls", True)),
            timeout=float(entry.get("timeout", 10)),
        )

    log.warning("unknown channel type '%s' skipped", kind)
    return None
