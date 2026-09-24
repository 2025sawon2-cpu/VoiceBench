from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adapters.voxcpm2_worker import generation_arguments, validate_request
from tools.register_voxcpm2 import SLUG, STYLE_PRESETS, engine_spec
from voicebench.audio import make_mock_wav
from voicebench.config import Paths
from voicebench.db import Database
from voicebench.engines import EngineRequest, run_engine
from voicebench.service import STUDIO_ENGINE_SLUG, Service


class VoxCPM2Tests(unittest.TestCase):
    def test_history_exposes_measurements_without_private_manifest(self):
        service = Service.__new__(Service)
        from unittest.mock import Mock
        service.db = Mock()
        service.db.fetch_all.return_value = [{
            "id": 1, "metrics_json": '{"wall_seconds": 60}',
            "manifest_json": json.dumps({
                "reference_text": "private reference",
                "adapter": {"worker": {"generate_seconds": 28.0,
                    "peak_allocated_mb": 6250.0, "output": "private/path.wav"}},
            }),
        }]
        row = service.runs(1)[0]
        self.assertEqual(row["metrics"]["engine_performance"], {
            "generate_seconds": 28.0, "peak_allocated_mb": 6250.0,
        })
        self.assertNotIn("manifest_json", row)
        self.assertNotIn("private", json.dumps(row))

    def test_registered_engine_is_selectable_without_changing_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict("os.environ", {"VOICEBENCH_ROOT": str(root)}):
                paths = Paths.discover()
            paths.ensure()
            reference = paths.data / "prepared/ref_voice/selected_reference.wav"
            reference.parent.mkdir(parents=True)
            make_mock_wav(reference, "reference", 13)
            reference.with_suffix(".txt").write_text("reference", encoding="utf-8")
            service = Service(paths, Database(paths.database))
            engine = service.upsert_engine(engine_spec(root, root / "model"))
            service.upsert_engine(engine_spec(root, root / "model"))
            for preset in STYLE_PRESETS:
                service.upsert_engine(engine_spec(root, root / "model", preset))
            state = service.studio_state()
            self.assertEqual(state["default_engine_slug"], STUDIO_ENGINE_SLUG)
            self.assertEqual(sum(e["slug"] == SLUG for e in state["studio_engines"]), 1)
            for preset in STYLE_PRESETS:
                self.assertNotIn(f"{SLUG}-{preset}", [e["slug"] for e in state["studio_engines"]])
                with self.assertRaises(ValueError):
                    service.create_studio_request("한국어 음성 비교입니다.", engine_slug=f"{SLUG}-{preset}")
            request = service.create_studio_request("한국어 음성 비교입니다.", engine_slug=SLUG)
            self.assertEqual(request["engine_slug"], SLUG)
            self.assertEqual(request["seed"], 13)
            project = service.create_project("benchmark")
            service.upload_reference(project["id"], "ref.wav", reference.read_bytes(), "")
            service.add_prompt(project["id"], "test")
            with self.assertRaises(ValueError):
                service.queue_benchmark(project["id"], [engine["id"]], [13])

    def test_worker_route_preserves_reference_seed_and_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = engine_spec(root, root / "model")
            spec["config_json"] = json.dumps(spec["config"])
            request = EngineRequest("한국어", root / "ref.wav", "참조 대본", root / "out.wav", 42)

            def synthesize(url, payload, timeout):
                self.assertEqual(payload["reference_text"], "참조 대본")
                self.assertEqual(payload["seed"], 42)
                self.assertEqual(payload["generation"]["inference_timesteps"], 10)
                make_mock_wav(request.output, request.text, request.seed)
                return {"sample_rate": 24000}

            with patch("voicebench.engines._ensure_worker") as ensure, patch(
                "voicebench.engines._json_request", side_effect=synthesize
            ):
                result = run_engine(spec, request)
            ensure.assert_called_once()
            self.assertEqual(result["adapter"], "persistent-http-worker")

    def test_invalid_generation_and_missing_transcript_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "ref.wav"
            reference.touch()
            payload = dict(text="test", reference=str(reference), reference_text="ref", output="out.wav")
            self.assertEqual(validate_request(payload)["inference_timesteps"], 10)
            for generation in ({"cfg_value": float("nan")}, {"inference_timesteps": 0},
                               {"inference_timesteps": 1.5}, {"temperature": 1}):
                with self.assertRaises(ValueError):
                    validate_request({**payload, "generation": generation})
            with self.assertRaises(ValueError):
                validate_request({**payload, "reference_text": ""})

    def test_style_uses_controlled_reference_without_rewriting_script(self):
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "ref.wav"
            reference.touch()
            payload = dict(text="와! 진짜 대박인데요?", reference=str(reference), output="out.wav",
                           generation={"cloning_mode": "controlled", "style_prompt": "cheerful"})
            args = generation_arguments(payload, validate_request(payload))
            self.assertEqual(args["text"], "(cheerful)와! 진짜 대박인데요?")
            self.assertEqual(payload["text"], "와! 진짜 대박인데요?")
            self.assertEqual(args["reference_wav_path"], str(reference.resolve()))
            self.assertNotIn("prompt_text", args)
            self.assertNotIn("prompt_wav_path", args)
            self.assertNotIn("cloning_mode", args)
            for generation in ({"style_prompt": "cheerful"},
                               {"cloning_mode": "controlled", "style_prompt": "(cheerful)"},
                               {"cloning_mode": "invalid"}):
                with self.assertRaises(ValueError):
                    validate_request({**payload, "reference_text": "ref", "generation": generation})


if __name__ == "__main__":
    unittest.main()
