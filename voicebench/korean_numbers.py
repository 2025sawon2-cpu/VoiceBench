"""Conservative spoken forms for explicit Shorts quantities, not general TN.

Keep captions/original_text untouched. Model names, dates, decimals, prices and
ambiguous counters are deliberately outside this policy.
"""
from __future__ import annotations

import re
from collections import defaultdict

POLICY = "ko-family-seconds-v1"
_DIGITS = "영일이삼사오육칠팔구"


def _sino(number: int) -> str:
    if number < 10:
        return _DIGITS[number]
    tens, ones = divmod(number, 10)
    return (_DIGITS[tens] if tens > 1 else "") + "십" + (_DIGITS[ones] if ones else "")


_VALUES = {_sino(n): n for n in range(100)}
_VALUES.update({"공": 0, "한": 1, "두": 2, "세": 3, "네": 4,
                "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9})
_UNIT = r"(?:인\s*가족|초(?=$|[\s,.!?…]|면|만|도|는|에|로|가|부터|동안|마다|밖에|입니다|예요))"
_BOUNDARY = r"(?<![\w.,/+：:\-])"
_WRITTEN = re.compile(_BOUNDARY + r"(?P<number>0|[1-9][0-9]?)\s*(?P<unit>" + _UNIT + r")")
_SPOKEN = re.compile(_BOUNDARY + r"(?P<number>" +
                     "|".join(sorted(_VALUES, key=len, reverse=True)) +
                     r"|0|[1-9][0-9]?)\s*(?P<unit>" + _UNIT + r")")


def normalize_korean_quantities(text: str) -> str:
    return _WRITTEN.sub(lambda m: _sino(int(m["number"])) + " " + m["unit"], text)


def quantity_mismatches(expected: str, actual: str) -> list[dict]:
    """Flag changed values only when both ASR and script identify the same unit.

    Omitted/ambiguous ASR units are not proof of a wrong quantity. The existing
    omission checks handle those separately. Multiple occurrences align in order
    only when counts match, avoiding comparisons of unrelated phrases.
    """
    def extract(text: str) -> dict[str, list[int]]:
        values: dict[str, list[int]] = defaultdict(list)
        for match in _SPOKEN.finditer(text):
            number = match["number"]
            value = int(number) if number.isdigit() else _VALUES[number]
            values[re.sub(r"\s+", "", match["unit"])].append(value)
        return values

    wanted, heard = extract(expected), extract(actual)
    return [{"unit": unit, "expected": values, "recognized": heard[unit]}
            for unit, values in wanted.items()
            if unit in heard and len(values) == len(heard[unit]) and values != heard[unit]]
