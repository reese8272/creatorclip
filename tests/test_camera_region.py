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


def _detect(stack, **kwargs):
    with patch("clip_engine.camera_region._sample_gray_frames", return_value=stack):
        return detect_camera_region(_SRC, 10.0, 40.0, _FRAME_W, _FRAME_H, **kwargs)


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
