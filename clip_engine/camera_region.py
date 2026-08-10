"""Camera-region detection for produced source layouts — Issue 430.

A produced podcast frame often carries its own chrome: show-logo cards, guest
name chips, social banners. The 9:16 render's full-height center crop slices
through all of it (logo cut mid-word, a third-party social banner occupying the
bottom of every Short). This module finds the ACTIVE CAMERA region — the part
of the frame where the video actually moves — so the render can crop/zoom into
it before the 9:16 composition.

Method: per-pixel temporal variance over a handful of sampled frames. Static
overlay pixels have near-zero temporal standard deviation while the live camera
region has motion — the patent-documented standard for static-logo/chrome
detection (US 11710315, US 7483484), and deployable today with only cv2+numpy
(mediapipe-free — Issue 422's stack is not required). Google's AutoFlip solves
a different problem (subject choice within the camera); this pass only decides
where the camera IS.

Fail-open contract: every uncertainty returns ``None`` → the caller keeps
today's full-height crop, so plain single-camera sources are byte-identical.
"""

from __future__ import annotations

import logging
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Downscale width for the sampled frames — variance geometry doesn't need
# resolution, and a small stack keeps the pass to tens of milliseconds.
_ANALYSIS_WIDTH = 480

# A secondary motion blob joins the region only if it is at least this fraction
# of the largest blob's area …
_SECONDARY_AREA_FRAC = 0.2
# … AND shares at least this fraction of its vertical span with the primary blob
# (Issue 439). A second camera in a side-by-side layout sits BESIDE the primary
# at a similar y, so it overlaps vertically; an animated SUBSCRIBE button,
# socials strip or superchat popup sits BELOW the camera and shares no vertical
# span. Area ratio alone cannot tell those apart, which is how a whole clip
# shipped with the overlay burned into the bottom third.
_MIN_VERTICAL_OVERLAP_FRAC = 0.5


def detect_camera_region(
    source_path: Path,
    start_s: float,
    end_s: float,
    frame_w: int,
    frame_h: int,
    *,
    sample_frames: int = 10,
    motion_thresh: float = 6.0,
    min_area_frac: float = 0.30,
    min_height_frac: float = 0.55,
    full_frame_frac: float = 0.92,
    pad_frac: float = 0.02,
    face_box: tuple[int, int, int, int] | None = None,
    timeout_s: float = 60.0,
) -> tuple[int, int, int, int] | None:
    """Return the active camera region ``(x, y, w, h)`` in source pixels, or
    ``None`` when the frame should be treated as all-camera (today's behavior).

    Gates (ALL must hold, else ``None``):
      - region area ≥ ``min_area_frac`` of the frame (a tiny motion blob is not
        a camera — e.g. an animated logo on an otherwise static slate),
      - region height ≥ ``min_height_frac`` of the frame (protects portrait
        framing quality after the crop),
      - region area ≤ ``full_frame_frac`` of the frame (near-full ⇒ no chrome
        worth cropping — skip the no-op),
      - the detected face box (when provided) lies inside the region (if the
        motion mask found something the face is NOT in, it found the wrong
        thing).
    Any exception → log + ``None`` (matches the render pipeline's face-detect
    degrade pattern).
    """
    try:
        import cv2
        import numpy as np

        stack = _sample_gray_frames(source_path, start_s, end_s, sample_frames, timeout_s)
        if stack is None or len(stack) < 3:
            return None

        arr = np.stack(stack).astype(np.float32)
        std = arr.std(axis=0)
        mask = (std > motion_thresh).astype(np.uint8) * 255

        # Fuse the talking-head region, drop speckle noise.
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None
        areas = [cv2.contourArea(c) for c in contours]
        largest = max(areas)
        if largest <= 0:
            return None
        # Anchor on the largest blob — that is the camera — then union the
        # secondary blobs that plausibly belong to the same camera row. A
        # side-by-side two-camera layout must yield ONE region spanning both
        # cameras, but an overlay strip must NOT be absorbed (Issue 439), so a
        # secondary blob has to clear the area ratio AND share the primary's
        # vertical span.
        px, py, pw, ph = cv2.boundingRect(contours[areas.index(largest)])
        boxes = [(px, py, pw, ph)]
        for c, a in zip(contours, areas, strict=True):
            if a < _SECONDARY_AREA_FRAC * largest:
                continue
            x, y, w, h = cv2.boundingRect(c)
            if (x, y, w, h) == (px, py, pw, ph):
                continue
            overlap = min(py + ph, y + h) - max(py, y)
            if overlap >= _MIN_VERTICAL_OVERLAP_FRAC * min(ph, h):
                boxes.append((x, y, w, h))
            else:
                logger.info(
                    "camera_region: excluding motion blob (%d,%d,%d,%d) — shares %dpx of "
                    "vertical span with the camera blob (%d,%d,%d,%d), below the %.0f%% "
                    "floor; treating it as overlay chrome",
                    x,
                    y,
                    w,
                    h,
                    max(overlap, 0),
                    px,
                    py,
                    pw,
                    ph,
                    _MIN_VERTICAL_OVERLAP_FRAC * 100,
                )
        rx0 = min(b[0] for b in boxes)
        ry0 = min(b[1] for b in boxes)
        rx1 = max(b[0] + b[2] for b in boxes)
        ry1 = max(b[1] + b[3] for b in boxes)

        # Pad, clamp, and scale back to source pixels.
        mh, mw = std.shape
        pad_x, pad_y = pad_frac * mw, pad_frac * mh
        rx0 = max(0.0, rx0 - pad_x)
        ry0 = max(0.0, ry0 - pad_y)
        rx1 = min(float(mw), rx1 + pad_x)
        ry1 = min(float(mh), ry1 + pad_y)
        sx, sy = frame_w / mw, frame_h / mh
        region = (
            int(rx0 * sx),
            int(ry0 * sy),
            int((rx1 - rx0) * sx),
            int((ry1 - ry0) * sy),
        )

        rx, ry, rw, rh = region
        frame_area = frame_w * frame_h
        area_frac = (rw * rh) / frame_area if frame_area else 0.0
        if area_frac < min_area_frac:
            logger.info(
                "camera_region: motion region too small (%.0f%% of frame) — keeping full frame",
                area_frac * 100,
            )
            return None
        if rh < min_height_frac * frame_h:
            logger.info(
                "camera_region: motion region too short (%dpx of %dpx) — keeping full frame",
                rh,
                frame_h,
            )
            return None
        if area_frac > full_frame_frac:
            logger.info(
                "camera_region: motion covers %.0f%% of frame — no chrome detected",
                area_frac * 100,
            )
            return None
        if face_box is not None:
            fx, fy, fw, fh = face_box
            face_cx, face_cy = fx + fw / 2, fy + fh / 2
            if not (rx <= face_cx <= rx + rw and ry <= face_cy <= ry + rh):
                logger.warning(
                    "camera_region: detected face center (%d,%d) outside motion region "
                    "%s — distrusting the region, keeping full frame",
                    face_cx,
                    face_cy,
                    region,
                )
                return None
        logger.info(
            "camera_region: cropping into %s (%.0f%% of the %dx%d frame)",
            region,
            area_frac * 100,
            frame_w,
            frame_h,
        )
        return region
    except Exception as exc:
        logger.warning("camera_region detection failed (%s) — keeping full frame", exc)
        return None


# Bump whenever detection SEMANTICS change, not only when the stored shape
# changes. The version invalidates two things at once: stored rects (see
# ``region_from_video_json`` below) and the hourly backfill's failure markers,
# which ``worker/tasks.py`` keys by version. A detector change that would
# produce a different answer for the same input MUST bump this, or the backfill
# silently suppresses retries for up to a week AFTER the fix lands — which is
# exactly what cost a full debugging cycle on Issue 443.
# 1 → 2 (Issue 443): one whole-runtime detection → a consensus of short windows.
VIDEO_REGION_VERSION = 2

# Span of each consensus window. Mid-range of the 30-90 s clip window the
# detector is frame-verified on, and safely under ``_LINEAR_DECODE_MAX_SPAN_S``
# so every window routes through ``_sample_by_linear_decode`` — the same
# machinery, at the same operating point, as a per-clip detection.
_WINDOW_SPAN_S = 60.0
# A hard floor, not a preference: ``statistics.median`` of two values is their
# MEAN, whose breakdown point is 0, so one poisoned window would drag a
# two-survivor "consensus" halfway to itself. Robustness starts at three.
_MIN_CONSENSUS_WINDOWS = 3
# Cost cap — 9 x 60 s = 540 s of decoded video for a 27-minute source.
_MAX_WINDOWS = 9
# Agreement floor between one window's rect and the consensus. Derived from the
# measured defect: the healthy band is h≈548 and the defective one h=757 at the
# same x/w, i.e. IoU 0.724 — so any threshold above ~0.73 rejects it. 0.80 keeps
# a margin while still tolerating a 25% height difference, or a vertical offset
# of 11% of region height, between windows that genuinely agree.
_MIN_WINDOW_IOU = 0.80
# Don't issue a detection the deadline has already doomed.
_MIN_WINDOW_TIMEOUT_S = 5.0


def _window_spans(duration_s: float, span_s: float, max_windows: int) -> list[tuple[float, float]]:
    """Placements for the consensus windows, or ``[]`` if the runtime is too
    short to take a consensus from at all.

    Each window is centred inside its own ``1/n`` slice of the runtime. Two
    properties fall out of that construction instead of needing clamps:

    - ``seg >= span_s`` is guaranteed (``n <= duration_s // span_s``), so the
      offset is never negative: the first window cannot start before 0 and the
      last cannot run past the end.
    - The windows are provably DISJOINT. That is deliberate, not incidental —
      overlapping windows share source frames, so a single overlay burst would
      poison two of them and erode the median's 50% breakdown point.

    Centring also buys head/tail margin for free (on a 27-minute source the
    first window starts near 60 s and the last ends near 1557 s), skipping the
    cold-open montage and the end card — the two stretches whose layout is
    least like the body of the video.
    """
    n = min(int(duration_s // span_s), max_windows)
    if n < _MIN_CONSENSUS_WINDOWS:
        return []
    seg = duration_s / n
    offset = (seg - span_s) / 2.0
    spans = []
    for i in range(n):
        start = max(0.0, i * seg + offset)
        spans.append((start, min(duration_s, start + span_s)))
    return spans


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """Intersection-over-union of two ``(x, y, w, h)`` rects."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _median_rect(
    rects: list[tuple[int, int, int, int]], frame_w: int, frame_h: int
) -> tuple[int, int, int, int]:
    """Component-wise median of the surviving rects, clamped to the frame.

    This is the MARGINAL median: ``y`` may come from one window and ``h`` from
    another, so the result is NOT required to be a member of ``rects`` and can
    violate gates that every individual survivor cleared. The caller re-validates
    it against the detector's own gates for exactly that reason.
    """
    x = max(0, int(statistics.median([float(r[0]) for r in rects])))
    y = max(0, int(statistics.median([float(r[1]) for r in rects])))
    w = min(int(statistics.median([float(r[2]) for r in rects])), frame_w - x)
    h = min(int(statistics.median([float(r[3]) for r in rects])), frame_h - y)
    return (x, y, w, h)


def detect_video_camera_region(
    source_path: Path,
    duration_s: float,
    frame_w: int,
    frame_h: int,
    *,
    sample_frames: int = 10,
    motion_thresh: float = 6.0,
    min_area_frac: float = 0.30,
    min_height_frac: float = 0.55,
    full_frame_frac: float = 0.92,
    pad_frac: float = 0.02,
    timeout_s: float = 240.0,
) -> dict | None:
    """Resolve the camera region ONCE for a whole video, as a consensus of
    several short windows (Issue 439 Stage 2, rebuilt by Issue 443).

    The first build ran ONE detection across the entire runtime, which measures
    the wrong thing. Temporal-variance detection works per clip *because* a
    30-90 s window has a stable layout; over 27 minutes nearly every pixel
    changes at some point — overlays come and go, segments differ, layouts shift
    — so the motion mask saturates and the region grows to swallow the very
    chrome it exists to exclude. It measured a 0.70 height fraction where the
    healthy per-clip siblings measured 0.51. A longer temporal window
    reclassifying static regions as moving is the documented behaviour of this
    family of detectors (Porikli 2007, "Detection of Temporarily Static Regions
    by Processing Video at Different Frame Rates").

    So: run the UNMODIFIED per-clip detector over N short windows spread across
    the runtime, drop the declines, take a component-wise median of the
    survivors, and keep it only if a majority of them agree with it. Each
    detection gets the stable-layout span it was designed for, and the median's
    50% breakdown point means one overlay-heavy window cannot move the answer.

    ``sample_frames`` is PER WINDOW, not a total to divide up. Dividing would be
    a silent total failure rather than a tuning choice: ``detect_camera_region``
    rejects any stack under 3 frames, so a split budget makes every window
    decline and this function return ``None`` forever — the same inertness class
    as the sampling defect that preceded this one.

    No ``face_box`` is passed: there is no single face for a whole video. The
    per-clip face-sanity check still runs against this rect at render time, so a
    bad video-level answer is caught there rather than trusted blindly.

    Returns the storage shape for ``Video.camera_region_jsonb``, or ``None``
    when the consensus declines — which is a correct outcome, not an error: the
    render simply detects per clip, the path this one is built out of.
    """
    if duration_s <= 0:
        return None
    spans = _window_spans(duration_s, _WINDOW_SPAN_S, _MAX_WINDOWS)
    if not spans:
        logger.info(
            "camera_region: %.0fs runtime is shorter than %d x %.0fs windows — no video-level "
            "consensus, every clip detects for itself",
            duration_s,
            _MIN_CONSENSUS_WINDOWS,
            _WINDOW_SPAN_S,
        )
        return None

    # A deadline, not a per-window quota: the fair share is recomputed from what
    # is actually left, so it GROWS as fast windows finish and no single slow
    # window can starve the ones behind it.
    deadline = time.monotonic() + timeout_s
    survivors: list[tuple[int, int, int, int]] = []
    attempted = 0
    for i, (start_s, end_s) in enumerate(spans):
        per_window = (deadline - time.monotonic()) / (len(spans) - i)
        if per_window < _MIN_WINDOW_TIMEOUT_S:
            logger.warning(
                "camera_region: consensus budget (%.0fs) exhausted after %d/%d windows",
                timeout_s,
                attempted,
                len(spans),
            )
            break
        attempted += 1
        region = detect_camera_region(
            source_path,
            start_s,
            end_s,
            frame_w,
            frame_h,
            sample_frames=sample_frames,
            motion_thresh=motion_thresh,
            min_area_frac=min_area_frac,
            min_height_frac=min_height_frac,
            full_frame_frac=full_frame_frac,
            pad_frac=pad_frac,
            timeout_s=per_window,
        )
        if region is not None:
            survivors.append(region)

    if len(survivors) < _MIN_CONSENSUS_WINDOWS:
        logger.info(
            "camera_region: only %d of %d windows detected a region (need %d) — declining the "
            "video-level consensus, every clip detects for itself",
            len(survivors),
            attempted,
            _MIN_CONSENSUS_WINDOWS,
        )
        return None

    # The marginal median is deliberately NOT re-checked against the detector's
    # own gates, because for an odd survivor count it cannot fail them. Each
    # component's median has at least ⌈n/2⌉ survivors at or above it, and two
    # such sets must intersect, so some survivor dominates the median in every
    # component at once — and that survivor already cleared the gates. The
    # clamps in ``_median_rect`` cover the even-count corner. An extra gate here
    # would be an unreachable branch, not a safety net; the agreement majority
    # below is what actually rejects a bad consensus.
    region = _median_rect(survivors, frame_w, frame_h)
    mx, my, mw, mh = region
    ious = [_iou(s, region) for s in survivors]
    agreeing = sum(1 for v in ious if v >= _MIN_WINDOW_IOU)
    # A strict majority is the only bar coherent with using a median at all: its
    # robustness claim is "a minority cannot move me". If half the survivors
    # disagree with the result, it is an artifact rather than a consensus.
    required = max(_MIN_CONSENSUS_WINDOWS, len(survivors) // 2 + 1)
    if agreeing < required:
        # Three gates can decline, so name which one fired and with what numbers
        # — otherwise a live drill cannot tell "correctly declined" from "still
        # broken". IoU conflates the axes; the height fractions disambiguate.
        logger.info(
            "camera_region: only %d of %d windows agree with the consensus rect %s (need %d) — "
            "declining. IoUs %s, height fracs %s",
            agreeing,
            len(survivors),
            region,
            required,
            [f"{v:.2f}" for v in ious],
            [f"{r[3] / frame_h:.3f}" for r in survivors],
        )
        return None

    logger.info(
        "camera_region: video-level consensus %s from %d of %d windows (height %.3f of frame)",
        region,
        agreeing,
        attempted,
        mh / frame_h,
    )
    return {
        "version": VIDEO_REGION_VERSION,
        "x": mx,
        "y": my,
        "width": mw,
        "height": mh,
        "frame": {"width": frame_w, "height": frame_h},
        "sample_frames": sample_frames,
        "windows": attempted,
        "windows_detected": len(survivors),
        "windows_agreeing": agreeing,
        "window_span_s": _WINDOW_SPAN_S,
    }


def region_from_video_json(
    stored: dict | None, frame_w: int, frame_h: int
) -> tuple[int, int, int, int] | None:
    """Unpack a stored video-level region, or ``None`` if it cannot be trusted.

    Rejects a rect measured against different source dimensions — a re-upload can
    change them, and cropping to a stale geometry is worse than re-detecting.
    """
    if not stored or stored.get("version") != VIDEO_REGION_VERSION:
        return None
    frame = stored.get("frame") or {}
    if (frame.get("width"), frame.get("height")) != (frame_w, frame_h):
        logger.info(
            "camera_region: stored region was measured against %sx%s but the source is "
            "%dx%d — re-detecting per clip",
            frame.get("width"),
            frame.get("height"),
            frame_w,
            frame_h,
        )
        return None
    try:
        return (
            int(stored["x"]),
            int(stored["y"]),
            int(stored["width"]),
            int(stored["height"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


# Above this span a single decode pass stops being viable. One ffmpeg call with
# an `fps` filter must decode the WHOLE span linearly; that is optimal for a
# 30-90 s clip window (one process, sequential read) but over a 27-minute video
# it is 20-30x the work and blew the 60 s timeout on the first real backfill —
# 24 frames at fps=0.0148 meant decoding all 1617 seconds. Long spans switch to
# one input-seek per frame, which is O(1) per sample regardless of file length.
# The threshold sits well above the 90 s clip ceiling so every per-clip call
# keeps its existing, frame-verified behaviour byte-identical.
_LINEAR_DECODE_MAX_SPAN_S = 120.0
# Per-seek ceiling; an input seek lands in well under a second on a local file.
_SEEK_FRAME_TIMEOUT_S = 10.0


def _sample_by_linear_decode(
    source_path: Path,
    start_s: float,
    duration: float,
    sample_frames: int,
    timeout_s: float,
    tmp_dir: str,
) -> bool:
    """Original single-pass extraction — one ffmpeg call over a short span."""
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{start_s:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(source_path),
        "-vf",
        f"fps={sample_frames / duration:.6f},scale={_ANALYSIS_WIDTH}:-2,format=gray",
        "-frames:v",
        str(sample_frames),
        str(Path(tmp_dir) / "f%03d.png"),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("camera_region: frame sampling failed (%s)", exc)
        return False
    if result.returncode != 0:
        logger.warning(
            "camera_region: ffmpeg sampling failed: %s",
            result.stderr[-300:] if result.stderr else "?",
        )
        return False
    return True


def _sample_by_seeking(
    source_path: Path,
    start_s: float,
    duration: float,
    sample_frames: int,
    timeout_s: float,
    tmp_dir: str,
) -> bool:
    """One input-seek per frame — cost is independent of the span's length.

    ``-ss`` BEFORE ``-i`` is input seeking: ffmpeg jumps to the nearest keyframe
    instead of decoding forward to it. Frames therefore land on keyframes rather
    than exact timestamps, which is irrelevant here — the detector wants diverse
    samples across the runtime, not precise ones.

    A single unreadable sample must not lose the whole pass, so failures are
    skipped; the caller's ``< 3 frames`` guard still rejects a stack too sparse
    to measure temporal variance against.
    """
    deadline = time.monotonic() + timeout_s
    captured = 0
    for i in range(sample_frames):
        if time.monotonic() >= deadline:
            logger.warning(
                "camera_region: sampling budget (%.0fs) exhausted after %d/%d frames",
                timeout_s,
                captured,
                sample_frames,
            )
            break
        t = start_s + duration * (i + 0.5) / sample_frames
        out = Path(tmp_dir) / f"f{i:03d}.png"
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{t:.3f}",
            "-i",
            str(source_path),
            "-frames:v",
            "1",
            "-vf",
            f"scale={_ANALYSIS_WIDTH}:-2,format=gray",
            "-f",
            "image2",
            str(out),
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=_SEEK_FRAME_TIMEOUT_S
            )
        except (subprocess.SubprocessError, OSError):
            continue
        if result.returncode == 0 and out.exists():
            captured += 1
    return captured > 0


def _sample_gray_frames(
    source_path: Path,
    start_s: float,
    end_s: float,
    sample_frames: int,
    timeout_s: float,
) -> list | None:
    """Extract ``sample_frames`` evenly-spaced grayscale frames (downscaled to
    ``_ANALYSIS_WIDTH``) from the clip window with ONE ffmpeg call. Returns a
    list of 2-D uint8 arrays, or ``None`` on any extraction failure."""
    import cv2

    duration = max(0.1, end_s - start_s)
    with tempfile.TemporaryDirectory(prefix="camregion_") as tmp_dir:
        if duration > _LINEAR_DECODE_MAX_SPAN_S:
            if not _sample_by_seeking(
                source_path, start_s, duration, sample_frames, timeout_s, tmp_dir
            ):
                return None
        elif not _sample_by_linear_decode(
            source_path, start_s, duration, sample_frames, timeout_s, tmp_dir
        ):
            return None
        frames = []
        for p in sorted(Path(tmp_dir).glob("f*.png")):
            img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
            if img is not None:
                frames.append(img)
        return frames or None
