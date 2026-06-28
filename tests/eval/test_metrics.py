"""Unit tests for the ranking-metric library (Issue 198) — runs anywhere, no DB."""

import math

import pytest

from tests.eval.metrics import (
    average_precision_at_k,
    bootstrap_ci,
    chronological_split,
    dcg_at_k,
    kendall_tau,
    ndcg_at_k,
    paired_bootstrap_delta,
    reciprocal_rank,
)


def test_ndcg_perfect_order_is_one():
    # Already in descending relevance → NDCG == 1.0 at any k.
    assert ndcg_at_k([3, 2, 1, 0], 5) == pytest.approx(1.0)


def test_ndcg_reverse_order_is_below_one_and_positive():
    perfect = ndcg_at_k([3, 2, 1, 0], 4)
    reversed_ = ndcg_at_k([0, 1, 2, 3], 4)
    assert perfect == pytest.approx(1.0)
    assert 0.0 < reversed_ < 1.0


def test_ndcg_zero_when_no_relevance():
    assert ndcg_at_k([0, 0, 0], 3) == 0.0
    assert ndcg_at_k([], 3) == 0.0


def test_dcg_known_value():
    # rel=[1,1] → 1/log2(2) + 1/log2(3) = 1.0 + 0.6309...
    assert dcg_at_k([1, 1], 2) == pytest.approx(1.0 + 1 / math.log2(3))


def test_average_precision_at_k():
    # hits at positions 1 and 3 → (1/1 + 2/3) / min(2,5) = (1 + 0.6667)/2
    assert average_precision_at_k([1, 0, 1, 0], 5) == pytest.approx((1.0 + 2 / 3) / 2)
    assert average_precision_at_k([0, 0, 0], 5) == 0.0
    # perfect: both relevant up front → 1.0
    assert average_precision_at_k([1, 1, 0], 5) == pytest.approx(1.0)


def test_reciprocal_rank():
    assert reciprocal_rank([0, 0, 1, 0]) == pytest.approx(1 / 3)
    assert reciprocal_rank([1, 0]) == 1.0
    assert reciprocal_rank([0, 0]) == 0.0


def test_kendall_tau_identical_and_reversed():
    assert kendall_tau([1, 2, 3, 4], [1, 2, 3, 4]) == pytest.approx(1.0)
    assert kendall_tau([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    # degenerate
    assert kendall_tau([1], [1]) == 0.0
    assert kendall_tau([1, 1, 1], [1, 1, 1]) == 0.0  # all tied → denom 0


def test_chronological_split_orders_and_no_overlap():
    items = [{"id": "c", "t": 3}, {"id": "a", "t": 1}, {"id": "b", "t": 2}, {"id": "d", "t": 4}]
    train, test = chronological_split(items, key=lambda r: r["t"], train_frac=0.5)
    assert [r["id"] for r in train] == ["a", "b"]  # oldest two
    assert [r["id"] for r in test] == ["c", "d"]  # newest two
    train_ids = {r["id"] for r in train}
    test_ids = {r["id"] for r in test}
    assert train_ids.isdisjoint(test_ids)  # leakage guard


def test_chronological_split_rejects_bad_frac():
    with pytest.raises(ValueError):
        chronological_split([1, 2, 3], key=lambda x: x, train_frac=1.0)


def test_bootstrap_ci_brackets_the_mean_and_is_deterministic():
    values = [0.2, 0.4, 0.6, 0.8, 1.0]
    point, lo, hi = bootstrap_ci(values, n_resamples=2000, seed=42)
    assert point == pytest.approx(0.6)
    assert lo < point < hi  # it's a band, not a point
    # Deterministic for a fixed seed.
    assert bootstrap_ci(values, n_resamples=2000, seed=42) == (point, lo, hi)


def test_paired_bootstrap_delta_detects_a_beats_b():
    # a strictly dominates b on every paired query → CI of the delta excludes 0.
    a = [0.9, 0.8, 0.95, 0.85, 0.9]
    b = [0.4, 0.3, 0.45, 0.35, 0.4]
    mean_delta, lo, hi = paired_bootstrap_delta(a, b, n_resamples=2000, seed=1)
    assert mean_delta > 0
    assert lo > 0  # significant: a beats b


def test_paired_bootstrap_delta_requires_aligned_inputs():
    with pytest.raises(ValueError):
        paired_bootstrap_delta([0.1, 0.2], [0.1], n_resamples=10)
