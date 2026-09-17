from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def api_request(url: str, api_key: str, payload: dict | None = None) -> tuple[dict, str]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json; charset=utf-8",
            "ngrok-skip-browser-warning": "VoiceBench API client",
        },
        method="POST" if payload is not None else "GET",
    )
    try:
        with urlopen(request, timeout=60) as response:
            return json.load(response), response.geturl()
    except HTTPError as error:
        message = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"VoiceBench API returned HTTP {error.code}: {message}") from error


def download_audio(url: str, api_key: str, output: Path) -> None:
    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "ngrok-skip-browser-warning": "VoiceBench API client",
        },
    )
    with urlopen(request, timeout=120) as response:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(response.read())


def main() -> None:
    parser = argparse.ArgumentParser(description="Create speech through VoiceBench External TTS API")
    parser.add_argument("--base-url", required=True, help="For example: https://example.ngrok-free.dev")
    parser.add_argument("--text", required=True)
    parser.add_argument("--output", type=Path, default=Path("voicebench-result.wav"))
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    arguments = parser.parse_args()

    api_key = os.environ.get("VOICEBENCH_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("Set VOICEBENCH_API_KEY before running this client.")

    base_url = arguments.base_url.rstrip("/")
    created, _ = api_request(
        f"{base_url}/v1/tts",
        api_key,
        {"text": arguments.text},
    )
    request_id = int(created["id"])
    print(f"VoiceBench request {request_id} queued.")

    while True:
        status, _ = api_request(f"{base_url}/v1/tts/{request_id}", api_key)
        print(
            f"status={status['status']} "
            f"segments={status['completed_segments']}/{status['segment_count']}"
        )
        if status["status"] == "succeeded":
            break
        if status["status"] in {"failed", "cancelled"}:
            raise RuntimeError(status.get("error") or f"TTS request {status['status']}.")
        time.sleep(max(1.0, arguments.poll_seconds))

    download_audio(f"{base_url}{status['audio_url']}", api_key, arguments.output)
    print(f"Saved {arguments.output.resolve()}")


if __name__ == "__main__":
    main()
