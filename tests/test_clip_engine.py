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
    _NMS_IOU_THRESHOLD,
    MIN_CLIP_S,
    WINDOW_S,
    _find_setup_start,
    _is_sentence_end,
    extract_candidates,
    snap_to_sentence_boundary,
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

# The minimum number of scenario files that must exist. CI enforces this floor so a
# silent deletion (or @skip-piling) cannot hollow out the eval harness without
# raising a visible failure. Raise this number whenever a new scenario is added;
# never lower it. (Issue 265)
SCENARIO_FLOOR = 6

# Scenario files that are explicitly allowed to carry a pytest skip/xfail marker
# (e.g. a known-broken scenario under active investigation). Add the YAML filename
# stem here with a brief justification. Empty by default — every scenario must be
# runnable unless explicitly exempted here. (Issue 265)
SKIP_ALLOWLIST: frozenset[str] = frozenset()


def _load_scenarios() -> list:
    pattern = os.path.join(SCENARIOS_DIR, "*.yaml")
    return [
        pytest.param(path, id=os.path.splitext(os.path.basename(path))[0])
        for path in sorted(glob.glob(pattern))
    ]


def test_eval_scenario_count_floor() -> None:
    """Guard: eval harness must have >= SCENARIO_FLOOR scenario files.

    A silent scenario deletion or bulk rename would otherwise pass the suite while
    hollowing out the clip-quality correctness contract. (Issue 265)
    """
    scenarios = _load_scenarios()
    assert len(scenarios) >= SCENARIO_FLOOR, (
        f"Only {len(scenarios)} eval scenario(s) found in {SCENARIOS_DIR!r}; "
        f"expected >= {SCENARIO_FLOOR}. "
        "Do not delete scenario files — add to SKIP_ALLOWLIST if a scenario is "
        "temporarily broken and needs investigation."
    )


def test_eval_scenario_no_unapproved_skip_markers() -> None:
    """Guard: no scenario YAML may carry a skip/xfail marker unless it is listed in
    SKIP_ALLOWLIST. This prevents the pattern of 'add @pytest.mark.skip to work
    around a failing eval' from silently passing CI. (Issue 265)
    """
    import re

    pattern = os.path.join(SCENARIOS_DIR, "*.yaml")
    skip_re = re.compile(r"\bskip\b|\bxfail\b", re.IGNORECASE)
    violations: list[str] = []
    for path in sorted(glob.glob(pattern)):
        stem = os.path.splitext(os.path.basename(path))[0]
        if stem in SKIP_ALLOWLIST:
            continue
        with open(path) as fh:
            content = fh.read()
        if skip_re.search(content):
            violations.append(stem)
    assert not violations, (
        f"Scenario file(s) contain 'skip' or 'xfail' markers but are NOT in "
        f"SKIP_ALLOWLIST: {violations}. Either fix the scenario or add the stem "
        "to SKIP_ALLOWLIST with a justification comment."
    )


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


# ── Issue 127: Sentence-boundary snapping ────────────────────────────────────


def test_is_sentence_end_terminal_punct():
    assert _is_sentence_end("right.")
    assert _is_sentence_end("really?")
    assert _is_sentence_end("amazing!")
    assert _is_sentence_end("wait...")


def test_is_sentence_end_non_terminal():
    assert not _is_sentence_end("and")
    assert not _is_sentence_end("the")
    assert not _is_sentence_end("")


def _words(*pairs: tuple[str, float, float]) -> list[dict]:
    """Build a word list: (word, start, end)."""
    return [{"word": w, "start": s, "end": e} for w, s, e in pairs]


def test_snap_backward_finds_terminal_punct():
    words = _words(("right.", 55.0, 57.2), ("So", 57.5, 57.8))
    result = snap_to_sentence_boundary(60.0, words, "backward")
    assert result == pytest.approx(57.2)


def test_snap_forward_finds_terminal_punct():
    words = _words(("wait", 60.5, 61.0), ("here?", 62.0, 62.8), ("Yeah", 63.0, 63.4))
    result = snap_to_sentence_boundary(60.0, words, "forward")
    assert result == pytest.approx(62.8)


def test_snap_hard_cap_not_exceeded():
    # Punct word is 5s away — beyond the default max_snap_s=3.0 — should not snap.
    words = _words(("done.", 54.0, 55.0))
    result = snap_to_sentence_boundary(60.0, words, "backward", max_snap_s=3.0)
    assert result == pytest.approx(60.0)


def test_snap_silence_fallback_backward():
    words = _words(("and", 57.0, 57.5))  # no terminal punct in window
    events = [{"type": "silence", "start_s": 57.8, "end_s": 58.5}]
    result = snap_to_sentence_boundary(
        60.0, words, "backward", min_pause_ms=400, max_snap_s=3.0, timeline_events=events
    )
    assert result == pytest.approx(58.5)


def test_snap_silence_fallback_forward():
    words = _words(("and", 60.5, 61.0))  # no terminal punct in window
    events = [{"type": "silence", "start_s": 61.5, "end_s": 62.2}]
    result = snap_to_sentence_boundary(
        60.0, words, "forward", min_pause_ms=400, max_snap_s=3.0, timeline_events=events
    )
    assert result == pytest.approx(61.5)


def test_snap_silence_too_short_ignored():
    # Silence is only 100ms — below the 400ms floor — should not be used.
    words = _words(("and", 57.0, 57.5))
    events = [{"type": "silence", "start_s": 59.0, "end_s": 59.1}]
    result = snap_to_sentence_boundary(
        60.0, words, "backward", min_pause_ms=400, max_snap_s=3.0, timeline_events=events
    )
    assert result == pytest.approx(60.0)


def test_snap_no_boundary_returns_original():
    words = _words(("and", 57.0, 57.5), ("the", 57.8, 58.1))
    result = snap_to_sentence_boundary(60.0, words, "backward")
    assert result == pytest.approx(60.0)


def test_extract_candidates_snaps_when_words_provided():
    """With a word list containing terminal punct near cut points, boundaries move."""
    tl = _make_timeline([90.0])
    # Place a sentence-ending word just before setup_start_s and just after end_s
    # so the snap has something to latch onto.
    candidates_no_snap = extract_candidates(tl, max_candidates=1)
    assert len(candidates_no_snap) == 1
    setup_no_snap = candidates_no_snap[0]["setup_start_s"]
    end_no_snap = candidates_no_snap[0]["end_s"]

    # Put a terminal-punct word 1s before setup and 1s after end — within max_snap_s=3.0.
    words = _words(
        ("done.", setup_no_snap - 1.0, setup_no_snap - 0.2),
        ("right?", end_no_snap + 0.3, end_no_snap + 1.0),
    )
    candidates_snap = extract_candidates(tl, max_candidates=1, words=words)
    assert len(candidates_snap) == 1

    # setup_start_s should have moved to the end of "done." (snapped backward)
    assert candidates_snap[0]["setup_start_s"] == pytest.approx(setup_no_snap - 0.2)
    # end_s should have moved to the end of "right?" (snapped forward)
    assert candidates_snap[0]["end_s"] == pytest.approx(end_no_snap + 1.0)


def test_extract_candidates_invariants_hold_after_snap():
    """setup_start_s < peak_s and clip length >= MIN_CLIP_S must hold after snapping."""
    tl = _make_timeline([90.0])
    words = _words(("done.", 10.0, 10.5))  # far before setup — snap will hold original
    candidates = extract_candidates(tl, max_candidates=1, words=words)
    assert len(candidates) == 1
    c = candidates[0]
    assert c["setup_start_s"] < c["peak_s"]
    assert c["end_s"] - c["setup_start_s"] >= MIN_CLIP_S


# ── Issue 103: IoU-based NMS deduplication ───────────────────────────────────


def test_candidates_dedups_overlapping_windows():
    """Two peaks 35s apart sharing a silence boundary can produce clips with IoU > 0.5.
    After NMS the lower-prominence peak must be suppressed, leaving one clip window.
    (Issue 103 fix #6, canonical NMS threshold = 0.5)
    """
    # A single silence at t=40–45s: both peaks at t=60 and t=95 will anchor to
    # setup_start_s ≈ 45. With POST_PEAK_S=20 and MIN_CLIP_S=30:
    #   Peak 60: setup=45, end=max(60+20, 45+30)=80 → window [45,80], len=35
    #   Peak 95: setup=45, end=max(95+20, 45+30)=115 → window [45,115], len=70
    # intersection=[45,80]=35, union=35+70-35=70, IoU=35/70=0.5 — right at threshold.
    # We use IoU > 0.5, so 0.5 does NOT suppress. Make the silence closer to peak 95
    # so both windows are more deeply overlapping.
    timeline = {
        "duration_s": 200.0,
        "events": [
            # A single silence: both nearby peaks lock to its end as setup_start.
            {"type": "silence", "start_s": 55.0, "end_s": 60.0},
            # Two retention spikes 35s apart — peak at 75s is stronger (higher value).
            {"type": "retention_spike", "start_s": 75.0, "end_s": 77.0, "value": 3.0},
            {"type": "retention_spike", "start_s": 110.0, "end_s": 112.0, "value": 1.5},
        ],
    }
    candidates = extract_candidates(timeline, max_candidates=8)
    # If NMS is working, the two heavily-overlapping windows collapse to 1.
    # Assert we get at most 1 candidate (the stronger peak survives).
    assert len(candidates) <= 1, (
        f"Expected ≤1 candidate after NMS, got {len(candidates)}: {candidates}"
    )


def test_nms_threshold_constant_is_canonical():
    """_NMS_IOU_THRESHOLD must be 0.5 — the canonical video-summarisation value."""
    assert _NMS_IOU_THRESHOLD == 0.5


def test_candidates_keeps_non_overlapping_windows():
    """Peaks that are far apart and do not overlap must both survive NMS."""
    tl = _make_timeline([60.0, 160.0])  # 100s apart — cannot overlap
    candidates = extract_candidates(tl, max_candidates=8)
    assert len(candidates) == 2
