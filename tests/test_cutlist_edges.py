"""Issue 330 — captions / filler / edits cut-list edge suite.

Edge cases the happy-path suites (`test_captions.py`, `test_filler.py`,
`test_edits.py`) don't cover, plus regression locks for two confirmed defects:

  - ``captions._to_ms`` crashed on a NaN/inf word timestamp
    (``int(round(nan))`` → ValueError; ``int(round(inf))`` → OverflowError),
    taking down the whole caption render for one malformed word.
  - ``edits.validate_user_cuts`` leaked a bare ``ValueError`` on non-numeric
    input instead of the typed ``CutValidationError`` the router maps to 422.

All tests are pure-function / real-file (libass-grade ASS written + reparsed via
pysubs2) — no mocks; the modules have no external dependencies.
"""

from __future__ import annotations

import logging

import pysubs2
import pytest

from clip_engine.captions import _iter_clipped_words, _to_ms, build_ass_subtitles
from clip_engine.edits import (
    CutValidationError,
    _invert_cuts,
    validate_user_cuts,
)
from clip_engine.filler import (
    CutSegment,
    detect_cut_segments,
    merge_adjacent_cuts,
    percent_removed,
)

# ── captions: _to_ms non-finite guard ─────────────────────────────────────────


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_to_ms_non_finite_returns_zero_not_crash(bad):
    """A non-finite word timestamp must not crash int(round(...))."""
    assert _to_ms(bad) == 0


def test_to_ms_normal_value_rounds_to_ms():
    assert _to_ms(1.2345) == 1234
    assert _to_ms(-5.0) == 0  # clamped at 0


# ── captions: _iter_clipped_words drops malformed words ────────────────────────


def test_iter_clipped_words_drops_inverted_word():
    """A word with end < start is dropped at the source, never yielded."""
    segments = [
        {
            "words": [
                {"word": "good", "start": 1.0, "end": 1.5},
                {"word": "bad", "start": 2.0, "end": 1.0},  # inverted
            ]
        }
    ]
    out = list(_iter_clipped_words(segments, 0.0, 10.0))
    assert [w["word"] for w in out] == ["good"]


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_iter_clipped_words_drops_non_finite(bad):
    segments = [
        {
            "words": [
                {"word": "ok", "start": 1.0, "end": 1.5},
                {"word": "nope", "start": bad, "end": bad},
            ]
        }
    ]
    out = list(_iter_clipped_words(segments, 0.0, 10.0))
    assert [w["word"] for w in out] == ["ok"]


def test_build_ass_subtitles_survives_nan_word_timestamp(tmp_path):
    """One malformed (NaN) word must not crash the render — the good word still
    produces a valid ASS file, the bad word is dropped."""
    segments = [
        {
            "start": 0.0,
            "end": 2.0,
            "text": "hello world",
            "words": [
                {"word": "hello", "start": 0.0, "end": 0.5},
                {"word": "world", "start": float("nan"), "end": float("nan")},
            ],
        }
    ]
    out = build_ass_subtitles(
        segments=segments,
        style="bold_pop",
        clip_start_s=0.0,
        clip_duration_s=2.0,
        out_path=tmp_path / "out.ass",
    )
    assert out is not None
    subs = pysubs2.load(str(out))
    # Exactly the finite word rendered; all event times are valid (start < end).
    assert len(subs.events) == 1
    assert subs.events[0].start < subs.events[0].end


# ── captions: silent-skip paths now log ────────────────────────────────────────


def test_build_ass_no_segment_overlap_returns_none_and_logs(tmp_path, caplog):
    """Segments entirely outside the clip window → None, with a logged reason."""
    segments = [
        {
            "start": 100.0,
            "end": 101.0,
            "text": "later",
            "words": [{"word": "later", "start": 100.0, "end": 101.0}],
        }
    ]
    with caplog.at_level(logging.INFO, logger="clip_engine.captions"):
        out = build_ass_subtitles(
            segments=segments,
            style="bold_pop",
            clip_start_s=0.0,
            clip_duration_s=5.0,
            out_path=tmp_path / "out.ass",
        )
    assert out is None
    assert any("no transcript segments overlap" in r.message for r in caplog.records)


def test_build_ass_all_empty_word_text_returns_none_and_logs(tmp_path, caplog):
    """Words present but all blank → no events → None, with a logged reason."""
    segments = [
        {
            "start": 0.0,
            "end": 2.0,
            "text": "   ",
            "words": [
                {"word": "  ", "start": 0.0, "end": 0.5},
                {"word": "", "start": 0.5, "end": 1.0},
            ],
        }
    ]
    with caplog.at_level(logging.INFO, logger="clip_engine.captions"):
        out = build_ass_subtitles(
            segments=segments,
            style="bold_pop",
            clip_start_s=0.0,
            clip_duration_s=2.0,
            out_path=tmp_path / "out.ass",
        )
    assert out is None
    assert any("produced no events" in r.message for r in caplog.records)


def test_build_ass_empty_segments_returns_none_and_logs(tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger="clip_engine.captions"):
        out = build_ass_subtitles(
            segments=[],
            style="bold_pop",
            clip_start_s=0.0,
            clip_duration_s=2.0,
            out_path=tmp_path / "out.ass",
        )
    assert out is None
    assert any("empty segments or non-positive duration" in r.message for r in caplog.records)


# ── filler: clip-window + duration guards ──────────────────────────────────────


def test_detect_cut_segments_inverted_clip_window_returns_empty():
    words = [{"word": "um", "start": 1.0, "end": 1.2}]
    assert detect_cut_segments(words, clip_start_s=10.0, clip_end_s=5.0) == []


def test_detect_cut_segments_empty_words_returns_empty():
    assert detect_cut_segments([], clip_start_s=0.0, clip_end_s=10.0) == []


def test_detect_cut_segments_inverted_tier2_phrase_not_cut():
    """A Tier-2 phrase whose words are time-inverted (phrase_dur <= 0) must be
    skipped by the explicit non-positive guard, never emitted as a filler cut."""
    words = [
        {"word": "you", "start": 5.0, "end": 5.1},
        {"word": "know", "start": 4.0, "end": 4.1},  # ends before "you" starts
    ]
    cuts = detect_cut_segments(words, clip_start_s=0.0, clip_end_s=10.0)
    assert [c for c in cuts if c.reason == "filler"] == []


def test_detect_cut_segments_tier1_filler_is_cut():
    """Positive control: a well-formed Tier-1 filler IS excised (so the inverted
    case above is the guard firing, not a dead detector)."""
    words = [
        {"word": "So", "start": 0.0, "end": 0.3},
        {"word": "um", "start": 0.4, "end": 0.7},
        {"word": "yeah", "start": 0.8, "end": 1.1},
    ]
    cuts = detect_cut_segments(words, clip_start_s=0.0, clip_end_s=10.0)
    assert any(c.reason == "filler" and c.word == "um" for c in cuts)


def test_percent_removed_does_not_double_count_overlaps():
    """Overlapping cuts are merged before summing — no double-count."""
    cuts = [
        CutSegment(0.0, 5.0, "silence", None),
        CutSegment(3.0, 8.0, "silence", None),  # overlaps the first
    ]
    # Merged span is [0, 8] = 8s of 10s = 80% (NOT 5+5 = 100%).
    assert percent_removed(cuts, clip_duration_s=10.0) == pytest.approx(80.0)
    assert len(merge_adjacent_cuts(cuts)) == 1


def test_percent_removed_zero_duration_is_zero():
    assert percent_removed([CutSegment(0.0, 1.0, "silence")], 0.0) == 0.0


# ── edits: typed errors, NaN/inf, clamp logging, invert robustness ─────────────


@pytest.mark.parametrize("seg", [[("a", "b")], [(None, 1.0)], [("1.0",)], [(1.0,)], ["nope"]])
def test_validate_user_cuts_non_numeric_raises_typed_error(seg):
    """Non-numeric / malformed input → CutValidationError(invalid_segment), never
    a bare ValueError/TypeError/IndexError that would surface as a 500."""
    with pytest.raises(CutValidationError) as exc:
        validate_user_cuts(seg, clip_duration_s=30.0)
    assert exc.value.code == "invalid_segment"


def test_validate_user_cuts_nan_raises_invalid_segment():
    with pytest.raises(CutValidationError) as exc:
        validate_user_cuts([(float("nan"), 5.0)], clip_duration_s=30.0)
    assert exc.value.code == "invalid_segment"


@pytest.mark.parametrize("bad", [(float("inf"), 5.0), (0.0, float("inf")), (float("-inf"), 5.0)])
def test_validate_user_cuts_infinite_bounds_rejected(bad):
    """±inf is rejected (out_of_bounds or invalid_segment), never accepted."""
    with pytest.raises(CutValidationError) as exc:
        validate_user_cuts([bad], clip_duration_s=30.0)
    assert exc.value.code in {"out_of_bounds", "invalid_segment"}


def test_validate_user_cuts_logs_right_edge_clamp(caplog):
    """A cut whose end runs one frame past the clip is clamped — and logged, so
    the silent right-edge shrink is observable."""
    with caplog.at_level(logging.DEBUG, logger="clip_engine.edits"):
        # A small tail cut that runs one frame past the clip: clamps to 30.0 but
        # only removes ~2s (well under the removed-too-much cap).
        result = validate_user_cuts([(28.0, 30.02)], clip_duration_s=30.0)
    assert result.cut_segments[0][1] == pytest.approx(30.0)
    assert any("clamping cut end" in r.message for r in caplog.records)


def test_validate_user_cuts_unsorted_input_is_sorted():
    """Unsorted segments are normalised (sorted) before inversion, so _invert_cuts
    only ever receives clean input."""
    result = validate_user_cuts([(20.0, 25.0), (5.0, 10.0)], clip_duration_s=30.0)
    assert result.cut_segments == [(5.0, 10.0), (20.0, 25.0)]
    # keep_ranges fill the gaps, all strictly increasing.
    assert all(e > s for s, e in result.keep_ranges)


def test_validate_user_cuts_overlap_rejected():
    with pytest.raises(CutValidationError) as exc:
        validate_user_cuts([(0.0, 10.0), (5.0, 15.0)], clip_duration_s=30.0)
    assert exc.value.code == "overlap"


def test_invert_cuts_adjacent_emits_no_zero_width_keep():
    """Directly exercise _invert_cuts with adjacent cuts: the defensive
    cursor=max(cursor,end) must not emit a zero/negative-width keep range."""
    keep = _invert_cuts([(0.0, 5.0), (5.0, 10.0)], clip_duration_s=15.0)
    assert keep == [(10.0, 15.0)]
    assert all(e > s for s, e in keep)


def test_invert_cuts_overlapping_input_defensive():
    """Even with overlapping input (which validate_user_cuts would reject upstream),
    _invert_cuts must not produce a reversed keep range."""
    keep = _invert_cuts([(0.0, 6.0), (4.0, 10.0)], clip_duration_s=15.0)
    assert all(e > s for s, e in keep)
