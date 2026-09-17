from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .audio import make_mock_wav


@dataclass(frozen=True)
class EngineRequest:
    text: str
    reference: Path
    reference_text: str
    output: Path
    seed: int
    language: str = "ko"


class EngineError(RuntimeError):
    pass


@dataclass
class WorkerHandle:
    process: subprocess.Popen
    endpoint: str
    health_url: str
    log_path: Path
    log_stream: Any


_worker_lock = threading.Lock()
_workers: dict[str, WorkerHandle] = {}


def _json_request(url: str, payload: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
    content = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=content,
        headers={"Content-Type": "application/json"} if content is not None else {},
        method="POST" if content is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise EngineError(f"Worker HTTP {error.code}: {body}") from error


def _worker_health(health_url: str) -> dict[str, Any] | None:
    try:
        return _json_request(health_url, None, 1)
    except Exception:
        return None


def _worker_matches(health: dict[str, Any] | None, expected_model: str | None) -> bool:
    if not health or health.get("status") != "ready":
        return False
    return not expected_model or health.get("model") == expected_model


def _ensure_worker(config: dict[str, Any], environment: dict[str, str]) -> None:
    worker = config["worker"]
    endpoint = str(worker["endpoint"])
    health_url = str(worker["health_url"])
    expected_model = worker.get("model")
    if _worker_matches(_worker_health(health_url), expected_model):
        return
    with _worker_lock:
        health = _worker_health(health_url)
        if _worker_matches(health, expected_model):
            return
        previous = _workers.get(endpoint)
        if health and health.get("status") == "ready":
            try:
                _json_request(endpoint.rsplit("/", 1)[0] + "/shutdown", {}, 3)
            except Exception:
                pass
            deadline = time.time() + 20
            while time.time() < deadline and _worker_health(health_url):
                time.sleep(0.25)
        if previous and previous.process.poll() is None:
            previous.process.terminate()
            previous.process.wait(timeout=10)
        if previous and not previous.log_stream.closed:
            previous.log_stream.close()
        replacements = {"parent_pid": str(os.getpid())}
        command = [str(part).format_map(replacements) for part in worker["command"]]
        cwd = worker.get("cwd") or config.get("cwd") or None
        log_path = Path(worker.get("log_path") or "qwen-worker.log").resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_stream = log_path.open("a", encoding="utf-8", buffering=1)
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            text=True,
            shell=False,
            creationflags=creationflags,
        )
        handle = WorkerHandle(process, endpoint, health_url, log_path, log_stream)
        _workers[endpoint] = handle
        deadline = time.time() + float(worker.get("startup_timeout_seconds", 240))
        while time.time() < deadline:
            if _worker_matches(_worker_health(health_url), expected_model):
                return
            if process.poll() is not None:
                log_stream.flush()
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
                log_stream.close()
                raise EngineError(f"TTS worker exited during startup: {tail}")
            time.sleep(0.5)
        process.terminate()
        process.wait(timeout=10)
        log_stream.close()
        raise EngineError("TTS worker did not become ready before the startup timeout")


def shutdown_workers() -> None:
    with _worker_lock:
        handles = list(_workers.values())
        _workers.clear()
    for handle in handles:
        if handle.process.poll() is None:
            try:
                _json_request(handle.endpoint.rsplit("/", 1)[0] + "/shutdown", {}, 3)
                handle.process.wait(timeout=15)
            except Exception:
                handle.process.terminate()
                try:
                    handle.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    handle.process.kill()
        handle.log_stream.close()


def run_engine(engine: dict[str, Any], request: EngineRequest) -> dict[str, Any]:
    if engine["kind"] == "mock":
        engine_offset = sum(str(engine.get("slug", "mock")).encode("utf-8"))
        make_mock_wav(request.output, request.text, request.seed + engine_offset)
        return {"adapter": "mock", "command": None}
    if engine["kind"] != "command":
        raise EngineError(f"Unsupported engine kind: {engine['kind']}")

    config = json.loads(engine.get("config_json") or "{}")
    environment = os.environ.copy()
    environment.update({str(k): str(v) for k, v in config.get("environment", {}).items()})
    if config.get("worker"):
        _ensure_worker(config, environment)
        payload = {
            "text": request.text,
            "reference": str(request.reference),
            "reference_text": request.reference_text,
            "output": str(request.output),
            "seed": request.seed,
            "language": request.language,
            "generation": config.get("generation", {}),
        }
        request.output.parent.mkdir(parents=True, exist_ok=True)
        result = _json_request(
            str(config["worker"]["endpoint"]), payload, int(config.get("timeout_seconds", 7200))
        )
        if not request.output.exists() or request.output.stat().st_size == 0:
            raise EngineError("TTS worker completed without producing the requested output")
        return {"adapter": "persistent-http-worker", "worker": result}

    command_template = config.get("command")
    if not isinstance(command_template, list) or not command_template:
        raise EngineError("Command engine requires a non-empty command array")
    replacements = {
        "text": request.text,
        "reference": str(request.reference),
        "reference_text": request.reference_text,
        "output": str(request.output),
        "seed": str(request.seed),
        "language": request.language,
    }
    command = [str(part).format_map(replacements) for part in command_template]
    timeout = int(config.get("timeout_seconds", 7200))
    cwd = config.get("cwd") or None
    request.output.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
        shell=False,
    )
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "")[-4000:]
        raise EngineError(f"Engine exited with {completed.returncode}: {tail}")
    if not request.output.exists() or request.output.stat().st_size == 0:
        raise EngineError("Engine completed without producing the requested output")
    return {
        "adapter": "command",
        "command": command,
        "stdout_tail": completed.stdout[-1000:],
        "stderr_tail": completed.stderr[-1000:],
    }
