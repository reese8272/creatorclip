"""Segment-aware sentence snapping — Issue 428.

Pure post-extraction layer between ``extract_candidates`` (byte-untouched — the
eval harness calls it directly and stays green by construction) and scoring,
mirroring merge.py's position in the pipeline.

The word-level ``snap_to_sentence_boundary`` in candidates.py only knows
sentence ENDS (terminal-punctuation word tokens) within a 3 s radius, so a raw
``setup_start_s`` landing mid-sentence more than 3 s after the previous
sentence end survives unchanged — the live meaning-inverting cut ("I don't |
really think it's gonna happen"). This module rebuilds SENTENCE spans from the
Deepgram utterance segments (utterances are semantic units and words carry
punctuation — https://developers.deepgram.com/docs/utterances,
https://developers.deepgram.com/docs/punctuation) and snaps candidate edges to
sentence STARTS/ends with a sentence-scale search radius.

Principle #12 "Clean Context Boundary": a clip never opens or closes
mid-sentence. The start guard is absolute — when a start falls strictly inside
a sentence it is always moved to a sentence start, even beyond
``SENTENCE_SNAP_MAX_S`` (preserving a negation like "I don't…" outranks the
window nudge).
"""

import logging

from clip_engine.candidates import MIN_CLIP_S, _is_sentence_end

logger = logging.getLogger(__name__)

SENTENCE_SNAP_MAX_S = 10.0  # sentence-scale search radius (the word-level 3 s cap was the bug)
SENTENCE_LEAD_IN_S = 0.3  # small breath before the sentence's first word
CLIP_TARGET_MAX_S = 90.0  # hard ceiling for LLM-proposed windows (60–90 s target)

# Tokens that cannot open a clip (Issue 441). A Deepgram utterance boundary is a
# PAUSE, not a grammatical break, so "because they still don't know what Mikey
# is." is a first-class sentence start and snapping to it is a no-op — which is
# how five of nine rendered clips on one video opened mid-thought.
#
# A closed word list, not a parser: these are closed grammatical classes, so
# enumeration is exact and a coreference model would be a dependency bought for
# nothing.
#
# Deliberately narrow — a wrong rejection drags the start earlier, bloats the
# clip, and can collapse two distinct candidates onto one opening. Only classes
# the live evidence actually showed broken are listed:
#   - SUBORDINATORS open a clause that cannot stand alone ("because they still
#     don't know what Mikey is", "when they're cut, when rosters go out").
#   - DISCOURSE MARKERS answer something the viewer never heard ("yeah, they've
#     been awesome so far").
# Explicitly NOT listed:
#   - Coordinators (and / but / or / so / yet). "But I think…" is a punchy,
#     perfectly serviceable Short opening, none of the live failures were
#     coordinator-initial, and rejecting them broke two pinned snap cases.
#   - Pronouns and demonstratives. "It's the weakest room on the roster" opens
#     fine; distinguishing that from a genuinely dangling referent needs more
#     than a word list, and the one live pronoun case ("yeah, they've…") is
#     already caught by its discourse marker.
#   - First and second person, which are self-anchoring.
_SUBORDINATORS = frozenset(
    {
        "because", "although", "though", "whereas", "unless", "until", "since",
        "while", "when", "whenever",
    }
)
_DISCOURSE_MARKERS = frozenset(
    {
        "yeah", "yes", "yep", "yup", "no", "nah", "ok", "okay", "well", "right",
        "um", "uh", "hmm", "anyway", "anyways",
    }
)
_WEAK_OPENERS = _SUBORDINATORS | _DISCOURSE_MARKERS

# How many sentences back the search may step looking for a clean opener. Bounded
# so a run of dependent clauses cannot walk the start arbitrarily far back; the
# `max_snap_s` budget bounds it in time as well.
_MAX_OPENER_STEPS = 3

# "The start already sits on this sentence boundary" — float coincidence, not a
# search radius.
_BOUNDARY_EPSILON_S = 0.05


def _normalize_token(word: str) -> str:
    """Lowercase a transcript token and strip surrounding punctuation."""
    return word.strip().strip("\"'“”‘’.,!?;:—-…").lower()


def is_weak_opener(word: str | None) -> bool:
    """Whether ``word`` cannot stand as the first word of a clip (Issue 441)."""
    return _normalize_token(word or "") in _WEAK_OPENERS


def build_sentence_index(segments: list[dict]) -> list[dict]:
    """Build ``[{"start_s", "end_s"}, ...]`` sentence spans from transcript segments.

    Each Deepgram utterance opens a sentence; within a segment a word whose
    token carries terminal punctuation closes the current sentence and the next
    word opens a new one. A segment without word timings contributes a single
    sentence spanning the whole segment. Spans are returned in chronological
    order.
    """
    sentences: list[dict] = []
    for seg in segments or []:
        words = seg.get("words") or []
        if not words:
            seg_start = seg.get("start")
            seg_end = seg.get("end")
            if seg_start is not None and seg_end is not None and seg_end > seg_start:
                text = str(seg.get("text", "")).split()
                sentences.append(
                    {
                        "start_s": float(seg_start),
                        "end_s": float(seg_end),
                        "first_word": text[0] if text else "",
                    }
                )
            continue

        open_start: float | None = None
        open_word = ""
        last_end = 0.0
        for w in words:
            w_start = float(w.get("start", 0.0))
            w_end = float(w.get("end", w_start))
            if open_start is None:
                open_start = w_start
                # Issue 441: the opening token is carried so snap_start can
                # reject a start that opens on a dependent clause. The index used
                # to discard all text, which made any lexical check impossible.
                open_word = str(w.get("word", ""))
            last_end = w_end
            if _is_sentence_end(w.get("word", "")):
                sentences.append(
                    {"start_s": open_start, "end_s": w_end, "first_word": open_word}
                )
                open_start = None
                open_word = ""
        # Utterance boundary closes an unterminated sentence (speaker trailed off).
        if open_start is not None:
            sentences.append(
                {"start_s": open_start, "end_s": last_end, "first_word": open_word}
            )

    sentences.sort(key=lambda s: s["start_s"])
    return sentences


def _containing_index(t: float, sentences: list[dict]) -> int | None:
    """Index of the sentence whose span strictly contains ``t``, else None."""
    for i, s in enumerate(sentences):
        if s["start_s"] < t < s["end_s"]:
            return i
        if s["start_s"] >= t:
            break
    return None


def snap_start(
    t: float,
    sentences: list[dict],
    *,
    max_snap_s: float = SENTENCE_SNAP_MAX_S,
    lead_in_s: float = SENTENCE_LEAD_IN_S,
    forward_limit_s: float | None = None,
) -> float:
    """Snap a clip start to a sentence start when it falls mid-sentence.

    Backward target: the containing sentence's own start. Forward target: the
    next sentence's start, usable only when ``forward_limit_s`` (the caller's
    invariant ceiling — peak/length constraints) allows it. Backward is
    PREFERRED whenever it is within ``max_snap_s`` — the sentence containing
    the detected setup point IS setup content, and moving backward only adds
    context while moving forward would discard it. Forward is used only for a
    run-on sentence whose start is more than ``max_snap_s`` back; when forward
    is unusable the start snaps backward regardless of distance — a start is
    NEVER left mid-sentence.

    The chosen start gets a small lead-in breath, clamped so it never swallows
    the previous sentence's last word.
    """
    if not sentences:
        return t
    idx = _containing_index(t, sentences)
    if idx is None:
        # Already on a boundary or inside a pause. That is a clean open ONLY if
        # the sentence starting here can stand alone (Issue 441) — a Deepgram
        # utterance boundary is a pause, not a grammatical break, so "because …"
        # lands here and used to be returned untouched.
        # Exact coincidence only. A start sitting in an inter-sentence PAUSE is a
        # clean open and must stay untouched — widening this to the lead-in
        # window would let a pause start claim the following sentence's opener.
        at = next(
            (i for i, s in enumerate(sentences) if abs(s["start_s"] - t) <= _BOUNDARY_EPSILON_S),
            None,
        )
        if at is None or not is_weak_opener(sentences[at].get("first_word")):
            return t
        idx = at

    back_target = sentences[idx]["start_s"]
    fwd_target = sentences[idx + 1]["start_s"] if idx + 1 < len(sentences) else None
    fwd_valid = (
        fwd_target is not None
        and (forward_limit_s is None or fwd_target <= forward_limit_s)
        and fwd_target - t <= max_snap_s
    )

    back_dist = t - back_target
    if back_dist > max_snap_s and fwd_valid:
        chosen, chosen_idx = float(fwd_target), idx + 1  # type: ignore[arg-type]
    else:
        chosen, chosen_idx = float(back_target), idx

    # Issue 441: a sentence opening on a subordinator, a discourse marker or a
    # dangling third-person referent depends on the sentence before it. Step back
    # until the opener can stand alone, bounded by both the step count and the
    # caller's time budget — a clip that opens mid-thought is worse than one that
    # starts a beat early, but not worth walking arbitrarily far for.
    for _ in range(_MAX_OPENER_STEPS):
        if chosen_idx == 0 or not is_weak_opener(sentences[chosen_idx].get("first_word")):
            break
        prev_start = float(sentences[chosen_idx - 1]["start_s"])
        if t - prev_start > max_snap_s:
            break  # out of budget — keep the nearest sentence start
        chosen, chosen_idx = prev_start, chosen_idx - 1

    # Lead-in breath, floored at the previous sentence's end. Overlapping
    # diarized utterances can put that end AFTER the chosen start — cap the
    # floor at `chosen` so the guard can never push the start mid-sentence.
    prev_end = sentences[chosen_idx - 1]["end_s"] if chosen_idx > 0 else 0.0
    lead_floor = min(float(prev_end), chosen)
    return max(lead_floor, chosen - lead_in_s, 0.0)


def snap_end(
    t: float,
    sentences: list[dict],
    *,
    max_snap_s: float = SENTENCE_SNAP_MAX_S,
) -> float:
    """Snap a clip end to a sentence end when it falls mid-sentence.

    Forward preferred (finish the sentence in progress) within ``max_snap_s``;
    otherwise backward to the previous completed sentence's end; unchanged when
    no boundary is in range (the caller's duration clamp still applies).
    """
    if not sentences:
        return t
    idx = _containing_index(t, sentences)
    if idx is None:
        return t

    fwd_target = sentences[idx]["end_s"]
    if fwd_target - t <= max_snap_s:
        return float(fwd_target)
    if idx > 0 and t - sentences[idx - 1]["end_s"] <= max_snap_s:
        return float(sentences[idx - 1]["end_s"])
    return t


def snap_candidates_to_sentences(
    candidates: list[dict],
    segments: list[dict],
    duration_s: float,
) -> list[dict]:
    """Snap every candidate's edges to sentence boundaries, holding invariants.

    Replays the exact invariant-repair tail of ``extract_candidates``: re-extend
    to ``MIN_CLIP_S`` → clamp ``end_s`` to the container duration → force
    ``setup_start_s <= peak_s - 0.1`` → drop when ``MIN_CLIP_S`` is impossible.
    Graceful no-op when segments are absent (no-transcript videos and the eval
    harness's geometry scenarios are unaffected).
    """
    if not candidates or not segments:
        return candidates
    sentences = build_sentence_index(segments)
    if not sentences:
        return candidates

    snapped: list[dict] = []
    for c in candidates:
        cand = dict(c)
        peak = cand["peak_s"]
        # A forward-snapped start must still leave a valid clip: before the
        # peak, and long enough against the (pre-snap) end.
        forward_limit = min(peak - 0.1, cand["end_s"] - MIN_CLIP_S)
        cand["setup_start_s"] = round(
            snap_start(cand["setup_start_s"], sentences, forward_limit_s=forward_limit), 2
        )
        cand["end_s"] = round(snap_end(cand["end_s"], sentences), 2)

        # Hard 90 s ceiling for ALL candidates (2026-08-05 live finding: a
        # signal window at 95 s natural max stretched to 100.6 s after edge
        # snapping). The sentence-aligned cut can never land before the peak:
        # setup >= peak - 85 (75 s lookback + <=10 s snap), so the ceiling sits
        # >= 5 s past the peak — but a sparse sentence index could still pick an
        # earlier end, so floor at the bare ceiling when it would cut the payoff.
        if cand["end_s"] - cand["setup_start_s"] > CLIP_TARGET_MAX_S:
            new_end = clamp_window_to_target(cand["setup_start_s"], cand["end_s"], sentences)
            if new_end <= peak:
                new_end = cand["setup_start_s"] + CLIP_TARGET_MAX_S
            cand["end_s"] = round(new_end, 2)

        if cand["end_s"] - cand["setup_start_s"] < MIN_CLIP_S:
            cand["end_s"] = round(cand["setup_start_s"] + MIN_CLIP_S, 2)
        if duration_s > 0:
            cand["end_s"] = round(min(cand["end_s"], duration_s), 2)
        cand["setup_start_s"] = min(cand["setup_start_s"], peak - 0.1)
        if cand["end_s"] - cand["setup_start_s"] < MIN_CLIP_S:
            logger.debug(
                "sentence snap dropped candidate: window [%.1f, %.1f] cannot satisfy MIN_CLIP_S",
                cand["setup_start_s"],
                cand["end_s"],
            )
            continue
        snapped.append(cand)
    return snapped


def clamp_window_to_target(
    start: float,
    end: float,
    sentences: list[dict],
    *,
    max_len_s: float = CLIP_TARGET_MAX_S,
) -> float:
    """Return a new ``end`` so that ``end - start <= max_len_s``, sentence-aligned.

    Picks the latest sentence end within ``(start, start + max_len_s]``; falls
    back to the hard cut ``start + max_len_s`` when no sentence index is
    available or no sentence closes inside the target window. Ends already
    within the target are returned unchanged.
    """
    if end - start <= max_len_s:
        return end
    ceiling = start + max_len_s
    candidates_ends = [s["end_s"] for s in sentences if start < s["end_s"] <= ceiling]
    if candidates_ends:
        return float(max(candidates_ends))
    return ceiling
