"""Register a Studio-only comparison preset without changing the production preset."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voicebench.config import Paths
from voicebench.db import Database
from voicebench.service import QWEN_EXPRESSIVE_SLUG
from tools.setup_registration import RegistrationService as Service

CONTINUITY_ENGINE_SLUG = "qwen3-tts-17b-pascal-expressive-continuity"


def register_continuity_variant(paths: Paths, database: Database) -> dict:
    source = database.fetch_one("SELECT * FROM engines WHERE slug=?", (QWEN_EXPRESSIVE_SLUG,))
    if not source or source["kind"] != "command":
        raise ValueError("Register the Qwen expressive engine first")
    config = json.loads(source["config_json"])
    if not config.get("worker"):
        raise ValueError("Continuity requires the persistent Qwen worker")
    config["continuity_mode"] = "anchored"
    return Service(paths, database).upsert_engine({
        "slug": CONTINUITY_ENGINE_SLUG,
        "name": "Qwen3-TTS 1.7B · 문맥 연결 비교 (실험·HQ)",
        "kind": source["kind"], "version": source["version"],
        "enabled": bool(source["enabled"]), "config": config,
    })


def main() -> None:
    paths = Paths.discover()
    engine = register_continuity_variant(paths, Database(paths.database))
    print(f"Registered comparison engine {engine['slug']} (id={engine['id']})")


if __name__ == "__main__":
    main()
