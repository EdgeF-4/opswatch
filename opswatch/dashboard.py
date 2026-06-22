"""Status dashboard.

A single self-contained page served from the standard library, no front-end
build, no CDN, no external assets. It is the thing a buyer looks at: jobs and
their last run, monitors and their current state, and the live alert feed. The
page polls /api/status every few seconds so a failure shows up on screen within
one monitor tick.

Bind it to 127.0.0.1 and put a reverse proxy with TLS and auth in front (see
deploy/Caddyfile.example). It is read-only and exposes no controls.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("opswatch.dashboard")


def _build_status(store, brand: str) -> dict:
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
        })
    monitors = [{
        "name": st["name"],
        "status": st["status"],
        "detail": st["detail"],
        "since_seconds": int(now - st["since"]),
    } for st in store.all_states("monitor")]
    alerts = [{
        "source": a["source"], "severity": a["severity"],
        "title": a["title"], "detail": a["detail"],
        "ago_seconds": int(now - a["created_at"]),
    } for a in store.recent_alerts(25)]

    failing = sum(1 for j in jobs if j["status"] == "failing")
    failing += sum(1 for m in monitors if m["status"] == "failing")
    return {
        "brand": brand,
        "overall": "failing" if failing else "ok",
        "failing_count": failing,
        "generated_at": now,
        "jobs": jobs,
        "monitors": monitors,
        "alerts": alerts,
    }


def _make_handler(store, brand: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence default request logging
            pass

        def _send(self, code, body, content_type):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/api/status"):
                body = json.dumps(_build_status(store, brand)).encode("utf-8")
                self._send(200, body, "application/json")
            elif self.path in ("/", "/index.html"):
                self._send(200, PAGE.replace("__BRAND__", brand).encode("utf-8"),
                           "text/html; charset=utf-8")
            elif self.path == "/healthz":
                self._send(200, b"ok", "text/plain")
            else:
                self._send(404, b"not found", "text/plain")

    return Handler


def start_dashboard(host: str, port: int, store, brand: str,
                    stop: threading.Event) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), _make_handler(store, brand))
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
  :root { color-scheme: dark; }
  body { margin: 0; background: #0d1117; color: #e6edf3;
         font: 15px/1.5 -apple-system, Segoe UI, Roboto, sans-serif; }
  header { padding: 18px 24px; border-bottom: 1px solid #21262d;
           display: flex; align-items: center; gap: 14px; }
  header h1 { font-size: 18px; margin: 0; font-weight: 600; }
  .pill { padding: 3px 12px; border-radius: 999px; font-size: 13px; font-weight: 600; }
  .ok { background: #122a1d; color: #3fb950; }
  .failing { background: #2d1417; color: #f85149; }
  .recovered { background: #122a1d; color: #3fb950; }
  .critical { background: #2d1417; color: #f85149; }
  main { padding: 24px; max-width: 1000px; margin: 0 auto; }
  section { margin-bottom: 30px; }
  h2 { font-size: 14px; text-transform: uppercase; letter-spacing: .06em;
       color: #8b949e; margin: 0 0 10px; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 9px 12px; border-bottom: 1px solid #21262d;
           font-size: 14px; vertical-align: top; }
  th { color: #8b949e; font-weight: 500; }
  .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%;
         margin-right: 7px; }
  .dot.ok { background: #3fb950; }
  .dot.failing { background: #f85149; }
  .muted { color: #8b949e; }
  .feed div { padding: 8px 12px; border-bottom: 1px solid #21262d; }
  footer { color: #8b949e; font-size: 12px; padding: 16px 24px; }
</style>
</head>
<body>
<header>
  <h1>__BRAND__</h1>
  <span id="overall" class="pill ok">loading</span>
  <span class="muted" id="updated"></span>
</header>
<main>
  <section><h2>Jobs</h2><table id="jobs"><tbody></tbody></table></section>
  <section><h2>Monitors</h2><table id="monitors"><tbody></tbody></table></section>
  <section><h2>Recent alerts</h2><div class="feed" id="alerts"></div></section>
</main>
<footer>Self-hosted ops monitoring. Auto-refreshes every 3 seconds.</footer>
<script>
function ago(s){ if(s==null) return 'never';
  if(s<60) return s+'s ago'; if(s<3600) return Math.floor(s/60)+'m ago';
  return Math.floor(s/3600)+'h ago'; }
function row(name, status, detail, extra){
  return `<tr><td><span class="dot ${status}"></span>${name}</td>`+
         `<td>${status}</td><td class="muted">${detail||''}</td>`+
         `<td class="muted">${extra||''}</td></tr>`; }
async function tick(){
  try{
    const r = await fetch('/api/status'); const d = await r.json();
    const o = document.getElementById('overall');
    o.textContent = d.overall==='ok' ? 'all systems normal' : d.failing_count+' failing';
    o.className = 'pill '+d.overall;
    document.getElementById('updated').textContent =
      'updated '+new Date(d.generated_at*1000).toLocaleTimeString();
    document.querySelector('#jobs tbody').innerHTML =
      d.jobs.length ? d.jobs.map(j=>row(j.name,j.status,j.detail,'ran '+ago(j.last_run))).join('')
                    : '<tr><td class="muted">no jobs yet</td></tr>';
    document.querySelector('#monitors tbody').innerHTML =
      d.monitors.length ? d.monitors.map(m=>row(m.name,m.status,m.detail,'for '+ago(m.since_seconds))).join('')
                        : '<tr><td class="muted">no monitors yet</td></tr>';
    document.getElementById('alerts').innerHTML =
      d.alerts.length ? d.alerts.map(a=>
        `<div><span class="pill ${a.severity}">${a.severity}</span> `+
        `<strong>${a.title}</strong> <span class="muted">${ago(a.ago_seconds)}</span>`+
        `<br><span class="muted">${a.detail}</span></div>`).join('')
      : '<div class="muted">no alerts yet</div>';
  }catch(e){ /* dashboard keeps trying */ }
}
tick(); setInterval(tick, 3000);
</script>
</body>
</html>"""
