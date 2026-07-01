"""Issue 327 — malformed-geometry validation at the signal-build boundary.

The signal/clip layer (``window.py``, ``candidates.py``, ``scoring.py``) compares
timestamps only against *positive* thresholds, so an inverted (``end_s < start_s``),
negative, non-finite, or out-of-bounds event used to pass through silently and
either distort the composite signal array or anchor a clip's setup to the wrong
point. ``build_signal_timeline`` now drops such events once, at ingress, with a
WARNING that carries a dropped-count.

These tests lock that behavior (and the ``window.py`` defense-in-depth guard) so a
future refactor cannot silently reintroduce the silent-pass.
"""

from __future__ import annotations

import logging

import numpy as np
import pytest

from clip_engine.window import build_signal_array
from ingestion.signals import _event_geometry_is_valid, build_signal_timeline


def _audio(duration_s: float, **kinds) -> dict:
    base = {"duration_s": duration_s, "energy_spikes": [], "silences": [], "laughter": []}
    base.update(kinds)
    return base


# ── _event_geometry_is_valid (the boundary predicate) ────────────────────────


@pytest.mark.parametrize(
    "event, duration_s, expected",
    [
        # valid ranged event
        ({"start_s": 1.0, "end_s": 2.0}, 10.0, True),
        # valid point event (no end_s) — e.g. retention_spike
        ({"start_s": 4.5}, 10.0, True),
        # zero-length is degenerate but harmless → kept
        ({"start_s": 3.0, "end_s": 3.0}, 10.0, True),
        # inverted: end < start
        ({"start_s": 5.0, "end_s": 4.0}, 10.0, False),
        # negative start
        ({"start_s": -1.0, "end_s": 2.0}, 10.0, False),
        # start beyond known duration
        ({"start_s": 11.0, "end_s": 12.0}, 10.0, False),
        # non-finite start / end
        ({"start_s": float("nan"), "end_s": 2.0}, 10.0, False),
        ({"start_s": float("inf")}, 10.0, False),
        ({"start_s": 1.0, "end_s": float("nan")}, 10.0, False),
        # missing/None start
        ({"end_s": 2.0}, 10.0, False),
        ({"start_s": None}, 10.0, False),
        # bool is not a valid timestamp (bool ⊂ int in Python)
        ({"start_s": True}, 10.0, False),
        # unknown duration (<= 0) disables the upper-bound check
        ({"start_s": 9999.0, "end_s": 10000.0}, 0.0, True),
    ],
)
def test_event_geometry_predicate(event, duration_s, expected):
    assert _event_geometry_is_valid(event, duration_s) is expected


# ── build_signal_timeline drops malformed events + logs a count ──────────────


def test_inverted_event_dropped_and_logged(caplog):
    audio = _audio(10.0, energy_spikes=[{"start_s": 6.0, "end_s": 5.0, "value": 0.9}])
    with caplog.at_level(logging.WARNING, logger="ingestion.signals"):
        result = build_signal_timeline(audio, [])
    assert result["events"] == []
    assert any("dropped 1 malformed event" in r.message for r in caplog.records)


def test_negative_and_oob_events_dropped():
    audio = _audio(
        10.0,
        energy_spikes=[{"start_s": -2.0, "end_s": 1.0, "value": 0.8}],
        silences=[{"start_s": 50.0, "end_s": 51.0}],  # beyond duration
    )
    result = build_signal_timeline(audio, [])
    assert result["events"] == []


def test_valid_events_are_kept_regression():
    audio = _audio(
        20.0,
        energy_spikes=[{"start_s": 10.0, "end_s": 11.0, "value": 0.9}],
        silences=[{"start_s": 2.0, "end_s": 3.0}],
        laughter=[{"start_s": 6.0, "end_s": 7.0, "value": 0.5}],
    )
    result = build_signal_timeline(audio, [])
    types = {e["type"] for e in result["events"]}
    assert types == {"energy_spike", "silence", "laughter"}


def test_retention_point_out_of_bounds_dropped():
    from types import SimpleNamespace

    audio = _audio(10.0)
    # timestamp beyond the audio duration → malformed → dropped
    pt = SimpleNamespace(
        timestamp_s=999.0, audience_watch_ratio=0.9, relative_retention_performance=2.0
    )
    result = build_signal_timeline(audio, [pt])
    assert not any(e["type"] == "retention_spike" for e in result["events"])


def test_unknown_duration_keeps_in_range_events():
    # duration_s missing/0 → upper-bound check disabled, but inverted still dropped
    audio = {
        "duration_s": 0.0,
        "energy_spikes": [{"start_s": 5.0, "end_s": 6.0, "value": 0.8}],
        "silences": [{"start_s": 8.0, "end_s": 7.0}],  # inverted → dropped
        "laughter": [],
    }
    result = build_signal_timeline(audio, [])
    types = [e["type"] for e in result["events"]]
    assert types == ["energy_spike"]


def test_mixed_valid_and_malformed_counts_only_dropped(caplog):
    audio = _audio(
        20.0,
        energy_spikes=[
            {"start_s": 10.0, "end_s": 11.0, "value": 0.9},  # valid
            {"start_s": 12.0, "end_s": 11.0, "value": 0.9},  # inverted
        ],
        silences=[{"start_s": -1.0, "end_s": 1.0}],  # negative
    )
    with caplog.at_level(logging.WARNING, logger="ingestion.signals"):
        result = build_signal_timeline(audio, [])
    assert len(result["events"]) == 1
    assert any("dropped 2 malformed event" in r.message for r in caplog.records)


# ── window.build_signal_array defense-in-depth ───────────────────────────────


def test_build_signal_array_inverted_window_is_noop():
    # Even if a malformed event reaches window.py directly, the i1<=i0 guard
    # must prevent a reversed/empty slice from being written.
    timeline = {
        "duration_s": 10.0,
        "events": [{"type": "energy_spike", "start_s": 6.0, "end_s": 5.0, "value": 1.0}],
    }
    _times, signal = build_signal_array(timeline)
    assert np.all(signal == 0.0)


def test_build_signal_array_valid_event_writes_signal():
    timeline = {
        "duration_s": 10.0,
        "events": [{"type": "retention_spike", "start_s": 4.0, "end_s": 5.0, "value": 1.0}],
    }
    _times, signal = build_signal_array(timeline)
    assert signal.max() > 0.0
