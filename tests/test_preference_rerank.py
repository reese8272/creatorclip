"""
Unit tests for Issue 60 — maturity-gated preference blend + rerank wiring.

DB-free: `preference_weight` is pure, and `rerank_with_preference` is exercised
with `load_latest` patched to a stub scorer, so these run in the default suite and
guard the honesty threshold (no personalization below the data threshold) and the
blend math. End-to-end training/enqueue is covered by
tests/test_retrain_preference_integration.py.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import settings
from models import Clip
from preference.model import preference_weight

# ── preference_weight curve ─────────────────────────────────────────────────────


def test_weight_zero_below_threshold():
    assert preference_weight(settings.PERSONALIZATION_THRESHOLD_LABELS - 1) == 0.0
    assert preference_weight(0) == 0.0


def test_weight_zero_at_threshold_then_ramps():
    t = settings.PERSONALIZATION_THRESHOLD_LABELS
    assert preference_weight(t) == 0.0  # ramp starts at the threshold
    mid = preference_weight(t + t // 2)  # 1.5× threshold
    assert 0.0 < mid < settings.PREFERENCE_WEIGHT_CAP


def test_weight_reaches_and_caps_at_cap():
    t = settings.PERSONALIZATION_THRESHOLD_LABELS
    cap = settings.PREFERENCE_WEIGHT_CAP
    assert preference_weight(2 * t) == pytest.approx(cap)
    assert preference_weight(100 * t) == pytest.approx(cap)  # never exceeds the cap


# ── rerank_with_preference gating ───────────────────────────────────────────────


class _StubScorer:
    def __init__(self, label_count: int, scores: list[float]):
        self.label_count = label_count
        self._scores = list(scores)

    def predict_score(self, _features) -> float:
        return self._scores.pop(0)


def _clip(score: float) -> Clip:
    return Clip(score=score, dna_match=score, rank=0, signals_jsonb={"features": {}})


@pytest.mark.asyncio
async def test_rerank_noop_below_threshold():
    from clip_engine.ranking import rerank_with_preference

    clips = [_clip(0.9), _clip(0.1)]
    stub = _StubScorer(label_count=settings.PERSONALIZATION_THRESHOLD_LABELS - 1, scores=[1.0, 0.0])
    with patch("preference.train.load_latest", new=AsyncMock(return_value=stub)):
        out = await rerank_with_preference(clips, MagicMock(), MagicMock())

    # Below threshold → DNA scores untouched, order preserved (honest fallback).
    assert [c.score for c in out] == [0.9, 0.1]


@pytest.mark.asyncio
async def test_rerank_blends_and_reorders_above_threshold():
    from clip_engine.ranking import rerank_with_preference

    a, b = _clip(0.9), _clip(0.1)
    # weight = cap (0.5) at 2× threshold. pref flips the order: a→0.0, b→1.0.
    stub = _StubScorer(label_count=2 * settings.PERSONALIZATION_THRESHOLD_LABELS, scores=[0.0, 1.0])
    with patch("preference.train.load_latest", new=AsyncMock(return_value=stub)):
        out = await rerank_with_preference([a, b], MagicMock(), MagicMock())

    # blended: a = 0.5*0.9 + 0.5*0.0 = 0.45 ; b = 0.5*0.1 + 0.5*1.0 = 0.55 → b first
    assert out[0] is b and out[0].rank == 1
    assert out[1] is a and out[1].rank == 2
    assert b.score == pytest.approx(0.55)


@pytest.mark.asyncio
async def test_rerank_noop_when_no_model():
    from clip_engine.ranking import rerank_with_preference

    clips = [_clip(0.9), _clip(0.1)]
    with patch("preference.train.load_latest", new=AsyncMock(return_value=None)):
        out = await rerank_with_preference(clips, MagicMock(), MagicMock())
    assert [c.score for c in out] == [0.9, 0.1]


# ── Issue 71: predict_score validation + DNA fallback ───────────────────────────


def test_predict_score_raises_on_feature_mismatch():
    import numpy as np

    from preference.features import FEATURE_NAMES
    from preference.model import fit

    n = len(FEATURE_NAMES)
    scorer = fit(
        np.array([[0.0] * n, [1.0] * n]),
        np.array([0, 1]),
        np.array([1.0, 1.0]),
    )
    assert 0.0 <= scorer.predict_score([0.5] * n) <= 1.0  # correct width works
    with pytest.raises(ValueError, match="feature count"):
        scorer.predict_score([0.0] * (n - 1))  # drift → raises, not 0.5


class _RaisingScorer:
    label_count = 2 * 9999  # well above threshold → weight > 0

    def predict_score(self, _features):
        raise ValueError("model is broken")


@pytest.mark.asyncio
async def test_rerank_falls_back_to_dna_when_scorer_raises():
    from clip_engine.ranking import rerank_with_preference

    # label_count above threshold so weight > 0 and the model WOULD be applied.
    stub = _RaisingScorer()
    stub.label_count = 2 * settings.PERSONALIZATION_THRESHOLD_LABELS
    a, b = _clip(0.9), _clip(0.1)
    with patch("preference.train.load_latest", new=AsyncMock(return_value=stub)):
        out = await rerank_with_preference([a, b], MagicMock(), MagicMock())

    # Scorer raised → DNA scores left untouched, original order preserved.
    assert [c.score for c in out] == [0.9, 0.1]
