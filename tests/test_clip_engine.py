"""
Unit tests for clip_engine/window.py and clip_engine/candidates.py.

Includes eval harness that loads YAML scenario fixtures and asserts the
"clip the setup, not the aftermath" invariant on labeled timelines.
"""

import glob
import os

import numpy as np
import pytest
import yaml

from clip_engine.candidates import (
    MIN_CLIP_S,
    WINDOW_S,
    _find_setup_start,
    extract_candidates,
)
from clip_engine.window import RESOLUTION_S, build_signal_array

# ── build_signal_array ─────────────────────────────────────────────────────────


def test_build_signal_array_empty_timeline():
    times, signal = build_signal_array({"duration_s": 0.0, "events": []})
    assert len(times) == 0
    assert len(signal) == 0


def test_build_signal_array_length():
    times, signal = build_signal_array({"duration_s": 10.0, "events": []})
    assert len(times) == len(signal)
    expected_n = int(10.0 / RESOLUTION_S) + 1
    assert len(times) == expected_n


def test_build_signal_array_retention_spike_raises_signal():
    timeline = {
        "duration_s": 20.0,
        "events": [{"type": "retention_spike", "start_s": 10.0, "end_s": 11.0, "value": 1.5}],
    }
    _, signal = build_signal_array(timeline)
    idx = int(10.0 / RESOLUTION_S)
    assert signal[idx] > 0.0


def test_build_signal_array_silence_lowers_signal():
    timeline = {
        "duration_s": 20.0,
        "events": [{"type": "silence", "start_s": 5.0, "end_s": 7.0}],
    }
    _, signal = build_signal_array(timeline)
    idx = int(6.0 / RESOLUTION_S)
    assert signal[idx] < 0.0


def test_build_signal_array_unknown_event_type_ignored():
    timeline = {
        "duration_s": 10.0,
        "events": [{"type": "unknown_event", "start_s": 5.0, "end_s": 6.0}],
    }
    _, signal = build_signal_array(timeline)
    assert np.all(signal == 0.0)


# ── _find_setup_start ──────────────────────────────────────────────────────────


def _timeline(events):
    return {"duration_s": 200.0, "events": events}


def test_find_setup_start_uses_silence_end():
    tl = _timeline([{"type": "silence", "start_s": 40.0, "end_s": 45.0}])
    result = _find_setup_start(tl, peak_s=90.0)
    assert result == pytest.approx(45.0)


def test_find_setup_start_most_recent_silence():
    tl = _timeline(
        [
            {"type": "silence", "start_s": 20.0, "end_s": 23.0},
            {"type": "silence", "start_s": 55.0, "end_s": 58.0},
        ]
    )
    result = _find_setup_start(tl, peak_s=90.0)
    assert result == pytest.approx(58.0)


def test_find_setup_start_falls_back_to_energy_spike():
    tl = _timeline([{"type": "energy_spike", "start_s": 60.0, "end_s": 75.0, "value": 0.8}])
    result = _find_setup_start(tl, peak_s=90.0)
    assert result == pytest.approx(60.0)


def test_find_setup_start_falls_back_to_window_edge():
    result = _find_setup_start(_timeline([]), peak_s=90.0, window_s=WINDOW_S)
    assert result == pytest.approx(max(0.0, 90.0 - WINDOW_S))


def test_find_setup_start_clamps_to_zero():
    result = _find_setup_start(_timeline([]), peak_s=5.0, window_s=WINDOW_S)
    assert result == 0.0


def test_find_setup_start_silence_outside_window_ignored():
    # silence before the lookback window should not be used
    tl = _timeline([{"type": "silence", "start_s": 1.0, "end_s": 3.0}])
    result = _find_setup_start(tl, peak_s=90.0, window_s=10.0)
    # window edge is 80.0; silence is at 1-3s, which is outside
    assert result == pytest.approx(80.0)


# ── extract_candidates ─────────────────────────────────────────────────────────


def _make_timeline(peaks_at: list[float], duration_s: float = 200.0) -> dict:
    """Helper: timeline with strong retention spikes at given times."""
    events = []
    for t in peaks_at:
        events.append({"type": "silence", "start_s": max(0, t - 20), "end_s": t - 15})
        events.append({"type": "energy_spike", "start_s": t - 15, "end_s": t, "value": 0.8})
        events.append({"type": "retention_spike", "start_s": t, "end_s": t + 2, "value": 1.5})
    return {"duration_s": duration_s, "events": events}


def test_extract_candidates_empty_timeline():
    result = extract_candidates({"duration_s": 0.0, "events": []})
    assert result == []


def test_extract_candidates_structure():
    tl = _make_timeline([90.0])
    candidates = extract_candidates(tl)
    assert len(candidates) >= 1
    c = candidates[0]
    assert "setup_start_s" in c
    assert "start_s" in c
    assert "peak_s" in c
    assert "end_s" in c


def test_extract_candidates_respects_max():
    tl = _make_timeline([40.0, 80.0, 120.0, 160.0])
    candidates = extract_candidates(tl, max_candidates=2)
    assert len(candidates) <= 2


def test_extract_candidates_sorted_chronologically():
    tl = _make_timeline([120.0, 60.0])
    candidates = extract_candidates(tl)
    starts = [c["setup_start_s"] for c in candidates]
    assert starts == sorted(starts)


def test_extract_candidates_end_after_peak():
    tl = _make_timeline([90.0])
    candidates = extract_candidates(tl)
    for c in candidates:
        assert c["end_s"] > c["peak_s"]


def test_extract_candidates_min_clip_length_respected():
    tl = _make_timeline([90.0])
    candidates = extract_candidates(tl)
    for c in candidates:
        assert c["end_s"] - c["setup_start_s"] >= MIN_CLIP_S


# ── CORE INVARIANT: setup always before peak ──────────────────────────────────


def test_setup_always_before_peak():
    """Principle #2: setup_start_s must be strictly less than peak_s for every candidate."""
    tl = _make_timeline([60.0, 120.0, 170.0])
    candidates = extract_candidates(tl, max_candidates=8)
    assert len(candidates) >= 1
    for c in candidates:
        assert c["setup_start_s"] < c["peak_s"], (
            f"setup_start_s={c['setup_start_s']} >= peak_s={c['peak_s']} "
            "— clip starts AFTER the peak (aftermath), not at the setup"
        )


# ── Eval harness: YAML scenario fixtures ─────────────────────────────────────

SCENARIOS_DIR = os.path.join(os.path.dirname(__file__), "eval", "scenarios")


def _load_scenarios():
    pattern = os.path.join(SCENARIOS_DIR, "*.yaml")
    return [
        pytest.param(path, id=os.path.splitext(os.path.basename(path))[0])
        for path in sorted(glob.glob(pattern))
    ]


@pytest.mark.parametrize("scenario_path", _load_scenarios())
def test_eval_scenario(scenario_path):
    with open(scenario_path) as f:
        scenario = yaml.safe_load(f)

    timeline = scenario["input"]["timeline"]
    expected = scenario.get("expected", {})
    candidates = extract_candidates(timeline, max_candidates=8)

    # Minimum candidates check
    min_c = expected.get("min_candidates", 0)
    assert len(candidates) >= min_c, (
        f"[{scenario['scenario']}] expected >= {min_c} candidates, got {len(candidates)}"
    )

    # Global invariant: setup before peak
    if expected.get("all_setup_before_peak", False):
        for c in candidates:
            assert c["setup_start_s"] < c["peak_s"], (
                f"[{scenario['scenario']}] setup_start_s={c['setup_start_s']} "
                f">= peak_s={c['peak_s']}"
            )

    # Window overlap / deduplication check
    win = expected.get("max_candidates_in_window")
    if win:
        w_start = win["window_start_s"]
        w_end = win["window_end_s"]
        in_window = [
            c
            for c in candidates
            if c["setup_start_s"] < w_end and c.get("end_s", c["peak_s"] + 30) > w_start
        ]
        assert len(in_window) <= win["max"], (
            f"[{scenario['scenario']}] {len(in_window)} candidates overlap window "
            f"[{w_start},{w_end}], expected <= {win['max']}"
        )

    # Per-candidate assertions
    for i, exp_c in enumerate(expected.get("candidates", [])):
        assert i < len(candidates), f"[{scenario['scenario']}] missing candidate {i}"
        # Match candidate by peak proximity
        matched = min(
            candidates,
            key=lambda c: abs(
                c["peak_s"] - (exp_c.get("peak_s_min", 0) + exp_c.get("peak_s_max", 200)) / 2
            ),
        )

        if "peak_s_min" in exp_c:
            assert matched["peak_s"] >= exp_c["peak_s_min"], (
                f"[{scenario['scenario']}] peak_s={matched['peak_s']} < expected min {exp_c['peak_s_min']}"
            )
        if "peak_s_max" in exp_c:
            assert matched["peak_s"] <= exp_c["peak_s_max"], (
                f"[{scenario['scenario']}] peak_s={matched['peak_s']} > expected max {exp_c['peak_s_max']}"
            )
        if "setup_start_s_max" in exp_c:
            assert matched["setup_start_s"] <= exp_c["setup_start_s_max"], (
                f"[{scenario['scenario']}] setup_start_s={matched['setup_start_s']} "
                f"> expected max {exp_c['setup_start_s_max']} — "
                "clip is starting at the aftermath, not the setup"
            )
        if "setup_start_s_min" in exp_c:
            assert matched["setup_start_s"] >= exp_c["setup_start_s_min"], (
                f"[{scenario['scenario']}] setup_start_s={matched['setup_start_s']} "
                f"< expected min {exp_c['setup_start_s_min']} — clip starts before video begins"
            )
