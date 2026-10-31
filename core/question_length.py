"""The one rule for how long a question is (PLAN.md section 8, "How questions are written").

    short   6 words or fewer. Anything under 3 words counts as short too.
    medium  one sentence of 7 to 30 words.
    long    two or more sentences, or more than 30 words.

The checks run in that order, so a very short text is short whatever its punctuation ("Help. Now." is short).

An explicit `length` tag on a question always wins over this count: a question set that already says "short" or "long" is
never re-measured. `length_for` is how every caller applies that. Nothing else in the code base works out a length.
Used by: `aeo questions draft`, the audit report, `aeo compare` (through the audit report's counting), the dossier, and the
question-set loader's resolve_tags.
"""

from __future__ import annotations

import re

LENGTHS = ("short", "medium", "long")
SHORT_MAX_WORDS = 6
MEDIUM_MAX_WORDS = 30


def sentence_count(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s])


def length_of(text: str) -> str:
    """short / medium / long from the words and sentences actually in the question."""
    words = len(text.split())
    if words <= SHORT_MAX_WORDS:
        return "short"
    if sentence_count(text) >= 2 or words > MEDIUM_MAX_WORDS:
        return "long"
    return "medium"


def length_for(text: str, tag: str | None = None) -> tuple[str, str]:
    """(length, where it came from). A valid tag wins; no tag, or one that is not short/medium/long, is counted instead."""
    if tag in LENGTHS:
        return tag, "tag"
    return length_of(text), "word count"
