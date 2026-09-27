from __future__ import annotations

import json
import threading
import time
import traceback
from pathlib import Path

from .audio import concatenate_wavs, inspect_wav
from .config import Paths
from .db import Database
from .engines import EngineError, EngineRequest, run_engine
from .monitor import GpuSampler
from .quality import (
    QUALITY_RETRY_SEEDS,
    WhisperQualityVerifier,
    choose_best_candidate,
    cleanup_candidates,
    promote_candidate,
)
from .service import (
    LONG_FORM_QUALITY_MODE,
    STUDIO_INPUT_POLICY,
    QWEN_EXPRESSIVE_SLUG,
    supports_quality_control,
    partition_studio_chapters,
)
from .util import json_dumps, sha256_file, system_snapshot, utc_now


class JobRunner(threading.Thread):
    def __init__(self, paths: Paths, database: Database):
        super().__init__(name="voicebench-job-runner", daemon=True)
        self.paths = paths
        self.db = database
        self.stop_event = threading.Event()
        self.quality_verifier = WhisperQualityVerifier(paths.root)

    def stop(self) -> None:
        self.stop_event.set()

    def claim(self) -> dict | None:
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT j.*,p.normalized_text,p.original_text,e.slug,e.name,e.kind,e.version,e.config_json,
                          pr.reference_path,pr.reference_sha256,pr.reference_text,
                          sr.id AS studio_request_id,sr.quality_mode AS studio_quality_mode,
                          sr.generation_json AS studio_generation_json,
                          (SELECT COUNT(*) FROM studio_segments total
                           WHERE total.request_id=sr.id) AS request_segment_count,
                          ss.position AS segment_position,ss.paragraph_index,ss.semantic_role
                   FROM jobs j JOIN prompts p ON p.id=j.prompt_id
                   JOIN engines e ON e.id=j.engine_id JOIN projects pr ON pr.id=j.project_id
                   LEFT JOIN studio_segments ss ON ss.job_id=j.id
                   LEFT JOIN studio_requests sr ON sr.id=ss.request_id
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
        try:
            while not self.stop_event.is_set():
                job = self.claim()
                if not job:
                    self.stop_event.wait(0.5)
                    continue
                self.execute_job(job)
        finally:
            self.quality_verifier.close()

    def _quality_control_enabled(self, job: dict) -> bool:
        return bool(
            job.get("studio_request_id")
            and job.get("studio_quality_mode") == LONG_FORM_QUALITY_MODE
            and job.get("kind") == "command"
            and supports_quality_control(str(job.get("slug", "")))
        )

    def _request_cancelled(self, job: dict) -> bool:
        request_id = job.get("studio_request_id")
        if not request_id:
            return False
        row = self.db.fetch_one("SELECT status FROM studio_requests WHERE id=?", (request_id,))
        return bool(row and row["status"] == "cancelled")

    def _synthesis_engine(self, job: dict) -> tuple[dict, dict]:
        """Apply the selected shorts input mode to one complete HQ generation.

        Count the entire request, not its remaining jobs. Explicit comparison
        settings and multi-segment requests retain their registered generation.
        Work on a copy so the preset and continuity compatibility stay intact.
        """
        config = json.loads(job["config_json"] or "{}")
        generation = {**config.get("generation", {}), **json.loads(job.get("studio_generation_json") or "{}")}
        config["generation"] = generation
        apply_full_text = bool(
            self._quality_control_enabled(job)
            and job.get("slug") == QWEN_EXPRESSIVE_SLUG
            and job.get("request_segment_count") == 1
            and config.get("worker")
            and "non_streaming_mode" not in generation
        )
        if apply_full_text:
            generation = {**generation, "non_streaming_mode": True}
            config["generation"] = generation
        synthesis = {
            "input_policy": (STUDIO_INPUT_POLICY if apply_full_text else
                             "zonos_shorts_whole_240_v1" if str(job.get("slug", "")).startswith("zonos2-")
                             and job.get("request_segment_count") == 1 else "engine_config"),
            "request_segment_count": job.get("request_segment_count"),
            "generation": generation,
        }
        return {**job, "config_json": json_dumps(config)}, synthesis

    def _continuity_context(self, job: dict) -> tuple[dict | None, str]:
        """Use one verified hop within this request; never accumulate synthetic context."""
        config = json.loads(job["config_json"] or "{}")
        if config.get("continuity_mode") != "anchored":
            return None, "disabled"
        if not self._quality_control_enabled(job) or not config.get("worker"):
            return None, "requires_hq_worker"
        if job.get("semantic_role") in {"transition", "conclusion"}:
            return None, "semantic_boundary"
        previous = self.db.fetch_one(
            """SELECT ss.text,ss.paragraph_index,ss.paragraph_end,j.id AS job_id,
                      j.status,j.engine_id,r.output_path,r.output_sha256,r.duration_seconds,
                      r.metrics_json,r.manifest_json
               FROM studio_segments ss JOIN jobs j ON j.id=ss.job_id
               LEFT JOIN runs r ON r.job_id=j.id
               WHERE ss.request_id=? AND ss.position=?""",
            (job["studio_request_id"], int(job.get("segment_position") or 1) - 1),
        )
        if not previous or previous["status"] != "succeeded":
            return None, "no_verified_previous_segment"
        if previous["paragraph_end"] or previous["paragraph_index"] != job["paragraph_index"]:
            return None, "paragraph_boundary"
        metrics = json.loads(previous["metrics_json"] or "{}")
        quality = metrics.get("quality_control", {})
        if quality.get("status") not in {"passed", "regenerated_pass"}:
            return None, "previous_quality_unverified"
        selected_quality = next((
            attempt for attempt in quality.get("attempts", [])
            if attempt.get("attempt") == quality.get("selected_attempt")
        ), {})
        if (
            selected_quality.get("transcription") is None
            or selected_quality.get("retry_recommended", True)
            or selected_quality.get("wav", {}).get("hard_reject", True)
        ):
            return None, "previous_quality_unverified"
        if metrics.get("continuity", {}).get("used"):
            return None, "one_hop_reset"
        if not 3.0 <= float(previous["duration_seconds"] or 0) <= 20.0:
            return None, "context_duration_out_of_range"
        manifest = json.loads(previous["manifest_json"] or "{}")
        if (
            previous["engine_id"] != job["engine_id"]
            or manifest.get("engine", {}).get("config") != config
            or manifest.get("reference_sha256") != job["reference_sha256"]
            or manifest.get("reference_text") != job.get("reference_text", "")
            or manifest.get("normalized_text") != previous["text"]
        ):
            return None, "context_settings_changed"
        path = Path(previous["output_path"])
        try:
            if sha256_file(path) != previous["output_sha256"]:
                return None, "context_audio_changed"
        except OSError:
            return None, "context_audio_missing"
        return {
            "audio": str(path), "text": previous["text"], "mode": "anchored",
            "previous_job_id": previous["job_id"],
        }, ""

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
            quality_enabled = self._quality_control_enabled(job)
            retry_seeds = [
                seed for seed in QUALITY_RETRY_SEEDS if seed != int(job["seed"])
            ]
            context, reset_reason = self._continuity_context(job)
            synthesis_engine, synthesis = self._synthesis_engine(job)
            # A failed context attempt returns to the original reference at the SAME
            # seed before the existing fallback seeds. Production defaults are unchanged.
            attempt_plan = [(int(job["seed"]), context)]
            if context:
                attempt_plan.append((int(job["seed"]), None))
            attempt_plan.extend((seed, None) for seed in retry_seeds)
            candidates: list[dict] = []
            candidate_paths: list[Path] = []
            for attempt, (seed, attempt_context) in enumerate(attempt_plan, start=1):
                candidate_path = (
                    output_path
                    if attempt == 1
                    else output_path.with_name(
                        f"{output_path.stem}-qa-attempt-{attempt}-seed-{seed}.wav"
                    )
                )
                candidate_paths.append(candidate_path)
                generation_error = None
                try:
                    adapter = run_engine(
                        synthesis_engine,
                        EngineRequest(
                            text=job["normalized_text"],
                            reference=Path(job["reference_path"]),
                            reference_text=job.get("reference_text", ""),
                            output=candidate_path,
                            seed=seed,
                            continuation=(
                                {key: attempt_context[key] for key in ("audio", "text", "mode")}
                                if attempt_context else None
                            ),
                        ),
                    )
                except EngineError as error:
                    if not attempt_context:
                        raise
                    generation_error = str(error)
                    adapter = {"adapter": "context-generation-failed"}
                if generation_error is not None:
                    quality = {
                        "verifier": "generation-error", "retry_recommended": True,
                        "reasons": ["context_generation_failed"], "score": 10.0,
                        "wav": {"hard_reject": True}, "error": generation_error[-2000:],
                    }
                elif quality_enabled:
                    quality = self.quality_verifier.inspect(
                        job["normalized_text"], candidate_path
                    )
                else:
                    wav_metrics = inspect_wav(candidate_path)
                    quality = {
                        "verifier": "disabled",
                        "retry_recommended": bool(wav_metrics.get("hard_reject")),
                        "reasons": ["waveform_reject"] if wav_metrics.get("hard_reject") else [],
                        "score": 10.0 if wav_metrics.get("hard_reject") else 0.0,
                        "wav": wav_metrics,
                    }
                candidates.append(
                    {
                        "attempt": attempt,
                        "seed": seed,
                        "path": candidate_path,
                        "adapter": adapter,
                        "quality": quality,
                        "continuity_used": bool(attempt_context),
                    }
                )
                context_verified = (
                    quality.get("transcription") is not None
                    and not quality.get("retry_recommended")
                    and not quality["wav"].get("hard_reject")
                )
                candidates[-1]["context_verified"] = context_verified
                if not quality_enabled or (
                    not quality["retry_recommended"] and (not attempt_context or context_verified)
                ):
                    break
                if self._request_cancelled(job):
                    break

            # Unverified context must not win on a lower ASR score if all retries fail.
            safe_candidates = [
                item for item in candidates
                if not item["continuity_used"] or item["context_verified"]
            ]
            if not safe_candidates:
                raise RuntimeError("문맥 연결 검증이 중단되었습니다.")
            selected = choose_best_candidate(safe_candidates)
            if quality_enabled and job["slug"].startswith("zonos2-") and (
                selected["quality"].get("retry_recommended")
                or selected["quality"].get("transcription") is None
            ):
                # Do not deliver an unverified/failed ZONOS take as a success.
                # Retain diagnostics and attempts for investigation, not playback.
                report = output_dir / f"job-{job['id']}-quality-failed.json"
                report.write_text(json_dumps([
                    {"seed": c["seed"], "path": str(c["path"]), "quality": c["quality"]}
                    for c in candidates
                ]), encoding="utf-8")
                raise RuntimeError("ZONOS2 음성 검사를 통과하지 못했습니다. 검사 환경 또는 실패 기록을 확인하세요.")
            selected_seed = int(selected["seed"])
            selected_output_path = output_dir / (
                f"job-{job['id']}-{job['slug']}-seed-{selected_seed}.wav"
            )
            promote_candidate(selected["path"], selected_output_path)
            cleanup_candidates(candidate_paths, selected["path"], selected_output_path)
            output_path = selected_output_path
            if selected["quality"]["wav"].get("hard_reject"):
                raise RuntimeError("자동 재생성 후에도 WAV 파형 품질 검사를 통과하지 못했습니다.")
            adapter_result = selected["adapter"]
            sampler.stop()
            sampler.join(timeout=6)
            metrics = inspect_wav(output_path)
            metrics["wall_seconds"] = round(time.perf_counter() - started, 4)
            metrics["gpu"] = sampler.summary()
            if before.get("gpu"):
                metrics["gpu"]["memory_total_mb"] = float(before["gpu"].get("memory_total_mb") or 0)
            performance = adapter_result.get("worker", {})
            metrics["engine_performance"] = {
                key: performance[key] for key in ("generate_seconds", "realtime_factor", "dac_device")
                if key in performance
            }
            metrics["synthesis"] = synthesis
            quality_attempts = []
            for candidate in candidates:
                quality_attempts.append(
                    {
                        "attempt": candidate["attempt"],
                        "seed": candidate["seed"],
                        "continuity_used": candidate["continuity_used"],
                        **candidate["quality"],
                    }
                )
            if not quality_enabled:
                quality_status = "disabled"
            elif selected["quality"].get("verifier") == "waveform-only":
                quality_status = "waveform_only"
            elif selected["quality"].get("retry_recommended"):
                quality_status = "best_available"
            elif len(candidates) > 1:
                quality_status = "regenerated_pass"
            else:
                quality_status = "passed"
            metrics["quality_control"] = {
                "enabled": quality_enabled,
                "status": quality_status,
                "base_seed": int(job["seed"]),
                "selected_seed": selected_seed,
                "selected_attempt": selected["attempt"],
                "regenerated": len(candidates) > 1,
                "fallback_selected": selected_seed != int(job["seed"]),
                "attempt_count": len(candidates),
                "attempts": quality_attempts,
            }
            metrics["continuity"] = {
                "requested": json.loads(job["config_json"] or "{}").get("continuity_mode") == "anchored",
                "used": selected["continuity_used"],
                "mode": "anchored" if selected["continuity_used"] else None,
                "previous_job_id": context["previous_job_id"] if context else None,
                "reset_reason": (
                    "context_failed_verification" if context and not selected["continuity_used"]
                    else reset_reason
                ),
                "fallback_to_reference": bool(context and not selected["continuity_used"]),
            }
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
                "selected_seed": selected_seed,
                "quality_control": metrics["quality_control"],
                "continuity": metrics["continuity"],
                "synthesis": synthesis,
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
        request = self.db.fetch_one(
            "SELECT status,quality_mode FROM studio_requests WHERE id=?", (request_id,)
        )
        if request and request["status"] == "cancelled":
            return
        rows = self.db.fetch_all(
            """SELECT ss.position,ss.text,ss.paragraph_index,ss.semantic_role,
                      ss.paragraph_end,ss.pause_after_ms,j.status,j.error,r.output_path
               FROM studio_segments ss JOIN jobs j ON j.id=ss.job_id
               LEFT JOIN runs r ON r.job_id=j.id
               WHERE ss.request_id=? ORDER BY ss.position""",
            (request_id,),
        )
        chapter_paths: list[Path] = []
        chapter_boundary_texts: list[str] = []
        chapter_boundary_pauses: list[int | None] = []
        chapter_count = 0
        if request and request.get("quality_mode") == LONG_FORM_QUALITY_MODE:
            try:
                chapters = partition_studio_chapters(rows)
                chapter_count = len(chapters)
                for position, chapter in enumerate(chapters, start=1):
                    if not all(item["status"] == "succeeded" for item in chapter):
                        continue
                    chapter_output = (
                        self.paths.artifacts / "studio"
                        / f"request-{request_id}-chapter-{position:02d}.wav"
                    )
                    if not chapter_output.exists():
                        concatenate_wavs(
                            [Path(item["output_path"]) for item in chapter],
                            chapter_output,
                            boundary_texts=[item["text"] for item in chapter],
                            boundary_pauses_ms=[item.get("pause_after_ms") for item in chapter],
                        )
                    chapter_paths.append(chapter_output)
                    chapter_boundary_texts.append(chapter[-1]["text"])
                    chapter_boundary_pauses.append(chapter[-1].get("pause_after_ms"))
            except Exception as error:
                self.db.execute(
                    "UPDATE studio_requests SET error=? WHERE id=?",
                    (f"장 WAV 결합 실패: {error}", request_id),
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
            final_sources = (
                chapter_paths
                if chapter_count and len(chapter_paths) == chapter_count
                else [Path(row["output_path"]) for row in rows]
            )
            final_boundary_texts = (
                chapter_boundary_texts
                if chapter_count and len(chapter_paths) == chapter_count
                else [row["text"] for row in rows]
            )
            final_boundary_pauses = (
                chapter_boundary_pauses
                if chapter_count and len(chapter_paths) == chapter_count
                else [row.get("pause_after_ms") for row in rows]
            )
            concatenate_wavs(
                final_sources,
                output,
                boundary_texts=final_boundary_texts,
                boundary_pauses_ms=final_boundary_pauses,
            )
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
