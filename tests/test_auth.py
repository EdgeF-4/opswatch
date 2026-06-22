import base64
import hashlib
import unittest

from opswatch import auth as auth_mod
from opswatch.auth import BasicAuth


def _header(user, pw):
    raw = base64.b64encode(f"{user}:{pw}".encode("utf-8")).decode("ascii")
    return "Basic " + raw


class BasicAuthTest(unittest.TestCase):
    def test_plaintext_password(self):
        a = BasicAuth("admin", password="s3cret")
        self.assertTrue(a.check(_header("admin", "s3cret")))
        self.assertFalse(a.check(_header("admin", "wrong")))
        self.assertFalse(a.check(_header("root", "s3cret")))

    def test_sha256_password(self):
        digest = hashlib.sha256(b"hunter2").hexdigest()
        a = BasicAuth("admin", password_sha256=digest)
        self.assertTrue(a.check(_header("admin", "hunter2")))
        self.assertFalse(a.check(_header("admin", "hunter3")))

    def test_missing_or_malformed_header(self):
        a = BasicAuth("admin", password="x")
        self.assertFalse(a.check(None))
        self.assertFalse(a.check("Bearer abc"))
        self.assertFalse(a.check("Basic not-base64!!"))

    def test_no_password_configured_rejects(self):
        a = BasicAuth("admin")
        self.assertFalse(a.check(_header("admin", "")))


class FromConfigTest(unittest.TestCase):
    def test_disabled_returns_none(self):
        self.assertIsNone(auth_mod.from_config({"auth": {"enabled": False}}, {}))
        self.assertIsNone(auth_mod.from_config({}, {}))

    def test_enabled_with_password_env(self):
        cfg = {"auth": {"enabled": True, "username": "ops",
                        "password_env": "DASH_PW"}}
        a = auth_mod.from_config(cfg, {"DASH_PW": "letmein"})
        self.assertIsNotNone(a)
        self.assertTrue(a.check(_header("ops", "letmein")))

    def test_enabled_without_secret_fails_open(self):
        cfg = {"auth": {"enabled": True, "password_env": "MISSING"}}
        self.assertIsNone(auth_mod.from_config(cfg, {}))

    def test_enabled_with_hash_env(self):
        digest = hashlib.sha256(b"pw").hexdigest()
        cfg = {"auth": {"enabled": True, "password_hash_env": "H"}}
        a = auth_mod.from_config(cfg, {"H": digest})
        self.assertTrue(a.check(_header("admin", "pw")))


if __name__ == "__main__":
    unittest.main()
