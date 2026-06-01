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
_NMS_IOU_THRESHOLD = 0.5  # IoU above which a lower-prominence candidate is suppressed


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
    # Retain prominence alongside the peak index for NMS ordering (score-order iteration).
    peak_indices_ordered = peak_indices[order][:max_candidates]
    prominences_ordered = prominences[order][:max_candidates]

    duration_s = float(timeline.get("duration_s", times[-1]))
    # Pre-NMS: build candidates in prominence order so we always keep the stronger peak
    # when two windows overlap.
    pre_nms: list[dict] = []

    for idx, prominence in zip(peak_indices_ordered, prominences_ordered):
        peak_s = float(times[idx])
        setup_start_s = _find_setup_start(timeline, peak_s, window_s)
        start_s = max(0.0, peak_s - window_s)
        # Extend end_s if needed so the clip is always at least MIN_CLIP_S long
        end_s = min(duration_s, max(peak_s + POST_PEAK_S, setup_start_s + MIN_CLIP_S))

        if end_s - setup_start_s < MIN_CLIP_S:
            continue

        pre_nms.append(
            {
                "setup_start_s": round(setup_start_s, 2),
                "start_s": round(start_s, 2),
                "peak_s": round(peak_s, 2),
                "end_s": round(end_s, 2),
                "_prominence": float(prominence),
            }
        )

    # Greedy NMS: iterate in prominence order and suppress any candidate whose [setup,end]
    # window overlaps an already-kept candidate by IoU > 0.5. This prevents two peaks 35s
    # apart from anchoring to the same silence boundary and yielding nearly-identical clips.
    # Threshold 0.5 is canonical for video-summarisation (SumMe / TVSum) and matches
    # standard object-detection NMS. (Issue 103 #6)
    kept: list[dict] = []
    for cand in pre_nms:
        a_start = cand["setup_start_s"]
        a_end = cand["end_s"]
        a_len = a_end - a_start
        overlaps = False
        for kept_c in kept:
            b_start = kept_c["setup_start_s"]
            b_end = kept_c["end_s"]
            inter = max(0.0, min(a_end, b_end) - max(a_start, b_start))
            union = a_len + (b_end - b_start) - inter
            iou = inter / union if union > 0.0 else 0.0
            if iou > _NMS_IOU_THRESHOLD:
                overlaps = True
                break
        if not overlaps:
            kept.append(cand)

    # Strip internal NMS metadata and sort chronologically for the caller.
    candidates = [
        {k: v for k, v in c.items() if k != "_prominence"}
        for c in kept
    ]
    candidates.sort(key=lambda c: c["setup_start_s"])
    return candidates
