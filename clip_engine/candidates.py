"""
Clip candidate detection: peak detection + backward setup-finding.

Core principle: #2 "Clip the setup, not the aftermath" — on every signal peak,
scan backwards up to WINDOW_S seconds for the most recent content boundary
(silence end or energy spike start), and begin the clip there.
"""

import numpy as np
from scipy.signal import find_peaks

from clip_engine.window import RESOLUTION_S, build_signal_array

WINDOW_S = 75.0  # max lookback from peak to find setup
POST_PEAK_S = 20.0  # seconds to include after peak
MIN_CLIP_S = 30.0  # clips shorter than this are discarded


def _find_setup_start(timeline: dict, peak_s: float, window_s: float = WINDOW_S) -> float:
    """
    Scan backwards from peak_s up to window_s for the nearest content boundary.

    Priority:
      1. End of the most-recent silence (marks a natural setup start)
      2. Start of the nearest energy spike before the peak
      3. Fallback: peak_s - window_s (clamped to 0)
    """
    earliest = max(0.0, peak_s - window_s)

    silences = [
        e
        for e in timeline.get("events", [])
        if e.get("type") == "silence"
        and e.get("start_s", 0.0) >= earliest
        and e.get("end_s", e.get("start_s", 0.0)) <= peak_s
    ]
    if silences:
        most_recent = max(silences, key=lambda e: e.get("end_s", e.get("start_s", 0.0)))
        return float(most_recent.get("end_s", most_recent.get("start_s", earliest)))

    energy = [
        e
        for e in timeline.get("events", [])
        if e.get("type") == "energy_spike"
        and e.get("start_s", 0.0) >= earliest
        and e.get("start_s", 0.0) < peak_s
    ]
    if energy:
        return float(min(e["start_s"] for e in energy))

    return earliest


def extract_candidates(
    timeline: dict,
    max_candidates: int = 8,
    window_s: float = WINDOW_S,
) -> list[dict]:
    """
    Return up to max_candidates clip windows, each with:
        setup_start_s  — backward-found content boundary (principle #2)
        start_s        — hard fallback clip start (peak - window_s, ≥ 0)
        peak_s         — detected signal peak
        end_s          — peak + post-peak context

    Events are weighted by type; scipy.signal.find_peaks locates peaks;
    candidates are sorted by signal prominence (strongest first), then
    returned sorted chronologically.
    """
    times, signal = build_signal_array(timeline)
    if len(signal) == 0:
        return []

    resolution_s = float(times[1] - times[0]) if len(times) > 1 else RESOLUTION_S
    min_distance_samples = max(1, int(MIN_CLIP_S / resolution_s))

    peak_indices, properties = find_peaks(
        signal,
        distance=min_distance_samples,
        prominence=0.5,
    )

    if len(peak_indices) == 0:
        return []

    # Sort by prominence descending; take top max_candidates
    prominences = properties.get("prominences", np.ones(len(peak_indices)))
    order = np.argsort(prominences)[::-1]
    peak_indices = peak_indices[order][:max_candidates]

    duration_s = float(timeline.get("duration_s", times[-1]))
    candidates: list[dict] = []

    for idx in peak_indices:
        peak_s = float(times[idx])
        setup_start_s = _find_setup_start(timeline, peak_s, window_s)
        start_s = max(0.0, peak_s - window_s)
        # Extend end_s if needed so the clip is always at least MIN_CLIP_S long
        end_s = min(duration_s, max(peak_s + POST_PEAK_S, setup_start_s + MIN_CLIP_S))

        if end_s - setup_start_s < MIN_CLIP_S:
            continue

        candidates.append(
            {
                "setup_start_s": round(setup_start_s, 2),
                "start_s": round(start_s, 2),
                "peak_s": round(peak_s, 2),
                "end_s": round(end_s, 2),
            }
        )

    candidates.sort(key=lambda c: c["setup_start_s"])
    return candidates
