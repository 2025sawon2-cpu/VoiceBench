"""Compare single-generation shorts using the actual Studio speech planner.

Run with the Qwen runtime Python. All recordings stay local. This tool never
changes production presets, jobs, references or seeds. Resume reuses only WAVs
whose inputs, runtime sources and hashes still match the saved experiment.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import random
import sqlite3
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.qwen3_tts_worker import QwenWorkerServer
from tools.compare_continuity import require_free_gpu
from tools.compare_prosody import SAMPLE
from voicebench.audio import concatenate_wavs, inspect_wav
from voicebench.quality import WhisperQualityVerifier
from voicebench.service import STUDIO_DEFAULT_SEED, QWEN_EXPRESSIVE_SLUG, plan_script
from voicebench.util import atomic_write_bytes, sha256_file


SCRIPTS = {
    "story": SAMPLE,
    "explanation": (
        "집중이 안 되는 날에는 할 일을 더 늘리기보다 시작하는 방법을 바꿔 보세요. "
        "책상 위에서 지금 필요 없는 물건을 잠깐 치우고, 가장 작은 일 하나만 골라 보는 거예요. "
        "책을 한 권 다 읽겠다는 계획 대신 첫 장을 펴는 것부터 시작해도 충분합니다. "
        "막상 시작하고 나면 생각보다 어렵지 않은 일이 많거든요. 오늘은 거창한 목표보다 "
        "지금 바로 할 수 있는 작은 행동 하나에 집중해 보세요."
    ),
    "conversation": (
        "저도 처음에는 그냥 지나칠 뻔했어요. 늘 걷던 길이라서 특별한 게 있을 거라고는 "
        "생각하지 않았거든요. 그런데 그날은 평소보다 조금 일찍 나와서 주변을 천천히 "
        "둘러볼 여유가 있었어요. 골목 안쪽에 작은 가게가 하나 있더라고요. 문을 열자마자 "
        "고소한 빵 냄새가 나는데, 괜히 기분이 좋아졌어요. 가끔은 이렇게 별일 없는 하루에 "
        "뜻밖의 작은 즐거움이 찾아오는 것 같아요."
    ),
}

# A two-factor comparison; each single-factor change has its own control.
VARIANTS = {
    "expressive": {},
    "talker_085": {"temperature": 0.85},
    "subtalker_085": {"subtalker_temperature": 0.85},
    "both_085": {"temperature": 0.85, "subtalker_temperature": 0.85},
}
INPUT_MODE_VARIANTS = {
    "streaming_text": {"non_streaming_mode": False},
    "full_text": {"non_streaming_mode": True},
}


def experiment_spec(
    config: dict, reference: Path, scripts: dict[str, str],
    variants: dict[str, dict] | None = None,
) -> dict:
    variants = VARIANTS if variants is None else variants
    plans = {key: [asdict(s) for s in plan_script(text)] for key, text in scripts.items()}
    if any(len(plan) != 1 for plan in plans.values()):
        raise ValueError("Shorts comparison requires one real Studio generation per script")
    return {
        "version": 1,
        "engine_slug": QWEN_EXPRESSIVE_SLUG,
        "model": config["worker"]["model"],
        "dtype": "fp16",
        "attention": config["worker"].get("attention", "eager"),
        "seed": STUDIO_DEFAULT_SEED,
        "reference_sha256": sha256_file(reference),
        "reference_text": reference.with_suffix(".txt").read_text(encoding="utf-8").strip(),
        "plans": plans,
        "generation": {key: {**config["generation"], **delta} for key, delta in variants.items()},
        "source_sha256": {
            name: sha256_file(ROOT / name) for name in (
                "adapters/qwen3_tts_worker.py", "adapters/whisper_quality_worker.py",
                "voicebench/service.py", "voicebench/quality.py", "voicebench/audio.py",
                "tools/compare_shorts.py",
            )
        },
    }


def fingerprint(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def validate_resume(report: dict, spec: dict, destination: Path) -> None:
    if report.get("spec") != spec or report.get("fingerprint") != fingerprint(spec):
        raise ValueError("Comparison inputs/runtime changed; use a new output directory")
    for key, variants in report["results"].items():
        if key not in spec["plans"]:
            raise ValueError("Unknown cached script")
        for name, row in variants.items():
            if name not in spec["generation"]:
                raise ValueError("Unknown cached variant")
            for path_key, hash_key in (("raw", "raw_sha256"), ("audio", "sha256")):
                path = (destination / row[path_key]).resolve()
                if not path.is_relative_to(destination.resolve()) or sha256_file(path) != row[hash_key]:
                    raise ValueError("Cached WAV content changed")
                if inspect_wav(path)["hard_reject"]:
                    raise ValueError("Cached WAV is invalid")


def write_listening_page(report: dict, destination: Path) -> None:
    blocks, mapping = [], []
    for key, plan in report["spec"]["plans"].items():
        variants = report["results"].get(key, {})
        order = list(report["spec"]["generation"])
        random.Random("shorts-13-" + key).shuffle(order)
        cards = []
        for index, name in enumerate(order):
            if name not in variants:
                continue
            row = variants[name]
            label = chr(65 + index)
            source = html.escape(row["audio"], quote=True)
            quality = row["quality"]
            status = "내용 확인 필요" if quality["retry_recommended"] or quality.get("transcription") is None else "내용 검사 통과"
            cards.append(
                f'<article><h3>{label}</h3><audio controls preload="none" src="{source}"></audio>'
                f'<p>{row["duration_seconds"]:.2f}초 · {status}</p>'
                f'<label>느낀 차이<textarea data-key="{key}-{label}" placeholder="말투, 원래 목소리와의 유사성, 기계적인 질감"></textarea></label></article>'
            )
            mapping.append(f"<li>{key} {label}: {html.escape(name)}</li>")
        blocks.append(f'<section><h2>{html.escape(key)}</h2><p>{html.escape(plan[0]["text"])}</p><div class="grid">{"".join(cards)}</div></section>')
    page = '''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>VoiceBench 쇼츠 말투 비교</title><style>
body{font:16px/1.65 system-ui,sans-serif;max-width:1000px;margin:40px auto;padding:0 20px;background:#f4f5f2;color:#26322b}h1{font-size:30px}h2{font-size:22px}h3{margin:0 0 12px}section{margin:32px 0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}article,details{padding:20px;background:white;border-radius:12px}audio,textarea{width:100%;box-sizing:border-box}textarea{min-height:80px;font:inherit;border:1px solid #bbb;border-radius:6px;padding:8px}button{padding:10px 20px;cursor:pointer}small{color:#526357}
</style><h1>한 번에 생성한 쇼츠 비교</h1><p>원본 목소리와 Seed 13, 실제 기본 분할 기준이 같습니다. 각 음성은 한 번에 생성했으며 문맥 연결·속도·음량 보정을 사용하지 않았습니다. 알파벳은 조건 이름을 가린 표기이며, 다른 비교 페이지의 같은 알파벳과는 관계가 없습니다.</p><p>말투가 상황에 어울리는지, 원래 목소리가 유지되는지, 모음과 문장 끝이 기계적으로 들리는지 비교해 주세요. 내용 검사 통과는 자연스러움 점수가 아닙니다.</p>'''
    page += "".join(blocks)
    page += '<button id="save">청취 메모 저장</button><details><summary>조건 이름 확인</summary><ul>' + "".join(mapping) + '</ul></details>'
    page += '<script>const experimentFingerprint=' + json.dumps(report['fingerprint']) + ';\n'
    page += '''
const fields=[...document.querySelectorAll('textarea')];
const storageKey='voicebench-listening-'+experimentFingerprint;
const readNotes=()=>Object.fromEntries(fields.map(e=>[e.dataset.key,e.value]));
try{const saved=JSON.parse(localStorage.getItem(storageKey)||'{}');fields.forEach(e=>{if(typeof saved[e.dataset.key]==='string')e.value=saved[e.dataset.key]})}catch{}
fields.forEach(e=>e.addEventListener('input',()=>{try{localStorage.setItem(storageKey,JSON.stringify(readNotes()))}catch{}}));
document.querySelector('#save').onclick=()=>{const blob=new Blob([JSON.stringify({experiment_fingerprint:experimentFingerprint,notes:readNotes()},null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='listening-notes-'+experimentFingerprint.slice(0,12)+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)};
document.addEventListener('play',e=>{document.querySelectorAll('audio').forEach(a=>{if(a!==e.target)a.pause()})},true);
</script></html>'''
    (destination / "listen.html").write_text(page, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--suite", choices=("sampling", "input_mode"), default="sampling")
    args = parser.parse_args()
    with sqlite3.connect(f"{(ROOT / 'data/voicebench.sqlite3').as_uri()}?mode=ro", uri=True) as db:
        for table in ("jobs", "studio_requests"):
            if db.execute(f"SELECT COUNT(*) FROM {table} WHERE status IN ('queued','running')").fetchone()[0]:
                raise SystemExit("Wait until the production TTS queue is idle")
        config = json.loads(db.execute("SELECT config_json FROM engines WHERE slug=? AND enabled=1", (QWEN_EXPRESSIVE_SLUG,)).fetchone()[0])
    reference = ROOT / "data/prepared/ref_voice/selected_reference.wav"
    variants = INPUT_MODE_VARIANTS if args.suite == "input_mode" else VARIANTS
    spec = experiment_spec(config, reference, SCRIPTS, variants)
    destination = (args.output_dir or ROOT / "data/comparisons" / datetime.now().strftime(f"shorts-{args.suite}-%Y%m%d-%H%M%S")).resolve()
    if args.resume:
        report = json.loads((destination / "report.json").read_text(encoding="utf-8"))
        validate_resume(report, spec, destination)
    else:
        destination.mkdir(parents=True, exist_ok=False)
        report = {"spec": spec, "fingerprint": fingerprint(spec), "status": "running", "results": {}}

    def save() -> None:
        atomic_write_bytes(destination / "report.json", json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
        write_listening_page(report, destination)

    remaining = [(key, name) for key in SCRIPTS for name in variants if name not in report["results"].get(key, {})]
    if not remaining:
        print(f"Already complete: {destination}")
        return
    require_free_gpu()
    save()
    os.environ.update({"HF_HOME": str(ROOT / ".runtime/cache/huggingface"), "HF_HUB_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false"})
    verifier = WhisperQualityVerifier(ROOT)
    server = QwenWorkerServer(
        ("127.0.0.1", 0), spec["model"], spec["dtype"], attention=spec["attention"],
    )
    try:
        for key, name in remaining:
            # Yield the GPU between utterances if production work has arrived.
            with sqlite3.connect(f"{(ROOT / 'data/voicebench.sqlite3').as_uri()}?mode=ro", uri=True) as db:
                if any(db.execute(f"SELECT COUNT(*) FROM {table} WHERE status IN ('queued','running')").fetchone()[0] for table in ("jobs", "studio_requests")):
                    report["status"] = "interrupted_for_production"
                    save()
                    raise SystemExit("Production work arrived; resume this experiment after the queue is idle")
            print(f"Generating {key}/{name}", flush=True)
            raw = destination / f"{key}-{name}-raw.wav"
            output = destination / f"{key}-{name}.wav"
            text = spec["plans"][key][0]["text"]
            started = time.perf_counter()
            server.synthesize({"text": text, "reference": str(reference), "reference_text": spec["reference_text"], "output": str(raw), "seed": spec["seed"], "generation": spec["generation"][name]})
            # Production applies the same edge handling even to one segment.
            concatenate_wavs([raw], output)
            quality = verifier.inspect(text, raw)
            report["results"].setdefault(key, {})[name] = {
                "raw": raw.name, "raw_sha256": sha256_file(raw), "audio": output.name,
                "sha256": sha256_file(output), "quality": quality,
                "duration_seconds": inspect_wav(output)["duration_seconds"],
                "wall_seconds": round(time.perf_counter() - started, 2),
            }
            save()
            print(json.dumps({"script": key, "variant": name, "cer": quality["character_error_rate"], "reasons": quality["reasons"]}, ensure_ascii=False), flush=True)
        report["status"] = "completed"
        save()
    finally:
        verifier.close()
        server.server_close()
    print(f"Comparison complete: {destination}", flush=True)


if __name__ == "__main__":
    main()
