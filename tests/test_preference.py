"""
Unit tests for preference/decay.py, preference/features.py, preference/model.py.
"""

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from preference.decay import _LAMBDA, feedback_age_days, recency_weight, sample_weight
from preference.features import FEATURE_NAMES, clip_features
from preference.model import PreferenceScorer, fit

# ── recency_weight ─────────────────────────────────────────────────────────────


def test_recency_weight_today_near_one():
    assert recency_weight(0.0) == pytest.approx(1.0)


def test_recency_weight_thirty_days_half():
    assert recency_weight(30.0) == pytest.approx(0.5, abs=0.01)


def test_recency_weight_sixty_days_quarter():
    assert recency_weight(60.0) == pytest.approx(0.25, abs=0.01)


def test_recency_weight_never_negative():
    assert recency_weight(1000.0) >= 0.0


def test_recency_weight_older_feedback_lower_weight():
    assert recency_weight(5.0) > recency_weight(25.0)


def test_recency_weight_half_life_is_30():
    """Half-life is 30 days — distinct from DNA builder's 90-day half-life."""
    assert pytest.approx(math.log(2) / 30) == _LAMBDA


# ── feedback_age_days ─────────────────────────────────────────────────────────


def test_feedback_age_days_recent():
    recent = datetime.now(UTC) - timedelta(days=10)
    assert feedback_age_days(recent) == pytest.approx(10.0, abs=0.1)


def test_feedback_age_days_naive_datetime():
    naive = datetime.now() - timedelta(days=5)
    assert feedback_age_days(naive) >= 0.0


# ── sample_weight ─────────────────────────────────────────────────────────────


def test_sample_weight_performed_well_multiplied():
    ts = datetime.now(UTC) - timedelta(days=1)
    w_base = sample_weight(ts, performed_well=None)
    w_good = sample_weight(ts, performed_well=True)
    assert w_good == pytest.approx(w_base * 3.0)


def test_sample_weight_performed_false_no_multiplier():
    ts = datetime.now(UTC) - timedelta(days=1)
    w_base = sample_weight(ts, performed_well=None)
    w_bad = sample_weight(ts, performed_well=False)
    assert w_bad == pytest.approx(w_base)


def test_sample_weight_older_is_less():
    new_ts = datetime.now(UTC) - timedelta(days=2)
    old_ts = datetime.now(UTC) - timedelta(days=28)
    assert sample_weight(new_ts) > sample_weight(old_ts)


# ── clip_features ─────────────────────────────────────────────────────────────


def test_clip_features_length():
    feats = clip_features()
    assert len(feats) == len(FEATURE_NAMES)


def test_clip_features_boolean_encoding():
    with_spike = clip_features(has_retention_spike=True)
    without = clip_features(has_retention_spike=False)
    assert with_spike[FEATURE_NAMES.index("has_retention_spike")] == 1.0
    assert without[FEATURE_NAMES.index("has_retention_spike")] == 0.0


def test_clip_features_dna_match_defaults_zero():
    feats = clip_features(dna_match=None)
    assert feats[FEATURE_NAMES.index("dna_match")] == 0.0


# ── fit + PreferenceScorer ─────────────────────────────────────────────────────


def _training_data(n_pos=5, n_neg=5):
    rng = np.random.default_rng(42)
    X = rng.random((n_pos + n_neg, len(FEATURE_NAMES)))
    y = np.array([1] * n_pos + [0] * n_neg)
    w = np.ones(n_pos + n_neg)
    return X, y, w


def test_fit_logistic_below_threshold():
    X, y, w = _training_data(5, 5)
    scorer = fit(X, y, w, threshold=20)
    assert isinstance(scorer, PreferenceScorer)
    assert scorer.label_count == 10


def test_fit_lgbm_at_threshold():
    X, y, w = _training_data(15, 15)
    scorer = fit(X, y, w, threshold=20)
    assert isinstance(scorer, PreferenceScorer)


def test_predict_score_in_range():
    X, y, w = _training_data()
    scorer = fit(X, y, w, threshold=20)
    feats = clip_features(signal_density=1.0, has_retention_spike=True)
    score = scorer.predict_score(feats)
    assert 0.0 <= score <= 1.0


def test_predict_score_positive_features_higher():
    """Features associated with positive examples should score higher."""
    X, y, w = _training_data(n_pos=10, n_neg=2)
    # Make all positives have signal_density=1.0 and negatives=0.0
    X[:10, FEATURE_NAMES.index("signal_density")] = 1.0
    X[10:, FEATURE_NAMES.index("signal_density")] = 0.0
    scorer = fit(X, y, w, threshold=20)

    high = scorer.predict_score(clip_features(signal_density=1.0))
    low = scorer.predict_score(clip_features(signal_density=0.0))
    # The model learned that high density correlates with positive
    assert high >= low


def test_scorer_round_trips_pickle():
    X, y, w = _training_data()
    scorer = fit(X, y, w, threshold=20)
    blob = scorer.to_bytes()
    reloaded = PreferenceScorer.from_bytes(blob)
    feats = clip_features(signal_density=0.5)
    assert reloaded.predict_score(feats) == pytest.approx(scorer.predict_score(feats))
