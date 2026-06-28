"""Offline ranking-evaluation metrics for the personalization efficacy harness (Issue 198).

Pure functions, zero third-party deps (no numpy/scipy) so they run on any box and are
reusable by Issues 199/200/201/202. Methodology follows the 2026 standard confirmed in
`docs/DECISIONS.md` (2026-06-27, the-moat batch):

- **NDCG@k** (Järvelin & Kekäläinen 2002) is the primary rank-quality metric — graded
  relevance, position-discounted. We also expose MRR (does the single best clip surface
  first?), MAP@k (binary, kept for the AC though NDCG supersedes it), and Kendall's tau-b
  (full-list order correlation).
- Metrics are computed over the FULL candidate set (no sampled negatives — Krichene &
  Rendle 2020 showed sampled-negative NDCG distorts; N/A here since lists are ~6 long).
- **Chronological** train/eval split (Koren 2009) — never random; random leaks future labels.
- **Paired bootstrap** (Sakai 2014) for significance: a ranker beats another iff the 95% CI
  of the per-query metric delta excludes 0 (a t-test is mis-calibrated for bounded NDCG).

Each metric takes `ranked_relevances`: the relevance labels of the candidates IN THE ORDER
the model ranked them (index 0 = top). Build that list by sorting candidates by model score
descending and reading each one's label.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence


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
    a: Sequence[float], b: Sequence[float], n_resamples: int = 10_000, ci: float = 0.95, seed: int = 0
) -> tuple[float, float, float]:
    """Paired bootstrap of the per-query metric delta (a - b). `a` beats `b` at the
    given confidence iff the returned ci_low > 0. Inputs must be aligned per query."""
    if len(a) != len(b):
        raise ValueError(f"paired inputs must be equal length: {len(a)} vs {len(b)}")
    deltas = [ai - bi for ai, bi in zip(a, b, strict=True)]
    return bootstrap_ci(deltas, n_resamples=n_resamples, ci=ci, seed=seed)
