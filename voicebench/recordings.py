"""Bounded, local reference-clip validation and optional Korean transcription."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import wave

from .quality import WhisperQualityVerifier


def validate_voice_clip(content: bytes) -> float:
    if len(content) > 12 * 1024 * 1024:
        raise ValueError("음성 구간은 12MB 이하여야 합니다.")
    try:
        with wave.open(io.BytesIO(content), "rb") as audio:
            duration = audio.getnframes() / audio.getframerate()
            if audio.getnchannels() not in (1, 2) or audio.getsampwidth() != 2 or not 3 <= duration <= 30:
                raise ValueError("3~30초 길이의 16비트 PCM WAV(모노 또는 스테레오)를 사용하세요.")
            if len(audio.readframes(audio.getnframes())) != audio.getnframes() * audio.getnchannels() * 2:
                raise ValueError("음성 파일이 손상되었습니다.")
            return duration
    except (wave.Error, EOFError, ZeroDivisionError) as error:
        raise ValueError("16비트 PCM WAV 파일을 사용하세요.") from error


class ReferenceTranscriber:
    """One CPU subprocess at a time; no GPU, downloads or database writes."""

    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.Lock()

    def transcribe(self, content: bytes) -> str:
        validate_voice_clip(content)
        if not self._lock.acquire(blocking=False):
            raise ValueError("다른 녹음의 대본을 인식 중입니다. 잠시 후 다시 시도하거나 직접 입력해 주세요.")
        try:
            runtime = WhisperQualityVerifier(self.root)
            python, model = runtime._python_path(), runtime._model_path()
            worker = self.root / "adapters" / "whisper_quality_worker.py"
            if not python.is_file() or model is None or not worker.is_file():
                raise ValueError("로컬 음성 인식 환경이 없습니다. 녹음 대본을 직접 입력해 주세요.")
            temporary = self.root / ".runtime" / "reference-transcription"
            temporary.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=temporary) as directory:
                audio = Path(directory) / "clip.wav"
                audio.write_bytes(content)
                commands = json.dumps({"audio": str(audio), "language": "ko"}) + '\n{"command":"shutdown"}\n'
                result = subprocess.run(
                    [str(python), str(worker), "--model", str(model)], cwd=self.root,
                    input=commands, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    encoding="utf-8", timeout=110, check=False,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8", "HF_HUB_OFFLINE": "1"},
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                for line in result.stdout.splitlines():
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if result.returncode == 0 and payload.get("status") == "ok" and payload.get("transcription", "").strip():
                        return payload["transcription"].strip()
                raise ValueError("녹음에서 문장을 인식하지 못했습니다. 대본을 직접 입력해 주세요.")
        except subprocess.TimeoutExpired as error:
            raise ValueError("음성 인식 시간이 초과됐습니다. 잠시 후 다시 시도하거나 직접 입력해 주세요.") from error
        finally:
            self._lock.release()
