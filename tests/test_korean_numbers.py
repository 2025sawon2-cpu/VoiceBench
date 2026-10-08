import unittest

from voicebench.korean_numbers import normalize_korean_quantities, quantity_mismatches
from voicebench.quality import choose_best_candidate, evaluate_candidate
from voicebench.service import plan_studio_script


class KoreanNumberTests(unittest.TestCase):
    def test_explicit_family_and_seconds(self):
        self.assertEqual(normalize_korean_quantities("3초면 펼쳐지고 4인가족도 써요."),
                         "삼 초면 펼쳐지고 사 인가족도 써요.")
        self.assertEqual(normalize_korean_quantities("4 인 가족, 23초 동안"),
                         "사 인 가족, 이십삼 초 동안")

    def test_unrelated_numbers_and_ambiguous_units_unchanged(self):
        text = "2026-10-06, Q4, 1.4초, -4초, 1,004초, 3/4초, 04초, 4명, 4시, 10원"
        self.assertEqual(normalize_korean_quantities(text), text)

    def test_already_spoken_and_words_unchanged(self):
        text = "사 인 가족과 삼 초면 충분해요. 삼초록 세상이 좋아요."
        self.assertEqual(normalize_korean_quantities(text), text)
        self.assertEqual(quantity_mismatches("3초면 충분해요", "삼초록 세상"), [])

    def test_real_reported_low_cer_quantity_error_gets_retry(self):
        text = "이 텐트는 3 초 만에 펼쳐지고 4 인 가족도 넉넉하거든요."
        result = evaluate_candidate(text, text.replace("4 인", "세인"), {"duration_seconds": 6})
        self.assertLess(result["character_error_rate"], 0.1)
        self.assertIn("quantity_mismatch", result["reasons"])
        self.assertEqual(result["quantity_mismatches"][0]["expected"], [4])
        self.assertEqual(result["quantity_mismatches"][0]["recognized"], [3])

    def test_equivalent_numerals_pass(self):
        for actual in ("사인 가족, 삼초", "4 인가족, 3초", "네 인 가족, 삼 초"):
            self.assertEqual(quantity_mismatches("4인 가족, 3초", actual), [])

    def test_ambiguous_or_missing_asr_not_declared_quantity_error(self):
        self.assertEqual(quantity_mismatches("4인 가족", "쎄인 가족"), [])
        self.assertEqual(quantity_mismatches("4인 가족, 5인 가족", "4인 가족"), [])
        self.assertEqual(quantity_mismatches("4인 가족", "가족"), [])

    def test_repeated_same_unit_alignment(self):
        issues = quantity_mismatches("3초에서 4초로", "삼 초에서 오 초로")
        self.assertEqual(issues, [{"unit": "초", "expected": [3, 4], "recognized": [3, 5]}])

    def test_qa_prefers_verified_quantity(self):
        text = "4인 가족도 넉넉하게 쓸 수 있어요."
        bad = evaluate_candidate(text, text.replace("4인", "세인"), {"duration_seconds": 4})
        good = evaluate_candidate(text, text.replace("4인", "사인"), {"duration_seconds": 4})
        chosen = choose_best_candidate([{"attempt": 1, "quality": bad}, {"attempt": 2, "quality": good}])
        self.assertEqual(chosen["attempt"], 2)

    def test_web_and_external_planner_uses_spoken_forms(self):
        for engine in ("qwen3-tts-17b-pascal-expressive", "zonos2-q8-experimental"):
            plan = plan_studio_script("4인 가족도 3초면 준비해요.", engine)
            self.assertEqual(" ".join(x.text for x in plan), "사 인 가족도 삼 초면 준비해요.")


if __name__ == "__main__":
    unittest.main()
