"""Claude Desktop stdio MCP bridge for a VoiceBench server on the local network.

This file deliberately uses only the Python standard library so it can be copied to
the PC that runs Claude Desktop. Configure VOICEBENCH_URL and VOICEBENCH_API_KEY.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_TIMEOUT_SECONDS = 3600
POLL_INTERVAL_SECONDS = 2
ASYNC_SEGMENT_THRESHOLD = 8


def log(message: str) -> None:
    print(f"[VoiceBench MCP] {message}", file=sys.stderr, flush=True)


def required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Set {name} in Claude Desktop MCP configuration.")
    return value.rstrip("/")


def api_request(path: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> tuple[int, bytes]:
    base_url = required_environment("VOICEBENCH_URL")
    api_key = required_environment("VOICEBENCH_API_KEY")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = Request(
        f"{base_url}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()
    except URLError as error:
        raise RuntimeError(f"VoiceBench server connection failed: {error.reason}") from error


def api_json(path: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    status, content = api_request(path, method=method, payload=payload)
    try:
        result = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"VoiceBench returned an invalid response (HTTP {status}).") from error
    if status >= 400:
        raise RuntimeError(str(result.get("error", f"VoiceBench request failed (HTTP {status}).")))
    if not isinstance(result, dict):
        raise RuntimeError("VoiceBench returned an unexpected JSON response.")
    return result


def download_audio(audio_url: str, output_path: str) -> str:
    base_url = required_environment("VOICEBENCH_URL")
    api_key = required_environment("VOICEBENCH_API_KEY")
    target = Path(output_path).expanduser().resolve()
    if not target.is_absolute():
        raise RuntimeError("output_path must be an absolute path on the Claude PC.")
    if target.suffix.lower() != ".wav":
        target = target.with_suffix(".wav")
    target.parent.mkdir(parents=True, exist_ok=True)
    request = Request(
        f"{base_url}{audio_url}",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urlopen(request, timeout=120) as response:
            data = response.read()
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Could not download audio (HTTP {error.code}): {detail}") from error
    target.write_bytes(data)
    return str(target)


def submit_tts(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("text must be a non-empty string.")
    return api_json("/v1/tts", method="POST", payload={"text": text})


def wait_for_tts(request_id: int, timeout_seconds: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    result: dict[str, Any] = {"id": request_id, "status": "queued"}
    while time.monotonic() < deadline:
        result = api_json(f"/v1/tts/{request_id}")
        if result.get("status") == "succeeded":
            return result
        if result.get("status") in {"failed", "rejected", "cancelled"}:
            raise RuntimeError(str(result.get("error") or f"Voice generation {result['status']}."))
        time.sleep(POLL_INTERVAL_SECONDS)
    result["wait_timed_out"] = True
    result["message"] = (
        f"VoiceBench request {request_id} is still running after {timeout_seconds} seconds. "
        "Do not submit the script again; call voicebench_tts_status with this request_id."
    )
    return result


def pending_result(result: dict[str, Any], output_path: str | None = None) -> dict[str, Any]:
    value = dict(result)
    value["async"] = True
    value["message"] = (
        f"VoiceBench request {value['id']} is queued or running. Do not submit the script again. "
        "Use voicebench_tts_status with the same request_id until status is succeeded."
    )
    value["next_tool"] = "voicebench_tts_status"
    value["next_arguments"] = {"request_id": value["id"]}
    if output_path:
        value["requested_output_path"] = output_path
        value["next_arguments"]["output_path"] = output_path
    return value


def tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": "voicebench_tts",
            "description": "Create a Korean voice-cloned WAV. Short scripts may wait for completion; long scripts return one persistent request_id immediately. Never resubmit the same script after receiving an id: use voicebench_tts_status with that id until it succeeds.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Script to synthesize."},
                    "output_path": {"type": "string", "description": "Optional absolute WAV destination on this PC, for example C:\\\\Users\\\\me\\\\Downloads\\\\speech.wav."},
                    "timeout_seconds": {"type": "integer", "default": 3600, "minimum": 10, "maximum": 7200},
                },
                "required": ["text"],
            },
        },
        {
            "name": "voicebench_tts_submit",
            "description": "Queue a VoiceBench TTS request without waiting. The returned request_id is persistent; never resubmit the same script, and use voicebench_tts_status later.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                },
                "required": ["text"],
            },
        },
        {
            "name": "voicebench_tts_status",
            "description": "Get an existing VoiceBench request's status without creating a new request. If complete, optionally download the WAV onto this Claude PC.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "integer"},
                    "output_path": {"type": "string", "description": "Optional absolute WAV destination on this PC."},
                },
                "required": ["request_id"],
            },
        },
    ]


def tool_result(value: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, indent=2)}]}


def handle_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "voicebench_tts_submit":
        submitted = submit_tts(arguments.get("text", ""))
        if submitted.get("status") == "succeeded":
            return tool_result(submitted)
        return tool_result(pending_result(submitted))
    if name == "voicebench_tts_status":
        request_id = arguments.get("request_id")
        if not isinstance(request_id, int):
            raise RuntimeError("request_id must be an integer.")
        result = api_json(f"/v1/tts/{request_id}")
        if result.get("status") == "succeeded" and arguments.get("output_path"):
            result["saved_audio_path"] = download_audio(result["audio_url"], arguments["output_path"])
        elif result.get("status") in {"queued", "running"}:
            result = pending_result(result, arguments.get("output_path"))
        return tool_result(result)
    if name == "voicebench_tts":
        timeout = arguments.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        if not isinstance(timeout, int) or timeout < 10 or timeout > 7200:
            raise RuntimeError("timeout_seconds must be an integer from 10 to 7200.")
        submitted = submit_tts(arguments.get("text", ""))
        if submitted.get("status") == "succeeded":
            if arguments.get("output_path"):
                submitted["saved_audio_path"] = download_audio(
                    submitted["audio_url"], arguments["output_path"]
                )
            return tool_result(submitted)
        if int(submitted.get("segment_count") or 0) >= ASYNC_SEGMENT_THRESHOLD:
            return tool_result(pending_result(submitted, arguments.get("output_path")))
        result = wait_for_tts(int(submitted["id"]), timeout)
        if result.get("status") == "succeeded" and arguments.get("output_path"):
            result["saved_audio_path"] = download_audio(result["audio_url"], arguments["output_path"])
        elif result.get("status") in {"queued", "running"}:
            result = pending_result(result, arguments.get("output_path"))
        return tool_result(result)
    raise RuntimeError(f"Unknown tool: {name}")


def send(message: dict[str, Any]) -> None:
    print(json.dumps(message, ensure_ascii=False), flush=True)


def main() -> None:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                continue
            method = request.get("method")
            request_id = request.get("id")
            if method == "notifications/initialized":
                continue
            if method == "initialize":
                response = {
                    "protocolVersion": request.get("params", {}).get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "voicebench-lan", "version": "1.1.0"},
                }
            elif method == "tools/list":
                response = {"tools": tool_definitions()}
            elif method == "tools/call":
                params = request.get("params", {})
                try:
                    response = handle_tool(params.get("name", ""), params.get("arguments", {}))
                except Exception as error:
                    log(str(error))
                    response = {"content": [{"type": "text", "text": str(error)}], "isError": True}
            else:
                if request_id is not None:
                    send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Method not found: {method}"}})
                continue
            if request_id is not None:
                send({"jsonrpc": "2.0", "id": request_id, "result": response})
        except Exception as error:
            log(f"Protocol error: {error}")
            if isinstance(request, dict) and request.get("id") is not None:
                send({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32603, "message": str(error)}})


if __name__ == "__main__":
    main()
