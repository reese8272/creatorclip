"""
clip_engine/edits.py — Validate user-supplied cut segments from the
text-based editor (Issue 135).

Companion to ``clip_engine/filler.py`` (which generates cuts from filler
detection): this module accepts cut ranges the user picked manually from
the transcript pane and shapes them into a safe input for the same
``clip_engine.render.render_cleaned_clip_file`` pipeline that Issue 134
already ships.

Two validators live here, and the split is deliberate (Issue 391):

  - :func:`validate_user_cuts` is the RENDER boundary. It enforces the hard
    caps below, because those describe an edit worth spending a render slot on.
  - :func:`validate_document_structure` is the SAVE boundary. It enforces only
    that the document is well-formed. A creator half-way through an edit will
    routinely have removed 90% of the clip; refusing to SAVE that would destroy
    the work in progress, which is the exact failure Issue 391 exists to remove.
    Saving is always allowed; exporting is gated.

Hard caps enforced at the render boundary (see ``docs/DECISIONS.md`` 2026-06-07):
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

# The edit-document schema version this build understands (Issue 391). A reader
# REJECTS a document with a higher version rather than half-parsing one written
# by a newer tab — silently dropping keys it does not recognise would hand the
# creator back a document with their newer work deleted.
SUPPORTED_DOC_VERSION = 1

# Upper bound on cuts in one document. Not a UX limit — a clip is at most a
# couple of minutes and MIN_CUT_S is 0.1s client-side, so 200 is far past any
# real edit. It exists so a malformed or hostile body cannot make the server
# validate an unbounded list, and so the JSONB payload stays small enough that
# the "read and write the document whole" design holds.
MAX_CUTS = 200


class CutValidationError(ValueError):
    """Raised when a user-supplied cut list violates the safety invariants.

    The ``code`` attribute is one of:
      - ``empty``                 — no segments at all
      - ``invalid_segment``       — start >= end, or NaN, or negative
      - ``out_of_bounds``         — segment outside [0, clip_duration_s]
      - ``overlap``               — segments overlap each other
      - ``kept_too_short``        — would leave < MIN_KEPT_DURATION_S
      - ``removed_too_much``      — would remove > MAX_REMOVED_PCT
      - ``unsupported_version``   — doc["version"] > SUPPORTED_DOC_VERSION
      - ``too_many_cuts``         — more than MAX_CUTS entries
      - ``duplicate_cut_id``      — two cuts share an id

    The last three are structural (Issue 391) and are raised only by
    :func:`validate_document_structure`.
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


def validate_document_structure(doc: object, clip_duration_s: float) -> dict:
    """Validate an edit document for SAVING, and return its canonical form.

    Structural only — see the module docstring. This deliberately does NOT
    apply ``MIN_KEPT_DURATION_S`` or ``MAX_REMOVED_PCT``: a work-in-progress
    edit is routinely past both, and refusing the autosave would destroy the
    creator's work, which is the defect Issue 391 exists to remove. Those caps
    are applied by :func:`validate_user_cuts` at the render boundary.

    An EMPTY cut list is valid here, unlike at render: clearing every cut is a
    legitimate edit the creator must be able to persist (it is also what
    ``/clean/confirm`` writes once the edit is baked into the render).

    Raises :class:`CutValidationError`; returns the canonical document with
    cuts sorted by ``start_s``.
    """
    if clip_duration_s <= 0:
        raise CutValidationError("invalid_segment", "clip duration must be positive")
    if not isinstance(doc, dict):
        raise CutValidationError("invalid_segment", "document must be an object")

    version = doc.get("version", SUPPORTED_DOC_VERSION)
    if not isinstance(version, int) or isinstance(version, bool):
        raise CutValidationError("invalid_segment", "document version must be an integer")
    if version > SUPPORTED_DOC_VERSION:
        raise CutValidationError(
            "unsupported_version",
            f"document version {version} is newer than this server supports "
            f"({SUPPORTED_DOC_VERSION}) — reload the page",
        )

    raw_cuts = doc.get("cuts", [])
    if not isinstance(raw_cuts, list):
        raise CutValidationError("invalid_segment", "document cuts must be a list")
    if len(raw_cuts) > MAX_CUTS:
        raise CutValidationError(
            "too_many_cuts", f"document has {len(raw_cuts)} cuts (cap {MAX_CUTS})"
        )

    cuts: list[dict] = []
    seen_ids: set[str] = set()
    for raw in raw_cuts:
        if not isinstance(raw, dict):
            raise CutValidationError("invalid_segment", f"cut must be an object: {raw!r}")
        cut_id = raw.get("id")
        if not isinstance(cut_id, str) or not cut_id:
            raise CutValidationError("invalid_segment", f"cut is missing a string id: {raw!r}")
        # Ids address cuts in the client's command stack; two cuts sharing one
        # would make undo target an arbitrary member of the pair.
        if cut_id in seen_ids:
            raise CutValidationError("duplicate_cut_id", f"duplicate cut id: {cut_id!r}")
        seen_ids.add(cut_id)

        try:
            start = float(raw["start_s"])
            end = float(raw["end_s"])
        except (ValueError, TypeError, KeyError) as exc:
            raise CutValidationError("invalid_segment", f"non-numeric cut bounds: {raw!r}") from exc
        # isfinite rejects NaN and ±inf in one check; the render validator has
        # to spell this out because it also accepts tuples.
        if not math.isfinite(start) or not math.isfinite(end):
            raise CutValidationError("invalid_segment", "non-finite cut bounds")
        if end <= start:
            raise CutValidationError(
                "invalid_segment", f"cut end ({end}) must exceed start ({start})"
            )
        if start < 0 or end > clip_duration_s + MIN_KEEP_SEGMENT_S:
            raise CutValidationError(
                "out_of_bounds", f"cut ({start}, {end}) outside [0, {clip_duration_s}]"
            )
        cuts.append({"id": cut_id, "start_s": start, "end_s": min(end, clip_duration_s)})

    cuts.sort(key=lambda c: c["start_s"])
    for prev, nxt in zip(cuts, cuts[1:], strict=False):
        if nxt["start_s"] < prev["end_s"]:
            raise CutValidationError(
                "overlap",
                f"cuts ({prev['start_s']}, {prev['end_s']}) and "
                f"({nxt['start_s']}, {nxt['end_s']}) overlap",
            )

    last_applied_at = doc.get("last_applied_at")
    if last_applied_at is not None and not isinstance(last_applied_at, str):
        raise CutValidationError("invalid_segment", "last_applied_at must be a string or null")

    return {"version": SUPPORTED_DOC_VERSION, "cuts": cuts, "last_applied_at": last_applied_at}


def empty_document() -> dict:
    """The canonical empty edit document, used for a clip that has none yet."""
    return {"version": SUPPORTED_DOC_VERSION, "cuts": [], "last_applied_at": None}


# ── Effective render geometry (Issue 470) ───────────────────────────────────
#
# When a trim/clean is confirmed, the delivered video is a concatenation of
# keep segments of the previous delivered video — but the clip row's
# start_s/end_s/setup_start_s keep describing the SOURCE window. The helpers
# below are the single shared vocabulary for the durable record of what was
# actually delivered (`clips.pending_geometry_jsonb` /
# `clips.effective_geometry_jsonb` — see models.py):
#
#   {"version": 1, "keep_segments_s": [[a, b], ...], "duration_s": <sum>}
#
# In `effective_geometry_jsonb` the segments are ORIGINAL clip-relative seconds
# (origin = setup_start_s ?? start_s) so transcript words map straight through;
# in `pending_geometry_jsonb` they are relative to the timeline of the render
# the cuts were applied to (composed into original time at confirm).

GEOMETRY_DOC_VERSION = 1


def geometry_doc(keep_ranges: list[tuple[float, float]]) -> dict:
    """Build the persisted geometry document from ffmpeg keep ranges."""
    segments = [[round(float(s), 4), round(float(e), 4)] for s, e in keep_ranges]
    return {
        "version": GEOMETRY_DOC_VERSION,
        "keep_segments_s": segments,
        "duration_s": round(sum(e - s for s, e in segments), 4),
    }


def parse_geometry(doc: object) -> list[tuple[float, float]] | None:
    """Tolerantly parse a stored geometry document into keep segments.

    Returns ``None`` for anything that is not a well-formed, non-empty,
    ascending, disjoint segment list at a supported version — readers then
    fall back to the source-window numbers (the pre-Issue-470 behavior)
    instead of computing against garbage.
    """
    if not isinstance(doc, dict):
        return None
    if doc.get("version") != GEOMETRY_DOC_VERSION:
        return None
    raw = doc.get("keep_segments_s")
    if not isinstance(raw, list) or not raw:
        return None
    segments: list[tuple[float, float]] = []
    cursor = -math.inf
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return None
        try:
            start, end = float(item[0]), float(item[1])
        except (TypeError, ValueError):
            return None
        if not (math.isfinite(start) and math.isfinite(end)):
            return None
        if start < cursor or end <= start:
            return None
        segments.append((start, end))
        cursor = end
    return segments


def geometry_duration_s(doc: object) -> float | None:
    """Delivered duration recorded by a geometry document, or ``None``."""
    segments = parse_geometry(doc)
    if segments is None:
        return None
    return sum(e - s for s, e in segments)


def clip_origin_s(setup_start_s: float | None, start_s: float) -> float:
    """The clip's render origin in VIDEO-ABSOLUTE seconds: ``setup_start_s ?? start_s``.

    The engine starts the clip at the setup, not the peak's aftermath, so when
    ``setup_start_s`` is set THAT — not ``start_s`` — is second 0 of the
    delivered video and the zero point of every clip-relative measurement
    (durations, setup leads, trim windows, transcript offsets). The ONE origin
    rule (Issue 475) — shared by ``playable_duration_s``, the Proof-of-Lift
    contrast and the originality fingerprint, so no surface can mis-measure
    clips whose setup point differs from ``start_s``.
    """
    return setup_start_s if setup_start_s is not None else start_s


def playable_duration_s(
    *,
    setup_start_s: float | None,
    start_s: float,
    end_s: float,
    effective_geometry: object,
) -> float:
    """Duration of the clip's DELIVERED video, in clip-relative seconds.

    When a confirmed trim/clean recorded effective geometry, that is the
    authority; otherwise the delivered window runs from the shared render
    origin (``clip_origin_s``) to ``end_s``.
    """
    effective = geometry_duration_s(effective_geometry)
    if effective is not None:
        return effective
    return end_s - clip_origin_s(setup_start_s, start_s)


def compose_keep_segments(
    prior: list[tuple[float, float]] | None,
    pending: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Map ``pending`` keep segments (in prior-DELIVERED time) through
    ``prior`` keep segments (in original clip-relative time).

    ``prior is None`` means the previous delivered video WAS the original
    window (identity), so ``pending`` is already original-relative. A pending
    segment that straddles a prior seam maps to multiple original intervals.
    """
    if prior is None:
        return list(pending)
    composed: list[tuple[float, float]] = []
    offset = 0.0  # delivered-time start of the current prior segment
    for a, b in prior:
        seg_len = b - a
        for p_start, p_end in pending:
            lo = max(p_start, offset)
            hi = min(p_end, offset + seg_len)
            if hi > lo:
                composed.append((a + (lo - offset), a + (hi - offset)))
        offset += seg_len
    composed.sort()
    return composed


def map_words_to_delivered(
    words: list[dict], keep_segments: list[tuple[float, float]]
) -> list[dict]:
    """Project clip-relative word dicts (``{"word", "start", "end"}``) onto the
    delivered timeline described by ``keep_segments`` (same timebase as the
    words). Words falling entirely inside a cut are dropped; a word straddling
    a seam is clamped to its largest kept overlap. Output times are
    delivered-relative seconds, ascending.
    """
    mapped: list[dict] = []
    for w in words:
        w_start = float(w.get("start", 0.0))
        w_end = float(w.get("end", w_start))
        best: tuple[float, float, float] | None = None  # (overlap, d_start, d_end)
        offset = 0.0
        for a, b in keep_segments:
            lo = max(w_start, a)
            hi = min(w_end, b)
            if hi > lo and (best is None or hi - lo > best[0]):
                best = (hi - lo, offset + (lo - a), offset + (hi - a))
            offset += b - a
        if best is not None:
            mapped.append({**w, "start": best[1], "end": best[2]})
    return mapped


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
