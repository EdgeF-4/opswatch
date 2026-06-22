"""Status dashboard.

A single self-contained app served from the standard library: no front-end
build, no CDN, no external assets. It is the thing a buyer looks at, so it earns
the polish. Five views share one page and poll the same handful of JSON APIs:

  * Overview   - a status board of every job and monitor at a glance
  * Monitors   - each monitor with its rolling history strip and 24h uptime
  * Jobs       - each scheduled job, its last run and recent outcome
  * Incidents  - the timeline of everything that has broken and recovered
  * Reports    - uptime and SLA per target across 24h, 7d and 30d windows

Theme, brand, logo, tagline and colors all come from the config, so the same
binary white-labels to any name. An optional basic-auth gate sits in front, and
an optional token-gated ingest endpoint accepts heartbeats and webhook-reported
failures pushed in from the outside.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import reporting

log = logging.getLogger("opswatch.dashboard")

_MAX_INGEST_BODY = 64 * 1024


def _build_status(store, display_brand: str) -> dict:
    now = time.time()
    jobs = []
    for st in store.all_states("job"):
        last = store.last_run(st["name"])
        jobs.append({
            "name": st["name"],
            "status": st["status"],
            "detail": st["detail"],
            "since_seconds": int(now - st["since"]),
            "last_run": int(now - last.started_at) if last else None,
            "uptime_24h": reporting.uptime_pct(store, "job", st["name"], 86400, now),
        })
    monitors = []
    for st in store.all_states("monitor"):
        samples = store.recent_samples(st["name"], 60)
        monitors.append({
            "name": st["name"],
            "status": st["status"],
            "detail": st["detail"],
            "since_seconds": int(now - st["since"]),
            "history": [int(s["ok"]) for s in samples],
            "uptime_24h": reporting.uptime_pct(store, "monitor", st["name"], 86400, now),
        })
    alerts = [{
        "source": a["source"], "severity": a["severity"],
        "title": a["title"], "detail": a["detail"],
        "ago_seconds": int(now - a["created_at"]),
    } for a in store.recent_alerts(25)]

    failing = sum(1 for j in jobs if j["status"] == "failing")
    failing += sum(1 for m in monitors if m["status"] == "failing")
    return {
        "brand": display_brand,
        "overall": "failing" if failing else "ok",
        "failing_count": failing,
        "open_incidents": store.open_incident_count(),
        "generated_at": now,
        "jobs": jobs,
        "monitors": monitors,
        "alerts": alerts,
    }


def _make_handler(store, display_brand, theme, report_windows, auth, ingest_token):
    boot = json.dumps({"brand": display_brand, "theme": theme})

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence default request logging
            pass

        def _send(self, code, body, content_type, extra_headers=None):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra_headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

        def _require_auth(self) -> bool:
            if auth is None:
                return True
            if auth.check(self.headers.get("Authorization")):
                return True
            self._send(401, b"authentication required", "text/plain",
                       {"WWW-Authenticate": f'Basic realm="{auth.realm}"'})
            return False

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/healthz":
                self._send(200, b"ok", "text/plain")
                return
            if not self._require_auth():
                return
            if path.startswith("/api/status"):
                self._json(200, _build_status(store, display_brand))
            elif path.startswith("/api/report"):
                self._json(200, reporting.build_report(store, report_windows))
            elif path.startswith("/api/incidents"):
                self._json(200, {"incidents": reporting.timeline(store, 100)})
            elif path in ("/", "/index.html"):
                self._send(200, PAGE.replace("__BRAND__", display_brand)
                           .replace("__BOOT__", boot).encode("utf-8"),
                           "text/html; charset=utf-8")
            else:
                self._send(404, b"not found", "text/plain")

        def do_HEAD(self):
            self.do_GET()

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            if path == "/api/ingest":
                self._handle_ingest()
            else:
                self._send(404, b"not found", "text/plain")

        def _handle_ingest(self):
            if ingest_token is None:
                self._send(404, b"ingest disabled", "text/plain")
                return
            supplied = (self.headers.get("X-OpsWatch-Token")
                        or _query_param(self.path, "token"))
            if supplied != ingest_token:
                self._send(401, b"bad ingest token", "text/plain")
                return
            try:
                length = min(int(self.headers.get("Content-Length", 0)), _MAX_INGEST_BODY)
                raw = self.rfile.read(length) if length else b"{}"
                payload = json.loads(raw or b"{}")
            except Exception:  # noqa: BLE001 - a bad body is a client error, not a crash
                self._json(400, {"ok": False, "error": "invalid json body"})
                return
            source = payload.get("source") or _query_param(self.path, "source")
            if not source:
                self._json(400, {"ok": False, "error": "missing source"})
                return
            status = (payload.get("status") or "ok").lower()
            if status not in ("ok", "fail"):
                status = "ok"
            detail = str(payload.get("detail", ""))[:500]
            store.record_ingest(source, status, detail)
            self._json(202, {"ok": True, "source": source, "status": status})

    return Handler


def _query_param(path: str, key: str) -> str | None:
    if "?" not in path:
        return None
    query = path.split("?", 1)[1]
    for pair in query.split("&"):
        k, _, v = pair.partition("=")
        if k == key:
            from urllib.parse import unquote_plus
            return unquote_plus(v)
    return None


def start_dashboard(host: str, port: int, store, display_brand: str, theme: dict,
                    report_windows, auth, ingest_token,
                    stop: threading.Event) -> ThreadingHTTPServer:
    handler = _make_handler(store, display_brand, theme, report_windows,
                            auth, ingest_token)
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.timeout = 1

    def serve():
        log.info("dashboard on http://%s:%d", host, port)
        while not stop.is_set():
            httpd.handle_request()
        httpd.server_close()

    threading.Thread(target=serve, daemon=True).start()
    return httpd


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__BRAND__ status</title>
<style>
  :root {
    color-scheme: dark;
    --ok: #3fb950; --fail: #f85149; --warn: #d29922; --accent: #3b82f6;
    --bg: #0d1117; --panel: #161b22; --line: #21262d; --ink: #e6edf3; --muted: #8b949e;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink);
         font: 15px/1.5 -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif; }
  header { padding: 16px 24px; border-bottom: 1px solid var(--line);
           display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
  .logo { font-size: 22px; line-height: 1; }
  .logo img { height: 26px; vertical-align: middle; }
  header h1 { font-size: 18px; margin: 0; font-weight: 650; }
  header .tagline { color: var(--muted); font-size: 13px; }
  .grow { flex: 1; }
  .pill { padding: 3px 12px; border-radius: 999px; font-size: 13px; font-weight: 600;
          white-space: nowrap; }
  .pill.ok, .pill.recovered { background: color-mix(in srgb, var(--ok) 16%, transparent); color: var(--ok); }
  .pill.failing, .pill.critical { background: color-mix(in srgb, var(--fail) 16%, transparent); color: var(--fail); }
  .pill.warning { background: color-mix(in srgb, var(--warn) 16%, transparent); color: var(--warn); }
  nav { display: flex; gap: 4px; padding: 0 18px; border-bottom: 1px solid var(--line);
        flex-wrap: wrap; }
  nav button { background: none; border: none; color: var(--muted); cursor: pointer;
               padding: 12px 14px; font-size: 14px; border-bottom: 2px solid transparent; }
  nav button:hover { color: var(--ink); }
  nav button.active { color: var(--ink); border-bottom-color: var(--accent); }
  main { padding: 22px 24px; max-width: 1080px; margin: 0 auto; }
  .view { display: none; }
  .view.active { display: block; }
  h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .06em;
       color: var(--muted); margin: 0 0 12px; }
  .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
           gap: 12px; margin-bottom: 26px; }
  .card { background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
          padding: 14px 16px; }
  .card .top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .card .name { font-weight: 600; }
  .card .detail { color: var(--muted); font-size: 13px; margin-top: 6px;
                  overflow: hidden; text-overflow: ellipsis; }
  .card .meta { color: var(--muted); font-size: 12px; margin-top: 8px; }
  .kpis { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 24px; }
  .kpi { background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
         padding: 12px 18px; min-width: 130px; }
  .kpi .v { font-size: 26px; font-weight: 700; }
  .kpi .l { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .05em; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line);
           font-size: 14px; vertical-align: middle; }
  th { color: var(--muted); font-weight: 500; }
  .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 8px; }
  .dot.ok { background: var(--ok); } .dot.failing { background: var(--fail); }
  .muted { color: var(--muted); }
  .strip { display: inline-flex; gap: 2px; align-items: flex-end; }
  .strip i { width: 5px; height: 16px; border-radius: 1px; background: var(--ok); opacity: .85; }
  .strip i.bad { background: var(--fail); }
  .feed div { padding: 9px 12px; border-bottom: 1px solid var(--line); }
  .mono { font-variant-numeric: tabular-nums; }
  footer { color: var(--muted); font-size: 12px; padding: 18px 24px; border-top: 1px solid var(--line); }
  .empty { color: var(--muted); padding: 14px 0; }
</style>
</head>
<body>
<header>
  <span class="logo" id="logo"></span>
  <div>
    <h1 id="brand">__BRAND__</h1>
    <div class="tagline" id="tagline"></div>
  </div>
  <span class="grow"></span>
  <span id="overall" class="pill ok">loading</span>
  <span class="muted" id="updated"></span>
</header>
<nav>
  <button data-view="overview" class="active">Overview</button>
  <button data-view="monitors">Monitors</button>
  <button data-view="jobs">Jobs</button>
  <button data-view="incidents">Incidents</button>
  <button data-view="reports">Reports</button>
</nav>
<main>
  <section class="view active" id="overview">
    <div class="kpis" id="kpis"></div>
    <h2>Status board</h2>
    <div class="cards" id="board"></div>
    <h2>Recent alerts</h2>
    <div class="feed" id="alerts"></div>
  </section>
  <section class="view" id="monitors">
    <h2>Monitors</h2>
    <table id="montable"><thead><tr><th>Monitor</th><th>State</th>
      <th>24h uptime</th><th>History</th><th>Detail</th></tr></thead><tbody></tbody></table>
  </section>
  <section class="view" id="jobs">
    <h2>Scheduled jobs</h2>
    <table id="jobtable"><thead><tr><th>Job</th><th>State</th>
      <th>Last run</th><th>24h uptime</th><th>Detail</th></tr></thead><tbody></tbody></table>
  </section>
  <section class="view" id="incidents">
    <h2>Incident timeline</h2>
    <table id="inctable"><thead><tr><th>Target</th><th>Started</th>
      <th>Duration</th><th>State</th><th>Detail</th></tr></thead><tbody></tbody></table>
  </section>
  <section class="view" id="reports">
    <h2>Uptime and SLA</h2>
    <table id="reptable"><thead><tr><th>Target</th></tr></thead><tbody></tbody></table>
  </section>
</main>
<footer id="footer"></footer>
<script>
const BOOT = __BOOT__;
function applyTheme(t){
  const r = document.documentElement.style;
  if(t.ok_color) r.setProperty('--ok', t.ok_color);
  if(t.fail_color) r.setProperty('--fail', t.fail_color);
  if(t.warn_color) r.setProperty('--warn', t.warn_color);
  if(t.accent) r.setProperty('--accent', t.accent);
  document.getElementById('brand').textContent = BOOT.brand;
  document.title = BOOT.brand + ' status';
  document.getElementById('tagline').textContent = t.tagline || '';
  document.getElementById('footer').textContent = t.footer || '';
  const logo = document.getElementById('logo');
  if(t.logo && /^https?:\\/\\//.test(t.logo)){ logo.innerHTML = '<img src="'+t.logo+'" alt="">'; }
  else if(t.logo){ logo.textContent = t.logo; }
}
applyTheme(BOOT.theme || {});

let view = 'overview';
document.querySelectorAll('nav button').forEach(b=>{
  b.onclick = ()=>{
    view = b.dataset.view;
    document.querySelectorAll('nav button').forEach(x=>x.classList.toggle('active', x===b));
    document.querySelectorAll('.view').forEach(s=>s.classList.toggle('active', s.id===view));
    refresh();
  };
});

function ago(s){ if(s==null) return 'never';
  if(s<60) return s+'s ago'; if(s<3600) return Math.floor(s/60)+'m ago';
  if(s<86400) return Math.floor(s/3600)+'h ago'; return Math.floor(s/86400)+'d ago'; }
function dur(s){ if(s<60) return s+'s'; if(s<3600) return Math.floor(s/60)+'m '+(s%60)+'s';
  if(s<86400) return Math.floor(s/3600)+'h '+Math.floor((s%3600)/60)+'m';
  return Math.floor(s/86400)+'d '+Math.floor((s%86400)/3600)+'h'; }
function pct(v){ return (v==null?'--':Number(v).toFixed(2)+'%'); }
function strip(hist){ if(!hist || !hist.length) return '<span class="muted">no data</span>';
  return '<span class="strip">'+hist.map(o=>'<i class="'+(o?'':'bad')+'"></i>').join('')+'</span>'; }
function esc(s){ return (s==null?'':String(s)).replace(/[&<>]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
async function getJSON(p){ const r = await fetch(p); if(!r.ok) throw new Error(r.status); return r.json(); }

async function renderStatus(){
  const d = await getJSON('/api/status');
  const o = document.getElementById('overall');
  o.textContent = d.overall==='ok' ? 'all systems normal' : d.failing_count+' failing';
  o.className = 'pill '+d.overall;
  document.getElementById('updated').textContent =
    'updated '+new Date(d.generated_at*1000).toLocaleTimeString();

  const kpis = [
    ['Overall', d.overall==='ok'?'OK':'DOWN'],
    ['Failing now', d.failing_count],
    ['Open incidents', d.open_incidents],
    ['Monitors', d.monitors.length],
    ['Jobs', d.jobs.length],
  ];
  document.getElementById('kpis').innerHTML = kpis.map(k=>
    '<div class="kpi"><div class="v">'+k[1]+'</div><div class="l">'+k[0]+'</div></div>').join('');

  const items = d.monitors.map(m=>({...m, kind:'monitor'})).concat(d.jobs.map(j=>({...j, kind:'job'})));
  document.getElementById('board').innerHTML = items.length ? items.map(it=>
    '<div class="card"><div class="top"><span class="name"><span class="dot '+it.status+'"></span>'+
    esc(it.name)+'</span><span class="pill '+it.status+'">'+it.status+'</span></div>'+
    '<div class="detail">'+esc(it.detail||'')+'</div>'+
    '<div class="meta">'+it.kind+(it.uptime_24h!=null?' &middot; '+pct(it.uptime_24h)+' 24h':'')+'</div></div>'
  ).join('') : '<div class="empty">Nothing configured yet.</div>';

  document.getElementById('alerts').innerHTML = d.alerts.length ? d.alerts.map(a=>
    '<div><span class="pill '+a.severity+'">'+a.severity+'</span> <strong>'+esc(a.title)+
    '</strong> <span class="muted">'+ago(a.ago_seconds)+'</span><br>'+
    '<span class="muted">'+esc(a.detail)+'</span></div>').join('')
    : '<div class="empty">No alerts yet.</div>';

  document.querySelector('#montable tbody').innerHTML = d.monitors.length ? d.monitors.map(m=>
    '<tr><td><span class="dot '+m.status+'"></span>'+esc(m.name)+'</td><td>'+m.status+
    '</td><td class="mono">'+pct(m.uptime_24h)+'</td><td>'+strip(m.history)+
    '</td><td class="muted">'+esc(m.detail||'')+'</td></tr>').join('')
    : '<tr><td class="empty" colspan="5">No monitors yet.</td></tr>';

  document.querySelector('#jobtable tbody').innerHTML = d.jobs.length ? d.jobs.map(j=>
    '<tr><td><span class="dot '+j.status+'"></span>'+esc(j.name)+'</td><td>'+j.status+
    '</td><td class="muted">'+ago(j.last_run)+'</td><td class="mono">'+pct(j.uptime_24h)+
    '</td><td class="muted">'+esc(j.detail||'')+'</td></tr>').join('')
    : '<tr><td class="empty" colspan="5">No jobs yet.</td></tr>';
}

async function renderIncidents(){
  const d = await getJSON('/api/incidents');
  document.querySelector('#inctable tbody').innerHTML = d.incidents.length ? d.incidents.map(i=>
    '<tr><td><span class="dot '+(i.ongoing?'failing':'ok')+'"></span>'+esc(i.kind)+':'+esc(i.name)+
    '</td><td class="muted">'+new Date(i.started_at*1000).toLocaleString()+'</td><td class="mono">'+
    dur(i.duration_seconds)+'</td><td>'+(i.ongoing?'<span class="pill failing">ongoing</span>':
    '<span class="pill ok">resolved</span>')+'</td><td class="muted">'+esc(i.detail||'')+'</td></tr>'
  ).join('') : '<tr><td class="empty" colspan="5">No incidents recorded.</td></tr>';
}

async function renderReport(){
  const d = await getJSON('/api/report');
  const head = ['<th>Target</th>'].concat(d.windows.map(w=>'<th>'+w+' uptime</th>'))
    .concat(['<th>Incidents (longest window)</th>']).join('');
  document.querySelector('#reptable thead').innerHTML = '<tr>'+head+'</tr>';
  const longest = d.windows[d.windows.length-1];
  document.querySelector('#reptable tbody').innerHTML = d.targets.length ? d.targets.map(t=>{
    const cells = d.windows.map(w=>'<td class="mono">'+pct(t.windows[w].uptime_pct)+'</td>').join('');
    return '<tr><td>'+esc(t.kind)+':'+esc(t.name)+(t.ongoing?' <span class="pill failing">down</span>':'')+
      '</td>'+cells+'<td class="mono">'+t.windows[longest].incidents+'</td></tr>';
  }).join('') : '<tr><td class="empty" colspan="9">No data yet.</td></tr>';
}

async function refresh(){
  try{
    if(view==='incidents') await renderIncidents();
    else if(view==='reports') await renderReport();
    else await renderStatus();
  }catch(e){ /* keep trying on the next tick */ }
}
refresh(); setInterval(refresh, 3000);
</script>
</body>
</html>"""
