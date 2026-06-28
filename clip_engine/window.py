"""
Convert a signal timeline dict into a 1D numpy array for peak detection.
"""

import numpy as np

RESOLUTION_S = 0.5  # seconds per sample

# Weights per event type for the composite signal
_WEIGHTS = {
    "retention_spike": 3.0,
    "laughter": 2.0,
    "energy_spike": 1.5,
    "silence": -0.5,
}


def build_signal_array(
    timeline: dict,
    resolution_s: float = RESOLUTION_S,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Build a 1D composite signal array from timeline events.

    Returns (times, signal) where times[i] = i * resolution_s.
    Returns empty arrays for zero-duration timelines.
    """
    duration_s = timeline.get("duration_s", 0.0)
    if duration_s <= 0:
        return np.array([]), np.array([])

    n = int(duration_s / resolution_s) + 1
    times = np.arange(n, dtype=float) * resolution_s
    signal = np.zeros(n)

    for event in timeline.get("events", []):
        weight = _WEIGHTS.get(event.get("type", ""), 0.0)
        if weight == 0.0:
            continue

        start_s = float(event.get("start_s", 0.0))
        end_s = float(event.get("end_s", start_s + resolution_s))
        value_scale = float(event.get("value", 1.0))

        i0 = max(0, int(start_s / resolution_s))
        i1 = min(n, int(end_s / resolution_s) + 1)
        # Defense-in-depth (Issue 327): events are sanitized at the signal-build
        # boundary, but guard here too so an inverted window (i1 <= i0) can never
        # silently write a reversed/empty slice. max(0.0, value_scale) keeps a
        # stray negative magnitude from inverting a positive event's contribution.
        if i1 <= i0:
            continue
        signal[i0:i1] += weight * max(0.0, value_scale)

    return times, signal
