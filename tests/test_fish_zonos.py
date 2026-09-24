import base64
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adapters"))
from fish_speech_worker import generation_options
from zonos2_worker import generation_request, ZonosBackend
from tools.register_fish_zonos import engine_spec
from unittest.mock import Mock, patch


class NewTTSBackendsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.reference = Path(self.temp.name) / "reference.wav"
        self.reference.write_bytes(b"test reference")
        self.payload = {"text": "와! 대본을 그대로 읽어주세요.", "reference": str(self.reference),
                        "reference_text": "참조 대본", "seed": 13,
                        "output": str(Path(self.temp.name) / "out.wav"), "language": "ko"}

    def test_fish_rejects_emotion_and_missing_transcript(self):
        self.assertEqual(generation_options(self.payload)["max_new_tokens"], 2048)
        with self.assertRaises(ValueError):
            generation_options(self.payload | {"reference_text": ""})
        with self.assertRaises(ValueError):
            generation_options(self.payload | {"generation": {"style_prompt": "excited"}})
        with self.assertRaises(ValueError):
            generation_options(self.payload | {"generation": {"temperature": float("nan")}})

    def test_zonos_clones_without_emotion_or_rewriting(self):
        embedding = b"example npy bytes"
        request = generation_request(self.payload, embedding)
        self.assertEqual(request["text"], self.payload["text"])
        self.assertEqual(base64.b64decode(request["speaker_embedding_base64"]), embedding)
        self.assertNotIn("speaker_audio_base64", request)
        self.assertTrue(request["accurate_mode"])
        self.assertFalse(request["emotion_enabled"])
        self.assertFalse(request["text_normalization"])
        self.assertEqual(request["seed"], 13)
        with self.assertRaises(ValueError):
            generation_request(self.payload | {"generation": {"emotion_enabled": True}}, embedding)

    def test_model_switch_uses_common_endpoint_and_distinct_identity(self):
        specs = [engine_spec(ROOT, engine, {"revision": "test", "path": "weights"})
                 for engine in ("fish-speech", "zonos2")]
        self.assertEqual(len({s["config"]["worker"]["endpoint"] for s in specs}), 1)
        self.assertEqual(len({s["config"]["worker"]["model"] for s in specs}), 2)
        self.assertTrue(all(s["config"]["environment"]["HF_HUB_OFFLINE"] == "1" for s in specs))
        self.assertTrue(specs[0]["config"]["requires_reference_text"])
        self.assertFalse(specs[1]["config"]["requires_reference_text"])

    def test_zonos_close_only_terminates_its_owned_child(self):
        backend = ZonosBackend.__new__(ZonosBackend)
        backend.process = Mock()
        backend.process.poll.return_value = None
        backend.close()
        backend.process.terminate.assert_called_once_with()
        backend.process.wait.assert_called_once_with(timeout=15)
        backend.process.kill.assert_not_called()

    def test_studio_selects_both_models_without_changing_production_default(self):
        from voicebench.config import Paths
        from voicebench.db import Database
        from voicebench.service import Service, STUDIO_ENGINE_SLUG
        from voicebench.audio import make_mock_wav
        root = Path(self.temp.name)
        with patch.dict("os.environ", {"VOICEBENCH_ROOT": str(root)}):
            paths = Paths.discover()
        paths.ensure()
        reference = paths.data / "prepared/ref_voice/selected_reference.wav"
        reference.parent.mkdir(parents=True)
        make_mock_wav(reference, "reference", 13)
        reference.with_suffix(".txt").write_text("reference", encoding="utf-8")
        service = Service(paths, Database(paths.database))
        for name in ("fish-speech", "zonos2"):
            engine = service.upsert_engine(engine_spec(root, name, {"path": "weights", "revision": "test"}))
            request = service.create_studio_request("한국어 비교 대본입니다.", engine_slug=engine["slug"])
            self.assertEqual(request["engine_slug"], engine["slug"])
            self.assertEqual(request["seed"], 13)
        state = service.studio_state()
        self.assertEqual(state["default_engine_slug"], STUDIO_ENGINE_SLUG)
        self.assertTrue({"fish-s2-pro-experimental", "zonos2-q8-experimental"}.issubset(
            {e["slug"] for e in state["studio_engines"]}))


if __name__ == "__main__":
    unittest.main()
