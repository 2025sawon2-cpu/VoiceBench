from __future__ import annotations

import base64
import http.client
import json
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from voicebench.api import VoiceBenchServer
from voicebench.audio import inspect_wav, make_mock_wav
from voicebench.config import Paths
from voicebench.db import Database
from voicebench.runner import JobRunner
from voicebench.service import STUDIO_ENGINE_SLUG, Service, split_script


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
        )
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
        self.assertEqual(request["seed"], 10)
        self.assertGreater(request["segment_count"], 100)

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
            body = json.dumps({"text": "External agent test"}).encode("utf-8")
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
            self.assertEqual(stored["seed"], 10)
            self.assertEqual(stored["status"], "queued")

            connection = http.client.HTTPConnection(host, port, timeout=5)
            connection.request("GET", f"/v1/tts/{created['id']}", headers=bearer_headers)
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            status = json.loads(response.read())
            self.assertEqual(status["status"], "queued")
            self.assertIsNone(status["audio_url"])
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
