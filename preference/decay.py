"""
Recency-weighted sample weights for preference model training.

Half-life is configurable via DECAY_HALF_LIFE_DAYS (default 30) — feedback adapts faster
than channel identity (the DNA builder keeps its SEPARATE 90-day half-life; the two are
intentionally not unified). Parameterized in Issue 200 so the efficacy harness (Issue 198)
can grid-search the half-life on a held-out NDCG@5 split rather than editing this constant.
Clips with performed_well=True receive an additional 3× outcome multiplier.
"""

import math
from datetime import UTC, datetime

from config import settings

# Derived at import from the configured half-life. λ = ln(2)/H so that recency_weight(H)=0.5.
_LAMBDA = math.log(2) / settings.DECAY_HALF_LIFE_DAYS


def _lambda_for(half_life_days: float | None) -> float:
    """Decay rate for an explicit half-life; None → the configured default λ."""
    if half_life_days is None:
        return _LAMBDA
    if half_life_days <= 0:
        raise ValueError(f"half_life_days must be > 0 (got {half_life_days})")
    return math.log(2) / half_life_days


def recency_weight(feedback_age_days: float, half_life_days: float | None = None) -> float:
    """Exponential recency decay: w = e^(-λ * age_days), λ = ln(2)/half-life.

    ``half_life_days`` overrides the configured DECAY_HALF_LIFE_DAYS for one call —
    used by the Issue-200 sweep harness; None keeps production behavior unchanged.
    """
    return math.exp(-_lambda_for(half_life_days) * max(0.0, feedback_age_days))


def feedback_age_days(created_at: datetime) -> float:
    """Days since a feedback row was created."""
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - created_at).total_seconds() / 86400)


def sample_weight(
    created_at: datetime,
    performed_well: bool | None = None,
    outcome_multiplier: float = 3.0,
    half_life_days: float | None = None,
) -> float:
    """
    Combined weight: recency_weight × outcome_multiplier (if performed_well).
    outcome_multiplier only applies when performed_well is explicitly True.
    ``half_life_days`` overrides the configured half-life (None = default).
    """
    w = recency_weight(feedback_age_days(created_at), half_life_days=half_life_days)
    if performed_well is True:
        w *= outcome_multiplier
    return w
