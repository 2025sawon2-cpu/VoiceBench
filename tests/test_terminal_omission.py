import unittest

from voicebench.quality import evaluate_candidate


class TerminalOmissionTests(unittest.TestCase):
    def test_missing_closing_sentence_is_rejected_despite_low_whole_script_cer(self):
        body = '준비할 때 손이 모자라잖아요. 몸통을 받쳐주고 다리는 아래로 나오게 해요. 씻기고 말릴 때도 같은 구조로 쓸 수 있어요.'
        quality = evaluate_candidate(body+' 댓글에 나도 남겨주세요.', body.replace('받쳐주고','바쳐주고'),
                                     {'duration_seconds':15,'hard_reject':False})
        self.assertLess(quality['character_error_rate'], .34)
        self.assertGreater(quality['length_ratio'], .55)
        self.assertIn('likely_ending_omission',quality['reasons'])

    def test_short_asr_ending_substitution_is_not_a_confirmed_omission(self):
        quality = evaluate_candidate('몸통을 받쳐서 씻길 수 있어요.', '몸통을 받쳐서 씻길 수 이써요.',
                                     {'duration_seconds':3,'hard_reject':False})
        self.assertNotIn('likely_ending_omission',quality['reasons'])

    def test_repeated_terminal_phrase_is_ambiguous(self):
        quality = evaluate_candidate('생각보다 편하게 쓸 수 있어요. 다른 날에도 편하게 쓸 수 있어요.',
                                     '생각보다 편하게 쓸 수 있어요.',{'duration_seconds':5,'hard_reject':False})
        self.assertNotIn('likely_ending_omission',quality['reasons'])

    def test_punctuation_normalization_does_not_drop_complete_cta(self):
        quality = evaluate_candidate('목욕할 때 쓰는 구조예요. 댓글에 ‘나도’ 남겨주세요.',
                                     '목욕할때쓰는구조예요 댓글에 나도 남겨주세요',
                                     {'duration_seconds':5,'hard_reject':False})
        self.assertFalse(quality['retry_recommended'])


if __name__ == '__main__':
    unittest.main()
