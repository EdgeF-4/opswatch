#!/usr/bin/env bash
# Self-contained demo. No API keys, no external services, no paid calls.
#
# It starts the stack plus a throwaway "payments API" on a clean database, then
# takes that API down and brings it back so you can watch the monitor catch the
# outage and clear it. Open the dashboard URL it prints to follow along live.
#
#   scripts/demo.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_DIR="$(mktemp -d)"
PORT="${OPSWATCH_DASHBOARD_PORT:-8765}"
URL="http://127.0.0.1:${PORT}"

cleanup() {
  [ -n "${OPS_PID:-}" ] && kill "$OPS_PID" 2>/dev/null || true
  [ -n "${TGT_PID:-}" ] && kill "$TGT_PID" 2>/dev/null || true
}
trap cleanup EXIT

snapshot() {
  python3 - "$URL" <<'PY' || true
import json, sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1] + "/api/status", timeout=5) as r:
        d = json.load(r)
except Exception as e:
    print(f"  (waiting for dashboard: {e})"); raise SystemExit
state = "ALL OK" if d["overall"] == "ok" else f"{d['failing_count']} FAILING"
mons = ", ".join(f"{m['name']}={m['status']}" for m in d["monitors"]) or "none yet"
print(f"  [{state}] monitors: {mons} | alerts: {len(d['alerts'])}")
PY
}

cd "$RUN_DIR"
echo "Starting a throwaway payments API and the ops stack in ${RUN_DIR}"
python3 "$ROOT/scripts/demo_target.py" 8766 >"$RUN_DIR/target.out" 2>&1 &
TGT_PID=$!
PYTHONPATH="$ROOT" OPSWATCH_STATE_DIR="$RUN_DIR" OPSWATCH_STORE_PATH="$RUN_DIR/opswatch.db" \
  python3 -m opswatch --config "$ROOT/config.demo.json" >"$RUN_DIR/opswatch.out" 2>&1 &
OPS_PID=$!

echo "Dashboard:  ${URL}    (open it in a browser to watch live)"
echo
echo "1) Warming up. Everything should be healthy:"
sleep 12; snapshot

echo
echo "2) Simulating an outage: taking the payments API down..."
touch "$RUN_DIR/DOWN"
sleep 12; snapshot

echo
echo "3) Restoring the payments API..."
rm -f "$RUN_DIR/DOWN"
sleep 12; snapshot

echo
echo "What the stack recorded during the incident:"
python3 - "$URL" <<'PY' || true
import json, sys, urllib.request
with urllib.request.urlopen(sys.argv[1] + "/api/status", timeout=5) as r:
    d = json.load(r)
for a in reversed(d["alerts"]):
    print(f"  {a['severity'].upper():9} {a['title']}  ::  {a['detail']}")
PY

echo
echo "Demo complete. The outage was caught and cleared automatically."
echo "In production these alerts also land in your chat channel."
