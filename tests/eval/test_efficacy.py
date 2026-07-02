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
    SweepRow,
    _relevance_for,
    _train_scorer,
    compute_creator_metrics,
    pool_metrics,
    select_best_half_life,
    sweep_half_life,
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
    try:
        m = compute_creator_metrics(train, ev, k=5, seed=1)
    except OSError:
        pytest.skip("libgomp.so.1 not available on this host")
    assert m is not None
    assert m.ndcg["dna_preference"] == pytest.approx(1.0)


def _pivot_clip(style: str, rel: float, when: datetime) -> LabeledClip:
    """A LabeledClip whose ONLY discriminative feature is its style flag: style A =
    has_laughter, style B = has_retention_spike. Everything else is constant so the
    scorer can only learn from the style axis."""
    laughter = 1.0 if style == "A" else 0.0
    retention = 1.0 if style == "B" else 0.0
    return LabeledClip(
        clip_id=f"pivot-{style}-{when.isoformat()}",
        created_at=when,
        relevance=rel,
        features=[1.0, 0.0, 0.0, 0.5, 30.0, 5.0, retention, laughter],
        dna_composite=0.5,
        signal_features={
            "signal_density": 1.0,
            "hook_energy": 0.0,
            "has_retention_spike": retention == 1.0,
            "has_laughter": laughter == 1.0,
        },
    )


def test_recency_decay_actually_reweights_feedback_concept_pivot() -> None:
    """The CLAUDE.md 'recency decay actually reweights feedback' gate (Issue 200).

    Concept pivot: OLD feedback (180d ago, and more of it) favors style A; RECENT
    feedback (1d ago) favors style B. The decayed scorer (configured 30d half-life)
    must rank a B-aligned clip above an A-aligned one; the undecayed control (huge
    half-life → flat weights) must not — it follows the old majority to A."""
    now = datetime.now(UTC)
    old = now - timedelta(days=180)
    recent = now - timedelta(days=1)
    train = []
    for i in range(6):  # old majority: A upvoted, B downvoted
        train.append(_pivot_clip("A", 1.0, old + timedelta(minutes=i)))
        train.append(_pivot_clip("B", 0.0, old + timedelta(minutes=30 + i)))
    for i in range(3):  # recent pivot: B upvoted, A downvoted
        train.append(_pivot_clip("B", 1.0, recent + timedelta(minutes=i)))
        train.append(_pivot_clip("A", 0.0, recent + timedelta(minutes=30 + i)))

    try:
        decayed = _train_scorer(train)  # configured DECAY_HALF_LIFE_DAYS (30d)
        undecayed = _train_scorer(train, half_life_days=1e9)  # ~flat weights control
    except OSError:
        pytest.skip("libgomp.so.1 not available on this host")
    assert decayed is not None and undecayed is not None

    a_clip = _pivot_clip("A", 1.0, now)
    b_clip = _pivot_clip("B", 1.0, now)
    assert decayed.predict_score(b_clip.features) > decayed.predict_score(a_clip.features), (
        "decayed model must follow the RECENT style-B pivot"
    )
    assert undecayed.predict_score(a_clip.features) > undecayed.predict_score(b_clip.features), (
        "undecayed control must follow the OLD style-A majority"
    )


def test_sweep_half_life_row_per_grid_point() -> None:
    """One SweepRow per grid value, in grid order, each with a CI band around the point."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    clips = []
    for i in range(7):  # train split (train_frac=0.7 of 10): both classes present
        clips.append(
            _clip(1.0 if i % 2 == 0 else 0.0, float(i % 2) * 5.0, 0.5, base + timedelta(days=i))
        )
    clips += [  # eval split: 3 clips, 2 relevance levels
        _clip(1.0, 5.0, 0.9, base + timedelta(days=7)),
        _clip(0.0, 0.0, 0.1, base + timedelta(days=8)),
        _clip(1.0, 5.0, 0.9, base + timedelta(days=9)),
    ]
    grid = (15.0, 30.0, 60.0)
    try:
        rows = sweep_half_life(clips, grid=grid, k=5)
    except OSError:
        pytest.skip("libgomp.so.1 not available on this host")
    assert [r.half_life_days for r in rows] == list(grid)
    for r in rows:
        assert r.n_creators == 1
        assert 0.0 <= r.ndcg_at_k <= 1.0
        assert r.ci_low <= r.ndcg_at_k <= r.ci_high


def test_select_best_half_life_ties_break_to_larger() -> None:
    """Exact NDCG ties resolve toward the LARGER half-life (incl. an inf control row);
    a strictly better score still wins regardless of size."""
    import math

    tied = [
        SweepRow(half_life_days=15.0, ndcg_at_k=0.8, ci_low=0.7, ci_high=0.9, n_creators=3),
        SweepRow(half_life_days=90.0, ndcg_at_k=0.8, ci_low=0.7, ci_high=0.9, n_creators=3),
        SweepRow(half_life_days=math.inf, ndcg_at_k=0.8, ci_low=0.7, ci_high=0.9, n_creators=3),
    ]
    assert select_best_half_life(tied).half_life_days == math.inf

    better_small = tied[:2] + [
        SweepRow(half_life_days=15.0, ndcg_at_k=0.95, ci_low=0.9, ci_high=1.0, n_creators=3)
    ]
    assert select_best_half_life(better_small).ndcg_at_k == 0.95
    with pytest.raises(ValueError):
        select_best_half_life([])


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
