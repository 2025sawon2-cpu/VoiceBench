from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voicebench.config import Paths
from voicebench.db import Database
from voicebench.runner import JobRunner
from voicebench.service import Service


REFERENCE_TEXT = (
    "Okay. Yeah. I resent you. I love you. I respect you. "
    "But you know what? You blew it! And thanks to you."
)
TARGET_TEXT = "안녕하세요. 로컬 음성 복제 품질을 확인합니다."


def main() -> None:
    paths = Paths.discover()
    sample = paths.root / ".runtime" / "samples" / "qwen-official-clone.wav"
    if not sample.exists():
        raise SystemExit(f"Official Qwen smoke-test reference is missing: {sample}")

    database = Database(paths.database)
    service = Service(paths, database)
    engine = database.fetch_one(
        "SELECT * FROM engines WHERE slug='qwen3-tts-06b-pascal' AND enabled=1"
    )
    if not engine:
        raise SystemExit("The Pascal-safe Qwen engine is not registered or enabled")

    project = service.create_project("Qwen3-TTS 0.6B · 공식 샘플 smoke test")
    service.upload_reference(
        project["id"], sample.name, sample.read_bytes(), reference_text=REFERENCE_TEXT
    )
    service.add_prompt(project["id"], TARGET_TEXT, "smoke")
    service.queue_benchmark(project["id"], [engine["id"]], [7])

    runner = JobRunner(paths, database)
    runner.start()
    try:
        deadline = time.time() + 900
        while time.time() < deadline:
            status = database.fetch_one(
                "SELECT status,error FROM jobs WHERE project_id=? ORDER BY id DESC LIMIT 1",
                (project["id"],),
            )
            if status and status["status"] in {"succeeded", "failed"}:
                break
            time.sleep(1)
        else:
            raise TimeoutError("VoiceBench Qwen smoke test exceeded 15 minutes")
    finally:
        runner.stop()
        runner.join(timeout=10)

    status = database.fetch_one(
        "SELECT status,error FROM jobs WHERE project_id=? ORDER BY id DESC LIMIT 1",
        (project["id"],),
    )
    if status["status"] != "succeeded":
        raise RuntimeError(status["error"] or "Qwen smoke benchmark failed")

    run = service.runs(project["id"])[0]
    print(
        json.dumps(
            {
                "project_id": project["id"],
                "run_id": run["id"],
                "audio": run["output_path"],
                "duration_seconds": run["duration_seconds"],
                "metrics": run["metrics"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
