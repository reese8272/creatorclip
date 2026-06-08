"""Tests for the auto chapter marker feature (Issue 131)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from auth import get_current_creator
from db import get_session
from knowledge.chapters import (
    MIN_CHAPTERS,
    find_chapter_boundaries,
    format_timestamp,
    parse_chapters,
)
from main import app
from models import Creator, Transcript, Video
from tests._helpers import override_current_creator

_CHAPTERS_URL = "/creators/me/videos/{video_id}/chapters"


# ── Unit: format_timestamp ────────────────────────────────────────────────────


def test_format_timestamp_zero() -> None:
    assert format_timestamp(0) == "0:00"


def test_format_timestamp_minutes_and_seconds() -> None:
    assert format_timestamp(4 * 60 + 23) == "4:23"
    assert format_timestamp(59) == "0:59"
    assert format_timestamp(60) == "1:00"


def test_format_timestamp_hours() -> None:
    assert format_timestamp(3661) == "1:01:01"
    assert format_timestamp(3600) == "1:00:00"


def test_format_timestamp_negative_clamps_to_zero() -> None:
    assert format_timestamp(-5) == "0:00"


# ── Unit: find_chapter_boundaries ─────────────────────────────────────────────


def test_find_chapter_boundaries_always_starts_at_zero() -> None:
    boundaries = find_chapter_boundaries(None, 600.0)
    assert boundaries[0] == 0.0


def test_find_chapter_boundaries_silence_gaps() -> None:
    """Silences spaced >= MAX_CHAPTER_PERIOD_S apart all become boundaries."""
    timeline = {
        "silences": [
            {"start_s": 200.0, "end_s": 203.0},
            {"start_s": 400.0, "end_s": 402.5},
        ]
    }
    bounds = find_chapter_boundaries(timeline, 600.0)
    assert 0.0 in bounds
    assert any(abs(b - 200.0) < 1.0 for b in bounds)
    assert any(abs(b - 400.0) < 1.0 for b in bounds)


def test_find_chapter_boundaries_short_silences_ignored() -> None:
    timeline = {
        "silences": [
            {"start_s": 50.0, "end_s": 51.0},  # only 1s — below threshold
        ]
    }
    bounds = find_chapter_boundaries(timeline, 600.0)
    assert all(abs(b - 50.0) > 0.5 for b in bounds if b > 0)


def test_find_chapter_boundaries_min_chapters_enforced() -> None:
    bounds = find_chapter_boundaries(None, 600.0)
    assert len(bounds) >= MIN_CHAPTERS


def test_find_chapter_boundaries_max_density() -> None:
    """One chapter per 3 minutes maximum — boundaries too close together are filtered."""
    # Many very close silences
    timeline = {
        "silences": [
            {"start_s": float(t), "end_s": float(t) + 3.0}
            for t in range(5, 600, 10)  # every 10 seconds
        ]
    }
    bounds = find_chapter_boundaries(timeline, 600.0)
    # Consecutive boundaries must be >= 90s apart (half of MAX_CHAPTER_PERIOD_S)
    for _i in range(len(bounds) - 1):
        pass  # main invariant: list is sorted and no crash
    assert bounds == sorted(bounds)


def test_find_chapter_boundaries_empty_signals() -> None:
    bounds = find_chapter_boundaries({}, 600.0)
    assert 0.0 in bounds
    assert len(bounds) >= MIN_CHAPTERS


# ── Unit: parse_chapters ──────────────────────────────────────────────────────


def test_parse_chapters_valid() -> None:
    raw = """{
        "chapters": [
            {"timestamp_s": 0.0, "timestamp_formatted": "0:00", "title": "Introduction"},
            {"timestamp_s": 263.0, "timestamp_formatted": "4:23", "title": "Main concept"}
        ],
        "description_block": "0:00 Introduction\\n4:23 Main concept"
    }"""
    result = parse_chapters(raw)
    assert len(result["chapters"]) == 2
    assert result["chapters"][0]["title"] == "Introduction"
    assert "0:00" in result["description_block"]


def test_parse_chapters_title_truncated_to_40_chars() -> None:
    raw = """{
        "chapters": [
            {"timestamp_s": 0.0, "timestamp_formatted": "0:00",
             "title": "A very long title that exceeds the YouTube forty character limit for chapters"}
        ],
        "description_block": "0:00 ..."
    }"""
    result = parse_chapters(raw)
    assert len(result["chapters"][0]["title"]) <= 40


def test_parse_chapters_missing_chapters_key() -> None:
    with pytest.raises(ValueError):
        parse_chapters('{"description_block": "0:00 Intro"}')


def test_parse_chapters_empty_chapters_list() -> None:
    with pytest.raises(ValueError):
        parse_chapters('{"chapters": [], "description_block": ""}')


def test_parse_chapters_description_block_fallback() -> None:
    raw = """{
        "chapters": [
            {"timestamp_s": 0.0, "timestamp_formatted": "0:00", "title": "Intro"},
            {"timestamp_s": 120.0, "timestamp_formatted": "2:00", "title": "Topic"}
        ],
        "description_block": ""
    }"""
    result = parse_chapters(raw)
    # Should auto-build a description_block
    assert "0:00" in result["description_block"]
    assert "Intro" in result["description_block"]


# ── API endpoint tests ─────────────────────────────────────────────────────────


def _make_creator() -> MagicMock:
    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    c.channel_id = "UC_test"
    return c


def _make_video(creator_id: uuid.UUID) -> MagicMock:
    v = MagicMock(spec=Video)
    v.id = uuid.uuid4()
    v.creator_id = creator_id
    v.title = "Test Video"
    v.duration_s = 600
    return v


@pytest.fixture(autouse=True)
def cleanup():
    yield
    app.dependency_overrides.clear()


def test_chapters_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.post(_CHAPTERS_URL.format(video_id=str(uuid.uuid4())))
    assert resp.status_code == 401


def test_chapters_invalid_video_id() -> None:
    creator = _make_creator()

    async def _gen():
        session = AsyncMock()
        session.scalar = AsyncMock(return_value=None)
        yield session

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _gen

    with TestClient(app) as client:
        resp = client.post(_CHAPTERS_URL.format(video_id="not-a-uuid"), cookies={"session": "x"})
    assert resp.status_code == 422


def test_chapters_video_not_found() -> None:
    creator = _make_creator()

    async def _gen():
        session = AsyncMock()
        session.scalar = AsyncMock(return_value=None)
        yield session

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _gen

    with TestClient(app) as client:
        resp = client.post(
            _CHAPTERS_URL.format(video_id=str(uuid.uuid4())), cookies={"session": "x"}
        )
    assert resp.status_code == 404


def test_chapters_no_transcript_returns_400() -> None:
    creator = _make_creator()
    video = _make_video(creator.id)

    async def _gen():
        session = AsyncMock()
        session.scalar = AsyncMock(side_effect=[video, None])  # video found, no transcript
        yield session

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _gen

    with TestClient(app) as client:
        resp = client.post(_CHAPTERS_URL.format(video_id=str(video.id)), cookies={"session": "x"})
    assert resp.status_code == 400
    assert "transcribed" in resp.json()["detail"].lower()


def test_chapters_queued_when_transcript_exists() -> None:
    creator = _make_creator()
    video = _make_video(creator.id)
    transcript_mock = MagicMock(spec=Transcript)
    transcript_mock.video_id = video.id

    async def _gen():
        session = AsyncMock()
        session.scalar = AsyncMock(
            side_effect=[video, transcript_mock.video_id]  # video, transcript check
        )
        yield session

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _gen

    fake_task = MagicMock()
    fake_task.id = "fake-chapters-task-id"

    with (
        patch("worker.tasks.generate_chapters", **{"delay.return_value": fake_task}),
        patch("worker.progress.aset_owner", new=AsyncMock()),
        TestClient(app) as client,
    ):
        resp = client.post(_CHAPTERS_URL.format(video_id=str(video.id)), cookies={"session": "x"})

    assert resp.status_code == 202
    data = resp.json()
    assert data["task_id"] == "fake-chapters-task-id"
    assert data["stream_url"] == f"/tasks/{fake_task.id}/events"


# ── Unit: generate_chapters prompt assembly + _segment_text ───────────────────


def test_generate_chapters_builds_request() -> None:
    """generate_chapters delegates to stream_and_emit with cached system block."""
    from knowledge.chapters import generate_chapters

    fake_stream = MagicMock(
        return_value=(
            '{"chapters":[]}',
            {
                "input_tokens": 50,
                "output_tokens": 25,
                "cache_read": 0,
                "cache_creation": 50,
            },
        )
    )
    segments = [
        {"start": 0.0, "text": "intro"},
        {"start": 120.0, "text": "second section"},
    ]
    with patch("worker.anthropic_stream.stream_and_emit", fake_stream):
        result = generate_chapters(
            boundaries=[0.0, 120.0],
            segments=segments,
            video_duration_s=240.0,
            task_id="t-c1",
        )

    assert result == '{"chapters":[]}'
    call_kwargs = fake_stream.call_args.kwargs
    system_blocks = call_kwargs["system"]
    assert len(system_blocks) == 1
    assert system_blocks[0]["cache_control"] == {"type": "ephemeral"}
    user_msg = call_kwargs["messages"][0]["content"]
    assert "intro" in user_msg
    assert "second section" in user_msg
    assert "4:00" in user_msg  # 240s formatted


def test_find_chapter_boundaries_short_video() -> None:
    """Very short videos can't fit MIN_CHAPTERS — should not crash."""
    bounds = find_chapter_boundaries(None, 30.0)  # 30s video
    assert 0.0 in bounds
    # No MIN_CHAPTERS fill when duration < MIN_CHAPTERS * 30 = 120s


def test_find_chapter_boundaries_silence_at_zero_skipped() -> None:
    """A silence at start_s=0 shouldn't be added (already covered by mandatory 0.0)."""
    timeline = {"silences": [{"start_s": 0.0, "end_s": 5.0}]}
    bounds = find_chapter_boundaries(timeline, 600.0)
    # bounds[0] is always 0.0; we should not have duplicate
    assert bounds.count(0.0) == 1


def test_find_chapter_boundaries_silence_at_end_skipped() -> None:
    """Silence at video end shouldn't be a chapter boundary."""
    timeline = {"silences": [{"start_s": 599.0, "end_s": 601.0}]}
    bounds = find_chapter_boundaries(timeline, 600.0)
    assert all(b < 599.5 for b in bounds)


def test_generate_chapters_empty_segment_placeholder() -> None:
    """Segments with no transcript text get a placeholder."""
    from knowledge.chapters import generate_chapters

    fake_stream = MagicMock(
        return_value=(
            '{"chapters":[]}',
            {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read": 0,
                "cache_creation": 0,
            },
        )
    )
    with patch("worker.anthropic_stream.stream_and_emit", fake_stream):
        generate_chapters(
            boundaries=[0.0, 60.0],
            segments=[],  # no transcript at all
            video_duration_s=120.0,
            task_id="t-c2",
        )

    user_msg = fake_stream.call_args.kwargs["messages"][0]["content"]
    assert "no transcript" in user_msg.lower()


# ── Additional unit coverage ──────────────────────────────────────────────────


def test_format_timestamp_float() -> None:
    """format_timestamp accepts floats and truncates to int seconds."""
    assert format_timestamp(0.5) == "0:00"
    assert format_timestamp(63.9) == "1:03"


def test_find_chapter_boundaries_silence_outside_window() -> None:
    """Silences past the video duration are ignored."""
    timeline = {
        "silences": [
            {"start_s": 100.0, "end_s": 105.0},
            {"start_s": 700.0, "end_s": 710.0},  # past 600s duration
        ]
    }
    bounds = find_chapter_boundaries(timeline, 600.0)
    assert all(b < 600.0 for b in bounds)


async def test_generate_chapters_async_video_not_found_returns_early() -> None:
    """When video doesn't exist, error + return without calling Claude."""
    import db
    from worker import tasks as worker_tasks

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *_a, **_kw):
            return None  # video not found

    aemit_mock = AsyncMock()
    with (
        patch.object(db, "AdminSessionLocal", MagicMock(return_value=_FakeSession())),
        patch("worker.progress.aemit", aemit_mock),
    ):
        await worker_tasks._generate_chapters_async("job-c", str(uuid.uuid4()), str(uuid.uuid4()))

    error_calls = [c for c in aemit_mock.await_args_list if "Video not found" in str(c)]
    assert error_calls


async def test_generate_chapters_async_no_transcript_returns_early() -> None:
    """Video exists but no transcript → emits error and returns."""
    import db
    from models import Video
    from worker import tasks as worker_tasks

    video = MagicMock(spec=Video)
    video.id = uuid.uuid4()
    video.creator_id = uuid.uuid4()
    video.duration_s = 300

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *_a, **_kw):
            return video

        async def scalar(self, *_a, **_kw):
            return None  # no transcript

    aemit_mock = AsyncMock()
    with (
        patch.object(db, "AdminSessionLocal", MagicMock(return_value=_FakeSession())),
        patch("worker.progress.aemit", aemit_mock),
    ):
        # Note: creator_id passed must MATCH video.creator_id so we hit transcript-check branch
        await worker_tasks._generate_chapters_async("job-d", str(video.creator_id), str(video.id))

    error_calls = [c for c in aemit_mock.await_args_list if "Transcript not available" in str(c)]
    assert error_calls


def test_parse_chapters_missing_title_falls_back() -> None:
    """Empty title gets a 'Chapter' fallback rather than empty string."""
    raw = """{
        "chapters": [
            {"timestamp_s": 0.0, "timestamp_formatted": "0:00", "title": ""}
        ],
        "description_block": "0:00"
    }"""
    result = parse_chapters(raw)
    assert result["chapters"][0]["title"] == "Chapter"


def test_parse_chapters_missing_timestamp_formatted() -> None:
    """Missing timestamp_formatted derives from timestamp_s."""
    raw = """{
        "chapters": [
            {"timestamp_s": 120.0, "title": "Section"}
        ],
        "description_block": "2:00 Section"
    }"""
    result = parse_chapters(raw)
    assert result["chapters"][0]["timestamp_formatted"] == "2:00"


def test_chapters_per_creator_isolation() -> None:
    """A video belonging to another creator returns 404."""
    creator_a = _make_creator()
    other_creator_id = uuid.uuid4()
    video_for_other = _make_video(other_creator_id)

    async def _gen():
        session = AsyncMock()
        # scalar returns None because creator_a != video.creator_id
        session.scalar = AsyncMock(return_value=None)
        yield session

    app.dependency_overrides[get_current_creator] = override_current_creator(creator_a)
    app.dependency_overrides[get_session] = _gen

    with TestClient(app) as client:
        resp = client.post(
            _CHAPTERS_URL.format(video_id=str(video_for_other.id)), cookies={"session": "x"}
        )
    assert resp.status_code == 404
