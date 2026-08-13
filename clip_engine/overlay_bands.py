"""Transient overlay-band detection — the superchat problem (Issue 448).

A livestream superchat is drawn by the streaming software **on top of** the
camera feed, so it lands INSIDE the camera region that Issues 439/443 resolve
and the region crop cannot exclude it. On video `7e988321` two of nine rendered
clips shipped with a stranger's donation banner burned in, one of them across
the opening five seconds.

Why this cannot live in ``camera_region``: that module's video-level pass takes
a **component-wise median across nine windows and keeps it only on a strict
majority** precisely so a transient overlay cannot move the answer (Issue 443).
Its blindness to transients is the property that makes it correct. This module
wants the opposite — the transients themselves — so it is a separate pass over
the same samplers rather than a change to that consensus.

The measurement, validated against `tests/fixtures/superchat/`: in the lower
``_LOWER_FRAC`` of each frame, count rows whose mean brightness exceeds **that
frame's own** lower-region median by ``_BRIGHT_DELTA``. A full-width donation
banner is a broad, near-uniform, markedly brighter horizontal slab; ordinary
footage is not. Baselining per frame is what makes the feature independent of
exposure, grade and set lighting. On the 36 fixture frames the counts separate
14-20 (clean) from 26-28 (band), and ``_MIN_BRIGHT_ROWS`` sits between them.

Never run this on a RENDERED clip. Burned-in captions are themselves a bright
lower-frame band; a rendered-clip scan on 2026-08-11 flagged two clips that are
visually clean. Detection belongs at ingest, on the source, before captions and
before the region pre-crop.
"""

from __future__ import annotations

import logging
from pathlib import Path

from clip_engine.camera_region import _sample_gray_frames_timed

logger = logging.getLogger(__name__)

# Bump when detection SEMANTICS change, not only when the stored shape does.
# Same contract as `camera_region.VIDEO_REGION_VERSION`, and for the same
# reason: the backfill's failure markers are keyed by it, so a detector fix
# invalidates the markers its own bug wrote (the trap that cost a full cycle on
# Issue 443).
# v2 (Issue 466): span times come from real per-sample timestamps instead of
# `duration / len(stack)`, and the document records `scanned_until_s`. Every
# v1 document was computed over a scrambled, time-dilated timeline on sources
# longer than ~500 s, so v1 is invalidated on read.
OVERLAY_SPANS_VERSION = 2

# Only the lower part of the frame is considered. Superchats, lower thirds and
# ticker bars live there; a bright ceiling light or window does not.
_LOWER_FRAC = 0.60

# A row counts as "band" when its mean exceeds the frame's own lower-region
# median by this much (8-bit levels). Derived from the fixtures, not guessed.
_BRIGHT_DELTA = 25.0

# Rows required before a frame is called positive. Fixture separation is
# 14-20 clean vs 26-28 band at 480x270, so 24 sits between with margin on both
# sides. Scaled by the analysis height so a different source aspect still works.
_MIN_BRIGHT_ROWS_FRAC = 24.0 / 270.0

# FAIL OPEN. A lone positive sample is noise — a camera flash, a bright cut. A
# real banner persists for seconds. Requiring a run means an uncertain signal
# produces no mask at all, which is the right way round: a false positive blurs
# a speaker's face in an otherwise good clip, which is worse than the defect
# this module exists to remove.
_MIN_RUN_SAMPLES = 3

# Seconds per sample. 2 Hz bounds span edges to +-0.5 s, which is tight enough
# that the padding below covers the error without masking real content.
_SAMPLE_INTERVAL_S = 0.5

# Spans are padded outward so a banner's fade-in/out is covered rather than
# clipped at the edges.
_SPAN_PAD_S = 0.5

# Vertical padding on the measured band, as a fraction of frame height. The
# analysis pass runs at 480px wide, so one analysis row is ~4 source rows on a
# 1080p frame; a rounded edge or a drop shadow can fall just outside. Covering
# a few extra rows of backdrop is free, leaving a bright sliver is not.
_BAND_PAD_FRAC = 0.01

# Detection is a best-effort enhancement; ingest must never wait on it forever.
# Truncation is EXPLICIT (Issue 466): when the budget runs out the stored
# document records `scanned_until_s`, so masking only ever applies within the
# scanned range instead of silently dilating over the whole runtime. Default
# matches `OVERLAY_BAND_TIMEOUT_S`, which the worker call sites pass.
_TIMEOUT_S = 600.0

# A gap between neighbouring samples larger than this many REQUESTED intervals
# splits a run — dropped frames must not bridge two separate banners into one.
_MAX_GAP_INTERVALS = 3.0

# Blur strength. Large enough that a donation name and dollar amount are not
# legible after the 1080-wide upscale, small enough to read as a deliberate
# privacy blur rather than a corrupted frame.
_BLUR_RADIUS = 12
_BLUR_PASSES = 2


def _bright_row_counts(stack: list) -> tuple[list[int], int]:
    """Per-frame count of anomalously bright rows in the lower frame.

    Returns ``(counts, analysis_height)``. Pure over the sampled stack so the
    threshold can be exercised against fixtures without ffmpeg.
    """
    import numpy as np

    arr = np.stack(stack).astype(np.float32)
    _n, h, _w = arr.shape
    lower = arr[:, int(h * _LOWER_FRAC) :, :]
    row_mean = lower.mean(axis=2)
    baseline = np.median(row_mean, axis=1, keepdims=True)
    counts = (row_mean > baseline + _BRIGHT_DELTA).sum(axis=1)
    return [int(c) for c in counts], h


def _run_band_rows(stack: list, first: int, last: int) -> tuple[int, int] | None:
    """Vertical extent (top, bottom) of ONE run's band, in ANALYSIS rows.

    Majority-of-frames **within the run**, which is the scope where that rule is
    sound: a run is a single banner instance, so its geometry is constant and a
    speaker who is briefly bright cannot widen the mask.

    Measuring across every positive frame in the video instead would be wrong,
    and silently so. Two banners of different heights share no rows in a
    majority of frames, so the majority collapses to their INTERSECTION and the
    taller one renders with its top edge still showing. Verified against the
    fixtures 2026-08-11: a whole-video majority yields rows 217-242 while the
    onset banner reaches row 199 — 72 source pixels of banner left visible.
    """
    import numpy as np

    arr = np.stack(stack[first : last + 1]).astype(np.float32)
    _n, h, _w = arr.shape
    offset = int(h * _LOWER_FRAC)
    lower = arr[:, offset:, :]
    row_mean = lower.mean(axis=2)
    baseline = np.median(row_mean, axis=1, keepdims=True)
    hot = row_mean > baseline + _BRIGHT_DELTA
    rows = np.flatnonzero(hot.mean(axis=0) >= 0.5)
    if rows.size == 0:
        return None
    return offset + int(rows.min()), offset + int(rows.max())


def _runs(positive: list[bool], min_run: int) -> list[tuple[int, int]]:
    """Index runs of consecutive positives at least ``min_run`` long."""
    out: list[tuple[int, int]] = []
    start: int | None = None
    for i, p in enumerate(positive):
        if p and start is None:
            start = i
        elif not p and start is not None:
            if i - start >= min_run:
                out.append((start, i - 1))
            start = None
    if start is not None and len(positive) - start >= min_run:
        out.append((start, len(positive) - 1))
    return out


def _timed_runs(
    positive: list[bool], times: list[float], min_run: int, max_gap_s: float
) -> list[tuple[int, int]]:
    """``_runs`` split wherever neighbouring samples are more than
    ``max_gap_s`` apart. The seek sampler skips failed frames and stops at its
    budget, so consecutive INDICES are not consecutive TIMES — two banners
    separated by a stretch of dropped samples must not merge into one run."""
    out: list[tuple[int, int]] = []
    seg_start = 0
    for i in range(1, len(times) + 1):
        if i == len(times) or times[i] - times[i - 1] > max_gap_s:
            out.extend(
                (seg_start + a, seg_start + b) for a, b in _runs(positive[seg_start:i], min_run)
            )
            seg_start = i
    return out


def detect_overlay_spans(
    source_path: Path,
    duration_s: float,
    frame_w: int,
    frame_h: int,
    *,
    sample_interval_s: float = _SAMPLE_INTERVAL_S,
    timeout_s: float = _TIMEOUT_S,
) -> dict | None:
    """Find transient bright overlay bands across the whole source.

    Returns the stored document, or ``None`` when nothing was found or the pass
    could not run. ``None`` is not a failure — it means no masking, which is
    exactly today's behaviour.

    The rect is in **absolute source pixels** and is full width: a superchat
    spans the frame, and a full-width mask cannot clip its edge if the measured
    horizontal extent is slightly short.
    """
    try:
        if duration_s <= 0 or frame_h <= 0:
            return None
        sample_frames = int(duration_s / max(sample_interval_s, 0.05))
        if sample_frames < _MIN_RUN_SAMPLES:
            return None

        timed = _sample_gray_frames_timed(source_path, 0.0, duration_s, sample_frames, timeout_s)
        if timed is None or len(timed[0]) < _MIN_RUN_SAMPLES:
            logger.info("overlay_bands: too few samples (%s) — no masking", timed and len(timed[0]))
            return None
        samples, scanned_until_s = timed
        times = [t for t, _ in samples]
        stack = [frame for _, frame in samples]

        counts, analysis_h = _bright_row_counts(stack)
        min_rows = max(1, int(round(_MIN_BRIGHT_ROWS_FRAC * analysis_h)))
        positive = [c >= min_rows for c in counts]
        runs = _timed_runs(
            positive, times, _MIN_RUN_SAMPLES, _MAX_GAP_INTERVALS * sample_interval_s
        )
        if not runs:
            logger.info(
                "overlay_bands: no run of %d+ positive samples (max %d rows vs threshold %d) "
                "— no masking",
                _MIN_RUN_SAMPLES,
                max(counts, default=0),
                min_rows,
            )
            return None

        # Extent per run, then UNION — see `_run_band_rows` for why a
        # whole-video majority silently under-covers.
        extents = [e for e in (_run_band_rows(stack, a, b) for a, b in runs) if e is not None]
        if not extents:
            return None
        top_row = min(e[0] for e in extents)
        bottom_row = max(e[1] for e in extents)

        # Analysis rows -> source pixels. Same scaling idiom as camera_region.
        sy = frame_h / analysis_h
        pad = int(round(_BAND_PAD_FRAC * frame_h))
        y = max(0, int(top_row * sy) - pad)
        h = min(frame_h - y, max(1, int((bottom_row - top_row + 1) * sy) + 2 * pad))

        # Span edges come from the run's REAL sample timestamps (Issue 466) —
        # never from index * interval, which dilates past the first dropped
        # frame or budget cut. The pad covers the half-interval uncertainty on
        # each side plus a banner's fade-in/out.
        spans = []
        for a, b in runs:
            spans.append(
                {
                    "start_s": round(max(0.0, times[a] - _SPAN_PAD_S), 3),
                    "end_s": round(min(duration_s, times[b] + _SPAN_PAD_S), 3),
                }
            )

        total = sum(s["end_s"] - s["start_s"] for s in spans)
        logger.info(
            "overlay_bands: %d span(s), %.1fs of %.1fs (%.1f%%), scanned to %.1fs, "
            "band y=%d h=%d of %dpx (threshold %d rows, peak %d)",
            len(spans),
            total,
            duration_s,
            100.0 * total / duration_s,
            scanned_until_s,
            y,
            h,
            frame_h,
            min_rows,
            max(counts, default=0),
        )
        return {
            "version": OVERLAY_SPANS_VERSION,
            "y": y,
            "height": h,
            "frame": {"width": frame_w, "height": frame_h},
            "spans": spans,
            "samples": len(stack),
            "sample_interval_s": round(sample_interval_s, 4),
            "scanned_until_s": round(scanned_until_s, 3),
        }
    except Exception as exc:  # noqa: BLE001 — diagnostic pass, never fails ingest
        logger.warning("overlay_bands: detection failed (%s) — no masking", exc)
        return None


def spans_from_video_json(
    stored: dict | None, frame_w: int, frame_h: int
) -> tuple[int, int, list[tuple[float, float]]] | None:
    """Unpack stored spans as ``(y, height, [(start_s, end_s), ...])``.

    Rejects a document measured against different source dimensions — a
    re-upload can change them, and masking the wrong rows is worse than not
    masking at all. Same guard, and same reasoning, as
    ``camera_region.region_from_video_json``.
    """
    if not stored or stored.get("version") != OVERLAY_SPANS_VERSION:
        return None
    frame = stored.get("frame") or {}
    if (frame.get("width"), frame.get("height")) != (frame_w, frame_h):
        logger.info(
            "overlay_bands: stored spans were measured against %sx%s but the source is %dx%d "
            "— skipping the mask",
            frame.get("width"),
            frame.get("height"),
            frame_w,
            frame_h,
        )
        return None
    spans = [
        (float(s["start_s"]), float(s["end_s"]))
        for s in (stored.get("spans") or [])
        if float(s.get("end_s", 0.0)) > float(s.get("start_s", 0.0))
    ]
    if not spans:
        return None
    y, h = int(stored.get("y", 0)), int(stored.get("height", 0))
    if h <= 0 or y < 0 or y + h > frame_h:
        return None
    return y, h, spans


def overlay_blur_filters(
    y: int,
    height: int,
    spans: list[tuple[float, float]],
    clip_start_s: float,
    clip_end_s: float,
    frame_w: int,
) -> list[str]:
    """ffmpeg vf fragment masking ``spans`` that overlap this clip's window.

    Returns ``[]`` when nothing overlaps, so an unaffected clip's filter chain
    stays byte-identical to what it renders today.

    Shape: ``split`` the frame, blur a full-width crop of the band, and
    ``overlay`` it back with a timeline ``enable``. ``boxblur`` alone would not
    do — it has no geometry and blurs the WHOLE frame. ``crop`` and ``scale``
    have no timeline support, which is why the mask cannot instead be a
    time-varying crop. Times are clip-relative because the render seeks with
    ``-ss`` before ``-i``.

    Returned as ONE element whose internal chains are separated by ``;``, not as
    three elements joined by ``render.py``'s ``,``. Both forms happen to parse —
    ffmpeg lets explicit link labels override the comma's implicit "pipe into
    the next filter" — but the comma form only works *because* of that override
    and reads as a linear chain when it is a branch. The semicolon is ffmpeg's
    documented separator for independent chains, so the graph says what it means
    and stays correct if a label is ever renamed. Every filter AFTER this
    fragment still chains onto the ``overlay`` output via the caller's comma,
    which is exactly what the region pre-crop and the 9:16 crop need.
    """
    windows = []
    for start_s, end_s in spans:
        a = max(start_s, clip_start_s) - clip_start_s
        b = min(end_s, clip_end_s) - clip_start_s
        if b > a:
            windows.append((max(0.0, a), b))
    if not windows:
        return []

    enable = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in windows)
    return [
        f"split[ob_bg][ob_fg];"
        f"[ob_fg]crop={frame_w}:{height}:0:{y},boxblur={_BLUR_RADIUS}:{_BLUR_PASSES}[ob_bl];"
        f"[ob_bg][ob_bl]overlay=0:{y}:enable='{enable}'"
    ]
