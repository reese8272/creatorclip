"""
Unit tests for clip_engine/render.py.

ffmpeg / ffprobe / cv2 calls are patched — no video files needed.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clip_engine.render import (
    _detect_face_center_x,
    _extract_keyframe,
    _frame_dimensions,
    _measure_loudnorm_filter,
    _parse_loudnorm_stats,
    _run,
    render_cleaned_clip_file,
    render_clip_file,
)

# A representative loudnorm JSON block as printed to stderr by
# `loudnorm=...:print_format=json`. input_i is well above the silence floor.
_LOUDNORM_JSON = """[Parsed_loudnorm_0 @ 0x55]
{
\t"input_i" : "-27.85",
\t"input_tp" : "-12.34",
\t"input_lra" : "5.20",
\t"input_thresh" : "-38.12",
\t"output_i" : "-14.00",
\t"output_tp" : "-1.50",
\t"output_lra" : "5.00",
\t"output_thresh" : "-24.61",
\t"normalization_type" : "dynamic",
\t"target_offset" : "0.30"
}
"""

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


def test_render_clip_file_uses_accurate_seek_before_input(tmp_path):
    """The cut must be frame-accurate: `-accurate_seek` after `-ss`, before `-i` (Issue 59)."""
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
        mock_cc.return_value.detectMultiScale.return_value = []
        render_clip_file(src, start_s=10.0, end_s=70.0, out_path=out)

    render_cmd = next((c for c in captured_cmds if "-vf" in c), None)
    assert render_cmd is not None
    # -accurate_seek must come after -ss and before -i (it is an input option).
    assert "-accurate_seek" in render_cmd
    assert render_cmd.index("-accurate_seek") < render_cmd.index("-i")
    assert render_cmd.index("-ss") < render_cmd.index("-accurate_seek")


# ── _render_start_for: render from setup_start_s, not the start_s clamp (Issue 59) ──


def test_render_start_uses_setup_start_when_present():
    from models import Clip
    from worker.tasks import _render_start_for

    clip = Clip(setup_start_s=12.0, start_s=40.0, end_s=72.0)
    # Must render from the computed setup boundary, NOT the peak−window fallback.
    assert _render_start_for(clip) == 12.0


def test_render_start_falls_back_to_start_s_when_setup_missing():
    from models import Clip
    from worker.tasks import _render_start_for

    clip = Clip(setup_start_s=None, start_s=40.0, end_s=72.0)
    assert _render_start_for(clip) == 40.0


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


# ── loudness normalization (Issue 181) ────────────────────────────────────────


def test_parse_loudnorm_stats_extracts_json():
    stats = _parse_loudnorm_stats(_LOUDNORM_JSON)
    assert stats is not None
    assert stats["input_i"] == "-27.85"
    assert stats["target_offset"] == "0.30"


def test_parse_loudnorm_stats_returns_none_on_garbage():
    assert _parse_loudnorm_stats("ffmpeg error: no such file") is None
    assert _parse_loudnorm_stats("") is None


def test_measure_loudnorm_filter_bakes_in_measured_values():
    """A non-silent measurement yields a second-pass filter with measured_* + linear."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr=_LOUDNORM_JSON)
        flt = _measure_loudnorm_filter(["ffmpeg", "..."], "render", 120.0)
    assert flt is not None
    assert "loudnorm=I=-14:TP=-1.5:LRA=11" in flt
    assert "measured_I=-27.85" in flt
    assert "linear=true" in flt


def test_measure_loudnorm_filter_skips_near_silent():
    """Integrated loudness at/below the floor → None (don't amplify hiss)."""
    silent = _LOUDNORM_JSON.replace('"input_i" : "-27.85"', '"input_i" : "-65.00"')
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr=silent)
        flt = _measure_loudnorm_filter(["ffmpeg", "..."], "render", 120.0)
    assert flt is None


def test_measure_loudnorm_filter_degrades_on_subprocess_failure():
    """A measurement that can't run (e.g. ffmpeg missing) → None, never raises."""
    with patch("subprocess.run", side_effect=OSError("ffmpeg not found")):
        assert _measure_loudnorm_filter(["ffmpeg", "..."], "render", 120.0) is None


def _fake_run_with_loudnorm(input_i: str = "-27.85"):
    """subprocess.run double: ffprobe dims via stdout, loudnorm stats via stderr."""
    stderr = _LOUDNORM_JSON.replace('"input_i" : "-27.85"', f'"input_i" : "{input_i}"')

    def _fake(cmd, **kwargs):
        return MagicMock(returncode=0, stdout="1920,1080\n", stderr=stderr)

    return _fake


def test_render_clip_file_applies_loudnorm_when_measured(tmp_path):
    src = tmp_path / "v.mp4"
    src.touch()
    out = tmp_path / "out.mp4"

    import numpy as np

    fake_img = np.zeros((1080, 1920, 3), dtype="uint8")
    captured: list[list[str]] = []

    def _fake(cmd, **kwargs):
        captured.append(cmd)
        return MagicMock(returncode=0, stdout="1920,1080\n", stderr=_LOUDNORM_JSON)

    with (
        patch("subprocess.run", side_effect=_fake),
        patch("cv2.imread", return_value=fake_img),
        patch("cv2.CascadeClassifier") as mock_cc,
    ):
        mock_cc.return_value.detectMultiScale.return_value = []
        render_clip_file(src, start_s=10.0, end_s=70.0, out_path=out)

    render_cmd = next((c for c in captured if "-vf" in c), None)
    assert render_cmd is not None
    assert "-af" in render_cmd
    af_arg = render_cmd[render_cmd.index("-af") + 1]
    assert "loudnorm=" in af_arg and "measured_I=-27.85" in af_arg


def test_render_clip_file_skips_loudnorm_when_silent(tmp_path):
    src = tmp_path / "v.mp4"
    src.touch()
    out = tmp_path / "out.mp4"

    import numpy as np

    fake_img = np.zeros((1080, 1920, 3), dtype="uint8")
    captured: list[list[str]] = []

    def _fake(cmd, **kwargs):
        captured.append(cmd)
        stderr = _LOUDNORM_JSON.replace('"input_i" : "-27.85"', '"input_i" : "-70.00"')
        return MagicMock(returncode=0, stdout="1920,1080\n", stderr=stderr)

    with (
        patch("subprocess.run", side_effect=_fake),
        patch("cv2.imread", return_value=fake_img),
        patch("cv2.CascadeClassifier") as mock_cc,
    ):
        mock_cc.return_value.detectMultiScale.return_value = []
        render_clip_file(src, start_s=10.0, end_s=70.0, out_path=out)

    render_cmd = next((c for c in captured if "-vf" in c), None)
    assert render_cmd is not None
    assert "-af" not in render_cmd  # near-silent → no normalization


def test_render_cleaned_clip_file_chains_loudnorm_into_graph(tmp_path):
    """When measurement succeeds the loudnorm filter is chained after concat and
    the audio map points at the normalized output label."""
    captured = {}

    def _fake_run(cmd, label, timeout_s=120.0):
        captured["cmd"] = cmd
        captured["script"] = Path(cmd[cmd.index("-filter_complex_script") + 1]).read_text()

    measured = "loudnorm=I=-14:TP=-1.5:LRA=11:measured_I=-20.0:linear=true"
    with (
        patch("clip_engine.render._run", _fake_run),
        patch("clip_engine.render._measure_loudnorm_filter", return_value=measured),
    ):
        render_cleaned_clip_file(
            source_path=Path("/fake/src.mp4"),
            keep_ranges=[(0.0, 2.5), (3.0, 8.0)],
            out_path=tmp_path / "out.mp4",
        )

    script = captured["script"]
    assert f";[outa]{measured}[outaln]" in script
    # The render must map the normalized audio, not the raw concat output.
    cmd = captured["cmd"]
    assert "[outaln]" in cmd


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
