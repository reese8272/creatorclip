"""Proof-of-Lift aggregation tests (Issue 374).

Focused on the three honesty rules — those are the load-bearing behaviour.
Over-claiming here breaches the honesty constraint far more visibly than a bad
clip score, which is exactly why they are tested and the arithmetic mostly is not.
"""

import pytest

from preference.lift import (
    MIN_JUDGED_FOR_RATE,
    MIN_PER_GROUP_FOR_CONTRAST,
    LiftClip,
    summarize_lift,
    wilson_interval,
)


def _clip(
    performed_well: bool | None,
    *,
    score: float | None = 0.5,
    dna_match: float | None = 0.5,
    start_s: float = 0.0,
    end_s: float = 30.0,
    peak_s: float | None = 10.0,
    principle: str | None = "clip-the-setup",
    idx: int = 0,
) -> LiftClip:
    return LiftClip(
        clip_id=f"clip-{idx}",
        performed_well=performed_well,
        score=score,
        dna_match=dna_match,
        duration_s=end_s - start_s,
        setup_lead_s=(peak_s - start_s) if peak_s is not None else None,
        principle=principle,
    )


# ── Rule 1: deferred verdicts are their own bucket, never failures ──────────


def test_deferred_verdicts_are_not_counted_as_underperformers() -> None:
    """performed_well=None means "can't judge yet" (Issue 201), not "lost".

    Counting these as failures would invent bad news about the creator.
    """
    clips = [_clip(True, idx=0), _clip(False, idx=1)] + [_clip(None, idx=i) for i in range(2, 7)]
    out = summarize_lift(clips)

    assert out["published_count"] == 7
    assert out["deferred_count"] == 5
    assert out["judged_count"] == 2
    assert out["beat_median_count"] == 1
    assert out["below_median_count"] == 1
    # The five deferred clips appear in NEITHER outcome bucket.
    assert out["beat_median_count"] + out["below_median_count"] == out["judged_count"]


def test_all_deferred_reports_zero_judged_and_no_rate() -> None:
    """The launch reality: published clips exist but none are judgeable yet."""
    out = summarize_lift([_clip(None, idx=i) for i in range(4)])
    assert out["published_count"] == 4
    assert out["judged_count"] == 0
    assert out["rate"] is None


# ── Rule 2: a count is a fact, a rate is an inference ───────────────────────


def test_rate_is_withheld_below_the_minimum_but_counts_are_still_shown() -> None:
    judged = MIN_JUDGED_FOR_RATE - 1
    clips = [_clip(True, idx=i) for i in range(judged)]
    out = summarize_lift(clips)

    assert out["judged_count"] == judged
    assert out["beat_median_count"] == judged  # the fact survives
    assert out["rate"] is None  # the inference does not
    assert out["rate_ci_low"] is None
    assert out["rate_ci_high"] is None
    assert out["min_judged_for_rate"] == MIN_JUDGED_FOR_RATE


def test_rate_appears_with_an_interval_at_the_threshold() -> None:
    clips = [_clip(i < 7, idx=i) for i in range(MIN_JUDGED_FOR_RATE)]
    out = summarize_lift(clips)

    assert out["rate"] == pytest.approx(0.7)
    assert out["rate_ci_low"] is not None and out["rate_ci_high"] is not None
    assert out["rate_ci_low"] < 0.7 < out["rate_ci_high"]


def test_empty_input_makes_no_claims_at_all() -> None:
    out = summarize_lift([])
    assert out["published_count"] == 0
    assert out["rate"] is None
    assert out["contrast"] is None


# ── Rule 3: Wilson, not Wald ────────────────────────────────────────────────


def test_wilson_interval_stays_in_range_at_the_extremes() -> None:
    """At p=1 the Wald interval degenerates to zero width; Wilson must not.

    This is the whole reason for choosing Wilson — a 10/10 run must not be
    reported as "100%, no uncertainty".
    """
    low, high = wilson_interval(10, 10)
    assert 0.0 <= low < 1.0, "a perfect run must still carry downside uncertainty"
    assert high <= 1.0, "the interval must never exceed a probability"

    low0, high0 = wilson_interval(0, 10)
    assert low0 >= 0.0, "the interval must never go negative"
    assert 0.0 < high0 < 1.0


def test_wilson_interval_narrows_as_n_grows() -> None:
    narrow = wilson_interval(50, 100)
    wide = wilson_interval(5, 10)
    assert (narrow[1] - narrow[0]) < (wide[1] - wide[0])


def test_wilson_interval_on_no_observations_is_maximally_uncertain() -> None:
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_wilson_interval_rejects_impossible_input() -> None:
    with pytest.raises(ValueError):
        wilson_interval(5, 3)


# ── Contrast gating ─────────────────────────────────────────────────────────


def test_contrast_requires_both_groups_to_clear_the_minimum() -> None:
    """A one-sided contrast would present noise as a pattern."""
    clips = [_clip(True, idx=i) for i in range(10)] + [
        _clip(False, idx=100 + i) for i in range(MIN_PER_GROUP_FOR_CONTRAST - 1)
    ]
    assert summarize_lift(clips)["contrast"] is None


def test_contrast_is_derived_from_stored_features_not_generic_advice() -> None:
    winners = [_clip(True, score=0.9, end_s=40.0, peak_s=20.0, idx=i) for i in range(5)]
    losers = [_clip(False, score=0.2, end_s=15.0, peak_s=2.0, idx=100 + i) for i in range(5)]
    contrast = summarize_lift(winners + losers)["contrast"]

    assert contrast is not None
    assert contrast["winners_n"] == 5 and contrast["underperformers_n"] == 5
    # Values come from the clips themselves, so the two groups must differ.
    assert contrast["winners"]["avg_score"] == pytest.approx(0.9)
    assert contrast["underperformers"]["avg_score"] == pytest.approx(0.2)
    assert contrast["winners"]["median_duration_s"] == pytest.approx(40.0)
    assert contrast["winners"]["median_setup_lead_s"] == pytest.approx(20.0)


def test_missing_features_do_not_crash_the_contrast() -> None:
    """Older clips predate some columns; a None feature must be skipped, not fatal."""
    winners = [_clip(True, score=None, dna_match=None, peak_s=None, idx=i) for i in range(3)]
    losers = [_clip(False, idx=100 + i) for i in range(3)]
    contrast = summarize_lift(winners + losers)["contrast"]

    assert contrast is not None
    assert contrast["winners"]["avg_score"] is None
    assert contrast["winners"]["median_setup_lead_s"] is None
