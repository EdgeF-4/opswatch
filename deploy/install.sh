#!/usr/bin/env bash
# Install the ops stack as a system service on a Linux VPS.
#
# It needs nothing but Python 3 (standard library only, no pip packages). It
# creates a dedicated unprivileged user, installs the code under /opt, drops a
# config you can edit, and registers a systemd service that restarts on failure
# and starts on boot.
#
#   sudo ./deploy/install.sh
#
# Idempotent: safe to run again to update the code in place.
set -euo pipefail

APP_USER="opswatch"
APP_DIR="/opt/opswatch"
CONFIG="/etc/opswatch/config.json"
SRC="$(cd "$(dirname "$0")/.." && pwd)"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo." >&2; exit 1
fi

command -v python3 >/dev/null || { echo "python3 is required." >&2; exit 1; }

echo "==> Creating service user '${APP_USER}'"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin "$APP_USER"

echo "==> Installing code to ${APP_DIR}"
mkdir -p "$APP_DIR"
cp -r "$SRC/opswatch" "$APP_DIR/"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "==> Writing default config to ${CONFIG} (edit this, then restart)"
mkdir -p "$(dirname "$CONFIG")" /var/lib/opswatch
if [ ! -f "$CONFIG" ]; then
  cp "$SRC/config.example.json" "$CONFIG"
fi
chown -R "$APP_USER:$APP_USER" /var/lib/opswatch
chown "$APP_USER:$APP_USER" "$CONFIG"

echo "==> Installing systemd service"
sed "s#__APP_DIR__#${APP_DIR}#g; s#__CONFIG__#${CONFIG}#g; s#__USER__#${APP_USER}#g" \
  "$SRC/deploy/opswatch.service" > /etc/systemd/system/opswatch.service
systemctl daemon-reload
systemctl enable opswatch
systemctl restart opswatch

echo
echo "Installed. Useful commands:"
echo "  systemctl status opswatch         # service health"
echo "  journalctl -u opswatch -f         # live logs and alerts"
echo "  \$EDITOR ${CONFIG}                  # edit jobs and monitors, then:"
echo "  systemctl restart opswatch"
echo
echo "The dashboard listens on 127.0.0.1 only. Put TLS and auth in front of it"
echo "with the reverse proxy example in deploy/Caddyfile.example."
