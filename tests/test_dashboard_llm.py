import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from opswatch import llm
from opswatch.dashboard import start_dashboard
from opswatch.store import Store

PRICING = {"m": {"input_per_million": 1.0, "output_per_million": 2.0, "tier": "cheap"}}


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.getcode(), resp.read().decode("utf-8")


def _post(url, body, headers=None):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers=headers or {})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.getcode(), resp.read().decode("utf-8")


class LLMDisabledTest(unittest.TestCase):
    """No llm_panel wired: the endpoint reports disabled and the tab is removed."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "t.db"))
        self.stop = threading.Event()
        self.httpd = start_dashboard(
            "127.0.0.1", 0, self.store, "Acme", {}, [("24h", 86400)],
            auth=None, ingest_token=None, stop=self.stop)
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.stop.set()
        self.store.close()

    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def test_api_llm_reports_disabled(self):
        _, body = _get(self.base() + "/api/llm")
        self.assertFalse(json.loads(body)["enabled"])

    def test_llm_ingest_disabled_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            _post(self.base() + "/api/llm/ingest", {"model": "m"})
        self.assertEqual(ctx.exception.code, 404)


class LLMEnabledTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "t.db"))
        self.settings = llm.LLMSettings.from_dict({
            "enabled": True, "pricing": PRICING, "cost_window_seconds": 3600})
        self.pricing = llm.Pricing.from_settings(self.settings)
        self.stop = threading.Event()

        def ingest(payload):
            cost = llm.record_call(
                self.store, self.pricing, model=payload["model"],
                input_tokens=int(payload.get("input_tokens", 0)),
                output_tokens=int(payload.get("output_tokens", 0)),
                route=str(payload.get("route", "")))
            return {"model": payload["model"], "cost_usd": cost}

        self.httpd = start_dashboard(
            "127.0.0.1", 0, self.store, "Acme", {}, [("24h", 86400)],
            auth=None, ingest_token="tok", stop=self.stop,
            llm_panel=lambda: llm.build_llm_panel(self.store, self.settings, self.pricing),
            llm_ingest=ingest)
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.stop.set()
        self.store.close()

    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def test_ingest_records_and_panel_reflects_cost(self):
        code, body = _post(
            self.base() + "/api/llm/ingest",
            {"model": "m", "input_tokens": 1_000_000, "output_tokens": 0,
             "route": "search"},
            {"X-OpsWatch-Token": "tok"})
        self.assertEqual(code, 202)
        self.assertAlmostEqual(json.loads(body)["cost_usd"], 1.0)

        _, body = _get(self.base() + "/api/llm")
        panel = json.loads(body)
        self.assertTrue(panel["enabled"])
        self.assertEqual(panel["cost"]["predictions"], 1)
        self.assertAlmostEqual(panel["cost"]["total_cost_usd"], 1.0)
        self.assertEqual(panel["cost"]["by_tier"][0]["key"], "cheap")

    def test_ingest_rejects_bad_token(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            _post(self.base() + "/api/llm/ingest", {"model": "m"},
                  {"X-OpsWatch-Token": "wrong"})
        self.assertEqual(ctx.exception.code, 401)

    def test_ingest_requires_model(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            _post(self.base() + "/api/llm/ingest", {"input_tokens": 5},
                  {"X-OpsWatch-Token": "tok"})
        self.assertEqual(ctx.exception.code, 400)

    def test_page_serves_llm_tab(self):
        _, body = _get(self.base() + "/")
        self.assertIn('data-view="llm"', body)
        self.assertIn('"llm_enabled": true', body)


if __name__ == "__main__":
    unittest.main()
