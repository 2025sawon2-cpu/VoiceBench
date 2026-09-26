from __future__ import annotations

import base64
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.compare_prosody import SAMPLE
from tools.compare_shorts import INPUT_MODE_VARIANTS, experiment_spec, fingerprint, validate_resume
from voicebench.api import VoiceBenchServer
from voicebench.audio import make_mock_wav
from voicebench.config import Paths
from voicebench.db import Database
from voicebench.service import Service
from voicebench.util import sha256_file


class StudioPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.paths = Paths(root, root / "data", root / "data/db.sqlite3",
                           root / "artifacts", root / "references", root / "outputs", root / "static")
        self.paths.ensure()
        self.db = Database(self.paths.database)
        self.service = Service(self.paths, self.db)

    def tearDown(self):
        self.temp.cleanup()

    def test_preview_is_read_only_and_does_not_count_each_sentence_as_generation(self):
        before = {table: self.db.fetch_all(f"SELECT * FROM {table}") for table in ("projects", "prompts", "jobs", "studio_requests")}
        preview = self.service.preview_studio_script(SAMPLE)
        self.assertEqual((preview["segment_count"], preview["join_count"]), (1, 0))
        self.assertEqual(preview["segments"][0]["text"], SAMPLE)
        empty = self.service.preview_studio_script(" \n ")
        self.assertEqual(empty["segment_count"], 0)
        self.assertEqual(empty["join_count"], 0)
        for table, rows in before.items():
            self.assertEqual(self.db.fetch_all(f"SELECT * FROM {table}"), rows)

    def test_preview_matches_persisted_generation_boundaries(self):
        prepared = self.paths.data / "prepared/ref_voice"
        prepared.mkdir(parents=True)
        reference = prepared / "selected_reference.wav"
        make_mock_wav(reference, "reference", 13)
        reference.with_suffix(".txt").write_text("reference", encoding="utf-8")
        for text in (SAMPLE, SAMPLE + "\n\n" + SAMPLE, "정말 그럴까요? " + SAMPLE + " 하지만 아직 끝이 아닙니다."):
            with self.subTest(text=text):
                preview = self.service.preview_studio_script(text)
                created = self.service.create_studio_request(text, engine_slug="mock-a")
                rows = self.db.fetch_all(
                    "SELECT position,text,paragraph_index,semantic_role,pause_after_ms FROM studio_segments WHERE request_id=? ORDER BY position",
                    (created["id"],),
                )
                self.assertEqual(len(rows), preview["segment_count"])
                for actual, expected in zip(rows, preview["segments"]):
                    self.assertEqual(actual, {key: expected[key] for key in actual})

    def test_preview_endpoint_uses_web_auth_and_rejects_non_text(self):
        with patch.dict("os.environ", {"VOICEBENCH_ACCESS_KEY": "preview-test-key"}):
            server = VoiceBenchServer(("127.0.0.1", 0), self.paths)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            token = base64.b64encode(b"voice:preview-test-key").decode("ascii")
            for headers, text, expected in (
                ({}, SAMPLE, 401),
                ({"Authorization": f"Basic {token}"}, SAMPLE, 200),
                ({"Authorization": f"Basic {token}"}, [SAMPLE], 400),
            ):
                connection = http.client.HTTPConnection(*server.server_address, timeout=5)
                connection.request("POST", "/api/studio/preview", json.dumps({"text": text}), {"Content-Type": "application/json", **headers})
                response = connection.getresponse()
                body = response.read()
                connection.close()
                self.assertEqual(response.status, expected)
                if expected == 200:
                    self.assertEqual(json.loads(body)["segment_count"], 1)
            self.assertEqual(self.db.fetch_all("SELECT id FROM jobs"), [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_comparison_resume_rejects_changed_settings_and_wavs(self):
        destination = self.paths.data
        raw, audio = destination / "raw.wav", destination / "audio.wav"
        make_mock_wav(raw, "reference", 13)
        audio.write_bytes(raw.read_bytes())
        spec = {"plans": {"story": []}, "generation": {"expressive": {"temperature": 1.0}}}
        row = {"raw": raw.name, "audio": audio.name, "raw_sha256": sha256_file(raw), "sha256": sha256_file(audio)}
        report = {"spec": spec, "fingerprint": fingerprint(spec), "results": {"story": {"expressive": row}}}
        validate_resume(report, spec, destination)
        with self.assertRaisesRegex(ValueError, "inputs/runtime changed"):
            validate_resume(report, {**spec, "seed": 42}, destination)
        audio.write_bytes(audio.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "WAV content changed"):
            validate_resume(report, spec, destination)

    def test_input_mode_experiment_holds_sampling_and_real_plan_fixed(self):
        reference = self.paths.references / "reference.wav"
        make_mock_wav(reference, "reference", 13)
        reference.with_suffix(".txt").write_text("reference", encoding="utf-8")
        generation = {"temperature": 1.0, "subtalker_temperature": 1.0, "top_p": 0.95}
        config = {"worker": {"model": "test"}, "generation": generation}
        spec = experiment_spec(config, reference, {"story": SAMPLE}, INPUT_MODE_VARIANTS)
        self.assertEqual(spec["attention"], "eager")
        config["worker"]["attention"] = "sdpa"
        sdpa_spec = experiment_spec(config, reference, {"story": SAMPLE}, INPUT_MODE_VARIANTS)
        self.assertEqual(sdpa_spec["attention"], "sdpa")
        self.assertNotEqual(fingerprint(spec), fingerprint(sdpa_spec))
        self.assertEqual(len(spec["plans"]["story"]), 1)
        self.assertEqual(spec["plans"]["story"][0]["text"], SAMPLE)
        for name, full_text in (("streaming_text", False), ("full_text", True)):
            self.assertEqual(spec["generation"][name], {**generation, "non_streaming_mode": full_text})
        self.assertNotIn("non_streaming_mode", config["generation"])
        with self.assertRaisesRegex(ValueError, "one real Studio generation"):
            experiment_spec(config, reference, {"story": SAMPLE + "\n\n" + SAMPLE}, INPUT_MODE_VARIANTS)


if __name__ == "__main__":
    unittest.main()
