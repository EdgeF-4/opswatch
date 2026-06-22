import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from opswatch.monitors import (
    check_heartbeat,
    check_http,
    check_log_pattern,
    check_resource,
    check_webhook,
)
from opswatch.store import Store


class _Target(BaseHTTPRequestHandler):
    body = b"pong"
    code = 200

    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(_Target.code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(_Target.body)))
        self.end_headers()
        self.wfile.write(_Target.body)


class HttpCheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Target)
        cls.port = cls.httpd.server_address[1]
        cls.t = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.t.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def url(self):
        return f"http://127.0.0.1:{self.port}/"

    def test_status_ok(self):
        ok, detail = check_http({"url": self.url()})
        self.assertTrue(ok)
        self.assertIn("200", detail)

    def test_status_mismatch_fails(self):
        ok, _ = check_http({"url": self.url(), "expect_status": 201})
        self.assertFalse(ok)

    def test_body_contains(self):
        ok, _ = check_http({"url": self.url(), "contains": "pong"})
        self.assertTrue(ok)
        bad, detail = check_http({"url": self.url(), "contains": "nope"})
        self.assertFalse(bad)
        self.assertIn("missing", detail)

    def test_latency_ceiling_fails(self):
        # a ceiling below any real measurement guarantees the latency branch
        # fires regardless of how fast the loopback responds
        ok, detail = check_http({"url": self.url(), "max_latency_ms": -1})
        self.assertFalse(ok)
        self.assertIn("ceiling", detail)

    def test_unreachable_is_failure(self):
        ok, _ = check_http({"url": "http://127.0.0.1:1/", "timeout": 1})
        self.assertFalse(ok)


class ResourceCheckTest(unittest.TestCase):
    def test_disk_metric(self):
        ok, _ = check_resource({"metric": "disk", "path": "/", "min_free_pct": 0})
        self.assertTrue(ok)

    def test_memory_bounds(self):
        ok, _ = check_resource({"metric": "memory", "min_free_pct": 0})
        self.assertTrue(ok)
        bad, _ = check_resource({"metric": "memory", "min_free_pct": 100})
        self.assertFalse(bad)

    def test_cpu_bounds(self):
        ok, detail = check_resource({"metric": "cpu", "max_used_pct": 100})
        self.assertTrue(ok)
        self.assertIn("CPU", detail)
        bad, _ = check_resource({"metric": "cpu", "max_used_pct": -1})
        self.assertFalse(bad)

    def test_load_bounds(self):
        ok, _ = check_resource({"metric": "load", "max_load_per_cpu": 10000})
        self.assertTrue(ok)
        bad, _ = check_resource({"metric": "load", "max_load_per_cpu": -1})
        self.assertFalse(bad)

    def test_unknown_metric_fails(self):
        ok, detail = check_resource({"metric": "bogus"})
        self.assertFalse(ok)
        self.assertIn("unknown", detail)


class HeartbeatCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "t.db"))

    def tearDown(self):
        self.store.close()

    def test_never_checked_in_is_failure(self):
        ok, detail = check_heartbeat({"source": "s", "max_age_seconds": 60},
                                     self.store, now=1000)
        self.assertFalse(ok)
        self.assertIn("never", detail)

    def test_within_and_beyond_window(self):
        self.store.record_ingest("s", "ok", "", now=1000)
        ok, _ = check_heartbeat({"source": "s", "max_age_seconds": 60},
                                self.store, now=1030)
        self.assertTrue(ok)
        stale, _ = check_heartbeat({"source": "s", "max_age_seconds": 60},
                                   self.store, now=1100)
        self.assertFalse(stale)


class WebhookCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "t.db"))

    def tearDown(self):
        self.store.close()

    def test_no_events_is_ok(self):
        ok, detail = check_webhook({"source": "a"}, self.store, now=10)
        self.assertTrue(ok)
        self.assertIn("no events", detail)

    def test_reported_failure_is_failing(self):
        self.store.record_ingest("a", "fail", "downstream 500", now=10)
        ok, detail = check_webhook({"source": "a"}, self.store, now=11)
        self.assertFalse(ok)
        self.assertIn("downstream 500", detail)

    def test_reported_ok_recovers(self):
        self.store.record_ingest("a", "fail", "x", now=10)
        self.store.record_ingest("a", "ok", "", now=20)
        ok, _ = check_webhook({"source": "a"}, self.store, now=21)
        self.assertTrue(ok)

    def test_stale_ok_with_max_age_fails(self):
        self.store.record_ingest("a", "ok", "", now=10)
        ok, _ = check_webhook({"source": "a", "max_age_seconds": 30},
                              self.store, now=100)
        self.assertFalse(ok)


class LogPatternCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "app.log")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("starting up\n")

    def test_watches_from_end_then_catches_new_error(self):
        offsets = {}
        ok, detail = check_log_pattern(
            {"path": self.path, "pattern": "ERROR"}, offsets, "m")
        self.assertTrue(ok)
        self.assertIn("watching", detail)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write("ERROR something broke\n")
        bad, detail = check_log_pattern(
            {"path": self.path, "pattern": "ERROR"}, offsets, "m")
        self.assertFalse(bad)
        self.assertIn("broke", detail)
        # no new lines -> clears on the next check
        ok2, _ = check_log_pattern(
            {"path": self.path, "pattern": "ERROR"}, offsets, "m")
        self.assertTrue(ok2)

    def test_from_start_reads_whole_file(self):
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write("ERROR boom\n")
        ok, _ = check_log_pattern(
            {"path": self.path, "pattern": "ERROR", "from_start": True}, {}, "m")
        self.assertFalse(ok)

    def test_ignore_pattern_excludes_lines(self):
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write("ERROR healthcheck noise\n")
        ok, _ = check_log_pattern(
            {"path": self.path, "pattern": "ERROR",
             "ignore_pattern": "healthcheck", "from_start": True}, {}, "m")
        self.assertTrue(ok)

    def test_missing_file_is_failure(self):
        ok, detail = check_log_pattern(
            {"path": "/no/such/file.log", "pattern": "x"}, {}, "m")
        self.assertFalse(ok)
        self.assertIn("unreadable", detail)


if __name__ == "__main__":
    unittest.main()
