"""Tiny health endpoint for the paper bot. It does not change strategy."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class HealthState:
    def __init__(self) -> None:
        self._payload: dict[str, Any] = {"ok": True, "ready": False}
        self._lock = threading.Lock()

    def update(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._payload = payload

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._payload)


def serve(state: HealthState, host: str, port: int) -> ThreadingHTTPServer:
    handler = _handler_for(state)

    server = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(target=server.serve_forever, name="health", daemon=True)
    thread.start()
    return server


def _handler_for(state: HealthState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?", 1)[0] != "/health":
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps(state.snapshot()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    return Handler
