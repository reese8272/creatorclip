"""Tests for clip_engine.captions (Issue 133 — animated word-level captions).

Covers the load-bearing edges:
  - ASS file structure: PlayResX/Y, Default Style, Dialogue events present
  - Word-level Bold Pop event count + scale-pop override tag
  - Gradient Slide accumulating phrase + indigo→white color animation
  - Minimal style maps 1:1 with transcript segments
  - Brand indigo encoded as ASS &Hd26a5e& (NOT HTML &H5e6ad2&)
  - Graceful line-level fallback when word timestamps absent
  - Clip window filtering (out-of-range words/segments skipped)
  - Style ScaleX/ScaleY baseline is 100 (Bold Pop pop relies on this)
  - Style enum rejection: unknown style returns None
"""

from pathlib import Path

import pysubs2
import pytest

from clip_engine.captions import VALID_STYLES, build_ass_subtitles


def _segments(words_per_seg: int = 3) -> list[dict]:
    """Two segments × N words each, contiguous time. Easy to slice on time."""
    seg1_words = [
        {"word": f"w{i}", "start": float(i) * 0.5, "end": float(i) * 0.5 + 0.45}
        for i in range(words_per_seg)
    ]
    seg2_words = [
        {
            "word": f"u{i}",
            "start": 2.0 + float(i) * 0.5,
            "end": 2.0 + float(i) * 0.5 + 0.45,
        }
        for i in range(words_per_seg)
    ]
    return [
        {
            "start": seg1_words[0]["start"],
            "end": seg1_words[-1]["end"],
            "text": " ".join(w["word"] for w in seg1_words),
            "words": seg1_words,
        },
        {
            "start": seg2_words[0]["start"],
            "end": seg2_words[-1]["end"],
            "text": " ".join(w["word"] for w in seg2_words),
            "words": seg2_words,
        },
    ]


def test_valid_styles_set_is_the_documented_styles():
    """VALID_STYLES is the load-bearing surface the worker checks against —
    keep it pinned so a typo in a new style entry doesn't silently slip in."""
    assert {"bold_pop", "bold_pop_highlight", "gradient_slide", "minimal"} == VALID_STYLES


def test_unknown_style_returns_none(tmp_path):
    out = build_ass_subtitles(
        segments=_segments(),
        style="not_a_real_style",
        clip_start_s=0.0,
        clip_duration_s=10.0,
        out_path=tmp_path / "out.ass",
    )
    assert out is None
    assert not (tmp_path / "out.ass").exists()


def test_empty_segments_returns_none(tmp_path):
    out = build_ass_subtitles(
        segments=[],
        style="bold_pop",
        clip_start_s=0.0,
        clip_duration_s=10.0,
        out_path=tmp_path / "out.ass",
    )
    assert out is None


def test_none_segments_returns_none(tmp_path):
    out = build_ass_subtitles(
        segments=None,
        style="bold_pop",
        clip_start_s=0.0,
        clip_duration_s=10.0,
        out_path=tmp_path / "out.ass",
    )
    assert out is None


def test_clip_window_outside_all_segments_returns_none(tmp_path):
    """Segments end before clip starts → no Dialogue lines → no file."""
    out = build_ass_subtitles(
        segments=_segments(),
        style="bold_pop",
        clip_start_s=100.0,
        clip_duration_s=10.0,
        out_path=tmp_path / "out.ass",
    )
    assert out is None


def test_ass_file_structure_bold_pop(tmp_path):
    """The generated ASS file declares 1080×1920 PlayRes, a Default style with
    Anton + ScaleX/Y=100 baseline, and one Dialogue per in-window word."""
    out_path = tmp_path / "bold_pop.ass"
    result = build_ass_subtitles(
        segments=_segments(words_per_seg=3),
        style="bold_pop",
        clip_start_s=0.0,
        clip_duration_s=5.0,
        out_path=out_path,
    )
    assert result == out_path
    subs = pysubs2.load(str(out_path))
    assert subs.info.get("PlayResX") == "1080"
    assert subs.info.get("PlayResY") == "1920"
    assert "Default" in subs.styles
    style = subs.styles["Default"]
    assert style.fontname == "Anton"
    # ScaleX/Y must be 100 baseline or \\t(\\fscx120) pop animates from the wrong size.
    assert style.scalex == 100.0
    assert style.scaley == 100.0
    # One Dialogue event per word (2 segments × 3 words = 6 events).
    assert len(subs.events) == 6


def test_bold_pop_emits_scale_pop_override_tag(tmp_path):
    """Each Bold Pop Dialogue carries the \\t(...,\\fscx120\\fscy120) pop tag."""
    out_path = tmp_path / "out.ass"
    build_ass_subtitles(
        segments=_segments(),
        style="bold_pop",
        clip_start_s=0.0,
        clip_duration_s=5.0,
        out_path=out_path,
    )
    raw = out_path.read_text()
    # The animated transform override is the load-bearing tag — without it
    # Bold Pop is just a plain caption.
    assert "\\fscx120\\fscy120" in raw
    assert "\\fscx100\\fscy100" in raw  # return to baseline


def _kw_segments(words: list[tuple[str, float, float]]) -> list[dict]:
    """One segment from explicit (text, start, end) word tuples — lets a test
    control exactly which token should win the salience ranking."""
    return [
        {
            "start": words[0][1],
            "end": words[-1][2],
            "text": " ".join(t for t, _, _ in words),
            "words": [{"word": t, "start": s, "end": e} for t, s, e in words],
        }
    ]


def test_bold_pop_highlight_colors_the_salient_keyword(tmp_path):
    """The most salient content word per phrase is wrapped in the punch-yellow
    \\c override; stopwords ('the') and shorter words ('amazing') are not."""
    out_path = tmp_path / "hl.ass"
    build_ass_subtitles(
        segments=_kw_segments(
            [("The", 0.0, 0.4), ("amazing", 0.5, 0.9), ("breakthrough", 1.0, 1.6)]
        ),
        style="bold_pop_highlight",
        clip_start_s=0.0,
        clip_duration_s=5.0,
        out_path=out_path,
    )
    raw = out_path.read_text()
    assert "&H00d4ff&" in raw  # ≥1 colored keyword for the phrase
    assert raw.count("&H00d4ff&") == 1  # exactly the top token, not every word
    # The highlight is attached to the keyword, not a stopword/filler.
    assert "&H00d4ff&}breakthrough" in raw
    assert "\\fscx120\\fscy120" in raw  # still Bold Pop underneath


def test_bold_pop_highlight_falls_back_to_plain_when_all_stopwords(tmp_path):
    """A phrase of only stopwords highlights nothing but still renders captions."""
    out_path = tmp_path / "plain.ass"
    build_ass_subtitles(
        segments=_kw_segments([("the", 0.0, 0.4), ("and", 0.5, 0.9), ("of", 1.0, 1.4)]),
        style="bold_pop_highlight",
        clip_start_s=0.0,
        clip_duration_s=5.0,
        out_path=out_path,
    )
    raw = out_path.read_text()
    assert "&H00d4ff&" not in raw  # nothing salient → no highlight
    assert "\\fscx120\\fscy120" in raw  # but the Bold Pop events still emit


def test_bold_pop_style_is_unchanged_by_highlight_feature(tmp_path):
    """The original bold_pop style must never emit the highlight color."""
    out_path = tmp_path / "plainbp.ass"
    build_ass_subtitles(
        segments=_kw_segments(
            [("The", 0.0, 0.4), ("amazing", 0.5, 0.9), ("breakthrough", 1.0, 1.6)]
        ),
        style="bold_pop",
        clip_start_s=0.0,
        clip_duration_s=5.0,
        out_path=out_path,
    )
    assert "&H00d4ff&" not in out_path.read_text()


def test_gradient_slide_emits_indigo_to_white_color_animation(tmp_path):
    """ASS color byte order is &HBBGGRR& — #5e6ad2 indigo becomes &Hd26a5e&.
    Confirm we did NOT accidentally write the HTML-hex byte order."""
    out_path = tmp_path / "out.ass"
    build_ass_subtitles(
        segments=_segments(),
        style="gradient_slide",
        clip_start_s=0.0,
        clip_duration_s=5.0,
        out_path=out_path,
    )
    raw = out_path.read_text()
    assert "\\c&Hd26a5e&" in raw  # indigo, correct byte order
    assert "\\c&Hffffff&" in raw  # white target
    # Guard against the easy mistake of writing the HTML byte order.
    assert "\\c&H5e6ad2&" not in raw
    # And the color-transition override must be present.
    assert "\\t(0,300,\\c&Hffffff&)" in raw


def test_gradient_slide_accumulates_phrase_words(tmp_path):
    """Each Gradient Slide Dialogue contains all prior words in the phrase
    plus the new word with the animation override applied — verifies the
    accumulating-phrase pattern (not a single-word-only pattern)."""
    out_path = tmp_path / "out.ass"
    build_ass_subtitles(
        segments=_segments(words_per_seg=3),
        style="gradient_slide",
        clip_start_s=0.0,
        clip_duration_s=5.0,
        out_path=out_path,
    )
    subs = pysubs2.load(str(out_path))
    # First segment yields 3 events: "w0", "w0 …w1", "w0 w1 …w2".
    seg1_events = [e for e in subs.events if e.text.startswith("w0") or "w0" in e.text]
    assert len(seg1_events) >= 3
    # The third event of the first phrase must carry both prior words as
    # plain text, and ONLY the newest word inside the override block.
    third = subs.events[2]
    assert "w0 w1" in third.text
    assert "w2" in third.text
    # The override is always on the newest word, not the accumulated prefix.
    assert third.text.index("w2") > third.text.index("\\c&Hd26a5e&")


def test_minimal_style_one_event_per_segment(tmp_path):
    """Minimal style is segment-level — no per-word events even when words
    are available."""
    out_path = tmp_path / "out.ass"
    build_ass_subtitles(
        segments=_segments(words_per_seg=4),
        style="minimal",
        clip_start_s=0.0,
        clip_duration_s=10.0,
        out_path=out_path,
    )
    subs = pysubs2.load(str(out_path))
    assert len(subs.events) == 2  # two segments
    # No animated-transform tags in any Dialogue line.
    for ev in subs.events:
        assert "\\t(" not in ev.text
        assert "\\fscx" not in ev.text


def test_word_timestamps_missing_falls_back_to_line_level(tmp_path):
    """Acceptance criterion: when word_timestamps are absent the animated
    styles must fall back to segment-level captions (Minimal-equivalent)
    rather than producing an empty file."""
    segments_no_words = [
        {"start": 0.0, "end": 2.0, "text": "first phrase"},
        {"start": 2.0, "end": 4.0, "text": "second phrase"},
    ]
    out_path = tmp_path / "fallback.ass"
    result = build_ass_subtitles(
        segments=segments_no_words,
        style="bold_pop",
        clip_start_s=0.0,
        clip_duration_s=5.0,
        out_path=out_path,
    )
    assert result == out_path
    subs = pysubs2.load(str(out_path))
    # Falls back to one Dialogue per segment, no pop animation.
    assert len(subs.events) == 2
    for ev in subs.events:
        assert "\\fscx120" not in ev.text


def test_clip_window_filters_out_of_range_words(tmp_path):
    """A word at t=10s should not appear in a clip [0–5s]. The timestamps in
    the file must be relative to clip_start_s (0 at clip start)."""
    segments = [
        {
            "start": 0.0,
            "end": 12.0,
            "text": "early late",
            "words": [
                {"word": "early", "start": 1.0, "end": 1.5},
                {"word": "late", "start": 10.0, "end": 10.5},
            ],
        }
    ]
    out_path = tmp_path / "out.ass"
    build_ass_subtitles(
        segments=segments,
        style="bold_pop",
        clip_start_s=0.0,
        clip_duration_s=5.0,
        out_path=out_path,
    )
    subs = pysubs2.load(str(out_path))
    # Only "early" should survive the clip window filter.
    assert len(subs.events) == 1
    assert "early" in subs.events[0].text
    assert "late" not in subs.events[0].text


def test_clip_start_offset_zeroes_word_timestamps(tmp_path):
    """A word at video-time 60.5s in a clip starting at 60.0s should land
    at ASS timestamp 500ms — not 60500ms — so libass renders it at the
    correct point inside the clip, not 60s past clip start."""
    segments = [
        {
            "start": 60.0,
            "end": 61.0,
            "text": "hello",
            "words": [{"word": "hello", "start": 60.5, "end": 60.8}],
        }
    ]
    out_path = tmp_path / "out.ass"
    build_ass_subtitles(
        segments=segments,
        style="bold_pop",
        clip_start_s=60.0,
        clip_duration_s=5.0,
        out_path=out_path,
    )
    subs = pysubs2.load(str(out_path))
    assert len(subs.events) == 1
    # pysubs2 stores start/end in milliseconds.
    assert subs.events[0].start == 500
    assert subs.events[0].end == 800


@pytest.mark.parametrize("style", ["bold_pop", "bold_pop_highlight", "gradient_slide", "minimal"])
def test_each_style_produces_loadable_ass(tmp_path, style):
    """Smoke test — every documented style produces a libass-loadable file
    with at least one Dialogue event for a non-trivial transcript."""
    out_path = tmp_path / f"{style}.ass"
    build_ass_subtitles(
        segments=_segments(),
        style=style,
        clip_start_s=0.0,
        clip_duration_s=10.0,
        out_path=out_path,
    )
    assert out_path.exists()
    subs = pysubs2.load(str(out_path))
    assert len(subs.events) >= 1
    assert "Default" in subs.styles


def test_output_path_directory_is_created(tmp_path):
    """build_ass_subtitles must create the parent directory if missing —
    callers may pass a nested temp dir that hasn't been mkdir'd yet."""
    out_path = tmp_path / "nested" / "deeper" / "out.ass"
    result = build_ass_subtitles(
        segments=_segments(),
        style="minimal",
        clip_start_s=0.0,
        clip_duration_s=10.0,
        out_path=out_path,
    )
    assert result == out_path
    assert out_path.exists()


def test_negative_duration_returns_none(tmp_path):
    out = build_ass_subtitles(
        segments=_segments(),
        style="bold_pop",
        clip_start_s=0.0,
        clip_duration_s=0.0,
        out_path=tmp_path / "out.ass",
    )
    assert out is None


def test_render_clip_file_skips_subtitles_when_transcript_missing(tmp_path):
    """When subtitle style is selected but no transcript_segments arrive,
    the render proceeds without a subtitles= filter — Issue 133 explicitly
    requires graceful degradation."""
    from unittest.mock import MagicMock, patch

    from clip_engine.render import render_clip_file

    called = {}

    def _fake_run(cmd, label, timeout_s=120.0):
        called["cmd"] = cmd

    with (
        patch("clip_engine.render._run", _fake_run),
        patch("clip_engine.render._frame_dimensions", return_value=(1920, 1080)),
        patch("clip_engine.render._extract_keyframe"),
        patch("clip_engine.render._detect_face_center_x", return_value=960),
        patch("tempfile.NamedTemporaryFile") as mock_tmp,
    ):
        mock_tmp.return_value.__enter__ = lambda s: s
        mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
        mock_tmp.return_value.name = str(tmp_path / "kf.jpg")

        render_clip_file(
            source_path=Path("/fake/source.mp4"),
            start_s=0.0,
            end_s=10.0,
            out_path=tmp_path / "out.mp4",
            style_preset={"subtitle": "bold_pop"},
            transcript_segments=None,
        )

    vf = called["cmd"][called["cmd"].index("-vf") + 1]
    assert "subtitles=" not in vf
