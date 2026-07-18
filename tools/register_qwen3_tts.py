from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voicebench.config import Paths
from voicebench.db import Database
from voicebench.service import Service


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
                    ],
                    "cwd": str(paths.root),
                    "endpoint": "http://127.0.0.1:8790/synthesize",
                    "health_url": "http://127.0.0.1:8790/health",
                    "startup_timeout_seconds": 240,
                    "log_path": str(paths.root / ".runtime" / "logs" / "qwen3-tts-worker.log"),
                },
                "requires_reference_text": True,
                "timeout_seconds": 7200,
            },
        }
    )
    print(f"Registered engine {engine['slug']} (id={engine['id']})")


if __name__ == "__main__":
    main()
