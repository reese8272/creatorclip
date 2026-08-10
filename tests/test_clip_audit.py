"""Tests for the read-only clip-audit diagnostic (`scripts/clip_audit.py`).

Only the pure summarizers are covered — they are the parts that turn a crop
track and a stored camera region into the numbers an audit verdict rests on, so
a wrong answer here would silently mis-grade a render. The SQL and ffmpeg halves
are I/O shells.
"""

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "clip_audit", Path(__file__).resolve().parents[1] / "scripts" / "clip_audit.py"
)
assert _SPEC and _SPEC.loader
clip_audit = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(clip_audit)


def _track(keyframes: list[tuple[float, float]], **overrides: object) -> dict:
    track = {
        "version": 1,
        "mode": "face_pan",
        "duration_s": 10.0,
        "keyframes": [{"t": t, "x": x} for t, x in keyframes],
        "cuts": [],
        "shots": [],
        "speakers": {"count": 2, "mapping_confidence": 0.5},
        "meta": {"sample_fps": 4.0, "fallback": False},
    }
    track.update(overrides)
    return track


def test_absent_track_reports_not_present() -> None:
    assert clip_audit.track_stats(None) == {"present": False}
    assert clip_audit.track_stats({}) == {"present": False}


def test_speaker_cut_hold_counts_only_the_cut_as_a_move() -> None:
    """A virtual-tripod hold is piecewise-constant: x changes only at the cut."""
    stats = clip_audit.track_stats(
        _track(
            [(0.0, 200.0), (4.0, 200.0), (4.03, 1100.0), (10.0, 1100.0)],
            mode="speaker_cut",
            cuts=[{"t": 4.0, "from_x": 200.0, "to_x": 1100.0, "speaker": "B"}],
            region={"x": 169, "y": 330, "width": 1728, "height": 547},
        )
    )
    assert stats["present"] is True
    assert stats["keyframes"] == 4
    assert stats["visible_moves"] == 1
    assert stats["direction_flips"] == 0
    assert stats["max_move_px"] == 900.0
    assert stats["cut_times"] == [4.0]
    assert stats["chrome_removed"] is True


def test_glide_ramp_is_one_direction_and_sub_pixel_wobble_is_ignored() -> None:
    """Densified glide samples are motion, but a run of them is not a direction flip.

    The sub-threshold wobble at the tail must not register as a visible move —
    that is the difference between "gliding" and the 5 Hz staircase Issue 436
    deleted.
    """
    ramp = [(0.0, 100.0)] + [(0.1 * i, 100.0 + 20.0 * i) for i in range(1, 6)]
    wobble = [(1.0, 200.0), (1.1, 202.0), (1.2, 200.0)]
    stats = clip_audit.track_stats(_track(ramp + wobble))
    assert stats["visible_moves"] == 5
    assert stats["direction_flips"] == 0
    assert stats["chrome_removed"] is False
    assert stats["region"] is None


def test_see_saw_pan_registers_direction_flips() -> None:
    """Repeated full-width sweeps are the failure mode an audit must surface."""
    sweep = [(0.0, 100.0), (1.0, 1000.0), (2.0, 100.0), (3.0, 1000.0), (4.0, 100.0)]
    stats = clip_audit.track_stats(_track(sweep, duration_s=4.0))
    assert stats["visible_moves"] == 4
    assert stats["direction_flips"] == 3
    assert stats["moves_per_s"] == pytest.approx(1.0)
    assert stats["x_range"] == [100.0, 1000.0]


def test_absent_region_is_reported_as_a_fallback_not_a_failure() -> None:
    """A NULL `camera_region_jsonb` means a consensus gate declined (Issue 443).

    The render then falls back to per-clip detection, which is the working path,
    so the summary must not read as a defect.
    """
    summary = clip_audit.region_stats(None)
    assert summary["present"] is False
    assert "per-clip" in summary["note"]


def test_height_frac_separates_the_healthy_region_from_the_chrome_defect() -> None:
    """0.51 vs 0.70 is the whole 439/443 verdict — both are real measurements.

    The rect is rank 6 of `3b6992fe`, which burned the SUBSCRIBE button and
    socials strip in; the healthy value is what its siblings resolved.
    """
    defective = clip_audit.region_stats(
        {
            "version": 2,
            "x": 0,
            "y": 330,
            "width": 1918,
            "height": 749,
            "frame": {"width": 1920, "height": 1080},
        }
    )
    healthy = clip_audit.region_stats(
        {
            "version": 2,
            "x": 1,
            "y": 2,
            "width": 1918,
            "height": 548,
            "frame": {"width": 1920, "height": 1080},
            "windows": 9,
            "windows_detected": 8,
            "windows_agreeing": 7,
        }
    )
    assert defective["height_frac"] == pytest.approx(0.694, abs=0.001)
    assert healthy["height_frac"] == pytest.approx(0.507, abs=0.001)
    assert healthy["windows_agreeing"] == 7
    # Provenance is absent on a v1 row rather than fabricated as zero.
    assert defective["windows_agreeing"] is None


def test_source_expiry_is_seventy_two_hours_after_ingest_done() -> None:
    """The retention clock starts at `ingest_done_at`, not upload — it is the
    deadline that ended the two previous verification attempts."""
    done = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    assert clip_audit._expiry(done) == str(datetime(2026, 8, 13, 12, 0, tzinfo=UTC))
    assert clip_audit._expiry(None) is None
