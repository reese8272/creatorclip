"""Issue 328 — clip-engine geometry / scoring-feature / ranking edge cases.

Locks the behaviour of the ranking NaN guard, the cold-start feature computation on
malformed windows, and the post-snap geometry invariants of candidate extraction.
Pure functions — no DB/Redis, default unit lane.
"""

from __future__ import annotations

import math

from clip_engine.candidates import MIN_CLIP_S, extract_candidates
from clip_engine.ranking import _safe_score, rank_candidates
from clip_engine.scoring import compute_features

# ── rank_candidates: deterministic order even with NaN/missing scores ─────────


def test_safe_score_maps_nan_and_garbage_to_neg_inf():
    assert _safe_score(0.7) == 0.7
    assert _safe_score(float("nan")) == float("-inf")
    assert _safe_score(float("inf")) == float("-inf")
    assert _safe_score(None) == float("-inf")
    assert _safe_score("not a number") == float("-inf")


def test_rank_candidates_nan_score_sorts_last_deterministically():
    cands = [
        {"id": "a", "score": 0.5},
        {"id": "b", "score": float("nan")},
        {"id": "c", "score": 0.9},
    ]
    ranked = rank_candidates(cands)
    # NaN must never float to rank 1; highest finite score wins, NaN sinks to last.
    assert ranked[0]["id"] == "c" and ranked[0]["rank"] == 1
    assert ranked[1]["id"] == "a"
    assert ranked[-1]["id"] == "b" and ranked[-1]["rank"] == 3


def test_rank_candidates_empty_and_missing_score():
    assert rank_candidates([]) == []
    ranked = rank_candidates([{"id": "x"}, {"id": "y", "score": 0.2}])
    assert ranked[0]["id"] == "y"  # the one with a real score ranks first


# ── compute_features on malformed windows degrades to finite zeros ────────────


def _timeline(duration_s=120.0):
    return {
        "duration_s": duration_s,
        "events": [
            {"type": "energy_spike", "start_s": 40.0, "end_s": 41.0, "value": 0.9},
            {"type": "retention_spike", "start_s": 45.0},
        ],
    }


def test_compute_features_inverted_window_is_finite():
    # setup_start_s > end_s → empty slices; must return finite, in-range features
    # (cold-start path), never NaN.
    cand = {"setup_start_s": 80.0, "end_s": 50.0, "peak_s": 45.0}
    feats = compute_features(cand, _timeline())
    for k, v in feats.items():
        if isinstance(v, float):
            assert math.isfinite(v), f"{k} is non-finite: {v}"
    assert feats["signal_density"] == 0.0
    assert feats["hook_energy"] == 0.0


def test_compute_features_empty_timeline_zeroed():
    feats = compute_features(
        {"setup_start_s": 0.0, "end_s": 30.0, "peak_s": 10.0},
        {"duration_s": 0.0, "events": []},
    )
    assert feats["signal_density"] == 0.0
    assert feats["clip_duration_s"] == 0.0


# ── extract_candidates: post-snap geometry invariants hold ────────────────────


def _peaky_timeline():
    """A timeline with clear, well-separated retention peaks → multiple candidates."""
    events = []
    for t in (30.0, 120.0, 210.0):
        events.append({"type": "silence", "start_s": t - 5.0, "end_s": t - 4.0})
        events.append({"type": "retention_spike", "start_s": t})
        events.append({"type": "energy_spike", "start_s": t - 2.0, "end_s": t, "value": 1.0})
    return {"duration_s": 260.0, "events": events}


def test_extract_candidates_invariants_without_words():
    cands = extract_candidates(_peaky_timeline(), max_candidates=8)
    assert cands, "expected at least one candidate from a peaky timeline"
    for c in cands:
        assert c["setup_start_s"] < c["peak_s"] < c["end_s"]
        assert c["end_s"] - c["setup_start_s"] >= MIN_CLIP_S - 1e-6
        assert c["start_s"] >= 0.0


def test_extract_candidates_invariants_with_adversarial_words():
    # Words include terminal punctuation near the cut points so snapping engages;
    # the post-snap invariant (setup < peak, clip >= MIN_CLIP_S) must still hold.
    words = [
        {"word": "setup.", "start": 24.0, "end": 24.5},
        {"word": "punch!", "start": 31.0, "end": 31.5},
        {"word": "done.", "start": 118.0, "end": 118.5},
        {"word": "end?", "start": 215.0, "end": 215.5},
    ]
    cands = extract_candidates(_peaky_timeline(), max_candidates=8, words=words)
    for c in cands:
        assert c["setup_start_s"] < c["peak_s"], c
        assert c["end_s"] - c["setup_start_s"] >= MIN_CLIP_S - 1e-6, c


def test_extract_candidates_empty_and_flat_return_empty():
    assert extract_candidates({"duration_s": 0.0, "events": []}) == []
    # A flat timeline (no events above the prominence threshold) yields no peaks.
    assert extract_candidates({"duration_s": 100.0, "events": []}) == []
