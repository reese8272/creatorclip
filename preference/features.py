"""
Feature vector per clip for the preference model.
"""

import math


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
    # Treat NaN/inf dna_match as "absent" (0.0): a non-finite value would otherwise
    # propagate through the model and poison the rerank sort order. (Issue 338)
    dna = dna_match if (dna_match is not None and math.isfinite(dna_match)) else 0.0
    return [
        signal_density,
        hook_energy,
        silence_ratio,
        dna,
        clip_duration_s,
        setup_length_s,
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
