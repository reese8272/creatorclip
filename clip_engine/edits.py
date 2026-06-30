"""
clip_engine/edits.py — Validate user-supplied cut segments from the
text-based editor (Issue 135).

Companion to ``clip_engine/filler.py`` (which generates cuts from filler
detection): this module accepts cut ranges the user picked manually from
the transcript pane and shapes them into a safe input for the same
``clip_engine.render.render_cleaned_clip_file`` pipeline that Issue 134
already ships.

Hard caps enforced (see ``docs/DECISIONS.md`` 2026-06-07):
  - ``kept_duration >= MIN_KEPT_DURATION_S`` (default 5.0 s). Clips trimmed
    below this floor fail every short-form upload validator we target.
  - ``percent_removed <= MAX_REMOVED_PCT`` (default 85 %). Above this the
    edit is almost certainly user error and would waste a render slot.

Other invariants:
  - Sub-frame keep segments (< 0.04 s = one frame at 25 fps) are
    floored away — ``ffmpeg`` ``atrim`` crashes on zero-duration trims.
  - End-exclusive bounds: the user's `end_s` is the *first* frame
    AFTER the cut, matching WhisperX timestamp semantics.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Minimum keep-segment duration (one frame at 25 fps). A keep segment below
# this would generate a zero/sub-frame ``trim=start=X:end=Y`` that crashes
# the ffmpeg filter graph at parse time.
MIN_KEEP_SEGMENT_S = 0.04

# Hard caps. See DECISIONS.md "Hard caps the spec doesn't mention" (D2).
MIN_KEPT_DURATION_S = 5.0
MAX_REMOVED_PCT = 85.0

# Soft warning threshold (UI band only, NOT a reject). Mirrors Issue 134's
# percent-removed warning so the two editors feel consistent.
WARNING_REMOVED_PCT = 40.0


class CutValidationError(ValueError):
    """Raised when a user-supplied cut list violates the safety invariants.

    The ``code`` attribute is one of:
      - ``empty``                 — no segments at all
      - ``invalid_segment``       — start >= end, or NaN, or negative
      - ``out_of_bounds``         — segment outside [0, clip_duration_s]
      - ``overlap``               — segments overlap each other
      - ``kept_too_short``        — would leave < MIN_KEPT_DURATION_S
      - ``removed_too_much``      — would remove > MAX_REMOVED_PCT
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ValidatedEdit:
    """Validated and merged result.

    ``cut_segments`` is the canonicalised (sorted, non-overlapping) cut list
    suitable for displaying back to the user. ``keep_ranges`` is the inverse
    — the input to ``render_cleaned_clip_file``. ``kept_duration_s`` and
    ``percent_removed`` drive the UI summary + soft warning band.
    """

    cut_segments: list[tuple[float, float]]
    keep_ranges: list[tuple[float, float]]
    kept_duration_s: float
    percent_removed: float


def validate_user_cuts(
    segments: list[tuple[float, float]] | list[dict],
    clip_duration_s: float,
    *,
    min_kept_duration_s: float = MIN_KEPT_DURATION_S,
    max_removed_pct: float = MAX_REMOVED_PCT,
) -> ValidatedEdit:
    """Validate a user-supplied cut list and emit ``keep_ranges`` for ffmpeg.

    Accepts either tuples ``(start_s, end_s)`` or dicts ``{start_s, end_s}``.
    Raises :class:`CutValidationError` on any safety-invariant violation.

    The bounds-check is permissive on the right edge by one ``MIN_KEEP_SEGMENT_S``
    so a user dragging to "the end" doesn't trip on transcript-vs-clip rounding.
    """
    if clip_duration_s <= 0:
        raise CutValidationError("invalid_segment", "clip duration must be positive")
    if not segments:
        raise CutValidationError("empty", "at least one cut segment is required")

    normalised: list[tuple[float, float]] = []
    for raw in segments:
        # Non-numeric / malformed input must surface as a typed CutValidationError
        # (→ 422 at the router), never a bare ValueError/TypeError/IndexError leaking
        # as a 500. ``float("abc")`` and ``raw[0]`` on a non-sequence both raise here.
        try:
            if isinstance(raw, dict):
                start = float(raw.get("start_s", raw.get("start", 0.0)))
                end = float(raw.get("end_s", raw.get("end", 0.0)))
            else:
                start = float(raw[0])
                end = float(raw[1])
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise CutValidationError(
                "invalid_segment", f"non-numeric segment bounds: {raw!r}"
            ) from exc
        if math.isnan(start) or math.isnan(end):
            raise CutValidationError("invalid_segment", "NaN in segment bounds")
        if end <= start:
            raise CutValidationError(
                "invalid_segment", f"segment end ({end}) must exceed start ({start})"
            )
        # Permissive right-edge bounds — allow up to one frame past clip duration.
        # (±inf is rejected here: +inf trips this bound, -inf trips start < 0.)
        if start < 0 or end > clip_duration_s + MIN_KEEP_SEGMENT_S:
            raise CutValidationError(
                "out_of_bounds",
                f"segment ({start}, {end}) outside [0, {clip_duration_s}]",
            )
        # Clamp end to the clip boundary so downstream math is exact. Log when the
        # clamp actually moves the edge so a silent right-edge shrink is visible.
        if end > clip_duration_s:
            logger.debug("clamping cut end %.3f to clip duration %.3f", end, clip_duration_s)
            end = clip_duration_s
        normalised.append((start, end))

    normalised.sort(key=lambda s: s[0])

    for prev, nxt in zip(normalised, normalised[1:], strict=False):
        if nxt[0] < prev[1]:
            raise CutValidationError(
                "overlap",
                f"segments ({prev[0]}, {prev[1]}) and ({nxt[0]}, {nxt[1]}) overlap",
            )

    total_removed = sum(end - start for start, end in normalised)
    kept_duration = clip_duration_s - total_removed
    percent_removed = 100.0 * total_removed / clip_duration_s

    if percent_removed > max_removed_pct:
        raise CutValidationError(
            "removed_too_much",
            f"cut would remove {percent_removed:.1f}% of clip (cap {max_removed_pct:.0f}%)",
        )
    if kept_duration < min_kept_duration_s:
        raise CutValidationError(
            "kept_too_short",
            f"cut would leave {kept_duration:.2f}s (minimum {min_kept_duration_s:.1f}s)",
        )

    keep_ranges = _invert_cuts(normalised, clip_duration_s)
    return ValidatedEdit(
        cut_segments=normalised,
        keep_ranges=keep_ranges,
        kept_duration_s=kept_duration,
        percent_removed=percent_removed,
    )


def _invert_cuts(
    cuts: list[tuple[float, float]], clip_duration_s: float
) -> list[tuple[float, float]]:
    """Invert non-overlapping sorted cuts to keep-ranges.

    Drops any keep range shorter than ``MIN_KEEP_SEGMENT_S`` — these would
    crash ffmpeg's ``trim`` filter. Adjacent cuts collapse to a single skip
    without emitting a zero-width keep.
    """
    keep: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in cuts:
        if start - cursor >= MIN_KEEP_SEGMENT_S:
            keep.append((cursor, start))
        cursor = max(cursor, end)
    if clip_duration_s - cursor >= MIN_KEEP_SEGMENT_S:
        keep.append((cursor, clip_duration_s))
    return keep
