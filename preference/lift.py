"""Proof-of-Lift aggregation helpers (Issue 374).

Pure functions over already-fetched rows so the honesty rules are unit-testable
without a database. The router owns the query (and therefore per-creator
isolation); this module owns the *claims*.

Three rules are load-bearing and exist because over-claiming here would breach
the honesty constraint far more visibly than a bad clip score:

1. ``performed_well`` is TRI-STATE, not boolean. ``worker/tasks.py`` leaves it
   ``None`` when the creator has fewer than ``_MIN_COMPARABLE_SHORTS`` (3)
   comparable published Shorts to form a baseline — an honest "can't judge
   yet" (Issue 201). Those clips are DEFERRED, not failures, and are reported
   as their own bucket. Counting them as underperformers would invent bad news.
2. A COUNT is a fact; a RATE is an inference. Counts are always shown. A rate
   (and any comparative claim) is withheld until ``MIN_JUDGED_FOR_RATE``
   judged clips exist.
3. When a rate IS shown it carries a Wilson score interval, not a bare
   percentage. The Wilson interval is the standard recommendation for binomial
   proportions at small n — it does not rely on asymptotic normality and keeps
   coverage near nominal near p=0 and p=1, where the Wald/normal interval
   collapses to a falsely-tight or out-of-range range.

The metric itself is deliberately NOT re-invented here: it is exactly the
stored ``ClipOutcome.performed_well`` (views >= the median of the creator's
comparable published Shorts, format-matched per Issue 201), so the panel
reports the same fact the preference model trains on.
"""

import math
from dataclasses import dataclass

# Wilson is stable from roughly n=10; below that the interval is so wide it
# communicates nothing a raw count doesn't already say more honestly.
MIN_JUDGED_FOR_RATE = 10

# Both groups need enough members before a winners-vs-underperformers contrast
# is anything but noise. Deliberately small (the beta has little data) but
# non-trivial, and every contrast row is reported with its own group sizes.
MIN_PER_GROUP_FOR_CONTRAST = 3


@dataclass(frozen=True)
class LiftClip:
    """One published clip's outcome plus the stored features used for contrast.

    ``performed_well`` is ``None`` when the verdict is deferred (rule 1).
    """

    clip_id: str
    performed_well: bool | None
    score: float | None
    dna_match: float | None
    duration_s: float | None
    setup_lead_s: float | None
    principle: str | None


def wilson_interval(successes: int, n: int, z: float = 1.959963985) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion, as (low, high) in 0..1.

    Default ``z`` is the two-sided 95% normal quantile. Returns (0.0, 1.0) for
    ``n == 0`` — with no observations every proportion is consistent with the
    data, and that is the honest answer rather than a divide-by-zero.
    """
    if n <= 0:
        return (0.0, 1.0)
    if successes < 0 or successes > n:
        raise ValueError("successes must be within [0, n]")
    p = successes / n
    denom = 1.0 + (z**2) / n
    centre = p + (z**2) / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + (z**2) / (4 * n * n))
    low = (centre - margin) / denom
    high = (centre + margin) / denom
    return (max(0.0, low), min(1.0, high))


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _top_principle(clips: list[LiftClip]) -> str | None:
    counts: dict[str, int] = {}
    for c in clips:
        if c.principle:
            counts[c.principle] = counts.get(c.principle, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]


def summarize_lift(clips: list[LiftClip]) -> dict:
    """Aggregate published-clip outcomes into an honest, claim-gated summary.

    Never returns a rate below ``MIN_JUDGED_FOR_RATE`` judged clips, and never
    returns a contrast unless BOTH groups clear ``MIN_PER_GROUP_FOR_CONTRAST``.
    """
    published = len(clips)
    winners = [c for c in clips if c.performed_well is True]
    losers = [c for c in clips if c.performed_well is False]
    deferred = [c for c in clips if c.performed_well is None]
    judged = len(winners) + len(losers)

    rate: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    if judged >= MIN_JUDGED_FOR_RATE:
        rate = len(winners) / judged
        ci_low, ci_high = wilson_interval(len(winners), judged)

    contrast: dict | None = None
    if len(winners) >= MIN_PER_GROUP_FOR_CONTRAST and len(losers) >= MIN_PER_GROUP_FOR_CONTRAST:
        contrast = {
            "winners_n": len(winners),
            "underperformers_n": len(losers),
            "winners": _group_features(winners),
            "underperformers": _group_features(losers),
        }

    return {
        "published_count": published,
        "judged_count": judged,
        "beat_median_count": len(winners),
        "below_median_count": len(losers),
        "deferred_count": len(deferred),
        "rate": rate,
        "rate_ci_low": ci_low,
        "rate_ci_high": ci_high,
        "min_judged_for_rate": MIN_JUDGED_FOR_RATE,
        "contrast": contrast,
    }


def _group_features(clips: list[LiftClip]) -> dict:
    """Averaged stored features for one group — never generic advice."""
    return {
        "avg_score": _mean([c.score for c in clips if c.score is not None]),
        "avg_dna_match": _mean([c.dna_match for c in clips if c.dna_match is not None]),
        "median_duration_s": _median([c.duration_s for c in clips if c.duration_s is not None]),
        "median_setup_lead_s": _median(
            [c.setup_lead_s for c in clips if c.setup_lead_s is not None]
        ),
        "top_principle": _top_principle(clips),
    }
