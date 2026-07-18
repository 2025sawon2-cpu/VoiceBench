from __future__ import annotations

import json
import threading
import time
import traceback
from pathlib import Path

from .audio import concatenate_wavs, inspect_wav
from .config import Paths
from .db import Database
from .engines import EngineRequest, run_engine
from .monitor import GpuSampler
from .util import json_dumps, sha256_file, system_snapshot, utc_now


class JobRunner(threading.Thread):
    def __init__(self, paths: Paths, database: Database):
        super().__init__(name="voicebench-job-runner", daemon=True)
        self.paths = paths
        self.db = database
        self.stop_event = threading.Event()

    def stop(self) -> None:
        self.stop_event.set()

    def claim(self) -> dict | None:
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT j.*,p.normalized_text,p.original_text,e.slug,e.name,e.kind,e.version,e.config_json,
                          pr.reference_path,pr.reference_sha256,pr.reference_text
                   FROM jobs j JOIN prompts p ON p.id=j.prompt_id
                   JOIN engines e ON e.id=j.engine_id JOIN projects pr ON pr.id=j.project_id
                   WHERE j.status='queued' ORDER BY j.id LIMIT 1"""
            ).fetchone()
            if not row:
                connection.execute("COMMIT")
                return None
            connection.execute(
                "UPDATE jobs SET status='running',started_at=? WHERE id=? AND status='queued'",
                (utc_now(), row["id"]),
            )
            connection.execute(
                """UPDATE studio_requests SET status='running',started_at=COALESCE(started_at,?)
                   WHERE id IN (SELECT request_id FROM studio_segments WHERE job_id=?)
                   AND status='queued'""",
                (utc_now(), row["id"]),
            )
            connection.execute("COMMIT")
            return dict(row)

    def run(self) -> None:
        while not self.stop_event.is_set():
            job = self.claim()
            if not job:
                self.stop_event.wait(0.5)
                continue
            self.execute_job(job)

    def execute_job(self, job: dict) -> None:
        output_dir = self.paths.outputs / f"project-{job['project_id']}" / f"prompt-{job['prompt_id']}"
        output_path = output_dir / f"job-{job['id']}-{job['slug']}-seed-{job['seed']}.wav"
        sampler = GpuSampler()
        started = time.perf_counter()
        try:
            if not job.get("reference_path"):
                raise RuntimeError("프로젝트에 레퍼런스 음성이 없습니다.")
            before = system_snapshot()
            sampler.start()
            adapter_result = run_engine(
                job,
                EngineRequest(
                    text=job["normalized_text"],
                    reference=Path(job["reference_path"]),
                    reference_text=job.get("reference_text", ""),
                    output=output_path,
                    seed=job["seed"],
                ),
            )
            sampler.stop()
            sampler.join(timeout=6)
            metrics = inspect_wav(output_path)
            metrics["wall_seconds"] = round(time.perf_counter() - started, 4)
            metrics["gpu"] = sampler.summary()
            digest = sha256_file(output_path)
            code = digest[:10].upper()
            manifest = {
                "job_id": job["id"],
                "engine": {
                    "slug": job["slug"],
                    "name": job["name"],
                    "version": job["version"],
                    "config": json.loads(job["config_json"] or "{}"),
                },
                "seed": job["seed"],
                "reference_sha256": job["reference_sha256"],
                "reference_text": job.get("reference_text", ""),
                "original_text": job["original_text"],
                "normalized_text": job["normalized_text"],
                "system_before": before,
                "system_after": system_snapshot(),
                "adapter": adapter_result,
            }
            now = utc_now()
            self.db.execute(
                """INSERT INTO runs(job_id,project_id,prompt_id,engine_id,anonymous_code,output_path,
                   output_sha256,duration_seconds,metrics_json,manifest_json,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    job["id"],
                    job["project_id"],
                    job["prompt_id"],
                    job["engine_id"],
                    code,
                    str(output_path),
                    digest,
                    metrics.get("duration_seconds"),
                    json_dumps(metrics),
                    json_dumps(manifest),
                    now,
                ),
            )
            self.db.execute(
                "UPDATE jobs SET status='succeeded',finished_at=? WHERE id=?", (now, job["id"])
            )
            self.refresh_studio_request(job["id"])
        except Exception as error:  # job failures must not kill the worker
            sampler.stop()
            if sampler.is_alive():
                sampler.join(timeout=6)
            message = f"{type(error).__name__}: {error}\n{traceback.format_exc()}"[-8000:]
            self.db.execute(
                "UPDATE jobs SET status='failed',error=?,finished_at=? WHERE id=?",
                (message, utc_now(), job["id"]),
            )
            self.refresh_studio_request(job["id"])

    def refresh_studio_request(self, job_id: int) -> None:
        segment = self.db.fetch_one(
            "SELECT request_id FROM studio_segments WHERE job_id=?", (job_id,)
        )
        if not segment:
            return
        request_id = segment["request_id"]
        rows = self.db.fetch_all(
            """SELECT ss.position,j.status,j.error,r.output_path
               FROM studio_segments ss JOIN jobs j ON j.id=ss.job_id
               LEFT JOIN runs r ON r.job_id=j.id
               WHERE ss.request_id=? ORDER BY ss.position""",
            (request_id,),
        )
        failed = [row for row in rows if row["status"] == "failed"]
        if failed:
            self.db.execute(
                """UPDATE jobs SET status='cancelled',finished_at=? WHERE status='queued'
                   AND id IN (SELECT job_id FROM studio_segments WHERE request_id=?)""",
                (utc_now(), request_id),
            )
            self.db.execute(
                """UPDATE studio_requests SET status='failed',error=?,finished_at=? WHERE id=?""",
                (failed[0].get("error") or "음성 조각 생성에 실패했습니다.", utc_now(), request_id),
            )
            return
        if not rows or any(row["status"] != "succeeded" for row in rows):
            return
        try:
            output = self.paths.artifacts / "studio" / f"request-{request_id}.wav"
            concatenate_wavs([Path(row["output_path"]) for row in rows], output)
            metrics = inspect_wav(output)
            self.db.execute(
                """UPDATE studio_requests SET status='succeeded',output_path=?,output_sha256=?,
                   duration_seconds=?,finished_at=? WHERE id=?""",
                (
                    str(output),
                    sha256_file(output),
                    metrics.get("duration_seconds"),
                    utc_now(),
                    request_id,
                ),
            )
        except Exception as error:
            self.db.execute(
                """UPDATE studio_requests SET status='failed',error=?,finished_at=? WHERE id=?""",
                (f"최종 WAV 결합 실패: {error}", utc_now(), request_id),
            )
