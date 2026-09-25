from __future__ import annotations

import array
import math
import re
import struct
import sys
import wave
from pathlib import Path
from typing import Any


def inspect_wav(path: Path) -> dict[str, Any]:
    with wave.open(str(path), "rb") as source:
        channels = source.getnchannels()
        sample_width = source.getsampwidth()
        sample_rate = source.getframerate()
        frame_count = source.getnframes()
        frames = source.readframes(frame_count)

    metrics: dict[str, Any] = {
        "channels": channels,
        "sample_width_bytes": sample_width,
        "sample_rate": sample_rate,
        "frame_count": frame_count,
        "duration_seconds": round(frame_count / sample_rate, 4) if sample_rate else 0,
        "hard_reject": frame_count == 0,
    }
    if sample_width == 2 and frames:
        values = struct.unpack(f"<{len(frames) // 2}h", frames)
        absolute = [abs(value) for value in values]
        peak = max(absolute) / 32767
        clipping = sum(value >= 32760 for value in absolute) / len(absolute)
        silence = sum(value < 328 for value in absolute) / len(absolute)
        rms = math.sqrt(sum(value * value for value in values) / len(values)) / 32767
        metrics.update(
            peak=round(peak, 6),
            rms=round(rms, 6),
            clipping_ratio=round(clipping, 8),
            silence_ratio=round(silence, 6),
            hard_reject=bool(clipping > 0.01 or frame_count == 0),
        )
    return metrics


def make_mock_wav(path: Path, text: str, seed: int, sample_rate: int = 24000) -> None:
    """Generate deterministic speech-shaped tones for end-to-end harness tests."""
    duration = max(1.2, min(8.0, len(text) * 0.075))
    frame_count = int(duration * sample_rate)
    base = 135 + (seed % 73)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        frames = bytearray()
        for index in range(frame_count):
            t = index / sample_rate
            envelope = min(1.0, index / 500, (frame_count - index) / 500)
            syllable = 1.0 if int(t * 5.5) % 3 != 2 else 0.2
            value = (
                math.sin(2 * math.pi * base * t)
                + 0.35 * math.sin(2 * math.pi * base * 2.03 * t)
                + 0.12 * math.sin(2 * math.pi * base * 3.11 * t)
            )
            sample = int(max(-1, min(1, value / 1.47)) * envelope * syllable * 9000)
            frames.extend(struct.pack("<h", sample))
        target.writeframes(frames)


def pause_after_text(text: str, fallback_ms: int = 220) -> int:
    ending = text.rstrip().rstrip("\"'’”)]}」』")
    if re.search(r"[!?！？]+$", ending):
        return 320
    if re.search(r"[.。]+$", ending):
        return 280
    if re.search(r"[,，;；:：]+$", ending):
        return 170
    return fallback_ms


def _trim_pcm16_silence(
    frames: bytes,
    channels: int,
    sample_rate: int,
    threshold: int = 350,
    window_ms: int = 10,
    edge_padding_ms: int = 40,
) -> tuple[bytes, int, int]:
    values = array.array("h")
    values.frombytes(frames)
    if sys.byteorder != "little":
        values.byteswap()
    frame_count = len(values) // channels
    if not frame_count:
        return frames, 0, 0
    window_frames = max(1, round(sample_rate * window_ms / 1000))
    first_active: int | None = None
    last_active = 0
    for start in range(0, frame_count, window_frames):
        end = min(frame_count, start + window_frames)
        sample_start = start * channels
        sample_end = end * channels
        if max(abs(value) for value in values[sample_start:sample_end]) >= threshold:
            if first_active is None:
                first_active = start
            last_active = end
    if first_active is None:
        return frames, 0, 0
    padding_frames = round(sample_rate * edge_padding_ms / 1000)
    trim_start = max(0, first_active - padding_frames)
    trim_end = min(frame_count, last_active + padding_frames)
    trimmed = values[trim_start * channels : trim_end * channels]
    if sys.byteorder != "little":
        trimmed.byteswap()
    return (
        trimmed.tobytes(),
        first_active - trim_start,
        trim_end - last_active,
    )


def concatenate_wavs(
    paths: list[Path],
    output: Path,
    silence_ms: int = 220,
    boundary_texts: list[str] | None = None,
    boundary_pauses_ms: list[int | None] | None = None,
) -> None:
    if not paths:
        raise ValueError("결합할 WAV 파일이 없습니다.")
    if boundary_texts is not None and len(boundary_texts) != len(paths):
        raise ValueError("WAV 파일과 경계 대본 수가 일치하지 않습니다.")
    if boundary_pauses_ms is not None and len(boundary_pauses_ms) != len(paths):
        raise ValueError("WAV 파일과 계획된 경계 호흡 수가 일치하지 않습니다.")
    parameters: tuple[int, int, int] | None = None
    chunks: list[tuple[bytes, int, int]] = []
    for path in paths:
        with wave.open(str(path), "rb") as source:
            current = (source.getnchannels(), source.getsampwidth(), source.getframerate())
            if parameters is None:
                parameters = current
            elif current != parameters:
                raise ValueError(f"WAV 형식이 일치하지 않습니다: {path}")
            frames = source.readframes(source.getnframes())
            if current[1] == 2:
                chunks.append(_trim_pcm16_silence(frames, current[0], current[2]))
            else:
                chunks.append((frames, 0, 0))
    channels, sample_width, sample_rate = parameters
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), "wb") as target:
        target.setnchannels(channels)
        target.setsampwidth(sample_width)
        target.setframerate(sample_rate)
        for index, (chunk, leading_frames, _) in enumerate(chunks):
            if index:
                planned_pause = (
                    boundary_pauses_ms[index - 1]
                    if boundary_pauses_ms is not None
                    else None
                )
                desired_ms = int(planned_pause) if planned_pause is not None else (
                    pause_after_text(boundary_texts[index - 1], silence_ms)
                    if boundary_texts is not None
                    else silence_ms
                )
                previous_trailing = chunks[index - 1][2]
                desired_frames = round(sample_rate * desired_ms / 1000)
                inserted_frames = max(
                    0, desired_frames - previous_trailing - leading_frames
                )
                target.writeframes(
                    b"\x00" * inserted_frames * channels * sample_width
                )
            target.writeframes(chunk)
