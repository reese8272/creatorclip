"""Tests for transient overlay-band detection (Issue 448).

Every frame here is real production footage frozen from video `7e988321`
before its source purged — see `tests/fixtures/superchat/README.md`. Nothing is
synthesised, so a pass means the detector works on the actual defect rather
than on a shape invented to match it.

No ffmpeg and no network: the fixtures are 480px grayscale PNGs, deliberately
identical to what `camera_region._sample_gray_frames` returns.
"""

import glob
from pathlib import Path
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest

from clip_engine.overlay_bands import (
    _MIN_BRIGHT_ROWS_FRAC,
    _MIN_RUN_SAMPLES,
    OVERLAY_SPANS_VERSION,
    _bright_row_counts,
    _run_band_rows,
    _runs,
    overlay_blur_filters,
    spans_from_video_json,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "superchat"


def _load(pattern: str) -> list:
    files = sorted(glob.glob(str(_FIXTURES / pattern)))
    assert files, f"no fixtures matched {pattern}"
    return [cv2.imread(f, cv2.IMREAD_GRAYSCALE) for f in files]


def _positive(stack: list) -> list[bool]:
    counts, h = _bright_row_counts(stack)
    threshold = max(1, int(round(_MIN_BRIGHT_ROWS_FRAC * h)))
    return [c >= threshold for c in counts]


# ── Detection on real footage ────────────────────────────────────────────────


def test_band_onset_is_found_at_the_measured_second() -> None:
    """Fixtures start at t=879 s at 1 Hz; the banner appears at 885 s."""
    stack = _load("onset_*.png")
    assert _positive(stack) == [False] * 6 + [True] * 6


def test_band_offset_is_found_at_the_measured_second() -> None:
    """Fixtures start at t=1000 s at 1 Hz; the banner clears at 1006 s."""
    stack = _load("offset_*.png")
    assert _positive(stack) == [True] * 7 + [False] * 5


def test_clean_footage_yields_no_run() -> None:
    """The control set must not produce a mask — a false positive blurs a face."""
    stack = _load("clean_*.png")
    assert not any(_positive(stack))
    assert _runs(_positive(stack), _MIN_RUN_SAMPLES) == []


def test_a_lone_positive_sample_is_not_a_span() -> None:
    """Fail-open: a flash or a bright cut must not mask anything."""
    assert _runs([False, True, False, True, False], _MIN_RUN_SAMPLES) == []
    assert _runs([False] + [True] * _MIN_RUN_SAMPLES + [False], _MIN_RUN_SAMPLES) == [
        (1, _MIN_RUN_SAMPLES)
    ]


def test_extent_is_per_run_so_two_banner_sizes_are_both_covered() -> None:
    """The bug this guards against silently left banner visible in the render.

    The two real banners differ in height (analysis rows 199-243 and 217-242).
    Taking a majority across EVERY positive frame in the video collapses to
    their intersection, 217-242, leaving ~72 source pixels of the taller banner
    on screen. Measuring per run and unioning is what fixes it.
    """
    stack = _load("onset_*.png") + _load("clean_*.png") + _load("offset_*.png")
    positive = _positive(stack)
    runs = _runs(positive, _MIN_RUN_SAMPLES)
    assert len(runs) == 2, "expected one run per banner"

    extents = [_run_band_rows(stack, a, b) for a, b in runs]
    assert all(e is not None for e in extents)
    union_top = min(e[0] for e in extents)
    union_bottom = max(e[1] for e in extents)

    # The taller banner's top must survive the union.
    assert union_top == min(e[0] for e in extents) == 199
    assert union_bottom == 243

    # And the naive whole-video majority would NOT have covered it — this is
    # the assertion that fails if someone "simplifies" the per-run logic away.
    positives_only = [stack[i] for i, p in enumerate(positive) if p]
    naive = _run_band_rows(positives_only, 0, len(positives_only) - 1)
    assert naive is not None
    assert naive[0] > union_top, "the naive majority must under-cover, or this test proves nothing"


# ── Stored-document guards ───────────────────────────────────────────────────


def _doc(**over) -> dict:
    doc = {
        "version": OVERLAY_SPANS_VERSION,
        "y": 785,
        "height": 202,
        "frame": {"width": 1920, "height": 1080},
        "spans": [{"start_s": 885.0, "end_s": 914.0}],
    }
    doc.update(over)
    return doc


def test_spans_unpack_round_trip() -> None:
    assert spans_from_video_json(_doc(), 1920, 1080) == (785, 202, [(885.0, 914.0)])


def test_spans_rejected_when_source_dimensions_changed() -> None:
    """Masking the wrong rows is worse than not masking — same guard as the
    camera region's."""
    assert spans_from_video_json(_doc(), 1280, 720) is None


def test_spans_rejected_on_version_mismatch_and_empty() -> None:
    assert spans_from_video_json(_doc(version=OVERLAY_SPANS_VERSION + 1), 1920, 1080) is None
    assert spans_from_video_json(_doc(spans=[]), 1920, 1080) is None
    assert spans_from_video_json(None, 1920, 1080) is None


def test_spans_rejected_when_band_falls_outside_the_frame() -> None:
    assert spans_from_video_json(_doc(y=1000, height=200), 1920, 1080) is None
    assert spans_from_video_json(_doc(height=0), 1920, 1080) is None


# ── Filter construction ──────────────────────────────────────────────────────


def test_no_filters_when_the_clip_misses_every_span() -> None:
    """An unaffected clip's vf chain must stay byte-identical to today's."""
    assert overlay_blur_filters(785, 202, [(885.0, 914.0)], 100.0, 200.0, 1920) == []


def test_filters_are_clip_relative_and_clamped_to_the_window() -> None:
    """Render seeks with `-ss` before `-i`, so filter time starts at 0."""
    parts = overlay_blur_filters(785, 202, [(885.0, 914.0)], 880.0, 900.0, 1920)
    assert parts == [
        "split[ob_bg][ob_fg];"
        "[ob_fg]crop=1920:202:0:785,boxblur=12:2[ob_bl];"
        # Span 885-914 against clip 880-900 -> 5.0-20.0 clip-relative.
        "[ob_bg][ob_bl]overlay=0:785:enable='between(t,5.000,20.000)'"
    ]


def test_fragment_is_one_part_with_semicolons_not_three_parts() -> None:
    """The branch is expressed with ffmpeg's documented chain separator.

    A comma-joined form also parses — explicit link labels override the comma's
    implicit "pipe into the next filter" — so this is a readability and
    robustness pin, NOT a fix for a broken graph. It matters because the comma
    form reads as a linear chain when it is actually a branch, and it would stop
    parsing the moment a label were dropped. The compile test below is what
    guards validity.
    """
    parts = overlay_blur_filters(785, 202, [(885.0, 914.0)], 880.0, 900.0, 1920)
    assert len(parts) == 1
    assert parts[0].count(";") == 2
    # Joining with a comma, as render.py does, must leave the chains intact.
    assert ",".join([*parts, "crop=309:551:230:0"]).endswith("',crop=309:551:230:0")


def test_multiple_spans_are_one_enable_expression() -> None:
    parts = overlay_blur_filters(785, 202, [(885.0, 914.0), (930.0, 1006.0)], 880.0, 1010.0, 1920)
    assert "between(t,5.000,34.000)+between(t,50.000,126.000)" in parts[0]


@pytest.mark.parametrize("bad", [(0.0, 0.0), (900.0, 880.0)])
def test_degenerate_spans_produce_no_filter(bad: tuple[float, float]) -> None:
    assert overlay_blur_filters(785, 202, [bad], 0.0, 1000.0, 1920) == []


# ── The graph must actually parse ────────────────────────────────────────────


def test_emitted_graph_compiles_in_a_real_simple_filtergraph() -> None:
    """Build the exact `-vf` string render.py builds and make ffmpeg parse it.

    The unit assertions above pin the *shape*; only ffmpeg can confirm the
    graph is *valid*. This is the test that fails if the stream labels, the
    filter order or the enable expression regress — none of which a string
    assertion can rule on.

    Skipped when ffmpeg is unavailable (the dev box may not have it); CI's
    render image does.
    """
    import shutil
    import subprocess

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")

    parts = overlay_blur_filters(785, 202, [(885.0, 914.0)], 880.0, 900.0, 1920)
    # Exactly how render.py assembles it: mask first, then the region pre-crop,
    # the labelled 9:16 crop and the scale.
    vf = ",".join([*parts, "crop=1704:551:169:326", "crop@spk=309:551:230:0", "scale=1080:1920"])

    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=1920x1080:duration=0.4:rate=5",
            "-vf",
            vf,
            "-frames:v",
            "2",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"ffmpeg rejected the graph:\n{vf}\n\n{proc.stderr[-800:]}"


# ── End-to-end detection (sampler mocked, frames real) ───────────────────────


def _combined_stack() -> list:
    """Onset -> clean -> offset, i.e. two banner instances with a gap."""
    return _load("onset_*.png") + _load("clean_*.png") + _load("offset_*.png")


def _timed(frames: list, interval: float = 1.0) -> tuple[list, float]:
    """Wrap plain frames the way `_sample_gray_frames_timed` returns them:
    evenly spaced sample-centre timestamps plus the fully-scanned duration."""
    stamps = [(i + 0.5) * interval for i in range(len(frames))]
    return list(zip(stamps, frames)), len(frames) * interval


def test_detect_builds_the_stored_document_from_real_frames(monkeypatch) -> None:
    """Only the ffmpeg sampler is stubbed; every frame is production footage.

    36 frames over a 36 s duration means one sample per second, so the runs at
    indices 6-11 and 24-30 (sample centres 6.5-11.5 and 24.5-30.5) become
    spans padded by _SPAN_PAD_S.
    """
    import clip_engine.overlay_bands as ob

    monkeypatch.setattr(ob, "_sample_gray_frames_timed", lambda *a, **k: _timed(_combined_stack()))
    doc = ob.detect_overlay_spans(Path("unused.mp4"), 36.0, 1920, 1080, sample_interval_s=1.0)

    assert doc is not None
    assert doc["version"] == OVERLAY_SPANS_VERSION
    assert doc["frame"] == {"width": 1920, "height": 1080}
    assert doc["samples"] == 36
    assert doc["scanned_until_s"] == 36.0
    assert doc["sample_interval_s"] == 1.0
    assert len(doc["spans"]) == 2, "one span per banner instance"
    assert doc["spans"][0] == {"start_s": 6.0, "end_s": 12.0}
    assert doc["spans"][1] == {"start_s": 24.0, "end_s": 31.0}

    # Band rect, scaled from 480x270 analysis rows to source pixels and padded.
    # The union must reach the TALLER banner's top (analysis row 199 -> ~796px).
    assert doc["y"] <= 796
    assert doc["y"] + doc["height"] >= 968
    assert doc["y"] + doc["height"] <= 1080

    # And it round-trips through the render-side unpacker.
    assert spans_from_video_json(doc, 1920, 1080) is not None


def test_detect_returns_none_on_clean_footage(monkeypatch) -> None:
    """No banner, no document — and therefore no mask at render."""
    import clip_engine.overlay_bands as ob

    monkeypatch.setattr(
        ob, "_sample_gray_frames_timed", lambda *a, **k: _timed(_load("clean_*.png"))
    )
    assert ob.detect_overlay_spans(Path("unused.mp4"), 12.0, 1920, 1080) is None


def test_detect_returns_none_when_sampling_fails_or_is_too_short(monkeypatch) -> None:
    """Ingest must never fail on this; None simply means no masking."""
    import clip_engine.overlay_bands as ob

    monkeypatch.setattr(ob, "_sample_gray_frames_timed", lambda *a, **k: None)
    assert ob.detect_overlay_spans(Path("unused.mp4"), 36.0, 1920, 1080) is None

    monkeypatch.setattr(
        ob, "_sample_gray_frames_timed", lambda *a, **k: _timed(_load("onset_*.png")[:2])
    )
    assert ob.detect_overlay_spans(Path("unused.mp4"), 36.0, 1920, 1080) is None

    # Degenerate inputs are rejected before any sampling is attempted.
    assert ob.detect_overlay_spans(Path("unused.mp4"), 0.0, 1920, 1080) is None
    assert ob.detect_overlay_spans(Path("unused.mp4"), 36.0, 1920, 0) is None


def _synthetic_frame(band: bool) -> np.ndarray:
    """54x32 gray frame; ``band`` paints 8 anomalously bright rows in the
    lower region — enough to clear the scaled `_MIN_BRIGHT_ROWS_FRAC` gate."""
    frame = np.full((54, 32), 60, np.uint8)
    if band:
        frame[42:50, :] = 200
    return frame


class _SeekClock:
    """Stand-in for camera_region's ``time`` module — ``monotonic()`` under
    test control so the sampling budget trips deterministically."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


def test_span_times_survive_dropped_frames_and_a_budget_cut(monkeypatch) -> None:
    """Issue 466 defects 2+3: span times must come from real timestamps.

    A 1000 s source with a banner at t=300-330 s, every 4th sample failing and
    the budget exhausted at ~700 s of source time must still yield a span at
    300-330 — and the stored document must say how far the scan actually got.
    Before the fix, ``duration / len(stack)`` dilated the surviving indices
    (the live repro mapped a real 300-330 s banner to ~606-668 s) and the
    truncation was silent.
    """
    import clip_engine.camera_region as cr
    import clip_engine.overlay_bands as ob

    clock = _SeekClock()
    monkeypatch.setattr(cr, "time", clock)
    calls: list[float] = []

    def _fake_run(cmd, **kwargs):
        clock.now += 1.0  # one fake second per seek; timeout_s below caps the count
        t = float(cmd[cmd.index("-ss") + 1])
        calls.append(t)
        if len(calls) % 4 == 0:
            return MagicMock(returncode=1, stdout="", stderr="")
        cv2.imwrite(cmd[-1], _synthetic_frame(300.0 <= t <= 330.0))
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cr.subprocess, "run", _fake_run)

    # 0.5 s cadence over 1000 s = 2000 samples; the fake clock stops the scan
    # after 1400 seeks, i.e. at source time ~700 s.
    doc = ob.detect_overlay_spans(Path("unused.mp4"), 1000.0, 1920, 1080, timeout_s=1400.0)

    assert doc is not None
    assert doc["version"] == OVERLAY_SPANS_VERSION
    [span] = doc["spans"]
    assert span["start_s"] == pytest.approx(300.0, abs=1.0)
    assert span["end_s"] == pytest.approx(330.0, abs=1.0)
    # Truncation is explicit, and the stored cadence is the REQUESTED one.
    assert doc["scanned_until_s"] == pytest.approx(700.0, abs=1.0)
    assert doc["sample_interval_s"] == pytest.approx(0.5)


def test_detect_swallows_detector_errors(monkeypatch) -> None:
    """A raising sampler must not propagate into ingest."""
    import clip_engine.overlay_bands as ob

    def _boom(*a, **k):
        raise RuntimeError("ffmpeg exploded")

    monkeypatch.setattr(ob, "_sample_gray_frames_timed", _boom)
    assert ob.detect_overlay_spans(Path("unused.mp4"), 36.0, 1920, 1080) is None
