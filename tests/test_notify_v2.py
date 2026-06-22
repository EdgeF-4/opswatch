import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from opswatch.notify import (
    Alert,
    EmailChannel,
    Notifier,
    SlackChannel,
    TelegramChannel,
    WebhookChannel,
    _build_channel,
)


class FakeStore:
    def __init__(self):
        self.recorded = []

    def record_alert(self, source, severity, title, detail, created_at):
        self.recorded.append((source, severity, title))


class FakeConfig:
    def __init__(self, notifications, env, webhook_url=None):
        self.notifications = notifications
        self.env = env
        self.webhook_url = webhook_url


_CAPTURED = []


class _Capture(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        _CAPTURED.append(json.loads(body))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


class ChannelBuildTest(unittest.TestCase):
    def test_webhook_from_env(self):
        ch = _build_channel({"type": "webhook", "url_env": "WH"}, {"WH": "http://x"})
        self.assertIsInstance(ch, WebhookChannel)
        self.assertEqual(ch.url, "http://x")

    def test_webhook_missing_url_skipped(self):
        self.assertIsNone(_build_channel({"type": "webhook", "url_env": "WH"}, {}))

    def test_slack_from_env(self):
        ch = _build_channel({"type": "slack", "url_env": "S"}, {"S": "http://s"})
        self.assertIsInstance(ch, SlackChannel)

    def test_telegram_needs_token_and_chat(self):
        ok = _build_channel(
            {"type": "telegram", "token_env": "T", "chat_id": "42"}, {"T": "abc"})
        self.assertIsInstance(ok, TelegramChannel)
        self.assertIn("/botabc/", ok.url)
        self.assertIsNone(_build_channel(
            {"type": "telegram", "token_env": "T"}, {}))

    def test_email_build_and_required_fields(self):
        ch = _build_channel({
            "type": "email", "host": "smtp.x", "from": "a@x", "to": ["b@x"],
            "username_env": "U", "password_env": "P",
        }, {"U": "user", "P": "pw"})
        self.assertIsInstance(ch, EmailChannel)
        self.assertEqual(ch.recipients, ["b@x"])
        self.assertEqual(ch.password, "pw")
        self.assertIsNone(_build_channel({"type": "email", "host": "smtp.x"}, {}))

    def test_unknown_type_skipped(self):
        self.assertIsNone(_build_channel({"type": "pager"}, {}))


class FromConfigTest(unittest.TestCase):
    def test_builds_console_file_and_listed_channels(self):
        store = FakeStore()
        cfg = FakeConfig(
            notifications={
                "console": True, "file": None,
                "channels": [{"type": "slack", "url_env": "S"}],
            },
            env={"S": "http://s"},
        )
        notifier = Notifier.from_config(cfg, store)
        kinds = [type(c).__name__ for c in notifier._channels]
        self.assertIn("ConsoleChannel", kinds)
        self.assertIn("SlackChannel", kinds)

    def test_legacy_webhook_still_wired(self):
        store = FakeStore()
        cfg = FakeConfig(
            notifications={"console": False, "channels": []},
            env={}, webhook_url="http://legacy")
        notifier = Notifier.from_config(cfg, store)
        self.assertEqual([type(c).__name__ for c in notifier._channels],
                         ["WebhookChannel"])


class DeliveryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Capture)
        cls.port = cls.httpd.server_address[1]
        cls.t = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.t.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def url(self):
        return f"http://127.0.0.1:{self.port}/"

    def setUp(self):
        _CAPTURED.clear()

    def test_webhook_payload_shape(self):
        WebhookChannel(self.url()).send(
            Alert(source="job:x", severity="critical", title="t", detail="d"))
        self.assertEqual(len(_CAPTURED), 1)
        self.assertIn("alert", _CAPTURED[0])
        self.assertEqual(_CAPTURED[0]["alert"]["title"], "t")

    def test_slack_payload_has_color_attachment(self):
        SlackChannel(self.url()).send(
            Alert(source="s", severity="recovered", title="up", detail="back"))
        self.assertEqual(_CAPTURED[0]["attachments"][0]["color"], "good")

    def test_delivery_failure_never_raises(self):
        # nothing listening on this port -> must be swallowed
        WebhookChannel("http://127.0.0.1:1/").send(
            Alert(source="s", severity="warning", title="t", detail="d"))

    def test_email_to_dead_host_is_swallowed(self):
        EmailChannel("127.0.0.1", 1, "a@x", ["b@x"], timeout=1).send(
            Alert(source="s", severity="critical", title="t", detail="d"))

    def test_notifier_isolates_a_throwing_channel(self):
        class Boom:
            def send(self, alert):
                raise RuntimeError("nope")

        store = FakeStore()
        good = WebhookChannel(self.url())
        notifier = Notifier(store, [Boom(), good])
        notifier.notify(Alert(source="s", severity="critical", title="t", detail="d"))
        self.assertEqual(len(store.recorded), 1)
        self.assertEqual(len(_CAPTURED), 1)  # the good channel still fired


if __name__ == "__main__":
    unittest.main()
