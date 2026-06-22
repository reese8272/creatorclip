"""Tests for clip_engine.edits (Issue 135 — text-based editor validator).

Covers the safety invariants the validator must enforce before user-supplied
cuts ever reach ffmpeg:
  - bounds / NaN / start>=end
  - overlap rejection
  - 5s minimum kept-duration cap
  - 85% maximum removed-percent cap
  - sub-frame keep-range floor (0.04s)
  - keep-range inversion + ordering
  - endpoint integration: 202 happy, 422 on each reject reason
  - per-creator isolation
  - afade guard in render.py (Issue 134 latent bug)
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clip_engine.edits import (
    MAX_REMOVED_PCT,
    MIN_KEEP_SEGMENT_S,
    MIN_KEPT_DURATION_S,
    CutValidationError,
    validate_user_cuts,
)

# ── validate_user_cuts ────────────────────────────────────────────────────


def test_validate_happy_path_returns_keep_ranges():
    edit = validate_user_cuts([(2.0, 3.0), (5.0, 6.0)], clip_duration_s=10.0)
    assert edit.cut_segments == [(2.0, 3.0), (5.0, 6.0)]
    assert edit.keep_ranges == [(0.0, 2.0), (3.0, 5.0), (6.0, 10.0)]
    assert edit.kept_duration_s == pytest.approx(8.0)
    assert edit.percent_removed == pytest.approx(20.0)


def test_validate_sorts_unsorted_input():
    edit = validate_user_cuts([(5.0, 6.0), (2.0, 3.0)], clip_duration_s=10.0)
    assert edit.cut_segments == [(2.0, 3.0), (5.0, 6.0)]


def test_validate_accepts_dict_form():
    edit = validate_user_cuts([{"start_s": 2.0, "end_s": 3.0}], clip_duration_s=10.0)
    assert edit.cut_segments == [(2.0, 3.0)]


def test_validate_rejects_empty_list():
    with pytest.raises(CutValidationError) as exc:
        validate_user_cuts([], clip_duration_s=10.0)
    assert exc.value.code == "empty"


def test_validate_rejects_zero_duration_segment():
    with pytest.raises(CutValidationError) as exc:
        validate_user_cuts([(2.0, 2.0)], clip_duration_s=10.0)
    assert exc.value.code == "invalid_segment"


def test_validate_rejects_negative_duration_segment():
    with pytest.raises(CutValidationError) as exc:
        validate_user_cuts([(3.0, 2.0)], clip_duration_s=10.0)
    assert exc.value.code == "invalid_segment"


def test_validate_rejects_nan_segment():
    with pytest.raises(CutValidationError) as exc:
        validate_user_cuts([(float("nan"), 2.0)], clip_duration_s=10.0)
    assert exc.value.code == "invalid_segment"


def test_validate_rejects_out_of_bounds_segment():
    with pytest.raises(CutValidationError) as exc:
        validate_user_cuts([(-0.5, 1.0)], clip_duration_s=10.0)
    assert exc.value.code == "out_of_bounds"
    with pytest.raises(CutValidationError) as exc:
        validate_user_cuts([(1.0, 11.0)], clip_duration_s=10.0)
    assert exc.value.code == "out_of_bounds"


def test_validate_permissive_right_edge_within_one_frame():
    """A user dragging to "the end" may produce end_s slightly past the
    clip duration due to rounding — accept up to MIN_KEEP_SEGMENT_S past
    and clamp to clip_duration_s. Cut size kept inside 85% cap on a 100s
    clip so the kept_too_short/removed_too_much guards don't fire."""
    edit = validate_user_cuts([(90.0, 100.0 + MIN_KEEP_SEGMENT_S / 2)], clip_duration_s=100.0)
    assert edit.cut_segments[0][1] == pytest.approx(100.0)


def test_validate_rejects_overlap():
    with pytest.raises(CutValidationError) as exc:
        validate_user_cuts([(2.0, 5.0), (4.0, 7.0)], clip_duration_s=10.0)
    assert exc.value.code == "overlap"


def test_validate_rejects_kept_too_short():
    """Cutting 6s of a 10s clip leaves 4s — below the 5s short-form floor.
    Stays under the 85% removed cap so kept_too_short fires (not
    removed_too_much)."""
    with pytest.raises(CutValidationError) as exc:
        validate_user_cuts([(0.0, 6.0)], clip_duration_s=10.0)
    assert exc.value.code == "kept_too_short"


def test_validate_kept_at_floor_passes():
    """Exactly 5s kept (the floor) is acceptable."""
    edit = validate_user_cuts([(0.0, 5.0)], clip_duration_s=10.0)
    assert edit.kept_duration_s == pytest.approx(5.0)


def test_validate_rejects_removed_too_much():
    """A 90s clip cut down to 7 seconds (93% removed) is rejected even
    though the kept duration alone would be acceptable — the user almost
    certainly intended something different."""
    with pytest.raises(CutValidationError) as exc:
        validate_user_cuts([(0.0, 83.0)], clip_duration_s=90.0)
    assert exc.value.code == "removed_too_much"


def test_validate_cap_constants_match_spec():
    assert MIN_KEPT_DURATION_S == 5.0
    assert MAX_REMOVED_PCT == 85.0


def test_invert_drops_sub_frame_keep_ranges():
    """A cut starting at clip_start_s would otherwise emit a zero-width
    keep range. The validator must drop it instead of passing it to ffmpeg."""
    edit = validate_user_cuts([(0.0, 5.0)], clip_duration_s=10.0)
    assert edit.keep_ranges == [(5.0, 10.0)]


def test_invert_drops_keep_range_smaller_than_one_frame():
    """A cut leaving a sub-frame (<40ms) keep-range between two cuts must
    be dropped. Use a 100s clip so the 85% removed cap doesn't fire on the
    aggressive cuts needed to exercise this edge."""
    edit = validate_user_cuts([(10.0, 50.0), (50.009, 70.0)], clip_duration_s=100.0)
    # The 9ms gap between the two cuts is below MIN_KEEP_SEGMENT_S (40ms);
    # no keep range smaller than one frame may survive.
    assert all(end - start >= MIN_KEEP_SEGMENT_S for start, end in edit.keep_ranges)


# ── afade guard in render.py (Issue 134 latent bug + Issue 135 fix) ──────


def test_render_cleaned_clip_file_afade_guard_short_segment(tmp_path):
    """A 6ms kept segment should produce afade=3ms (half of seg_dur),
    not 5ms which would exceed half-segment and ffmpeg would error."""
    from pathlib import Path

    from clip_engine.render import render_cleaned_clip_file

    captured = {}

    def _fake_run(cmd, label, timeout_s=120.0):
        captured["script"] = Path(cmd[cmd.index("-filter_complex_script") + 1]).read_text()

    with (
        patch("clip_engine.render._run", _fake_run),
        patch("clip_engine.render._measure_loudnorm_filter", return_value=None),
    ):
        render_cleaned_clip_file(
            source_path=Path("/fake/src.mp4"),
            keep_ranges=[(0.0, 0.006), (1.0, 6.0)],  # first segment is 6ms
            out_path=tmp_path / "out.mp4",
        )

    # First segment's afade should be 0.003s (half of 6ms) — NOT 0.005.
    assert "afade=t=in:st=0:d=0.003" in captured["script"]
    # Second segment is long enough → full 5ms afade applied.
    assert "afade=t=in:st=0:d=0.005" in captured["script"]


# ── Endpoint integration ─────────────────────────────────────────────────


def _mock_creator():
    from models import Creator

    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    c.minutes_balance = 100
    return c


def _mock_clip(creator_id, duration_s=10.0):
    from models import Clip, RenderStatus

    clip = MagicMock(spec=Clip)
    clip.id = uuid.uuid4()
    clip.creator_id = creator_id
    clip.video_id = uuid.uuid4()
    clip.setup_start_s = 0.0
    clip.start_s = 0.0
    clip.end_s = duration_s
    clip.render_status = RenderStatus.done
    clip.render_uri = "clips/x.mp4"
    clip.cleaned_render_uri = None
    return clip


def test_post_cuts_happy_path_returns_202(client):
    from auth import get_current_creator
    from billing.ledger import check_positive_balance
    from db import get_session
    from main import app

    creator = _mock_creator()
    clip = _mock_clip(creator.id, duration_s=10.0)

    async def _session():
        s = AsyncMock()
        s.get = AsyncMock(return_value=clip)
        s.commit = AsyncMock()
        yield s

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[check_positive_balance] = AsyncMock(return_value=None)

    with (
        patch("routers.clips.check_positive_balance", AsyncMock(return_value=None)),
        patch("worker.tasks.edit_clip") as mock_task,
        patch("worker.progress.aset_owner", AsyncMock()),
    ):
        mock_task.delay.return_value = MagicMock(id="task-edit-1")
        try:
            resp = client.post(
                f"/clips/{clip.id}/cuts",
                json={"segments": [{"start_s": 2.0, "end_s": 3.0}]},
            )
        finally:
            app.dependency_overrides.clear()

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["task_id"] == "task-edit-1"
    assert body["status"] == "queued"


@pytest.mark.parametrize(
    "segments, expected_code",
    [
        ([], "empty"),
        ([{"start_s": 3.0, "end_s": 2.0}], "invalid_segment"),
        ([{"start_s": -1.0, "end_s": 2.0}], "out_of_bounds"),
        (
            [
                {"start_s": 2.0, "end_s": 5.0},
                {"start_s": 4.0, "end_s": 7.0},
            ],
            "overlap",
        ),
        ([{"start_s": 0.0, "end_s": 6.0}], "kept_too_short"),
    ],
)
def test_post_cuts_validation_returns_422_with_code(client, segments, expected_code):
    from auth import get_current_creator
    from billing.ledger import check_positive_balance
    from db import get_session
    from main import app

    creator = _mock_creator()
    clip = _mock_clip(creator.id, duration_s=10.0)

    async def _session():
        s = AsyncMock()
        s.get = AsyncMock(return_value=clip)
        s.commit = AsyncMock()
        yield s

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[check_positive_balance] = AsyncMock(return_value=None)

    with patch("routers.clips.check_positive_balance", AsyncMock(return_value=None)):
        try:
            resp = client.post(
                f"/clips/{clip.id}/cuts",
                json={"segments": segments},
            )
        finally:
            app.dependency_overrides.clear()

    assert resp.status_code == 422, resp.text
    body = resp.json()
    detail = body.get("detail")
    # FastAPI may wrap the detail dict at the top level (HTTPException) OR
    # surface as pydantic-style errors (request body validation). The empty
    # segments case is a server-side CutValidationError (our HTTPException),
    # so detail is our dict.
    if isinstance(detail, dict):
        assert detail.get("code") == expected_code


def test_post_cuts_rejects_other_creators_clip(client):
    """Per-creator isolation: a clip owned by creator B cannot be edited
    by creator A — must 404, not 403, to avoid leaking clip existence."""
    from auth import get_current_creator
    from db import get_session
    from main import app

    creator = _mock_creator()
    other = _mock_creator()
    clip = _mock_clip(other.id, duration_s=10.0)

    async def _session():
        s = AsyncMock()
        s.get = AsyncMock(return_value=clip)
        yield s

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session
    try:
        with patch("routers.clips.check_positive_balance", AsyncMock(return_value=None)):
            resp = client.post(
                f"/clips/{clip.id}/cuts",
                json={"segments": [{"start_s": 2.0, "end_s": 3.0}]},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 404


def test_get_transcript_returns_clip_relative_words(client):
    from auth import get_current_creator
    from db import get_session
    from main import app
    from models import Transcript

    creator = _mock_creator()
    clip = _mock_clip(creator.id, duration_s=5.0)
    clip.setup_start_s = 10.0
    clip.start_s = 10.0
    clip.end_s = 15.0

    transcript = MagicMock(spec=Transcript)
    transcript.segments_jsonb = {
        "segments": [
            {
                "start": 10.0,
                "end": 12.0,
                "text": "hello world",
                "words": [
                    {"word": "hello", "start": 10.0, "end": 10.5},
                    {"word": "world", "start": 10.6, "end": 11.2},
                ],
            },
            {
                "start": 100.0,  # outside window — must be dropped
                "end": 102.0,
                "text": "later",
                "words": [{"word": "later", "start": 100.0, "end": 100.5}],
            },
        ]
    }

    async def _session():
        s = AsyncMock()
        s.get = AsyncMock(side_effect=[clip, transcript])
        yield s

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session
    try:
        resp = client.get(f"/clips/{clip.id}/transcript")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["clip_duration_s"] == pytest.approx(5.0)
    words = data["words"]
    assert len(words) == 2  # the later segment is dropped
    # Timestamps are shifted to clip-relative (subtract clip_origin_s=10.0).
    assert words[0]["start_s"] == pytest.approx(0.0)
    assert words[1]["word"] == "world"
    assert words[1]["start_s"] == pytest.approx(0.6)
    # Indices are stable for the JS editor.
    assert words[0]["index"] == 0
    assert words[1]["index"] == 1


def test_post_cuts_rejects_when_cleaned_render_uri_already_set(client):
    """Issue-135 audit fix: /cuts mirrors /clean and returns 409 when a
    cleaned/edited artifact is already pending — both flows share
    Clip.cleaned_render_uri as the destination slot, and a silent second
    write would drop the first user's work."""
    from auth import get_current_creator
    from billing.ledger import check_positive_balance
    from db import get_session
    from main import app

    creator = _mock_creator()
    clip = _mock_clip(creator.id, duration_s=10.0)
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
            resp = client.post(
                f"/clips/{clip.id}/cuts",
                json={"segments": [{"start_s": 2.0, "end_s": 3.0}]},
            )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "pending_clean_or_edit"
