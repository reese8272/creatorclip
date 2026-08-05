"""Hybrid candidate merge — LLM moments ∪ signal peaks (Issue 416).

Pure layer between ``extract_candidates`` (which stays byte-untouched — the
eval harness calls it directly and stays green by construction) and scoring.
``llm_moments_to_candidates`` converts the validated ``VideoContext`` moments
into candidates.py-shaped dicts (same geometry invariants, sentence-snapped,
signal-argmax peak); ``merge_candidates`` unions them with the signal
candidates under SIGNAL-PRIORITY NMS: every signal candidate is admitted
first, then LLM candidates by descending confidence, suppressing any whose
[setup, end] window overlaps an admitted candidate at IoU > 0.5.

Pre-scoring, priority protects the harness-verified geometry; displacement of
weak signal candidates happens POST-scoring on merit via the ranking trim to
``CLIPS_PER_VIDEO_DEFAULT`` (ranking.py). Industry check: hybrid
signal+semantic candidate generation with NMS union is the standard
highlight-detection composition (SumMe/TVSum lineage — see candidates.py's NMS
note); locked in DECISIONS 2026-08-04, L26 decision 3.
"""

import logging

from clip_engine.candidates import MIN_CLIP_S, snap_to_sentence_boundary
from clip_engine.window import RESOLUTION_S, build_signal_array

logger = logging.getLogger(__name__)

_NMS_IOU_THRESHOLD = 0.5  # same threshold as candidates.py's signal-only NMS


def _window_iou(a: dict, b: dict) -> float:
    """IoU of two candidates' [setup_start_s, end_s] windows."""
    a_start, a_end = a["setup_start_s"], a["end_s"]
    b_start, b_end = b["setup_start_s"], b["end_s"]
    inter = max(0.0, min(a_end, b_end) - max(a_start, b_start))
    union = (a_end - a_start) + (b_end - b_start) - inter
    return inter / union if union > 0.0 else 0.0


def llm_moments_to_candidates(
    moments: list[dict],
    timeline: dict,
    words: list[dict] | None = None,
) -> list[dict]:
    """Convert validated VideoContext moments into candidates.py-shaped dicts.

    Applies the same geometry discipline as ``extract_candidates``:
      - cut points sentence-snapped via ``snap_to_sentence_boundary`` (#12
        Clean Context Boundary) when word timings are available,
      - bounds clamped to the timeline duration,
      - ``MIN_CLIP_S`` enforced (re-extend, then drop when impossible),
      - ``setup_start_s < peak_s`` invariant held.

    ``peak_s`` is the argmax of the composite signal inside the window — so a
    moment that DOES have an energy peak anchors to it — falling back to the
    window midpoint on a flat/empty signal (the flat-energy story case, which
    is exactly what this path exists to admit).

    Each candidate is tagged ``origin: "llm"`` and carries ``llm_confidence``,
    ``llm_reason``, and ``principle_hint`` for the scorer's cold-start rule and
    the persisted provenance. Moments arrive already validated (bounds,
    principle registry, ≤ LLM_CANDIDATES_MAX) by
    ``analysis.video_context.validate_context``.
    """
    if not moments:
        return []

    times, signal = build_signal_array(timeline)
    duration_s = float(timeline.get("duration_s", times[-1] if len(times) else 0.0))
    events = timeline.get("events")

    out: list[dict] = []
    for m in moments:
        start = float(m["start_s"])
        end = float(m["end_s"])

        if words:
            start = snap_to_sentence_boundary(
                start, words, "backward", timeline_events=events
            )
            end = snap_to_sentence_boundary(end, words, "forward", timeline_events=events)

        start = max(0.0, start)
        if duration_s > 0:
            end = min(end, duration_s)

        # MIN_CLIP_S invariant: re-extend forward like candidates.py, clamp back
        # to the container, drop when the invariant cannot be met.
        if end - start < MIN_CLIP_S:
            end = start + MIN_CLIP_S
            if duration_s > 0:
                end = min(end, duration_s)
        if end - start < MIN_CLIP_S:
            logger.debug(
                "llm moment dropped: window [%.1f, %.1f] cannot satisfy MIN_CLIP_S", start, end
            )
            continue

        # peak_s = signal argmax inside the window; midpoint when flat/empty.
        midpoint = (start + end) / 2.0
        peak = midpoint
        i0 = max(0, int(start / RESOLUTION_S))
        i1 = min(len(signal), int(end / RESOLUTION_S) + 1)
        if i1 > i0:
            window = signal[i0:i1]
            if float(window.max()) > float(window.min()):  # not flat
                peak = float(times[i0 + int(window.argmax())])
        # setup < peak invariant (candidates.py holds setup <= peak - 0.1). The
        # moment's start IS the story's setup, so clamp the peak instead of
        # moving the start.
        peak = min(max(peak, start + 0.1), end)

        out.append(
            {
                "setup_start_s": round(start, 2),
                "start_s": round(start, 2),
                "peak_s": round(peak, 2),
                "end_s": round(end, 2),
                "origin": "llm",
                "llm_confidence": float(m.get("confidence", 0.0)),
                "llm_reason": str(m.get("reason", "")),
                "principle_hint": str(m.get("principle", "")),
            }
        )

    return out


def merge_candidates(
    signal_candidates: list[dict],
    llm_candidates: list[dict],
) -> list[dict]:
    """Union signal + LLM candidates under signal-priority NMS.

    All signal candidates are admitted first (their geometry is what the eval
    harness verifies); LLM candidates are then admitted in descending
    ``llm_confidence`` order, suppressing any whose window overlaps an
    already-admitted candidate at IoU > 0.5. The result is sorted
    chronologically — the same contract ``extract_candidates`` returns.
    """
    kept: list[dict] = list(signal_candidates)
    suppressed = 0
    for cand in sorted(
        llm_candidates, key=lambda c: c.get("llm_confidence", 0.0), reverse=True
    ):
        if any(_window_iou(cand, k) > _NMS_IOU_THRESHOLD for k in kept):
            suppressed += 1
            continue
        kept.append(cand)

    kept.sort(key=lambda c: c["setup_start_s"])
    logger.debug(
        "merge_candidates: signal=%d llm=%d suppressed=%d merged=%d",
        len(signal_candidates),
        len(llm_candidates),
        suppressed,
        len(kept),
    )
    return kept
