"""Budgeted multi-segment selection for stream-VOD recaps (Issue 190).

Pure logic — no DB, no LLM. Given PRE-SCORED candidates (each already carrying
a DNA-fit ``score`` and a named ``principle`` from docs/CLIPPING_PRINCIPLES.md)
and a total-duration budget, select a non-overlapping subset maximizing total
score under the budget, then order it chronologically (narrative order, never
score-descending).

Algorithm — cost-benefit greedy knapsack: run BOTH the plain score-greedy pass
and the score/duration-ratio-greedy pass, and keep whichever subset totals the
higher score. Taking the max of the two greedy solutions is the standard
bound-preserving trick for budgeted (knapsack-style) selection — either pass
alone can be arbitrarily bad, their max carries the classic 1/2-approximation
guarantee for the knapsack objective.

Chapter heuristic (kept deliberately simple): when chapter boundaries are
provided (knowledge/chapters.py ``parse_chapters`` output shape), a candidate
that straddles a boundary mid-segment is demoted by a fixed penalty in the
selection objective, so the greedy passes prefer segments that sit inside one
chapter. The reported per-segment ``score`` is never altered.
"""

import logging
from typing import Any

from config import settings

logger = logging.getLogger(__name__)

# Selection-objective multiplier for candidates that straddle a chapter
# boundary mid-segment. <1.0 demotes without excluding: a straddling segment
# can still win when it is clearly the strongest use of the budget.
CHAPTER_STRADDLE_PENALTY = 0.8


def _duration(c: dict[str, Any]) -> float:
    return float(c["end_s"]) - float(c["start_s"])


def _overlaps(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return float(a["start_s"]) < float(b["end_s"]) and float(b["start_s"]) < float(a["end_s"])


def _chapter_boundaries(chapters: list[dict[str, Any]] | None) -> list[float]:
    """Extract boundary timestamps from parse_chapters()['chapters'] elements."""
    if not chapters:
        return []
    return sorted(float(ch.get("timestamp_s", 0.0)) for ch in chapters)


def _straddles(c: dict[str, Any], boundaries: list[float]) -> bool:
    """True when a chapter boundary falls strictly inside (start_s, end_s)."""
    return any(float(c["start_s"]) < b < float(c["end_s"]) for b in boundaries)


def _objective(c: dict[str, Any], boundaries: list[float]) -> float:
    """Selection objective: score, demoted when the segment straddles a chapter."""
    score = float(c["score"])
    if _straddles(c, boundaries):
        score *= CHAPTER_STRADDLE_PENALTY
    return score


def _greedy(
    candidates: list[dict[str, Any]],
    budget_s: float,
    boundaries: list[float],
    by_ratio: bool,
) -> tuple[list[dict[str, Any]], float]:
    """One greedy pass. Returns (chosen, total objective value).

    ``by_ratio=False`` orders by plain objective score; ``by_ratio=True`` by
    objective score per second (cost-benefit). Both enforce the budget and the
    pairwise non-overlap constraint.
    """

    def key(c: dict[str, Any]) -> float:
        obj = _objective(c, boundaries)
        return obj / _duration(c) if by_ratio else obj

    chosen: list[dict[str, Any]] = []
    remaining = budget_s
    total = 0.0
    for c in sorted(candidates, key=key, reverse=True):
        d = _duration(c)
        if d > remaining:
            continue
        if any(_overlaps(c, k) for k in chosen):
            continue
        chosen.append(c)
        remaining -= d
        total += _objective(c, boundaries)
    return chosen, total


def select_recap_segments(
    candidates: list[dict[str, Any]],
    budget_s: float | None = None,
    chapters: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Select non-overlapping recap segments under a total-duration budget.

    Args:
        candidates: PRE-SCORED segment candidates. Each dict must carry
            ``start_s``, ``end_s``, ``score`` (this creator's DNA-fit score),
            and ``principle`` (exact name from docs/CLIPPING_PRINCIPLES.md);
            ``rationale`` is carried through when present.
        budget_s: hard cap on the summed segment duration. Defaults to
            ``settings.RECAP_TARGET_DURATION_MAX_S``.
        chapters: optional ``parse_chapters()['chapters']`` list; segments
            straddling a chapter boundary are demoted (never mutated).

    Returns:
        The chosen segments in CHRONOLOGICAL order (narrative, not
        score-descending), each the normalized element shape persisted to
        ``summaries.segments``: start_s, end_s, score, principle, rationale.
        Returns ``[]`` gracefully for empty/unusable input (too-short source).
    """
    if budget_s is None:
        budget_s = float(settings.RECAP_TARGET_DURATION_MAX_S)

    usable = [
        c
        for c in candidates
        if _duration(c) > 0 and _duration(c) <= budget_s and float(c["score"]) > 0
    ]
    if not usable:
        logger.info("recap selection: no usable candidates (of %d)", len(candidates))
        return []

    boundaries = _chapter_boundaries(chapters)
    by_score, score_total = _greedy(usable, budget_s, boundaries, by_ratio=False)
    by_ratio, ratio_total = _greedy(usable, budget_s, boundaries, by_ratio=True)
    chosen = by_score if score_total >= ratio_total else by_ratio

    chosen.sort(key=lambda c: float(c["start_s"]))
    logger.info(
        "recap selection: %d/%d segments, %.1fs of %.1fs budget",
        len(chosen),
        len(usable),
        sum(_duration(c) for c in chosen),
        budget_s,
    )
    return [
        {
            "start_s": float(c["start_s"]),
            "end_s": float(c["end_s"]),
            "score": float(c["score"]),
            "principle": str(c["principle"]),
            "rationale": str(c.get("rationale", "")),
        }
        for c in chosen
    ]
