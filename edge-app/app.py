"""Test workload deployed to edge devices.

Deliberately tiny and dependency-free: what matters is that each image tag is
a distinct version exposing a health endpoint the agent can check.

Environment:
    APP_VERSION  version reported by /health (baked in at build time)
    APP_BROKEN   "true" makes /health return 500, to exercise rollback
    APP_PORT     listen port (default 8080)
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import FrameType

VERSION = os.environ.get("APP_VERSION", "0.0.0")
BROKEN = os.environ.get("APP_BROKEN", "false").lower() == "true"
PORT = int(os.environ.get("APP_PORT", "8080"))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (name required by BaseHTTPRequestHandler)
        if self.path == "/health":
            if BROKEN:
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"status": "error", "version": VERSION})
            else:
                self._send(HTTPStatus.OK, {"status": "ok", "version": VERSION})
        elif self.path == "/":
            self._send(HTTPStatus.OK, {"app": "my-edge-app", "version": VERSION})
        else:
            self._send(HTTPStatus.NOT_FOUND, {"detail": "not found"})

    def _send(self, status: HTTPStatus, body: dict[str, str]) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        log = {"message": format % args, "version": VERSION}
        sys.stderr.write(json.dumps(log) + "\n")


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)  # noqa: S104

    def shutdown(_signum: int, _frame: FrameType | None) -> None:
        # As PID 1 in a container, Python ignores SIGTERM unless handled, which
        # would make every `docker stop` wait out the full timeout.
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    sys.stderr.write(json.dumps({"message": "started", "version": VERSION, "port": PORT}) + "\n")
    server.serve_forever()


if __name__ == "__main__":
    main()
