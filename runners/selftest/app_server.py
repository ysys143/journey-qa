#!/usr/bin/env python3
"""Invented demo app server for the runner smoke test (runners/selftest/smoke.sh).

    app_server.py PORT [--bind ADDR]

Serves runners/selftest/app/ and three API routes the failed-request checks need:
    /api/ok      200 with a small JSON body
    /api/boom    500 with a small JSON body
    /api/abort   closes the connection without sending a response (the browser reports a failed request)
Nothing else is dynamic. Access logging is off.
"""
import argparse
import json
import socket
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

APP = Path(__file__).resolve().parent / "app"


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _json(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/ok":
            self._json(200, {"ok": True})
        elif path == "/api/boom":
            self._json(500, {"ok": False, "error": "internal"})
        elif path == "/api/abort":
            self.close_connection = True
            try:
                self.request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        else:
            super().do_GET()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port", type=int)
    ap.add_argument("--bind", default="127.0.0.1")
    args = ap.parse_args()
    ThreadingHTTPServer((args.bind, args.port), partial(Handler, directory=str(APP))).serve_forever()


if __name__ == "__main__":
    main()
