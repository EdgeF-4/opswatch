"""A throwaway HTTP service for the demo to monitor.

Serves 200 on /health normally, and 503 when a file named DOWN exists in its
working directory. The demo script creates and removes that file to simulate an
outage so you can watch the monitor catch it and then clear. Standard library
only; nothing leaves the machine.

    python3 scripts/demo_target.py 8766
"""

import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DOWN_FLAG = os.path.join(os.getcwd(), "DOWN")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if os.path.exists(DOWN_FLAG):
            self.send_response(503)
            body = b"service unavailable"
        else:
            self.send_response(200)
            body = b"ok"
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
