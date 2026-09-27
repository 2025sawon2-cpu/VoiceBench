from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.register_continuity import CONTINUITY_ENGINE_SLUG, register_continuity_variant
from voicebench.audio import inspect_wav, make_mock_wav
from voicebench.config import Paths
from voicebench.db import Database
from voicebench.engines import EngineError, EngineRequest, run_engine
from voicebench.runner import JobRunner
from voicebench.service import QWEN_EXPRESSIVE_SLUG as STUDIO_ENGINE_SLUG, STUDIO_INPUT_POLICY, Service, SpeechSegment


class ContinuityRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.paths = Paths(root, root / "data", root / "data/db.sqlite3",
                           root / "artifacts", root / "references", root / "outputs", root / "static")
        self.paths.ensure()
        self.db = Database(self.paths.database)
        self.service = Service(self.paths, self.db)
        reference = self.paths.data / "prepared/ref_voice/selected_reference.wav"
        reference.parent.mkdir(parents=True)
        make_mock_wav(reference, "차분하게 이야기하는 원본 목소리입니다.", 13)
        reference.with_suffix(".txt").write_text("차분하게 이야기하는 원본 목소리입니다.", encoding="utf-8")
        self.service.upsert_engine({
            "slug": STUDIO_ENGINE_SLUG, "name": "Production", "kind": "command",
            "version": "test", "config": {
                "worker": {"endpoint": "http://unused/synthesize"},
                "generation": {"temperature": 1.0, "top_p": 0.95},
            },
        })
        config = json.loads(self.db.fetch_one("SELECT config_json FROM engines WHERE slug=?", (STUDIO_ENGINE_SLUG,))["config_json"])
        self.service.upsert_engine({"slug":"zonos2-q8-experimental", "name":"Zonos", "kind":"command", "config":config})
        register_continuity_variant(self.paths, self.db)
        self.runner = JobRunner(self.paths, self.db)
        self.calls = []

    def tearDown(self):
        self.runner.quality_verifier.close()
        self.temp.cleanup()

    def request(self, count=3, engine=CONTINUITY_ENGINE_SLUG, quality_mode="long_form_hq"):
        segments = [SpeechSegment(
            f"이것은 {i + 1}번째 이야기이며 천천히 끝까지 정확하게 읽어야 하는 문장입니다.",
            1, 1, "statement", i == count - 1, 280,
        ) for i in range(count)]
        with patch("voicebench.service.plan_script", return_value=segments):
            return self.service.create_studio_request(
                " ".join(s.text for s in segments), engine_slug=engine, quality_mode=quality_mode,
            )

    def fake_engine(self, _engine, request):
        self.calls.append(request)
        make_mock_wav(request.output, request.text, request.seed)
        return {"adapter": "test"}

    def passed(self, text, path):
        return {
            "verifier": "whisper-base-cpu", "transcription": text,
            "retry_recommended": False, "reasons": [], "score": 0.0,
            "wav": inspect_wav(path),
        }

    def execute(self, count=None, engine=None, inspect=None):
        with patch("voicebench.runner.run_engine", side_effect=engine or self.fake_engine), patch.object(
            self.runner.quality_verifier, "inspect", side_effect=inspect or self.passed,
        ), patch("voicebench.runner.GpuSampler") as sampler, patch(
            "voicebench.runner.system_snapshot", return_value={},
        ):
            sampler.return_value.summary.return_value = {}
            executed = 0
            while count is None or executed < count:
                job = self.runner.claim()
                if not job:
                    break
                self.runner.execute_job(job)
                status = self.db.fetch_one("SELECT status,error FROM jobs WHERE id=?", (job["id"],))
                self.assertEqual(status["status"], "succeeded", status["error"])
                executed += 1

    def metrics(self):
        return [json.loads(row["metrics_json"]) for row in self.db.fetch_all("SELECT metrics_json FROM runs ORDER BY id")]

    def test_opt_in_registers_without_changing_default_and_resets_after_one_hop(self):
        request = self.request()
        self.execute()
        self.assertEqual([bool(c.continuation) for c in self.calls], [False, True, False])
        self.assertEqual([c.seed for c in self.calls], [13, 13, 13])
        self.assertEqual(self.calls[1].continuation["text"], self.calls[0].text)
        self.assertEqual(self.calls[1].continuation["mode"], "anchored")
        self.assertEqual(self.metrics()[2]["continuity"]["reset_reason"], "one_hop_reset")
        self.assertEqual(self.service.studio_request(request["id"])["status"], "succeeded")
        summary = self.service.studio_request(request["id"])["continuity"]
        self.assertEqual(summary["requested_segments"], 3)
        self.assertEqual(summary["used_segments"], 1)
        self.assertEqual(summary["reset_reason_counts"]["one_hop_reset"], 1)
        self.assertEqual(summary["fallback_segments"], 0)
        default = self.db.fetch_one("SELECT config_json FROM engines WHERE slug=?", (STUDIO_ENGINE_SLUG,))
        self.assertNotIn("continuity_mode", json.loads(default["config_json"]))

    def test_failed_context_retries_original_reference_with_same_seed(self):
        request = self.request(2)

        def inspect(text, path):
            quality = self.passed(text, path)
            if self.calls[-1].continuation:
                quality.update(retry_recommended=True, reasons=["likely_omission"], score=0.05)
            return quality

        self.execute(inspect=inspect)
        self.assertEqual([bool(c.continuation) for c in self.calls], [False, True, False])
        self.assertEqual([c.seed for c in self.calls], [13, 13, 13])
        metrics = self.metrics()[1]
        self.assertTrue(metrics["continuity"]["fallback_to_reference"])
        self.assertEqual(metrics["quality_control"]["selected_attempt"], 2)
        self.assertEqual(metrics["quality_control"]["status"], "regenerated_pass")
        summary = self.service.studio_request(request["id"])["continuity"]
        self.assertEqual(summary["used_segments"], 0)
        self.assertEqual(summary["fallback_segments"], 1)
        self.assertFalse(list(self.paths.outputs.rglob("*-qa-attempt-*.wav")))

    def test_unverified_context_cannot_win_even_if_all_reference_retries_fail(self):
        self.request(2)
        self.execute(1)

        def inspect(text, path):
            quality = self.passed(text, path)
            quality.update(retry_recommended=True, reasons=["likely_omission"], score=0.1 if self.calls[-1].continuation else 0.4)
            return quality

        self.execute(inspect=inspect)
        self.assertEqual([c.seed for c in self.calls], [13, 13, 13, 10, 42])
        self.assertFalse(self.metrics()[1]["continuity"]["used"])
        self.assertEqual(self.metrics()[1]["quality_control"]["status"], "best_available")
        self.assertEqual(self.metrics()[1]["quality_control"]["selected_attempt"], 2)

    def test_context_engine_error_falls_back_and_is_audited(self):
        self.request(2)

        def engine(job, request):
            if request.continuation:
                self.calls.append(request)
                raise EngineError("Context could not be encoded")
            return self.fake_engine(job, request)

        self.execute(engine=engine)
        self.assertEqual([c.seed for c in self.calls], [13, 13, 13])
        metrics = self.metrics()[1]
        self.assertEqual(metrics["quality_control"]["attempts"][0]["reasons"], ["context_generation_failed"])
        self.assertTrue(metrics["continuity"]["fallback_to_reference"])

    def test_losing_asr_during_context_generation_returns_to_original_reference(self):
        self.request(2)
        self.execute(1)

        def inspect(text, path):
            quality = self.passed(text, path)
            quality.update(verifier="waveform-only", transcription=None)
            return quality

        self.execute(inspect=inspect)
        self.assertEqual([bool(c.continuation) for c in self.calls], [False, True, False])
        self.assertEqual(self.metrics()[1]["quality_control"]["status"], "waveform_only")
        self.assertTrue(self.metrics()[1]["continuity"]["fallback_to_reference"])

    def test_context_guards_reject_changed_audio_reference_and_semantic_boundaries(self):
        self.request(2)
        self.execute(1)
        job = self.runner.claim()
        self.assertIsNotNone(self.runner._continuity_context(job)[0])
        cases = [
            ({"paragraph_index": 2}, "paragraph_boundary"),
            ({"semantic_role": "transition"}, "semantic_boundary"),
            ({"semantic_role": "conclusion"}, "semantic_boundary"),
            ({"reference_sha256": "changed"}, "context_settings_changed"),
        ]
        for changes, reason in cases:
            with self.subTest(changes=changes):
                self.assertEqual(self.runner._continuity_context({**job, **changes}), (None, reason))
        output = self.calls[0].output
        output.write_bytes(output.read_bytes() + b"changed")
        self.assertEqual(self.runner._continuity_context(job), (None, "context_audio_changed"))
        output.unlink()
        self.assertEqual(self.runner._continuity_context(job), (None, "context_audio_missing"))

    def test_default_and_non_hq_requests_never_send_continuation(self):
        self.request(2, engine=STUDIO_ENGINE_SLUG)
        self.request(2, quality_mode="standard")
        self.execute()
        self.assertTrue(all(c.continuation is None for c in self.calls))

    def test_worker_payload_includes_only_explicit_internal_context(self):
        wav = self.paths.outputs / "sample.wav"
        make_mock_wav(wav, "테스트 문장을 정확하게 읽어주세요.", 13)
        context = {"audio": str(wav), "text": "이전 문장입니다.", "mode": "anchored"}
        engine = {"kind": "command", "config_json": json.dumps({"worker": {"endpoint": "unused"}})}
        with patch("voicebench.engines._ensure_worker"), patch("voicebench.engines._json_request", return_value={}) as request:
            run_engine(engine, EngineRequest("다음 문장입니다.", wav, "원본입니다.", wav, 13))
            self.assertNotIn("continuation", request.call_args.args[1])
            run_engine(engine, EngineRequest("다음 문장입니다.", wav, "원본입니다.", wav, 13, continuation=context))
            self.assertEqual(request.call_args.args[1]["continuation"], context)

    def test_single_generation_web_and_external_use_selected_full_text_mode(self):
        text = "오늘은 평소보다 조금 천천히 걸어 보았어요. 익숙한 길에서도 새로운 풍경을 발견했거든요."
        self.service.create_studio_request(text, engine_slug=STUDIO_ENGINE_SLUG)
        self.service.create_studio_request(text, engine_slug=STUDIO_ENGINE_SLUG, source="external_api")
        original = self.db.fetch_one("SELECT config_json FROM engines WHERE slug=?", (STUDIO_ENGINE_SLUG,))["config_json"]
        payloads = []

        def worker(_url, payload, _timeout):
            payloads.append(payload)
            make_mock_wav(Path(payload["output"]), payload["text"], payload["seed"])
            return {"status": "ok"}

        with patch("voicebench.engines._ensure_worker"), patch("voicebench.engines._json_request", side_effect=worker):
            self.execute(engine=run_engine)
        self.assertEqual(len(payloads), 2)
        expected = {**json.loads(original)["generation"], "non_streaming_mode": True}
        for payload in payloads:
            self.assertEqual(payload["generation"], expected)
            self.assertEqual(payload["seed"], 13)
            self.assertNotIn("continuation", payload)
        self.assertEqual(payloads[0]["reference"], payloads[1]["reference"])
        self.assertEqual(payloads[0]["text"], payloads[1]["text"])
        for row in self.db.fetch_all("SELECT metrics_json,manifest_json FROM runs"):
            synthesis = json.loads(row["metrics_json"])["synthesis"]
            manifest = json.loads(row["manifest_json"])
            self.assertEqual(synthesis, {
                "input_policy": STUDIO_INPUT_POLICY,
                "request_segment_count": 1, "generation": expected,
            })
            self.assertEqual(manifest["synthesis"], synthesis)
            self.assertEqual(manifest["engine"]["config"], json.loads(original))
        self.assertEqual(self.db.fetch_one("SELECT config_json FROM engines WHERE slug=?", (STUDIO_ENGINE_SLUG,))["config_json"], original)

    def test_last_remaining_long_form_segment_keeps_original_input_mode(self):
        self.request(3, engine=STUDIO_ENGINE_SLUG)
        self.execute(2)
        last = self.runner.claim()
        self.assertEqual(last["request_segment_count"], 3)
        effective, synthesis = self.runner._synthesis_engine(last)
        self.assertEqual(json.loads(effective["config_json"]), json.loads(last["config_json"]))
        self.assertEqual(synthesis["input_policy"], "engine_config")
        self.assertTrue(all(m["synthesis"]["input_policy"] == "engine_config" for m in self.metrics()))

    def test_short_input_policy_preserves_comparisons_standard_and_benchmarks(self):
        self.request(1, engine=STUDIO_ENGINE_SLUG)
        job = self.runner.claim()
        config = json.loads(job["config_json"])
        cases = [
            {"slug": CONTINUITY_ENGINE_SLUG},
            {"studio_quality_mode": "standard"},
            {"studio_request_id": None},
            {"request_segment_count": None},
            {"kind": "mock"},
            {"config_json": json.dumps({"generation": config["generation"]})},
        ]
        for value in (False, True):
            cases.append({"config_json": json.dumps({
                **config, "generation": {**config["generation"], "non_streaming_mode": value},
            })})
        for changes in cases:
            with self.subTest(changes=changes):
                source = {**job, **changes}
                effective, synthesis = self.runner._synthesis_engine(source)
                self.assertEqual(json.loads(effective["config_json"]), json.loads(source["config_json"]))
                self.assertEqual(synthesis["input_policy"], "engine_config")

    def test_short_full_text_mode_is_retained_on_quality_retry(self):
        self.request(1, engine=STUDIO_ENGINE_SLUG)
        generations = []

        def engine(job, request):
            generations.append(json.loads(job["config_json"])["generation"])
            return self.fake_engine(job, request)

        def inspect(text, path):
            quality = self.passed(text, path)
            if len(self.calls) == 1:
                quality.update(retry_recommended=True, reasons=["likely_omission"], score=0.3)
            return quality

        self.execute(engine=engine, inspect=inspect)
        self.assertEqual([c.seed for c in self.calls], [13, 10])
        self.assertTrue(all(g["non_streaming_mode"] is True for g in generations))
        self.assertEqual(self.metrics()[0]["quality_control"]["status"], "regenerated_pass")

    def test_external_cache_key_tracks_server_input_policy(self):
        text = "같은 대본도 입력 방식이 바뀌면 새로 생성해야 합니다."
        with patch("voicebench.service.STUDIO_INPUT_POLICY", "previous_policy"):
            previous = self.service.create_external_tts_request(text)
            self.assertEqual(self.service.reusable_external_tts_request(text)["id"], previous["id"])
        self.assertIsNone(self.service.reusable_external_tts_request(text))
        current = self.service.create_external_tts_request(text)
        self.assertNotEqual(current["id"], previous["id"])
        self.assertEqual(self.service.reusable_external_tts_request(text)["id"], current["id"])


if __name__ == "__main__":
    unittest.main()
