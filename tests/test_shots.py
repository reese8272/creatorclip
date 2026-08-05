"""Unit tests for clip_engine/shots.py (Issue 419).

The scdet parse is tested against a REAL captured ffmpeg stderr
(tests/fixtures/scdet_stderr.txt — ffmpeg 8.1.2, 3-shot synthetic color video,
cuts at t=2 and t=4). Subprocess and frame decode are mocked everywhere else —
no ffmpeg, cv2, or mediapipe needed at runtime.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from clip_engine.shots import (
    _frame_histogram,
    _histogram_changes,
    _parse_scdet_output,
    detect_shot_changes,
    histogram_shot_changes,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "scdet_stderr.txt"


# ── _parse_scdet_output ──────────────────────────────────────────────────────


class TestParseScdetOutput:
    def test_captured_stderr_fixture(self) -> None:
        """The real ffmpeg 8.1.2 stderr capture parses to exactly [2.0, 4.0]."""
        times = _parse_scdet_output(_FIXTURE.read_text())
        assert times == [2.0, 4.0]

    def test_fractional_times(self) -> None:
        stderr = (
            "[Parsed_scdet_1 @ 0x7f] lavfi.scd.score: 15.625, lavfi.scd.time: 2.002\n"
            "[Parsed_scdet_1 @ 0x7f] lavfi.scd.score: 11.100, lavfi.scd.time: 33.567\n"
        )
        assert _parse_scdet_output(stderr) == [2.002, 33.567]

    def test_empty_and_garbage(self) -> None:
        assert _parse_scdet_output("") == []
        assert _parse_scdet_output("frame= 150 fps=0.0 q=-0.0 Lsize=N/A") == []
        assert _parse_scdet_output("lavfi.scd.time: notanumber") == []

    def test_sorted_and_deduped(self) -> None:
        """Out-of-order + near-duplicate detections (consecutive frames on a
        dissolve) collapse to one cut and come back sorted."""
        stderr = (
            "[x] lavfi.scd.score: 12, lavfi.scd.time: 5.0\n"
            "[x] lavfi.scd.score: 12, lavfi.scd.time: 2.0\n"
            "[x] lavfi.scd.score: 12, lavfi.scd.time: 2.04\n"
        )
        assert _parse_scdet_output(stderr) == [2.0, 5.0]


# ── histogram fallback ───────────────────────────────────────────────────────


def _solid_frame(b: int, g: int, r: int) -> np.ndarray:
    frame = np.zeros((18, 32, 3), dtype=np.uint8)
    frame[:, :] = (b, g, r)
    return frame


class TestHistogramFallback:
    def test_frame_histogram_normalized(self) -> None:
        hist = _frame_histogram(_solid_frame(255, 0, 0))
        assert pytest.approx(float(np.asarray(hist).sum())) == 1.0

    def test_hard_cut_detected_at_later_sample(self) -> None:
        samples = [
            (10.0, _solid_frame(255, 0, 0)),
            (10.2, _solid_frame(255, 0, 0)),
            (10.4, _solid_frame(0, 0, 255)),  # cut lands here
            (10.6, _solid_frame(0, 0, 255)),
        ]
        assert histogram_shot_changes(samples) == [10.4]

    def test_static_scene_detects_nothing(self) -> None:
        samples = [(t, _solid_frame(10, 120, 30)) for t in (0.0, 0.2, 0.4)]
        assert histogram_shot_changes(samples) == []

    def test_none_frames_skipped(self) -> None:
        samples = [
            (0.0, _solid_frame(255, 0, 0)),
            (0.2, None),
            (0.4, _solid_frame(0, 0, 255)),
        ]
        assert histogram_shot_changes(samples) == [0.4]

    def test_fewer_than_two_frames_returns_empty(self) -> None:
        assert histogram_shot_changes([]) == []
        assert histogram_shot_changes([(0.0, _solid_frame(1, 2, 3))]) == []

    def test_never_raises_on_garbage(self) -> None:
        assert histogram_shot_changes([(0.0, "not-a-frame"), (0.2, "junk")]) == []

    def test_histogram_changes_threshold_boundary(self) -> None:
        """A distance exactly AT the threshold is not a change (strict >)."""
        h_a = np.zeros(8, dtype=np.float64)
        h_a[0] = 1.0
        h_b = np.zeros(8, dtype=np.float64)
        h_b[0], h_b[1] = 0.5, 0.5  # L1/2 distance vs h_a = 0.5
        assert _histogram_changes([(0.0, h_a), (0.2, h_b)], diff_threshold=0.5) == []
        assert _histogram_changes([(0.0, h_a), (0.2, h_b)], diff_threshold=0.49) == [0.2]


# ── detect_shot_changes (subprocess orchestration) ───────────────────────────


class TestDetectShotChanges:
    def _run_ok(self, stderr: str, **kwargs) -> list[float]:
        with patch("clip_engine.shots.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr=stderr, stdout="")
            result = detect_shot_changes(Path("/fake/v.mp4"), 100.0, 106.0, **kwargs)
        self.last_cmd = mock_run.call_args.args[0]
        return result

    def test_scdet_times_converted_to_source_relative(self) -> None:
        assert self._run_ok(_FIXTURE.read_text()) == [102.0, 104.0]

    def test_edge_of_window_detections_dropped(self) -> None:
        stderr = (
            "[x] lavfi.scd.score: 12, lavfi.scd.time: 0.0\n"
            "[x] lavfi.scd.score: 12, lavfi.scd.time: 3.0\n"
            "[x] lavfi.scd.score: 12, lavfi.scd.time: 5.99\n"
        )
        assert self._run_ok(stderr) == [103.0]

    def test_successful_zero_cut_pass_does_not_fall_back(self) -> None:
        """scdet succeeding with no detections is a VALID single-shot answer."""
        samples = [(100.0, _solid_frame(255, 0, 0)), (100.2, _solid_frame(0, 0, 255))]
        assert self._run_ok("no detections here", fallback_samples=samples) == []

    def test_command_uses_downscale_and_threshold(self) -> None:
        self._run_ok("", threshold=17.5)
        vf = self.last_cmd[self.last_cmd.index("-vf") + 1]
        assert vf == "scale=320:-2,scdet=threshold=17.5"
        # -ss before -i (fast seek) and null muxer output
        assert self.last_cmd.index("-ss") < self.last_cmd.index("-i")
        assert self.last_cmd[-2:] == ["null", "-"]

    def test_default_threshold_comes_from_settings(self) -> None:
        from config import settings

        self._run_ok("")
        vf = self.last_cmd[self.last_cmd.index("-vf") + 1]
        assert f"threshold={settings.SHOT_DETECT_SCDET_THRESHOLD}" in vf

    def test_subprocess_failure_falls_back_to_histograms(self) -> None:
        samples = [
            (100.0, _solid_frame(255, 0, 0)),
            (100.2, _solid_frame(0, 0, 255)),
        ]
        with patch(
            "clip_engine.shots.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=60),
        ):
            result = detect_shot_changes(
                Path("/fake/v.mp4"), 100.0, 106.0, fallback_samples=samples
            )
        assert result == [100.2]

    def test_nonzero_exit_falls_back(self) -> None:
        samples = [(0.0, _solid_frame(255, 0, 0)), (0.2, _solid_frame(0, 0, 255))]
        with patch("clip_engine.shots.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="boom", stdout="")
            result = detect_shot_changes(Path("/fake/v.mp4"), 0.0, 6.0, fallback_samples=samples)
        assert result == [0.2]

    def test_total_failure_returns_empty(self) -> None:
        """Subprocess dead AND no fallback samples → [] = one shot (safe)."""
        with patch("clip_engine.shots.subprocess.run", side_effect=OSError("no ffmpeg")):
            assert detect_shot_changes(Path("/fake/v.mp4"), 0.0, 6.0) == []

    def test_invalid_window_returns_empty(self) -> None:
        assert detect_shot_changes(Path("/fake/v.mp4"), 10.0, 10.0) == []
        assert detect_shot_changes(Path("/fake/v.mp4"), 10.0, 5.0) == []
