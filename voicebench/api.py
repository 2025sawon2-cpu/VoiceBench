from __future__ import annotations

import json
import hashlib
import mimetypes
import os
import re
import secrets
import threading
import time
from base64 import b64decode
from binascii import Error as Base64Error
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .config import Paths
from .db import Database
from .engines import shutdown_workers
from .recordings import ReferenceTranscriber
from .runner import JobRunner
from .service import STUDIO_DEFAULT_SEED, Service


class VoiceBenchServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], paths: Paths):
        paths.ensure()
        self.paths = paths
        self.database = Database(paths.database)
        self.service = Service(paths, self.database)
        self.service.recover_interrupted_jobs()
        self.runner = JobRunner(paths, self.database)
        self.reference_transcriber = ReferenceTranscriber(paths.root)
        self.access_key = os.environ.get("VOICEBENCH_ACCESS_KEY", "")
        self.external_api_key = os.environ.get("VOICEBENCH_EXTERNAL_API_KEY", "")
        self.external_request_times: deque[float] = deque()
        self.external_request_lock = threading.Lock()
        self.external_rate_limit = 10
        self.external_rate_window_seconds = 60
        self.external_max_active = 4
        super().__init__(address, VoiceBenchHandler)

    def ui_version(self) -> str:
        digest = hashlib.sha256()
        for path in sorted(self.paths.static.glob("*")):
            if path.suffix in {".html", ".js", ".css"}:
                digest.update(path.name.encode())
                digest.update(path.read_bytes())
        return digest.hexdigest()[:16]

    def start(self) -> None:
        self.runner.start()

    def server_close(self) -> None:
        self.runner.stop()
        if self.runner.is_alive():
            self.runner.join(timeout=5)
        shutdown_workers()
        super().server_close()


class VoiceBenchHandler(BaseHTTPRequestHandler):
    server: VoiceBenchServer

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[VoiceBench] {self.address_string()} {format % args}")

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            if path.startswith("/v1/"):
                if not self.is_external_api_authorized():
                    return self.request_bearer_authentication()
                return self.handle_external_get(path)
            if not self.is_authorized():
                return self.request_authentication()
            query = parse_qs(parsed.query)
            if path == "/api/state":
                return self.send_json(self.server.service.state())
            if path == "/api/studio":
                return self.send_json({**self.server.service.studio_state(), "ui_version": self.server.ui_version()})
            match = re.fullmatch(r"/api/voice-catalog/([a-z0-9_-]+)/preview", path)
            if match:
                audio = self.server.service.voice_preview_path(match.group(1))
                return self.send_file(audio, mimetypes.guess_type(str(audio))[0] or "audio/wav")
            match = re.fullmatch(r"/api/voices/(\d+)/audio", path)
            if match:
                voice = self.server.service.library_voice(int(match.group(1)))
                return self.send_file(Path(voice["reference_path"]), "audio/wav")
            match = re.fullmatch(r"/api/projects/(\d+)", path)
            if match:
                return self.send_json(self.server.service.get_project(int(match.group(1))))
            if path == "/api/runs":
                return self.send_json(self.server.service.runs(self.required_int(query, "project_id")))
            if path == "/api/comparisons/next":
                return self.send_json(
                    self.server.service.next_comparison(self.required_int(query, "project_id"))
                )
            if path == "/api/comparisons/summary":
                return self.send_json(
                    self.server.service.comparison_summary(self.required_int(query, "project_id"))
                )
            match = re.fullmatch(r"/api/audio/(\d+)", path)
            if match:
                return self.send_file(self.server.service.audio_path(int(match.group(1))), "audio/wav")
            match = re.fullmatch(r"/api/studio/audio/(\d+)", path)
            if match:
                return self.send_file(
                    self.server.service.studio_audio_path(int(match.group(1))), "audio/wav"
                )
            match = re.fullmatch(r"/api/studio/(\d+)/chapters/(\d+)/audio", path)
            if match:
                return self.send_file(
                    self.server.service.studio_chapter_audio_path(
                        int(match.group(1)), int(match.group(2))
                    ),
                    "audio/wav",
                )
            return self.serve_static(path)
        except Exception as error:
            self.send_error_json(error)

    def do_HEAD(self) -> None:
        try:
            path = urlparse(self.path).path
            if path.startswith("/v1/"):
                if not self.is_external_api_authorized():
                    return self.request_bearer_authentication(head_only=True)
                match = re.fullmatch(r"/v1/tts/(\d+)/audio", path)
                if match:
                    return self.send_file(
                        self.server.service.external_tts_audio_path(int(match.group(1))),
                        "audio/wav",
                        head_only=True,
                    )
                match = re.fullmatch(r"/v1/tts/(\d+)/chapters/(\d+)/audio", path)
                if match:
                    return self.send_file(
                        self.server.service.external_tts_chapter_audio_path(
                            int(match.group(1)), int(match.group(2))
                        ),
                        "audio/wav",
                        head_only=True,
                    )
                return self.send_json({}, status=HTTPStatus.NOT_FOUND, head_only=True)
            if not self.is_authorized():
                return self.request_authentication(head_only=True)
            match = re.fullmatch(r"/api/audio/(\d+)", path)
            if match:
                return self.send_file(
                    self.server.service.audio_path(int(match.group(1))),
                    "audio/wav",
                    head_only=True,
                )
            match = re.fullmatch(r"/api/studio/audio/(\d+)", path)
            if match:
                return self.send_file(
                    self.server.service.studio_audio_path(int(match.group(1))),
                    "audio/wav",
                    head_only=True,
                )
            match = re.fullmatch(r"/api/studio/(\d+)/chapters/(\d+)/audio", path)
            if match:
                return self.send_file(
                    self.server.service.studio_chapter_audio_path(
                        int(match.group(1)), int(match.group(2))
                    ),
                    "audio/wav",
                    head_only=True,
                )
            return self.serve_static(path, head_only=True)
        except Exception as error:
            self.send_error_json(error, head_only=True)

    def do_POST(self) -> None:
        try:
            path = urlparse(self.path).path
            if path.startswith("/v1/"):
                if not self.is_external_api_authorized():
                    return self.request_bearer_authentication()
                return self.handle_external_post(path)
            if not self.is_authorized():
                return self.request_authentication()
            if path == "/api/projects":
                payload = self.read_json()
                return self.send_json(
                    self.server.service.create_project(payload.get("name", ""), payload.get("description", "")),
                    status=HTTPStatus.CREATED,
                )
            if path == "/api/studio/preview":
                payload = self.read_json()
                return self.send_json(
                    self.server.service.preview_studio_script(
                        payload.get("text", ""), payload.get("engine_slug")
                    )
                )
            if path == "/api/studio/synthesize":
                payload = self.read_json()
                return self.send_json(
                    self.server.service.create_studio_request(
                        payload.get("text", ""),
                        int(payload.get("seed", STUDIO_DEFAULT_SEED)),
                        payload.get("engine_slug"),
                        quality_mode=payload.get("quality_mode", "long_form_hq"),
                        voice_project_id=payload.get("voice_project_id"),
                        voice_id=payload.get("voice_id"),
                        generation=payload.get("generation"),
                    ),
                    status=HTTPStatus.CREATED,
                )
            if path == "/api/voices/transcribe":
                transcript = self.server.reference_transcriber.transcribe(self.read_body(maximum=12 * 1024 * 1024))
                return self.send_json({"transcript": transcript})
            if path == "/api/voices":
                return self.send_json(self.server.service.create_library_voice(
                    unquote(self.headers.get("X-Voice-Name", "")),
                    unquote(self.headers.get("X-Reference-Text", "")),
                    self.read_body(maximum=12 * 1024 * 1024),
                ), status=HTTPStatus.CREATED)
            match = re.fullmatch(r"/api/projects/(\d+)/reference", path)
            if match:
                content = self.read_body(maximum=512 * 1024 * 1024)
                filename = unquote(self.headers.get("X-Filename", "reference.wav"))
                reference_text = unquote(self.headers.get("X-Reference-Text", ""))
                return self.send_json(
                    self.server.service.upload_reference(
                        int(match.group(1)), filename, content, reference_text
                    )
                )
            match = re.fullmatch(r"/api/projects/(\d+)/reference-text", path)
            if match:
                payload = self.read_json()
                return self.send_json(
                    self.server.service.update_reference_text(
                        int(match.group(1)), payload.get("reference_text", "")
                    )
                )
            match = re.fullmatch(r"/api/projects/(\d+)/corpus/default", path)
            if match:
                return self.send_json(self.server.service.import_default_corpus(int(match.group(1))))
            match = re.fullmatch(r"/api/projects/(\d+)/prompts", path)
            if match:
                payload = self.read_json()
                return self.send_json(
                    self.server.service.add_prompt(
                        int(match.group(1)), payload.get("text", ""), payload.get("category", "custom")
                    ),
                    status=HTTPStatus.CREATED,
                )
            if path == "/api/engines":
                return self.send_json(
                    self.server.service.upsert_engine(self.read_json()), status=HTTPStatus.CREATED
                )
            if path == "/api/benchmarks":
                payload = self.read_json()
                return self.send_json(
                    self.server.service.queue_benchmark(
                        int(payload["project_id"]),
                        [int(value) for value in payload.get("engine_ids", [])],
                        [int(value) for value in payload.get("seeds", [42])],
                    ),
                    status=HTTPStatus.CREATED,
                )
            match = re.fullmatch(r"/api/comparisons/(\d+)", path)
            if match:
                payload = self.read_json()
                return self.send_json(
                    self.server.service.rate_comparison(
                        int(match.group(1)), payload.get("winner", ""), payload.get("notes", "")
                    )
                )
            self.send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
        except Exception as error:
            self.send_error_json(error)

    def do_DELETE(self) -> None:
        try:
            path = urlparse(self.path).path
            if not self.is_authorized():
                return self.request_authentication()
            match = re.fullmatch(r"/api/studio/(\d+)", path)
            if match:
                return self.send_json(
                    self.server.service.cancel_studio_request(int(match.group(1)))
                )
            self.send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
        except Exception as error:
            self.send_error_json(error)

    def handle_external_get(self, path: str) -> None:
        if path == '/v1/voices':
            return self.send_json(self.server.service.external_voice_catalog())
        voice_match = re.fullmatch(r'/v1/voices/([a-z0-9_-]+)/preview',path)
        if voice_match:
            self.server.service.external_voice(voice_match.group(1))
            audio = self.server.service.voice_preview_path(voice_match.group(1))
            return self.send_file(audio,mimetypes.guess_type(str(audio))[0] or 'audio/wav')
        if path == "/v1/health":
            return self.send_json(
                {
                    "status": "ok",
                    "service": "VoiceBench External TTS API",
                    "active_requests": self.server.service.active_external_tts_count(),
                    **self.server.service.synthesis_readiness(),
                }
            )
        match = re.fullmatch(r"/v1/tts/(\d+)/audio", path)
        if match:
            request_id = int(match.group(1))
            self.send_file(
                self.server.service.external_tts_audio_path(request_id), "audio/wav"
            )
            if not self.headers.get("Range"):
                self.server.service.record_external_tts_download(request_id)
            return
        match = re.fullmatch(r"/v1/tts/(\d+)/chapters/(\d+)/audio", path)
        if match:
            return self.send_file(
                self.server.service.external_tts_chapter_audio_path(
                    int(match.group(1)), int(match.group(2))
                ),
                "audio/wav",
            )
        match = re.fullmatch(r"/v1/tts/(\d+)", path)
        if match:
            return self.send_json(
                self.server.service.external_tts_request(int(match.group(1)))
            )
        self.send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

    def handle_external_post(self, path: str) -> None:
        if path != "/v1/tts":
            return self.send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
        payload = self.read_json()
        text = payload.get("text", "")
        if not isinstance(text, str):
            raise ValueError("text must be a string.")
        generation_key = payload.get('generation_key', '')
        voice_id = payload.get('voice_id')
        now = time.monotonic()
        with self.server.external_request_lock:
            reusable = self.server.service.reusable_external_tts_request(text, generation_key, voice_id)
            if reusable:
                reusable["reused"] = True
                return self.send_json(reusable, status=HTTPStatus.OK)
            cutoff = now - self.server.external_rate_window_seconds
            while (
                self.server.external_request_times
                and self.server.external_request_times[0] < cutoff
            ):
                self.server.external_request_times.popleft()
            if len(self.server.external_request_times) >= self.server.external_rate_limit:
                return self.send_json(
                    {"error": "Rate limit exceeded. Try again in one minute."},
                    status=HTTPStatus.TOO_MANY_REQUESTS,
                )
            if (
                self.server.service.active_external_tts_count()
                >= self.server.external_max_active
            ):
                return self.send_json(
                    {"error": "The TTS queue is full. Try again later."},
                    status=HTTPStatus.TOO_MANY_REQUESTS,
                )
            request = self.server.service.create_external_tts_request(text, generation_key, voice_id)
            self.server.external_request_times.append(now)
        response = self.server.service.external_tts_request(request["id"])
        response["reused"] = False
        return self.send_json(response, status=HTTPStatus.ACCEPTED)

    def required_int(self, query: dict[str, list[str]], key: str) -> int:
        if key not in query or not query[key]:
            raise ValueError(f"Missing query parameter: {key}")
        return int(query[key][0])

    def is_authorized(self) -> bool:
        access_key = self.server.access_key
        if not access_key:
            return True
        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Basic "):
            return False
        try:
            credentials = b64decode(authorization.removeprefix("Basic ").strip()).decode("utf-8")
            username, supplied = credentials.split(":", 1)
        except (Base64Error, ValueError, UnicodeDecodeError):
            return False
        return secrets.compare_digest(username, "voice") and secrets.compare_digest(supplied, access_key)

    def is_external_api_authorized(self) -> bool:
        api_key = self.server.external_api_key
        if not api_key:
            return False
        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            return False
        supplied = authorization.removeprefix("Bearer ").strip()
        return secrets.compare_digest(supplied, api_key)

    def request_authentication(self, head_only: bool = False) -> None:
        content = b'{"error":"Authentication required"}'
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="VoiceBench", charset="UTF-8"')
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head_only:
            self.wfile.write(content)

    def request_bearer_authentication(self, head_only: bool = False) -> None:
        message = (
            "External API is unavailable."
            if not self.server.external_api_key
            else "A valid Bearer API key is required."
        )
        status = (
            HTTPStatus.SERVICE_UNAVAILABLE
            if not self.server.external_api_key
            else HTTPStatus.UNAUTHORIZED
        )
        content = json.dumps({"error": message}).encode("utf-8")
        self.send_response(status)
        if status == HTTPStatus.UNAUTHORIZED:
            self.send_header("WWW-Authenticate", 'Bearer realm="VoiceBench External TTS"')
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head_only:
            self.wfile.write(content)

    def read_body(self, maximum: int = 2 * 1024 * 1024) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > maximum:
            raise ValueError("요청 본문 크기가 올바르지 않습니다.")
        return self.rfile.read(length)

    def read_json(self, maximum: int = 2 * 1024 * 1024) -> dict[str, Any]:
        content = self.read_body(maximum=maximum)
        value = json.loads(content.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON 객체가 필요합니다.")
        return value

    def send_json(
        self,
        payload: Any,
        status: int = HTTPStatus.OK,
        head_only: bool = False,
    ) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head_only:
            self.wfile.write(content)

    def send_error_json(self, error: Exception, head_only: bool = False) -> None:
        status = HTTPStatus.NOT_FOUND if isinstance(error, (KeyError, FileNotFoundError)) else HTTPStatus.BAD_REQUEST
        if head_only:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        self.send_json({"error": str(error).strip("'\"")}, status=status)

    def send_file(
        self,
        path: Path,
        content_type: str | None = None,
        head_only: bool = False,
    ) -> None:
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(path)
        file_size = path.stat().st_size
        start = 0
        end = max(0, file_size - 1)
        status = HTTPStatus.OK
        range_header = self.headers.get("Range")
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
            if not match or (not match.group(1) and not match.group(2)):
                return self.send_range_not_satisfiable(file_size)
            if match.group(1):
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else end
            else:
                suffix_length = int(match.group(2))
                if suffix_length <= 0:
                    return self.send_range_not_satisfiable(file_size)
                start = max(0, file_size - suffix_length)
            if start >= file_size or start > end:
                return self.send_range_not_satisfiable(file_size)
            end = min(end, file_size - 1)
            status = HTTPStatus.PARTIAL_CONTENT

        content_length = max(0, end - start + 1) if file_size else 0
        self.send_response(status)
        self.send_header("Content-Type", content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(content_length))
        self.send_header("Accept-Ranges", "bytes")
        if self.server.paths.static.resolve() in path.resolve().parents:
            self.send_header("Cache-Control", "no-cache")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.end_headers()
        if head_only or content_length == 0:
            return
        with path.open("rb") as source:
            source.seek(start)
            remaining = content_length
            while remaining:
                chunk = source.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def send_range_not_satisfiable(self, file_size: int) -> None:
        self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
        self.send_header("Content-Range", f"bytes */{file_size}")
        self.send_header("Content-Length", "0")
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def serve_static(self, request_path: str, head_only: bool = False) -> None:
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        target = (self.server.paths.static / relative).resolve()
        if self.server.paths.static.resolve() not in target.parents and target != self.server.paths.static.resolve():
            raise FileNotFoundError(relative)
        if relative == "index.html":
            content = target.read_text(encoding="utf-8").replace("__VOICEBENCH_UI_VERSION__", self.server.ui_version()).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not head_only:
                self.wfile.write(content)
            return
        self.send_file(target, head_only=head_only)


def run_server(host: str, port: int, paths: Paths | None = None) -> None:
    resolved_paths = paths or Paths.discover()
    server = VoiceBenchServer((host, port), resolved_paths)
    server.start()
    print(f"VoiceBench running at http://{host}:{port}")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nVoiceBench stopping...")
    finally:
        server.shutdown()
        server.server_close()
