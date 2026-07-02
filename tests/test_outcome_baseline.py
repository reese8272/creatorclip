"""Unit tests for the performed_well comparable-unit baseline (Issue 201).

The load-bearing decision — judge a published Short against the median of OTHER Shorts
(not the full-video view median) and defer when too few comparable Shorts exist — lives
in the pure helper `_shorts_baseline_median`, so it's verifiable without a DB. The query
wiring is covered by the staging integration test.
"""

from worker.tasks import _MIN_COMPARABLE_SHORTS, _shorts_baseline_median


def test_defers_below_comparable_floor():
    """Too few comparable Shorts → None (defer the verdict, don't mislabel)."""
    assert _shorts_baseline_median([]) is None
    assert _shorts_baseline_median([100, 200]) is None  # < 3 comparable Shorts
    assert _MIN_COMPARABLE_SHORTS == 3


def test_median_over_comparable_shorts():
    assert _shorts_baseline_median([100, 200, 300]) == 200
    assert _shorts_baseline_median([100, 200, 300, 400]) == 250  # even count → mean of mid two


def test_short_judged_against_shorts_not_longform_scale():
    """The bug this fixes: a Short with a few-thousand views compared to a full-video
    median (tens/hundreds of thousands) is always performed_well=False. Against the
    format-matched baseline, an average/strong Short is correctly NOT auto-failed."""
    shorts = [4000, 5000, 6000]
    median = _shorts_baseline_median(shorts)
    assert median == 5000
    assert (median <= 6000) is True  # strong Short → performed_well True
    assert (median <= 5000) is True  # median Short → True (not systematically failed)
    assert (median <= 3000) is False  # weak Short → False
