"""Unit tests for clip_engine/camera_region.py (Issue 430).

Pure numpy/cv2 — the ffmpeg frame-sampling seam is patched with synthetic
grayscale stacks. cv2 is required (same guard as test_render.py).
"""

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

pytest.importorskip("cv2")

from clip_engine import camera_region as cr
from clip_engine.camera_region import detect_camera_region, detect_video_camera_region

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


# ── frame sampling strategy ──────────────────────────────────────────────────
#
# These exercise _sample_gray_frames ITSELF. Every other test in this file
# patches it out, which is exactly why the first live backfill hit a defect the
# suite could not see: a whole-video call built ONE ffmpeg pass with
# `fps=0.0148`, forcing a linear decode of all 1617 seconds, and timed out.


def _sampling_commands(start_s: float, end_s: float, frames: int = 24) -> list[list[str]]:
    """Capture the ffmpeg command(s) _sample_gray_frames issues for a span."""
    from clip_engine.camera_region import _sample_gray_frames

    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        from unittest.mock import MagicMock

        return MagicMock(returncode=1, stdout="", stderr="")

    with patch("clip_engine.camera_region.subprocess.run", side_effect=_fake_run):
        _sample_gray_frames(_SRC, start_s, end_s, frames, 60.0)
    return calls


def test_short_clip_window_still_uses_one_linear_decode_pass():
    """The per-clip path is frame-verified in production — it must not change."""
    calls = _sampling_commands(10.0, 70.0)
    assert len(calls) == 1
    vf = calls[0][calls[0].index("-vf") + 1]
    assert "fps=" in vf


def test_whole_video_span_seeks_per_frame_instead_of_decoding_linearly():
    """Issue 439: a 27-minute span must not be decoded end to end.

    One pass with an `fps` filter costs O(span); 24 input seeks cost O(1) each.
    This is the defect the first live backfill surfaced — the suite could not
    have caught it while every test patched the sampler out.
    """
    calls = _sampling_commands(0.0, 1617.2)
    assert len(calls) == 24, f"expected one seek per frame, got {len(calls)} call(s)"
    for cmd in calls:
        joined = " ".join(cmd)
        assert "fps=" not in joined, "long spans must not use the linear-decode filter"
        assert cmd.index("-ss") < cmd.index("-i"), "-ss must precede -i (input seeking)"
        assert cmd[cmd.index("-frames:v") + 1] == "1"
    # Seek timestamps must actually spread across the whole runtime.
    stamps = [float(c[c.index("-ss") + 1]) for c in calls]
    assert stamps == sorted(stamps)
    assert stamps[0] < 60.0 and stamps[-1] > 1500.0


def test_seek_sampling_survives_individual_frame_failures():
    """One unreadable sample must not lose the pass; the <3-frame guard still
    rejects a stack too sparse to measure variance against."""
    from clip_engine.camera_region import _sample_by_seeking

    seen: list[list[str]] = []

    def _flaky(cmd, **kwargs):
        seen.append(cmd)
        from unittest.mock import MagicMock

        return MagicMock(returncode=1 if len(seen) % 2 else 0, stdout="", stderr="")

    with (
        patch("clip_engine.camera_region.subprocess.run", side_effect=_flaky),
        patch("pathlib.Path.exists", return_value=True),
    ):
        captured, scanned_until_s = _sample_by_seeking(_SRC, 0.0, 1617.2, 8, 60.0, "/tmp")
    assert len(seen) == 8, "a failed frame must not abort the remaining samples"
    # Issue 466 contract: the captured (timestamp, path) list IS the ordering
    # authority, and a completed loop reports the full span as scanned.
    assert len(captured) == 4
    stamps = [t for t, _ in captured]
    assert stamps == sorted(stamps)
    assert scanned_until_s == pytest.approx(1617.2)


def test_seek_sampled_stack_is_read_back_in_temporal_order_past_999_samples():
    """Issue 466 defect 1: >999 samples must come back in TEMPORAL order.

    Repro of the live failure: frames were rediscovered with a lexicographic
    glob, so `f1000` sorted before `f101` and the stack's timeline scrambled.
    Each fake ffmpeg call writes a 1x1 PNG whose pixel value encodes the
    capture index, so ordering is asserted end to end through the real
    read-back path.
    """
    import cv2

    from clip_engine.camera_region import _sample_gray_frames

    written = [0]

    def _fake_run(cmd, **kwargs):
        from unittest.mock import MagicMock

        idx = written[0]
        written[0] += 1
        cv2.imwrite(cmd[-1], np.full((1, 1), idx % 256, np.uint8))
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("clip_engine.camera_region.subprocess.run", side_effect=_fake_run):
        stack = _sample_gray_frames(_SRC, 0.0, 1200.0, 1050, 3600.0)

    assert stack is not None and len(stack) == 1050
    assert [int(f[0, 0]) for f in stack] == [i % 256 for i in range(1050)]


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
    from clip_engine.camera_region import VIDEO_REGION_VERSION, region_from_video_json

    stored = {
        # Not a literal: a version bump must not quietly turn this into a
        # rejection test that passes for the wrong reason.
        "version": VIDEO_REGION_VERSION,
        "x": 100,
        "y": 200,
        "width": 1400,
        "height": 600,
        "frame": {"width": 1920, "height": 1080},
        "sample_frames": 10,
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


# ── video-level consensus (Issue 443) ────────────────────────────────────────

# Modelled on the 2026-08-07 live audit. The healthy per-clip band measured a
# 0.507 height fraction; the defective whole-runtime rect measured 0.701 and had
# swallowed the SUBSCRIBE strip. Their IoU is 0.724 — the number _MIN_WINDOW_IOU
# is calibrated against.
_HEALTHY = (0, 266, 1918, 548)
_POISONED = (0, 322, 1918, 757)
# A side-by-side layout that lost its second camera: same band, half the width.
# Height-only agreement is blind to this; IoU is not.
_HALF_WIDTH = (0, 266, 959, 548)


def _consensus(regions, duration_s: float = 1617.0, **kwargs):
    """Run the video-level consensus with the per-window detector stubbed out.

    ``regions`` must be sized to the window count. A short list is a real trap:
    ``StopIteration`` from a ``side_effect`` would be swallowed by
    ``detect_camera_region``'s bare ``except Exception`` and degrade to a
    ``None`` decline, so the test would pass for entirely the wrong reason.
    Indexing raises ``IndexError`` out of the stub instead — loudly.
    """
    calls: list[dict] = []

    def _fake(_src, start_s, end_s, _fw, _fh, **kw):
        calls.append({"start_s": start_s, "end_s": end_s, **kw})
        return regions[len(calls) - 1]

    with patch("clip_engine.camera_region.detect_camera_region", side_effect=_fake):
        stored = detect_video_camera_region(_SRC, duration_s, _FRAME_W, _FRAME_H, **kwargs)
    return stored, calls


def test_video_region_never_runs_one_detection_over_the_whole_runtime():
    """Issue 443's root cause, pinned.

    One variance pass across 27 minutes measures "what changed at any point",
    not "where the camera is": the motion mask saturates and the rect grows to
    swallow the chrome it exists to exclude. Every window must stay inside the
    span the detector is actually verified on. This is the assertion whose
    absence let the defect ship.
    """
    stored, calls = _consensus([_HEALTHY] * cr._MAX_WINDOWS)

    assert stored is not None
    assert len(calls) >= cr._MIN_CONSENSUS_WINDOWS
    spans = [c["end_s"] - c["start_s"] for c in calls]
    assert max(spans) <= cr._LINEAR_DECODE_MAX_SPAN_S
    assert max(spans) <= cr._WINDOW_SPAN_S
    assert max(c["end_s"] for c in calls) < 1617.0, "no window may span the whole runtime"


def test_window_spans_are_ordered_disjoint_and_skip_head_and_tail():
    spans = cr._window_spans(1617.0, 60.0, 9)

    assert len(spans) == 9
    assert spans == sorted(spans)
    # Disjoint: overlapping windows share frames, so one overlay burst would
    # poison two of them and erode the median's 50% breakdown point.
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:], strict=False))
    assert spans[0][0] > 0.0, "the cold open is skipped"
    assert spans[-1][1] < 1617.0, "the end card is skipped"


def test_one_poisoned_window_cannot_move_the_consensus():
    """AC2. The median's 50% breakdown point is the whole reason for the design."""
    stored, _ = _consensus([_HEALTHY] * 8 + [_POISONED])

    assert stored is not None
    assert stored["height"] == _HEALTHY[3]
    assert stored["height"] / _FRAME_H < 0.60, "the 0.70 chrome-swallowing shape must not win"


def test_consensus_declines_when_the_windows_disagree():
    """AC3. Three different layouts across the runtime, none of them a majority:
    storing any of them would be an artifact, so store nothing and let each clip
    detect for itself."""
    stored, _ = _consensus([_HEALTHY] * 3 + [_POISONED] * 3 + [_HALF_WIDTH] * 3)

    assert stored is None


def test_consensus_declines_below_the_survivor_quorum():
    """Two survivors is not a consensus: ``statistics.median`` of two values is
    their MEAN, whose breakdown point is 0, so one poisoned window would drag it
    halfway to itself."""
    stored, calls = _consensus([_HEALTHY, _POISONED] + [None] * 7)

    assert stored is None
    assert len(calls) == cr._MAX_WINDOWS, "declines must not stop the remaining windows"


def test_short_video_declines_without_spending_ffmpeg():
    """A runtime too short for a quorum of windows is decided before any decode:
    a ≤3-minute video's clips each span most of it, so per-clip detection already
    IS the whole-video detection."""
    stored, calls = _consensus([], duration_s=120.0)

    assert stored is None
    assert calls == []


def test_the_window_budget_is_a_deadline_not_a_quota():
    """Each window gets the fair share of what is actually LEFT, so a slow window
    cannot starve the ones behind it — and once the deadline passes, the pass
    stops rather than running the remaining windows on a spent budget."""
    stored, calls = _consensus([_HEALTHY] * cr._MAX_WINDOWS, timeout_s=240.0)
    assert stored is not None
    assert all(c["timeout_s"] <= 240.0 for c in calls)

    clock = iter([0.0, 0.0, 1.0, 2.0] + [10_000.0] * 50)
    with patch("clip_engine.camera_region.time.monotonic", side_effect=lambda: next(clock)):
        stored, calls = _consensus([_HEALTHY] * cr._MAX_WINDOWS, timeout_s=240.0)

    assert len(calls) == 3, "the loop must stop once the budget is spent"
    # A truncated pass is still judged on its merits — three agreeing windows
    # ARE the quorum, so this one stands rather than being thrown away.
    assert stored is not None
    assert stored["windows"] == 3


def test_video_region_records_its_consensus_provenance():
    """The stored shape carries how the answer was reached, so a live drill is
    one SQL query rather than a log hunt."""
    stored, _ = _consensus([_HEALTHY] * 8 + [_POISONED])

    assert stored is not None
    assert stored["version"] == cr.VIDEO_REGION_VERSION
    assert stored["windows"] == cr._MAX_WINDOWS
    assert stored["windows_detected"] == cr._MAX_WINDOWS
    assert stored["windows_agreeing"] == 8
    assert stored["sample_frames"] == 10, "frames are PER WINDOW, never a total to divide"
    assert stored["window_span_s"] == cr._WINDOW_SPAN_S


def test_a_window_with_an_overlay_burst_does_not_poison_the_consensus():
    """End-to-end: the real detector runs on every window, and the one window
    that happens to contain an animated overlay overlapping the camera band
    cannot move the stored rect."""
    camera = (slice(40, 200), slice(20, 460))
    overlay = (slice(200, 250), slice(20, 460))
    clean = _stack_bands([camera])
    burst = _stack_bands([camera, overlay])
    stacks = [clean] * (cr._MAX_WINDOWS - 1) + [burst]

    with patch("clip_engine.camera_region._sample_gray_frames", side_effect=stacks):
        stored = detect_video_camera_region(_SRC, 1617.0, _FRAME_W, _FRAME_H)
    with patch("clip_engine.camera_region._sample_gray_frames", return_value=clean):
        all_clean = detect_video_camera_region(_SRC, 1617.0, _FRAME_W, _FRAME_H)

    assert stored is not None and all_clean is not None
    assert stored["height"] == all_clean["height"]


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
