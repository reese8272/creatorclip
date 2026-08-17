"""Issue 480 — preference-rerank eval: a REAL trained model must flip an order.

Before this lane, ``rerank_with_preference`` was tested only with stub scorers
(blend math) and the DNA fixture proved a descending sort — no test ever ran a
TRAINED model over a candidate set, so a personalization regression that
worsens rank 1 for a mature creator was invisible.

The fixture (tests/eval/scenarios/ranking/rerank_preference_flips_order.yaml)
defines deterministic, RNG-free label sets at several class balances and two
label counts, plus two clips whose DNA order must FLIP once the creator's own
feedback is applied through the REAL ``rerank_with_preference``. ``load_latest``
is mocked at the session boundary (the unit-lane convention); everything else —
``fit``, ``predict_score``, ``preference_weight``, ``blend_scores``, the rerank
itself — is production code.

Assertions are ORDERINGS, never float comparisons (LightGBM leaf values are an
implementation detail; the editorial contract is who ranks first).

Issue 521 — WHY THIS FILE WAS PARAMETRISED
------------------------------------------
This eval was written on 2026-08-13 to close exactly the class of defect it then
went on to exhibit. Its fixture was a single ``rows_per_class: 20`` label set,
justified in the docstring as "40 = 2x threshold, so the LightGBM branch runs".
That justification is true and beside the point: 40 rows at a PERFECTLY BALANCED
20/20 is the one and only shape LightGBM's default ``min_child_samples=20`` can
split, because the unique maximum-gain split lands exactly 20/20 and clears the
minimum on both children by a hair. Measured against ``preference.model.fit`` on
lightgbm 4.6.0:

    20/20 (n=40) -> 92 trees, 2 leaves, probability spread 0.999903
    21/19 (n=40) ->  1 tree,  1 leaf,  probability spread 0.000000

One label. The gate certified a knife-edge and reported it as "a REAL trained
preference model flips the order".

Four of these splits (21/19, 24/16, 16/24 at n=40 and 15/15 at n=30) landed as
``xfail(strict=True)`` because they were a constant predictor on that tree. Issue
520 then raised the LightGBM switchover to a measured 60, so the 20-60 range is
served by LogisticRegression and every split discriminates; the markers are gone.
That transition is what ``strict=True`` was for — the markers XPASSed, pytest
failed the run, and the fix could not land while still claiming the defect was
open.

IF YOU ADD A SPLIT AND IT FAILS, the model cannot learn that shape. Do not delete
the split and do not narrow the assertion — that is precisely how this file came
to certify a knife-edge in the first place.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
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


def _shapes() -> list[tuple[int, int]]:
    return [(s["pos"], s["neg"]) for s in _load_fixture()["label_set"]["splits"]]


def _split_params() -> list[Any]:
    """Every fixture split as a pytest param.

    Issue 521 landed four of these (21/19, 24/16, 16/24 at n=40 and 15/15 at n=30)
    as ``xfail(strict=True)`` because they were a MEASURED no-op: LightGBM was
    fitted with ``min_child_samples`` left at its default of 20, so no split was
    legal below 40 rows and the only 40-row shape that could split was exactly
    20/20. Issue 520 raised the LightGBM switchover to a measured 60, so the
    20-60 range is now served by LogisticRegression and all eight splits
    discriminate. The markers were removed as part of that fix — which is exactly
    the transition ``strict=True`` was there to force: they XPASSed, pytest failed
    the run, and the fix could not land while pretending the defect was still open.
    """
    return [pytest.param(*s, id=f"{s[0]}pos-{s[1]}neg-n{sum(s)}") for s in _shapes()]


def _row(base: dict, shared: dict, jitter: float) -> list[float]:
    feats = {**base, **shared}
    for name in _CONTINUOUS:
        feats[name] = feats[name] + jitter
    return clip_features(**feats)


def _train_scorer(fx: dict, n_pos: int, n_neg: int) -> PreferenceScorer:
    """Fit the REAL preference model on a deterministic label set of the given shape."""
    spec = fx["label_set"]
    step = spec["jitter_step"]
    X_rows = [_row(spec["positive"], spec["shared"], step * i) for i in range(n_pos)]
    X_rows += [_row(spec["negative"], spec["shared"], step * i) for i in range(n_neg)]
    y = np.array([1] * n_pos + [0] * n_neg)
    return fit(np.array(X_rows), y, sample_weights=np.ones(n_pos + n_neg))


def _assert_non_degenerate(scorer: PreferenceScorer) -> None:
    """The Issue-521 acceptance criterion: the fitted model must actually discriminate.

    A model that cannot discriminate still round-trips, still serialises, still
    returns a probability, and still lets ``preference_weight`` report a healthy
    non-zero weight — it just returns the SAME probability for every input, which
    makes the downstream blend order-preserving and the whole feature a no-op.
    Every other assertion in this file is downstream of this one holding.

    Dispatches on the fitted estimator so the check survives Issue 520 moving the
    small-n regime to LogisticRegression.
    """
    model = scorer._model  # noqa: SLF001
    if hasattr(model, "booster_"):
        trees = model.booster_.num_trees()
        leaves = max(t["num_leaves"] for t in model.booster_.dump_model()["tree_info"])
        assert trees > 1 and leaves > 1, (
            f"the booster is degenerate at label_count={scorer.label_count}: "
            f"{trees} tree(s), {leaves} leaf/leaves. It has learned no split, so it "
            f"returns a constant probability and the rerank is a provable no-op."
        )
    else:
        coef_max = float(np.abs(model.coef_).max())
        assert coef_max > 0.0, (
            f"the linear model is degenerate at label_count={scorer.label_count}: "
            f"all coefficients are zero, so it returns a constant probability."
        )


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


@pytest.mark.parametrize(("n_pos", "n_neg"), _split_params())
def test_label_set_is_above_threshold_with_a_live_weight(n_pos: int, n_neg: int) -> None:
    """Fixture precondition: every split must sit at or above the personalization
    threshold with a NON-ZERO blend weight — otherwise ``rerank_with_preference``
    early-returns and the split proves nothing about the trained model.

    Deliberately derived from settings rather than asserting "LightGBM at the cap":
    the old form hardcoded both the estimator class and the exact weight, which is
    what let a single fixture shape stand in for the whole ramp.

    Note this passes on EVERY split, including the four that are a measured no-op.
    That gap — a live weight over a model that cannot discriminate — is the
    Issue-520 defect stated as a test result."""
    from config import settings

    fx = _load_fixture()
    scorer = _train_scorer(fx, n_pos, n_neg)
    n = n_pos + n_neg

    assert scorer.label_count == n
    assert n >= settings.PERSONALIZATION_THRESHOLD_LABELS, (
        "split is below the personalization threshold — the rerank would early-return"
    )
    weight = preference_weight(n)
    assert 0.0 < weight <= settings.PREFERENCE_WEIGHT_CAP, (
        f"weight {weight} is not a live blend weight at label_count={n}"
    )


@pytest.mark.parametrize(("n_pos", "n_neg"), _split_params())
def test_trained_model_orders_upvoted_pattern_above_downvoted(n_pos: int, n_neg: int) -> None:
    """The trained scorer itself must prefer the upvoted feature pattern —
    without this, a flip in the rerank could come from blend-math accident."""
    fx = _load_fixture()
    scorer = _train_scorer(fx, n_pos, n_neg)

    # Non-degeneracy FIRST: a constant model fails here with "learned no split"
    # rather than with the ambiguous "does not separate the label patterns".
    _assert_non_degenerate(scorer)

    by_id = {c["id"]: c for c in fx["clips"]}
    p_favorite = scorer.predict_score(clip_features(**by_id["feedback_favorite"]["features"]))
    p_leader = scorer.predict_score(clip_features(**by_id["dna_leader"]["features"]))
    assert p_favorite > p_leader, (
        f"trained model does not separate the label patterns: "
        f"favorite={p_favorite:.3f} leader={p_leader:.3f}"
    )


@pytest.mark.parametrize(("n_pos", "n_neg"), _split_params())
async def test_rerank_flips_the_dna_order(n_pos: int, n_neg: int) -> None:
    """The headline Issue-480 assertion: the REAL rerank over the REAL trained
    model flips the DNA order, honoring the Issue-465 contract (score unmutated,
    blend in blended_score, dense ranks)."""
    fx = _load_fixture()
    scorer = _train_scorer(fx, n_pos, n_neg)
    clips = _clips(fx)
    assert _ids(clips) == fx["expected"]["dna_rank_order"], "fixture pre-condition drifted"
    original_scores = [c.score for c in clips]

    reranked = await _rerank(clips, scorer)

    assert _ids(reranked) == fx["expected"]["reranked_order"], (
        f"preference rerank did not flip the DNA order: got {_ids(reranked)}"
    )
    # Issue-465 contract: fit score never mutated; blend lands in blended_score.
    assert sorted((c.score for c in reranked), reverse=True) == original_scores, (
        "rerank mutated clip.score — Issue 465 contract broken"
    )
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
    trained model, not to the harness.

    One split is enough here (80/20 rule): this exercises the early-return at
    ranking.py, which does not depend on the label set's shape."""
    fx = _load_fixture()
    scorer = _train_scorer(fx, 20, 20)
    clips = _clips(fx)

    with patch("preference.model.preference_weight", lambda n: 0.0):
        result = await _rerank(clips, scorer)

    assert _ids(result) == fx["expected"]["dna_rank_order"], (
        "with weight 0 the DNA order must be preserved (honest fallback)"
    )
    assert all(c.blended_score is None for c in result), (
        "weight 0 must not write blended_score — NULL means personalization not applied"
    )
