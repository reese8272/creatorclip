"""
clip_engine/captions.py — Generate ASS subtitle files for animated word-level captions.

Four styles (Issue 133 + 183):
  - ``bold_pop``           MrBeast/Hormozi feel — one word at a time, scale-pop on
                           appearance, centered, white fill + black outline.
  - ``bold_pop_highlight`` Bold Pop, plus the most salient (keyword) token in each
                           phrase rendered in a punch-yellow ``\\c`` override
                           (Issue 183). Keyword selection is a dependency-free,
                           per-phrase salience scorer (YAKE-inspired features:
                           stopword filter + clip term-frequency + casing + length);
                           DNA-driven selection is the planned follow-up.
  - ``gradient_slide``     Per-word fade-in with indigo→white color transition.
                           Words accumulate within a phrase.
  - ``minimal``            Plain phrase-level captions, no animation. Also the
                           fallback when a transcript lacks word-level timestamps.

Word-level timing comes from ``Transcript.segments_jsonb[segments][i][words][j]``
(WhisperX / Deepgram / AssemblyAI normalize to the same shape — see
``ingestion/transcribe.py``: each word has ``word``, ``start``, ``end``).

Output is consumed by ffmpeg via ``subtitles=/path/to/file.ass:fontsdir=…`` (libass).
PlayResX/PlayResY match the 1080×1920 vertical Shorts output of
``clip_engine/render.py``.

References (Phase-1 research, ``docs/DECISIONS.md`` 2026-06-07 + 2026-08-05):
  - libass + pysubs2 is the production standard (Submagic/Opus.pro/CapCut pattern).
  - ASS colors are ``&HBBGGRR&`` — reversed from HTML hex.
  - ``\\t(start_ms,end_ms, tags)`` is the ASS animated-transform override.
  - Style ScaleX/ScaleY MUST be 100 baseline or ``\\t(\\fscx120)`` multiplies wrong.
  - Placement (Issue 427): karaoke styles default to a ~70%-height baseline —
    below a talking head's chin, above the Shorts bottom-UI zone (avoid the top
    ~20% and bottom ~25% per Opus Clip's caption guidance). Style-level
    ``MarginV`` + alignment (never ``\\pos``) so libass keeps line wrapping.
    ``caption_position`` (top/middle/bottom) is the creator's override.
"""

from __future__ import annotations

import logging
import math
import string
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import pysubs2

logger = logging.getLogger(__name__)

# Render output resolution (matches clip_engine/render.py:_OUTPUT_W/_OUTPUT_H).
_PLAY_RES_X = 1080
_PLAY_RES_Y = 1920

# Font defaults (Anton — SIL OFL, installed via Dockerfile under
# /usr/share/fonts/custom/). fonts-open-sans is the libass fallback.
_FONT_NAME = "Anton"
_FONT_SIZE_ANIMATED = 95
_FONT_SIZE_MINIMAL = 60
_OUTLINE_PX = 4

# ASS colour byte order is &HBBGGRR& — reversed from HTML hex.
#   Brand indigo  #5e6ad2 → &Hd26a5e&
#   White         #ffffff → &Hffffff&
#   Punch yellow  #ffd400 → &H00d4ff&  (keyword highlight, Issue 183)
_COLOR_WHITE_ASS = "&Hffffff&"
_COLOR_INDIGO_ASS = "&Hd26a5e&"
_COLOR_HIGHLIGHT_ASS = "&H00d4ff&"

# Bold Pop pop-scale animation: 80ms up to 120%, then 80ms back to 100%.
_POP_RAMP_MS = 80
_POP_SCALE_PCT = 120
# Built once — every Bold Pop word event carries the same scale-pop override.
_POP_OVERRIDE = (
    f"{{\\t(0,{_POP_RAMP_MS},\\fscx{_POP_SCALE_PCT}\\fscy{_POP_SCALE_PCT})"
    f"\\t({_POP_RAMP_MS},{_POP_RAMP_MS * 2},\\fscx100\\fscy100)}}"
)

# Gradient Slide: 300ms colour transition + 150ms fade-in.
_GRADIENT_COLOR_MS = 300
_GRADIENT_FADE_IN_MS = 150

# Number of keyword tokens highlighted per phrase in bold_pop_highlight (Issue 183).
_HIGHLIGHT_TOP_N = 1

# Compact English stopword list for the v1 keyword-salience scorer (Issue 183).
# Deliberately small — just the high-frequency function words that must never be
# the highlighted token. A fuller list / DNA-driven selection is the follow-up.
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "get",
        "got",
        "had",
        "has",
        "have",
        "he",
        "her",
        "here",
        "him",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "just",
        "me",
        "my",
        "no",
        "not",
        "now",
        "of",
        "on",
        "or",
        "our",
        "out",
        "she",
        "so",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "they",
        "this",
        "to",
        "too",
        "up",
        "us",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "yeah",
        "okay",
        "like",
    }
)

VALID_STYLES = frozenset({"bold_pop", "bold_pop_highlight", "gradient_slide", "minimal"})

VALID_POSITIONS = frozenset({"top", "middle", "bottom"})

# Placement defaults (Issue 427) — render.py passes the configured values; these
# keep the module usable standalone. All px values are at PlayResY=1920 and are
# scaled proportionally for other canvas heights.
_DEFAULT_BASELINE_FRAC = 0.70  # karaoke baseline at 70% of frame height
_DEFAULT_MIN_BOTTOM_MARGIN_PX = 480  # never sink into the Shorts bottom-UI zone
_DEFAULT_TOP_MARGIN_PX = 200  # "top" position: below the title/channel overlay
_DEFAULT_FACE_AVOID_PAD_PX = 40  # min gap between face-box bottom and caption top


def build_ass_subtitles(
    segments: list[dict] | None,
    style: str,
    clip_start_s: float,
    clip_duration_s: float,
    out_path: Path,
    play_res_x: int = _PLAY_RES_X,
    play_res_y: int = _PLAY_RES_Y,
    *,
    caption_position: str | None = None,
    face_bottom_px: int | None = None,
    words_per_group: int = 1,
    group_max_gap_s: float = 0.6,
    baseline_frac: float = _DEFAULT_BASELINE_FRAC,
    min_bottom_margin_px: int = _DEFAULT_MIN_BOTTOM_MARGIN_PX,
    top_margin_px: int = _DEFAULT_TOP_MARGIN_PX,
    face_avoid_pad_px: int = _DEFAULT_FACE_AVOID_PAD_PX,
) -> Path | None:
    """Render an ASS subtitle file for the clip window.

    ``segments`` is the full transcript segment list (WhisperX shape — each
    segment carries ``start``, ``end``, ``text``, optional ``words``). Word-level
    timing drives ``bold_pop`` / ``gradient_slide``; when absent the renderer falls
    back to segment-level lines (Minimal style).

    Placement (Issue 427): ``caption_position`` is the creator's choice
    (``top`` / ``middle`` / ``bottom``); ``None`` keeps the per-style default —
    karaoke styles at the ``baseline_frac`` bottom band, minimal/gradient at the
    legacy lower-third. ``face_bottom_px`` (output-canvas pixels, from the
    render pipeline's face detection) pushes a bottom-band caption below the
    chin, clamped so it never enters the bottom ``min_bottom_margin_px`` UI
    zone. ``words_per_group`` > 1 groups karaoke words into multi-word events
    (``1`` reproduces the legacy per-word output byte-identically).

    Returns the written path, or ``None`` when there is no usable text in the
    clip window or the style is unknown — callers must handle ``None`` by
    skipping the ``subtitles=`` filter rather than failing the render.
    """
    if style not in VALID_STYLES:
        logger.warning("captions: unknown style %r — no subtitles generated", style)
        return None
    if not segments or clip_duration_s <= 0:
        logger.info(
            "captions: no subtitles — empty segments or non-positive duration "
            "(segments=%d, clip_duration_s=%.3f)",
            len(segments or []),
            clip_duration_s,
        )
        return None

    clip_end_s = clip_start_s + clip_duration_s

    clipped = [
        seg
        for seg in segments
        if seg.get("end", 0.0) > clip_start_s and seg.get("start", 0.0) < clip_end_s
    ]
    if not clipped:
        logger.info(
            "captions: no subtitles — no transcript segments overlap clip window [%.3f, %.3f]",
            clip_start_s,
            clip_end_s,
        )
        return None

    if style == "bold_pop":
        events = _build_bold_pop(
            clipped,
            clip_start_s,
            clip_end_s,
            words_per_group=words_per_group,
            group_max_gap_s=group_max_gap_s,
        )
    elif style == "bold_pop_highlight":
        events = _build_bold_pop(
            clipped,
            clip_start_s,
            clip_end_s,
            highlight=True,
            words_per_group=words_per_group,
            group_max_gap_s=group_max_gap_s,
        )
    elif style == "gradient_slide":
        events = _build_gradient_slide(clipped, clip_start_s, clip_end_s)
    else:
        events = _build_minimal(clipped, clip_start_s, clip_end_s)

    if not events:
        logger.info(
            "captions: no subtitles — style %r produced no events for clip window "
            "[%.3f, %.3f] (all words empty or zero-length)",
            style,
            clip_start_s,
            clip_end_s,
        )
        return None

    subs = pysubs2.SSAFile()
    subs.info["PlayResX"] = str(play_res_x)
    subs.info["PlayResY"] = str(play_res_y)
    # ScaledBorderAndShadow=yes makes \bord values render the same regardless of
    # libass's internal scaling — necessary because PlayRes != output res in some
    # edge cases (e.g. someone overrides _OUTPUT_W/_OUTPUT_H later).
    subs.info["ScaledBorderAndShadow"] = "yes"

    subs.styles["Default"] = _base_style(
        style,
        play_res_y,
        caption_position=caption_position,
        face_bottom_px=face_bottom_px,
        baseline_frac=baseline_frac,
        min_bottom_margin_px=min_bottom_margin_px,
        top_margin_px=top_margin_px,
        face_avoid_pad_px=face_avoid_pad_px,
    )
    subs.events = events

    out_path.parent.mkdir(parents=True, exist_ok=True)
    subs.save(str(out_path))
    return out_path


# Karaoke styles — historically rendered dead-center (an5, the Issue-427
# defect); their default is now the bottom band at ``baseline_frac``.
_KARAOKE_STYLES = {"bold_pop", "bold_pop_highlight"}


def _scale_px(px: int | float, play_res_y: int) -> int:
    """Scale a PlayResY=1920 pixel value to the actual canvas height."""
    return round(px * play_res_y / _PLAY_RES_Y)


def _bottom_marginv(
    style: str,
    play_res_y: int,
    face_bottom_px: int | None,
    baseline_frac: float,
    min_bottom_margin_px: int,
    face_avoid_pad_px: int,
) -> int:
    """MarginV for a bottom-band caption: baseline at ``baseline_frac`` of the
    canvas height, pushed DOWN below a detected face when the face's chin
    intrudes into the caption line, and floored at ``min_bottom_margin_px`` so
    the block never sinks into the Shorts bottom-UI zone (~bottom 25%)."""
    font_size = _FONT_SIZE_ANIMATED if style in _KARAOKE_STYLES else _FONT_SIZE_MINIMAL
    # Conservative single-line height: font size + pop overshoot + outline.
    line_h = _scale_px(font_size * 1.5, play_res_y)
    marginv = round((1.0 - baseline_frac) * play_res_y)
    if face_bottom_px is not None:
        caption_top = play_res_y - marginv - line_h
        needed_top = face_bottom_px + _scale_px(face_avoid_pad_px, play_res_y)
        if needed_top > caption_top:
            marginv = play_res_y - needed_top - line_h
    floor = _scale_px(min_bottom_margin_px, play_res_y)
    if marginv < floor:
        if face_bottom_px is not None:
            logger.warning(
                "captions: face bottom %dpx too low for the caption band — "
                "clamping to the bottom-UI floor (face may overlap)",
                face_bottom_px,
            )
        marginv = floor
    return marginv


def _base_style(
    style: str,
    play_res_y: int = _PLAY_RES_Y,
    *,
    caption_position: str | None = None,
    face_bottom_px: int | None = None,
    baseline_frac: float = _DEFAULT_BASELINE_FRAC,
    min_bottom_margin_px: int = _DEFAULT_MIN_BOTTOM_MARGIN_PX,
    top_margin_px: int = _DEFAULT_TOP_MARGIN_PX,
    face_avoid_pad_px: int = _DEFAULT_FACE_AVOID_PAD_PX,
) -> pysubs2.SSAStyle:
    is_animated = style in {"bold_pop", "bold_pop_highlight", "gradient_slide"}
    if caption_position is not None and caption_position not in VALID_POSITIONS:
        logger.warning(
            "captions: unknown caption_position %r — using the style default", caption_position
        )
        caption_position = None

    if caption_position == "middle":
        # The pre-427 karaoke look, now an explicit creator choice.
        alignment, marginv = pysubs2.Alignment.MIDDLE_CENTER, 0
        if face_bottom_px is not None:
            logger.info("captions: middle position chosen — face avoidance does not apply")
    elif caption_position == "top":
        alignment = pysubs2.Alignment.TOP_CENTER
        marginv = _scale_px(top_margin_px, play_res_y)
    elif caption_position == "bottom" or style in _KARAOKE_STYLES:
        # Karaoke default (the Issue-427 fix) and the explicit bottom choice:
        # baseline at ~70% height with face avoidance.
        alignment = pysubs2.Alignment.BOTTOM_CENTER
        marginv = _bottom_marginv(
            style,
            play_res_y,
            face_bottom_px,
            baseline_frac,
            min_bottom_margin_px,
            face_avoid_pad_px,
        )
    else:
        # minimal / gradient_slide legacy default: lower-third margin scales
        # with the canvas height (Issue 182). At 1920 this is 290 (unchanged).
        alignment = pysubs2.Alignment.BOTTOM_CENTER
        marginv = _scale_px(290, play_res_y)

    return pysubs2.SSAStyle(
        fontname=_FONT_NAME,
        fontsize=_FONT_SIZE_ANIMATED if is_animated else _FONT_SIZE_MINIMAL,
        primarycolor=pysubs2.Color(0xFF, 0xFF, 0xFF),
        outlinecolor=pysubs2.Color(0x00, 0x00, 0x00),
        outline=_OUTLINE_PX,
        shadow=0,
        bold=-1,
        # \fscx/\fscy animations multiply against ScaleX/ScaleY — baseline MUST
        # be 100 or the Bold Pop pop lands at the wrong size.
        scalex=100.0,
        scaley=100.0,
        alignment=alignment,
        marginv=marginv,
    )


def _to_ms(seconds: float) -> int:
    # A non-finite (NaN/inf) word timestamp from a malformed transcript would crash
    # int(round(...)) (ValueError/OverflowError) and take down the whole caption
    # render. Treat it as 0 so the resulting zero-length event is dropped by the
    # start<end guards instead. (Defense-in-depth; _iter_clipped_words also filters.)
    if not math.isfinite(seconds):
        return 0
    return max(0, int(round(seconds * 1000)))


def _has_word_timing(segments: list[dict]) -> bool:
    return any(seg.get("words") for seg in segments)


def _iter_clipped_words(
    segments: list[dict], clip_start_s: float, clip_end_s: float
) -> Iterator[dict]:
    for seg in segments:
        for w in seg.get("words") or []:
            start = w.get("start", 0.0)
            end = w.get("end", 0.0)
            # Drop non-finite or inverted (end < start) word times at the source so
            # event builders never emit malformed ASS timestamps.
            if not (math.isfinite(start) and math.isfinite(end)) or end < start:
                continue
            if end <= clip_start_s or start >= clip_end_s:
                continue
            yield w


def _bold_pop_event(
    w: dict, clip_start_s: float, clip_end_s: float, prefix: str = ""
) -> pysubs2.SSAEvent | None:
    """Build one scale-pop Dialogue event for a single word, or ``None`` when the
    word is empty or zero-length in the clip window. ``prefix`` carries any extra
    override (e.g. a keyword ``\\c`` highlight) inserted after the pop animation."""
    text = (w.get("word") or "").strip()
    if not text:
        return None
    start_ms = _to_ms(max(0.0, w["start"] - clip_start_s))
    end_ms = _to_ms(min(clip_end_s, w["end"]) - clip_start_s)
    if end_ms <= start_ms:
        return None
    return pysubs2.SSAEvent(
        start=start_ms,
        end=end_ms,
        style="Default",
        text=f"{_POP_OVERRIDE}{prefix}{text}",
    )


def _normalize_token(word: str) -> str:
    """Lowercase a caption token and strip surrounding punctuation for matching."""
    return word.strip().strip(string.punctuation).lower()


def _salience(token_norm: str, original: str, clip_freq: Counter[str]) -> float:
    """YAKE-inspired per-token salience (higher = more keyword-like): longer
    content words, words repeated within the clip, and capitalized tokens (proper
    nouns / emphasis) score higher. Applied per phrase — see ``_keyword_indices``."""
    score = float(len(token_norm))
    score += clip_freq.get(token_norm, 0) * 2.0
    if original[:1].isupper():
        score += 3.0
    return score


def _keyword_indices(words: list[dict], clip_freq: Counter[str], top_n: int) -> set[int]:
    """Indices (into ``words``) of the top-``top_n`` salient, non-stopword tokens in
    one phrase. Empty when the phrase carries no salient token → plain fallback."""
    scored: list[tuple[float, int]] = []
    for i, w in enumerate(words):
        norm = _normalize_token(w.get("word") or "")
        if not norm or norm in _STOPWORDS or not any(c.isalpha() for c in norm):
            continue
        scored.append((_salience(norm, (w.get("word") or "").strip(), clip_freq), i))
    # Highest score first; ties broken by earliest position for stable output.
    scored.sort(key=lambda t: (-t[0], t[1]))
    return {i for _, i in scored[:top_n]}


def _group_words(words: list[dict], n: int, max_gap_s: float) -> Iterator[list[dict]]:
    """Split one segment's in-window words into karaoke groups of at most ``n``,
    breaking early when the inter-word silence exceeds ``max_gap_s``. Groups
    never cross segments — the caller iterates per segment (segment = phrase)."""
    group: list[dict] = []
    for w in words:
        if group and (
            len(group) >= n or w.get("start", 0.0) - group[-1].get("end", 0.0) > max_gap_s
        ):
            yield group
            group = []
        group.append(w)
    if group:
        yield group


def _grouped_pop_event(
    group: list[dict],
    clip_start_s: float,
    clip_end_s: float,
    kw_words: set[int],
) -> pysubs2.SSAEvent | None:
    """One scale-pop Dialogue for a word group: on screen from the first word's
    start to the last word's end. ``kw_words`` holds id()s of keyword-highlighted
    word dicts — highlighted inline with an explicit white RESET so the yellow
    never bleeds onto the rest of the group."""
    parts: list[str] = []
    for w in group:
        text = (w.get("word") or "").strip()
        if not text:
            continue
        if id(w) in kw_words:
            parts.append(f"{{\\c{_COLOR_HIGHLIGHT_ASS}}}{text}{{\\c{_COLOR_WHITE_ASS}}}")
        else:
            parts.append(text)
    if not parts:
        return None
    start_ms = _to_ms(max(0.0, group[0]["start"] - clip_start_s))
    end_ms = _to_ms(min(clip_end_s, group[-1]["end"]) - clip_start_s)
    if end_ms <= start_ms:
        return None
    return pysubs2.SSAEvent(
        start=start_ms, end=end_ms, style="Default", text=f"{_POP_OVERRIDE}{' '.join(parts)}"
    )


def _build_bold_pop(
    segments: list[dict],
    clip_start_s: float,
    clip_end_s: float,
    *,
    highlight: bool = False,
    words_per_group: int = 1,
    group_max_gap_s: float = 0.6,
) -> list[pysubs2.SSAEvent]:
    """Scale-pop Dialogue events — one per word (``words_per_group=1``, the
    legacy byte-identical output) or one per multi-word group (Issue 427:
    2-3-word groups are the Shorts readability norm). Falls back to Minimal
    when word-level timing is missing (per acceptance criterion).

    When ``highlight`` is set (``bold_pop_highlight``, Issue 183) the most salient
    token in each phrase is rendered in a punch-yellow ``\\c`` override; phrases
    with no salient token render as plain Bold Pop."""
    if not _has_word_timing(segments):
        return _build_minimal(segments, clip_start_s, clip_end_s)

    if words_per_group > 1:
        return _build_bold_pop_grouped(
            segments, clip_start_s, clip_end_s, highlight, words_per_group, group_max_gap_s
        )

    if not highlight:
        return [
            ev
            for w in _iter_clipped_words(segments, clip_start_s, clip_end_s)
            if (ev := _bold_pop_event(w, clip_start_s, clip_end_s)) is not None
        ]

    # Highlight path: score salience per phrase, colour the top token(s).
    clip_freq: Counter[str] = Counter(
        norm
        for w in _iter_clipped_words(segments, clip_start_s, clip_end_s)
        if (norm := _normalize_token(w.get("word") or "")) and norm not in _STOPWORDS
    )
    highlight_prefix = f"{{\\c{_COLOR_HIGHLIGHT_ASS}}}"
    events: list[pysubs2.SSAEvent] = []
    for seg in segments:
        words_in_clip = [
            w
            for w in seg.get("words") or []
            if w.get("end", 0.0) > clip_start_s and w.get("start", 0.0) < clip_end_s
        ]
        if not words_in_clip:
            continue
        kw_idx = _keyword_indices(words_in_clip, clip_freq, _HIGHLIGHT_TOP_N)
        for i, w in enumerate(words_in_clip):
            prefix = highlight_prefix if i in kw_idx else ""
            ev = _bold_pop_event(w, clip_start_s, clip_end_s, prefix)
            if ev is not None:
                events.append(ev)
    return events


def _build_bold_pop_grouped(
    segments: list[dict],
    clip_start_s: float,
    clip_end_s: float,
    highlight: bool,
    words_per_group: int,
    group_max_gap_s: float,
) -> list[pysubs2.SSAEvent]:
    """Grouped karaoke events (Issue 427): each group pops on appearance and
    stays until its last word ends. Groups never cross segment boundaries."""
    kw_words: set[int] = set()
    if highlight:
        clip_freq: Counter[str] = Counter(
            norm
            for w in _iter_clipped_words(segments, clip_start_s, clip_end_s)
            if (norm := _normalize_token(w.get("word") or "")) and norm not in _STOPWORDS
        )
    events: list[pysubs2.SSAEvent] = []
    for seg in segments:
        seg_words = list(_iter_clipped_words([seg], clip_start_s, clip_end_s))
        if not seg_words:
            continue
        if highlight:
            kw_idx = _keyword_indices(seg_words, clip_freq, _HIGHLIGHT_TOP_N)
            kw_words = {id(seg_words[i]) for i in kw_idx}
        for group in _group_words(seg_words, words_per_group, group_max_gap_s):
            ev = _grouped_pop_event(group, clip_start_s, clip_end_s, kw_words)
            if ev is not None:
                events.append(ev)
    return events


def _build_gradient_slide(
    segments: list[dict], clip_start_s: float, clip_end_s: float
) -> list[pysubs2.SSAEvent]:
    """Per-phrase accumulating Dialogue lines. Each new word fades in with an
    indigo→white colour transition; prior words stay at the Style default
    (white). Only one Dialogue is on screen at a time, so positioning is handled
    automatically by libass — no per-word ``\\pos()`` needed."""
    if not _has_word_timing(segments):
        return _build_minimal(segments, clip_start_s, clip_end_s)

    events: list[pysubs2.SSAEvent] = []
    indigo_to_white = (
        f"{{\\fad({_GRADIENT_FADE_IN_MS},0)"
        f"\\c{_COLOR_INDIGO_ASS}\\t(0,{_GRADIENT_COLOR_MS},\\c{_COLOR_WHITE_ASS})}}"
    )
    for seg in segments:
        words_in_clip = [
            w
            for w in seg.get("words") or []
            if w.get("end", 0.0) > clip_start_s and w.get("start", 0.0) < clip_end_s
        ]
        if not words_in_clip:
            continue
        phrase_end_s = min(clip_end_s, seg.get("end", clip_end_s))
        phrase_end_ms = _to_ms(phrase_end_s - clip_start_s)
        for i, w in enumerate(words_in_clip):
            new_word = (w.get("word") or "").strip()
            if not new_word:
                continue
            start_ms = _to_ms(max(0.0, w["start"] - clip_start_s))
            # End at next word's start so only ONE Dialogue line is visible at a
            # time — that line carries the accumulating phrase text.
            if i + 1 < len(words_in_clip):
                end_ms = _to_ms(max(0.0, words_in_clip[i + 1]["start"] - clip_start_s))
            else:
                end_ms = phrase_end_ms
            if end_ms <= start_ms:
                continue
            prior_words = [
                (wd.get("word") or "").strip()
                for wd in words_in_clip[:i]
                if (wd.get("word") or "").strip()
            ]
            prior_text = " ".join(prior_words)
            if prior_text:
                line_text = f"{prior_text} {indigo_to_white}{new_word}"
            else:
                line_text = f"{indigo_to_white}{new_word}"
            events.append(
                pysubs2.SSAEvent(
                    start=start_ms,
                    end=end_ms,
                    style="Default",
                    text=line_text,
                )
            )
    return events


def _build_minimal(
    segments: list[dict], clip_start_s: float, clip_end_s: float
) -> list[pysubs2.SSAEvent]:
    """Plain phrase-level Dialogue per transcript segment, no animation. Used
    directly for the ``minimal`` style and as the line-level fallback when word
    timing is absent."""
    events: list[pysubs2.SSAEvent] = []
    for seg in segments:
        seg_start_s = max(clip_start_s, seg.get("start", 0.0))
        seg_end_s = min(clip_end_s, seg.get("end", 0.0))
        if seg_end_s <= seg_start_s:
            continue
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        events.append(
            pysubs2.SSAEvent(
                start=_to_ms(seg_start_s - clip_start_s),
                end=_to_ms(seg_end_s - clip_start_s),
                style="Default",
                text=text,
            )
        )
    return events
