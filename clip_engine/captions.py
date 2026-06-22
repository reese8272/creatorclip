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

References (Phase-1 research, ``docs/DECISIONS.md`` 2026-06-07):
  - libass + pysubs2 is the production standard (Submagic/Opus.pro/CapCut pattern).
  - ASS colors are ``&HBBGGRR&`` — reversed from HTML hex.
  - ``\\t(start_ms,end_ms, tags)`` is the ASS animated-transform override.
  - Style ScaleX/ScaleY MUST be 100 baseline or ``\\t(\\fscx120)`` multiplies wrong.
  - Lower-third (~y=1350 at PlayResY=1920) keeps captions clear of the Shorts
    subscribe-overlay zone and the speaker's face.
"""

from __future__ import annotations

import logging
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


def build_ass_subtitles(
    segments: list[dict] | None,
    style: str,
    clip_start_s: float,
    clip_duration_s: float,
    out_path: Path,
    play_res_x: int = _PLAY_RES_X,
    play_res_y: int = _PLAY_RES_Y,
) -> Path | None:
    """Render an ASS subtitle file for the clip window.

    ``segments`` is the full transcript segment list (WhisperX shape — each
    segment carries ``start``, ``end``, ``text``, optional ``words``). Word-level
    timing drives ``bold_pop`` / ``gradient_slide``; when absent the renderer falls
    back to segment-level lines (Minimal style).

    Returns the written path, or ``None`` when there is no usable text in the
    clip window or the style is unknown — callers must handle ``None`` by
    skipping the ``subtitles=`` filter rather than failing the render.
    """
    if style not in VALID_STYLES:
        logger.warning("captions: unknown style %r — no subtitles generated", style)
        return None
    if not segments or clip_duration_s <= 0:
        return None

    clip_end_s = clip_start_s + clip_duration_s

    clipped = [
        seg
        for seg in segments
        if seg.get("end", 0.0) > clip_start_s and seg.get("start", 0.0) < clip_end_s
    ]
    if not clipped:
        return None

    if style == "bold_pop":
        events = _build_bold_pop(clipped, clip_start_s, clip_end_s)
    elif style == "bold_pop_highlight":
        events = _build_bold_pop(clipped, clip_start_s, clip_end_s, highlight=True)
    elif style == "gradient_slide":
        events = _build_gradient_slide(clipped, clip_start_s, clip_end_s)
    else:
        events = _build_minimal(clipped, clip_start_s, clip_end_s)

    if not events:
        return None

    subs = pysubs2.SSAFile()
    subs.info["PlayResX"] = str(play_res_x)
    subs.info["PlayResY"] = str(play_res_y)
    # ScaledBorderAndShadow=yes makes \bord values render the same regardless of
    # libass's internal scaling — necessary because PlayRes != output res in some
    # edge cases (e.g. someone overrides _OUTPUT_W/_OUTPUT_H later).
    subs.info["ScaledBorderAndShadow"] = "yes"

    subs.styles["Default"] = _base_style(style, play_res_y)
    subs.events = events

    out_path.parent.mkdir(parents=True, exist_ok=True)
    subs.save(str(out_path))
    return out_path


_CENTERED_STYLES = {"bold_pop", "bold_pop_highlight"}


def _base_style(style: str, play_res_y: int = _PLAY_RES_Y) -> pysubs2.SSAStyle:
    is_animated = style in {"bold_pop", "bold_pop_highlight", "gradient_slide"}
    # Lower-third margin scales with the canvas height so the safe-zone fraction
    # holds across export presets (Issue 182). At 1920 this is 290 (unchanged).
    lower_third_marginv = round(290 * play_res_y / _PLAY_RES_Y)
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
        # an5 = middle-center (Bold Pop's centered-on-face placement).
        # an2 = bottom-center (Minimal / Gradient Slide — lower-third).
        alignment=pysubs2.Alignment.MIDDLE_CENTER
        if style in _CENTERED_STYLES
        else pysubs2.Alignment.BOTTOM_CENTER,
        # MarginV lifts bottom-aligned text into the lower-third safe zone,
        # clear of the Shorts subscribe button overlay (~y=70% of 1920).
        marginv=0 if style in _CENTERED_STYLES else lower_third_marginv,
    )


def _to_ms(seconds: float) -> int:
    return max(0, int(round(seconds * 1000)))


def _has_word_timing(segments: list[dict]) -> bool:
    return any(seg.get("words") for seg in segments)


def _iter_clipped_words(
    segments: list[dict], clip_start_s: float, clip_end_s: float
) -> Iterator[dict]:
    for seg in segments:
        for w in seg.get("words") or []:
            if w.get("end", 0.0) <= clip_start_s or w.get("start", 0.0) >= clip_end_s:
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


def _build_bold_pop(
    segments: list[dict], clip_start_s: float, clip_end_s: float, *, highlight: bool = False
) -> list[pysubs2.SSAEvent]:
    """One Dialogue per word, scale-pop animation. Falls back to Minimal when
    word-level timing is missing (per acceptance criterion).

    When ``highlight`` is set (``bold_pop_highlight``, Issue 183) the most salient
    token in each phrase is rendered in a punch-yellow ``\\c`` override; phrases
    with no salient token render as plain Bold Pop. With ``highlight=False`` the
    output is byte-identical to the original Bold Pop."""
    if not _has_word_timing(segments):
        return _build_minimal(segments, clip_start_s, clip_end_s)

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
