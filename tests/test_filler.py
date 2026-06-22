"""Tests for clip_engine.filler (Issue 134 — filler-word + silence removal).

Covers the load-bearing edges of the cut-list generator:
  - Tier-1 fillers excised unconditionally
  - Tier-2 fillers gated by flank-gap + max-duration
  - Multi-word Tier-2 phrase matching ("you know")
  - Silence excision with 150ms tail subtraction
  - Adjacent-cut merging + cut→keep inversion roundtrip
  - >30% warning threshold
  - Clip-window filtering
  - Pure-Python ffmpeg filter_complex shape (render_cleaned_clip_file)
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clip_engine.filler import (
    DEFAULT_TIER1_FILLERS,
    DEFAULT_TIER2_FILLERS,
    CutSegment,
    detect_cut_segments,
    invert_to_keep_ranges,
    merge_adjacent_cuts,
    percent_removed,
)


def _w(word: str, start: float, end: float) -> dict:
    return {"word": word, "start": start, "end": end}


# ── Tier 1: unconditional ─────────────────────────────────────────────────


def test_tier1_filler_excised_unconditionally():
    words = [_w("hello", 0.0, 0.5), _w("um", 0.6, 0.85), _w("world", 1.0, 1.4)]
    cuts = detect_cut_segments(words, clip_start_s=0.0, clip_end_s=2.0)
    assert len(cuts) == 1
    assert cuts[0].reason == "filler"
    assert cuts[0].word == "um"
    assert cuts[0].start_s == pytest.approx(0.6)
    assert cuts[0].end_s == pytest.approx(0.85)


def test_tier1_filler_normalised_against_punctuation_and_case():
    """Transcripts emit ``"Um,"`` and ``"Uh."`` — must still match the lexicon."""
    words = [_w("Um,", 0.0, 0.2), _w("Uh.", 0.5, 0.7)]
    cuts = detect_cut_segments(words, clip_start_s=0.0, clip_end_s=1.0)
    assert {c.word for c in cuts} == {"um", "uh"}


def test_tier1_lexicon_contains_expected_set():
    """Pin the load-bearing surface — if a teammate adds a marginal token to
    Tier 1, the breaking test prompts a DECISIONS entry."""
    assert {"um", "umm", "uh", "uhh", "mhm", "hmm", "er", "ah"}.issubset(DEFAULT_TIER1_FILLERS)


# ── Tier 2: pause-flanked ─────────────────────────────────────────────────


def test_tier2_filler_excised_when_pause_flanked():
    # "and like impossible" with a 300ms gap BEFORE "like" → excise.
    words = [
        _w("and", 0.0, 0.3),
        _w("like", 0.65, 0.85),
        _w("impossible", 0.95, 1.5),
    ]
    cuts = detect_cut_segments(words, clip_start_s=0.0, clip_end_s=2.0)
    assert any(c.word == "like" for c in cuts)


def test_tier2_filler_kept_when_no_flanking_pause():
    # "I like this" — no gap around "like" → KEEP (verb sense).
    words = [
        _w("I", 0.0, 0.1),
        _w("like", 0.15, 0.45),
        _w("this", 0.5, 0.75),
    ]
    cuts = detect_cut_segments(words, clip_start_s=0.0, clip_end_s=1.0)
    assert not any(c.word == "like" for c in cuts)


def test_tier2_filler_kept_when_token_too_long():
    """Deliberate 800ms "liiiiike" is presumed deliberate speech, not filler."""
    words = [
        _w("and", 0.0, 0.3),
        _w("like", 0.55, 1.55),  # 1000ms — over the 600ms cap
        _w("impossible", 1.75, 2.3),
    ]
    cuts = detect_cut_segments(words, clip_start_s=0.0, clip_end_s=3.0)
    assert not any(c.word == "like" for c in cuts)


def test_tier2_multi_word_phrase_matches():
    # "you know" must match as a phrase.
    words = [
        _w("it", 0.0, 0.2),
        _w("you", 0.45, 0.6),
        _w("know", 0.65, 0.85),
        _w("works", 1.05, 1.5),
    ]
    cuts = detect_cut_segments(words, clip_start_s=0.0, clip_end_s=2.0)
    assert any(c.word == "you know" for c in cuts)


def test_tier2_lexicon_contains_expected_phrases():
    assert {"like", "you know", "basically", "so", "right"}.issubset(DEFAULT_TIER2_FILLERS)


# ── Silence ───────────────────────────────────────────────────────────────


def test_silence_excised_with_tail_subtracted():
    # 1500ms gap between two words; 800ms threshold + 150ms tail each side
    # → cut is the inner [3.15, 3.85] = 700ms.
    words = [_w("hello", 0.0, 2.0), _w("world", 3.5, 4.0)]
    cuts = detect_cut_segments(words, clip_start_s=0.0, clip_end_s=5.0)
    silence_cuts = [c for c in cuts if c.reason == "silence"]
    assert len(silence_cuts) == 1
    assert silence_cuts[0].start_s == pytest.approx(2.15)
    assert silence_cuts[0].end_s == pytest.approx(3.35)


def test_silence_under_threshold_kept():
    # 500ms gap < 800ms threshold → no cut.
    words = [_w("hello", 0.0, 1.0), _w("world", 1.5, 2.0)]
    cuts = detect_cut_segments(words, clip_start_s=0.0, clip_end_s=3.0)
    assert not any(c.reason == "silence" for c in cuts)


# ── Merge + invert ────────────────────────────────────────────────────────


def test_merge_collapses_adjacent_cuts():
    cuts = [
        CutSegment(0.0, 0.5, "filler", "um"),
        CutSegment(0.5, 0.8, "filler", "uh"),
        CutSegment(2.0, 2.5, "silence", None),
    ]
    merged = merge_adjacent_cuts(cuts)
    assert len(merged) == 2
    assert merged[0].start_s == 0.0
    assert merged[0].end_s == 0.8
    assert merged[0].reason == "filler"
    # Filler+silence merge tags as silence (it dominates audio character).
    cuts2 = [
        CutSegment(0.0, 0.5, "filler", "um"),
        CutSegment(0.5, 1.0, "silence", None),
    ]
    merged2 = merge_adjacent_cuts(cuts2)
    assert len(merged2) == 1
    assert merged2[0].reason == "silence"


def test_invert_to_keep_ranges_complementary():
    cuts = [CutSegment(0.5, 1.0, "filler", "um"), CutSegment(2.0, 2.5, "silence", None)]
    keeps = invert_to_keep_ranges(cuts, 0.0, 3.0)
    assert keeps == [(0.0, 0.5), (1.0, 2.0), (2.5, 3.0)]


def test_invert_with_no_cuts_returns_full_range():
    assert invert_to_keep_ranges([], 0.0, 10.0) == [(0.0, 10.0)]


def test_invert_drops_zero_width_keep_segments():
    """A cut starting at clip_start_s must not produce a (0,0) keep range
    that ffmpeg would reject."""
    cuts = [CutSegment(0.0, 0.5, "filler", "um")]
    keeps = invert_to_keep_ranges(cuts, 0.0, 1.0)
    assert keeps == [(0.5, 1.0)]


# ── Percent removed ──────────────────────────────────────────────────────


def test_percent_removed_drives_warning_threshold():
    # 3.5s of cuts over 10s → 35%, above the warning threshold.
    cuts = [
        CutSegment(0.0, 1.5, "silence", None),
        CutSegment(3.0, 5.0, "silence", None),
    ]
    assert percent_removed(cuts, 10.0) == pytest.approx(35.0)


def test_percent_removed_zero_duration_safe():
    assert percent_removed([], 0.0) == 0.0


# ── Window + empty edges ─────────────────────────────────────────────────


def test_empty_input_returns_empty():
    assert detect_cut_segments([], 0.0, 10.0) == []
    assert detect_cut_segments([_w("hello", 0, 1)], 5.0, 5.0) == []


def test_out_of_window_words_skipped():
    words = [_w("um", 0.0, 0.3), _w("uh", 100.0, 100.5)]
    cuts = detect_cut_segments(words, clip_start_s=0.0, clip_end_s=10.0)
    assert len(cuts) == 1
    assert cuts[0].start_s == pytest.approx(0.0)


# ── render_cleaned_clip_file ─────────────────────────────────────────────


def test_render_cleaned_clip_file_builds_filter_complex_script(tmp_path):
    """The cleaning render must invoke ffmpeg with -filter_complex_script
    pointing at a file containing trim/atrim/concat for each keep range,
    plus the 5ms afade for click prevention."""
    from clip_engine.render import render_cleaned_clip_file

    captured = {}

    def _fake_run(cmd, label, timeout_s=120.0):
        captured["cmd"] = cmd
        # Capture the script content before the finally-cleanup unlinks it.
        idx = cmd.index("-filter_complex_script")
        captured["script_path"] = cmd[idx + 1]
        captured["script_text"] = Path(cmd[idx + 1]).read_text()

    # Loudnorm measurement (Issue 181) is exercised in test_render.py; here we
    # stub it to None so this test stays a hermetic check of the cut-graph shape.
    with (
        patch("clip_engine.render._run", _fake_run),
        patch("clip_engine.render._measure_loudnorm_filter", return_value=None),
    ):
        render_cleaned_clip_file(
            source_path=Path("/fake/src.mp4"),
            keep_ranges=[(0.0, 2.5), (3.0, 8.0)],
            out_path=tmp_path / "out.mp4",
        )

    cmd = captured["cmd"]
    assert "-filter_complex_script" in cmd
    assert "-map" in cmd and "[outv]" in cmd and "[outa]" in cmd
    script = captured["script_text"]
    assert "trim=start=0.000:end=2.500" in script
    assert "trim=start=3.000:end=8.000" in script
    assert "afade=t=in:st=0:d=0.005" in script
    assert "afade=t=out:" in script
    assert "concat=n=2:v=1:a=1[outv][outa]" in script
    # Script file must be cleaned up after run.
    assert not Path(captured["script_path"]).exists()


def test_render_cleaned_clip_file_rejects_empty_keep_ranges(tmp_path):
    from clip_engine.render import render_cleaned_clip_file

    with pytest.raises(ValueError):
        render_cleaned_clip_file(
            source_path=Path("/fake/src.mp4"),
            keep_ranges=[],
            out_path=tmp_path / "out.mp4",
        )


def test_render_cleaned_clip_file_rejects_invalid_range(tmp_path):
    from clip_engine.render import render_cleaned_clip_file

    with pytest.raises(ValueError):
        render_cleaned_clip_file(
            source_path=Path("/fake/src.mp4"),
            keep_ranges=[(2.0, 1.0)],
            out_path=tmp_path / "out.mp4",
        )


# ── End-to-end roundtrip ─────────────────────────────────────────────────


def test_detect_merge_invert_roundtrip_yields_disjoint_keep_ranges():
    """A realistic transcript: cuts overlap silences, get merged, invert to
    disjoint keep ranges with no zero-width segments. This is the
    invariant the ffmpeg filter graph depends on."""
    words = [
        _w("hello", 0.0, 0.5),
        _w("um", 0.55, 0.75),
        _w("uh", 0.8, 1.0),
        _w("world", 3.0, 3.5),  # 2.0s silence between 1.0 and 3.0
    ]
    cuts = detect_cut_segments(words, clip_start_s=0.0, clip_end_s=4.0)
    merged = merge_adjacent_cuts(cuts)
    keeps = invert_to_keep_ranges(merged, 0.0, 4.0)
    # All keeps must be strictly positive-width and ordered.
    for s, e in keeps:
        assert e > s
    for prev, nxt in zip(keeps, keeps[1:], strict=False):
        assert prev[1] <= nxt[0]
    # First keep must end before the first cut starts.
    assert keeps[0][1] <= cuts[0].start_s


def _mock_creator():
    from models import Creator

    c = MagicMock(spec=Creator)
    c.id = __import__("uuid").uuid4()
    c.minutes_balance = 100
    return c


def test_clean_preview_endpoint_returns_cuts(client):
    """End-to-end: GET /clips/{id}/clean-preview returns the cut list,
    populates the warning when percent_removed >= 30%."""
    import uuid as _uuid
    from unittest.mock import AsyncMock

    from auth import get_current_creator
    from db import get_session
    from main import app
    from models import Clip, RenderStatus, Transcript

    creator = _mock_creator()
    clip_id = _uuid.uuid4()
    video_id = _uuid.uuid4()
    clip = MagicMock(spec=Clip)
    clip.id = clip_id
    clip.creator_id = creator.id
    clip.video_id = video_id
    clip.setup_start_s = 0.0
    clip.start_s = 0.0
    clip.end_s = 10.0
    clip.render_status = RenderStatus.done
    clip.render_uri = "clips/x.mp4"
    clip.cleaned_render_uri = None
    transcript = MagicMock(spec=Transcript)
    transcript.segments_jsonb = {
        "segments": [
            {
                "start": 0.0,
                "end": 10.0,
                "text": "hello um world",
                "words": [
                    {"word": "hello", "start": 0.0, "end": 1.0},
                    {"word": "um", "start": 1.05, "end": 1.3},
                    {"word": "world", "start": 5.0, "end": 5.5},
                ],
            }
        ]
    }

    async def _session():
        s = AsyncMock()
        s.get = AsyncMock(side_effect=[clip, transcript])
        yield s

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session
    try:
        resp = client.get(f"/clips/{clip_id}/clean-preview")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["clip_id"] == str(clip_id)
    assert any(c["reason"] == "filler" and c["word"] == "um" for c in data["cuts"])
    # 5s gap between 1.3 and 5.0 is well above the 800ms threshold → silence cut.
    assert any(c["reason"] == "silence" for c in data["cuts"])
    # Should breach the 30% warning threshold.
    assert data["percent_removed"] >= 30.0
    assert data["warning"] is not None


def test_clean_confirm_idempotent_when_no_cleaned_uri(client):
    """Confirming when there's nothing to swap returns 200 + status=noop —
    not 400 — so router-retry is safe."""
    import uuid as _uuid
    from unittest.mock import AsyncMock

    from auth import get_current_creator
    from db import get_session
    from main import app
    from models import Clip, RenderStatus

    creator = _mock_creator()
    clip_id = _uuid.uuid4()
    clip = MagicMock(spec=Clip)
    clip.id = clip_id
    clip.creator_id = creator.id
    clip.render_status = RenderStatus.done
    clip.render_uri = "clips/x.mp4"
    clip.cleaned_render_uri = None

    async def _session():
        s = AsyncMock()
        s.get = AsyncMock(return_value=clip)
        s.commit = AsyncMock()
        yield s

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session
    try:
        resp = client.post(f"/clips/{clip_id}/clean/confirm")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json()["status"] == "noop"


def test_clean_rejects_when_cleaned_render_uri_already_set(client):
    """Issue-135 audit fix: /clean returns 409 when a cleaned/edited
    artifact is already pending. Otherwise the worker's idempotency probe
    silently no-ops and the user's request is lost without an error."""
    import uuid as _uuid
    from unittest.mock import AsyncMock

    from auth import get_current_creator
    from billing.ledger import check_positive_balance
    from db import get_session
    from main import app
    from models import Clip, RenderStatus

    creator = _mock_creator()
    clip_id = _uuid.uuid4()
    clip = MagicMock(spec=Clip)
    clip.id = clip_id
    clip.creator_id = creator.id
    clip.render_status = RenderStatus.done
    clip.render_uri = "clips/x.mp4"
    clip.cleaned_render_uri = "clips/x_clean.mp4"  # already pending

    async def _session():
        s = AsyncMock()
        s.get = AsyncMock(return_value=clip)
        yield s

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[check_positive_balance] = AsyncMock(return_value=None)
    try:
        with patch("routers.clips.check_positive_balance", AsyncMock(return_value=None)):
            resp = client.post(f"/clips/{clip_id}/clean")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "pending_clean_or_edit"
