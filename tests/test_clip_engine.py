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
from clip_engine.ranking import rank_candidates
from clip_engine.summary_select import select_recap_segments
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
# never lower it. (Issue 265; raised 6 → 14 when the adversarial scenarios landed — Issue 199;
# 14 → 15 when the stream-recap budget scenario landed — Issue 190;
# 15 → 18 when the three kind:merge hybrid-candidate scenarios landed — Issue 416;
# 18 → 21 when the mid-sentence-open, LLM-length-clamp, and contained-duplicate
# scenarios landed — Issues 428/429)
SCENARIO_FLOOR = 23

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


# Exact principle names from docs/CLIPPING_PRINCIPLES.md — recap segments must
# cite one of these verbatim (same contract as clips). (Issue 190)
NAMED_PRINCIPLES = frozenset(
    {
        "Hook in the first 3 seconds",
        "Clip the setup, not the aftermath",
        "Tension and release",
        "Pattern interrupt",
        "Dead-air elimination",
        "Retention curve is ground truth",
        "Loop-ability",
        "Front-load value",
        "One idea per Short",
        "Native length over generic length",
        "Audience-fit over generic virality",
        "Clean Context Boundary",
    }
)


def _assert_recap_scenario(scenario: dict) -> None:
    """Recap-kind scenario assertions (Issue 190): total-budget compliance,
    pairwise non-overlap, chronological (narrative) order, named principles."""
    inp = scenario["input"]
    expected = scenario.get("expected", {})
    segments = select_recap_segments(
        inp["candidates"], budget_s=float(inp["budget_s"]), chapters=inp.get("chapters")
    )
    name = scenario["scenario"]

    total = sum(s["end_s"] - s["start_s"] for s in segments)
    max_total = expected.get("max_total_duration_s", inp["budget_s"])
    assert total <= max_total, f"[{name}] total duration {total:.1f}s exceeds budget {max_total}s"

    min_segments = expected.get("min_segments", 0)
    assert len(segments) >= min_segments, (
        f"[{name}] expected >= {min_segments} segments, got {len(segments)}"
    )

    if expected.get("no_overlap", True):
        for a, b in zip(segments, segments[1:], strict=False):
            assert a["end_s"] <= b["start_s"], (
                f"[{name}] segments overlap: ({a['start_s']},{a['end_s']}) vs "
                f"({b['start_s']},{b['end_s']})"
            )

    if expected.get("chronological", True):
        starts = [s["start_s"] for s in segments]
        assert starts == sorted(starts), f"[{name}] segments not in chronological order: {starts}"

    if expected.get("all_principles_named", True):
        for s in segments:
            assert s["principle"] in NAMED_PRINCIPLES, (
                f"[{name}] segment ({s['start_s']},{s['end_s']}) cites unknown principle "
                f"{s['principle']!r} — must be an exact name from docs/CLIPPING_PRINCIPLES.md"
            )


def _assert_merge_scenario(scenario: dict) -> None:
    """Merge-kind scenario assertions (Issue 416): raw moments flow through
    validate_context (bounds / principle registry / cap), become llm-origin
    candidates, and union with the signal candidates under signal-priority NMS.
    Asserts pool counts, per-candidate window expectations, and the global
    geometry invariants on every merged candidate."""
    from analysis.video_context import validate_context
    from clip_engine.candidates import MIN_CLIP_S
    from clip_engine.merge import llm_moments_to_candidates, merge_candidates

    inp = scenario["input"]
    expected = scenario.get("expected", {})
    name = scenario["scenario"]
    timeline = inp["timeline"]
    duration_s = float(timeline.get("duration_s", 0.0))

    moments = validate_context({"moments": inp.get("moments", [])}, duration_s)["moments"]
    signal_cands = extract_candidates(timeline, max_candidates=8)
    llm_cands = llm_moments_to_candidates(moments, timeline)
    merged = merge_candidates(signal_cands, llm_cands)

    llm_in_merged = [c for c in merged if c.get("origin") == "llm"]
    if "llm_candidates" in expected:
        assert len(llm_in_merged) == expected["llm_candidates"], (
            f"[{name}] expected {expected['llm_candidates']} llm-origin candidate(s) "
            f"in the merged pool, got {len(llm_in_merged)}"
        )
    if "min_total" in expected:
        assert len(merged) >= expected["min_total"], (
            f"[{name}] merged pool {len(merged)} < expected min {expected['min_total']}"
        )
    if "max_total" in expected:
        assert len(merged) <= expected["max_total"], (
            f"[{name}] merged pool {len(merged)} > expected max {expected['max_total']}"
        )

    # Global invariants: every merged candidate keeps the candidates.py geometry.
    for c in merged:
        assert c["setup_start_s"] < c["peak_s"], (
            f"[{name}] setup_start_s={c['setup_start_s']} >= peak_s={c['peak_s']}"
        )
        assert c["end_s"] - c["setup_start_s"] >= MIN_CLIP_S - 1e-6, (
            f"[{name}] window ({c['setup_start_s']},{c['end_s']}) shorter than MIN_CLIP_S"
        )
        assert c["end_s"] <= duration_s + 1e-6, (
            f"[{name}] end_s={c['end_s']} runs past duration {duration_s}"
        )
    starts = [c["setup_start_s"] for c in merged]
    assert starts == sorted(starts), f"[{name}] merged pool not chronological: {starts}"

    # Per-candidate window expectations, matched within the expected origin pool.
    for exp_c in expected.get("candidates", []):
        pool = llm_in_merged if exp_c.get("origin") == "llm" else merged
        assert pool, f"[{name}] no candidates in the {exp_c.get('origin', 'any')} pool"
        anchor = exp_c.get("setup_start_s_min", exp_c.get("setup_start_s_max", 0.0))
        matched = min(pool, key=lambda c: abs(c["setup_start_s"] - anchor))
        if "setup_start_s_min" in exp_c:
            assert matched["setup_start_s"] >= exp_c["setup_start_s_min"], (
                f"[{name}] setup_start_s={matched['setup_start_s']} < {exp_c['setup_start_s_min']}"
            )
        if "setup_start_s_max" in exp_c:
            assert matched["setup_start_s"] <= exp_c["setup_start_s_max"], (
                f"[{name}] setup_start_s={matched['setup_start_s']} > {exp_c['setup_start_s_max']}"
            )
        if "end_s_min" in exp_c:
            assert matched["end_s"] >= exp_c["end_s_min"], (
                f"[{name}] end_s={matched['end_s']} < {exp_c['end_s_min']}"
            )
        if "end_s_max" in exp_c:
            assert matched["end_s"] <= exp_c["end_s_max"], (
                f"[{name}] end_s={matched['end_s']} > {exp_c['end_s_max']}"
            )
        if "len_s_max" in exp_c:
            length = matched["end_s"] - matched["setup_start_s"]
            assert length <= exp_c["len_s_max"] + 1e-6, (
                f"[{name}] window length {length:.1f}s > {exp_c['len_s_max']}s — "
                "LLM window escaped the CLIP_TARGET_MAX_S clamp"
            )


def _assert_snap_scenario(scenario: dict) -> None:
    """Snap-kind scenario assertions (Issue 428): signal candidates flow through
    the segment-aware sentence pass and must open on sentence boundaries — never
    strictly inside a sentence (the meaning-inverting mid-sentence cut)."""
    from clip_engine.sentence_snap import (
        _containing_index,
        build_sentence_index,
        snap_candidates_to_sentences,
    )

    inp = scenario["input"]
    expected = scenario.get("expected", {})
    name = scenario["scenario"]
    timeline = inp["timeline"]
    segments = inp.get("segments", [])
    duration_s = float(timeline.get("duration_s", 0.0))

    candidates = snap_candidates_to_sentences(
        extract_candidates(timeline, max_candidates=8), segments, duration_s
    )

    min_c = expected.get("min_candidates", 0)
    assert len(candidates) >= min_c, f"[{name}] expected >= {min_c} candidates, got {len(candidates)}"

    if expected.get("starts_on_sentence_start", False):
        sentences = build_sentence_index(segments)
        for c in candidates:
            assert _containing_index(c["setup_start_s"], sentences) is None, (
                f"[{name}] setup_start_s={c['setup_start_s']} opens strictly inside a "
                "sentence — the clip starts mid-sentence"
            )

    if expected.get("opens_on_content_word", False):
        # Issue 441: `starts_on_sentence_start` cannot express this. Deepgram
        # splits utterances on PAUSES, so "because they still don't know…" IS a
        # sentence start — a clean boundary onto a clause that cannot stand
        # alone. This asserts the OPENING TOKEN can carry a cold open.
        from clip_engine.sentence_snap import is_weak_opener

        sentences = build_sentence_index(segments)
        for c in candidates:
            opener = next(
                (
                    s.get("first_word")
                    for s in sentences
                    if s["start_s"] >= c["setup_start_s"] - 0.35
                ),
                None,
            )
            assert not is_weak_opener(opener), (
                f"[{name}] setup_start_s={c['setup_start_s']} opens on {opener!r} — a "
                "subordinating conjunction or discourse marker cannot open a clip"
            )

    for exp_c in expected.get("candidates", []):
        anchor = exp_c.get("setup_start_s_min", exp_c.get("setup_start_s_max", 0.0))
        assert candidates, f"[{name}] no candidates to match against"
        matched = min(candidates, key=lambda c: abs(c["setup_start_s"] - anchor))
        for key, op, msg in (
            ("setup_start_s_min", lambda v, e: v >= e, "<"),
            ("setup_start_s_max", lambda v, e: v <= e, ">"),
            ("end_s_min", lambda v, e: v >= e, "<"),
            ("end_s_max", lambda v, e: v <= e, ">"),
        ):
            if key in exp_c:
                field = key.rsplit("_", 1)[0]
                assert op(matched[field], exp_c[key]), (
                    f"[{name}] {field}={matched[field]} {msg} expected {exp_c[key]}"
                )


def _assert_containment_scenario(scenario: dict) -> None:
    """Containment-kind scenario assertions (Issue 429): pre-scored candidates
    flow through rank → suppress_contained; a (near-)contained lower-ranked
    window must not survive to the persisted set."""
    from clip_engine.ranking import rank_candidates, suppress_contained

    inp = scenario["input"]
    expected = scenario.get("expected", {})
    name = scenario["scenario"]

    survivors = suppress_contained(rank_candidates([dict(c) for c in inp["candidates"]]))

    if "survivors" in expected:
        assert len(survivors) == expected["survivors"], (
            f"[{name}] expected {expected['survivors']} survivor(s), got {len(survivors)} — "
            "a contained near-duplicate was rendered alongside its container"
        )
    assert [c["rank"] for c in survivors] == list(range(1, len(survivors) + 1)), (
        f"[{name}] survivor ranks not dense: {[c['rank'] for c in survivors]}"
    )

    if "max_pairwise_overlap_s" in expected:
        # Issue 441: no ratio can express this. The live pairs scored IoMin 0.419
        # and 0.27 — BELOW the 0.8 containment threshold, which sits above the
        # 0.67 ceiling on purpose — while sharing 17.4 s and 9.1 s of speech.
        from clip_engine.ranking import window_overlap_s

        limit = float(expected["max_pairwise_overlap_s"])
        for i, a in enumerate(survivors):
            for b in survivors[i + 1 :]:
                shared = window_overlap_s(a, b)
                assert shared <= limit, (
                    f"[{name}] survivors [{a['setup_start_s']}, {a['end_s']}] and "
                    f"[{b['setup_start_s']}, {b['end_s']}] share {shared:.1f}s of speech "
                    f"(limit {limit}s) — two clips telling the same story"
                )
    for exp_c in expected.get("candidates", []):
        anchor = exp_c.get("setup_start_s_min", exp_c.get("setup_start_s_max", 0.0))
        assert survivors, f"[{name}] no survivors to match against"
        matched = min(survivors, key=lambda c: abs(c["setup_start_s"] - anchor))
        if "setup_start_s_min" in exp_c:
            assert matched["setup_start_s"] >= exp_c["setup_start_s_min"]
        if "setup_start_s_max" in exp_c:
            assert matched["setup_start_s"] <= exp_c["setup_start_s_max"]
        if "origin" in exp_c:
            assert matched.get("origin", "signal") == exp_c["origin"], (
                f"[{name}] surviving candidate origin {matched.get('origin', 'signal')!r} "
                f"!= expected {exp_c['origin']!r}"
            )


def _assert_scenario(scenario: dict) -> None:
    """Run all geometry assertions for one loaded scenario. Raises AssertionError on
    any violation. Shared by the per-scenario test and the aggregate pass-rate gate."""
    if scenario.get("kind") == "recap":
        _assert_recap_scenario(scenario)
        return
    if scenario.get("kind") == "merge":
        _assert_merge_scenario(scenario)
        return
    if scenario.get("kind") == "snap":
        _assert_snap_scenario(scenario)
        return
    if scenario.get("kind") == "containment":
        _assert_containment_scenario(scenario)
        return
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


@pytest.mark.parametrize("scenario_path", _load_scenarios())
def test_eval_scenario(scenario_path):
    with open(scenario_path) as f:
        scenario = yaml.safe_load(f)
    _assert_scenario(scenario)


def test_eval_scenario_pass_rate():
    """Aggregate clip-quality gate (Issue 199): every geometry scenario must pass.

    Per-scenario failures are also caught individually by test_eval_scenario, but this
    reports the harness as a single ratchet-able number — a 100% pass-rate is the
    pre-launch 'eval harness hardened' gate. If a scenario regresses, this names every
    failing fixture at once rather than stopping at the first.
    """
    scenarios = _load_scenarios()
    failures: list[str] = []
    for param in scenarios:
        path = param.values[0]
        stem = os.path.splitext(os.path.basename(path))[0]
        with open(path) as f:
            scenario = yaml.safe_load(f)
        try:
            _assert_scenario(scenario)
        except AssertionError as exc:
            failures.append(f"{stem}: {exc}")
    pass_rate = (len(scenarios) - len(failures)) / len(scenarios) if scenarios else 0.0
    assert pass_rate == 1.0, (
        f"Geometry scenario pass-rate {pass_rate:.0%} (< 100%). Failing scenarios:\n"
        + "\n".join(failures)
    )


def test_ranking_dna_preferred_ranks_first():
    """Ranking-aware fixture (Issue 199): with recorded/stubbed scores (no live
    Anthropic), the production rank_candidates() sort must place the DNA-preferred
    candidate at rank #1. Pins 'ranking reflects DNA fit' independent of geometry."""
    path = os.path.join(SCENARIOS_DIR, "ranking", "ranking_dna_preferred_first.yaml")
    with open(path) as f:
        fx = yaml.safe_load(f)
    ranked = rank_candidates([dict(c) for c in fx["candidates"]])
    assert ranked[0]["id"] == fx["expected_top_id"], (
        f"expected {fx['expected_top_id']!r} at rank 1, got {ranked[0]['id']!r}"
    )
    assert ranked[0]["rank"] == 1
    # Ranks are assigned in descending score order (1 = best).
    assert [c["rank"] for c in ranked] == list(range(1, len(ranked) + 1))
    scores = [c["score"] for c in ranked]
    assert scores == sorted(scores, reverse=True)


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


def test_extract_candidates_end_clamped_to_duration():
    """Regression for the end_s clamp: transcript word `end` values can exceed the
    container duration (encoder/transcriber rounding), and a forward snap latching
    onto such a word must be clamped back to duration_s — render.py rejects any
    end_s > source duration.

    Geometry: peak at 90.0, duration 111.0 → setup=75.0 (silence end), pre-snap
    end_s=110.0. The terminal-punct word ending at 112.5 sits inside the forward
    snap window [110.0, 113.0], so end_s snaps to 112.5 > duration_s and must be
    clamped to exactly 111.0.
    """
    tl = _make_timeline([90.0], duration_s=111.0)
    words = _words(("over.", 111.8, 112.5))
    candidates = extract_candidates(tl, max_candidates=1, words=words)
    assert len(candidates) == 1
    assert candidates[0]["end_s"] == pytest.approx(111.0)
    assert candidates[0]["end_s"] <= tl["duration_s"]


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
