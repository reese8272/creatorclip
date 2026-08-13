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


# ── Issue 465: blended_score separation ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_rerank_writes_blend_to_blended_score_and_never_touches_fit():
    """Issue 465 — the persisted `score` is the immutable DNA/LLM fit composite.

    The rerank writes the personalization blend to `blended_score` and orders by
    it; `score` survives the rerank byte-identical so every fit reader (recap
    candidates, proof-of-lift, chat tools, efficacy) stays uncontaminated.
    """
    from clip_engine.ranking import rerank_with_preference

    a, b = _clip(0.9), _clip(0.1)
    # weight = cap (0.5) at 2× threshold. pref flips the order: a→0.0, b→1.0.
    stub = _StubScorer(label_count=2 * settings.PERSONALIZATION_THRESHOLD_LABELS, scores=[0.0, 1.0])
    with patch("preference.train.load_latest", new=AsyncMock(return_value=stub)):
        out = await rerank_with_preference([a, b], MagicMock(), MagicMock())

    # fit composite untouched by the rerank
    assert a.score == pytest.approx(0.9)
    assert b.score == pytest.approx(0.1)
    # the blend lives in blended_score: (1-w)*fit + w*pref
    assert a.blended_score == pytest.approx(0.45)
    assert b.blended_score == pytest.approx(0.55)
    # rank order follows the BLENDED score
    assert out[0] is b and out[0].rank == 1
    assert out[1] is a and out[1].rank == 2


@pytest.mark.asyncio
async def test_rerank_below_threshold_leaves_blended_null():
    """NULL blended_score means 'personalization never applied' — the honest
    below-threshold fallback must not write one."""
    from clip_engine.ranking import rerank_with_preference

    clips = [_clip(0.9), _clip(0.1)]
    stub = _StubScorer(label_count=settings.PERSONALIZATION_THRESHOLD_LABELS - 1, scores=[1.0, 0.0])
    with patch("preference.train.load_latest", new=AsyncMock(return_value=stub)):
        out = await rerank_with_preference(clips, MagicMock(), MagicMock())

    assert [c.score for c in out] == [0.9, 0.1]
    assert all(c.blended_score is None for c in out)


@pytest.mark.asyncio
async def test_efficacy_dna_composite_uncontaminated_after_rerank():
    """Issue 465(c) — preference/efficacy.py ingests `clip.score` as
    `dna_composite`, the fit the blend is built FROM. Before the fix the rerank
    overwrote `clip.score` with the blend, so the harness scored the blend
    against itself. After a rerank the exact value the loader reads
    (`float(clip.score or 0.0)`) must still be the fit."""
    from clip_engine.ranking import rerank_with_preference

    a, b = _clip(0.8), _clip(0.2)
    stub = _StubScorer(label_count=2 * settings.PERSONALIZATION_THRESHOLD_LABELS, scores=[0.1, 0.9])
    with patch("preference.train.load_latest", new=AsyncMock(return_value=stub)):
        await rerank_with_preference([a, b], MagicMock(), MagicMock())

    assert float(a.score or 0.0) == pytest.approx(0.8)
    assert float(b.score or 0.0) == pytest.approx(0.2)


# ── Issue 465: reader map — downstream readers choose the FIT score ─────────────


def test_clip_response_reads_fit_not_blended():
    from models import ClipTriage, RenderStatus
    from routers.clips import _clip_response

    clip = MagicMock(spec=Clip)
    clip.score = 0.42
    clip.blended_score = 0.99  # personalization applied — must NOT surface here
    clip.rank = 1
    clip.signals_jsonb = {}
    clip.style_preset = None
    clip.render_status = RenderStatus.pending
    clip.triage = ClipTriage.pending

    assert _clip_response(clip)["score"] == 0.42


def test_downstream_readers_never_read_blended_score():
    """The Issue-465 reader map, pinned: recap candidates (create_summary),
    clip-explain, proof-of-lift (route + _group_features), and the chat tools
    all deliberately read the immutable fit `score`. A future switch to
    blended_score must be an explicit, tested choice — this test makes an
    accidental one fail."""
    import inspect

    import chat.tools as chat_tools
    import preference.lift as lift
    import routers.clips as clips_router
    import routers.insights as insights_router

    readers = {
        "create_summary": inspect.getsource(clips_router.create_summary),
        "get_clip_explanation": inspect.getsource(clips_router.get_clip_explanation),
        "get_proof_of_lift": inspect.getsource(insights_router.get_proof_of_lift),
        "_group_features": inspect.getsource(lift._group_features),
        "_list_top_clips": inspect.getsource(chat_tools._list_top_clips),
        "_get_clip_detail": inspect.getsource(chat_tools._get_clip_detail),
    }
    for name, src in readers.items():
        assert "blended_score" not in src, f"{name} must read the fit score, not the blend"
        assert ".score" in src, f"{name} no longer reads Clip.score — update the reader map"


@pytest.mark.asyncio
async def test_rerank_falls_back_to_dna_on_non_finite_score():
    """Issue 328 — a NaN/inf prediction must not corrupt the sort order.

    A non-finite blended score makes Python's sort order undefined (NaN compares
    False to everything), which could float a broken clip to rank 1. The guard
    treats it like a broken model and returns the DNA ranking untouched.
    """
    from clip_engine.ranking import rerank_with_preference

    a, b = _clip(0.9), _clip(0.1)
    stub = _StubScorer(
        label_count=2 * settings.PERSONALIZATION_THRESHOLD_LABELS,
        scores=[float("nan"), 1.0],
    )
    with patch("preference.train.load_latest", new=AsyncMock(return_value=stub)):
        out = await rerank_with_preference([a, b], MagicMock(), MagicMock())

    # Non-finite pref score → DNA scores untouched, original order preserved.
    assert [c.score for c in out] == [0.9, 0.1]
    assert out[0] is a and out[1] is b
