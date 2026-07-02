"""Re-export shim — the pure ranking-metric library moved into `preference/efficacy.py`
(Issue 202) alongside the harness that depends on it, so production code never imports
`tests.*`. Existing imports of `tests.eval.metrics` keep working through this module.
"""

from preference.efficacy import (
    average_precision_at_k,
    bootstrap_ci,
    chronological_split,
    dcg_at_k,
    kendall_tau,
    ndcg_at_k,
    paired_bootstrap_delta,
    reciprocal_rank,
)

__all__ = [
    "average_precision_at_k",
    "bootstrap_ci",
    "chronological_split",
    "dcg_at_k",
    "kendall_tau",
    "ndcg_at_k",
    "paired_bootstrap_delta",
    "reciprocal_rank",
]
