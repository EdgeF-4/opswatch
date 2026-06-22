#!/usr/bin/env bash
# Self-contained demo. No API keys, no external services, no paid calls.
#
# It starts the full stack plus a throwaway "payments API" on a clean database,
# then drives a realistic incident: the API goes down, a data pipeline reports a
# failure over the ingest endpoint, and a scheduled sync stops checking in. You
# watch all three get caught, then clear, and the dashboard records the
# incidents and the uptime hit. Open the dashboard URL it prints to follow live.
#
#   scripts/demo.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_DIR="$(mktemp -d)"
PORT="${OPSWATCH_DASHBOARD_PORT:-8765}"
URL="http://127.0.0.1:${PORT}"
export OPSWATCH_INGEST_TOKEN="demo-$(date +%s)"

cleanup() {
  [ -n "${OPS_PID:-}" ] && kill "$OPS_PID" 2>/dev/null || true
  [ -n "${TGT_PID:-}" ] && kill "$TGT_PID" 2>/dev/null || true
}
trap cleanup EXIT

# Push an event to the ingest endpoint: ping <source> <ok|fail> [detail]
ping() {
  python3 - "$URL" "$OPSWATCH_INGEST_TOKEN" "$1" "$2" "${3:-}" <<'PY' || true
import json, sys, urllib.request
url, token, source, status, detail = sys.argv[1:6]
body = json.dumps({"source": source, "status": status, "detail": detail}).encode()
req = urllib.request.Request(url + "/api/ingest", data=body, method="POST",
                            headers={"Content-Type": "application/json",
                                     "X-OpsWatch-Token": token})
try:
    urllib.request.urlopen(req, timeout=5).read()
except Exception:
    pass
PY
}

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
print(f"  [{state}] open incidents: {d['open_incidents']}")
print(f"  monitors: {mons}")
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

echo "1) Warming up. Sending a healthy pipeline event and a sync check-in:"
sleep 10
ping etl ok "nightly load complete"
ping nightly-sync ok "checked in"
sleep 3; snapshot

echo
echo "2) Incident: payments API down, the ETL pipeline reports a failure, and"
echo "   the nightly sync stops checking in..."
touch "$RUN_DIR/DOWN"
ping etl fail "exit 1: source database refused connection"
sleep 18; snapshot

echo
echo "3) Recovery: API restored, pipeline succeeds, sync checks in again..."
rm -f "$RUN_DIR/DOWN"
ping etl ok "re-run succeeded"
ping nightly-sync ok "checked in"
sleep 8; snapshot

echo
echo "What the stack recorded during the incident:"
python3 - "$URL" <<'PY' || true
import json, sys, urllib.request
base = sys.argv[1]
with urllib.request.urlopen(base + "/api/status", timeout=5) as r:
    d = json.load(r)
for a in reversed(d["alerts"]):
    print(f"  {a['severity'].upper():9} {a['title']}  ::  {a['detail']}")
with urllib.request.urlopen(base + "/api/incidents", timeout=5) as r:
    incs = json.load(r)["incidents"]
print()
print(f"  Incidents logged: {len(incs)}")
for i in incs[:6]:
    state = "ongoing" if i["ongoing"] else f"{i['duration_seconds']}s"
    print(f"    {i['kind']}:{i['name']:14} {state}")
PY

echo
echo "Demo complete. Three failures were caught and cleared automatically, each"
echo "recorded as an incident with its own uptime impact. In production these"
echo "alerts also land in Slack, Telegram, email, or any webhook you wire up."
