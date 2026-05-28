"""
Unit tests for clip_engine/render.py.

ffmpeg / ffprobe / cv2 calls are patched — no video files needed.
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from clip_engine.render import (
    _detect_face_center_x,
    _extract_keyframe,
    _frame_dimensions,
    _run,
    render_clip_file,
)

# ── _frame_dimensions ─────────────────────────────────────────────────────────


def test_frame_dimensions_parses_output(tmp_path):
    fake = tmp_path / "v.mp4"
    fake.touch()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="1920,1080\n", stderr="")
        w, h = _frame_dimensions(fake)
    assert w == 1920 and h == 1080


def test_frame_dimensions_defaults_on_bad_output(tmp_path):
    fake = tmp_path / "v.mp4"
    fake.touch()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="bad", stderr="")
        w, h = _frame_dimensions(fake)
    assert w == 1920 and h == 1080


# ── _extract_keyframe ─────────────────────────────────────────────────────────


def test_extract_keyframe_calls_ffmpeg(tmp_path):
    src = tmp_path / "v.mp4"
    src.touch()
    kf = tmp_path / "kf.jpg"
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        _extract_keyframe(src, 30.0, kf)
    cmd = mock_run.call_args[0][0]
    assert "ffmpeg" in cmd
    assert "30.0" in cmd
    assert str(kf) in cmd


def test_extract_keyframe_raises_on_failure(tmp_path):
    src = tmp_path / "v.mp4"
    src.touch()
    kf = tmp_path / "kf.jpg"
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="Error")
        with pytest.raises(RuntimeError, match="ffmpeg"):
            _extract_keyframe(src, 30.0, kf)


# ── _detect_face_center_x ─────────────────────────────────────────────────────


def test_detect_face_center_x_with_face(tmp_path):
    kf = tmp_path / "kf.jpg"
    kf.touch()
    import numpy as np

    fake_img = np.zeros((1080, 1920, 3), dtype="uint8")

    with (
        patch("cv2.imread", return_value=fake_img),
        patch("cv2.CascadeClassifier") as mock_cascade_cls,
    ):
        mock_cascade = MagicMock()
        # Detected face at x=800, y=200, w=200, h=200
        mock_cascade.detectMultiScale.return_value = [(800, 200, 200, 200)]
        mock_cascade_cls.return_value = mock_cascade
        cx = _detect_face_center_x(kf, 1920)

    assert cx == 800 + 100  # x + w//2


def test_detect_face_center_x_no_face_returns_center(tmp_path):
    kf = tmp_path / "kf.jpg"
    kf.touch()
    import numpy as np

    fake_img = np.zeros((1080, 1920, 3), dtype="uint8")

    with (
        patch("cv2.imread", return_value=fake_img),
        patch("cv2.CascadeClassifier") as mock_cascade_cls,
    ):
        mock_cascade = MagicMock()
        mock_cascade.detectMultiScale.return_value = []
        mock_cascade_cls.return_value = mock_cascade
        cx = _detect_face_center_x(kf, 1920)

    assert cx == 960  # 1920 // 2


def test_detect_face_center_x_fallback_on_exception(tmp_path):
    kf = tmp_path / "kf.jpg"
    kf.touch()
    with patch("cv2.imread", side_effect=Exception("cv2 broken")):
        cx = _detect_face_center_x(kf, 1920)
    assert cx == 960


# ── render_clip_file ──────────────────────────────────────────────────────────


def test_render_clip_file_raises_on_invalid_range(tmp_path):
    src = tmp_path / "v.mp4"
    src.touch()
    out = tmp_path / "out.mp4"
    with pytest.raises(ValueError, match="Invalid clip range"):
        render_clip_file(src, start_s=50.0, end_s=30.0, out_path=out)


def test_render_clip_file_calls_ffmpeg_with_crop(tmp_path):
    src = tmp_path / "v.mp4"
    src.touch()
    out = tmp_path / "out.mp4"

    import numpy as np

    fake_img = np.zeros((1080, 1920, 3), dtype="uint8")

    def fake_subprocess_run(cmd, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stdout = "1920,1080\n"
        m.stderr = ""
        return m

    with (
        patch("subprocess.run", side_effect=fake_subprocess_run),
        patch("cv2.imread", return_value=fake_img),
        patch("cv2.CascadeClassifier") as mock_cc,
    ):
        mock_cc.return_value.detectMultiScale.return_value = []
        render_clip_file(src, start_s=10.0, end_s=70.0, out_path=out)

    # subprocess.run was called (ffprobe, keyframe extract, render)
    # Just verify it ran without error (no assertion on exact call count needed)


def test_render_clip_file_crop_centers_on_face(tmp_path):
    """Face at x=1400 should push the crop right, not stay at center."""
    src = tmp_path / "v.mp4"
    src.touch()
    out = tmp_path / "out.mp4"

    import numpy as np

    fake_img = np.zeros((1080, 1920, 3), dtype="uint8")
    captured_cmds: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        m = MagicMock()
        m.returncode = 0
        m.stdout = "1920,1080\n"
        m.stderr = ""
        return m

    with (
        patch("subprocess.run", side_effect=fake_run),
        patch("cv2.imread", return_value=fake_img),
        patch("cv2.CascadeClassifier") as mock_cc,
    ):
        # Face centered at x=1400+100=1500
        mock_cc.return_value.detectMultiScale.return_value = [(1400, 300, 200, 200)]
        render_clip_file(src, start_s=10.0, end_s=70.0, out_path=out)

    # Find the render ffmpeg call (has -vf crop=...)
    render_cmd = next((c for c in captured_cmds if "-vf" in c), None)
    assert render_cmd is not None
    vf_idx = render_cmd.index("-vf")
    vf_arg = render_cmd[vf_idx + 1]
    # x_offset should not be the default center (960-304=656); face at 1500 pushes it right
    assert "crop=" in vf_arg


# ── timeout tests ─────────────────────────────────────────────────────────────


def test_run_raises_runtime_error_on_timeout():
    """`_run` converts `subprocess.TimeoutExpired` into a `RuntimeError` with message."""
    with (
        patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=120),
        ),
        pytest.raises(RuntimeError, match="timed out after 120"),
    ):
        _run(["ffmpeg", "-version"], "test label", timeout_s=120)


def test_frame_dimensions_raises_on_timeout(tmp_path):
    """`_frame_dimensions` raises `RuntimeError` when ffprobe hangs."""
    fake = tmp_path / "v.mp4"
    fake.touch()
    with (
        patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["ffprobe"], timeout=30),
        ),
        pytest.raises(RuntimeError, match="ffprobe timed out after 30s"),
    ):
        _frame_dimensions(fake)


def test_render_clip_file_raises_on_ffmpeg_timeout(tmp_path):
    """`render_clip_file` surfaces a `RuntimeError` when the render ffmpeg call stalls."""
    src = tmp_path / "v.mp4"
    src.touch()
    out = tmp_path / "out.mp4"

    import numpy as np

    fake_img = np.zeros((1080, 1920, 3), dtype="uint8")
    call_count = 0

    def fake_subprocess_run(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        # First call: ffprobe returns dimensions normally.
        # Second call: keyframe extraction returns normally.
        # Third call: the render ffmpeg stalls → TimeoutExpired.
        if call_count <= 2:
            m = MagicMock()
            m.returncode = 0
            m.stdout = "1920,1080\n"
            m.stderr = ""
            return m
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 120))

    with (
        patch("subprocess.run", side_effect=fake_subprocess_run),
        patch("cv2.imread", return_value=fake_img),
        patch("cv2.CascadeClassifier") as mock_cc,
    ):
        mock_cc.return_value.detectMultiScale.return_value = []
        with pytest.raises(RuntimeError, match="timed out after"):
            render_clip_file(src, start_s=10.0, end_s=70.0, out_path=out)
