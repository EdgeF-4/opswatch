import base64
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from opswatch.auth import BasicAuth
from opswatch.dashboard import start_dashboard
from opswatch.store import Store

THEME = {"tagline": "Watching things", "accent": "#abcdef", "footer": "mine"}
WINDOWS = [("24h", 86400), ("7d", 604800)]


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.getcode(), resp.read().decode("utf-8")


def _post(url, body, headers=None):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers=headers or {})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.getcode(), resp.read().decode("utf-8")


class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "t.db"))
        self.stop = threading.Event()
        self.httpd = start_dashboard(
            "127.0.0.1", 0, self.store, "Acme Ops", THEME, WINDOWS,
            auth=None, ingest_token="tok", stop=self.stop)
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.stop.set()
        self.store.close()

    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def test_healthz_open(self):
        code, body = _get(self.base() + "/healthz")
        self.assertEqual(code, 200)
        self.assertEqual(body, "ok")

    def test_status_api(self):
        self.store.transition("monitor", "m", "failing", "down", now=100)
        code, body = _get(self.base() + "/api/status")
        self.assertEqual(code, 200)
        d = json.loads(body)
        self.assertEqual(d["brand"], "Acme Ops")
        self.assertEqual(d["overall"], "failing")
        self.assertEqual(d["open_incidents"], 1)

    def test_report_and_incidents_apis(self):
        self.store.transition("monitor", "m", "failing", "down", now=100)
        self.store.transition("monitor", "m", "ok", "up", now=160)
        _, body = _get(self.base() + "/api/report")
        self.assertIn("targets", json.loads(body))
        _, body = _get(self.base() + "/api/incidents")
        incs = json.loads(body)["incidents"]
        self.assertEqual(incs[0]["name"], "m")
        self.assertFalse(incs[0]["ongoing"])

    def test_page_injects_brand_and_theme(self):
        _, body = _get(self.base() + "/")
        self.assertIn("Acme Ops", body)
        self.assertIn("Watching things", body)

    def test_ingest_records_event(self):
        code, body = _post(self.base() + "/api/ingest",
                           {"source": "cron-x", "status": "fail", "detail": "boom"},
                           {"X-OpsWatch-Token": "tok"})
        self.assertEqual(code, 202)
        self.assertTrue(json.loads(body)["ok"])
        row = self.store.last_ingest("cron-x")
        self.assertEqual(row["status"], "fail")
        self.assertEqual(row["detail"], "boom")

    def test_ingest_rejects_bad_token(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            _post(self.base() + "/api/ingest", {"source": "x"},
                  {"X-OpsWatch-Token": "wrong"})
        self.assertEqual(ctx.exception.code, 401)

    def test_ingest_requires_source(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            _post(self.base() + "/api/ingest", {"status": "ok"},
                  {"X-OpsWatch-Token": "tok"})
        self.assertEqual(ctx.exception.code, 400)


class DashboardAuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "t.db"))
        self.stop = threading.Event()
        auth = BasicAuth("admin", password="open-sesame")
        self.httpd = start_dashboard(
            "127.0.0.1", 0, self.store, "Acme", {}, WINDOWS,
            auth=auth, ingest_token=None, stop=self.stop)
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.stop.set()
        self.store.close()

    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def test_protected_without_auth_is_401(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            _get(self.base() + "/api/status")
        self.assertEqual(ctx.exception.code, 401)
        self.assertIn("Basic", ctx.exception.headers.get("WWW-Authenticate", ""))

    def test_protected_with_auth_succeeds(self):
        cred = base64.b64encode(b"admin:open-sesame").decode("ascii")
        code, _ = _get(self.base() + "/api/status",
                       {"Authorization": "Basic " + cred})
        self.assertEqual(code, 200)

    def test_healthz_open_even_with_auth(self):
        code, body = _get(self.base() + "/healthz")
        self.assertEqual(code, 200)

    def test_ingest_disabled_returns_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            _post(self.base() + "/api/ingest", {"source": "x"})
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
