from __future__ import annotations

import pytest

from scoring import scoring


# ---- visibility_score -------------------------------------------------


def test_visibility_score_basic():
    assert scoring.visibility_score(3, 10) == 30.0


def test_visibility_score_none_named():
    assert scoring.visibility_score(0, 10) == 0.0


def test_visibility_score_all_named():
    assert scoring.visibility_score(10, 10) == 100.0


def test_visibility_score_zero_total_raises():
    with pytest.raises(ValueError, match="zero answers"):
        scoring.visibility_score(0, 0)


# ---- gap_score ----------------------------------------------------------


def test_gap_score_basic():
    assert scoring.gap_score(4, 10) == 40.0


def test_gap_score_zero_total_raises():
    with pytest.raises(ValueError, match="zero answers"):
        scoring.gap_score(0, 0)


# ---- opportunity_score ----------------------------------------------------


def test_opportunity_score_uses_configured_weights():
    # 0.5 * 40 + 0.3 * 80 + 0.2 * 60 = 20 + 24 + 12 = 56
    result = scoring.opportunity_score(
        gap_score=40.0, fixability_signal=80.0, business_reality_signal=60.0
    )
    assert result == pytest.approx(56.0)


def test_opportunity_score_all_zero_is_zero():
    assert scoring.opportunity_score(0.0, 0.0, 0.0) == 0.0


def test_opportunity_score_all_max_is_hundred():
    assert scoring.opportunity_score(100.0, 100.0, 100.0) == pytest.approx(100.0)


# ---- accuracy_score ---------------------------------------------------


def test_accuracy_score_basic():
    assert scoring.accuracy_score(correct_claims=7, incorrect_claims=3) == 70.0


def test_accuracy_score_excludes_unverifiable_by_construction():
    # Caller simply never includes unverifiable claims in either count.
    assert scoring.accuracy_score(correct_claims=1, incorrect_claims=1) == 50.0


def test_accuracy_score_zero_checked_claims_raises():
    with pytest.raises(ValueError, match="zero answers"):
        scoring.accuracy_score(correct_claims=0, incorrect_claims=0)


# ---- recognition_rate ---------------------------------------------------


def test_recognition_rate_basic():
    assert scoring.recognition_rate(recognized_answers=6, total_brand_answers=8) == 75.0


def test_recognition_rate_zero_total_raises():
    with pytest.raises(ValueError, match="zero answers"):
        scoring.recognition_rate(0, 0)


# ---- visibility_tier: every boundary from PLAN.md section 11 --------------


@pytest.mark.parametrize(
    "score,expected_tier",
    [
        (0, "Invisible"),
        (19, "Invisible"),
        (20, "Barely Visible"),
        (39, "Barely Visible"),
        (40, "Partially Visible"),
        (59, "Partially Visible"),
        (60, "Visible"),
        (79, "Visible"),
        (80, "Dominant"),
        (100, "Dominant"),
    ],
)
def test_visibility_tier_boundaries(score, expected_tier):
    assert scoring.visibility_tier(score) == expected_tier


@pytest.mark.parametrize("bad_score", [-1, 100.01, 150])
def test_visibility_tier_rejects_out_of_range_scores(bad_score):
    with pytest.raises(ValueError, match="between 0 and 100"):
        scoring.visibility_tier(bad_score)
