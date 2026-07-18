from __future__ import annotations

import math
import struct
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


def concatenate_wavs(paths: list[Path], output: Path, silence_ms: int = 220) -> None:
    if not paths:
        raise ValueError("결합할 WAV 파일이 없습니다.")
    parameters: tuple[int, int, int] | None = None
    chunks: list[bytes] = []
    for path in paths:
        with wave.open(str(path), "rb") as source:
            current = (source.getnchannels(), source.getsampwidth(), source.getframerate())
            if parameters is None:
                parameters = current
            elif current != parameters:
                raise ValueError(f"WAV 형식이 일치하지 않습니다: {path}")
            chunks.append(source.readframes(source.getnframes()))
    channels, sample_width, sample_rate = parameters
    silent_frames = round(sample_rate * silence_ms / 1000)
    silence = b"\x00" * silent_frames * channels * sample_width
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), "wb") as target:
        target.setnchannels(channels)
        target.setsampwidth(sample_width)
        target.setframerate(sample_rate)
        for index, chunk in enumerate(chunks):
            if index:
                target.writeframes(silence)
            target.writeframes(chunk)
