"""
Clip candidate detection: peak detection + backward setup-finding.

Core principle: #2 "Clip the setup, not the aftermath" — on every signal peak,
scan backwards up to WINDOW_S seconds for the most recent content boundary
(silence end or energy spike start), and begin the clip there.

Principle #12 "Clean Context Boundary" is enforced by snapping both cut points
to the nearest sentence boundary (terminal punctuation or silence gap) so clips
never start or end mid-sentence.
"""

import logging

import numpy as np
from scipy.signal import find_peaks

from clip_engine.window import build_signal_array

logger = logging.getLogger(__name__)

WINDOW_S = 75.0  # max lookback from peak to find setup
POST_PEAK_S = 20.0  # seconds to include after peak
MIN_CLIP_S = 30.0  # clips shorter than this are discarded
_NMS_IOU_THRESHOLD = 0.5  # IoU above which a lower-prominence candidate is suppressed
_PEAK_PROMINENCE = 0.5  # find_peaks prominence floor shared by detection + skip-reason paths
# Positive-evidence floor (Issue 458): prominence alone is RELATIVE — a flat 0
# stretch between two -0.5 silences carries prominence 0.5 with no positive
# signal at all, fabricating clips from nothing. scipy ANDs the absolute
# `height` condition with prominence, so a peak must also carry real positive
# mass. 0.25 sits far below the weakest genuine eval-fixture peak (1.35,
# interrupted_setup) and above the all-zero floor.
_PEAK_MIN_HEIGHT = 0.25

# Named reasons a video produces no clips — each maps to a CLIPPING_PRINCIPLES.md principle.
# These are the ONLY valid skip-reason strings; callers and tests should compare against these.
SKIP_REASON_NO_SIGNAL = "no_signal_above_threshold"
SKIP_REASON_NO_POSITIVE_SIGNAL = "no_positive_signal"
SKIP_REASON_NO_RETENTION_DATA = "insufficient_retention_data"
SKIP_REASON_SOURCE_UNAVAILABLE = "source_unavailable"
SKIP_REASON_ALL_SUPPRESSED = "all_candidates_suppressed_by_nms"

# Human-readable labels surfaced to the creator (no virality language).
# Each cites the named principle from CLIPPING_PRINCIPLES.md that defines the expectation.
_SKIP_REASON_LABELS: dict[str, str] = {
    SKIP_REASON_NO_SIGNAL: (
        "No engagement signal reached the detection threshold "
        "(Principle #6: Retention curve is ground truth — "
        "this video lacks the data density needed to locate a setup)."
    ),
    SKIP_REASON_NO_POSITIVE_SIGNAL: (
        "No positive engagement signal was detected — the timeline contains only "
        "silence or flat audio, so there is no moment to anchor a clip to "
        "(Principle #6: Retention curve is ground truth — "
        "a setup needs positive evidence, not the absence of sound)."
    ),
    SKIP_REASON_NO_RETENTION_DATA: (
        "Insufficient retention data to identify a setup window "
        "(Principle #6: Retention curve is ground truth — "
        "analytics data is not yet available for this video)."
    ),
    SKIP_REASON_SOURCE_UNAVAILABLE: (
        "Source file not available — upload the original file to generate clips "
        "(Principle #2: Clip the setup, not the aftermath — "
        "we need the source media to locate and extract the setup)."
    ),
    SKIP_REASON_ALL_SUPPRESSED: (
        "All candidate windows overlapped and were deduplicated "
        "(Principle #9: One idea per Short — "
        "the video's signal clusters around a single moment already covered by the top clip)."
    ),
}

_TERMINAL_PUNCT = frozenset({".", "?", "!", "...", "…", '."', '?"', '!"'})


def _is_sentence_end(word_text: str) -> bool:
    """True when the word token ends with terminal punctuation."""
    text = word_text.strip()
    return any(text.endswith(p) for p in _TERMINAL_PUNCT)


def snap_to_sentence_boundary(
    timestamp_s: float,
    words: list[dict],
    direction: str,
    min_pause_ms: int = 400,
    max_snap_s: float = 3.0,
    timeline_events: list[dict] | None = None,
) -> float:
    """Snap timestamp_s to the nearest sentence boundary (principle #12).

    direction="backward": used for setup_start_s — walks backward to find the
    end of the previous sentence so the clip starts at the opening of a new
    sentence, never mid-sentence.

    direction="forward": used for end_s — walks forward to find the next
    terminal-punctuation word so the clip closes at a sentence end.

    Priority within max_snap_s:
      1. Terminal-punctuation word token
      2. Silence-gap boundary from timeline_events (>= min_pause_ms long)
      3. Original timestamp_s (hard cap — never snap farther than max_snap_s)
    """
    min_pause_s = min_pause_ms / 1000.0

    if direction == "backward":
        # Latest terminal-punct word whose END falls in [timestamp_s - max_snap_s, timestamp_s]
        punct_words = [
            w
            for w in words
            if _is_sentence_end(w.get("word", ""))
            and timestamp_s - max_snap_s <= w.get("end", 0.0) <= timestamp_s
        ]
        if punct_words:
            return float(max(punct_words, key=lambda w: w["end"])["end"])

        if timeline_events:
            silence_ends = [
                e.get("end_s", e.get("start_s", 0.0))
                for e in timeline_events
                if e.get("type") == "silence"
                and (e.get("end_s", e.get("start_s", 0.0)) - e.get("start_s", 0.0) >= min_pause_s)
                and timestamp_s - max_snap_s <= e.get("end_s", e.get("start_s", 0.0)) <= timestamp_s
            ]
            if silence_ends:
                return float(max(silence_ends))

    else:  # forward
        # First terminal-punct word whose END falls in [timestamp_s, timestamp_s + max_snap_s]
        punct_words = [
            w
            for w in words
            if _is_sentence_end(w.get("word", ""))
            and timestamp_s <= w.get("end", 0.0) <= timestamp_s + max_snap_s
        ]
        if punct_words:
            return float(min(punct_words, key=lambda w: w["end"])["end"])

        if timeline_events:
            silence_starts = [
                e.get("start_s", 0.0)
                for e in timeline_events
                if e.get("type") == "silence"
                and (e.get("end_s", e.get("start_s", 0.0)) - e.get("start_s", 0.0) >= min_pause_s)
                and timestamp_s <= e.get("start_s", 0.0) <= timestamp_s + max_snap_s
            ]
            if silence_starts:
                return float(min(silence_starts))

    return timestamp_s


def _find_setup_start(timeline: dict, peak_s: float, window_s: float = WINDOW_S) -> float:
    """
    Scan backwards from peak_s up to window_s for the nearest content boundary.

    Priority:
      1. End of the most-recent silence (marks a natural setup start)
      2. Start of the most-recent (nearest) energy spike before the peak
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
        # Issue 460: MOST RECENT spike start, mirroring the silence rule above —
        # min() took the earliest spike in the lookback, dragging setups to the
        # window edge on silence-free segments (contradicting the docstring).
        return float(max(e["start_s"] for e in energy))

    return earliest


def _detect_peaks(timeline: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """Shared peak-detection pipeline for extract_candidates and derive_skip_reason.

    Builds the composite signal array, derives the MIN_CLIP_S-based minimum
    peak distance, and runs scipy.signal.find_peaks with the module's
    prominence policy. Returns (times, signal, peak_indices, properties);
    empty arrays and {} when the timeline yields no signal.
    """
    times, signal = build_signal_array(timeline)
    if len(signal) <= 1:
        return times, signal, np.array([], dtype=int), {}

    resolution_s = float(times[1] - times[0])
    min_distance_samples = max(1, int(MIN_CLIP_S / resolution_s))
    # Issue 459: find_peaks can never report the first or last array sample as
    # a peak, so a retention spike in the video's first/last 0.5s bucket was
    # invisible. Pad one sample per side with the signal floor — never -inf,
    # which corrupts prominence ordering — then shift indices back.
    pad_value = min(0.0, float(signal.min()))
    padded = np.concatenate(([pad_value], signal, [pad_value]))
    peak_indices, properties = find_peaks(
        padded,
        distance=min_distance_samples,
        prominence=_PEAK_PROMINENCE,
        height=_PEAK_MIN_HEIGHT,
    )
    peak_indices = peak_indices - 1
    # Degenerate clamp: an endpoint peak is nudged one sample inward
    # (idx 0 -> times[1]; idx n-1 -> times[n-2]) so setup(0.0) < peak_s and
    # peak_s < end_s both hold downstream. The distance floor (>= 60 samples)
    # guarantees the nudge cannot collide with a neighbouring peak.
    peak_indices = np.clip(peak_indices, 1, len(signal) - 2)
    return times, signal, peak_indices, properties


def derive_skip_reason(
    timeline: dict,
    source_available: bool = True,
) -> str | None:
    """Return the dominant reason a video produced zero clip candidates, or None when clips exist.

    Priority order (first match wins):
      1. source_unavailable — caller tells us no stored media exists
      2. no_signal_above_threshold — timeline empty / zero duration (no signal array)
      3. no_positive_signal — the composite carries no positive mass at all
         (silence-only or all-zero timelines; Issue 458)
      4. insufficient_retention_data — timeline has no retention_spike events at all
      5. all_candidates_suppressed_by_nms — peaks found but all suppressed by IoU overlap

    Used by routers/clips.py to populate ClipListOut.skip_reason so the creator
    gets an honest, principle-grounded explanation instead of a silent empty list.
    Each reason string maps to a label in _SKIP_REASON_LABELS.
    """
    if not source_available:
        return SKIP_REASON_SOURCE_UNAVAILABLE

    _, signal, peak_indices, _ = _detect_peaks(timeline)
    if len(signal) == 0:
        return SKIP_REASON_NO_SIGNAL

    # Issue 458: checked BEFORE the retention branch — a timeline whose composite
    # never rises above zero has no positive evidence anywhere; "insufficient
    # retention data" would misdiagnose it as a data-availability problem.
    if float(signal.max()) <= 0.0:
        return SKIP_REASON_NO_POSITIVE_SIGNAL

    if len(peak_indices) == 0:
        # No peaks — check whether there are any retention events at all to help
        # distinguish a completely flat video from one with only audio signals.
        has_retention = any(e.get("type") == "retention_spike" for e in timeline.get("events", []))
        if not has_retention:
            return SKIP_REASON_NO_RETENTION_DATA
        return SKIP_REASON_NO_SIGNAL

    # Peaks were detected but the caller already knows zero clips were persisted;
    # the most likely explanation is that all windows were deduplicated by NMS.
    return SKIP_REASON_ALL_SUPPRESSED


def skip_reason_label(reason: str) -> str:
    """Return the human-readable label for a skip reason code.

    Falls back gracefully to the raw code string if an unrecognised reason is
    passed — so callers never silently surface an empty string.
    """
    return _SKIP_REASON_LABELS.get(reason, reason)


def extract_candidates(
    timeline: dict,
    max_candidates: int = 8,
    window_s: float = WINDOW_S,
    words: list[dict] | None = None,
    min_pause_ms: int = 400,
    max_snap_s: float = 3.0,
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
    times, signal, peak_indices, properties = _detect_peaks(timeline)
    if len(signal) == 0 or len(peak_indices) == 0:
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

    for idx, prominence in zip(peak_indices_ordered, prominences_ordered, strict=True):
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
    candidates = [{k: v for k, v in c.items() if k != "_prominence"} for c in kept]
    candidates.sort(key=lambda c: c["setup_start_s"])

    # Breadcrumb for "why did I get N clips?" debugging (Issue 328): peaks found →
    # survived the MIN_CLIP_S length filter → survived NMS dedup → final count.
    logger.debug(
        "extract_candidates: peaks=%d pre_nms=%d after_nms=%d final=%d (duration_s=%.1f)",
        len(peak_indices),
        len(pre_nms),
        len(kept),
        len(candidates),
        duration_s,
    )

    # Principle #12 — Clean Context Boundary: snap both cut points to the nearest
    # sentence boundary so clips never start or end mid-sentence. Only runs when
    # word-level transcript is provided; falls back gracefully when absent.
    if words:
        events = timeline.get("events")
        snapped: list[dict] = []
        for c in candidates:
            c["setup_start_s"] = round(
                snap_to_sentence_boundary(
                    c["setup_start_s"], words, "backward", min_pause_ms, max_snap_s, events
                ),
                2,
            )
            c["end_s"] = round(
                snap_to_sentence_boundary(
                    c["end_s"], words, "forward", min_pause_ms, max_snap_s, events
                ),
                2,
            )
            # Maintain invariants after snapping: setup < peak, clip >= MIN_CLIP_S
            if c["end_s"] - c["setup_start_s"] < MIN_CLIP_S:
                c["end_s"] = round(c["setup_start_s"] + MIN_CLIP_S, 2)
            # Forward snapping and the MIN_CLIP_S re-extension can both push end_s
            # past the container duration (transcript word `end` values can exceed
            # the ffprobe duration by encoder/transcriber rounding), and render.py
            # rejects any end_s > source duration. Clamp back into the renderable
            # range; if that breaks the MIN_CLIP_S invariant, drop the candidate —
            # the same handling as the pre-NMS too-short filter above.
            c["end_s"] = round(min(c["end_s"], duration_s), 2)
            c["setup_start_s"] = min(c["setup_start_s"], c["peak_s"] - 0.1)
            if c["end_s"] - c["setup_start_s"] < MIN_CLIP_S:
                continue
            snapped.append(c)
        candidates = snapped

    return candidates
