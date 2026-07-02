"""Personalization efficacy harness (Issues 198/200/202) — DB-backed, read-only.

Answers the moat question "is the ranking actually GOOD for this creator?" by comparing
three rankings on each creator's chronologically held-out labels:

  1. random            — sanity floor
  2. generic-signal    — clip_engine.scoring._signal_score (cold-start, no DNA/preference);
                         the honest stand-in for a non-personalized ranker
  3. dna+preference    — the production blend: (1-w)*clip.score + w*scorer.predict_score(feats),
                         w = preference_weight(train_label_count)

Methodology (DECISIONS 2026-06-27, the-moat batch): chronological split (never random — that
leaks future labels), graded relevance (performed_well=2 > upvote/trim=1 > downvote=0; skip
excluded), pooled micro-average across creators as the primary number + per-creator-above-N as
a secondary breakdown, all with paired-bootstrap 95% CIs.

Metric functions are pure (no numpy/scipy) and follow the 2026 standard confirmed in
`docs/DECISIONS.md`:

- **NDCG@k** (Järvelin & Kekäläinen 2002) is the primary rank-quality metric — graded
  relevance, position-discounted. We also expose MRR, MAP@k, and Kendall's tau-b.
- Metrics are computed over the FULL candidate set (no sampled negatives — Krichene &
  Rendle 2020 showed sampled-negative NDCG distorts; N/A here since lists are ~6 long).
- **Chronological** train/eval split (Koren 2009) — never random.
- **Paired bootstrap** (Sakai 2014) for significance: a ranker beats another iff the 95% CI
  of the per-query metric delta excludes 0.

This module lives in `preference/` (extracted from `tests/eval/` in Issue 202) so the worker
can emit per-retrain metrics WITHOUT importing `tests.*`; the tests/eval modules re-export it.
It is import-light at module scope (SQLAlchemy/preference imports live inside functions) and
NEVER calls live Anthropic or YouTube — it reads already-persisted clip features/labels only.
"""

from __future__ import annotations

import math
import random
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

# ── Ranking metrics (pure, dependency-free) ───────────────────────────────────


def dcg_at_k(relevances: Sequence[float], k: int) -> float:
    """Discounted cumulative gain over the first k positions."""
    return sum(rel / math.log2(i + 2) for i, rel in enumerate(relevances[:k]))


def ndcg_at_k(ranked_relevances: Sequence[float], k: int) -> float:
    """Normalized DCG@k in [0, 1]. 0 when there is no relevance to recover."""
    ideal = sorted(ranked_relevances, reverse=True)
    idcg = dcg_at_k(ideal, k)
    if idcg <= 0:
        return 0.0
    return dcg_at_k(ranked_relevances, k) / idcg


def average_precision_at_k(ranked_binary: Sequence[float], k: int) -> float:
    """AP@k for binary relevance: mean of precision@i at each hit, normalized by
    min(#relevant, k). 0 when there are no relevant items."""
    hits = 0
    running = 0.0
    for i, rel in enumerate(ranked_binary[:k]):
        if rel > 0:
            hits += 1
            running += hits / (i + 1)
    n_rel = sum(1 for r in ranked_binary if r > 0)
    denom = min(n_rel, k)
    return running / denom if denom else 0.0


def reciprocal_rank(ranked_binary: Sequence[float]) -> float:
    """1 / (rank of the first relevant item); 0 if none are relevant."""
    for i, rel in enumerate(ranked_binary):
        if rel > 0:
            return 1.0 / (i + 1)
    return 0.0


def kendall_tau(x: Sequence[float], y: Sequence[float]) -> float:
    """Kendall's tau-b rank correlation between two paired sequences, with tie
    correction. Returns 0.0 for degenerate input (n<2 or all-tied)."""
    n = len(x)
    if n < 2 or len(y) != n:
        return 0.0
    concordant = discordant = ties_x = ties_y = 0
    for i in range(n):
        for j in range(i + 1, n):
            dx = x[i] - x[j]
            dy = y[i] - y[j]
            prod = dx * dy
            if prod > 0:
                concordant += 1
            elif prod < 0:
                discordant += 1
            else:  # at least one pair is tied
                if dx == 0:
                    ties_x += 1
                if dy == 0:
                    ties_y += 1
    n0 = n * (n - 1) / 2
    denom = math.sqrt((n0 - ties_x) * (n0 - ties_y))
    if denom <= 0:
        return 0.0
    return (concordant - discordant) / denom


def chronological_split[T](
    items: Sequence[T], key: Callable[[T], object], train_frac: float = 0.7
) -> tuple[list[T], list[T]]:
    """Time-ordered split: oldest `train_frac` → train, newest remainder → eval.
    NEVER random — a random split leaks future labels into the past (Koren 2009)."""
    if not 0.0 < train_frac < 1.0:
        raise ValueError(f"train_frac must be in (0, 1), got {train_frac}")
    ordered = sorted(items, key=key)  # type: ignore[arg-type]
    cut = int(len(ordered) * train_frac)
    return ordered[:cut], ordered[cut:]


def bootstrap_ci(
    values: Sequence[float], n_resamples: int = 10_000, ci: float = 0.95, seed: int = 0
) -> tuple[float, float, float]:
    """(point estimate, ci_low, ci_high) for the mean of `values` via the bootstrap.
    Deterministic for a fixed seed."""
    if not values:
        return (0.0, 0.0, 0.0)
    rng = random.Random(seed)
    m = len(values)
    point = sum(values) / m
    means = []
    for _ in range(n_resamples):
        means.append(sum(values[rng.randrange(m)] for _ in range(m)) / m)
    means.sort()
    lo = means[int((1 - ci) / 2 * n_resamples)]
    hi = means[min(int((1 + ci) / 2 * n_resamples), n_resamples - 1)]
    return (point, lo, hi)


def paired_bootstrap_delta(
    a: Sequence[float],
    b: Sequence[float],
    n_resamples: int = 10_000,
    ci: float = 0.95,
    seed: int = 0,
) -> tuple[float, float, float]:
    """Paired bootstrap of the per-query metric delta (a - b). `a` beats `b` at the
    given confidence iff the returned ci_low > 0. Inputs must be aligned per query."""
    if len(a) != len(b):
        raise ValueError(f"paired inputs must be equal length: {len(a)} vs {len(b)}")
    deltas = [ai - bi for ai, bi in zip(a, b, strict=True)]
    return bootstrap_ci(deltas, n_resamples=n_resamples, ci=ci, seed=seed)


# ── Harness core ──────────────────────────────────────────────────────────────

# Graded relevance for ranking eval. A published clip that performed well is the strongest
# positive; an explicit keep (upvote/trim) is a positive; a downvote is the negative floor.
# `skip` is excluded entirely (matches training; position-bias-confounded — IPS deferred to v2).
_REL_PERFORMED_WELL = 2.0
_REL_KEEP = 1.0
_REL_DOWNVOTE = 0.0

DEFAULT_K = 5
DEFAULT_MIN_LABELS = 30  # per-creator metrics below this are noise → pooled only


@dataclass
class LabeledClip:
    """One held-out clip with its label and the feature vector needed to re-score it."""

    clip_id: uuid.UUID
    created_at: datetime  # the chronological-split key
    relevance: float
    features: list[float]
    dna_composite: float  # clip.score (the DNA composite the production blend uses)
    signal_features: dict  # signals_jsonb['features'] for the generic-signal baseline


@dataclass
class CreatorMetrics:
    creator_id: uuid.UUID
    n_eval: int
    ndcg: dict[str, float] = field(default_factory=dict)  # ranking -> NDCG@k
    map: dict[str, float] = field(default_factory=dict)
    mrr: dict[str, float] = field(default_factory=dict)
    kendall: dict[str, float] = field(default_factory=dict)


RANKINGS = ("random", "generic_signal", "dna_preference")


def _relevance_for(action_value: str, performed_well: bool | None) -> float | None:
    """Graded relevance from a feedback action + outcome. None means 'exclude this label'."""
    if performed_well is True:
        return _REL_PERFORMED_WELL
    if action_value in ("upvote", "trim"):
        return _REL_KEEP
    if action_value == "downvote":
        return _REL_DOWNVOTE
    return None  # skip / format / anything else → excluded


def _ranked_relevances(clips: list[LabeledClip], order_scores: list[float]) -> list[float]:
    """Relevances in the order induced by `order_scores` (descending)."""
    paired = sorted(zip(order_scores, clips, strict=True), key=lambda t: t[0], reverse=True)
    return [c.relevance for _, c in paired]


def _binary(rels: list[float]) -> list[float]:
    """Binarize graded relevance for MAP/MRR (anything above the downvote floor is relevant)."""
    return [1.0 if r > _REL_DOWNVOTE else 0.0 for r in rels]


def compute_creator_metrics(
    train: list[LabeledClip],
    eval_clips: list[LabeledClip],
    k: int = DEFAULT_K,
    seed: int = 0,
) -> CreatorMetrics | None:
    """Build the three rankings on the eval split and compute the metric table for one creator.

    Returns None when the eval split has <2 distinct relevance levels (a ranking metric is
    meaningless when every held-out clip has the same label).
    """
    import random as _random

    from clip_engine.scoring import _signal_score

    if len({c.relevance for c in eval_clips}) < 2:
        return None

    # Ranking 1 — random (deterministic per seed).
    rng = _random.Random(seed)
    rand_scores = [rng.random() for _ in eval_clips]

    # Ranking 2 — generic signal (cold-start scorer, no DNA/preference).
    signal_scores = [_signal_score(c.signal_features) for c in eval_clips]

    # Ranking 3 — production DNA+preference blend. Train a scorer on the TRAIN split only
    # (no leakage), then blend exactly as rerank_with_preference does. Below the
    # personalization threshold the weight is 0 → the blend reduces to the DNA composite,
    # which is the honest production behavior.
    blend_scores = _blend_scores(train, eval_clips)

    order = {"random": rand_scores, "generic_signal": signal_scores, "dna_preference": blend_scores}
    m = CreatorMetrics(creator_id=eval_clips[0].clip_id, n_eval=len(eval_clips))
    m.creator_id = getattr(eval_clips[0], "creator_id", m.creator_id)
    for name, scores in order.items():
        rels = _ranked_relevances(eval_clips, scores)
        binary = _binary(rels)
        m.ndcg[name] = ndcg_at_k(rels, k)
        m.map[name] = average_precision_at_k(binary, k)
        m.mrr[name] = reciprocal_rank(binary)
        # Kendall tau: predicted order vs the true relevance order (full list).
        m.kendall[name] = kendall_tau(scores, [c.relevance for c in eval_clips])
    return m


def _train_scorer(train: list[LabeledClip], half_life_days: float | None = None):
    """Fit a PreferenceScorer on the train split, reproducing preference.train.build_and_save's
    label/weight construction. Returns None when there aren't 2 classes to fit.

    ``half_life_days`` overrides the configured recency half-life for the sample weights —
    the Issue-200 sweep retrains per candidate half-life; None keeps production behavior.
    """
    import numpy as np

    from preference.model import fit

    if len(train) < 2:
        return None
    y = [1 if c.relevance >= _REL_KEEP else 0 for c in train]
    if len(set(y)) < 2:
        return None
    # Recency/outcome sample weights mirror production (decay × outcome multiplier). We pass a
    # flat weight here when created_at handling differs per fixture; the production weighting is
    # exercised by preference.decay's own tests. Keep the fit identical otherwise.
    from preference.decay import sample_weight

    X = np.array([c.features for c in train], dtype=float)
    w = np.array(
        [
            sample_weight(
                c.created_at,
                performed_well=(c.relevance >= _REL_PERFORMED_WELL),
                half_life_days=half_life_days,
            )
            for c in train
        ],
        dtype=float,
    )
    return fit(X, np.array(y, dtype=int), w)


def _blend_scores(
    train: list[LabeledClip],
    eval_clips: list[LabeledClip],
    half_life_days: float | None = None,
) -> list[float]:
    """Production DNA+preference blend scores for the eval split, trained on `train` only.

    Below the personalization threshold the weight is 0 → the blend reduces to the DNA
    composite (the honest production fallback).
    """
    from preference.model import preference_weight

    scorer = _train_scorer(train, half_life_days=half_life_days)
    weight = preference_weight(len(train)) if scorer is not None else 0.0
    if scorer is not None and weight > 0.0:
        return [
            (1.0 - weight) * c.dna_composite + weight * scorer.predict_score(c.features)
            for c in eval_clips
        ]
    return [c.dna_composite for c in eval_clips]


DEFAULT_SWEEP_GRID: tuple[float, ...] = (15.0, 30.0, 60.0, 90.0)


@dataclass
class SweepRow:
    """Pooled NDCG@k (+ bootstrap 95% CI) for one candidate half-life."""

    half_life_days: float
    ndcg_at_k: float
    ci_low: float
    ci_high: float
    n_creators: int


def sweep_half_life(
    labeled_clips: list[list[LabeledClip]] | list[LabeledClip],
    grid: tuple[float, ...] = DEFAULT_SWEEP_GRID,
    k: int = DEFAULT_K,
    train_frac: float = 0.7,
) -> list[SweepRow]:
    """Grid-search the recency half-life on held-out NDCG@k (Issue 200).

    ``labeled_clips`` is one list of LabeledClip per creator (a single flat list is
    treated as one creator). Each creator is split chronologically ONCE; the scorer is
    retrained per candidate half-life on the same train split so rows differ only by
    the decay. Returns one SweepRow (pooled NDCG@k + bootstrap CI) per grid value.
    """
    if labeled_clips and isinstance(labeled_clips[0], LabeledClip):
        creators: list[list[LabeledClip]] = [labeled_clips]  # type: ignore[list-item]
    else:
        creators = labeled_clips  # type: ignore[assignment]

    splits: list[tuple[list[LabeledClip], list[LabeledClip]]] = []
    for clips in creators:
        if len(clips) < 4:
            continue
        train, eval_clips = chronological_split(
            clips, key=lambda c: c.created_at, train_frac=train_frac
        )
        if len(train) < 2 or len(eval_clips) < 2:
            continue
        if len({c.relevance for c in eval_clips}) < 2:
            continue
        splits.append((train, eval_clips))

    rows: list[SweepRow] = []
    for h in grid:
        ndcgs = [
            ndcg_at_k(_ranked_relevances(ev, _blend_scores(tr, ev, half_life_days=h)), k)
            for tr, ev in splits
        ]
        point, lo, hi = bootstrap_ci(ndcgs) if ndcgs else (0.0, 0.0, 0.0)
        rows.append(
            SweepRow(
                half_life_days=h, ndcg_at_k=point, ci_low=lo, ci_high=hi, n_creators=len(ndcgs)
            )
        )
    return rows


def select_best_half_life(rows: list[SweepRow]) -> SweepRow:
    """The row with the best pooled NDCG; ties break toward the LARGER half-life (the
    less aggressive decay — prefer the simpler prior when the data can't separate them)."""
    if not rows:
        raise ValueError("sweep produced no rows")
    return max(rows, key=lambda r: (r.ndcg_at_k, r.half_life_days))


def pool_metrics(
    per_creator: list[CreatorMetrics],
) -> dict[str, dict[str, tuple[float, float, float]]]:
    """Micro-average each metric across creators with bootstrap 95% CIs.

    Returns {metric_name: {ranking: (point, ci_low, ci_high)}}. Pooled is the PRIMARY number;
    per-creator (the input list) is the secondary breakdown.
    """
    out: dict[str, dict[str, tuple[float, float, float]]] = {}
    for metric in ("ndcg", "map", "mrr", "kendall"):
        out[metric] = {}
        for ranking in RANKINGS:
            vals = [
                getattr(cm, metric)[ranking] for cm in per_creator if ranking in getattr(cm, metric)
            ]
            out[metric][ranking] = bootstrap_ci(vals) if vals else (0.0, 0.0, 0.0)
    return out


async def load_labeled_clips(session, creator_id: uuid.UUID) -> list[LabeledClip]:
    """Load a creator's trainable, labeled clips (read-only) for the harness.

    Mirrors preference.train.build_and_save's query: trainable feedback joined to its clip and
    (optionally) its outcome. Graded relevance comes from the outcome/action; skip is excluded.
    """
    from sqlalchemy import select

    from models import Clip, ClipFeedback, ClipOutcome
    from preference.features import clip_features

    result = await session.execute(
        select(ClipFeedback, Clip, ClipOutcome)
        .join(Clip, Clip.id == ClipFeedback.clip_id)
        .outerjoin(ClipOutcome, ClipOutcome.clip_id == ClipFeedback.clip_id)
        .where(ClipFeedback.creator_id == creator_id)
        .order_by(ClipFeedback.created_at.asc())
    )
    labeled: list[LabeledClip] = []
    for feedback, clip, outcome in result.all():
        performed_well = outcome.performed_well if outcome else None
        rel = _relevance_for(
            getattr(feedback.action, "value", str(feedback.action)), performed_well
        )
        if rel is None:
            continue
        feats_dict = (clip.signals_jsonb or {}).get("features", {})
        lc = LabeledClip(
            clip_id=clip.id,
            created_at=feedback.created_at,
            relevance=rel,
            features=clip_features(
                signal_density=feats_dict.get("signal_density", 0.0),
                hook_energy=feats_dict.get("hook_energy", 0.0),
                silence_ratio=feats_dict.get("silence_ratio", 0.0),
                dna_match=clip.dna_match,
                clip_duration_s=feats_dict.get("clip_duration_s", 0.0),
                setup_length_s=feats_dict.get("setup_length_s", 0.0),
                has_retention_spike=feats_dict.get("has_retention_spike", False),
                has_laughter=feats_dict.get("has_laughter", False),
            ),
            dna_composite=float(clip.score or 0.0),
            signal_features={
                "signal_density": feats_dict.get("signal_density", 0.0),
                "hook_energy": feats_dict.get("hook_energy", 0.0),
                "has_retention_spike": feats_dict.get("has_retention_spike", False),
                "has_laughter": feats_dict.get("has_laughter", False),
            },
        )
        lc.creator_id = creator_id  # type: ignore[attr-defined]
        labeled.append(lc)
    return labeled


async def evaluate_creator(
    session, creator_id: uuid.UUID, k: int = DEFAULT_K, train_frac: float = 0.7
) -> CreatorMetrics | None:
    """End-to-end per-creator eval: load → chronological split → metric table."""
    clips = await load_labeled_clips(session, creator_id)
    if len(clips) < 4:  # need enough to split and have ≥2 in eval
        return None
    train, eval_clips = chronological_split(
        clips, key=lambda c: c.created_at, train_frac=train_frac
    )
    if len(eval_clips) < 2 or len(train) < 2:
        return None
    return compute_creator_metrics(train, eval_clips, k=k)
