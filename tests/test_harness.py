from __future__ import annotations

import base64
import http.client
import json
import math
import struct
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from voicebench.api import VoiceBenchServer
from voicebench.audio import concatenate_wavs, inspect_wav, make_mock_wav
from voicebench.config import Paths
from voicebench.db import Database
from voicebench.runner import JobRunner
from voicebench.quality import (
    character_error_rate,
    choose_best_candidate,
    evaluate_candidate,
    normalize_for_quality,
)
from voicebench.service import STUDIO_ENGINE_SLUG, Service, plan_script, split_script
from tools import voicebench_mcp


def test_paths(root: Path) -> Paths:
    data = root / "data"
    return Paths(
        root=root,
        data=data,
        database=data / "voicebench.sqlite3",
        artifacts=data / "artifacts",
        references=data / "artifacts" / "references",
        outputs=data / "artifacts" / "outputs",
        static=Path(__file__).parents[1] / "voicebench" / "static",
    )


class HarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.paths = test_paths(Path(self.temp.name))
        self.paths.ensure()
        self.database = Database(self.paths.database)
        self.service = Service(self.paths, self.database)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def reference_bytes(self) -> bytes:
        path = self.paths.data / "source.wav"
        make_mock_wav(path, "레퍼런스 음성", 11)
        return path.read_bytes()

    def test_audio_inspection(self) -> None:
        path = self.paths.data / "audio.wav"
        make_mock_wav(path, "품질 검사", 7)
        metrics = inspect_wav(path)
        self.assertEqual(metrics["sample_rate"], 24000)
        self.assertGreater(metrics["duration_seconds"], 1)
        self.assertFalse(metrics["hard_reject"])

    def test_quality_evaluator_detects_clear_omission_and_selects_best_candidate(self) -> None:
        expected = "궁금하면 댓글 남겨주세요?"
        wav = {"hard_reject": False, "duration_seconds": 2.4}
        omitted = evaluate_candidate(expected, "궁금하면 댓글", wav)
        complete = evaluate_candidate(expected, "궁금하면 댓글 남겨주세요", wav)
        self.assertTrue(omitted["retry_recommended"])
        self.assertIn("likely_omission", omitted["reasons"])
        self.assertFalse(complete["retry_recommended"])
        selected = choose_best_candidate(
            [
                {"attempt": 1, "seed": 13, "quality": omitted},
                {"attempt": 2, "seed": 10, "quality": complete},
            ]
        )
        self.assertEqual(selected["seed"], 10)

    def test_quality_text_normalization_ignores_spacing_and_punctuation(self) -> None:
        self.assertEqual(normalize_for_quality("안녕하세요! 오늘 입니다."), "안녕하세요오늘입니다")
        self.assertEqual(character_error_rate("댓글 남겨주세요?", "댓글남겨주세요."), 0)

    def test_studio_quality_control_retries_and_records_selected_seed(self) -> None:
        prepared = self.paths.data / "prepared" / "ref_voice"
        prepared.mkdir(parents=True)
        reference = prepared / "selected_reference.wav"
        make_mock_wav(reference, "Quality reference", 7)
        reference.with_suffix(".txt").write_text("Quality reference", encoding="utf-8")
        self.service.upsert_engine(
            {
                "slug": "qwen3-tts-quality-test",
                "name": "Qwen quality test",
                "kind": "command",
                "version": "test",
                "config": {"command": ["unused"]},
            }
        )
        request = self.service.create_studio_request(
            "마지막까지 정확하게 읽어주세요.",
            engine_slug="qwen3-tts-quality-test",
        )
        runner = JobRunner(self.paths, self.database)
        calls: list[int] = []

        def fake_engine(_engine, engine_request):
            calls.append(engine_request.seed)
            make_mock_wav(engine_request.output, engine_request.text, engine_request.seed)
            return {"adapter": "test", "seed": engine_request.seed}

        failed_quality = {
            "verifier": "whisper-base-cpu",
            "retry_recommended": True,
            "reasons": ["likely_ending_omission"],
            "score": 0.7,
            "wav": {"hard_reject": False},
        }
        passed_quality = {
            "verifier": "whisper-base-cpu",
            "retry_recommended": False,
            "reasons": [],
            "score": 0.0,
            "wav": {"hard_reject": False},
        }
        with patch("voicebench.runner.run_engine", side_effect=fake_engine), patch.object(
            runner.quality_verifier,
            "inspect",
            side_effect=[failed_quality, passed_quality],
        ):
            runner.start()
            deadline = time.time() + 10
            while time.time() < deadline:
                current = self.service.studio_request(request["id"])
                if current["status"] in {"succeeded", "failed"}:
                    break
                time.sleep(0.05)
            runner.stop()
            runner.join(timeout=3)

        self.assertEqual(calls, [13, 10])
        completed = self.service.studio_request(request["id"])
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["quality_control"]["regenerated_segments"], 1)
        self.assertEqual(completed["quality_control"]["fallback_selected_segments"], 1)
        self.assertEqual(completed["quality_control"]["selected_seed_counts"], {"10": 1})
        run = self.database.fetch_one(
            "SELECT output_path,metrics_json,manifest_json FROM runs ORDER BY id DESC LIMIT 1"
        )
        metrics = json.loads(run["metrics_json"])
        manifest = json.loads(run["manifest_json"])
        self.assertEqual(metrics["quality_control"]["selected_seed"], 10)
        self.assertEqual(metrics["quality_control"]["attempt_count"], 2)
        self.assertEqual(manifest["selected_seed"], 10)
        self.assertTrue(run["output_path"].endswith("seed-10.wav"))

    def test_end_to_end_queue_and_blind_comparison(self) -> None:
        project = self.service.create_project("테스트")
        self.service.upload_reference(project["id"], "reference.wav", self.reference_bytes())
        self.service.add_prompt(project["id"], "오늘은 음성 품질을 비교합니다.", "general")
        engines = self.database.fetch_all("SELECT id FROM engines ORDER BY id")
        queued = self.service.queue_benchmark(project["id"], [row["id"] for row in engines], [42])
        self.assertEqual(queued["queued"], 2)

        runner = JobRunner(self.paths, self.database)
        runner.start()
        deadline = time.time() + 10
        while time.time() < deadline:
            counts = self.database.fetch_one(
                "SELECT COUNT(*) AS count FROM jobs WHERE status IN ('queued','running')"
            )["count"]
            if counts == 0:
                break
            time.sleep(0.1)
        runner.stop()
        runner.join(timeout=3)

        runs = self.service.runs(project["id"])
        self.assertEqual(len(runs), 2)
        self.assertTrue(all(not row["metrics"]["hard_reject"] for row in runs))
        comparison = self.service.next_comparison(project["id"])
        self.assertIsNotNone(comparison)
        rated = self.service.rate_comparison(comparison["id"], "left", "선명함")
        self.assertEqual(rated["winner"], "left")

    def test_engine_can_require_reference_transcript(self) -> None:
        project = self.service.create_project("레퍼런스 대본 검사")
        self.service.upload_reference(project["id"], "reference.wav", self.reference_bytes())
        self.service.add_prompt(project["id"], "새 문장을 읽습니다.")
        engine = self.service.upsert_engine(
            {
                "slug": "transcript-required",
                "name": "Transcript Required",
                "kind": "command",
                "config": {
                    "command": ["unused"],
                    "requires_reference_text": True,
                },
            }
        )

        with self.assertRaisesRegex(ValueError, "정확한 대본"):
            self.service.queue_benchmark(project["id"], [engine["id"]], [7])

        self.service.update_reference_text(project["id"], "이 문장을 정확히 읽었습니다.")
        queued = self.service.queue_benchmark(project["id"], [engine["id"]], [7])
        self.assertEqual(queued["queued"], 1)

    def test_studio_splits_and_combines_a_long_request(self) -> None:
        prepared = self.paths.data / "prepared" / "ref_voice"
        prepared.mkdir(parents=True)
        reference = prepared / "selected_reference.wav"
        make_mock_wav(reference, "내 목소리 레퍼런스", 3)
        reference.with_suffix(".txt").write_text(
            "이 목소리가 자연스럽게 유지되었으면 좋겠습니다.\n", encoding="utf-8"
        )
        text = (
            "첫 번째 문장은 자연스럽게 읽습니다. "
            "두 번째 문장에는 조금 더 긴 설명을 넣어도 안전하게 나누어야 합니다. "
            "세 번째 문장은 서로 다른 음성 조각의 순서를 확인하기 위해 충분한 길이로 작성합니다. "
            "마지막 문장을 하나의 파일로 결합합니다."
        ) * 2
        self.assertGreaterEqual(len(split_script(text, maximum=45)), 3)
        request = self.service.create_studio_request(text, seed=7, engine_slug="mock-a")
        self.assertGreaterEqual(request["segment_count"], 2)

        runner = JobRunner(self.paths, self.database)
        runner.start()
        deadline = time.time() + 10
        while time.time() < deadline:
            current = self.service.studio_request(request["id"])
            if current["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.1)
        runner.stop()
        runner.join(timeout=3)

        completed = self.service.studio_request(request["id"])
        self.assertEqual(completed["status"], "succeeded")
        self.assertTrue(self.service.studio_audio_path(request["id"]).exists())
        self.assertEqual(completed["quality_mode"], "long_form_hq")
        self.assertEqual(completed["chapters"][0]["status"], "succeeded")
        self.assertTrue(self.service.studio_chapter_audio_path(request["id"], 1).exists())
        self.assertGreater(completed["duration_seconds"], 1)

    def test_studio_has_no_fixed_character_limit(self) -> None:
        prepared = self.paths.data / "prepared" / "ref_voice"
        prepared.mkdir(parents=True)
        reference = prepared / "selected_reference.wav"
        make_mock_wav(reference, "Long script reference", 7)
        reference.with_suffix(".txt").write_text("Long script reference", encoding="utf-8")

        long_text = "가" * 10_001
        request = self.service.create_studio_request(
            long_text,
            engine_slug="mock-a",
        )
        self.assertEqual(len(request["original_text"]), 10_001)
        self.assertEqual(request["seed"], 13)
        self.assertGreater(request["segment_count"], 40)
        self.assertLessEqual(
            max(
                len(row["text"])
                for row in self.database.fetch_all(
                    "SELECT text FROM studio_segments WHERE request_id=?", (request["id"],)
                )
            ),
            240,
        )
        self.assertEqual(request["quality_mode"], "long_form_hq")
        self.assertGreater(len(request["chapters"]), 1)

    def test_studio_preserves_sentence_boundaries_for_repeatable_phrases(self) -> None:
        text = "따뜻한 밥이 좋았어요. 궁금하면 댓글 남겨주세요?"
        self.assertEqual(
            split_script(text, preserve_sentences=True),
            ["따뜻한 밥이 좋았어요.", "궁금하면 댓글 남겨주세요?"],
        )
        self.assertEqual(split_script(text), [text])

    def test_studio_prefers_long_sentence_and_paragraph_boundaries(self) -> None:
        first = "첫 문장은 충분히 자연스럽게 끝납니다."
        second = "두 번째 문장도 중간에 자르지 않고 마지막까지 읽습니다."
        third = "새 문단은 이전 청크와 합치지 않습니다."
        text = f"{first} {second}\n\n{third}"
        chunks = split_script(text, maximum=100)
        self.assertEqual(chunks, [f"{first} {second}", third])
        long_sentence = (
            "아주 긴 문장에서는 앞부분을 설명하고, 중간의 자연스러운 쉼표를 경계로 나누며, "
            "마지막 절의 문장부호까지 보존합니다."
        )
        pieces = split_script(long_sentence, maximum=45, preserve_sentences=True)
        self.assertGreater(len(pieces), 1)
        self.assertTrue(pieces[0].endswith(","))

    def test_speech_plan_marks_questions_transitions_and_paragraph_breaths(self) -> None:
        text = (
            "평생을 앞만 보고 달려온 우리 부부는 은퇴 생활비가 얼마나 필요할지 자주 고민합니다. "
            "이 숫자를 들으면 어떤 생각이 드시나요?\n\n"
            "그렇다면 막연한 불안보다 우리 부부의 실제 지출부터 차분히 살펴보겠습니다."
        )
        plan = plan_script(text, maximum=100)
        self.assertEqual(max(item.paragraph_index for item in plan), 2)
        self.assertTrue(any(item.semantic_role == "question" for item in plan))
        self.assertTrue(any(item.semantic_role == "transition" for item in plan))
        first_paragraph_end = next(
            item for item in plan if item.paragraph_index == 1 and item.paragraph_end
        )
        self.assertGreaterEqual(first_paragraph_end.pause_after_ms, 420)
        transition = next(item for item in plan if item.semantic_role == "transition")
        self.assertEqual(transition.paragraph_index, 2)

    def test_wav_join_normalizes_edge_silence_and_uses_punctuation_pause(self) -> None:
        def padded_tone(path: Path, leading_ms: int, trailing_ms: int) -> None:
            sample_rate = 24000
            leading = round(sample_rate * leading_ms / 1000)
            tone = round(sample_rate * 0.5)
            trailing = round(sample_rate * trailing_ms / 1000)
            samples = [0] * leading
            samples.extend(
                round(math.sin(2 * math.pi * 180 * index / sample_rate) * 6000)
                for index in range(tone)
            )
            samples.extend([0] * trailing)
            path.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(path), "wb") as target:
                target.setnchannels(1)
                target.setsampwidth(2)
                target.setframerate(sample_rate)
                target.writeframes(struct.pack(f"<{len(samples)}h", *samples))

        first = self.paths.data / "padded-first.wav"
        second = self.paths.data / "padded-second.wav"
        padded_tone(first, 300, 400)
        padded_tone(second, 500, 300)
        period_output = self.paths.data / "period-join.wav"
        comma_output = self.paths.data / "comma-join.wav"
        planned_output = self.paths.data / "planned-join.wav"
        concatenate_wavs(
            [first, second], period_output, boundary_texts=["문장입니다.", "다음 문장입니다."]
        )
        concatenate_wavs(
            [first, second], comma_output, boundary_texts=["이어서,", "계속합니다."]
        )
        concatenate_wavs(
            [first, second],
            planned_output,
            boundary_texts=["문단을 마칩니다.", "새 문단입니다."],
            boundary_pauses_ms=[500, 280],
        )
        period_duration = inspect_wav(period_output)["duration_seconds"]
        comma_duration = inspect_wav(comma_output)["duration_seconds"]
        planned_duration = inspect_wav(planned_output)["duration_seconds"]
        self.assertGreater(period_duration, 1.32)
        self.assertLess(period_duration, 1.40)
        self.assertAlmostEqual(period_duration - comma_duration, 0.11, places=2)
        self.assertAlmostEqual(planned_duration - period_duration, 0.22, places=2)

    def test_studio_request_can_be_cancelled_and_running_work_is_recovered(self) -> None:
        prepared = self.paths.data / "prepared" / "ref_voice"
        prepared.mkdir(parents=True)
        reference = prepared / "selected_reference.wav"
        make_mock_wav(reference, "Queue reference", 7)
        reference.with_suffix(".txt").write_text("Queue reference", encoding="utf-8")
        request = self.service.create_studio_request("문장 하나입니다. 문장 둘입니다.", engine_slug="mock-a")
        job_id = self.database.fetch_one(
            "SELECT job_id FROM studio_segments WHERE request_id=? ORDER BY position LIMIT 1",
            (request["id"],),
        )["job_id"]
        self.database.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
        self.assertEqual(self.service.recover_interrupted_jobs(), 1)
        self.assertEqual(
            self.database.fetch_one("SELECT status FROM jobs WHERE id=?", (job_id,))["status"],
            "queued",
        )
        cancelled = self.service.cancel_studio_request(request["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        remaining = self.database.fetch_one(
            "SELECT COUNT(*) AS count FROM jobs WHERE status='queued'"
        )["count"]
        self.assertEqual(remaining, 0)

        single = self.service.create_studio_request("한 조각 요청", engine_slug="mock-a")
        single_job = self.database.fetch_one(
            "SELECT job_id FROM studio_segments WHERE request_id=?",
            (single["id"],),
        )["job_id"]
        self.database.execute("UPDATE jobs SET status='running' WHERE id=?", (single_job,))
        self.service.cancel_studio_request(single["id"])
        self.database.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (single_job,))
        JobRunner(self.paths, self.database).refresh_studio_request(single_job)
        self.assertEqual(self.service.studio_request(single["id"])["status"], "cancelled")

    def test_optional_access_key_protects_public_server(self) -> None:
        with patch.dict("os.environ", {"VOICEBENCH_ACCESS_KEY": "correct-horse-battery"}):
            server = VoiceBenchServer(("127.0.0.1", 0), self.paths)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        try:
            connection = http.client.HTTPConnection(host, port, timeout=5)
            connection.request("GET", "/api/state")
            response = connection.getresponse()
            self.assertEqual(response.status, 401)
            self.assertIn("Basic", response.getheader("WWW-Authenticate"))
            response.read()
            connection.close()

            token = base64.b64encode(b"voice:correct-horse-battery").decode("ascii")
            connection = http.client.HTTPConnection(host, port, timeout=5)
            connection.request("GET", "/api/state", headers={"Authorization": f"Basic {token}"})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_file_responses_support_browser_byte_ranges(self) -> None:
        server = VoiceBenchServer(("127.0.0.1", 0), self.paths)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        try:
            connection = http.client.HTTPConnection(host, port, timeout=5)
            connection.request("GET", "/app.js", headers={"Range": "bytes=0-9"})
            response = connection.getresponse()
            content = response.read()
            expected_size = (self.paths.static / "app.js").stat().st_size
            self.assertEqual(response.status, 206)
            self.assertEqual(response.getheader("Accept-Ranges"), "bytes")
            self.assertEqual(response.getheader("Content-Range"), f"bytes 0-9/{expected_size}")
            self.assertEqual(len(content), 10)
            connection.close()

            connection = http.client.HTTPConnection(host, port, timeout=5)
            connection.request("HEAD", "/app.js")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Accept-Ranges"), "bytes")
            self.assertEqual(int(response.getheader("Content-Length")), expected_size)
            self.assertEqual(response.read(), b"")
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_external_api_uses_separate_bearer_auth_and_source(self) -> None:
        prepared = self.paths.data / "prepared" / "ref_voice"
        prepared.mkdir(parents=True)
        reference = prepared / "selected_reference.wav"
        make_mock_wav(reference, "External API reference", 7)
        reference.with_suffix(".txt").write_text("External API reference", encoding="utf-8")

        external_key = "vb_test_external_key_12345678901234567890"
        with patch.dict(
            "os.environ",
            {
                "VOICEBENCH_ACCESS_KEY": "admin-password",
                "VOICEBENCH_EXTERNAL_API_KEY": external_key,
            },
        ):
            server = VoiceBenchServer(("127.0.0.1", 0), self.paths)
        server.service.upsert_engine(
            {
                "slug": STUDIO_ENGINE_SLUG,
                "name": "External API Test Engine",
                "kind": "mock",
                "version": "test",
                "config": {},
            }
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        bearer_headers = {
            "Authorization": f"Bearer {external_key}",
            "Content-Type": "application/json",
        }
        try:
            connection = http.client.HTTPConnection(host, port, timeout=5)
            connection.request("GET", "/api/state", headers=bearer_headers)
            response = connection.getresponse()
            self.assertEqual(response.status, 401)
            response.read()
            connection.close()

            connection = http.client.HTTPConnection(host, port, timeout=5)
            body = json.dumps(
                {
                    "text": "External agent test", "seed": "this value must be ignored",
                    "engine_slug": "mock-b", "quality_mode": "standard",
                    "generation": {"non_streaming_mode": True},
                    "continuation": {"audio": "untrusted.wav", "text": "ignored"},
                }
            ).encode("utf-8")
            connection.request("POST", "/v1/tts", body=body, headers=bearer_headers)
            response = connection.getresponse()
            self.assertEqual(response.status, 202)
            created = json.loads(response.read())
            connection.close()

            stored = self.database.fetch_one(
                "SELECT source,seed,status FROM studio_requests WHERE id=?",
                (created["id"],),
            )
            self.assertEqual(stored["source"], "external_api")
            self.assertEqual(stored["seed"], 13)
            self.assertEqual(stored["status"], "queued")

            connection = http.client.HTTPConnection(host, port, timeout=5)
            connection.request("POST", "/v1/tts", body=body, headers=bearer_headers)
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            reused = json.loads(response.read())
            self.assertTrue(reused["reused"])
            self.assertEqual(reused["id"], created["id"])
            self.assertEqual(
                self.database.fetch_one(
                    "SELECT COUNT(*) AS count FROM studio_requests WHERE source='external_api'"
                )["count"],
                1,
            )
            connection.close()

            connection = http.client.HTTPConnection(host, port, timeout=5)
            connection.request("GET", f"/v1/tts/{created['id']}", headers=bearer_headers)
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            status = json.loads(response.read())
            self.assertEqual(status["status"], "queued")
            self.assertIsNone(status["audio_url"])
            self.assertEqual(status["quality_mode"], "long_form_hq")
            self.assertEqual(len(status["chapters"]), 1)
            self.assertIsNone(status["chapters"][0]["audio_url"])
            self.assertEqual(status["download_count"], 0)
            self.assertIsNone(status["last_downloaded_at"])
            connection.close()

            output = self.paths.artifacts / "studio" / f"request-{created['id']}.wav"
            make_mock_wav(output, "External completed audio", 13)
            self.database.execute(
                """UPDATE studio_requests SET status='succeeded',output_path=?,finished_at=?
                   WHERE id=?""",
                (str(output), "2026-08-17T00:00:00+00:00", created["id"]),
            )
            connection = http.client.HTTPConnection(host, port, timeout=5)
            connection.request(
                "GET", f"/v1/tts/{created['id']}/audio", headers=bearer_headers
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertGreater(len(response.read()), 44)
            connection.close()
            deadline = time.time() + 2
            delivered = None
            while time.time() < deadline:
                delivered = self.database.fetch_one(
                    "SELECT download_count,last_downloaded_at FROM studio_requests WHERE id=?",
                    (created["id"],),
                )
                if delivered["download_count"]:
                    break
                time.sleep(0.01)
            self.assertEqual(delivered["download_count"], 1)
            self.assertIsNotNone(delivered["last_downloaded_at"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_mcp_long_requests_return_persistent_id_without_waiting(self) -> None:
        definitions = voicebench_mcp.tool_definitions()
        self.assertTrue(
            all("seed" not in item["inputSchema"]["properties"] for item in definitions)
        )
        submitted = {
            "id": 65,
            "status": "queued",
            "segment_count": 75,
            "reused": False,
        }
        with patch.object(voicebench_mcp, "submit_tts", return_value=submitted), patch.object(
            voicebench_mcp, "wait_for_tts"
        ) as wait:
            response = voicebench_mcp.handle_tool(
                "voicebench_tts",
                {"text": "긴 대본", "output_path": r"C:\Downloads\voice.wav"},
            )
        wait.assert_not_called()
        result = json.loads(response["content"][0]["text"])
        self.assertTrue(result["async"])
        self.assertEqual(result["id"], 65)
        self.assertEqual(result["next_tool"], "voicebench_tts_status")
        self.assertEqual(result["next_arguments"]["request_id"], 65)

    def test_mcp_wait_timeout_returns_status_instead_of_raising(self) -> None:
        with patch.object(voicebench_mcp.time, "monotonic", side_effect=[0, 11]):
            result = voicebench_mcp.wait_for_tts(77, 10)
        self.assertEqual(result["id"], 77)
        self.assertEqual(result["status"], "queued")
        self.assertTrue(result["wait_timed_out"])
        self.assertIn("Do not submit", result["message"])


if __name__ == "__main__":
    unittest.main()
