"""
clip_engine/filler.py — Detect filler words + long silences as a cut list for
ffmpeg trim+concat removal (Issue 134).

Pure-function module over a WhisperX-style word array (the same
``[{word, start, end}]`` shape produced by ``ingestion/transcribe.py``). Returns
a list of ``CutSegment`` ranges to REMOVE; callers invert to keep-ranges before
handing them to ``clip_engine.render.render_cleaned_clip_file``.

Two filler tiers:

  - **Tier 1** — unconditional phonetic disfluencies (``um``, ``uh``, ``mhm``, …).
    No legitimate non-filler usage in English creator content; always excised.

  - **Tier 2** — context-dependent fillers (``like``, ``you know``, ``so``, …).
    Excised only when the token is short (<=600 ms) AND flanked by an
    inter-word gap >= ``flank_gap_ms`` on at least one side. The
    pause-flanked guard is the standard short-form-tool heuristic for
    distinguishing filler ``like`` from the verb ``like``; no POS tagging
    needed at this content length (see ``docs/DECISIONS.md`` 2026-06-07).

Silence-gap removal: a gap > ``silence_threshold_ms`` between consecutive words
is cut, leaving ``silence_tail_ms`` of natural "breath" on each side so the
splice sounds natural and the waveform tapers toward zero (the foundation of
the audio-click fix in render.py).
"""

from __future__ import annotations

from dataclasses import dataclass

# Default Tier 1 lexicon — safe to excise unconditionally on any English clip.
# Stored as a frozenset of LOWERCASE tokens; the matcher case-folds at compare
# time so transcripts capitalised at sentence start ("Um, …") still match.
DEFAULT_TIER1_FILLERS: frozenset[str] = frozenset(
    {"um", "umm", "uh", "uhh", "uhhh", "er", "ah", "mhm", "hmm", "uhm"}
)

# Default Tier 2 lexicon — flank-gap-guarded. Each entry is a phrase (one or
# more tokens separated by a single space). The detector matches phrases by
# sliding a window of len(tokens) over the word array; the flank-gap test
# applies to the FIRST word's preceding gap and the LAST word's following gap.
DEFAULT_TIER2_FILLERS: frozenset[str] = frozenset(
    {"like", "you know", "basically", "so", "right", "okay", "you know what i mean"}
)

# Tier-2 guard thresholds. See docs/DECISIONS.md 2026-06-07 for source citations.
DEFAULT_FLANK_GAP_MS = 150
DEFAULT_TIER2_MAX_DURATION_MS = 600

# Silence-gap defaults — Issue 134 spec.
DEFAULT_SILENCE_THRESHOLD_MS = 800
DEFAULT_SILENCE_TAIL_MS = 150

# >30% warning threshold — surfaced to UI to flag aggressive cuts.
WARNING_THRESHOLD_PCT = 30.0


@dataclass(frozen=True)
class CutSegment:
    """A single contiguous span of source media to be removed.

    ``start_s`` and ``end_s`` are in the source-clip timeline (relative to
    the rendered clip's t=0, NOT video-absolute). ``reason`` is one of
    ``"filler"`` or ``"silence"``; ``word`` is the matched filler phrase for
    ``filler`` cuts (``None`` for silences).
    """

    start_s: float
    end_s: float
    reason: str
    word: str | None = None

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)


def _normalise_word(raw: str) -> str:
    """Lowercase + strip surrounding punctuation. Transcripts return tokens
    like ``"Um,"`` and ``"Uh."`` — we want those to match the lexicon."""
    return raw.strip().strip(".,!?;:\"'…—-").lower()


def detect_cut_segments(
    words: list[dict],
    clip_start_s: float,
    clip_end_s: float,
    *,
    tier1_fillers: frozenset[str] = DEFAULT_TIER1_FILLERS,
    tier2_fillers: frozenset[str] = DEFAULT_TIER2_FILLERS,
    silence_threshold_ms: int = DEFAULT_SILENCE_THRESHOLD_MS,
    silence_tail_ms: int = DEFAULT_SILENCE_TAIL_MS,
    flank_gap_ms: int = DEFAULT_FLANK_GAP_MS,
    tier2_max_duration_ms: int = DEFAULT_TIER2_MAX_DURATION_MS,
) -> list[CutSegment]:
    """Walk a transcript word array and emit cut ranges for filler tokens +
    long inter-word silences.

    All ``*_ms`` parameters are integers; converted to seconds internally.
    Output cuts are sorted by ``start_s`` and clamped to ``[clip_start_s,
    clip_end_s]``. Adjacent/overlapping cuts are NOT merged here — call
    ``merge_adjacent_cuts`` separately so callers can distinguish unmerged
    cuts (for transcript-strikethrough rendering) from the merged form
    (for the ffmpeg invocation).
    """
    if not words or clip_end_s <= clip_start_s:
        return []

    flank_gap_s = flank_gap_ms / 1000.0
    tier2_max_dur_s = tier2_max_duration_ms / 1000.0
    silence_threshold_s = silence_threshold_ms / 1000.0
    silence_tail_s = silence_tail_ms / 1000.0

    # Scope to clip window. Keep original list for adjacency lookups
    # (preceding/following gap calculations); but only emit cuts inside window.
    in_window = [
        w
        for w in words
        if w.get("end", 0.0) > clip_start_s and w.get("start", 0.0) < clip_end_s
    ]
    if not in_window:
        return []

    cuts: list[CutSegment] = []

    # ── Tier 1: unconditional filler excise ───────────────────────────────
    for w in in_window:
        token = _normalise_word(w.get("word") or "")
        if token in tier1_fillers:
            start = max(clip_start_s, float(w["start"]))
            end = min(clip_end_s, float(w["end"]))
            if end > start:
                cuts.append(CutSegment(start, end, "filler", token))

    # ── Tier 2: pause-flanked filler excise ───────────────────────────────
    # Build phrase-token list for multi-word matches ("you know"). For each
    # phrase length L, slide a window of L words; if normalised tokens
    # concatenate to a Tier 2 phrase AND flank-gap guard passes, excise.
    max_phrase_len = max((len(p.split()) for p in tier2_fillers), default=1)
    n = len(in_window)
    for i in range(n):
        for length in range(1, max_phrase_len + 1):
            j = i + length
            if j > n:
                break
            phrase = " ".join(_normalise_word(in_window[k].get("word") or "") for k in range(i, j))
            if phrase not in tier2_fillers:
                continue
            phrase_start = float(in_window[i]["start"])
            phrase_end = float(in_window[j - 1]["end"])
            phrase_dur = phrase_end - phrase_start
            if phrase_dur > tier2_max_dur_s:
                continue
            # Flank-gap test: gap >= flank_gap_s on at least one side.
            prev_gap = (
                phrase_start - float(in_window[i - 1]["end"])
                if i > 0
                else float("inf")
            )
            next_gap = (
                float(in_window[j]["start"]) - phrase_end
                if j < n
                else float("inf")
            )
            if max(prev_gap, next_gap) < flank_gap_s:
                continue
            start = max(clip_start_s, phrase_start)
            end = min(clip_end_s, phrase_end)
            if end > start:
                cuts.append(CutSegment(start, end, "filler", phrase))

    # ── Silence: long inter-word gaps ─────────────────────────────────────
    for prev, nxt in zip(in_window, in_window[1:], strict=False):
        gap_start = float(prev["end"])
        gap_end = float(nxt["start"])
        gap_dur = gap_end - gap_start
        if gap_dur > silence_threshold_s:
            # Leave silence_tail_s of breath on each side.
            cut_start = max(clip_start_s, gap_start + silence_tail_s)
            cut_end = min(clip_end_s, gap_end - silence_tail_s)
            if cut_end > cut_start:
                cuts.append(CutSegment(cut_start, cut_end, "silence", None))

    cuts.sort(key=lambda c: c.start_s)
    return cuts


def merge_adjacent_cuts(cuts: list[CutSegment]) -> list[CutSegment]:
    """Collapse adjacent / overlapping cuts so the ffmpeg keep-range inversion
    cannot emit zero-width ``trim`` segments (which crash the filter graph).

    A merged cut's ``reason`` is ``"silence"`` if any component cut was a
    silence; otherwise ``"filler"``. The ``word`` field on a merged filler is
    a ``+``-joined trace of component words for debug logging — UI strikethrough
    operates on the UNMERGED list, not this one.
    """
    if not cuts:
        return []
    ordered = sorted(cuts, key=lambda c: c.start_s)
    merged: list[CutSegment] = [ordered[0]]
    for c in ordered[1:]:
        last = merged[-1]
        if c.start_s <= last.end_s:
            new_end = max(last.end_s, c.end_s)
            new_reason = "silence" if "silence" in (last.reason, c.reason) else "filler"
            new_word: str | None = None
            if new_reason == "filler":
                pieces = [w for w in (last.word, c.word) if w]
                new_word = "+".join(pieces) if pieces else None
            merged[-1] = CutSegment(last.start_s, new_end, new_reason, new_word)
        else:
            merged.append(c)
    return merged


def invert_to_keep_ranges(
    cuts: list[CutSegment], clip_start_s: float, clip_end_s: float
) -> list[tuple[float, float]]:
    """Convert a list of cut ranges into the complementary list of KEEP ranges
    over ``[clip_start_s, clip_end_s]``. The caller must pass merged cuts —
    overlapping cuts produce reversed (start > end) keep ranges that ffmpeg
    rejects."""
    if clip_end_s <= clip_start_s:
        return []
    if not cuts:
        return [(clip_start_s, clip_end_s)]
    keeps: list[tuple[float, float]] = []
    cursor = clip_start_s
    for c in cuts:
        if c.start_s > cursor:
            keeps.append((cursor, min(c.start_s, clip_end_s)))
        cursor = max(cursor, c.end_s)
        if cursor >= clip_end_s:
            break
    if cursor < clip_end_s:
        keeps.append((cursor, clip_end_s))
    # Filter zero-width keep ranges so the ffmpeg filter graph never sees a
    # ``trim=start=X:end=X`` segment (crashes the graph at parse time).
    return [(s, e) for s, e in keeps if e > s]


def percent_removed(cuts: list[CutSegment], clip_duration_s: float) -> float:
    """Percent of ``clip_duration_s`` that the cut list would excise. Drives
    the >30% UI warning band."""
    if clip_duration_s <= 0:
        return 0.0
    merged = merge_adjacent_cuts(cuts)
    total = sum(c.duration_s for c in merged)
    return min(100.0, 100.0 * total / clip_duration_s)
