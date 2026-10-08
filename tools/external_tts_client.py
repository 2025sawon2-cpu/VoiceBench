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
    parser.add_argument("--base-url", required=True, help="For example: http://192.168.0.58:8766")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text")
    source.add_argument('--request-id', type=int, help='Resume an existing request without resubmitting')
    parser.add_argument('--voice-id', help='Official voice ID; omit for personal default')
    parser.add_argument('--generation-key', default='')
    parser.add_argument('--editing-export', action='store_true', help='Save JSON manifest and whole-script SRT next to WAV')
    parser.add_argument("--output", type=Path, default=Path("voicebench-result.wav"))
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    arguments = parser.parse_args()

    api_key = os.environ.get("VOICEBENCH_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("Set VOICEBENCH_API_KEY before running this client.")

    base_url = arguments.base_url.rstrip("/")
    if arguments.request_id is not None:
        if arguments.request_id < 1:
            raise SystemExit('request-id must be positive')
        request_id = arguments.request_id
    else:
        created, _ = api_request(f"{base_url}/v1/tts", api_key,
            {"text": arguments.text, 'voice_id': arguments.voice_id, 'generation_key': arguments.generation_key})
        request_id = int(created["id"])
    print(f"VoiceBench request {request_id}; keep this ID to resume.")

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
    if arguments.editing_export:
        manifest, _ = api_request(f'{base_url}/v1/tts/{request_id}/export', api_key)
        arguments.output.with_suffix('.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        arguments.output.with_suffix('.srt').write_text(manifest['srt'], encoding='utf-8-sig')
        print('Saved editing manifest and whole-script SRT (not word-aligned).')


if __name__ == "__main__":
    main()
