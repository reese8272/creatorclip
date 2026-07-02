"""Local tests for the efficacy harness logic (Issue 198).

`compute_creator_metrics` / `pool_metrics` take plain lists, so the 3-ranking construction
and the train→predict path are testable here without Postgres. The DB loaders
(`load_labeled_clips`, `evaluate_creator`) are exercised by the staging integration test.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tests.eval.efficacy import (
    RANKINGS,
    LabeledClip,
    _relevance_for,
    compute_creator_metrics,
    pool_metrics,
)


def _clip(rel, sig_density, dna, when):
    """A LabeledClip whose features/baseline/composite all track `rel` (separable)."""
    return LabeledClip(
        clip_id=f"clip-{when.isoformat()}",
        created_at=when,
        relevance=rel,
        features=[sig_density, 0.0, 0.0, dna, 30.0, 5.0, 0.0, 0.0],
        dna_composite=dna,
        signal_features={
            "signal_density": sig_density,
            "hook_energy": 0.0,
            "has_retention_spike": False,
            "has_laughter": False,
        },
    )


def test_relevance_grading():
    assert _relevance_for("upvote", None) == 1.0
    assert _relevance_for("trim", None) == 1.0
    assert _relevance_for("downvote", None) == 0.0
    # performed_well outranks an explicit keep
    assert _relevance_for("upvote", True) == 2.0
    # skip / unknown actions are excluded entirely
    assert _relevance_for("skip", None) is None
    assert _relevance_for("format", None) is None


def test_below_threshold_blend_falls_back_to_dna_and_beats_random():
    """With train below the personalization threshold the blend weight is 0, so ranking 3 ==
    the DNA composite. With dna aligned to relevance it ranks perfectly (NDCG@5 == 1.0) and
    is >= random — the plumbing + honest cold-start fallback."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    # 5 train clips → below threshold (20) → weight 0.
    train = [_clip(1.0, 5.0, 0.9, base + timedelta(days=i)) for i in range(3)]
    train += [_clip(0.0, 0.0, 0.1, base + timedelta(days=3 + i)) for i in range(2)]
    # eval clips with graded relevance, dna_composite aligned to relevance.
    ev = [
        _clip(2.0, 5.0, 0.95, base + timedelta(days=10)),
        _clip(1.0, 4.0, 0.80, base + timedelta(days=11)),
        _clip(1.0, 3.0, 0.70, base + timedelta(days=12)),
        _clip(0.0, 1.0, 0.20, base + timedelta(days=13)),
        _clip(0.0, 0.0, 0.10, base + timedelta(days=14)),
    ]
    m = compute_creator_metrics(train, ev, k=5, seed=7)
    assert m is not None
    assert m.ndcg["dna_preference"] == pytest.approx(1.0)
    assert m.ndcg["dna_preference"] >= m.ndcg["random"]
    # generic-signal also tracks relevance here, so it should clear random too.
    assert m.mrr["dna_preference"] == pytest.approx(1.0)


def test_trained_scorer_path_ranks_correctly() -> None:
    """Above the threshold the real fit()+predict_score path runs (weight > 0). With perfectly
    separable features the blend ranks positives first → NDCG@5 == 1.0."""
    pytest.importorskip("lightgbm")  # skip if libgomp absent on this host
    base = datetime(2026, 1, 1, tzinfo=UTC)
    # 30 train clips (above threshold 20) → weight > 0; perfectly separable.
    train = []
    for i in range(15):
        train.append(_clip(1.0, 5.0, 0.9, base + timedelta(days=i)))
        train.append(_clip(0.0, 0.0, 0.1, base + timedelta(days=i, hours=1)))
    ev = [
        _clip(1.0, 5.0, 0.9, base + timedelta(days=40)),
        _clip(1.0, 5.0, 0.9, base + timedelta(days=41)),
        _clip(0.0, 0.0, 0.1, base + timedelta(days=42)),
        _clip(0.0, 0.0, 0.1, base + timedelta(days=43)),
    ]
    m = compute_creator_metrics(train, ev, k=5, seed=1)
    assert m is not None
    assert m.ndcg["dna_preference"] == pytest.approx(1.0)


def test_pool_metrics_micro_averages_with_cis():
    base = datetime(2026, 1, 1, tzinfo=UTC)
    ev = [
        _clip(2.0, 5.0, 0.95, base + timedelta(days=10)),
        _clip(1.0, 3.0, 0.70, base + timedelta(days=11)),
        _clip(0.0, 0.0, 0.10, base + timedelta(days=12)),
    ]
    train = [_clip(1.0, 5.0, 0.9, base), _clip(0.0, 0.0, 0.1, base + timedelta(days=1))]
    m1 = compute_creator_metrics(train, ev, k=5, seed=1)
    m2 = compute_creator_metrics(train, ev, k=5, seed=2)
    pooled = pool_metrics([m1, m2])
    assert set(pooled.keys()) == {"ndcg", "map", "mrr", "kendall"}
    for ranking in RANKINGS:
        point, lo, hi = pooled["ndcg"][ranking]
        assert lo <= point <= hi  # a CI band around the point estimate
