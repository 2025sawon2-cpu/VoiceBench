from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .audio import inspect_wav


QUALITY_RETRY_SEEDS = (10, 42)
_KEEP_TEXT = re.compile(r"[^0-9a-z가-힣]+")


def normalize_for_quality(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower()
    return _KEEP_TEXT.sub("", normalized)


def character_error_rate(expected: str, actual: str) -> float:
    expected = normalize_for_quality(expected)
    actual = normalize_for_quality(actual)
    if not expected:
        return 0.0 if not actual else 1.0
    previous = list(range(len(actual) + 1))
    for row, expected_char in enumerate(expected, start=1):
        current = [row]
        for column, actual_char in enumerate(actual, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (expected_char != actual_char),
                )
            )
        previous = current
    return previous[-1] / len(expected)


def _adjacent_repetitions(text: str) -> Counter[str]:
    compact = normalize_for_quality(text)
    repeated: Counter[str] = Counter()
    for width in range(3, min(16, len(compact) // 2) + 1):
        for start in range(0, len(compact) - width * 2 + 1):
            phrase = compact[start : start + width]
            if compact[start + width : start + width * 2] == phrase:
                repeated[phrase] += 1
    return repeated


def evaluate_candidate(
    expected_text: str,
    transcription: str | None,
    wav_metrics: dict[str, Any],
) -> dict[str, Any]:
    expected = normalize_for_quality(expected_text)
    actual = normalize_for_quality(transcription or "")
    cer = character_error_rate(expected, actual) if transcription is not None else None
    length_ratio = len(actual) / len(expected) if expected else 1.0
    ending_size = min(4, len(expected))
    ending = expected[-ending_size:] if ending_size else ""
    ending_match = bool(ending and actual.endswith(ending))
    missing_suffix = bool(
        len(expected) >= 8
        and actual
        and expected.startswith(actual)
        and 0 < len(expected) - len(actual) <= 3
    )
    # Scripted emphasis ("천천히 천천히") is not a synthesis defect.
    repeated = bool(
        _adjacent_repetitions(transcription or "") - _adjacent_repetitions(expected_text)
    )
    reasons: list[str] = []

    if wav_metrics.get("hard_reject"):
        reasons.append("waveform_reject")
    duration = float(wav_metrics.get("duration_seconds") or 0)
    if expected and duration < max(0.45, len(expected) * 0.055):
        reasons.append("implausibly_short_audio")
    if expected and duration > max(3.5, len(expected) * 0.35):
        reasons.append("implausibly_long_audio")
    if duration and "rms" in wav_metrics and float(wav_metrics.get("rms") or 0) < 0.002:
        reasons.append("near_silent_audio")
    if (
        duration > 1
        and "silence_ratio" in wav_metrics
        and float(wav_metrics.get("silence_ratio") or 0) > 0.9
    ):
        reasons.append("excessive_silence")
    if transcription is not None and expected:
        if not actual:
            reasons.append("empty_transcription")
        elif len(expected) >= 8 and length_ratio < 0.55:
            reasons.append("likely_omission")
        elif len(expected) >= 8 and length_ratio > 1.55:
            reasons.append("likely_repetition")
        if missing_suffix:
            reasons.append("likely_ending_omission")
        if repeated and length_ratio > 1.12:
            reasons.append("adjacent_repetition")
        if (
            len(expected) >= 8
            and not ending_match
            and cer is not None
            and cer > 0.34
        ):
            reasons.append("ending_mismatch")
        if cer is not None and len(expected) >= 8 and cer > 0.58:
            reasons.append("high_character_error")

    score = 0.0 if cer is None else cer
    score += abs(1.0 - min(length_ratio, 2.0)) * 0.18
    if not ending_match and transcription is not None:
        score += 0.08
    if repeated:
        score += 0.18
    score += 0.5 * sum(
        reason
        in {
            "implausibly_short_audio",
            "implausibly_long_audio",
            "near_silent_audio",
            "excessive_silence",
        }
        for reason in reasons
    )
    if wav_metrics.get("hard_reject"):
        score += 10.0
    return {
        "transcription": transcription,
        "character_error_rate": round(cer, 4) if cer is not None else None,
        "length_ratio": round(length_ratio, 4),
        "ending_match": ending_match,
        "adjacent_repetition": repeated,
        "retry_recommended": bool(reasons),
        "reasons": reasons,
        "score": round(score, 4),
        "wav": wav_metrics,
    }


def choose_best_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        raise ValueError("품질 후보가 없습니다.")
    # A slightly lower CER must never beat a candidate that actually passed.
    # When all candidates fail, retain the least risky playable result.
    return min(
        candidates,
        key=lambda item: (
            bool(item["quality"].get("wav", {}).get("hard_reject")),
            bool(item["quality"].get("retry_recommended")),
            item["quality"]["score"],
            item["attempt"],
        ),
    )


@dataclass
class _Worker:
    process: subprocess.Popen[str]
    log_stream: Any


class WhisperQualityVerifier:
    """Lazy, CPU-only local ASR verifier backed by the existing Qwen runtime."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self._worker: _Worker | None = None
        self._lock = threading.Lock()
        self.unavailable_reason: str | None = None

    def _python_path(self) -> Path:
        suffix = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
        return self.root / ".runtime" / "engines" / "qwen3-tts" / "venv" / suffix

    def _model_path(self) -> Path | None:
        snapshots = (
            self.root
            / ".runtime"
            / "cache"
            / "huggingface"
            / "hub"
            / "models--openai--whisper-base"
            / "snapshots"
        )
        if not snapshots.exists():
            return None
        return next(
            (path for path in sorted(snapshots.iterdir()) if (path / "model.safetensors").is_file()),
            None,
        )

    def _start(self) -> _Worker:
        if self._worker and self._worker.process.poll() is None:
            return self._worker
        python = self._python_path()
        model = self._model_path()
        worker_script = self.root / "adapters" / "whisper_quality_worker.py"
        if not python.is_file() or not worker_script.is_file() or model is None:
            raise RuntimeError("로컬 Whisper Base 검사 환경을 찾을 수 없습니다.")
        log_path = self.root / ".runtime" / "logs" / "whisper-quality.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_stream = log_path.open("a", encoding="utf-8", buffering=1)
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen(
            [str(python), str(worker_script), "--model", str(model)],
            cwd=self.root,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log_stream,
            text=True,
            encoding="utf-8",
            bufsize=1,
            creationflags=creationflags,
        )
        ready = process.stdout.readline() if process.stdout else ""
        try:
            payload = json.loads(ready)
        except json.JSONDecodeError as error:
            process.terminate()
            log_stream.close()
            raise RuntimeError("Whisper 품질 검사기가 시작되지 않았습니다.") from error
        if payload.get("status") != "ready":
            process.terminate()
            log_stream.close()
            raise RuntimeError(payload.get("error") or "Whisper 품질 검사기 준비 실패")
        self._worker = _Worker(process, log_stream)
        return self._worker

    def transcribe(self, path: Path, language: str = "ko") -> str | None:
        if self.unavailable_reason:
            return None
        with self._lock:
            try:
                worker = self._start()
                if not worker.process.stdin or not worker.process.stdout:
                    raise RuntimeError("Whisper 품질 검사기 파이프가 닫혔습니다.")
                worker.process.stdin.write(
                    json.dumps(
                        {"audio": str(path.resolve()), "language": language},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                worker.process.stdin.flush()
                response = json.loads(worker.process.stdout.readline())
                if response.get("status") != "ok":
                    raise RuntimeError(response.get("error") or "Whisper 인식 실패")
                return str(response.get("transcription", ""))
            except Exception as error:
                self.unavailable_reason = f"{type(error).__name__}: {error}"
                self.close()
                return None

    def inspect(self, expected_text: str, path: Path) -> dict[str, Any]:
        wav_metrics = inspect_wav(path)
        transcription = self.transcribe(path)
        quality = evaluate_candidate(expected_text, transcription, wav_metrics)
        quality["verifier"] = "whisper-base-cpu" if transcription is not None else "waveform-only"
        if self.unavailable_reason:
            quality["verifier_error"] = self.unavailable_reason
        return quality

    def close(self) -> None:
        worker = self._worker
        self._worker = None
        if not worker:
            return
        try:
            if worker.process.poll() is None and worker.process.stdin:
                worker.process.stdin.write('{"command":"shutdown"}\n')
                worker.process.stdin.flush()
                worker.process.wait(timeout=8)
        except Exception:
            if worker.process.poll() is None:
                worker.process.terminate()
        finally:
            if worker.process.stdout:
                worker.process.stdout.close()
            if worker.process.stdin:
                worker.process.stdin.close()
            worker.log_stream.close()


def promote_candidate(candidate_path: Path, output_path: Path) -> None:
    if candidate_path.resolve() == output_path.resolve():
        return
    shutil.copy2(candidate_path, output_path)


def cleanup_candidates(paths: list[Path], selected: Path, canonical: Path) -> None:
    for path in paths:
        if path.resolve() not in {selected.resolve(), canonical.resolve()}:
            path.unlink(missing_ok=True)
    if selected.resolve() != canonical.resolve():
        selected.unlink(missing_ok=True)
