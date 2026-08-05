"""
Shot-change detection for the speaker-aware reframe (Issue 419).

Primary path: ONE downscaled ffmpeg ``scdet`` pass over the clip window —
``-vf scale=320:-2,scdet=threshold=<SHOT_DETECT_SCDET_THRESHOLD>`` to
``-f null -`` — parsing ``lavfi.scd.time`` values from stderr. Frame-accurate,
zero new Python dependencies (the worker already ships ffmpeg and the
measurement-pass subprocess pattern from render.py's loudnorm). This amends
the 2026-06-23 "PySceneDetect when needed" pencil-in: we only need hard cuts
in talking-head footage; PySceneDetect's edge (gradual transitions) remains
the documented upgrade path. See docs/DECISIONS.md (2026-08-04, decision 7).

Fallback: histogram-diff over the reframe pipeline's EXISTING 5 fps frame
samples (no second decode) — ±1/sample_fps accuracy, good enough to reset
smoothing state near the right frame. Total failure → ``[]`` = one shot,
which is safe: the planner then treats the clip as a single shot (today's
behavior, no cuts forced).

CONTRACT (binding for clip_engine/speaker_map.py + reframe.py, Issue 420):
  - face tracks NEVER span a shot boundary;
  - EMA smoothing state RESETS at every shot boundary;
  - the crop NEVER pans across a source cut — the planner always emits a
    crop cut at every detected shot change.

All returned times are SOURCE-relative seconds (the same time base as the
crop-center track); the wire contract's clip-relative ``shots:[{t}]`` is a
serialization-time conversion.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# scdet logs one line per detected change:
#   [Parsed_scdet_1 @ 0x7fc3ac010400] lavfi.scd.score: 15.625, lavfi.scd.time: 2
# The time value may be integral ("2") or fractional ("2.002").
_SCD_TIME_RE = re.compile(r"lavfi\.scd\.time:\s*([0-9]+(?:\.[0-9]+)?)")

# Histogram-diff fallback: 8 bins/channel keeps the descriptor tiny while
# separating hard cuts (inter-shot L1/2 distance ~0.8+ on real footage) from
# in-shot motion (~0.05–0.2). 0.5 is the midpoint convention for normalized
# histogram intersection distance.
_HIST_BINS = 8
_HIST_DIFF_THRESHOLD = 0.5

# Two detections closer than this are the same physical cut (scdet can fire
# on consecutive frames across a dissolve-ish transition).
_MIN_CUT_SPACING_S = 0.25


def _parse_scdet_output(stderr: str) -> list[float]:
    """Extract ``lavfi.scd.time`` values from a captured ffmpeg stderr.

    Pure and unit-tested against a REAL captured stderr fixture
    (tests/fixtures/scdet_stderr.txt). Returns sorted, de-duplicated
    clip-relative times; garbage/empty input → ``[]``.
    """
    times: list[float] = []
    for match in _SCD_TIME_RE.finditer(stderr):
        try:
            times.append(float(match.group(1)))
        except ValueError:  # pragma: no cover — the regex only matches numbers
            continue
    times.sort()
    deduped: list[float] = []
    for t in times:
        if not deduped or t - deduped[-1] >= _MIN_CUT_SPACING_S:
            deduped.append(t)
    return deduped


def _frame_histogram(frame: object) -> object:
    """Normalized 3-D color histogram (8×8×8) of a BGR frame array.

    numpy-only (no cv2) so the fallback stays computable anywhere the sampled
    frames already exist. Returns a flat float array summing to 1.0.
    """
    import numpy as np

    pixels = np.asarray(frame).reshape(-1, 3).astype(np.float64)
    hist, _ = np.histogramdd(pixels, bins=(_HIST_BINS,) * 3, range=((0, 256), (0, 256), (0, 256)))
    total = hist.sum()
    if total <= 0:
        return np.zeros(_HIST_BINS**3, dtype=np.float64)
    return (hist / total).ravel()


def _histogram_changes(
    histograms: list[tuple[float, object]],
    *,
    diff_threshold: float = _HIST_DIFF_THRESHOLD,
) -> list[float]:
    """Detect shot changes from consecutive-histogram L1/2 distances.

    ``histograms`` is an ordered list of ``(timestamp_s, normalized_hist)``.
    Distance ``0.5 · Σ|h₁−h₂| ∈ [0, 1]``; a jump above ``diff_threshold``
    marks a change at the LATER sample's timestamp (the first frame of the
    new shot). Pure — unit-tested with synthetic histograms.
    """
    import numpy as np

    changes: list[float] = []
    for (_, prev_h), (t, cur_h) in zip(histograms, histograms[1:], strict=False):
        distance = 0.5 * float(np.abs(np.asarray(cur_h) - np.asarray(prev_h)).sum())
        if distance > diff_threshold and (not changes or t - changes[-1] >= _MIN_CUT_SPACING_S):
            changes.append(t)
    return changes


def histogram_shot_changes(
    samples: list[tuple[float, object]],
    *,
    diff_threshold: float = _HIST_DIFF_THRESHOLD,
) -> list[float]:
    """Fallback detector over already-decoded frame samples.

    ``samples`` is the reframe pipeline's existing 5 fps decode —
    ``(source_timestamp_s, BGR ndarray | None)`` pairs; ``None`` frames
    (failed reads) are skipped. Returns SOURCE-relative change times.
    Never raises — any error → ``[]`` (one shot).
    """
    try:
        histograms = [(t, _frame_histogram(f)) for t, f in samples if f is not None]
        if len(histograms) < 2:
            return []
        return _histogram_changes(histograms, diff_threshold=diff_threshold)
    except Exception as exc:
        logger.warning("histogram shot fallback failed: %s — treating clip as one shot", exc)
        return []


def detect_shot_changes(
    source_path: Path,
    start_s: float,
    end_s: float,
    *,
    threshold: float | None = None,
    timeout_s: float | None = None,
    fallback_samples: list[tuple[float, object]] | None = None,
) -> list[float]:
    """Detect hard shot changes inside the clip window ``[start_s, end_s]``.

    Runs the downscaled scdet pass; a SUCCESSFUL pass with zero detections is
    a valid "single shot" answer and does NOT trigger the fallback. Only a
    failed/timed-out/unstartable subprocess falls back to the histogram diff
    over ``fallback_samples`` (the already-decoded 5 fps frames, when the
    caller has them). Total failure → ``[]`` = one shot.

    Returns SOURCE-relative change times, strictly inside the clip window.
    """
    from config import settings  # local import — config is heavyweight at module init

    duration = end_s - start_s
    if duration <= 0:
        return []
    scd_threshold = settings.SHOT_DETECT_SCDET_THRESHOLD if threshold is None else threshold
    budget = timeout_s if timeout_s is not None else max(60.0, duration * 2)

    cmd = [
        "ffmpeg",
        "-v",
        "info",
        "-ss",
        str(start_s),
        "-i",
        str(source_path),
        "-t",
        str(duration),
        "-an",
        "-sn",
        "-dn",
        "-vf",
        f"scale=320:-2,scdet=threshold={scd_threshold}",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=budget)
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("scdet pass failed for %s: %s — trying histogram fallback", source_path, exc)
        return histogram_shot_changes(fallback_samples or [])
    if result.returncode != 0:
        logger.warning(
            "scdet pass exited %d for %s: %s — trying histogram fallback",
            result.returncode,
            source_path,
            result.stderr[-300:],
        )
        return histogram_shot_changes(fallback_samples or [])

    # scdet timestamps are relative to the -ss seek point → convert to source
    # time; keep only changes strictly inside the window (a "change" at t=0 is
    # the clip's own first frame, not a cut).
    clip_times = _parse_scdet_output(result.stderr)
    return [start_s + t for t in clip_times if 0.05 < t < duration - 0.05]
