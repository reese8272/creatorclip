"""Unit tests for clip_engine/sentence_snap.py (Issue 428).

Covers the sentence-index build from Deepgram-shaped segments, the bounded
never-start-mid-sentence guard (the meaning-inverting live cut), the length
clamp for LLM windows, and the invariant-repair tail (Issues 456 + 449:
bounded backward snapping, the post-snap peak invariant, the degenerate
no-utterance index, and the pause-before-weak-opener walk-back).
"""

import logging

from clip_engine.sentence_snap import (
    CLIP_TARGET_MAX_S,
    SENTENCE_SNAP_MAX_S,
    build_sentence_index,
    clamp_window_to_target,
    snap_candidates_to_sentences,
    snap_end,
    snap_start,
)


def _words(specs: list[tuple[str, float, float]]) -> list[dict]:
    return [{"word": w, "start": s, "end": e} for w, s, e in specs]


def _segment(start: float, end: float, specs: list[tuple[str, float, float]]) -> dict:
    return {
        "start": start,
        "end": end,
        "text": " ".join(w for w, _, _ in specs),
        "words": _words(specs),
    }


# The live defect geometry: previous sentence ends at 71.2, the negation
# sentence "I don't really think it's gonna happen." spans 71.5–78.5, and the
# raw setup lands at 76.0 — 4.8 s into the sentence, beyond the legacy 3 s
# word-snap cap.
NEGATION_SEGMENTS = [
    _segment(
        60.0,
        78.5,
        [
            ("So", 60.0, 60.4),
            ("that's", 60.5, 60.9),
            ("the", 61.0, 61.2),
            ("situation.", 61.3, 71.2),
            ("I", 71.5, 71.7),
            ("don't", 71.8, 72.3),
            ("really", 72.4, 76.2),
            ("think", 76.3, 76.9),
            ("it's", 77.0, 77.3),
            ("gonna", 77.4, 77.8),
            ("happen.", 77.9, 78.5),
        ],
    ),
    _segment(
        79.0,
        95.0,
        [
            ("But", 79.0, 79.3),
            ("here's", 79.4, 79.8),
            ("the", 79.9, 80.0),
            ("thing.", 80.1, 95.0),
        ],
    ),
]


def test_build_index_splits_on_terminal_punct():
    sentences = build_sentence_index(NEGATION_SEGMENTS)
    assert [(s["start_s"], s["end_s"]) for s in sentences] == [
        (60.0, 71.2),
        (71.5, 78.5),
        (79.0, 95.0),
    ]


def test_build_index_wordless_segment_is_one_sentence():
    sentences = build_sentence_index([{"start": 10.0, "end": 20.0, "text": "hi"}])
    # `first_word` carries the opening token so snap_start can reject a start
    # that opens on a dependent clause (Issue 441); a wordless segment falls
    # back to the first token of its text.
    assert sentences == [{"start_s": 10.0, "end_s": 20.0, "first_word": "hi"}]


def test_build_index_unterminated_sentence_closed_by_utterance_boundary():
    seg = _segment(0.0, 5.0, [("well", 0.0, 0.5), ("I", 0.6, 0.8), ("mean", 0.9, 5.0)])
    sentences = build_sentence_index([seg])
    assert sentences == [{"start_s": 0.0, "end_s": 5.0, "first_word": "well"}]


def test_snap_start_backward_beyond_legacy_cap_preserves_negation():
    sentences = build_sentence_index(NEGATION_SEGMENTS)
    # 4.8 s back — the legacy word-level snap (3 s cap) left this mid-sentence.
    snapped = snap_start(76.0, sentences)
    # Sentence start 71.5, lead-in 0.3 floored at the previous sentence end 71.2.
    assert snapped == 71.2


def test_snap_start_backward_preferred_even_when_forward_is_nearer():
    sentences = build_sentence_index(NEGATION_SEGMENTS)
    # t = 78.0 is 6.5 s past its sentence start but only 1.0 s before the next
    # sentence — backward still wins within the radius: the containing sentence
    # is setup content, and forward snapping would discard it.
    snapped = snap_start(78.0, sentences, forward_limit_s=100.0)
    assert snapped == 71.2


def test_snap_start_forward_invalid_falls_back_to_backward_guard():
    sentences = build_sentence_index(NEGATION_SEGMENTS)
    # Forward target 79.0 exceeds the invariant ceiling — backward wins even
    # though the distance is larger. The start is NEVER left mid-sentence.
    snapped = snap_start(78.0, sentences, forward_limit_s=78.0)
    assert snapped == 71.2


def test_snap_start_run_on_sentence_prefers_forward():
    long_seg = _segment(
        0.0,
        40.0,
        [
            ("this", 0.0, 0.4),
            ("just", 0.5, 20.0),
            ("goes", 20.1, 30.0),
            ("on.", 30.1, 31.0),
            ("Next", 32.0, 32.4),
            ("one.", 32.5, 40.0),
        ],
    )
    sentences = build_sentence_index([long_seg])
    # t = 25.0 is 25 s into a run-on sentence (> SENTENCE_SNAP_MAX_S back);
    # forward to 32.0 (7 s away, within the radius) is preferred.
    assert SENTENCE_SNAP_MAX_S < 25.0 - 0.0
    snapped = snap_start(25.0, sentences, forward_limit_s=100.0)
    assert snapped == 31.7  # 32.0 - 0.3, floored at prev end 31.0


def test_snap_start_clean_boundary_unchanged():
    sentences = build_sentence_index(NEGATION_SEGMENTS)
    assert snap_start(71.5, sentences) == 71.5  # exactly on a sentence start
    assert snap_start(78.7, sentences) == 78.7  # inside the inter-sentence pause


# ── Issue 441: a clip may not open on a dependent clause ─────────────────────

# The live geometry from video 3b6992fe rank 2/3: rank 3 ends "…I worry about
# this room some **because**" and rank 2 opens "**because** they still don't
# know what Mikey is." Deepgram splits utterances on PAUSES, not grammar, so the
# "because…" utterance is a first-class sentence start and snapping to it used
# to be a no-op.
DEPENDENT_OPEN_SEGMENTS = [
    _segment(
        14.0,
        20.0,
        [("I", 14.0, 14.3), ("worry", 14.4, 14.9), ("about", 15.0, 19.0), ("this.", 19.1, 20.0)],
    ),
    _segment(
        20.5,
        30.0,
        [("because", 20.5, 21.0), ("they", 21.1, 21.4), ("moved.", 21.5, 30.0)],
    ),
]


def test_snap_start_walks_back_off_a_subordinating_conjunction():
    sentences = build_sentence_index(DEPENDENT_OPEN_SEGMENTS)
    # A start landing exactly on the "because…" utterance boundary used to be
    # returned untouched — it IS a sentence start, just not a usable one. The
    # walk-back lands on the START of the previous sentence, so the main clause
    # the "because" depends on is inside the clip.
    assert snap_start(20.5, sentences) == 13.7  # 14.0 - 0.3 lead-in


def test_snap_start_walks_back_off_a_discourse_marker():
    segments = [
        _segment(0.0, 9.0, [("The", 0.0, 0.3), ("room", 0.4, 8.0), ("improved.", 8.1, 9.0)]),
        _segment(9.5, 18.0, [("Yeah,", 9.5, 9.9), ("they've", 10.0, 10.4), ("been.", 10.5, 18.0)]),
    ]
    sentences = build_sentence_index(segments)
    assert snap_start(9.5, sentences) == 0.0  # back to the start of "The room improved."


def test_snap_start_leaves_a_coordinator_alone():
    """ "But here's the thing." is a punchy opening, not a broken one — and none
    of the live failures were coordinator-initial. Over-rejecting drags starts
    earlier and can collapse two candidates onto one opening."""
    sentences = build_sentence_index(NEGATION_SEGMENTS)
    assert snap_start(79.0, sentences) == 79.0


def test_snap_start_walk_back_respects_the_time_budget():
    """A dependent opener more than max_snap_s from the previous sentence start
    keeps the nearest sentence start rather than walking arbitrarily far."""
    sentences = build_sentence_index(DEPENDENT_OPEN_SEGMENTS)
    # Refused: only the 0.3 s lead-in breath is applied to the original start.
    assert snap_start(20.5, sentences, max_snap_s=0.1) == 20.2


def test_weak_opener_classification():
    from clip_engine.sentence_snap import is_weak_opener

    assert is_weak_opener("Because")
    assert is_weak_opener("  yeah,  ")  # punctuation + case + whitespace normalized
    assert is_weak_opener("When")
    assert not is_weak_opener("But")
    assert not is_weak_opener("Commanders")
    assert not is_weak_opener(None)


def test_snap_end_finishes_sentence_in_progress():
    sentences = build_sentence_index(NEGATION_SEGMENTS)
    assert snap_end(75.0, sentences) == 78.5


def test_snap_end_backward_when_forward_too_far():
    sentences = build_sentence_index(NEGATION_SEGMENTS)
    # t = 81.0 sits inside the 79.0–95.0 sentence: forward end is 14 s away
    # (beyond the radius), the previous sentence end 78.5 is 2.5 s back.
    assert snap_end(81.0, sentences) == 78.5


def test_snap_end_unchanged_when_no_boundary_in_range():
    sentences = build_sentence_index(NEGATION_SEGMENTS)
    # t = 87.0: forward end 8 s away? 95.0 - 87.0 = 8 <= 10 → snaps forward.
    # Use a mid-point where both directions exceed the radius.
    assert snap_end(89.5, sentences) == 95.0  # still within 10 s forward
    wide = build_sentence_index(
        [_segment(0.0, 60.0, [("a", 0.0, 0.2), ("long", 0.3, 59.0), ("one.", 59.1, 60.0)])]
    )
    assert snap_end(30.0, wide) == 30.0


def test_snap_candidates_repairs_invariants():
    candidates = [
        # Mid-sentence start snaps back to 71.2; end 108 stays (no sentence there).
        {"setup_start_s": 76.0, "start_s": 40.0, "peak_s": 90.0, "end_s": 108.0},
        # Start snaps to 79.0 - 0.3 lead-in floored at 78.5 → 78.7; end clamps
        # to the container duration.
        {"setup_start_s": 80.5, "start_s": 60.0, "peak_s": 99.0, "end_s": 130.0},
    ]
    out = snap_candidates_to_sentences(candidates, NEGATION_SEGMENTS, duration_s=120.0)
    assert len(out) == 2
    assert out[0]["setup_start_s"] == 71.2  # negation preserved
    assert out[0]["end_s"] == 108.0
    assert out[1]["setup_start_s"] == 78.7
    assert out[1]["end_s"] == 120.0


def test_snap_candidates_drops_when_duration_clamp_breaks_min():
    # Snapped setup 71.2, end clamped 108 → 100: 28.8 s < MIN_CLIP_S → dropped.
    candidates = [{"setup_start_s": 76.0, "start_s": 40.0, "peak_s": 90.0, "end_s": 108.0}]
    assert snap_candidates_to_sentences(candidates, NEGATION_SEGMENTS, duration_s=100.0) == []


def test_snap_candidates_noop_without_segments():
    candidates = [{"setup_start_s": 76.0, "start_s": 40.0, "peak_s": 90.0, "end_s": 108.0}]
    assert snap_candidates_to_sentences(candidates, [], duration_s=100.0) is candidates


def test_snap_candidates_clamps_signal_window_to_target():
    """The rank-10 live escape: a 95 s natural signal window stretched past
    100 s by edge snapping must come back <= CLIP_TARGET_MAX_S, cut at a
    sentence end."""
    segments = [
        _segment(0.0, 60.0, [("Opening", 0.0, 0.5), ("beat.", 0.6, 60.0)]),
        _segment(61.0, 85.0, [("Middle", 61.0, 61.5), ("beat.", 61.6, 85.0)]),
        _segment(86.0, 130.0, [("Closing", 86.0, 86.5), ("beat.", 86.6, 130.0)]),
    ]
    # Start on a boundary (0.0), end mid-final-sentence at 101 → forward snap
    # would push it to 130; the clamp must cut at the latest sentence end
    # within (0, 90] = 85.0.
    candidates = [{"setup_start_s": 0.0, "start_s": 0.0, "peak_s": 80.0, "end_s": 101.0}]
    out = snap_candidates_to_sentences(candidates, segments, duration_s=200.0)
    assert len(out) == 1
    assert out[0]["end_s"] == 85.0
    assert out[0]["end_s"] - out[0]["setup_start_s"] <= CLIP_TARGET_MAX_S


def test_snap_candidates_clamp_never_cuts_before_peak():
    """A sparse sentence index whose only end inside the target window falls
    BEFORE the peak must not amputate the payoff — fall back to the ceiling."""
    segments = [
        _segment(0.0, 40.0, [("Early", 0.0, 0.5), ("beat.", 0.6, 40.0)]),
        _segment(95.0, 130.0, [("Late", 95.0, 95.5), ("beat.", 95.6, 130.0)]),
    ]
    # Latest sentence end within (0, 90] is 40.0 — before the peak at 88.
    candidates = [{"setup_start_s": 0.0, "start_s": 0.0, "peak_s": 88.0, "end_s": 101.0}]
    out = snap_candidates_to_sentences(candidates, segments, duration_s=200.0)
    assert len(out) == 1
    assert out[0]["end_s"] == 90.0  # bare ceiling, past the peak
    assert out[0]["end_s"] > out[0]["peak_s"]


def test_clamp_window_to_target_cuts_at_sentence_end():
    sentences = [
        {"start_s": 0.0, "end_s": 40.0},
        {"start_s": 41.0, "end_s": 85.0},
        {"start_s": 86.0, "end_s": 110.0},
    ]
    # 110 s window from 0 → latest sentence end within (0, 90] is 85.0.
    assert clamp_window_to_target(0.0, 110.0, sentences) == 85.0


def test_clamp_window_to_target_hard_cut_without_sentences():
    assert clamp_window_to_target(10.0, 120.0, []) == 10.0 + CLIP_TARGET_MAX_S


def test_clamp_window_to_target_within_target_unchanged():
    assert clamp_window_to_target(0.0, 88.0, [{"start_s": 0.0, "end_s": 50.0}]) == 88.0


# ── Issues 456 + 449: bounded backward snap + post-snap peak invariant ───────

# Issue 456 repro geometry: a 30 s unpunctuated run-on utterance (205–235)
# spans the raw setup at 225; the peak sits at 300, so the run-on's sentence
# start is 95 s before the peak — further back than the 90 s length ceiling
# can ever recover from. Punctuated content follows around the peak.
RUN_ON_SEGMENTS = [
    _segment(
        205.0,
        235.0,
        [
            ("so", 205.0, 205.4),
            ("the", 205.5, 205.8),
            ("story", 205.9, 212.0),
            ("kept", 212.1, 220.0),
            ("going", 220.1, 226.0),
            ("and", 226.1, 228.0),
            ("going", 228.1, 231.0),
            ("without", 231.1, 232.5),
            ("a", 232.6, 232.9),
            ("single", 233.0, 233.9),
            ("pause", 234.0, 235.0),
        ],
    ),
    _segment(
        240.0,
        250.0,
        [("That", 240.0, 240.4), ("part", 240.5, 241.0), ("mattered.", 241.1, 250.0)],
    ),
    _segment(
        296.0,
        308.0,
        [
            ("Here", 296.0, 296.4),
            ("is", 296.5, 296.8),
            ("the", 296.9, 297.1),
            ("payoff.", 297.2, 308.0),
        ],
    ),
    _segment(
        310.0,
        330.0,
        [("And", 310.0, 310.3), ("it", 310.4, 310.6), ("landed.", 310.7, 330.0)],
    ),
]


def test_snap_candidates_bounded_backward_keeps_peak_inside():
    """Issue 456 repro: raw {setup 225, peak 300, end 320}. Pre-fix the
    absolute backward rule dragged the start to 204.7 and the 90 s clamp cut
    the end at 294.7 — shipping [204.7, 294.7] with the peak at 300 entirely
    OUTSIDE the delivered clip. Bounded snapping keeps the raw start and the
    persisted window must contain the peak."""
    candidates = [{"setup_start_s": 225.0, "start_s": 225.0, "peak_s": 300.0, "end_s": 320.0}]
    out = snap_candidates_to_sentences(candidates, RUN_ON_SEGMENTS, duration_s=400.0)
    assert len(out) == 1
    assert out[0]["setup_start_s"] >= 215.0
    assert out[0]["end_s"] > 300.1
    assert out[0]["setup_start_s"] + 0.1 <= out[0]["peak_s"] <= out[0]["end_s"] - 0.1


def test_snap_candidates_degenerate_single_span_disables_snapping(caplog):
    """Issue 456 degenerate half: the Deepgram no-utterance fallback emits ONE
    whole-video unpunctuated segment. Pre-fix every candidate collapsed onto
    the same [0.2, 90.2] window (both peaks outside). A degenerate index must
    disable snapping with ONE warning and keep raw candidate geometry."""
    segments = [
        {"start": 0.5, "end": 399.5, "text": "one long unterminated span with no punctuation"}
    ]
    candidates = [
        {"setup_start_s": 45.0, "start_s": 45.0, "peak_s": 120.0, "end_s": 140.0},
        {"setup_start_s": 225.0, "start_s": 225.0, "peak_s": 300.0, "end_s": 320.0},
    ]
    with caplog.at_level(logging.WARNING, logger="clip_engine.sentence_snap"):
        out = snap_candidates_to_sentences(candidates, segments, duration_s=400.0)
    assert out == candidates  # raw geometry preserved, both moments survive
    degenerate_warnings = [r for r in caplog.records if "degenerate" in r.message]
    assert len(degenerate_warnings) == 1  # one warning per video, not per candidate


def test_snap_candidates_drop_backstop_when_repair_impossible(caplog):
    """Issue 456 stage-2 backstop: when even the restored raw end cannot put
    the peak back inside the window (peak within 0.1 s of the container
    duration), the candidate is DROPPED with a warning — a peak-outside window
    must never be returned."""
    segments = [
        _segment(0.0, 40.0, [("Early", 0.0, 0.5), ("beat.", 0.6, 40.0)]),
        _segment(41.0, 85.0, [("Middle", 41.0, 41.5), ("beat.", 41.6, 85.0)]),
    ]
    candidates = [{"setup_start_s": 5.0, "start_s": 5.0, "peak_s": 88.0, "end_s": 90.0}]
    with caplog.at_level(logging.WARNING, logger="clip_engine.sentence_snap"):
        out = snap_candidates_to_sentences(candidates, segments, duration_s=88.05)
    assert out == []
    assert any("dropped candidate" in r.message for r in caplog.records)


def test_snap_candidates_repairs_end_snapped_before_peak():
    """Issue 456 stage-1 repair: a backward end-snap that lands BEFORE the peak
    (raw end 101 inside a long sentence, previous sentence end 92.5 < peak 93)
    is repaired by restoring the pre-snap raw end."""
    segments = [
        _segment(
            38.0,
            44.0,
            [("The", 38.0, 38.3), ("setup", 38.4, 39.0), ("began.", 39.1, 44.0)],
        ),
        _segment(
            60.0,
            92.5,
            [("He", 60.0, 60.2), ("kept", 60.3, 61.0), ("building.", 61.1, 92.5)],
        ),
        _segment(
            95.0,
            130.0,
            [
                ("Then", 95.0, 95.4),
                ("it", 95.5, 95.7),
                ("kept", 95.8, 100.0),
                ("rolling", 100.1, 130.0),
            ],
        ),
    ]
    candidates = [{"setup_start_s": 40.5, "start_s": 40.5, "peak_s": 93.0, "end_s": 101.0}]
    out = snap_candidates_to_sentences(candidates, segments, duration_s=200.0)
    assert len(out) == 1
    assert out[0]["end_s"] == 101.0  # pre-snap raw end restored
    assert out[0]["end_s"] > out[0]["peak_s"]


def test_snap_start_raw_fallback_beyond_backward_limit(caplog):
    """When the backward target violates the caller's backward_limit_s and
    forward is unusable, the RAW start is kept (debug breadcrumb) — a
    mid-sentence open is recoverable in review; a clip without its peak is not."""
    sentences = build_sentence_index(RUN_ON_SEGMENTS)
    with caplog.at_level(logging.DEBUG, logger="clip_engine.sentence_snap"):
        out = snap_start(225.0, sentences, forward_limit_s=290.0, backward_limit_s=215.0)
    assert out == 225.0
    assert any("kept raw start" in r.message for r in caplog.records)


def test_snap_start_raw_fallback_beyond_run_on_cap():
    """Backward drag past _RUN_ON_BACKWARD_CAP_S (35 s here) keeps the raw
    start even with no caller-supplied backward_limit_s."""
    segments = [
        _segment(
            190.0,
            235.0,
            [
                ("it", 190.0, 190.3),
                ("just", 190.4, 210.0),
                ("went", 210.1, 226.0),
                ("on", 226.1, 235.0),
            ],
        ),
        _segment(
            240.0,
            250.0,
            [("That", 240.0, 240.4), ("part", 240.5, 241.0), ("mattered.", 241.1, 250.0)],
        ),
    ]
    sentences = build_sentence_index(segments)
    assert snap_start(225.0, sentences) == 225.0


def test_snap_start_moderate_run_on_still_snaps_backward():
    """A run-on beyond the 10 s budget but within the 30 s cap (20 s here, no
    backward_limit_s) still takes the never-mid-sentence backward snap."""
    sentences = build_sentence_index(RUN_ON_SEGMENTS)
    assert snap_start(225.0, sentences) == 204.7  # 205.0 - 0.3 lead-in


# ── Issue 449: a pause start audibly opens on the NEXT sentence ──────────────

# The live geometry from video 7e988321 rank 4, in real absolute seconds: the
# shipped start 1306.43 sits in the inter-sentence pause [1306.27, 1306.51],
# 0.08 s before "Yeah." — outside _BOUNDARY_EPSILON_S = 0.05, so the pre-fix
# pause branch returned it untouched and the clip opened on a discourse marker,
# bypassing the Issue-441 weak-opener guard entirely.
PAUSE_WEAK_SEGMENTS = [
    _segment(
        1297.5,
        1301.87,
        [
            ("The", 1297.5, 1297.8),
            ("corner", 1297.9, 1298.4),
            ("room", 1298.5, 1299.3),
            ("thin.", 1299.4, 1301.87),
        ],
    ),
    _segment(
        1301.87,
        1306.27,
        [
            ("We", 1301.87, 1302.1),
            ("could", 1302.2, 1302.6),
            ("trade", 1302.7, 1303.2),
            ("back.", 1303.3, 1306.27),
        ],
    ),
    _segment(
        1306.51,
        1310.0,
        [
            ("Yeah.", 1306.51, 1306.9),
            ("They've", 1307.2, 1307.5),
            ("been", 1307.6, 1307.9),
            ("awesome.", 1308.0, 1310.0),
        ],
    ),
]


def test_snap_start_pause_before_weak_opener_walks_back():
    """Issue 449: the clip audibly opens on the first sentence at/after the
    start, so a weak opener there must take the same bounded walk-back as the
    in-sentence path. Cost here is 4.56 s against the 10.0 s budget; the
    walked-back open is the previous sentence's start, 1301.87."""
    sentences = build_sentence_index(PAUSE_WEAK_SEGMENTS)
    assert snap_start(1306.43, sentences) == 1301.87
