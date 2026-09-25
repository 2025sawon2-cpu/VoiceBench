from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voicebench.config import Paths
from voicebench.db import Database
from voicebench.service import Service
from tools.register_continuity import register_continuity_variant


def main() -> None:
    paths = Paths.discover()
    paths.ensure()
    python = paths.root / ".runtime" / "engines" / "qwen3-tts" / "venv" / "Scripts" / "python.exe"
    adapter = paths.root / "adapters" / "qwen3_tts.py"
    worker_adapter = paths.root / "adapters" / "qwen3_tts_worker.py"
    if not python.exists():
        raise SystemExit(f"Qwen3-TTS engine Python is missing: {python}")

    database = Database(paths.database)
    database.execute(
        """DELETE FROM engines WHERE slug='qwen3-tts-06b-fp16'
           AND NOT EXISTS (SELECT 1 FROM jobs WHERE engine_id=engines.id)"""
    )
    database.execute("UPDATE engines SET enabled=0 WHERE slug='qwen3-tts-06b-fp16'")
    engine = Service(paths, database).upsert_engine(
        {
            "slug": "qwen3-tts-06b-pascal",
            "name": "Qwen3-TTS 0.6B · Pascal-safe/eager",
            "kind": "command",
            "version": "qwen-tts 0.1.1 · Qwen3-TTS-12Hz-0.6B-Base",
            "enabled": True,
            "config": {
                "command": [
                    str(python),
                    str(adapter),
                    "--text",
                    "{text}",
                    "--reference",
                    "{reference}",
                    "--ref-text",
                    "{reference_text}",
                    "--output",
                    "{output}",
                    "--seed",
                    "{seed}",
                    "--language",
                    "{language}",
                    "--dtype",
                    "auto",
                    "--model",
                    "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
                ],
                "cwd": str(paths.root),
                "environment": {
                    "HF_HOME": str(paths.root / ".runtime" / "cache" / "huggingface"),
                    "HF_HUB_DISABLE_TELEMETRY": "1",
                    "TOKENIZERS_PARALLELISM": "false",
                },
                "worker": {
                    "command": [
                        str(python),
                        str(worker_adapter),
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "8790",
                        "--dtype",
                        "auto",
                        "--model",
                        "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
                    ],
                    "cwd": str(paths.root),
                    "endpoint": "http://127.0.0.1:8790/synthesize",
                    "health_url": "http://127.0.0.1:8790/health",
                    "model": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
                    "startup_timeout_seconds": 240,
                    "log_path": str(paths.root / ".runtime" / "logs" / "qwen3-tts-worker.log"),
                },
                "requires_reference_text": True,
                "generation": {
                    "temperature": 0.9,
                    "top_k": 50,
                    "top_p": 1.0,
                    "repetition_penalty": 1.05,
                    "subtalker_temperature": 0.9,
                    "subtalker_top_k": 50,
                    "subtalker_top_p": 1.0,
                },
                "timeout_seconds": 7200,
            },
        }
    )
    print(f"Registered engine {engine['slug']} (id={engine['id']})")

    engine_17b = Service(paths, database).upsert_engine(
        {
            "slug": "qwen3-tts-17b-pascal",
            "name": "Qwen3-TTS 1.7B · Pascal FP16/eager",
            "kind": "command",
            "version": "qwen-tts 0.1.1 · Qwen3-TTS-12Hz-1.7B-Base",
            "enabled": True,
            "config": {
                "command": [
                    str(python),
                    str(adapter),
                    "--text",
                    "{text}",
                    "--reference",
                    "{reference}",
                    "--ref-text",
                    "{reference_text}",
                    "--output",
                    "{output}",
                    "--seed",
                    "{seed}",
                    "--language",
                    "{language}",
                    "--dtype",
                    "fp16",
                    "--model",
                    "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
                ],
                "cwd": str(paths.root),
                "environment": {
                    "HF_HOME": str(paths.root / ".runtime" / "cache" / "huggingface"),
                    "HF_HUB_DISABLE_TELEMETRY": "1",
                    "TOKENIZERS_PARALLELISM": "false",
                },
                "worker": {
                    "command": [
                        str(python),
                        str(worker_adapter),
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "8790",
                        "--dtype",
                        "fp16",
                        "--model",
                        "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
                    ],
                    "cwd": str(paths.root),
                    "endpoint": "http://127.0.0.1:8790/synthesize",
                    "health_url": "http://127.0.0.1:8790/health",
                    "model": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
                    "startup_timeout_seconds": 300,
                    "log_path": str(paths.root / ".runtime" / "logs" / "qwen3-tts-worker-17b.log"),
                },
                "requires_reference_text": True,
                "generation": {
                    "temperature": 0.9,
                    "top_k": 50,
                    "top_p": 1.0,
                    "repetition_penalty": 1.05,
                    "subtalker_temperature": 0.9,
                    "subtalker_top_k": 50,
                    "subtalker_top_p": 1.0,
                },
                "timeout_seconds": 7200,
            },
        }
    )
    print(f"Registered engine {engine_17b['slug']} (id={engine_17b['id']})")

    presets = [
        (
            "qwen3-tts-17b-pascal-stable",
            "Qwen3-TTS 1.7B · 안정형 T0.75/P0.90",
            {
                "temperature": 0.75,
                "top_k": 40,
                "top_p": 0.9,
                "repetition_penalty": 1.05,
                "subtalker_temperature": 0.75,
                "subtalker_top_k": 40,
                "subtalker_top_p": 0.9,
            },
        ),
        (
            "qwen3-tts-17b-pascal-expressive",
            "Qwen3-TTS 1.7B · 표현형 T1.0/P0.95",
            {
                "temperature": 1.0,
                "top_k": 50,
                "top_p": 0.95,
                "repetition_penalty": 1.05,
                "subtalker_temperature": 1.0,
                "subtalker_top_k": 50,
                "subtalker_top_p": 0.95,
            },
        ),
    ]
    for slug, name, generation in presets:
        config = deepcopy(engine_17b["config"])
        config["generation"] = generation
        preset = Service(paths, database).upsert_engine(
            {
                "slug": slug,
                "name": name,
                "kind": "command",
                "version": engine_17b["version"],
                "enabled": True,
                "config": config,
            }
        )
        print(f"Registered engine {preset['slug']} (id={preset['id']})")

    comparison = register_continuity_variant(paths, database)
    print(f"Registered comparison engine {comparison['slug']} (id={comparison['id']})")


if __name__ == "__main__":
    main()
