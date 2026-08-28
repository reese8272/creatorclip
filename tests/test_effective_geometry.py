"""Issue 470 — trim/clean must not leave stale clip geometry behind the swap.

After a trim+confirm the delivered video is the concatenation of kept segments,
but `start_s`/`end_s`/`setup_start_s` keep the source window. These tests pin:

  - the geometry helpers (compose across a second trim, word projection)
  - duration readers report the DELIVERED duration, not end_s - origin
  - /transcript returns only kept-segment words, mapped to delivered time
  - /clean/confirm persists composed effective geometry inside the Issue-468
    CAS UPDATE and clears the pending record with it
  - a re-render of a confirmed-clean clip does not silently resurrect the
    pre-trim window: the Issue-353 reset also clears the baked-edit record and
    the 202 says so (`discarded_edits`), and a failed enqueue restores it
  - the edit/clean workers write `pending_geometry_jsonb` in the same commit
    as `cleaned_render_uri` (integration-lane covers the full swap round trip)
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

from tests._helpers import owned_result

# Fixture geometry used throughout: a 40 s clip (origin 10 → end 50) trimmed to
# keep [0, 10] + [20, 30] clip-relative — a middle cut. Delivered duration 20 s.
_KEEP = [(0.0, 10.0), (20.0, 30.0)]


def _geometry():
    from clip_engine.edits import geometry_doc

    return geometry_doc(_KEEP)


def _mock_creator():
    from models import Creator

    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    c.minutes_balance = 100
    return c


def _mock_clip(creator_id, effective_geometry=None):
    from models import Clip, ClipTriage, RenderStatus

    clip = MagicMock(spec=Clip)
    clip.id = uuid.uuid4()
    clip.creator_id = creator_id
    clip.video_id = uuid.uuid4()
    clip.setup_start_s = 10.0
    clip.start_s = 12.0
    clip.end_s = 50.0
    clip.peak_s = 35.0
    clip.score = 0.8
    clip.rank = 1
    clip.signals_jsonb = {}
    clip.render_status = RenderStatus.done
    clip.render_uri = "clips/x.mp4"
    clip.cleaned_render_uri = None
    clip.pending_geometry_jsonb = None
    clip.effective_geometry_jsonb = effective_geometry
    clip.style_preset = None
    clip.applied_title = None
    clip.applied_description = None
    clip.suggested_title = None
    clip.suggested_description = None
    clip.suggested_hook = None
    clip.poster_uri = None
    clip.reframe_track_jsonb = None
    clip.triage = ClipTriage.pending
    clip.downloaded_at = None
    return clip


# ── Pure helpers ─────────────────────────────────────────────────────────────


def test_parse_geometry_round_trip_and_duration():
    from clip_engine.edits import geometry_doc, geometry_duration_s, parse_geometry

    doc = geometry_doc(_KEEP)
    assert parse_geometry(doc) == _KEEP
    assert geometry_duration_s(doc) == 20.0


def test_parse_geometry_rejects_garbage():
    """Malformed/legacy values fall back to None so readers use the source
    window instead of computing against garbage."""
    from clip_engine.edits import parse_geometry

    for bad in (
        None,
        MagicMock(),  # spec'd-mock attribute leakage in older tests
        {"version": 99, "keep_segments_s": [[0, 1]]},
        {"version": 1, "keep_segments_s": []},
        {"version": 1, "keep_segments_s": [[5.0, 2.0]]},  # inverted
        {"version": 1, "keep_segments_s": [[0.0, 5.0], [3.0, 8.0]]},  # overlap
        {"version": 1, "keep_segments_s": [["a", "b"]]},
    ):
        assert parse_geometry(bad) is None


def test_compose_keep_segments_second_trim():
    """A second trim is expressed in DELIVERED time of the first trim; the
    composition must land back in original clip-relative time, splitting
    across the first trim's seam."""
    from clip_engine.edits import compose_keep_segments

    # Delivered timeline of _KEEP is [0,20): 0-10 maps to 0-10, 10-20 → 20-30.
    # Keeping delivered [5, 15] straddles the seam → original [5,10] + [20,25].
    assert compose_keep_segments(_KEEP, [(5.0, 15.0)]) == [(5.0, 10.0), (20.0, 25.0)]
    # No prior geometry → identity.
    assert compose_keep_segments(None, [(5.0, 15.0)]) == [(5.0, 15.0)]


def test_map_words_to_delivered_drops_cut_words():
    from clip_engine.edits import map_words_to_delivered

    words = [
        {"word": "kept-early", "start": 2.0, "end": 3.0},
        {"word": "cut-middle", "start": 12.0, "end": 13.0},
        {"word": "kept-late", "start": 24.0, "end": 25.0},
        {"word": "seam-straddler", "start": 9.5, "end": 10.5},
    ]
    mapped = map_words_to_delivered(words, _KEEP)
    assert [w["word"] for w in mapped] == ["kept-early", "kept-late", "seam-straddler"]
    kept_late = next(w for w in mapped if w["word"] == "kept-late")
    assert kept_late["start"] == 14.0 and kept_late["end"] == 15.0
    straddler = next(w for w in mapped if w["word"] == "seam-straddler")
    assert (straddler["start"], straddler["end"]) == (9.5, 10.0)


# ── Duration readers (acceptance a) ──────────────────────────────────────────


def test_clip_duration_uses_effective_geometry():
    """After a confirmed trim, every duration reader must report the delivered
    20 s, not the stale end_s - origin (40 s)."""
    from routers.clips import _clip_duration_s
    from routers.review import _clip_duration_s as review_duration_s

    creator = _mock_creator()
    clip = _mock_clip(creator.id, effective_geometry=_geometry())
    assert _clip_duration_s(clip) == 20.0
    assert review_duration_s(clip) == 20.0


def test_clip_duration_falls_back_without_geometry():
    from routers.clips import _clip_duration_s

    creator = _mock_creator()
    clip = _mock_clip(creator.id, effective_geometry=None)
    assert _clip_duration_s(clip) == 40.0


def test_clip_origin_prefers_setup_start():
    """Issue 475 — the ONE origin rule: `setup_start_s ?? start_s`, shared by
    every clip-relative measurement (durations, lift geometry, fingerprint)."""
    from clip_engine.edits import clip_origin_s

    assert clip_origin_s(5.0, 12.0) == 5.0
    assert clip_origin_s(None, 12.0) == 12.0


def test_clip_response_exposes_delivered_duration_and_baked_flag(client):
    """GET /clips/{id} must carry the delivered duration and an explicit
    baked-edits flag so the UI can warn before a from-source re-render."""
    from auth import get_current_creator
    from db import get_session
    from main import app

    creator = _mock_creator()
    clip = _mock_clip(creator.id, effective_geometry=_geometry())

    async def _session():
        s = AsyncMock()
        s.execute = AsyncMock(return_value=owned_result(clip))
        yield s

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session
    try:
        resp = client.get(f"/clips/{clip.id}")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["duration_s"] == 20.0
    assert body["has_baked_edits"] is True


# ── /transcript (acceptance b) ───────────────────────────────────────────────


def _transcript_row(video_id):
    from models import Transcript

    t = MagicMock(spec=Transcript)
    t.video_id = video_id
    # Video-absolute times; clip origin is 10.0 → clip-relative = absolute - 10.
    t.segments_jsonb = {
        "segments": [
            {
                "start": 10.0,
                "end": 50.0,
                "words": [
                    {"word": "kept-early", "start": 12.0, "end": 13.0},
                    {"word": "cut-middle", "start": 22.0, "end": 23.0},
                    {"word": "kept-late", "start": 34.0, "end": 35.0},
                ],
            }
        ]
    }
    return t


def test_transcript_returns_only_kept_words_in_delivered_time(client):
    """After trim+confirm, /transcript must window to the DELIVERED video:
    cut words gone, kept words at delivered offsets, duration effective."""
    from auth import get_current_creator
    from db import get_session
    from main import app

    creator = _mock_creator()
    clip = _mock_clip(creator.id, effective_geometry=_geometry())

    async def _session():
        s = AsyncMock()
        s.execute = AsyncMock(return_value=owned_result(clip))
        s.get = AsyncMock(return_value=_transcript_row(clip.video_id))
        yield s

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session
    try:
        resp = client.get(f"/clips/{clip.id}/transcript")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["clip_duration_s"] == 20.0
    words = body["words"]
    assert [w["word"] for w in words] == ["kept-early", "kept-late"]
    # kept-early: clip-relative 2-3 inside keep[0] → delivered 2-3.
    assert words[0]["start_s"] == 2.0 and words[0]["end_s"] == 3.0
    # kept-late: clip-relative 24-25 inside keep[1] (20-30, offset 10) → 14-15.
    assert words[1]["start_s"] == 14.0 and words[1]["end_s"] == 15.0
    assert [w["index"] for w in words] == [0, 1]


# ── /clean/confirm persists effective geometry in the CAS ────────────────────


def _confirm_session_factory(clip, cas_result, extra_results):
    captured: list = []
    remaining = list(extra_results)

    async def _execute(stmt, *a, **kw):
        captured.append(stmt)
        if len(captured) == 1:
            return owned_result(clip)
        if len(captured) == 2:
            return cas_result
        return remaining.pop(0) if remaining else MagicMock()

    async def _session():
        s = AsyncMock()
        s.execute = AsyncMock(side_effect=_execute)
        s.commit = AsyncMock()
        s.rollback = AsyncMock()
        yield s

    return _session, captured


def test_clean_confirm_persists_composed_geometry_inside_cas(client):
    """The swap that bakes the cuts in must, in the SAME CAS UPDATE, promote
    the pending record to effective geometry (composed with any prior trim)
    and clear the pending column — the row lock the CAS takes is what makes
    this race-free (Issue 468)."""
    from unittest.mock import patch

    from auth import get_current_creator
    from clip_engine.edits import geometry_doc
    from db import get_session
    from main import app

    creator = _mock_creator()
    # First trim already confirmed (_KEEP); the pending SECOND trim keeps
    # delivered [5, 15] → composed original [5,10] + [20,25].
    clip = _mock_clip(creator.id, effective_geometry=_geometry())
    clip.cleaned_render_uri = "clips/x_clean.mp4"
    clip.pending_geometry_jsonb = geometry_doc([(5.0, 15.0)])

    doc_clear = MagicMock()
    doc_clear.first.return_value = (4,)
    session_factory, captured = _confirm_session_factory(clip, MagicMock(rowcount=1), [doc_clear])

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = session_factory
    try:
        with patch("worker.storage.adelete_file", AsyncMock(return_value=None)):
            resp = client.post(f"/clips/{clip.id}/clean/confirm")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "swapped"

    cas_stmt = captured[1]
    cas_sql = str(cas_stmt).replace("\n", " ")
    assert "UPDATE clips" in cas_sql and "WHERE" in cas_sql
    values = {c.name: v for c, v in cas_stmt._values.items()}
    assert values["pending_geometry_jsonb"].value is None
    effective = values["effective_geometry_jsonb"].value
    assert effective["keep_segments_s"] == [[5.0, 10.0], [20.0, 25.0]]
    assert effective["duration_s"] == 10.0
    # The ORM identity map is kept in step with the CAS.
    assert clip.effective_geometry_jsonb == effective
    assert clip.pending_geometry_jsonb is None


def test_clean_confirm_without_pending_record_clears_effective(client):
    """A legacy pending artifact (worker predates 0061) has no durable cut
    list — confirming it must clear effective geometry rather than keep a
    claim that no longer describes the delivered video."""
    from unittest.mock import patch

    from auth import get_current_creator
    from db import get_session
    from main import app

    creator = _mock_creator()
    clip = _mock_clip(creator.id, effective_geometry=_geometry())
    clip.cleaned_render_uri = "clips/x_clean.mp4"
    clip.pending_geometry_jsonb = None

    doc_clear = MagicMock()
    doc_clear.first.return_value = (2,)
    session_factory, captured = _confirm_session_factory(clip, MagicMock(rowcount=1), [doc_clear])

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = session_factory
    try:
        with patch("worker.storage.adelete_file", AsyncMock(return_value=None)):
            resp = client.post(f"/clips/{clip.id}/clean/confirm")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    values = {c.name: v for c, v in captured[1]._values.items()}
    assert values["effective_geometry_jsonb"].value is None


# ── Re-render must not silently resurrect the pre-trim window (acceptance c) ─


def _render_session(clip, video):
    s = AsyncMock()
    call_count = {"n": 0}

    async def _execute(stmt, *a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return owned_result(clip)
        res = MagicMock()
        res.scalar_one_or_none.return_value = None  # no CreatorStyle kit
        return res

    s.execute = AsyncMock(side_effect=_execute)
    s.get = AsyncMock(return_value=video)
    s.commit = AsyncMock()
    return s


def test_rerender_of_confirmed_clip_discards_geometry_and_says_so(client):
    """POST /render on a confirmed-clean clip regenerates the FULL window from
    source. Honest behavior: clear the baked-edit record in the same reset
    that clears render_uri, and tell the caller (`discarded_edits: true`)."""
    from unittest.mock import patch

    from auth import get_current_creator
    from db import get_session
    from main import app
    from models import Video

    creator = _mock_creator()
    clip = _mock_clip(creator.id, effective_geometry=_geometry())
    video = MagicMock(spec=Video)
    video.source_uri = "videos/src.mp4"

    async def _session():
        yield _render_session(clip, video)

    task = MagicMock()
    task.id = "task-123"

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session
    try:
        with (
            patch("routers.clips.check_positive_balance", AsyncMock(return_value=None)),
            patch("worker.tasks.render_clip") as render_task,
            patch("routers.clips.stamp_stream_owner", AsyncMock(return_value="/stream")),
        ):
            render_task.delay.return_value = task
            resp = client.post(f"/clips/{clip.id}/render")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 202, resp.text
    assert resp.json()["discarded_edits"] is True
    assert clip.effective_geometry_jsonb is None
    assert clip.render_uri is None  # the Issue-353 reset still applies


def test_rerender_enqueue_failure_restores_geometry(client):
    """Issue 359c extended: a broker throw must restore the baked-edit record
    along with render_uri — the old render (with its trims) is still live."""
    from unittest.mock import patch

    from auth import get_current_creator
    from db import get_session
    from main import app
    from models import Video

    creator = _mock_creator()
    geometry = _geometry()
    clip = _mock_clip(creator.id, effective_geometry=geometry)
    video = MagicMock(spec=Video)
    video.source_uri = "videos/src.mp4"

    async def _session():
        yield _render_session(clip, video)

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session
    try:
        with (
            patch("routers.clips.check_positive_balance", AsyncMock(return_value=None)),
            patch("worker.tasks.render_clip") as render_task,
        ):
            render_task.delay.side_effect = RuntimeError("broker down")
            resp = client.post(f"/clips/{clip.id}/render")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 503
    assert clip.effective_geometry_jsonb == geometry
    assert clip.render_uri == "clips/x.mp4"


def test_rerender_without_baked_edits_reports_false(client):
    from unittest.mock import patch

    from auth import get_current_creator
    from db import get_session
    from main import app
    from models import Video

    creator = _mock_creator()
    clip = _mock_clip(creator.id, effective_geometry=None)
    video = MagicMock(spec=Video)
    video.source_uri = "videos/src.mp4"

    async def _session():
        yield _render_session(clip, video)

    task = MagicMock()
    task.id = "task-123"

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session
    try:
        with (
            patch("routers.clips.check_positive_balance", AsyncMock(return_value=None)),
            patch("worker.tasks.render_clip") as render_task,
            patch("routers.clips.stamp_stream_owner", AsyncMock(return_value="/stream")),
        ):
            render_task.delay.return_value = task
            resp = client.post(f"/clips/{clip.id}/render")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 202, resp.text
    assert resp.json()["discarded_edits"] is False


# ── Discard clears the pending record in lockstep ────────────────────────────


def test_clean_discard_clears_pending_geometry(client):
    """The discard CAS must clear pending_geometry_jsonb with
    cleaned_render_uri — the two are one record."""
    from unittest.mock import patch

    from auth import get_current_creator
    from clip_engine.edits import geometry_doc
    from db import get_session
    from main import app

    creator = _mock_creator()
    clip = _mock_clip(creator.id)
    clip.cleaned_render_uri = "clips/x_clean.mp4"
    clip.pending_geometry_jsonb = geometry_doc([(0.0, 5.0)])

    captured: list = []

    async def _execute(stmt, *a, **kw):
        captured.append(stmt)
        if len(captured) == 1:
            return owned_result(clip)
        return MagicMock(rowcount=1)

    async def _session():
        s = AsyncMock()
        s.execute = AsyncMock(side_effect=_execute)
        s.commit = AsyncMock()
        s.rollback = AsyncMock()
        yield s

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session
    try:
        with patch("worker.storage.adelete_file", AsyncMock(return_value=None)):
            resp = client.post(f"/clips/{clip.id}/clean/discard")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "discarded"
    values = {c.name: v for c, v in captured[1]._values.items()}
    assert values["pending_geometry_jsonb"].value is None
    assert clip.pending_geometry_jsonb is None


# ── Issue 531/532: delivered-timeline projections ────────────────────────────


class TestMapTimeToDelivered:
    def test_maps_inside_a_keep_segment(self) -> None:
        from clip_engine.edits import map_time_to_delivered

        # keep [2,5) and [8,10): t=9 → 3 (len of first segment) + 1
        assert map_time_to_delivered(9.0, [(2.0, 5.0), (8.0, 10.0)]) == 4.0

    def test_cut_instant_returns_none(self) -> None:
        from clip_engine.edits import map_time_to_delivered

        assert map_time_to_delivered(6.0, [(2.0, 5.0), (8.0, 10.0)]) is None


class TestRemapCropTrackToDelivered:
    _TRACK = {
        "version": 1,
        "mode": "speaker_cut",
        "source": {"width": 1920, "height": 1080},
        "crop": {"width": 607, "height": 1080},
        "origin_s": 10.0,
        "duration_s": 20.0,
        "keyframes": [{"t": 0.0, "x": 100}, {"t": 4.0, "x": 1196}, {"t": 12.0, "x": 300}],
        "cuts": [{"t": 4.0, "from_x": 100, "to_x": 1196, "speaker": 1}],
        "shots": [{"t": 4.0}],
        "speakers": {"count": 2, "mapping_confidence": 0.9},
        "meta": {"sample_fps": 5.0, "fallback": False},
    }

    def test_entries_shift_and_cut_entries_drop(self) -> None:
        """Trim removes [0,6): the t=0/t=4 keyframes, the cut and the shot all
        fall inside the cut and drop; the t=12 keyframe shifts to 6; a seam
        keyframe is synthesized at delivered t=0 with the sampled x."""
        from clip_engine.edits import remap_crop_track_to_delivered

        out = remap_crop_track_to_delivered(self._TRACK, [(6.0, 20.0)])
        assert out["duration_s"] == 14.0
        assert out["cuts"] == []
        assert out["shots"] == []
        # Seam keyframe at t=0: sampled at original t=6 (between kf 4.0→12.0,
        # after the cut at 4.0 — snap rule gives to_x=1196... but the cut is at
        # exactly k0.t so the span 4.0→12.0 has no interior cut; lerp applies:
        # x = 1196 + (6-4)/(12-4) * (300-1196) = 1196 - 224 = 972.
        assert out["keyframes"][0] == {"t": 0.0, "x": 972.0}
        assert out["keyframes"][1] == {"t": 6.0, "x": 300}
        # Untouched metadata rides along.
        assert out["origin_s"] == 10.0
        assert out["source"] == self._TRACK["source"]

    def test_multi_segment_offsets_accumulate(self) -> None:
        from clip_engine.edits import remap_crop_track_to_delivered

        # keep [0,2) and [10,20): kf t=12 → 2 + (12-10) = 4
        out = remap_crop_track_to_delivered(self._TRACK, [(0.0, 2.0), (10.0, 20.0)])
        assert out["duration_s"] == 12.0
        kept_times = [k["t"] for k in out["keyframes"]]
        assert kept_times[0] == 0.0  # seam for segment 1 (t=0 original)
        assert 4.0 in kept_times  # the t=12 keyframe, shifted
        # The original t=4 keyframe fell in the cut → only seam + shifted survive.
        assert out["cuts"] == []


class TestExtractDeliveredWords:
    _SEGMENTS = {
        "segments": [
            {
                "start": 300.0,
                "end": 320.0,
                "text": "TRIMMED OPEN then KEPT STORY",
                "words": [
                    {"word": "TRIMMED", "start": 301.0, "end": 302.0},
                    {"word": "OPEN", "start": 302.5, "end": 303.0},
                    {"word": "KEPT", "start": 315.0, "end": 315.5},
                    {"word": "STORY", "start": 316.0, "end": 316.5},
                ],
            }
        ]
    }

    def test_drops_trimmed_words_and_rebases_times(self) -> None:
        from clip_engine.edits import extract_delivered_words

        # Clip origin 300, end 360; trim removed the first 10 clip-relative
        # seconds (keep [10, 60)).
        out = extract_delivered_words(self._SEGMENTS, 300.0, 360.0, [(10.0, 60.0)])
        assert out is not None
        assert [w["word"] for w in out] == ["KEPT", "STORY"]
        # Delivered-relative: original clip-relative 15.0 - 10.0 = 5.0
        assert out[0]["start"] == 5.0

    def test_returns_none_without_word_timings(self) -> None:
        """Pre-Deepgram transcripts have no word arrays — callers fall back to
        the segment-midpoint window (the pre-fix behavior) instead of silently
        grounding on nothing."""
        from clip_engine.edits import extract_delivered_words

        segs = {"segments": [{"start": 300.0, "end": 320.0, "text": "no words here"}]}
        assert extract_delivered_words(segs, 300.0, 360.0, [(10.0, 60.0)]) is None
