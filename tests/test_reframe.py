"""
Unit tests for clip_engine/reframe.py (Issue 189).

All tests use synthetic inputs — no real video files, no ffmpeg, no mediapipe
required. The module is importable without mediapipe installed (lazy import guard).

Coverage targets (80/20 principle):
  - Happy path: multi-face track → EMA smoothed → sendcmd lines correct
  - Geometry: clamp_crop_x boundary conditions
  - EMA math: α=1.0 (no smoothing), α=0.0 (frozen), convergence
  - Pan clamp: inter-frame delta is bounded
  - Sendcmd formatting: timestamps are clip-relative, x is clamped
  - Fallback: build_crop_center_track when cv2/mediapipe unavailable
  - compute_reframe_crop: single-sample returns empty script; multi-sample returns script
  - ValueError on invalid range
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

try:
    import cv2  # noqa: F401

    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False

from clip_engine.reframe import (
    _EMA_ALPHA,
    CropCenterPoint,
    build_crop_center_track,
    build_sendcmd_script,
    clamp_crop_x,
    compute_reframe_crop,
    smooth_crop_track,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_track(*centers: int, start_s: float = 0.0, fps: float = 5.0) -> list[CropCenterPoint]:
    """Build a synthetic raw track with evenly spaced timestamps."""
    interval = 1.0 / fps
    return [CropCenterPoint(start_s + i * interval, cx) for i, cx in enumerate(centers)]


def _frames_iter(frame: object):
    """Fake for the _iter_sampled_frames seam: every timestamp yields ``frame``.

    (Issue 420 migrated the per-sample ``_read_frame_cv2`` patches here — the
    module now decodes through ONE cv2.VideoCapture.)
    """

    def _gen(video_path, timestamps):
        for ts in timestamps:
            yield (ts, frame)

    return _gen


# ---------------------------------------------------------------------------
# CropCenterPoint
# ---------------------------------------------------------------------------


class TestCropCenterPoint:
    def test_repr_normal(self) -> None:
        p = CropCenterPoint(1.5, 640)
        assert "1.500s" in repr(p)
        assert "640px" in repr(p)
        assert "fallback" not in repr(p)

    def test_repr_fallback(self) -> None:
        p = CropCenterPoint(1.5, 640, is_fallback=True)
        assert "fallback" in repr(p)

    def test_slots(self) -> None:
        p = CropCenterPoint(0.0, 100)
        with pytest.raises(AttributeError):
            p.nonexistent = 1  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# clamp_crop_x
# ---------------------------------------------------------------------------


class TestClampCropX:
    """Geometry: ensure crop window always stays within the source frame."""

    def test_center_case(self) -> None:
        # Frame 1920px, crop 607px, center at 960 → x_offset = 960 - 303 = 657
        x = clamp_crop_x(960, 607, 1920)
        assert x == 960 - 607 // 2
        assert 0 <= x <= 1920 - 607

    def test_clamp_left_boundary(self) -> None:
        # Face at 0 → x_offset would be negative → clamp to 0
        x = clamp_crop_x(0, 607, 1920)
        assert x == 0

    def test_clamp_right_boundary(self) -> None:
        # Face at far right → clamp so crop fits
        x = clamp_crop_x(1920, 607, 1920)
        assert x == 1920 - 607

    def test_crop_wider_than_frame_clamps_to_zero(self) -> None:
        # crop_w == frame_w → only valid x is 0
        x = clamp_crop_x(500, 1920, 1920)
        assert x == 0

    def test_face_exactly_at_center(self) -> None:
        # Symmetric case: no clamping needed
        crop_w, frame_w = 608, 1920
        cx = frame_w // 2  # 960
        x = clamp_crop_x(cx, crop_w, frame_w)
        # x + crop_w must not exceed frame_w
        assert x + crop_w <= frame_w
        assert x >= 0


# ---------------------------------------------------------------------------
# smooth_crop_track
# ---------------------------------------------------------------------------


class TestSmoothCropTrack:
    def test_empty_returns_empty(self) -> None:
        assert smooth_crop_track([]) == []

    def test_single_point_unchanged(self) -> None:
        track = [CropCenterPoint(0.5, 300)]
        result = smooth_crop_track(track)
        assert len(result) == 1
        assert result[0].center_x == 300
        assert result[0].timestamp_s == 0.5

    def test_ema_alpha_1_is_no_smoothing(self) -> None:
        """α=1.0: smoothed value equals raw value at every step."""
        raw = _make_track(100, 200, 300, 900)
        smoothed = smooth_crop_track(raw, ema_alpha=1.0, max_pan_px_per_s=1e9)
        for orig, sm in zip(raw, smoothed, strict=True):
            assert sm.center_x == orig.center_x

    def test_ema_alpha_0_is_frozen(self) -> None:
        """α=0.0: smoothed value is always the initial value (frozen)."""
        raw = _make_track(500, 200, 300)
        smoothed = smooth_crop_track(raw, ema_alpha=0.0, max_pan_px_per_s=1e9)
        # Every smoothed value converges to the first raw value
        for sm in smoothed:
            assert sm.center_x == 500

    def test_ema_converges_toward_target(self) -> None:
        """After enough steps with constant input, EMA should approach that value."""
        # 20 points at 200, starting from 500.
        raw = [CropCenterPoint(float(i) * 0.2, 200) for i in range(20)]
        # Seed with high initial value by using first point = 500.
        raw[0] = CropCenterPoint(0.0, 500)
        smoothed = smooth_crop_track(raw, ema_alpha=_EMA_ALPHA, max_pan_px_per_s=1e9)
        # After 20 steps, smoothed value should be much closer to 200 than 500.
        final_cx = smoothed[-1].center_x
        assert abs(final_cx - 200) < abs(500 - 200)

    def test_pan_clamp_limits_delta(self) -> None:
        """Pan speed must not exceed max_pan_px_per_s."""
        # Two points 0.2s apart; raw delta = 900px; max = 300 * 0.2 = 60px.
        interval = 0.2  # seconds
        raw = [
            CropCenterPoint(0.0, 100),
            CropCenterPoint(interval, 1000),  # +900px in 0.2s = 4500 px/s
        ]
        max_pan = 300.0  # px/s
        smoothed = smooth_crop_track(raw, ema_alpha=1.0, max_pan_px_per_s=max_pan)
        delta = abs(smoothed[1].center_x - smoothed[0].center_x)
        assert delta <= max_pan * interval + 1  # +1 for rounding

    def test_pan_clamp_direction_preserved(self) -> None:
        """Pan clamp must preserve the direction of movement."""
        raw = [
            CropCenterPoint(0.0, 100),
            CropCenterPoint(0.2, 1000),  # moves right
        ]
        smoothed = smooth_crop_track(raw, ema_alpha=1.0, max_pan_px_per_s=300.0)
        assert smoothed[1].center_x > smoothed[0].center_x  # direction preserved

    def test_fallback_flag_preserved(self) -> None:
        """is_fallback must be preserved through smoothing."""
        raw = [CropCenterPoint(0.0, 960, is_fallback=True)]
        smoothed = smooth_crop_track(raw)
        assert smoothed[0].is_fallback is True

    def test_preserves_timestamps(self) -> None:
        raw = _make_track(100, 200, 300, start_s=5.0, fps=2.0)
        smoothed = smooth_crop_track(raw)
        assert len(smoothed) == len(raw)
        for orig, sm in zip(raw, smoothed, strict=True):
            assert sm.timestamp_s == pytest.approx(orig.timestamp_s)


# ---------------------------------------------------------------------------
# build_sendcmd_script
# ---------------------------------------------------------------------------


class TestBuildSendcmdScript:
    """Verify the sendcmd script text is correctly formatted and clip-relative."""

    def test_timestamps_are_clip_relative(self) -> None:
        # Source timestamps start at 10.0s; clip starts at 10.0s.
        # sendcmd timestamps must start at 0.0.
        track = [
            CropCenterPoint(10.0, 600),
            CropCenterPoint(10.2, 650),
        ]
        script = build_sendcmd_script(track, crop_w=607, frame_w=1920, start_s=10.0)
        lines = [ln for ln in script.splitlines() if ln.strip()]
        assert lines[0].startswith("0.000")
        assert lines[1].startswith("0.200")

    def test_x_is_clamped(self) -> None:
        # Face at x=1 with crop_w=607 → x_offset must be 0, not negative.
        track = [CropCenterPoint(0.0, 1)]
        script = build_sendcmd_script(track, crop_w=607, frame_w=1920, start_s=0.0)
        assert " crop x 0;" in script

    def test_format_has_enter_directive(self) -> None:
        track = [CropCenterPoint(0.0, 960), CropCenterPoint(0.2, 980)]
        script = build_sendcmd_script(track, crop_w=607, frame_w=1920, start_s=0.0)
        for line in script.splitlines():
            assert "[enter]" in line
            assert "crop x" in line
            assert line.strip().endswith(";")

    def test_negative_clip_relative_timestamps_clamped_to_zero(self) -> None:
        # If start_s > first timestamp_s (shouldn't happen normally, defensive).
        track = [CropCenterPoint(9.9, 600)]
        script = build_sendcmd_script(track, crop_w=607, frame_w=1920, start_s=10.0)
        # Timestamp would be 9.9 - 10.0 = -0.1 → clamped to 0.000
        assert script.startswith("0.000")

    def test_multiple_points_produce_multiple_lines(self) -> None:
        n = 5
        track = [CropCenterPoint(float(i) * 0.2, 900 + i * 10) for i in range(n)]
        script = build_sendcmd_script(track, crop_w=607, frame_w=1920, start_s=0.0)
        assert len(script.splitlines()) == n


# ---------------------------------------------------------------------------
# build_crop_center_track (with mocked cv2 / mediapipe)
# ---------------------------------------------------------------------------


class TestBuildCropCenterTrack:
    def test_invalid_range_raises(self, tmp_path: Path) -> None:
        fake = tmp_path / "v.mp4"
        fake.touch()
        with pytest.raises(ValueError, match="invalid range"):
            build_crop_center_track(fake, start_s=10.0, end_s=5.0, frame_width=1920)

    def test_cv2_unavailable_gives_fallback_track(self, tmp_path: Path) -> None:
        """When cv2 is not importable, every point should be the frame-center fallback."""
        fake = tmp_path / "v.mp4"
        fake.touch()
        with patch("clip_engine.reframe._iter_sampled_frames", _frames_iter(None)):
            track = build_crop_center_track(
                fake, start_s=0.0, end_s=1.0, frame_width=1920, sample_fps=2.0
            )
        assert len(track) >= 1
        for p in track:
            assert p.is_fallback is True
            assert p.center_x == 960  # 1920 // 2

    def test_face_detected_uses_face_center(self, tmp_path: Path) -> None:
        """When mediapipe detects a face, its center_x should appear in the track."""
        import numpy as np

        fake = tmp_path / "v.mp4"
        fake.touch()
        fake_frame = np.zeros((1080, 1920, 3), dtype="uint8")

        # Patch both frame extraction and mediapipe detection.
        with (
            patch("clip_engine.reframe._iter_sampled_frames", _frames_iter(fake_frame)),
            patch(
                "clip_engine.reframe._detect_faces_mediapipe",
                return_value=[800],  # face center_x = 800
            ),
        ):
            track = build_crop_center_track(
                fake, start_s=0.0, end_s=0.5, frame_width=1920, sample_fps=2.0
            )

        assert any(p.center_x == 800 for p in track)
        assert not any(p.is_fallback for p in track)

    def test_no_detection_uses_center_fallback(self, tmp_path: Path) -> None:
        """When no face is detected (empty list), each point is the frame-center fallback."""
        import numpy as np

        fake = tmp_path / "v.mp4"
        fake.touch()
        fake_frame = np.zeros((1080, 1920, 3), dtype="uint8")

        with (
            patch("clip_engine.reframe._iter_sampled_frames", _frames_iter(fake_frame)),
            patch("clip_engine.reframe._detect_faces_mediapipe", return_value=[]),
        ):
            track = build_crop_center_track(
                fake, start_s=0.0, end_s=0.4, frame_width=1920, sample_fps=5.0
            )

        assert len(track) >= 1
        for p in track:
            assert p.is_fallback is True
            assert p.center_x == 960

    def test_track_is_chronologically_ordered(self, tmp_path: Path) -> None:
        import numpy as np

        fake = tmp_path / "v.mp4"
        fake.touch()
        fake_frame = np.zeros((1080, 1920, 3), dtype="uint8")

        with (
            patch("clip_engine.reframe._iter_sampled_frames", _frames_iter(fake_frame)),
            patch("clip_engine.reframe._detect_faces_mediapipe", return_value=[600]),
        ):
            track = build_crop_center_track(
                fake, start_s=5.0, end_s=10.0, frame_width=1920, sample_fps=5.0
            )

        timestamps = [p.timestamp_s for p in track]
        assert timestamps == sorted(timestamps)

    def test_exception_in_mediapipe_gives_fallback(self, tmp_path: Path) -> None:
        """A crash inside _detect_faces_mediapipe must not propagate — fallback instead."""
        import numpy as np

        fake = tmp_path / "v.mp4"
        fake.touch()
        fake_frame = np.zeros((1080, 1920, 3), dtype="uint8")

        with (
            patch("clip_engine.reframe._iter_sampled_frames", _frames_iter(fake_frame)),
            patch(
                "clip_engine.reframe._detect_faces_mediapipe",
                side_effect=RuntimeError("mediapipe crashed"),
            ),
        ):
            # _detect_faces_mediapipe wraps in try/except and returns [] on exception;
            # build_crop_center_track sees [] → fallback.
            track = build_crop_center_track(
                fake, start_s=0.0, end_s=0.4, frame_width=1920, sample_fps=5.0
            )
        assert all(p.is_fallback for p in track)


# ---------------------------------------------------------------------------
# compute_reframe_crop (integration of the three stages)
# ---------------------------------------------------------------------------


class TestComputeReframeCrop:
    """End-to-end tests for the high-level entry point used by render.py."""

    def _run(
        self,
        tmp_path: Path,
        *,
        detected_centers: list[int] | None = None,
        duration: float = 2.0,
        sample_fps: float = 5.0,
    ) -> tuple[list[CropCenterPoint], str]:
        import numpy as np

        from clip_engine.speaker_map import FaceObs

        fake = tmp_path / "v.mp4"
        fake.touch()
        fake_frame = np.zeros((1080, 1920, 3), dtype="uint8")
        centers = detected_centers if detected_centers is not None else [800]

        def _fake_obs(frame, detector, ts):
            return [FaceObs(t=ts, cx=float(c), cy=500.0, w=100.0, h=100.0) for c in centers]

        # compute_reframe_crop delegates to compute_dynamic_crop (Issue 420):
        # the detection seam is _detect_face_obs and shot detection is mocked
        # to keep the test hermetic (no ffmpeg subprocess).
        with (
            patch("clip_engine.reframe._iter_sampled_frames", _frames_iter(fake_frame)),
            patch("clip_engine.reframe._detect_face_obs", side_effect=_fake_obs),
            patch("clip_engine.reframe._shots.detect_shot_changes", return_value=[]),
            patch("clip_engine.reframe._downscale_for_hist", return_value=None),
        ):
            return compute_reframe_crop(
                source_path=fake,
                start_s=10.0,
                end_s=10.0 + duration,
                frame_width=1920,
                frame_height=1080,
                crop_w=607,
                sample_fps=sample_fps,
            )

    def test_returns_smoothed_track_and_script_for_multi_point(self, tmp_path: Path) -> None:
        track, script = self._run(tmp_path, duration=2.0, sample_fps=5.0)
        # 2s * 5fps = 10 points → multi-point → script non-empty
        assert len(track) > 1
        assert len(script) > 0

    def test_script_contains_sendcmd_lines(self, tmp_path: Path) -> None:
        _, script = self._run(tmp_path, duration=1.0, sample_fps=5.0)
        assert "[enter]" in script
        assert "crop x" in script

    def test_single_sample_returns_empty_script(self, tmp_path: Path) -> None:
        # duration=0.19s at 5fps → only 1 sample (0 * 0.2 = 0.0s is the only ts < 0.19)
        track, script = self._run(tmp_path, duration=0.19, sample_fps=5.0)
        assert len(track) == 1
        assert script == ""

    def test_total_failure_returns_center_fallback(self, tmp_path: Path) -> None:
        """When the detection pass raises, the fallback is the frame center."""
        fake = tmp_path / "v.mp4"
        fake.touch()
        with patch(
            "clip_engine.reframe._iter_sampled_frames",
            side_effect=RuntimeError("disk error"),
        ):
            track, script = compute_reframe_crop(
                source_path=fake,
                start_s=0.0,
                end_s=5.0,
                frame_width=1920,
                frame_height=1080,
                crop_w=607,
            )
        assert len(track) == 1
        assert track[0].is_fallback is True
        assert track[0].center_x == 960  # 1920 // 2
        assert script == ""

    def test_no_detection_gives_center_fallback_track(self, tmp_path: Path) -> None:
        track, _ = self._run(tmp_path, detected_centers=[])  # no faces detected
        assert all(p.is_fallback for p in track)
        assert all(p.center_x == 960 for p in track)

    def test_x_in_script_is_within_frame(self, tmp_path: Path) -> None:
        """Every crop x value in the sendcmd script must be within [0, frame_w - crop_w]."""
        # Face at far right edge — should be clamped.
        track, script = self._run(tmp_path, detected_centers=[1919], duration=2.0)
        for line in script.splitlines():
            # Line format: "0.200 [enter] crop x 1313;"
            parts = line.strip().rstrip(";").split()
            x_val = int(parts[-1])
            assert 0 <= x_val <= 1920 - 607


# ---------------------------------------------------------------------------
# Module-level import guard (mediapipe absence)
# ---------------------------------------------------------------------------


class TestLazyImportGuard:
    """The module must be importable without mediapipe installed."""

    def test_module_importable_without_mediapipe(self) -> None:
        """Importing clip_engine.reframe with mediapipe absent must not raise."""
        import importlib

        # Remove mediapipe from sys.modules to simulate it not being installed.
        mediapipe_backup = sys.modules.pop("mediapipe", None)
        try:
            # Re-import the module; it should not blow up.
            import clip_engine.reframe as reframe_mod

            importlib.reload(reframe_mod)
        finally:
            if mediapipe_backup is not None:
                sys.modules["mediapipe"] = mediapipe_backup

    def test_detect_faces_mediapipe_returns_empty_without_mediapipe(self) -> None:
        """_detect_faces_mediapipe must return [] when mediapipe is not installed."""

        import numpy as np

        from clip_engine.reframe import _detect_faces_mediapipe

        mediapipe_backup = sys.modules.pop("mediapipe", None)
        try:
            result = _detect_faces_mediapipe(np.zeros((1080, 1920, 3), dtype="uint8"), 1920)
            assert result == []
        finally:
            if mediapipe_backup is not None:
                sys.modules["mediapipe"] = mediapipe_backup


# ---------------------------------------------------------------------------
# Issue 329: edge-suite additions
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _CV2_AVAILABLE, reason="cv2/libGL not available on this host")
class TestIterSampledFramesNaNFps:
    """_iter_sampled_frames: NaN fps must not propagate to int() and crash.

    (Ported from the old _read_frame_cv2 tests when Issue 420 replaced the
    per-sample open+seek with one sequential capture — same guard, new home.)
    """

    def _read_with_fps(self, tmp_path: Path, fps_value: float) -> list:
        """Patch cv2 so cap.get(CAP_PROP_FPS) returns fps_value."""
        import numpy as np

        from clip_engine.reframe import _iter_sampled_frames

        fake = tmp_path / "v.mp4"
        fake.touch()
        fake_frame = np.zeros((1080, 1920, 3), dtype="uint8")

        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.get.return_value = fps_value
        mock_cap.read.return_value = (True, fake_frame)
        mock_cap.grab.return_value = True

        with patch("cv2.VideoCapture", return_value=mock_cap):
            return list(_iter_sampled_frames(fake, [1.0]))

    def test_fps_zero_falls_back_to_default(self, tmp_path: Path) -> None:
        """fps=0.0 must use the 25.0 default, not divide-by-zero/underflow."""
        results = self._read_with_fps(tmp_path, 0.0)
        assert len(results) == 1  # just no exception, one yield per timestamp

    def test_fps_nan_no_crash(self, tmp_path: Path) -> None:
        """fps=NaN is truthy so a bare `fps or 25.0` would keep NaN, making
        int(ts*NaN) raise. The math.isfinite guard must intercept it."""
        results = self._read_with_fps(tmp_path, float("nan"))
        assert len(results) == 1

    def test_fps_nan_uses_25_default(self, tmp_path: Path) -> None:
        """With NaN fps the seek frame index must be computed with 25.0."""
        import math

        import numpy as np

        from clip_engine.reframe import _iter_sampled_frames

        fake = tmp_path / "v.mp4"
        fake.touch()
        fake_frame = np.zeros((1080, 1920, 3), dtype="uint8")

        set_calls: list[float] = []
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.get.return_value = float("nan")
        mock_cap.read.return_value = (True, fake_frame)
        mock_cap.grab.return_value = True

        def _fake_set(prop, value):
            set_calls.append(value)

        mock_cap.set.side_effect = _fake_set

        with patch("cv2.VideoCapture", return_value=mock_cap):
            list(_iter_sampled_frames(fake, [2.0]))

        # frame_idx = int(2.0 * 25.0) = 50; cap.set receives 50.0
        assert set_calls, "cap.set must have been called"
        frame_idx_arg = set_calls[0]
        assert math.isfinite(frame_idx_arg), f"frame_idx was NaN/inf: {frame_idx_arg}"
        assert frame_idx_arg == pytest.approx(50.0)


@pytest.mark.skipif(not _CV2_AVAILABLE, reason="cv2/libGL not available on this host")
class TestIterSampledFramesSequentialCapture:
    """The Issue-420 mandatory refactor: ONE VideoCapture per clip, ONE seek,
    sequential grab()/retrieve() — no per-sample open+seek."""

    def _run_capture(self, tmp_path: Path, timestamps: list[float]) -> tuple[MagicMock, list]:
        import numpy as np

        from clip_engine.reframe import _iter_sampled_frames

        fake = tmp_path / "v.mp4"
        fake.touch()
        fake_frame = np.zeros((4, 4, 3), dtype="uint8")

        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.get.return_value = 25.0
        mock_cap.read.return_value = (True, fake_frame)
        mock_cap.grab.return_value = True

        with patch("cv2.VideoCapture", return_value=mock_cap) as cap_ctor:
            results = list(_iter_sampled_frames(fake, timestamps))
        return cap_ctor, results

    def test_one_capture_one_seek_for_many_samples(self, tmp_path: Path) -> None:
        cap_ctor, results = self._run_capture(tmp_path, [10.0, 10.2, 10.4, 10.6, 10.8])
        assert cap_ctor.call_count == 1, "must open exactly ONE VideoCapture per clip"
        mock_cap = cap_ctor.return_value
        assert mock_cap.set.call_count == 1, "must seek exactly once (to the first sample)"
        assert mock_cap.release.call_count == 1
        assert len(results) == 5
        assert all(frame is not None for _, frame in results)

    def test_intermediate_frames_skipped_via_grab(self, tmp_path: Path) -> None:
        # 25 fps, samples 0.2s apart → 5 frames apart: read() consumes one,
        # grab() must skip the 4 in between for each subsequent sample.
        cap_ctor, _ = self._run_capture(tmp_path, [10.0, 10.2, 10.4])
        mock_cap = cap_ctor.return_value
        assert mock_cap.grab.call_count == 8  # 2 gaps × 4 skipped frames
        assert mock_cap.read.call_count == 3

    def test_unopenable_source_yields_all_none(self, tmp_path: Path) -> None:
        from clip_engine.reframe import _iter_sampled_frames

        fake = tmp_path / "v.mp4"
        fake.touch()
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = False
        with patch("cv2.VideoCapture", return_value=mock_cap):
            results = list(_iter_sampled_frames(fake, [0.0, 0.2]))
        assert results == [(0.0, None), (0.2, None)]

    def test_eof_mid_track_yields_none_for_rest(self, tmp_path: Path) -> None:
        import numpy as np

        from clip_engine.reframe import _iter_sampled_frames

        fake = tmp_path / "v.mp4"
        fake.touch()
        fake_frame = np.zeros((4, 4, 3), dtype="uint8")
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.get.return_value = 25.0
        # First read succeeds, then EOF.
        mock_cap.read.side_effect = [(True, fake_frame), (False, None)]
        mock_cap.grab.return_value = False  # grab also fails past EOF

        with patch("cv2.VideoCapture", return_value=mock_cap):
            results = list(_iter_sampled_frames(fake, [0.0, 0.2, 0.4]))
        assert results[0][1] is not None
        assert results[1][1] is None
        assert results[2][1] is None


class TestSmoothCropTrackEdges:
    """Pins: non-monotonic timestamps must not crash smooth_crop_track."""

    def test_non_monotonic_timestamps_no_crash(self) -> None:
        """dt <= 0 (duplicate or reversed timestamps) — pan-clamp is skipped but
        EMA still runs; function must not raise."""
        raw = [
            CropCenterPoint(1.0, 500),
            CropCenterPoint(1.0, 700),  # same timestamp → dt=0
            CropCenterPoint(0.8, 300),  # earlier → dt<0
        ]
        result = smooth_crop_track(raw)
        assert len(result) == 3  # every point is returned


class TestBuildCropCenterTrackEdges:
    """Pins: start_s<0 and seek-past-EOF produce fallback points, no raise."""

    def test_negative_start_s_falls_back(self, tmp_path: Path) -> None:
        """start_s=-5 with end_s=5 is a valid duration (10s) but negative timestamps
        → cv2 seek returns None → fallback, no raise."""
        fake = tmp_path / "v.mp4"
        fake.touch()
        with patch("clip_engine.reframe._iter_sampled_frames", _frames_iter(None)):
            track = build_crop_center_track(
                fake, start_s=-5.0, end_s=5.0, frame_width=1920, sample_fps=1.0
            )
        assert len(track) >= 1
        assert all(p.is_fallback for p in track)

    def test_seek_past_eof_falls_back(self, tmp_path: Path) -> None:
        """Timestamps beyond the video length → _read_frame_cv2 returns None
        (cv2 seek-past-EOF) → fallback, no raise."""
        fake = tmp_path / "v.mp4"
        fake.touch()
        with patch("clip_engine.reframe._iter_sampled_frames", _frames_iter(None)):
            track = build_crop_center_track(
                fake, start_s=9000.0, end_s=9005.0, frame_width=1920, sample_fps=1.0
            )
        assert len(track) >= 1
        assert all(p.is_fallback for p in track)


class TestClampCropXWiderThanFrame:
    """Pin: crop_w > frame_w must not produce negative x."""

    def test_crop_wider_than_frame_clamps_to_zero_no_negative(self) -> None:
        # crop_w=2000 > frame_w=1920 → frame_w - crop_w = -80 → must clamp to 0
        x = clamp_crop_x(960, 2000, 1920)
        assert x == 0


# ---------------------------------------------------------------------------
# Issue 352 Batch H: detector hoisted to one instance per track + model path
# ---------------------------------------------------------------------------


class TestDetectorHoisting:
    """The FaceDetector is built ONCE per track, shared across frames, and closed."""

    def test_detector_created_once_shared_and_closed(self, tmp_path: Path) -> None:
        import numpy as np

        fake = tmp_path / "v.mp4"
        fake.touch()
        fake_frame = np.zeros((1080, 1920, 3), dtype="uint8")
        mock_detector = MagicMock()

        with (
            patch("clip_engine.reframe._iter_sampled_frames", _frames_iter(fake_frame)),
            patch(
                "clip_engine.reframe._create_face_detector", return_value=mock_detector
            ) as create_mock,
            patch("clip_engine.reframe._detect_faces_mediapipe", return_value=[800]) as detect_mock,
        ):
            build_crop_center_track(fake, start_s=0.0, end_s=2.0, frame_width=1920, sample_fps=5.0)

        # Hoisted (Issue 352 Batch H): one construction per track, not per frame.
        assert create_mock.call_count == 1
        assert detect_mock.call_count >= 2  # multiple sampled frames…
        for call in detect_mock.call_args_list:
            assert call.args[2] is mock_detector  # …all share the same detector
        mock_detector.close.assert_called_once()

    def test_detect_faces_without_detector_returns_empty(self) -> None:
        """detector=None (mediapipe/model unavailable) → [] → center fallback."""
        import numpy as np

        from clip_engine.reframe import _detect_faces_mediapipe

        assert _detect_faces_mediapipe(np.zeros((10, 10, 3), dtype="uint8"), 1920, None) == []


class TestMediapipeModelPath:
    """_mediapipe_model_path must resolve a Tasks-compatible asset — never the
    legacy Solutions .tflite inside the mediapipe package (rejected by the
    Tasks FaceDetector at create_from_options)."""

    def test_configured_path_wins_when_it_exists(self, tmp_path: Path, monkeypatch) -> None:
        from clip_engine.reframe import _mediapipe_model_path
        from config import settings

        model = tmp_path / "blaze_face_short_range.tflite"
        model.write_bytes(b"\x00")
        monkeypatch.setattr(settings, "MEDIAPIPE_FACE_MODEL_PATH", str(model))
        assert _mediapipe_model_path() == str(model)

    def test_configured_path_missing_returns_empty(self, tmp_path: Path, monkeypatch) -> None:
        from clip_engine.reframe import _mediapipe_model_path
        from config import settings

        monkeypatch.setattr(settings, "MEDIAPIPE_FACE_MODEL_PATH", str(tmp_path / "nope.tflite"))
        assert _mediapipe_model_path() == ""

    def test_legacy_solutions_tflite_is_never_returned(self, tmp_path: Path, monkeypatch) -> None:
        """A fake mediapipe package containing ONLY the legacy Solutions asset
        must yield "" (frame-center fallback), not the incompatible .tflite."""
        from clip_engine.reframe import _mediapipe_model_path
        from config import settings

        monkeypatch.setattr(settings, "MEDIAPIPE_FACE_MODEL_PATH", "")
        pkg_root = tmp_path / "mediapipe"
        legacy = pkg_root / "modules" / "face_detection"
        legacy.mkdir(parents=True)
        (legacy / "face_detection_short_range.tflite").write_bytes(b"\x00")
        (pkg_root / "__init__.py").touch()

        fake_mp = MagicMock()
        fake_mp.__file__ = str(pkg_root / "__init__.py")
        monkeypatch.setitem(sys.modules, "mediapipe", fake_mp)
        assert _mediapipe_model_path() == ""
