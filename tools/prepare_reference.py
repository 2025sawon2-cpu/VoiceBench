from __future__ import annotations

import argparse
import hashlib
import json
from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly


CORE_TEXT = "안녕하세요. 지금부터 제 목소리를 이용한 음성 합성 테스트를 시작하겠습니다."
QUALITY_TEXT = (
    "이 목소리가 밝게 웃을 때와 진지하게 설명할 때, 그리고 조용히 이야기할 때에도 "
    "본래의 느낌을 자연스럽게 유지했으면 좋겠습니다."
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def metrics(audio: np.ndarray, sample_rate: int) -> dict[str, float | int]:
    absolute = np.abs(audio)
    return {
        "sample_rate": sample_rate,
        "frames": len(audio),
        "duration_seconds": round(len(audio) / sample_rate, 4),
        "peak": round(float(absolute.max(initial=0)), 6),
        "rms": round(float(np.sqrt(np.mean(audio * audio))), 6),
        "clipping_ratio": round(float(np.mean(absolute >= 0.999)), 8),
    }


def extract(audio: np.ndarray, sample_rate: int, start: float, end: float) -> np.ndarray:
    return audio[round(start * sample_rate) : round(end * sample_rate)].copy()


def resample(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    divisor = gcd(source_rate, target_rate)
    return resample_poly(audio, target_rate // divisor, source_rate // divisor).astype(np.float32)


def normalize_peak(audio: np.ndarray, target_dbfs: float = -6.0) -> tuple[np.ndarray, float]:
    peak = float(np.max(np.abs(audio)))
    target = 10 ** (target_dbfs / 20)
    if peak <= 1e-9:
        return audio, 0.0
    gain = target / peak
    return np.clip(audio * gain, -1.0, 1.0), float(20 * np.log10(gain))


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare the recorded VoiceBench reference")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/prepared/ref_voice"))
    args = parser.parse_args()

    source = args.input.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    audio, sample_rate = sf.read(source, dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)

    definitions = [
        {
            "name": "reference_core_5s_24k_pcm16.wav",
            "start": 0.87,
            "end": 6.17,
            "text": CORE_TEXT,
            "purpose": "Cross-engine short reference",
        },
        {
            "name": "reference_quality_10s_24k_pcm16.wav",
            "start": 45.98,
            "end": 55.93,
            "text": QUALITY_TEXT,
            "purpose": "Primary high-fidelity Qwen reference",
        },
    ]

    preserved = extract(audio, sample_rate, 0.87, 60.95)
    preserved_path = output / "reference_master_trimmed_48k_pcm24.wav"
    sf.write(preserved_path, preserved, sample_rate, subtype="PCM_24")

    manifest: dict = {
        "source": str(source),
        "source_sha256": sha256(source),
        "source_metrics": metrics(audio, sample_rate),
        "processing": {
            "noise_reduction": False,
            "dynamic_compression": False,
            "target_sample_rate": 24000,
            "target_peak_dbfs": -6.0,
        },
        "master": {
            "path": str(preserved_path),
            "sha256": sha256(preserved_path),
            "metrics": metrics(preserved, sample_rate),
        },
        "clips": [],
    }

    for definition in definitions:
        clip = extract(audio, sample_rate, definition["start"], definition["end"])
        clip = resample(clip, sample_rate, 24000)
        clip, gain_db = normalize_peak(clip)
        destination = output / definition["name"]
        sf.write(destination, clip, 24000, subtype="PCM_16")
        transcript = destination.with_suffix(".txt")
        transcript.write_text(definition["text"] + "\n", encoding="utf-8")
        manifest["clips"].append(
            {
                **definition,
                "path": str(destination),
                "sha256": sha256(destination),
                "gain_db": round(gain_db, 4),
                "metrics": metrics(clip, 24000),
            }
        )

    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
