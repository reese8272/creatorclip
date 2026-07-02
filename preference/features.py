"""
Feature vector per clip for the preference model.
"""

import math


def _finite(value: float | None, default: float = 0.0) -> float:
    """Coerce None/NaN/inf to ``default``.

    A single non-finite feature fails the sklearn/LightGBM retrain
    (``check_array`` rejects non-finite input) or silently poisons the
    rerank sort at predict time, so every float feature is clamped here —
    not just ``dna_match`` (Issue 338 → generalized in Issue 352).
    """
    return float(value) if (value is not None and math.isfinite(value)) else default


def clip_features(
    signal_density: float = 0.0,
    hook_energy: float = 0.0,
    silence_ratio: float = 0.0,
    dna_match: float | None = None,
    clip_duration_s: float = 0.0,
    setup_length_s: float = 0.0,
    has_retention_spike: bool = False,
    has_laughter: bool = False,
) -> list[float]:
    """
    Return a fixed-length feature vector for one clip.
    Feature order must stay stable between training runs.
    """
    return [
        _finite(signal_density),
        _finite(hook_energy),
        _finite(silence_ratio),
        _finite(dna_match),
        _finite(clip_duration_s),
        _finite(setup_length_s),
        1.0 if has_retention_spike else 0.0,
        1.0 if has_laughter else 0.0,
    ]


FEATURE_NAMES = [
    "signal_density",
    "hook_energy",
    "silence_ratio",
    "dna_match",
    "clip_duration_s",
    "setup_length_s",
    "has_retention_spike",
    "has_laughter",
]
