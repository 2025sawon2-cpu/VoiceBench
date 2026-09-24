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
    python = paths.root / ".runtime" / "engines" / "chatterbox-v3" / "venv" / "Scripts" / "python.exe"
    adapter = paths.root / "adapters" / "chatterbox_v3.py"
    if not python.is_file():
        raise SystemExit(f"Chatterbox V3 engine Python is missing: {python}")

    database = Database(paths.database)
    engine = Service(paths, database).upsert_engine(
        {
            "slug": "chatterbox-multilingual-v3-experimental",
            "name": "Chatterbox Multilingual V3 · 실험용 음색 비교",
            "kind": "command",
            "version": "chatterbox-tts 0.1.7 · git 5de7a54 · V3",
            "enabled": True,
            "config": {
                "command": [
                    str(python),
                    str(adapter),
                    "--text",
                    "{text}",
                    "--reference",
                    "{reference}",
                    "--output",
                    "{output}",
                    "--seed",
                    "{seed}",
                    "--language",
                    "{language}",
                ],
                "cwd": str(paths.root),
                "environment": {
                    "HF_HOME": str(paths.root / ".runtime" / "cache" / "huggingface"),
                    "HF_HUB_DISABLE_TELEMETRY": "1",
                    "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",
                },
                "requires_reference_text": False,
                "timeout_seconds": 900,
            },
        }
    )
    print(f"Registered engine {engine['slug']} (id={engine['id']})")


if __name__ == "__main__":
    main()
