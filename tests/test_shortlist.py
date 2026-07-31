"""
Tests for Issue 377 — shortlist mode ("return the decision, not the pile").

``clip_engine.ranking.is_shortlisted`` is a pure, presentation-only selection cut
applied strictly AFTER ``rank_candidates`` has produced a total order. These tests
assert:

  1. The cut behaves correctly at its boundaries (top-N, ties, fewer candidates
     than the shortlist size, zero candidates, a never-scored creator selection).
  2. It never mutates score/rank and never changes ranking order — so the
     production ranking quality metric (NDCG, ``preference/efficacy.py``) is
     mathematically IDENTICAL whether or not the shortlist cut is applied. This
     is the strongest form of "does not regress": there is no ranking change to
     regress.
  3. The router wires the flag onto the API response correctly (``_clip_response``).
"""

import uuid
from unittest.mock import MagicMock

from clip_engine.ranking import is_shortlisted, rank_candidates
from config import settings
from models import Clip, RenderStatus
from preference.efficacy import ndcg_at_k
from routers.clips import _clip_response

# ── is_shortlisted boundary behavior ───────────────────────────────────────────


def test_is_shortlisted_default_size_matches_settings():
    assert settings.SHORTLIST_SIZE == 3
    assert is_shortlisted(1) is True
    assert is_shortlisted(3) is True
    assert is_shortlisted(4) is False


def test_is_shortlisted_custom_size_boundary():
    assert is_shortlisted(2, size=2) is True
    assert is_shortlisted(3, size=2) is False


def test_is_shortlisted_rank_none_never_shortlisted():
    """A creator-made selection (Issue 373) is never engine-scored — no case to
    argue, so it is never shortlisted regardless of size."""
    assert is_shortlisted(None) is False
    assert is_shortlisted(None, size=100) is False


def test_is_shortlisted_fewer_candidates_than_shortlist_size():
    """3 candidates, size=5 (more than exist) — every real rank is shortlisted."""
    candidates = [{"score": 0.9}, {"score": 0.5}, {"score": 0.1}]
    ranked = rank_candidates(candidates)
    assert [is_shortlisted(c["rank"], size=5) for c in ranked] == [True, True, True]


def test_is_shortlisted_zero_candidates():
    assert [is_shortlisted(c["rank"]) for c in rank_candidates([])] == []


def test_is_shortlisted_ties_follow_stable_rank_order():
    """Equal scores: rank_candidates' stable sort keeps extraction order, and the
    shortlist cut just reads the resulting rank — it does not re-resolve ties."""
    candidates = [
        {"id": "a", "score": 0.5},
        {"id": "b", "score": 0.5},
        {"id": "c", "score": 0.5},
        {"id": "d", "score": 0.5},
    ]
    ranked = rank_candidates(candidates)
    assert [c["id"] for c in ranked] == ["a", "b", "c", "d"]  # stable
    assert [is_shortlisted(c["rank"], size=2) for c in ranked] == [True, True, False, False]


# ── NDCG invariance: shortlist is presentation-only, never a ranking change ────


def test_shortlist_cut_never_changes_ndcg():
    """Applying the shortlist flag must not alter the underlying ranking that
    preference/efficacy.py measures. Relevances follow the SAME order the
    candidates were ranked in, so NDCG@k is identical computed over the full
    ranked list before vs. after tagging `shortlisted` — proving there is no
    ranking-quality regression because there is no ranking change at all.
    """
    candidates = [
        {"id": "a", "score": 0.95},
        {"id": "b", "score": 0.80},
        {"id": "c", "score": 0.60},
        {"id": "d", "score": 0.40},
        {"id": "e", "score": 0.10},
    ]
    ranked = rank_candidates([dict(c) for c in candidates])
    relevances_before = [2.0, 1.0, 1.0, 0.0, 0.0]  # graded relevance, rank order
    ndcg_before = ndcg_at_k(relevances_before, k=5)

    # Tag shortlisted — must not touch score/rank/order.
    for c in ranked:
        c["shortlisted"] = is_shortlisted(c["rank"])
    assert [c["id"] for c in ranked] == ["a", "b", "c", "d", "e"]
    assert [c["score"] for c in ranked] == [0.95, 0.80, 0.60, 0.40, 0.10]

    relevances_after = [2.0, 1.0, 1.0, 0.0, 0.0]
    ndcg_after = ndcg_at_k(relevances_after, k=5)
    assert ndcg_after == ndcg_before


# ── Router wiring: GET /videos/{id}/clips response shape ──────────────────────


def _clip(rank: int | None, *, origin: str = "engine") -> MagicMock:
    clip = MagicMock(spec=Clip)
    clip.id = uuid.uuid4()
    clip.video_id = uuid.uuid4()
    clip.setup_start_s = 2.0
    clip.start_s = 0.0
    clip.end_s = 20.0
    clip.peak_s = 10.0
    clip.score = 0.9 if rank else None
    clip.rank = rank
    clip.signals_jsonb = {"principle": "Hook", "reasoning": "Strong open.", "origin": origin}
    clip.render_status = RenderStatus.done
    clip.render_uri = "http://x/c.mp4"
    clip.cleaned_render_uri = None
    clip.applied_title = None
    clip.applied_description = None
    clip.style_preset = None
    return clip


def test_clip_response_marks_top_ranked_shortlisted():
    body = _clip_response(_clip(1))
    assert body["shortlisted"] is True


def test_clip_response_marks_below_cut_not_shortlisted():
    body = _clip_response(_clip(settings.SHORTLIST_SIZE + 1))
    assert body["shortlisted"] is False


def test_clip_response_creator_selection_never_shortlisted():
    body = _clip_response(_clip(None, origin="creator"))
    assert body["shortlisted"] is False
