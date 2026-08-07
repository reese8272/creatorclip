"""Unit tests for clip_engine/camera_region.py (Issue 430).

Pure numpy/cv2 — the ffmpeg frame-sampling seam is patched with synthetic
grayscale stacks. cv2 is required (same guard as test_render.py).
"""

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

pytest.importorskip("cv2")

from clip_engine.camera_region import detect_camera_region

_SRC = Path("/nonexistent/source.mp4")
_FRAME_W, _FRAME_H = 1920, 1080
# Analysis stack geometry (480 wide, 16:9).
_AW, _AH = 480, 270


def _stack(noise_slice: tuple[slice, slice] | None, n: int = 6) -> list[np.ndarray]:
    """Frames of constant gray 100; ``noise_slice`` (rows, cols) gets per-frame
    deterministic pseudo-random noise → high temporal std inside, zero outside."""
    rng = np.random.default_rng(42)
    frames = []
    for _ in range(n):
        f = np.full((_AH, _AW), 100, dtype=np.uint8)
        if noise_slice is not None:
            rows, cols = noise_slice
            shape = (rows.stop - rows.start, cols.stop - cols.start)
            f[rows, cols] = rng.integers(0, 255, size=shape, dtype=np.uint8)
        frames.append(f)
    return frames


def _stack_bands(noise_slices: list[tuple[slice, slice]], n: int = 6) -> list[np.ndarray]:
    """Like ``_stack`` but with several independently-noisy bands — used to model
    a live camera band plus a separate animated overlay strip."""
    rng = np.random.default_rng(42)
    frames = []
    for _ in range(n):
        f = np.full((_AH, _AW), 100, dtype=np.uint8)
        for rows, cols in noise_slices:
            shape = (rows.stop - rows.start, cols.stop - cols.start)
            f[rows, cols] = rng.integers(0, 255, size=shape, dtype=np.uint8)
        frames.append(f)
    return frames


def _detect(stack, **kwargs):
    with patch("clip_engine.camera_region._sample_gray_frames", return_value=stack):
        return detect_camera_region(_SRC, 10.0, 40.0, _FRAME_W, _FRAME_H, **kwargs)


def test_animated_overlay_strip_below_the_camera_is_excluded():
    """Issue 439: an animated SUBSCRIBE / socials / superchat strip must not be
    unioned into the camera region.

    The union rule at ``camera_region.py`` admits any contour ≥20% of the largest
    blob so a side-by-side two-camera layout yields one region. An animated
    overlay strip is also its own motion contour, so it was absorbed — which is
    how rank 6 of video 3b6992fe shipped with the SUBSCRIBE button, the socials
    strip and a live superchat burned into the bottom third for 84 seconds.

    A second camera sits BESIDE the primary blob at a similar y; an overlay strip
    sits BELOW it, sharing no vertical span.
    """
    camera = (slice(40, 200), slice(20, 460))  # the live camera band
    overlay = (slice(235, 268), slice(20, 460))  # wide, short, vertically disjoint
    region = _detect(_stack_bands([camera, overlay]))
    assert region is not None
    _rx, ry, _rw, rh = region
    # Analysis→source scale is 4×: the camera band ends at row 200 → 800 px.
    # Absorbing the overlay would push the bottom edge to ~1072.
    assert ry + rh <= 880, f"overlay strip was unioned into the region: bottom={ry + rh}"


def test_side_by_side_second_camera_is_still_unioned():
    """The two-camera layout the union rule exists for must keep working: a
    second blob BESIDE the primary, sharing its vertical span, is still merged."""
    left = (slice(40, 220), slice(20, 230))
    right = (slice(45, 215), slice(250, 460))
    region = _detect(_stack_bands([left, right]))
    assert region is not None
    rx, _ry, rw, _rh = region
    # Must span both cameras: left edge near col 20 (×4) and right edge near 460.
    assert rx <= 200
    assert rx + rw >= 1700


def test_detects_inner_camera_region():
    # Camera occupies rows 40-230, cols 60-420 (~53% of area, 70% of height);
    # everything else is static chrome.
    region = _detect(_stack((slice(40, 230), slice(60, 420))))
    assert region is not None
    rx, ry, rw, rh = region
    # Scale factor analysis→source is 4×; allow morphology/pad slack.
    assert 150 <= rx <= 260
    assert 100 <= ry <= 180
    assert 1300 <= rw <= 1650
    assert 700 <= rh <= 880


# ── video-level region (Issue 439 Stage 2) ───────────────────────────────────


def test_video_region_is_shared_by_every_clip_of_one_source():
    """One rect for the whole video is what stops clips from disagreeing.

    Modelled on the live failure: several clip windows detect cleanly and one is
    poisoned by an overlay. Resolved once at video level, every clip reads the
    same rect, so the poisoned window cannot produce its own answer.
    """
    from clip_engine.camera_region import detect_video_camera_region, region_from_video_json

    camera = (slice(40, 200), slice(20, 460))
    stack = _stack_bands([camera])
    with patch("clip_engine.camera_region._sample_gray_frames", return_value=stack):
        stored = detect_video_camera_region(_SRC, 1617.0, _FRAME_W, _FRAME_H)

    assert stored is not None
    assert stored["frame"] == {"width": _FRAME_W, "height": _FRAME_H}
    # Every clip unpacks the identical rect.
    rects = {region_from_video_json(stored, _FRAME_W, _FRAME_H) for _ in range(5)}
    assert len(rects) == 1
    assert rects.pop() is not None


def test_video_region_rejected_when_source_dimensions_changed():
    """A rect measured against other dimensions must not be trusted — a re-upload
    can change them, and cropping to stale geometry is worse than re-detecting."""
    from clip_engine.camera_region import region_from_video_json

    stored = {
        "version": 1,
        "x": 100,
        "y": 200,
        "width": 1400,
        "height": 600,
        "frame": {"width": 1920, "height": 1080},
        "sample_frames": 24,
    }
    assert region_from_video_json(stored, 1920, 1080) == (100, 200, 1400, 600)
    assert region_from_video_json(stored, 1280, 720) is None
    assert region_from_video_json(None, 1920, 1080) is None
    assert region_from_video_json({"version": 99}, 1920, 1080) is None


def test_zero_duration_video_has_no_region():
    from clip_engine.camera_region import detect_video_camera_region

    assert detect_video_camera_region(_SRC, 0.0, _FRAME_W, _FRAME_H) is None


def test_face_inside_guards_the_video_level_region():
    """The video-level rect carries no face check of its own, so the render path
    validates it per clip. No face detected → trusted (fail-open contract)."""
    from clip_engine.render import _face_inside

    region = (100, 200, 1400, 600)
    assert _face_inside((800, 400, 200, 200), region) is True
    assert _face_inside((1800, 900, 100, 100), region) is False
    assert _face_inside(None, region) is True


def test_full_frame_motion_returns_none():
    region = _detect(_stack((slice(0, _AH), slice(0, _AW))))
    assert region is None  # ≥ full_frame_frac ⇒ no chrome ⇒ keep full frame


def test_tiny_motion_blob_returns_none():
    region = _detect(_stack((slice(100, 130), slice(200, 230))))
    assert region is None  # < min_area_frac — a blinking logo is not a camera


def test_face_outside_region_distrusts_detection():
    # Valid camera region, but the detected face sits in the static corner —
    # the motion mask found the wrong thing.
    region = _detect(
        _stack((slice(40, 230), slice(60, 420))),
        face_box=(1700, 950, 100, 100),
    )
    assert region is None


def test_face_inside_region_keeps_detection():
    region = _detect(
        _stack((slice(40, 230), slice(60, 420))),
        face_box=(900, 500, 120, 120),
    )
    assert region is not None


def test_sampling_failure_returns_none():
    region = _detect(None)
    assert region is None


def test_exception_returns_none():
    with patch("clip_engine.camera_region._sample_gray_frames", side_effect=RuntimeError("boom")):
        assert detect_camera_region(_SRC, 10.0, 40.0, _FRAME_W, _FRAME_H) is None
