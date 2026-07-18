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


def counts(database: Database, project_id: int) -> dict[str, int]:
    rows = database.fetch_all(
        "SELECT status,COUNT(*) AS count FROM jobs WHERE project_id=? GROUP BY status",
        (project_id,),
    )
    return {row["status"]: row["count"] for row in rows}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run or resume a VoiceBench Gate 0 project")
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--reference-text")
    parser.add_argument("--engine", default="qwen3-tts-06b-pascal")
    parser.add_argument("--project", default="Korean Gate 0")
    parser.add_argument("--project-id", type=int)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--timeout", type=int, default=28800)
    args = parser.parse_args()

    paths = Paths.discover()
    database = Database(paths.database)
    service = Service(paths, database)
    engine = database.fetch_one(
        "SELECT * FROM engines WHERE slug=? AND enabled=1", (args.engine,)
    )
    if not engine:
        raise SystemExit(f"Engine is not registered or enabled: {args.engine}")

    if args.project_id:
        project = service.get_project(args.project_id)
    else:
        if not args.reference or not args.reference_text:
            raise SystemExit("--reference and --reference-text are required for a new project")
        reference = args.reference.resolve()
        if not reference.exists():
            raise SystemExit(f"Reference audio is missing: {reference}")
        project = service.create_project(args.project)
        service.upload_reference(
            project["id"], reference.name, reference.read_bytes(), args.reference_text
        )
        project = service.import_default_corpus(project["id"])
        service.queue_benchmark(project["id"], [engine["id"]], [args.seed])

    project_id = project["id"]
    print(f"GATE0_PROJECT_ID={project_id}", flush=True)
    runner = JobRunner(paths, database)
    runner.start()
    try:
        deadline = time.time() + args.timeout
        previous = None
        while time.time() < deadline:
            current = counts(database, project_id)
            snapshot = tuple(sorted(current.items()))
            if snapshot != previous:
                print("PROGRESS " + json.dumps(current, ensure_ascii=False), flush=True)
                previous = snapshot
            active = current.get("queued", 0) + current.get("running", 0)
            if active == 0:
                break
            time.sleep(2)
        else:
            raise TimeoutError(f"Gate 0 exceeded {args.timeout} seconds")
    finally:
        runner.stop()
        runner.join(timeout=10)

    final_counts = counts(database, project_id)
    runs = service.runs(project_id)
    mean_wall_seconds = (
        round(
            sum(float(run["metrics"].get("wall_seconds", 0)) for run in runs) / len(runs),
            3,
        )
        if runs
        else 0
    )
    summary = {
        "project_id": project_id,
        "engine": args.engine,
        "jobs": final_counts,
        "runs": len(runs),
        "hard_rejects": sum(bool(run["metrics"].get("hard_reject")) for run in runs),
        "mean_wall_seconds": mean_wall_seconds,
        "peak_vram_mb": max(
            (float(run["metrics"].get("gpu", {}).get("peak_memory_used_mb", 0)) for run in runs),
            default=0,
        ),
    }
    print("SUMMARY " + json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if final_counts.get("failed", 0):
        failures = database.fetch_all(
            "SELECT id,error FROM jobs WHERE project_id=? AND status='failed' ORDER BY id",
            (project_id,),
        )
        print("FAILURES " + json.dumps(failures, ensure_ascii=False, indent=2), flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
