"""Conservative, opt-in continuity experiments; no production default changes."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .service import SpeechSegment


@dataclass(frozen=True)
class GenerationGroup:
    positions: tuple[int, ...]
    text: str
    estimated_seconds: float
    pause_after_ms: int


def group_for_continuity(
    segments: list[SpeechSegment],
    durations: list[float],
    target_seconds: float = 28.0,
    maximum_chars: int = 280,
) -> list[GenerationGroup]:
    """Join compatible units using measured baseline durations, not a 30 s limit.

    The estimate is only a planning budget: generating the combined text can
    change its duration. Callers must inspect the resulting audio separately.
    Existing long units remain intact; this function only merges, never cuts.
    """
    if len(segments) != len(durations):
        raise ValueError("Each segment needs a measured duration")
    if not math.isfinite(target_seconds) or target_seconds <= 0 or maximum_chars <= 0:
        raise ValueError("The duration and character budgets must be positive")
    if any(not math.isfinite(value) or value <= 0 for value in durations):
        raise ValueError("Measured durations must be positive and finite")
    groups: list[GenerationGroup] = []
    for position, (segment, duration) in enumerate(zip(segments, durations)):
        previous = segments[position - 1] if position else None
        current = groups[-1] if groups else None
        combined_text = f"{current.text} {segment.text}" if current else segment.text
        may_merge = bool(
            current and previous
            and previous.paragraph_index == segment.paragraph_index
            and not previous.paragraph_end
            and previous.semantic_role not in {"question", "exclamation"}
            and segment.semantic_role not in {"transition", "conclusion"}
            and len(combined_text) <= maximum_chars
            and current.estimated_seconds + duration <= target_seconds
        )
        if may_merge:
            groups[-1] = GenerationGroup(
                (*current.positions, position), combined_text,
                current.estimated_seconds + duration, segment.pause_after_ms,
            )
        else:
            groups.append(GenerationGroup((position,), segment.text, duration, segment.pause_after_ms))
    return groups
