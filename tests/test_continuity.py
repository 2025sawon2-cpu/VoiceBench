from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from adapters.qwen3_tts_worker import continuation_prompt
from tools.compare_continuity import load_baseline, require_free_gpu
from voicebench.audio import inspect_wav, make_mock_wav
from voicebench.continuity import group_for_continuity
from voicebench.service import SpeechSegment
from voicebench.util import sha256_file


def segment(text="문장입니다.", role="statement", paragraph=1, end=False):
    return SpeechSegment(text, paragraph, 1, role, end, 420 if end else 280)


class ContinuityTests(unittest.TestCase):
    def test_merging_keeps_words_in_order_and_removes_only_compatible_boundaries(self):
        segments = [segment("첫 문장입니다."), segment("두 번째 문장입니다."), segment("세 번째입니다.", end=True)]
        groups = group_for_continuity(segments, [12.0, 10.0, 8.0])
        self.assertEqual([item.positions for item in groups], [(0, 1), (2,)])
        self.assertEqual(groups[0].text, "첫 문장입니다. 두 번째 문장입니다.")
        self.assertEqual(" ".join(g.text for g in groups), " ".join(s.text for s in segments))
        self.assertEqual(groups[-1].pause_after_ms, 420)

    def test_questions_transitions_and_paragraphs_keep_their_breath(self):
        examples = [
            [segment(role="question"), segment()],
            [segment(role="exclamation"), segment()],
            [segment(), segment(role="transition")],
            [segment(), segment(role="conclusion")],
            [segment(end=True), segment(paragraph=2)],
        ]
        for segments in examples:
            with self.subTest(segments=segments):
                self.assertEqual(len(group_for_continuity(segments, [5, 5])), 2)

    def test_character_budget_and_existing_long_units_are_preserved(self):
        units = [segment("가" * 160), segment("나" * 150)]
        self.assertEqual(len(group_for_continuity(units, [10, 10])), 2)
        long = segment("아주 긴 기존 문장은 다시 자르지 않습니다.")
        self.assertEqual(group_for_continuity([long], [35])[0].text, long.text)
        for durations in ([], [0], [float("nan")], [float("inf")]):
            with self.assertRaises(ValueError):
                group_for_continuity([long], durations)

    def test_anchored_prompt_keeps_real_and_previous_audio_in_transcript_order(self):
        @dataclass
        class Prompt:
            ref_spk_embedding: object
            ref_code: object
            ref_text: str

        owner = object()
        anchor = Prompt(owner, [1, 2], "실제 원본.")
        previous = Prompt(object(), [3, 4], "바로 앞 문장.")
        model = MagicMock()
        model.create_voice_clone_prompt.return_value = [previous]
        torch = MagicMock()
        torch.cat.side_effect = lambda parts, dim: parts[0] + parts[1]
        with patch.dict("sys.modules", {"torch": torch}):
            result = continuation_prompt(model, [anchor], "previous.wav", previous.ref_text, mode="anchored")[0]
        self.assertIs(result.ref_spk_embedding, owner)
        self.assertEqual(result.ref_code, [1, 2, 3, 4])
        self.assertEqual(result.ref_text, "실제 원본. 바로 앞 문장.")
        self.assertEqual(anchor.ref_code, [1, 2])
        self.assertEqual(previous.ref_code, [3, 4])
        with self.assertRaises(ValueError):
            continuation_prompt(model, [anchor], "previous.wav", "앞 문장", mode="unsupported")

    def test_low_gpu_memory_stops_before_model_load_without_touching_other_processes(self):
        with patch("tools.compare_continuity.subprocess.run", return_value=SimpleNamespace(stdout="3800\n")) as run:
            with self.assertRaisesRegex(RuntimeError, "3800"):
                require_free_gpu()
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][0], "nvidia-smi")
        with patch("tools.compare_continuity.subprocess.run", return_value=SimpleNamespace(stdout="7200\n")):
            require_free_gpu()

    def test_reusing_baseline_checks_voice_settings_text_and_audio_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference, wav, report_path = root / "ref.wav", root / "baseline.wav", root / "report.json"
            reference.write_bytes(b"selected voice")
            text = "오늘의 이야기를 차분하게 시작해 보겠습니다."
            make_mock_wav(wav, text, 13)
            config = {"worker": {"model": "Qwen/test"}, "generation": {"temperature": 1.0}}
            report = {
                "reference_sha256": sha256_file(reference), "seed": 13,
                "model": "Qwen/test", "generation": config["generation"],
                "text": text, "segment_target_chars": 140,
                "variants": {"baseline": {"segments": [{
                    "path": str(wav), "text": text, "sha256": sha256_file(wav),
                    "quality": {"transcription": text, "retry_recommended": False, "wav": inspect_wav(wav)},
                }]}},
            }
            report_path.write_text(json.dumps(report), encoding="utf-8")
            self.assertEqual(load_baseline(report_path, reference, config)[1][0].text, text)
            report["seed"] = 42
            report_path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "settings differ"):
                load_baseline(report_path, reference, config)
            report["seed"] = 13
            report_path.write_text(json.dumps(report), encoding="utf-8")
            make_mock_wav(wav, text, 42)
            with self.assertRaisesRegex(ValueError, "content has changed"):
                load_baseline(report_path, reference, config)


if __name__ == "__main__":
    unittest.main()
