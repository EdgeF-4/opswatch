"""Dashboard basic auth.

Optional HTTP basic auth in front of the dashboard, so the stack is safe to
expose on its own without requiring a reverse proxy to do the gating. A reverse
proxy with TLS is still recommended in production; this is the belt to that
suspenders.

The password is never stored in the config or the repo. It is read from the
environment, either in the clear or as a SHA-256 hash, and compared in constant
time so a wrong guess leaks nothing through timing.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging

log = logging.getLogger("opswatch.auth")


class BasicAuth:
    def __init__(self, username: str, password: str | None = None,
                 password_sha256: str | None = None, realm: str = "OpsWatch"):
        self.username = username
        self.password = password
        self.password_sha256 = password_sha256.lower() if password_sha256 else None
        self.realm = realm

    def check(self, header_value: str | None) -> bool:
        """True when an Authorization header carries the right credentials."""
        if not header_value or not header_value.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header_value[6:].strip()).decode("utf-8")
        except Exception:  # noqa: BLE001 - a malformed header is simply a failed auth
            return False
        user, _, pw = decoded.partition(":")
        user_ok = hmac.compare_digest(user, self.username)
        if self.password_sha256:
            digest = hashlib.sha256(pw.encode("utf-8")).hexdigest()
            pw_ok = hmac.compare_digest(digest, self.password_sha256)
        elif self.password is not None:
            pw_ok = hmac.compare_digest(pw, self.password)
        else:
            pw_ok = False
        return user_ok and pw_ok


def from_config(dashboard_cfg: dict, env: dict) -> BasicAuth | None:
    """Build a BasicAuth from the dashboard config, or None when auth is off.

    Auth is also treated as off (with a warning) when it is switched on but no
    password or hash has been provisioned, so a misconfiguration fails open to a
    127.0.0.1-only dashboard rather than locking you out silently.
    """
    auth = dashboard_cfg.get("auth") or {}
    if not auth.get("enabled"):
        return None
    username = auth.get("username", "admin")
    password = env.get(auth["password_env"]) if auth.get("password_env") else None
    password_sha256 = (
        env.get(auth["password_hash_env"]) if auth.get("password_hash_env")
        else auth.get("password_sha256")
    )
    if not password and not password_sha256:
        log.warning("dashboard auth enabled but no password set; leaving auth off")
        return None
    return BasicAuth(username, password, password_sha256,
                     auth.get("realm", "OpsWatch"))
