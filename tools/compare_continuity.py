"""Compare anchored acoustic context and fewer generation boundaries locally.

Requires an existing compare_prosody baseline report. Run with the Qwen runtime
Python, or use --analyze-only with the application Python (no GPU required).
"""
from __future__ import annotations

import argparse
import html
import json
import os
import random
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.qwen3_tts_worker import QwenWorkerServer
from tools.compare_prosody import boundary_metrics, eligible_context
from voicebench.audio import concatenate_wavs, inspect_wav
from voicebench.continuity import group_for_continuity
from voicebench.quality import WhisperQualityVerifier, normalize_for_quality
from voicebench.service import STUDIO_DEFAULT_SEED, QWEN_EXPRESSIVE_SLUG, plan_script
from voicebench.util import sha256_file


def load_baseline(path: Path, reference: Path, config: dict) -> tuple[dict, list, list]:
    """Do not silently compare a changed script, voice, seed or sampling preset."""
    report = json.loads(path.read_text(encoding="utf-8"))
    if (
        report["reference_sha256"] != sha256_file(reference)
        or report["seed"] != STUDIO_DEFAULT_SEED
        or report["model"] != config["worker"]["model"]
        or report.get("attention", "eager") != config["worker"].get("attention", "eager")
        or report["generation"] != config["generation"]
    ):
        raise ValueError("Baseline settings differ from the selected production voice/preset")
    segments = plan_script(report["text"], maximum=report["segment_target_chars"])
    baseline = report["variants"]["baseline"]["segments"]
    if len(segments) != len(baseline):
        raise ValueError("Baseline segmentation no longer matches the speech planner")
    for segment, row in zip(segments, baseline):
        if segment.text != row["text"] or sha256_file(Path(row["path"])) != row["sha256"]:
            raise ValueError("Baseline text or WAV content has changed")
        if row["quality"].get("transcription") is None or row["quality"]["retry_recommended"]:
            raise ValueError("Only an ASR-verified baseline can supply acoustic context")
        measured = inspect_wav(Path(row["path"]))
        if measured["hard_reject"] or measured["duration_seconds"] != row["quality"]["wav"]["duration_seconds"]:
            raise ValueError("Baseline waveform metadata does not match the WAV")
    return report, segments, baseline


def describe_row(row: dict, baseline_seconds: float) -> dict:
    wav = row["quality"]["wav"]
    duration = float(wav["duration_seconds"])
    return {
        "duration_seconds": duration,
        "characters_per_second": round(len(normalize_for_quality(row["text"])) / duration, 3) if duration else None,
        "duration_ratio_to_baseline": round(duration / baseline_seconds, 3),
        "whole_clip_rms": wav.get("rms"),
    }


def require_free_gpu(minimum_mb: int = 6144) -> None:
    completed = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits", "-i", "0"],
        capture_output=True, text=True, check=True,
    )
    free_mb = int(completed.stdout.strip().splitlines()[0])
    if free_mb < minimum_mb:
        raise RuntimeError(
            f"Qwen comparison requires {minimum_mb} MiB free VRAM; currently {free_mb} MiB. "
            "Close other GPU workloads yourself before retrying; this tool never stops them."
        )


def write_listening_page(report: dict, destination: Path) -> Path:
    """Keep condition names hidden until the listener chooses to reveal them."""
    names = {
        "baseline": "기존 분할 생성",
        "anchored": "원본 참조를 유지한 문맥 연결",
        "longer_turns": "같은 흐름을 한 번에 생성",
    }
    order = list(report["variants"])
    random.Random(13).shuffle(order)
    cards, details = [], []
    for index, key in enumerate(order):
        variant = report["variants"][key]
        audio = Path(variant["audio"])
        relative = audio.resolve().relative_to(destination.resolve()).as_posix()
        if not audio.is_file():
            raise FileNotFoundError(audio)
        label = chr(ord("A") + index)
        source = html.escape(relative, quote=True)
        cards.append(
            f'<section><h2>음성 {label}</h2><audio controls preload="metadata" src="{source}"></audio>'
            f'<p><a href="{source}" download>WAV 저장</a></p></section>'
        )
        warnings = sum(bool(row["quality"]["retry_recommended"]) for row in variant["segments"])
        details.append(
            f'<tr><td>{label}</td><td>{html.escape(names.get(key, key))}</td>'
            f'<td>{variant["generation_boundary_count"]}</td><td>{warnings}</td></tr>'
        )
    page = '''<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>VoiceBench 연속성 비교</title>
<style>body{font-family:system-ui,sans-serif;max-width:850px;margin:40px auto;padding:0 24px;background:#f5f5f2;color:#202623;line-height:1.7}h1{font-size:30px}h2{font-size:20px;margin:0 0 10px}section,details{background:white;padding:22px;border-radius:14px;margin:18px 0}audio{width:100%}p{color:#495750}table{width:100%;border-collapse:collapse;margin:20px 0}td,th{text-align:left;padding:10px;border-bottom:1px solid #ddd}summary{cursor:pointer;font-weight:600}blockquote{border-left:3px solid #93a798;margin:20px 0;padding:0 16px}a{color:#245a43}</style>
<h1>말의 흐름 비교</h1><p>같은 대본 · 원본 목소리 · Seed 13. 조건 이름을 보기 전에 음색, 감정 흐름, 속도와 문장 연결을 비교해 보세요. 음량 보정이나 속도 보정은 적용하지 않았습니다.</p>'''
    page += "".join(cards)
    page += '<details><summary>대본 보기</summary><blockquote>' + html.escape(report["text"]) + '</blockquote></details>'
    page += '<details><summary>조건과 검사 결과 공개</summary><table><tr><th>음성</th><th>조건</th><th>생성 연결부</th><th>검사 경고</th></tr>' + ''.join(details) + '</table><p>자동 검사 통과가 사람처럼 자연스럽다는 판정을 의미하지는 않습니다. 실제 길이는 생성 후 측정하며, 28초는 묶기 계획의 예상 범위입니다.</p></details>'
    page += "<script>document.querySelectorAll('audio').forEach(p=>p.addEventListener('play',()=>document.querySelectorAll('audio').forEach(q=>{if(q!==p)q.pause()})));</script></html>"
    output = destination / "listen.html"
    output.write_text(page, encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-report", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--analyze-only", action="store_true")
    args = parser.parse_args()
    with sqlite3.connect(f"{(ROOT / 'data/voicebench.sqlite3').as_uri()}?mode=ro", uri=True) as db:
        if not args.analyze_only and db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]:
            raise SystemExit("Wait for the production queue to become idle")
        row = db.execute("SELECT config_json FROM engines WHERE slug=?", (QWEN_EXPRESSIVE_SLUG,)).fetchone()
    if not row:
        raise SystemExit("Production Qwen engine is not registered")
    config = json.loads(row[0])
    reference = ROOT / "data/prepared/ref_voice/selected_reference.wav"
    source, segments, baseline = load_baseline(args.baseline_report, reference, config)
    durations = [item["quality"]["wav"]["duration_seconds"] for item in baseline]
    groups = group_for_continuity(segments, durations)
    report = {
        "status": "planned", "source_report": str(args.baseline_report.resolve()),
        "reference_sha256": source["reference_sha256"], "seed": STUDIO_DEFAULT_SEED,
        "model": source["model"], "attention": source.get("attention", "eager"),
        "generation": source["generation"], "text": source["text"],
        "duration_target_seconds": 28, "maximum_group_chars": 280,
        "generation_groups": [vars(group) for group in groups],
        "limitations": "Duration estimates are not hard limits. Boundary pitch and RMS do not establish naturalness. No gain, time stretch or crossfade is applied.",
        "variants": {},
    }
    if not args.analyze_only:
        require_free_gpu()
    destination = (args.output_dir or ROOT / "data/comparisons" / datetime.now().strftime("continuity-%Y%m%d-%H%M%S")).resolve()
    destination.mkdir(parents=True, exist_ok=False)

    def save() -> None:
        (destination / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def finish_variant(name: str, rows: list[dict], pauses: list[int]) -> None:
        combined = destination / f"{name}.wav"
        concatenate_wavs([Path(item["path"]) for item in rows], combined, boundary_pauses_ms=pauses)
        report["variants"][name] = {
            "audio": str(combined), "segments": rows,
            "generation_boundary_count": max(0, len(rows) - 1),
            "boundaries": [boundary_metrics(Path(a["path"]), Path(b["path"])) for a, b in zip(rows, rows[1:])],
        }
        save()

    # Analyze-only deliberately avoids importing numpy, soundfile or torch.
    report["baseline_profiles"] = [describe_row(row, duration) for row, duration in zip(baseline, durations)]
    save()
    if args.analyze_only:
        print(json.dumps({"baseline_segments": len(segments), "planned_groups": len(groups), "report": str(destination / "report.json")}), flush=True)
        return
    baseline_rows = []
    for index, item in enumerate(baseline):
        output = destination / f"baseline-{index + 1:02d}.wav"
        shutil.copy2(item["path"], output)
        baseline_rows.append({**item, "path": str(output), "reused_variant": "verified_baseline"})
    finish_variant("baseline", baseline_rows, [item.pause_after_ms for item in segments])
    os.environ.update({"HF_HOME": str(ROOT / ".runtime/cache/huggingface"), "HF_HUB_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false"})
    server = QwenWorkerServer(
        ("127.0.0.1", 0), source["model"], "fp16", attention=report["attention"],
    )
    verifier = WhisperQualityVerifier(ROOT)
    reference_text = reference.with_suffix(".txt").read_text(encoding="utf-8").strip()

    def generate(text: str, output: Path, baseline_seconds: float, context: dict | None = None) -> dict:
        payload = {
            "text": text, "reference": str(reference), "reference_text": reference_text,
            "output": str(output), "seed": STUDIO_DEFAULT_SEED,
            "generation": {**config["generation"], "non_streaming_mode": False},
        }
        if context is not None:
            payload["continuation"] = context
        started = time.perf_counter()
        server.synthesize(payload)
        quality = verifier.inspect(text, output)
        result = {
            "path": str(output), "text": text, "quality": quality,
            "sha256": sha256_file(output), "continuation_used": context is not None,
            "context_mode": "anchored" if context else None,
            "wall_seconds": round(time.perf_counter() - started, 2),
        }
        result["profile"] = describe_row(result, baseline_seconds)
        print(json.dumps({"file": output.name, "cer": quality["character_error_rate"], "reasons": quality["reasons"], "profile": result["profile"]}, ensure_ascii=False), flush=True)
        return result

    try:
        rows = []
        for index, segment in enumerate(segments):
            previous = rows[-1] if rows else None
            if eligible_context(previous, segment, segments[index - 1] if index else None):
                result = generate(
                    segment.text, destination / f"anchored-{index + 1:02d}.wav", durations[index],
                    {"audio": previous["path"], "text": previous["text"], "mode": "anchored"},
                )
            else:
                result = {**baseline_rows[index], "reused_variant": "baseline"}
            rows.append(result)
        finish_variant("anchored", rows, [item.pause_after_ms for item in segments])
        rows = []
        for index, group in enumerate(groups):
            if len(group.positions) == 1:
                result = {**baseline_rows[group.positions[0]], "reused_variant": "baseline"}
            else:
                result = generate(group.text, destination / f"longer-turns-{index + 1:02d}.wav", group.estimated_seconds)
            result["source_positions"] = group.positions
            result["duration_target_exceeded"] = result["quality"]["wav"]["duration_seconds"] > 28
            rows.append(result)
        finish_variant("longer_turns", rows, [group.pause_after_ms for group in groups])
        report["status"] = "completed"
        save()
        write_listening_page(report, destination)
    finally:
        verifier.close()
        server.server_close()
    print(f"Completed comparison: {destination}", flush=True)


if __name__ == "__main__":
    main()
