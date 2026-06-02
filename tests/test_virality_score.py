"""Unit tests for virality score computation (Issue 124).

Pure-function tests — no DB required. Covers the 80/20 load-bearing paths:
happy path, missing component renormalization, and the < 3-video fallback.
"""

import pytest

from routers.insights import (
    _Baselines,
    _compute_virality_score,  # internal name kept; public field is performance_score
    _mad,
    _mod_z,
    _z_to_score,
)

# ── Building-block tests ──────────────────────────────────────────────────────


def test_mad_symmetric():
    """MAD of [3,4,5,6,7]: median=5, deviations=[2,1,0,1,2], MAD=median([0,1,1,2,2])=1."""
    assert _mad([3.0, 4.0, 5.0, 6.0, 7.0]) == pytest.approx(1.0)


def test_mad_identical_values_returns_zero():
    assert _mad([1.0, 1.0, 1.0]) == 0.0


def test_mad_empty_returns_zero():
    assert _mad([]) == 0.0


def test_mod_z_at_median_is_zero():
    assert _mod_z(5.0, median=5.0, mad=2.0) == pytest.approx(0.0)


def test_mod_z_mad_zero_returns_zero():
    """When all values are identical (MAD=0), score must not crash or divide by zero."""
    assert _mod_z(1.0, median=1.0, mad=0.0) == 0.0


def test_z_to_score_midpoint():
    """z=0 should map to exactly 50."""
    assert _z_to_score(0.0) == pytest.approx(50.0)


def test_z_to_score_clamps():
    """z=10 (far outlier) clamps to 100; z=-10 clamps to 0."""
    assert _z_to_score(10.0) == pytest.approx(100.0)
    assert _z_to_score(-10.0) == pytest.approx(0.0)


# ── _compute_virality_score ───────────────────────────────────────────────────


def _baselines_from_5_videos() -> _Baselines:
    """Symmetric baselines: all 5 videos have identical metrics → MAD=0 → all scores = 50."""
    return _Baselines(
        ret_median=0.5,
        ret_mad=0.1,
        eng_median=0.04,
        eng_mad=0.01,
        views_median=1000.0,
        views_mad=200.0,
        n=5,
    )


def test_compute_virality_score_at_channel_average():
    """A video that exactly matches channel medians on all components should score ~50."""
    baselines = _baselines_from_5_videos()
    score, components = _compute_virality_score(
        engagement_rate=0.04,  # == eng_median
        avg_view_duration_s=30.0,
        duration_s=60.0,  # retention = 0.5 == ret_median
        views=1000.0,  # == views_median
        baselines=baselines,
    )
    assert score is not None
    assert score == pytest.approx(50.0, abs=0.1)
    assert components is not None
    assert components["engagement"] == pytest.approx(50.0, abs=0.1)
    assert components["retention"] == pytest.approx(50.0, abs=0.1)
    assert components["views"] == pytest.approx(50.0, abs=0.1)


def test_compute_virality_score_above_average_video():
    """A video well above channel averages should score > 50."""
    baselines = _baselines_from_5_videos()
    score, _ = _compute_virality_score(
        engagement_rate=0.10,  # 6× std above median
        avg_view_duration_s=55.0,
        duration_s=60.0,  # retention 0.917 — well above 0.5 median
        views=5000.0,  # well above 1000 median
        baselines=baselines,
    )
    assert score is not None
    assert score > 50.0


def test_compute_virality_score_missing_retention_renormalizes():
    """When avg_view_duration_s is None, retention drops out and the
    remaining weights (eng + views = 0.60) renormalize to sum to 1.0."""
    baselines = _baselines_from_5_videos()
    score, components = _compute_virality_score(
        engagement_rate=0.04,
        avg_view_duration_s=None,  # retention missing
        duration_s=60.0,
        views=1000.0,
        baselines=baselines,
    )
    assert score is not None
    assert score == pytest.approx(50.0, abs=0.1)
    assert components is not None
    assert components["retention"] is None
    # engagement and views both at median → score should still be ~50
    assert components["engagement"] == pytest.approx(50.0, abs=0.1)
    assert components["views"] == pytest.approx(50.0, abs=0.1)


def test_compute_virality_score_too_few_videos_returns_none():
    """When N < 3 (e.g., new channel), score and components are both None."""
    thin_baselines = _Baselines(n=2)
    score, components = _compute_virality_score(
        engagement_rate=0.04,
        avg_view_duration_s=30.0,
        duration_s=60.0,
        views=1000.0,
        baselines=thin_baselines,
    )
    assert score is None
    assert components is None


def test_compute_virality_score_all_metrics_missing_returns_none():
    """If every metric component is None, return (None, None) gracefully."""
    baselines = _baselines_from_5_videos()
    score, components = _compute_virality_score(
        engagement_rate=None,
        avg_view_duration_s=None,
        duration_s=None,
        views=None,
        baselines=baselines,
    )
    assert score is None
    assert components is None


def test_compute_virality_score_clamps_at_0_and_100():
    """An extreme outlier is clamped to [0, 100], never outside."""
    baselines = _Baselines(
        ret_median=0.5,
        ret_mad=0.01,
        eng_median=0.04,
        eng_mad=0.001,
        views_median=100.0,
        views_mad=10.0,
        n=10,
    )
    # Extremely high video
    score_high, _ = _compute_virality_score(
        engagement_rate=1.0,  # impossible in practice, but tests clamping
        avg_view_duration_s=60.0,
        duration_s=60.0,
        views=100_000.0,
        baselines=baselines,
    )
    assert score_high is not None
    assert 0.0 <= score_high <= 100.0

    # Extremely low video
    score_low, _ = _compute_virality_score(
        engagement_rate=0.0,
        avg_view_duration_s=0.1,
        duration_s=60.0,
        views=0.0,
        baselines=baselines,
    )
    assert score_low is not None
    assert 0.0 <= score_low <= 100.0
