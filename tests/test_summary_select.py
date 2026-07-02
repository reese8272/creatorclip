"""Golden-selection tests for clip_engine/summary_select.py (Issue 190).

Pure-logic tests — no DB, no LLM, no Anthropic. Principles cited are exact
names from docs/CLIPPING_PRINCIPLES.md.
"""

from typing import Any

from clip_engine.summary_select import select_recap_segments


def _cand(
    start_s: float,
    end_s: float,
    score: float,
    principle: str = "Retention curve is ground truth",
    rationale: str = "rewatch spike",
) -> dict[str, Any]:
    return {
        "start_s": start_s,
        "end_s": end_s,
        "score": score,
        "principle": principle,
        "rationale": rationale,
    }


GOLDEN = [
    _cand(120.0, 210.0, 0.71, "Hook in the first 3 seconds"),
    _cand(900.0, 1020.0, 0.64, "Tension and release"),
    _cand(2400.0, 2520.0, 0.88),
    _cand(2460.0, 2580.0, 0.55, "Tension and release"),  # overlaps previous
    _cand(4100.0, 4220.0, 0.92),
    _cand(6000.0, 6180.0, 0.77, "Audience-fit over generic virality"),
]


def test_golden_selection_budget_overlap_order_and_principles() -> None:
    """Happy path: budget respected, no overlap, chronological, principle carried."""
    segments = select_recap_segments(GOLDEN, budget_s=600.0)

    assert segments, "expected a non-empty selection"
    total = sum(s["end_s"] - s["start_s"] for s in segments)
    assert total <= 600.0

    for a, b in zip(segments, segments[1:], strict=False):
        assert a["end_s"] <= b["start_s"], "segments must not overlap"
    starts = [s["start_s"] for s in segments]
    assert starts == sorted(starts), "segments must be chronological"

    # Of the two overlapping beats only the stronger survives.
    assert any(s["start_s"] == 2400.0 for s in segments)
    assert not any(s["start_s"] == 2460.0 for s in segments)

    # Element shape consumed by the Issue 191 renderer, principle carried verbatim.
    for s in segments:
        assert set(s) == {"start_s", "end_s", "score", "principle", "rationale"}
    by_start = {s["start_s"]: s for s in segments}
    assert by_start[120.0]["principle"] == "Hook in the first 3 seconds"
    assert by_start[120.0]["rationale"] == "rewatch spike"


def test_empty_and_short_source_fallback() -> None:
    """Empty input, zero-length, over-budget-only, and zero-score inputs return []."""
    assert select_recap_segments([], budget_s=600.0) == []
    assert select_recap_segments([_cand(10.0, 10.0, 0.9)], budget_s=600.0) == []
    assert select_recap_segments([_cand(0.0, 700.0, 0.9)], budget_s=600.0) == []
    assert select_recap_segments([_cand(0.0, 30.0, 0.0)], budget_s=600.0) == []


def test_ratio_greedy_beats_plain_score_greedy() -> None:
    """Two short dense segments outscore one long high-score segment the plain
    pass would pick — the max-of-both must return the ratio solution."""
    long_hog = _cand(0.0, 100.0, 10.0)
    dense_a = _cand(200.0, 240.0, 6.0, "Tension and release")
    dense_b = _cand(300.0, 340.0, 6.0, "Pattern interrupt")

    segments = select_recap_segments([long_hog, dense_a, dense_b], budget_s=100.0)

    assert [s["start_s"] for s in segments] == [200.0, 300.0]


def test_plain_score_greedy_beats_ratio_greedy() -> None:
    """A tiny high-ratio segment starves the budget for the dominant beat under
    the ratio pass — the max-of-both must return the plain-score solution."""
    dominant = _cand(0.0, 100.0, 10.0)
    tiny = _cand(200.0, 205.0, 1.0, "Dead-air elimination")

    segments = select_recap_segments([dominant, tiny], budget_s=100.0)

    assert [s["start_s"] for s in segments] == [0.0]


def test_chapter_straddle_demotes_but_never_mutates_score() -> None:
    """With a chapter boundary mid-segment, an in-chapter near-peer wins the
    slot; the reported score of any chosen straddler is never altered."""
    straddler = _cand(3550.0, 3700.0, 0.80, "Pattern interrupt")
    in_chapter = _cand(3400.0, 3550.0, 0.75, "Tension and release")
    chapters = [
        {"timestamp_s": 0.0, "title": "Intro"},
        {"timestamp_s": 3600.0, "title": "Main event"},
    ]

    # Budget fits only one of the two overlappingly-close beats.
    segments = select_recap_segments([straddler, in_chapter], budget_s=160.0)
    assert [s["start_s"] for s in segments] == [3550.0], "sanity: straddler wins w/o chapters"

    segments = select_recap_segments([straddler, in_chapter], budget_s=160.0, chapters=chapters)
    assert [s["start_s"] for s in segments] == [3400.0], "in-chapter beat must win the slot"

    # A chosen straddler still reports its original score (0.80 * 0.8 = 0.64 never leaks).
    segments = select_recap_segments([straddler], budget_s=600.0, chapters=chapters)
    assert segments[0]["score"] == 0.80
