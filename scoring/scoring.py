"""The scoring formulas from PLAN.md section 11, as plain Python functions.

An LLM never computes a final score -- these are the only functions in the
codebase allowed to produce one. They are pure: given the same counts, they
always return the same score. They don't touch the database and don't know
what a "probe" or "business" is -- whatever calls these has already counted
up the answers.

Weights and tier bands live in scoring/config.yaml, not here, so tuning them
never means touching code.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).parent / "config.yaml"


@lru_cache(maxsize=1)
def _config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text())


def _percentage(numerator: int, denominator: int) -> float:
    if denominator == 0:
        raise ValueError("can't compute a score from zero answers")
    return numerator / denominator * 100


def visibility_score(answers_naming_business: int, total_answers: int) -> float:
    """Share of category-question answers that named this business."""
    return _percentage(answers_naming_business, total_answers)


def gap_score(answers_naming_competitor_not_business: int, total_answers: int) -> float:
    """Share of category-question answers that named a competitor but not this business."""
    return _percentage(answers_naming_competitor_not_business, total_answers)


def opportunity_score(
    gap_score: float,
    fixability_signal: float,
    business_reality_signal: float,
) -> float:
    """How worth pursuing a prospect is: a weighted mix of the visibility
    gap and the gate check's fixability/reality signals (each already on a
    0-100 scale).
    """
    weights = _config()["opportunity_score_weights"]
    return (
        weights["gap_score"] * gap_score
        + weights["fixability_signal"] * fixability_signal
        + weights["business_reality_signal"] * business_reality_signal
    )


def accuracy_score(correct_claims: int, incorrect_claims: int) -> float:
    """Share of checked brand claims that were correct.

    Unverifiable claims are excluded entirely per PLAN.md section 11 --
    don't include them in either count.
    """
    return _percentage(correct_claims, correct_claims + incorrect_claims)


def recognition_rate(recognized_answers: int, total_brand_answers: int) -> float:
    """Share of brand-question answers where the AI recognised the business at all."""
    return _percentage(recognized_answers, total_brand_answers)


def visibility_tier(score: float) -> str:
    """Classifies a visibility_score (0-100) into one of PLAN.md's five tiers."""
    if not 0 <= score <= 100:
        raise ValueError(f"visibility_score must be between 0 and 100, got {score}")
    for band in _config()["visibility_tiers"]:
        if score <= band["max"]:
            return band["label"]
    raise ValueError(f"no tier band in {CONFIG_PATH.name} covers score {score}")
