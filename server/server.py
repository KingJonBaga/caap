"""Starts the NURTURA server: static site + JSON API.
Run with: py server/server.py  (or double-click start.bat)
Uses port 3000 if it is free, otherwise the next free port, and opens the browser."""
import errno
import os
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # lets the modules import each other

from app import MAX_BODY_BYTES, create_app  # noqa: E402
from db import open_database  # noqa: E402

FIRST_PORT = int(os.environ.get("PORT") or 3000)
TRIES = 10
HOST = "127.0.0.1"


class Handler(BaseHTTPRequestHandler):
    def _dispatch(self, method):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            self._send(413, "application/json; charset=utf-8", b'{"error": "Request body too large"}', {})
            return
        raw_body = self.rfile.read(length) if length else b""
        headers = {key.lower(): value for key, value in self.headers.items()}
        status, content_type, body, extra = self.server.app.handle(method, self.path, headers, raw_body)
        self._send(status, content_type, body, extra)

    def _send(self, status, content_type, body, extra):
        self.send_response(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        for name, value in extra.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("HEAD")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def do_OPTIONS(self):
        self._dispatch("OPTIONS")

    def log_message(self, format, *args):
        pass  # keep the terminal quiet, as before


def is_port_busy(error):
    return error.errno in (errno.EADDRINUSE, 10048) or getattr(error, "winerror", None) == 10048


def main():
    db = open_database()
    app = create_app(db)

    server = None
    port = FIRST_PORT
    for attempt in range(TRIES):
        port = FIRST_PORT + attempt
        try:
            server = HTTPServer((HOST, port), Handler)
            break
        except OSError as error:
            if not is_port_busy(error):
                print(f"Could not start the server: {error}")
                sys.exit(1)
    if server is None:
        print(f"Could not start the server: ports {FIRST_PORT}-{FIRST_PORT + TRIES - 1} are all busy.")
        sys.exit(1)

    server.app = app
    url = f"http://localhost:{port}"
    print(f"NURTURA server running at {url}")
    if port != FIRST_PORT:
        print(f"(Port {FIRST_PORT} was busy, so {port} is in use.)")
    if not os.environ.get("NO_OPEN"):
        webbrowser.open(url)  # opens the default browser
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
