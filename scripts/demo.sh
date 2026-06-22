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
echo "4) LLM observability: a normal hour of model traffic, then a prompt change"
echo "   that runs longer and pricier, plus an accuracy eval on a labeled set..."
python3 "$ROOT/scripts/llm_demo.py" "$URL" "$OPSWATCH_INGEST_TOKEN" >/dev/null || true
PYTHONPATH="$ROOT" OPSWATCH_STORE_PATH="$RUN_DIR/opswatch.db" \
  python3 -m opswatch.evalrun --config "$ROOT/config.demo.json" || true
sleep 4
python3 - "$URL" <<'PY' || true
import json, sys, urllib.request
try:
    d = json.load(urllib.request.urlopen(sys.argv[1] + "/api/llm", timeout=5))
except Exception as e:
    print(f"  (waiting for dashboard: {e})"); raise SystemExit
c = d["cost"]
proj = c["projected_monthly_at_scale_usd"]
proj = proj if proj is not None else c["projected_monthly_runrate_usd"]
print(f"  spend (last hour): ${c['total_cost_usd']:.2f} over {c['predictions']} predictions"
      f"  ->  ${c['dollars_per_1k']:.2f} per 1k, ${proj:,.0f}/mo projected at scale")
print("  spend by tier: " + ", ".join(
    f"{t['key']} ${t['cost_usd']:.2f} ({t['share_pct']:.0f}%)" for t in c["by_tier"]))
for p in d["drift"]:
    if p["drifted"]:
        print(f"  drift: prompt '{p['name']}' {p['candidate_version']} vs "
              f"{p['baseline_version']}: " + "; ".join(p["reasons"]))
for e in d["evals"]:
    L = e["latest"]
    if L:
        print(f"  eval '{e['name']}': {L['status'].upper()} at "
              f"{L['accuracy']*100:.0f}% accuracy, {L['hallucination_rate']*100:.0f}% hallucination")
PY

echo
echo "Demo complete. The stack caught failing jobs and monitors, and on the LLM"
echo "side it tracked dollar cost per tier, flagged a prompt that drifted after a"
echo "change, and graded a labeled eval set. In production these alerts also land"
echo "in Slack, Telegram, email, or any webhook you wire up."
