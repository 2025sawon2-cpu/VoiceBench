from __future__ import annotations

import tempfile
import threading
import unittest
import wave
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from adapters.qwen3_tts_worker import QwenWorkerServer, continuation_prompt
from adapters.whisper_quality_worker import transcribe_audio
from voicebench.audio import inspect_wav
from voicebench.quality import choose_best_candidate, evaluate_candidate
from voicebench.service import plan_script
from tools.compare_prosody import eligible_context


class SpeechQualityTests(unittest.TestCase):
    def test_passed_candidate_beats_lower_cer_with_missing_ending(self):
        expected = "오늘은 우리가 함께 걸어온 길을 천천히 돌아보려고 합니다"
        wav = {"duration_seconds": 6, "hard_reject": False}
        omitted = evaluate_candidate(expected, expected[:-1], wav)
        passed = evaluate_candidate(expected, expected.replace("우리가 함께", "모두 같이"), wav)
        self.assertTrue(omitted["retry_recommended"])
        self.assertFalse(passed["retry_recommended"])
        self.assertLess(omitted["score"], passed["score"])
        selected = choose_best_candidate([
            {"attempt": 1, "quality": omitted},
            {"attempt": 2, "quality": passed},
        ])
        self.assertEqual(selected["attempt"], 2)

    def test_scripted_emphasis_is_not_a_repetition_defect(self):
        text = "천천히 천천히 다시 한번 생각해 보세요."
        quality = evaluate_candidate(text, text, {"duration_seconds": 4})
        self.assertFalse(quality["adjacent_repetition"])
        self.assertEqual(quality["score"], 0)
        repeated = evaluate_candidate(
            "천천히 다시 한번 생각해 보세요.", text, {"duration_seconds": 4}
        )
        self.assertTrue(repeated["adjacent_repetition"])
        self.assertTrue(repeated["retry_recommended"])
        extra = evaluate_candidate(
            text, text.replace("천천히 천천히", "천천히 천천히 천천히"),
            {"duration_seconds": 5},
        )
        self.assertTrue(extra["adjacent_repetition"])

    def test_empty_pcm_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.wav"
            with wave.open(str(path), "wb") as output:
                output.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            self.assertTrue(inspect_wav(path)["hard_reject"])

    def test_transition_words_require_a_real_word_boundary(self):
        for text in ("자연스럽게 이야기를 이어갑니다.", "자신의 마음을 돌아봅니다."):
            with self.subTest(text=text):
                self.assertEqual(plan_script(text)[0].semantic_role, "opening")
        self.assertEqual(plan_script("자, 다음 이야기를 시작합니다.")[0].semantic_role, "transition")
        self.assertEqual(plan_script("하지만, 아직 늦지 않았습니다.")[0].semantic_role, "transition")

    def test_short_tail_cannot_erase_transition_or_question_breath(self):
        setup = "우리는 결과를 서두르지 않고 지금까지 지나온 시간을 되짚으며 서로에게 어떤 말이 필요했는지 차분히 생각해 보려고 합니다."
        transition = "하지만 아직 늦지 않았습니다."
        plan = plan_script(setup + " " + transition)
        self.assertEqual([item.text for item in plan], [setup, transition])
        self.assertGreaterEqual(plan[0].pause_after_ms, 460)
        question = setup[:-1] + "?"
        answer = "네, 이제 알겠습니다."
        plan = plan_script(question + " " + answer)
        self.assertEqual([item.text for item in plan], [question, answer])
        self.assertEqual(plan[0].semantic_role, "question")

    def test_split_question_keeps_question_role_on_its_ending(self):
        text = "우리가 오랫동안 준비하고 생각했던 이야기를 먼저 꺼내고, 마음에 남아 있던 여러 가지 생각들을 차근차근 정리해서 모두에게 다시 들려줄 수 있을까요?"
        plan = plan_script(text, maximum=40, preserve_sentences=True)
        self.assertGreater(len(plan), 1)
        self.assertNotEqual(plan[0].semantic_role, "question")
        self.assertEqual(plan[-1].semantic_role, "question")
        self.assertEqual(sum(item.sentence_count for item in plan), 1)

    def test_long_sentence_does_not_split_at_numeric_comma(self):
        text = "오늘 접수된 전체 주문의 수량은 12,345개이며 내일 오전까지 배송을 마치기로 하였습니다."
        plan = plan_script(text, maximum=30, preserve_sentences=True)
        self.assertTrue(any("12,345개" in item.text for item in plan))

    def test_whisper_keeps_tail_beyond_thirty_seconds(self):
        for seconds in (10, 31):
            with self.subTest(seconds=seconds):
                processor = MagicMock()
                processor.return_value = SimpleNamespace(input_features="features", attention_mask="mask")
                processor.batch_decode.return_value = [" 마지막 문장까지 인식했습니다. "]
                model = MagicMock()
                result = transcribe_audio(processor, model, range(seconds * 16000), "ko")
                self.assertEqual(result, "마지막 문장까지 인식했습니다.")
                self.assertFalse(processor.call_args.kwargs["truncation"])
                self.assertEqual(model.generate.call_args.kwargs["return_timestamps"], seconds > 30)
                self.assertEqual(model.generate.call_args.kwargs["attention_mask"], "mask")

    def test_continuation_preserves_owner_embedding_without_mutating_anchor(self):
        @dataclass
        class Prompt:
            ref_spk_embedding: object
            ref_code: object
            ref_text: str

        owner = object()
        anchor = Prompt(owner, "original-codes", "원본 대본")
        previous = Prompt(object(), "previous-codes", "이전 문장")
        model = MagicMock()
        model.create_voice_clone_prompt.return_value = [previous]
        result = continuation_prompt(model, [anchor], "previous.wav", "이전 문장")
        self.assertIs(result[0].ref_spk_embedding, owner)
        self.assertEqual(result[0].ref_code, "previous-codes")
        self.assertEqual(result[0].ref_text, "이전 문장")
        self.assertEqual(anchor.ref_code, "original-codes")
        self.assertIsNot(previous.ref_spk_embedding, owner)

    def test_continuation_resets_on_paragraphs_warnings_or_a_previous_hop(self):
        segment = SimpleNamespace(paragraph_index=1, semantic_role="statement")
        previous_segment = SimpleNamespace(paragraph_index=1)
        previous = {
            "continuation_used": False,
            "quality": {"transcription": "검증된 앞 문장", "retry_recommended": False,
                        "wav": {"duration_seconds": 10}},
        }
        self.assertTrue(eligible_context(previous, segment, previous_segment))
        previous["continuation_used"] = True
        self.assertFalse(eligible_context(previous, segment, previous_segment))
        previous["continuation_used"] = False
        previous["quality"]["retry_recommended"] = True
        self.assertFalse(eligible_context(previous, segment, previous_segment))
        previous["quality"]["retry_recommended"] = False
        previous["quality"]["transcription"] = None
        self.assertFalse(eligible_context(previous, segment, previous_segment))
        previous["quality"]["transcription"] = "검증된 앞 문장"
        previous_segment.paragraph_index = 2
        self.assertFalse(eligible_context(previous, segment, previous_segment))

    def test_reference_cache_tracks_content_and_seeds_after_prompt_creation(self):
        server = object.__new__(QwenWorkerServer)
        server.model = MagicMock()
        server.model.generate_voice_clone.return_value = ([[0.1]], 24000)
        server.torch = MagicMock()
        server.prompt_cache = {}
        server.generation_lock = threading.Lock()
        events = []
        server.model.create_voice_clone_prompt.side_effect = lambda **kw: events.append("prompt") or [object()]
        numpy = MagicMock()
        numpy.random.seed.side_effect = lambda seed: events.append("seed")
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "sys.modules", {"numpy": numpy, "soundfile": MagicMock()}
        ):
            reference = Path(directory) / "ref.wav"
            reference.write_bytes(b"original reference")
            payload = {"text": "새 문장", "reference": str(reference),
                       "reference_text": "원본", "output": str(Path(directory) / "out.wav")}
            server.synthesize(payload)
            server.synthesize(payload)
            self.assertEqual(server.model.create_voice_clone_prompt.call_count, 1)
            self.assertEqual(events, ["prompt", "seed", "seed"])
            reference.write_bytes(b"changed reference at the same path")
            server.synthesize(payload)
            self.assertEqual(server.model.create_voice_clone_prompt.call_count, 2)
            self.assertEqual(events[-2:], ["prompt", "seed"])


if __name__ == "__main__":
    unittest.main()
