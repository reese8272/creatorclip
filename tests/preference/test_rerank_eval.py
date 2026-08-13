"""Issue 480 — preference-rerank eval: a REAL trained model must flip an order.

Before this lane, ``rerank_with_preference`` was tested only with stub scorers
(blend math) and the DNA fixture proved a descending sort — no test ever ran a
TRAINED model over a candidate set, so a personalization regression that
worsens rank 1 for a mature creator was invisible.

The fixture (tests/eval/scenarios/ranking/rerank_preference_flips_order.yaml)
defines a deterministic, RNG-free 40-row label set (40 = 2x the
personalization threshold → the LightGBM branch, ``preference_weight`` exactly
at PREFERENCE_WEIGHT_CAP) and two clips whose DNA order must FLIP once the
creator's own feedback is applied through the REAL ``rerank_with_preference``.
``load_latest`` is mocked at the session boundary (the unit-lane convention);
everything else — ``fit``, ``predict_score``, ``preference_weight``,
``blend_scores``, the rerank itself — is production code.

Assertions are ORDERINGS, never float comparisons (LightGBM leaf values are an
implementation detail; the editorial contract is who ranks first).
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import yaml

from clip_engine.ranking import rerank_with_preference
from models import Clip
from preference.features import clip_features
from preference.model import PreferenceScorer, fit, preference_weight

_FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "eval",
    "scenarios",
    "ranking",
    "rerank_preference_flips_order.yaml",
)

_CONTINUOUS = (
    "signal_density",
    "hook_energy",
    "silence_ratio",
    "clip_duration_s",
    "setup_length_s",
)


def _load_fixture() -> dict:
    with open(_FIXTURE, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _row(base: dict, shared: dict, jitter: float) -> list[float]:
    feats = {**base, **shared}
    for name in _CONTINUOUS:
        feats[name] = feats[name] + jitter
    return clip_features(**feats)


def _train_scorer(fx: dict) -> PreferenceScorer:
    """Fit the REAL preference model on the fixture's deterministic label set."""
    spec = fx["label_set"]
    n, step = spec["rows_per_class"], spec["jitter_step"]
    X_rows = [_row(spec["positive"], spec["shared"], step * (i % n)) for i in range(n)]
    X_rows += [_row(spec["negative"], spec["shared"], step * (i % n)) for i in range(n)]
    y = np.array([1] * n + [0] * n)
    return fit(np.array(X_rows), y, sample_weights=np.ones(2 * n))


def _clips(fx: dict) -> list[Clip]:
    """In-memory Clip rows in DNA rank order (rank 1 = highest fit score)."""
    ordered = sorted(fx["clips"], key=lambda c: c["score"], reverse=True)
    clips = []
    for i, spec in enumerate(ordered):
        clips.append(
            Clip(
                score=spec["score"],
                dna_match=spec["dna_match"],
                signals_jsonb={"features": dict(spec["features"]), "id": spec["id"]},
                rank=i + 1,
            )
        )
    return clips


async def _rerank(clips: list[Clip], scorer: PreferenceScorer | None) -> list[Clip]:
    """Drive the REAL rerank_with_preference, mocking only the session-boundary
    model load (preference.train.load_latest) — the unit-lane convention."""
    import uuid

    with patch("preference.train.load_latest", AsyncMock(return_value=scorer)):
        return await rerank_with_preference(clips, MagicMock(), uuid.uuid4())


def _ids(clips: list[Clip]) -> list[str]:
    return [c.signals_jsonb["id"] for c in clips]


# ── The eval ─────────────────────────────────────────────────────────────────


def test_label_set_hits_lightgbm_branch_at_cap_weight() -> None:
    """40 labels = 2x threshold: fit() must take the LightGBM branch and
    preference_weight must sit exactly at the cap — the fixture is only a valid
    probe of the mature-creator path if both hold."""
    from lightgbm import LGBMClassifier

    from config import settings

    fx = _load_fixture()
    scorer = _train_scorer(fx)
    assert scorer.label_count == 2 * settings.PERSONALIZATION_THRESHOLD_LABELS
    assert isinstance(scorer._model, LGBMClassifier)  # noqa: SLF001
    assert preference_weight(scorer.label_count) == settings.PREFERENCE_WEIGHT_CAP


def test_trained_model_orders_upvoted_pattern_above_downvoted() -> None:
    """The trained scorer itself must prefer the upvoted feature pattern —
    without this, a flip in the rerank could come from blend-math accident."""
    fx = _load_fixture()
    scorer = _train_scorer(fx)
    by_id = {c["id"]: c for c in fx["clips"]}
    p_favorite = scorer.predict_score(clip_features(**by_id["feedback_favorite"]["features"]))
    p_leader = scorer.predict_score(clip_features(**by_id["dna_leader"]["features"]))
    assert p_favorite > p_leader, (
        f"trained model does not separate the label patterns: "
        f"favorite={p_favorite:.3f} leader={p_leader:.3f}"
    )


async def test_rerank_at_cap_weight_flips_the_dna_order() -> None:
    """The headline Issue-480 assertion: the REAL rerank over the REAL trained
    model flips the DNA order at w=cap, honoring the Issue-465 contract
    (score unmutated, blend in blended_score, dense ranks)."""
    fx = _load_fixture()
    scorer = _train_scorer(fx)
    clips = _clips(fx)
    assert _ids(clips) == fx["expected"]["dna_rank_order"], "fixture pre-condition drifted"
    original_scores = [c.score for c in clips]

    reranked = await _rerank(clips, scorer)

    assert _ids(reranked) == fx["expected"]["reranked_order"], (
        f"preference rerank did not flip the DNA order: got {_ids(reranked)}"
    )
    # Issue-465 contract: fit score never mutated; blend lands in blended_score.
    assert sorted(
        (c.score for c in reranked), reverse=True
    ) == original_scores, "rerank mutated clip.score — Issue 465 contract broken"
    assert all(c.blended_score is not None for c in reranked)
    # Ordering (never float equality): blended_score order matches the new rank.
    blended = [c.blended_score for c in reranked]
    assert blended == sorted(blended, reverse=True)
    assert [c.rank for c in reranked] == list(range(1, len(reranked) + 1)), (
        "reranked ranks are not dense 1..n"
    )


async def test_control_weight_zero_preserves_dna_order() -> None:
    """Control (proves the flip test CAN fail): with the preference weight
    forced to 0 — a sub-threshold creator — the same clips come back in DNA
    order with NO blended_score, so the flip above is attributable to the
    trained model, not to the harness."""
    fx = _load_fixture()
    scorer = _train_scorer(fx)
    clips = _clips(fx)

    with patch("preference.model.preference_weight", lambda n: 0.0):
        result = await _rerank(clips, scorer)

    assert _ids(result) == fx["expected"]["dna_rank_order"], (
        "with weight 0 the DNA order must be preserved (honest fallback)"
    )
    assert all(c.blended_score is None for c in result), (
        "weight 0 must not write blended_score — NULL means personalization not applied"
    )
