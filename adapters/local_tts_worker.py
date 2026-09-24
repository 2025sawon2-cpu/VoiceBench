"""Loopback worker protocol for optional TTS backends (no model dependencies)."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def validate_payload(payload, *, transcript=False):
    if not isinstance(payload, dict):
        raise ValueError("Request must be an object")
    for key in ("text", "reference", "output"):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise ValueError(f"{key} is required")
    if not Path(payload["reference"]).is_file():
        raise ValueError("Reference audio does not exist")
    if transcript and not str(payload.get("reference_text", "")).strip():
        raise ValueError("reference_text is required")
    if payload.get("language", "ko") not in ("ko", "Korean"):
        raise ValueError("This comparison preset supports Korean requests")
    if not isinstance(payload.get("generation", {}), dict):
        raise ValueError("generation must be an object")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)

    def send_json(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/health":
            self.send_json(self.server.backend.health())
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        try:
            if self.path == "/shutdown":
                self.send_json({"status": "stopping"})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            elif self.path == "/synthesize":
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024 * 1024:
                    raise ValueError("Invalid body size")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                with self.server.generation_lock:
                    result = self.server.backend.synthesize(payload)
                self.send_json(result)
            else:
                self.send_json({"error": "Not found"}, 404)
        except Exception as error:
            import traceback
            traceback.print_exc()
            self.send_json({"error": f"{type(error).__name__}: {error}"}, 500)


def serve(backend, port):
    try:
        with ThreadingHTTPServer(("127.0.0.1", port), Handler) as server:
            server.backend = backend
            server.generation_lock = threading.Lock()
            print(f"{backend.health()} on port {port}", flush=True)
            server.serve_forever(poll_interval=0.25)
    finally:
        backend.close()
