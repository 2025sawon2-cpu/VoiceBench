"""Local, opt-in Qwen prosody comparison. Run with the Qwen runtime Python.

No production settings, database rows, reference files, or generated jobs are
changed. A full original speaker embedding is retained in every condition.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.qwen3_tts_worker import QwenWorkerServer
from voicebench.audio import concatenate_wavs
from voicebench.quality import WhisperQualityVerifier
from voicebench.service import STUDIO_ENGINE_SLUG, STUDIO_DEFAULT_SEED, plan_script
from voicebench.util import sha256_file


SAMPLE = (
    "괜찮다고 말은 했지만, 사실 그날은 마음이 조금 무거웠어요. "
    "집으로 돌아오는 길에 문득 그런 생각이 들었죠. 내가 정말 원했던 건 대단한 대답이 아니라, "
    "그저 내 이야기를 끝까지 들어주는 사람이었구나. "
    "그래서 오늘은 조금 천천히 말해 보려고 해요. 서둘러 결론을 내리지 않아도 괜찮고, "
    "잠깐 말이 끊겨도 괜찮아요. 우리에게 필요한 건 완벽한 문장보다 서로의 마음을 알아주는 시간이니까요."
)


def boundary_metrics(left: Path, right: Path) -> dict:
    """Advisory measurements, not a naturalness score or a retry criterion."""
    import numpy as np
    import soundfile as sf
    from scipy.signal import find_peaks

    def edge(path: Path, tail: bool) -> dict:
        samples, rate = sf.read(path, dtype="float32")
        if samples.ndim > 1:
            samples = samples.mean(axis=1)
        # Restrict to audible speech before selecting the 1.5 s edge.
        audible = np.flatnonzero(np.abs(samples) >= 0.012)
        if not audible.size:
            return {"pitch_hz": None, "rms_dbfs": None, "voiced_frames": 0}
        samples = samples[audible[0] : audible[-1] + 1]
        samples = samples[-int(1.5 * rate):] if tail else samples[:int(1.5 * rate)]
        window = int(rate * 0.05)
        hop = int(rate * 0.02)
        pitches, energies = [], []
        for start in range(0, len(samples) - window + 1, hop):
            frame = samples[start : start + window].astype("float64")
            energy = float(np.sqrt(np.mean(frame ** 2)))
            if energy < 0.006:
                continue
            energies.append(energy)
            frame -= frame.mean()
            frame *= np.hanning(window)
            spectrum = np.fft.rfft(frame, 2 * window)
            correlation = np.fft.irfft(spectrum * np.conj(spectrum))[:window]
            if correlation[0] <= 0:
                continue
            low, high = int(rate / 450), int(rate / 65)
            peaks, _ = find_peaks(correlation[low:high])
            if not peaks.size:
                continue
            lag = int(peaks[np.argmax(correlation[peaks + low])] + low)
            if correlation[lag] / correlation[0] >= 0.55:
                pitches.append(rate / lag)
        return {
            "pitch_hz": round(float(np.median(pitches)), 2) if len(pitches) >= 3 else None,
            "rms_dbfs": round(20 * math.log10(float(np.median(energies))), 2) if energies else None,
            "voiced_frames": len(pitches),
        }

    before, after = edge(left, True), edge(right, False)
    pitch_delta = (
        12 * math.log2(after["pitch_hz"] / before["pitch_hz"])
        if before["pitch_hz"] and after["pitch_hz"] else None
    )
    level_delta = (
        after["rms_dbfs"] - before["rms_dbfs"]
        if before["rms_dbfs"] is not None and after["rms_dbfs"] is not None else None
    )
    return {
        "before": before, "after": after,
        "pitch_delta_semitones": round(pitch_delta, 2) if pitch_delta is not None else None,
        "level_delta_db": round(level_delta, 2) if level_delta is not None else None,
    }


def eligible_context(previous: dict | None, segment, previous_segment) -> bool:
    if previous is None or previous_segment is None:
        return False
    quality = previous["quality"]
    return bool(
        segment.paragraph_index == previous_segment.paragraph_index
        and segment.semantic_role not in {"transition", "conclusion"}
        and not previous["continuation_used"]  # Reset after one hop to limit drift.
        and quality.get("transcription") is not None
        and not quality["retry_recommended"]
        and 3 <= quality["wav"]["duration_seconds"] <= 20
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text-file", type=Path)
    parser.add_argument("--max-chars", type=int, default=140)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if not 60 <= args.max_chars <= 180:
        parser.error("--max-chars must be between 60 and 180")
    text = args.text_file.read_text(encoding="utf-8-sig") if args.text_file else SAMPLE
    segments = plan_script(text, maximum=args.max_chars)
    if len(segments) < 2:
        parser.error("Use at least two speech segments to compare a boundary")
    with sqlite3.connect(f"{(ROOT / 'data/voicebench.sqlite3').as_uri()}?mode=ro", uri=True) as db:
        if db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]:
            raise SystemExit("Wait until the production TTS queue is idle before comparing")
        row = db.execute("SELECT config_json FROM engines WHERE slug=?", (STUDIO_ENGINE_SLUG,)).fetchone()
    if not row:
        raise SystemExit("Register the production Qwen engine first")
    config = json.loads(row[0])
    try:
        with urllib.request.urlopen(config["worker"]["health_url"], timeout=2):
            raise SystemExit("Stop the idle production Qwen worker before loading a second GPU model")
    except OSError:
        pass
    destination = (args.output_dir or ROOT / "data/comparisons" / datetime.now().strftime("prosody-%Y%m%d-%H%M%S")).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    reference = ROOT / "data/prepared/ref_voice/selected_reference.wav"
    reference_text = reference.with_suffix(".txt").read_text(encoding="utf-8").strip()
    os.environ.update({
        "HF_HOME": str(ROOT / ".runtime/cache/huggingface"),
        "HF_HUB_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false",
    })
    verifier = WhisperQualityVerifier(ROOT)
    server = QwenWorkerServer(("127.0.0.1", 0), config["worker"]["model"], "fp16")
    report = {
        "seed": STUDIO_DEFAULT_SEED, "reference_sha256": sha256_file(reference),
        "model": server.model_name, "generation": config["generation"],
        "segment_target_chars": args.max_chars, "text": text,
        "limitations": "Pitch/level/CER are diagnostics, not proof of human naturalness. Listen blind for identity, emotion, rhythm, omissions and repetition.",
        "variants": {},
    }
    try:
        for variant in ("baseline", "full_context", "continuation", "continuation_streaming"):
            rows = []
            for index, segment in enumerate(segments):
                print(f"{variant}: segment {index + 1}/{len(segments)}", flush=True)
                output = destination / f"{variant}-{index + 1:02d}.wav"
                previous = rows[-1] if rows else None
                is_continuation = variant.startswith("continuation")
                base_variant = "baseline" if variant == "continuation_streaming" else "full_context"
                use_context = is_continuation and eligible_context(
                    previous, segment, segments[index - 1] if index else None
                )
                if is_continuation and not use_context:
                    fixed = report["variants"][base_variant]["segments"][index]
                    shutil.copy2(fixed["path"], output)
                    result = {**fixed, "path": str(output), "reused_variant": base_variant}
                else:
                    generation = {
                        **config["generation"],
                        "non_streaming_mode": variant in {"full_context", "continuation"},
                    }
                    payload = {
                        "text": segment.text, "reference": str(reference),
                        "reference_text": reference_text, "output": str(output),
                        "seed": STUDIO_DEFAULT_SEED, "generation": generation,
                    }
                    if use_context:
                        payload["continuation"] = {"audio": previous["path"], "text": previous["text"]}
                    started = time.perf_counter()
                    server.synthesize(payload)
                    quality = verifier.inspect(segment.text, output)
                    result = {
                        "path": str(output), "text": segment.text,
                        "quality": quality, "continuation_used": use_context,
                        "wall_seconds": round(time.perf_counter() - started, 2),
                    }
                result["sha256"] = sha256_file(output)
                rows.append(result)
                print(json.dumps({"variant": variant, "segment": index + 1, "cer": result["quality"]["character_error_rate"], "reasons": result["quality"]["reasons"]}, ensure_ascii=False), flush=True)
            combined = destination / f"{variant}.wav"
            concatenate_wavs(
                [Path(row["path"]) for row in rows], combined,
                boundary_texts=[item.text for item in segments],
                boundary_pauses_ms=[item.pause_after_ms for item in segments],
            )
            report["variants"][variant] = {
                "audio": str(combined), "segments": rows,
                "boundaries": [boundary_metrics(Path(a["path"]), Path(b["path"])) for a, b in zip(rows, rows[1:])],
            }
            (destination / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        verifier.close()
        server.server_close()
    print(f"Comparison complete: {destination}", flush=True)


if __name__ == "__main__":
    main()
