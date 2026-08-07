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
import subprocess
import tempfile
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


VIDEO_REGION_VERSION = 1


def detect_video_camera_region(
    source_path: Path,
    duration_s: float,
    frame_w: int,
    frame_h: int,
    *,
    sample_frames: int = 24,
    **kwargs: object,
) -> dict | None:
    """Resolve the camera region ONCE for a whole video (Issue 439).

    Same detector, sampled across the entire runtime instead of one clip window,
    so every clip of a source shares one answer. Sampling wide is also what makes
    an intermittent overlay harmless: a SUBSCRIBE animation that is on screen for
    part of the video contributes far less temporal variance across 24 frames
    spanning 27 minutes than across 10 frames inside the 84 seconds it happens to
    cover — which is how one clip came to disagree with its siblings.

    No ``face_box`` is passed: there is no single face for a whole video. The
    per-clip face-sanity check still runs at render time against this rect, so a
    bad video-level answer is caught there rather than trusted blindly.

    Returns the storage shape for ``Video.camera_region_jsonb``, or ``None`` when
    detection declines (same fail-open contract as ``detect_camera_region``).
    """
    if duration_s <= 0:
        return None
    region = detect_camera_region(
        source_path,
        0.0,
        duration_s,
        frame_w,
        frame_h,
        sample_frames=sample_frames,
        **kwargs,  # type: ignore[arg-type]
    )
    if region is None:
        return None
    x, y, w, h = region
    return {
        "version": VIDEO_REGION_VERSION,
        "x": x,
        "y": y,
        "width": w,
        "height": h,
        "frame": {"width": frame_w, "height": frame_h},
        "sample_frames": sample_frames,
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
    fps = sample_frames / duration
    with tempfile.TemporaryDirectory(prefix="camregion_") as tmp_dir:
        pattern = str(Path(tmp_dir) / "f%03d.png")
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
            f"fps={fps:.6f},scale={_ANALYSIS_WIDTH}:-2,format=gray",
            "-frames:v",
            str(sample_frames),
            pattern,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        except (subprocess.SubprocessError, OSError) as exc:
            logger.warning("camera_region: frame sampling failed (%s)", exc)
            return None
        if result.returncode != 0:
            logger.warning(
                "camera_region: ffmpeg sampling failed: %s",
                result.stderr[-300:] if result.stderr else "?",
            )
            return None
        frames = []
        for p in sorted(Path(tmp_dir).glob("f*.png")):
            img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
            if img is not None:
                frames.append(img)
        return frames or None
