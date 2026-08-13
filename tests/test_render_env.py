"""Real-media verification lane (Issue 478) — marker ``render_env``.

Every test here drives REAL ffmpeg/ffprobe binaries over synthetic lavfi
sources through the production ``render_clip_file`` path. Excluded from the
default unit lane via pytest.ini addopts; executed locally with
``pytest -m render_env`` and in CI by the dedicated render-env step, which
hard-installs ffmpeg and fails if fewer than 3 tests collect.

Deliberately NO skipif anywhere: a missing binary must FAIL this lane, never
silently skip it — the empty-lane defect this file exists to close was
invisible for months precisely because absence looked like success.
"""

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.render_env

pytest.importorskip("cv2")  # the render path needs real cv2 for the face pass
import cv2  # noqa: E402
import numpy as np  # noqa: E402

from clip_engine.render import render_clip_file  # noqa: E402

_FRAME_S = 1.0 / 30.0  # source fps is 30 → one-frame tolerance


def _require_binaries() -> None:
    # FIRST assertion of every test (via the fixtures): a missing binary is a
    # hard FAILURE of this lane, never a skip.
    assert shutil.which("ffmpeg"), "ffmpeg binary missing — the render-env lane must fail"
    assert shutil.which("ffprobe"), "ffprobe binary missing — the render-env lane must fail"


def _make_source(path: Path, video_graph: str) -> Path:
    _require_binaries()
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            video_graph,
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=30",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=300,
    )
    return path


@pytest.fixture(scope="session")
def testsrc_source(tmp_path_factory) -> Path:
    """30 s / 30 fps / 1920×1080 animated testsrc2 + 440 Hz sine."""
    return _make_source(
        tmp_path_factory.mktemp("render_env") / "testsrc2.mp4",
        "testsrc2=duration=30:size=1920x1080:rate=30",
    )


@pytest.fixture(scope="session")
def static_source(tmp_path_factory) -> Path:
    """30 s / 30 fps / 1920×1080 STATIC smptebars + 440 Hz sine.

    Static on purpose: off-pulse frames of one render are pixel-identical
    across time, so a punch-in SSIM comparison within THE SAME RENDER is
    meaningful (on an animated source any two timestamps differ trivially).
    """
    return _make_source(
        tmp_path_factory.mktemp("render_env_static") / "smptebars.mp4",
        "smptebars=duration=30:size=1920x1080:rate=30",
    )


def _flags_off():
    from config import settings

    return (
        patch.object(settings, "CAMERA_REGION_DETECT_ENABLED", False),
        patch.object(settings, "ACTIVE_SPEAKER_REFRAME_ENABLED", False),
    )


def _probe_stream(out: Path) -> tuple[int, int, float]:
    """(width, height, VIDEO-STREAM duration) of the first video stream."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,duration",
            "-of",
            "csv=p=0",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    w, h, dur = result.stdout.strip().split(",")
    return int(w), int(h), float(dur)


def _extract_frame(rendered: Path, at_s: float) -> np.ndarray:
    """Grayscale frame of ``rendered`` at ``at_s`` (output-relative seconds)."""
    result = subprocess.run(
        [
            "ffmpeg",
            "-ss",
            str(at_s),
            "-i",
            str(rendered),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        capture_output=True,
        check=True,
        timeout=60,
    )
    frame = np.frombuffer(result.stdout, dtype=np.uint8)
    return frame.reshape(1920, 1080)  # 9:16 output: height 1920, width 1080


def _ssim(a: np.ndarray, b: np.ndarray) -> float:
    """Mean SSIM (Wang et al.) over an 11×11 Gaussian window, σ=1.5."""
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    kernel, sigma = (11, 11), 1.5
    mu_a = cv2.GaussianBlur(a, kernel, sigma)
    mu_b = cv2.GaussianBlur(b, kernel, sigma)
    var_a = cv2.GaussianBlur(a * a, kernel, sigma) - mu_a * mu_a
    var_b = cv2.GaussianBlur(b * b, kernel, sigma) - mu_b * mu_b
    cov = cv2.GaussianBlur(a * b, kernel, sigma) - mu_a * mu_b
    num = (2 * mu_a * mu_b + c1) * (2 * cov + c2)
    den = (mu_a * mu_a + mu_b * mu_b + c1) * (var_a + var_b + c2)
    return float((num / den).mean())


def test_cut_boundary_and_geometry(testsrc_source, tmp_path):
    """render_clip_file(3.0→13.0) through REAL ffmpeg: the video stream must be
    10.0 s long within ±1 frame, at 1080×1920. No argv mock has ever verified
    the actual seek/cut — this is the assertion that catches an off-by-N in
    ``-ss``/``-t`` handling (Issue 478)."""
    _require_binaries()
    out = tmp_path / "cut.mp4"
    region_off, reframe_off = _flags_off()
    with region_off, reframe_off:
        render_clip_file(testsrc_source, start_s=3.0, end_s=13.0, out_path=out)

    assert out.exists() and out.stat().st_size > 0
    width, height, stream_dur = _probe_stream(out)
    assert (width, height) == (1080, 1920)
    assert abs(stream_dur - 10.0) <= _FRAME_S


def test_punch_in_executes_and_zooms(static_source, tmp_path):
    """zoom_on_peak with the peak inside the window must render rc=0 (the old
    crop-w/h chain failed rc=234 on EVERY such render — Issue 467) and must
    visibly zoom: on a static source, frames of the SAME render are
    pixel-identical off-pulse, so the peak frame differing sharply from an
    off-pulse frame is the zoom itself."""
    _require_binaries()
    out = tmp_path / "punch.mp4"
    region_off, reframe_off = _flags_off()
    with region_off, reframe_off:
        # peak_s=8.0 → clip-relative 5.0; pulse spans ±0.6 s of it.
        render_clip_file(
            static_source,
            start_s=3.0,
            end_s=13.0,
            out_path=out,
            style_preset={"zoom_on_peak": True},
            peak_s=8.0,
        )

    width, height, _ = _probe_stream(out)
    assert (width, height) == (1080, 1920)

    off_a = _extract_frame(out, 1.0)  # well outside the pulse
    off_b = _extract_frame(out, 2.0)  # ditto — control pair
    peak = _extract_frame(out, 5.0)  # pulse center: 1.08× zoom

    control = _ssim(off_a, off_b)
    zoomed = _ssim(peak, off_a)
    assert control > 0.98, f"static control frames should match (SSIM={control:.4f})"
    assert zoomed < control - 0.05, (
        f"peak frame should differ sharply from off-pulse (zoom absent? "
        f"SSIM peak-vs-off={zoomed:.4f}, control={control:.4f})"
    )


def test_sendcmd_reframe_chain_parses_and_inits(testsrc_source, tmp_path):
    """The sendcmd + crop@spk chain must parse and initialize through REAL
    ffmpeg (Issue 467's failure class was invisible to every argv-string
    test). compute_dynamic_crop is pinned to a fixed 2-point track so the
    chain — not the tracker — is what's under test."""
    _require_binaries()
    from clip_engine.reframe import CropCenterPoint, ReframeResult
    from config import settings

    out = tmp_path / "sendcmd.mp4"
    fixed = ReframeResult(
        track_points=[CropCenterPoint(0.0, 500), CropCenterPoint(2.0, 700)],
        sendcmd_text=("0.000 [enter] crop@spk x 197;\n2.000 [enter] crop@spk x 397;"),
        mode="face_pan",
        track_json={"version": 1},
    )
    with (
        patch.object(settings, "CAMERA_REGION_DETECT_ENABLED", False),
        patch.object(settings, "ACTIVE_SPEAKER_REFRAME_ENABLED", True),
        patch("clip_engine.reframe.compute_dynamic_crop", return_value=fixed),
    ):
        track = render_clip_file(testsrc_source, start_s=3.0, end_s=13.0, out_path=out)

    assert out.exists() and out.stat().st_size > 0
    width, height, stream_dur = _probe_stream(out)
    assert (width, height) == (1080, 1920)
    assert abs(stream_dur - 10.0) <= _FRAME_S
    assert track == {"version": 1}  # the reframe track is returned to the caller
