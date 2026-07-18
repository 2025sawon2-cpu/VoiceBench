from __future__ import annotations

import argparse
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one real VoiceBench engine job")
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--reference-text", required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--project", default="Single-engine smoke benchmark")
    parser.add_argument("--engine", default="qwen3-tts-06b-pascal")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    reference = args.reference.resolve()
    if not reference.exists():
        raise SystemExit(f"Reference audio is missing: {reference}")

    paths = Paths.discover()
    database = Database(paths.database)
    service = Service(paths, database)
    engine = database.fetch_one(
        "SELECT * FROM engines WHERE slug=? AND enabled=1", (args.engine,)
    )
    if not engine:
        raise SystemExit(f"Engine is not registered or enabled: {args.engine}")

    project = service.create_project(args.project)
    service.upload_reference(
        project["id"], reference.name, reference.read_bytes(), args.reference_text
    )
    service.add_prompt(project["id"], args.text, "smoke")
    service.queue_benchmark(project["id"], [engine["id"]], [args.seed])

    runner = JobRunner(paths, database)
    runner.start()
    try:
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            status = database.fetch_one(
                "SELECT status,error FROM jobs WHERE project_id=? ORDER BY id DESC LIMIT 1",
                (project["id"],),
            )
            if status and status["status"] in {"succeeded", "failed"}:
                break
            time.sleep(1)
        else:
            raise TimeoutError(f"Benchmark exceeded {args.timeout} seconds")
    finally:
        runner.stop()
        runner.join(timeout=10)

    if status["status"] != "succeeded":
        raise RuntimeError(status["error"] or "Benchmark failed")
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
