"""Re-export shim — the efficacy harness core moved to `preference/efficacy.py` (Issue 202)
so the worker can emit per-retrain metrics without importing `tests.*`. Existing test/script
imports of `tests.eval.efficacy` keep working through this module.
"""

from preference.efficacy import (
    DEFAULT_K,
    DEFAULT_MIN_LABELS,
    DEFAULT_SWEEP_GRID,
    RANKINGS,
    CreatorMetrics,
    LabeledClip,
    SweepRow,
    _binary,
    _blend_scores,
    _ranked_relevances,
    _relevance_for,
    _train_scorer,
    compute_creator_metrics,
    evaluate_creator,
    load_labeled_clips,
    pool_metrics,
    select_best_half_life,
    sweep_half_life,
)

__all__ = [
    "DEFAULT_K",
    "DEFAULT_MIN_LABELS",
    "DEFAULT_SWEEP_GRID",
    "RANKINGS",
    "CreatorMetrics",
    "LabeledClip",
    "SweepRow",
    "_binary",
    "_blend_scores",
    "_ranked_relevances",
    "_relevance_for",
    "_train_scorer",
    "compute_creator_metrics",
    "evaluate_creator",
    "load_labeled_clips",
    "pool_metrics",
    "select_best_half_life",
    "sweep_half_life",
]
