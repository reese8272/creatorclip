"""Issue 338 — preference model / decay / features / config edge cases.

Covers the confirmable suspected defects from the L21 backlog:
  * predict_score assumed a 2-column [neg, pos] proba (IndexError on a single-class model);
  * features.clip_features let a NaN dna_match propagate into the model;
  * DECAY_HALF_LIFE_DAYS / threshold / weight-cap had no fail-fast config validation;
  * from_bytes must reject a corrupt blob.
Pure / config-level — default unit lane.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pydantic
import pytest

from config import Settings, settings
from preference.decay import feedback_age_days, recency_weight, sample_weight
from preference.features import clip_features
from preference.model import PreferenceScorer


# ── predict_score: robust to single-class / class-order models ───────────────
class _FakeModel:
    def __init__(self, proba_row, classes, n_features=8):
        self._proba_row = np.array([proba_row], dtype=float)
        self.classes_ = np.array(classes)
        self.n_features_in_ = n_features

    def predict_proba(self, _x):
        return self._proba_row


def _scorer(proba_row, classes):
    return PreferenceScorer(_FakeModel(proba_row, classes), label_count=99)


def test_predict_score_binary_picks_positive_column():
    # classes_ = [0, 1] → positive prob is column index 1.
    assert _scorer([0.3, 0.7], [0, 1]).predict_score([0.0] * 8) == pytest.approx(0.7)


def test_predict_score_single_class_all_negative_returns_zero():
    # Model only ever saw label 0 → 1-column proba; P(positive)=0.0, no IndexError.
    assert _scorer([1.0], [0]).predict_score([0.0] * 8) == 0.0


def test_predict_score_single_class_all_positive_returns_that_prob():
    # Model only saw label 1 → its single column IS the positive class.
    assert _scorer([1.0], [1]).predict_score([0.0] * 8) == pytest.approx(1.0)


def test_predict_score_reversed_class_order():
    # classes_ = [1, 0] → positive prob is column index 0.
    assert _scorer([0.8, 0.2], [1, 0]).predict_score([0.0] * 8) == pytest.approx(0.8)


# ── clip_features: NaN/None dna_match coerced to 0.0 ─────────────────────────


def test_clip_features_nan_dna_match_zeroed():
    vec = clip_features(dna_match=float("nan"))
    assert vec[3] == 0.0
    assert all(math.isfinite(x) for x in vec)


def test_clip_features_none_dna_match_zeroed():
    assert clip_features(dna_match=None)[3] == 0.0


def test_clip_features_normal_dna_match_passthrough():
    assert clip_features(dna_match=0.42)[3] == 0.42


# ── decay weights ────────────────────────────────────────────────────────────


def test_recency_weight_halves_at_half_life():
    h = float(settings.DECAY_HALF_LIFE_DAYS)
    assert recency_weight(0.0) == pytest.approx(1.0)
    assert recency_weight(h) == pytest.approx(0.5)
    assert recency_weight(2 * h) == pytest.approx(0.25)


def test_feedback_age_future_timestamp_clamped_to_zero():
    future = datetime.now(UTC) + timedelta(days=5)
    assert feedback_age_days(future) == 0.0


def test_sample_weight_outcome_multiplier_only_when_true():
    now = datetime.now(UTC)
    base = sample_weight(now, performed_well=None)
    assert sample_weight(now, performed_well=False) == pytest.approx(base)
    assert sample_weight(now, performed_well=True) == pytest.approx(base * 3.0)


# ── config fail-fast validators ──────────────────────────────────────────────


@pytest.mark.parametrize("bad", [0, -1, -30])
def test_decay_half_life_must_be_positive(bad):
    with pytest.raises(pydantic.ValidationError):
        Settings(DECAY_HALF_LIFE_DAYS=bad)


@pytest.mark.parametrize("bad", [0, -5])
def test_personalization_threshold_must_be_positive(bad):
    with pytest.raises(pydantic.ValidationError):
        Settings(PERSONALIZATION_THRESHOLD_LABELS=bad)


@pytest.mark.parametrize("bad", [0.0, -0.1, 1.5, 2.0])
def test_weight_cap_must_be_in_unit_range(bad):
    with pytest.raises(pydantic.ValidationError):
        Settings(PREFERENCE_WEIGHT_CAP=bad)


def test_valid_preference_settings_accepted():
    s = Settings(
        DECAY_HALF_LIFE_DAYS=45,
        PERSONALIZATION_THRESHOLD_LABELS=10,
        PREFERENCE_WEIGHT_CAP=1.0,
    )
    assert s.DECAY_HALF_LIFE_DAYS == 45
    assert s.PREFERENCE_WEIGHT_CAP == 1.0


# ── from_bytes rejects a corrupt blob ────────────────────────────────────────


def test_from_bytes_rejects_corrupt_blob():
    with pytest.raises(Exception):  # noqa: B017 — joblib/pickle raise varied types
        PreferenceScorer.from_bytes(b"\x00\x01not a joblib dump\xff")
