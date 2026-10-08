# Korean quantity pronunciation

The user reported that `4인 가족` in the old tent narration sounded like
`세인 가족`. Both the saved Whisper Base QA and independent Whisper Small
transcriptions had also returned `세인 가족`; whole-text CER alone had passed it.

`voicebench/korean_numbers.py` applies a deliberately bounded spoken-form policy:
standalone integers 0–99 followed by `인 가족` or an unambiguous `초` expression
are spelled in Sino-Korean before Studio speech planning. For example `4인 가족`
becomes `사 인 가족`, and `3초면` becomes `삼 초면`. The saved original script and
downstream captions are unchanged. Dates, decimals, leading-zero strings,
identifiers, prices and other counters are outside this policy.

The QA checker compares numeric values when the reference and ASR both identify
the same unit and the same number of occurrences. A changed value adds
`quantity_mismatch` and uses the existing bounded Seed 13 → 10 → 42 retry policy.
Equivalent digit/Korean spellings pass. Missing or ambiguous ASR units do not
prove a different number and are not forced into a guess. ASR remains imperfect;
this is not a guarantee that every numeric pronunciation will be correct.

The shared Web/external Studio planner is used; original_text stays unchanged.
The external request fingerprint includes the pronunciation policy and version 8
so a pre-correction successful request is not treated as an equivalent cache hit.
No voice, reference, engine, HQ or user-visible script settings are changed.
