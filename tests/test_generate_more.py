"""Issue 431 — "Generate more clips" (append-mode regeneration).

DB-free unit lane. Covers (80/20):
  - score_and_rank(exclude_windows=…) drops candidates contained in an existing
    clip's window, keeps novel ones, and renumbers densely before the trim,
  - append_ranked_clips offsets ranks past the video's max engine rank, SKIPS
    the preference rerank, and re-derives the offset once on a
    uq_clips_video_rank race,
  - POST /videos/{id}/clips/generate-more: 409 when no engine clips exist,
    409 at the CLIP_REGEN_TOTAL_CAP, and the happy path (exclusion windows
    built from existing clips with setup-NULL coercion, append + fill-only
    metadata enqueue, full refreshed list in the response).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from auth import get_current_creator
from config import settings
from db import get_session
from main import app
from models import Creator, IngestStatus, RenderStatus, Signals, Video
from tests._helpers import override_current_creator, owned_lookup_result

# ── score_and_rank exclude_windows ───────────────────────────────────────────


def _cand(start: float, end: float) -> dict:
    return {"setup_start_s": start, "start_s": start, "peak_s": (start + end) / 2, "end_s": end}


async def test_exclude_windows_drops_contained_keeps_novel(mocker) -> None:
    from clip_engine import ranking

    cands = [_cand(0.0, 60.0), _cand(200.0, 260.0), _cand(400.0, 460.0)]
    mocker.patch.object(ranking, "extract_candidates", return_value=cands)

    async def _fake_score(candidates, *args, **kwargs):
        for i, c in enumerate(candidates):
            c["score"] = 0.9 - i * 0.1
        return candidates

    mocker.patch.object(ranking, "score_candidates", new=AsyncMock(side_effect=_fake_score))

    ranked = await ranking.score_and_rank(
        video_id=uuid.uuid4(),
        creator_id=uuid.uuid4(),
        timeline={"version": 1, "duration_s": 600.0, "events": []},
        max_candidates=8,
        # The first candidate's window is an existing clip — excluded.
        exclude_windows=[{"setup_start_s": 0.0, "end_s": 60.0}],
    )
    assert [c["setup_start_s"] for c in ranked] == [200.0, 400.0]
    assert [c["rank"] for c in ranked] == [1, 2]  # dense renumber after the drop


async def test_exclude_windows_none_is_todays_behavior(mocker) -> None:
    from clip_engine import ranking

    cands = [_cand(0.0, 60.0), _cand(200.0, 260.0)]
    mocker.patch.object(ranking, "extract_candidates", return_value=cands)

    async def _fake_score(candidates, *args, **kwargs):
        for c in candidates:
            c["score"] = 0.5
        return candidates

    mocker.patch.object(ranking, "score_candidates", new=AsyncMock(side_effect=_fake_score))

    ranked = await ranking.score_and_rank(
        video_id=uuid.uuid4(),
        creator_id=uuid.uuid4(),
        timeline={"version": 1, "duration_s": 600.0, "events": []},
        max_candidates=8,
    )
    assert len(ranked) == 2


# ── append_ranked_clips ──────────────────────────────────────────────────────


def _append_session(max_ranks: list[int], commit_effects: list) -> AsyncMock:
    """Fake session: select(max(rank)) pops from max_ranks; commit pops from
    commit_effects (None = success, exception instance = raise)."""
    session = AsyncMock()
    added: list = []
    session.add_all = MagicMock(side_effect=lambda clips: added.extend(clips))
    session.added = added

    async def _execute(stmt, *a, **kw):
        result = MagicMock()
        result.scalar.return_value = max_ranks.pop(0)
        return result

    session.execute = AsyncMock(side_effect=_execute)

    async def _commit():
        effect = commit_effects.pop(0)
        if effect is not None:
            raise effect

    session.commit = AsyncMock(side_effect=_commit)
    return session


_RANKED = [
    {"setup_start_s": 100.0, "start_s": 100.0, "end_s": 160.0, "peak_s": 130.0, "score": 0.8},
    {"setup_start_s": 300.0, "start_s": 300.0, "end_s": 360.0, "peak_s": 330.0, "score": 0.7},
]


async def test_append_offsets_ranks_and_skips_preference_rerank(mocker) -> None:
    from clip_engine import ranking

    rerank_spy = mocker.patch.object(
        ranking,
        "rerank_with_preference",
        side_effect=AssertionError("append must not preference-rerank"),
    )
    session = _append_session(max_ranks=[12], commit_effects=[None])
    clips = await ranking.append_ranked_clips(session, uuid.uuid4(), uuid.uuid4(), _RANKED)

    assert [c.rank for c in clips] == [13, 14]
    assert all(c.render_status == RenderStatus.pending for c in clips)
    rerank_spy.assert_not_called()


async def test_append_reoffsets_once_on_rank_collision() -> None:
    from clip_engine.ranking import append_ranked_clips

    collision = IntegrityError("stmt", {}, Exception("uq_clips_video_rank"))
    session = _append_session(max_ranks=[12, 14], commit_effects=[collision, None])
    clips = await append_ranked_clips(session, uuid.uuid4(), uuid.uuid4(), _RANKED)

    assert session.rollback.await_count == 1
    assert [c.rank for c in clips] == [15, 16]  # re-derived offset, not 13/14


async def test_append_empty_ranked_is_noop() -> None:
    from clip_engine.ranking import append_ranked_clips

    session = AsyncMock()
    assert await append_ranked_clips(session, uuid.uuid4(), uuid.uuid4(), []) == []
    session.commit.assert_not_awaited()


# ── POST /videos/{id}/clips/generate-more ────────────────────────────────────


def _creator() -> MagicMock:
    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    return c


def _video(creator_id: uuid.UUID) -> MagicMock:
    v = MagicMock(spec=Video)
    v.id = uuid.uuid4()
    v.creator_id = creator_id
    v.ingest_status = IngestStatus.done
    return v


def _signals() -> MagicMock:
    s = MagicMock(spec=Signals)
    s.timeline_jsonb = {"version": 1, "duration_s": 600.0, "events": []}
    return s


def _clip_stub(
    video_id: uuid.UUID,
    rank: int | None,
    setup_start_s: float | None = 10.0,
    start_s: float = 10.0,
    end_s: float = 70.0,
) -> MagicMock:
    c = MagicMock()
    c.id = uuid.uuid4()
    c.video_id = video_id
    c.setup_start_s = setup_start_s
    c.start_s = start_s
    c.end_s = end_s
    c.peak_s = (start_s + end_s) / 2
    c.score = 0.9
    c.rank = rank
    c.signals_jsonb = {"principle": "Hook in the first 3 seconds", "reasoning": "x"}
    c.render_status = RenderStatus.pending
    c.render_uri = None
    c.cleaned_render_uri = None
    c.applied_title = None
    c.suggested_title = None
    c.suggested_description = None
    c.suggested_hook = None
    c.applied_description = None
    c.style_preset = None
    return c


def _request_session(video: MagicMock, signals: MagicMock) -> AsyncMock:
    session = AsyncMock()
    session.info = {}

    async def _get(model, pk):
        if model is Signals:
            return signals
        return None

    session.get = AsyncMock(side_effect=_get)

    async def _execute(stmt, *a, **kw):
        entity = stmt.column_descriptions[0]["type"]
        if entity is Video:
            return owned_lookup_result(stmt, video)
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        return result

    session.execute = AsyncMock(side_effect=_execute)
    return session


def _persist_session() -> AsyncMock:
    session = AsyncMock()
    session.info = {}
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


def _override(creator: MagicMock, session: AsyncMock) -> None:
    app.dependency_overrides[get_current_creator] = override_current_creator(creator)

    async def _session_gen():
        yield session

    app.dependency_overrides[get_session] = _session_gen


@pytest.fixture()
def _generate_more_mocks():
    """Patch stack shared by the router tests; yields a dict of the mocks."""
    mocks = {
        "score": AsyncMock(return_value=[]),
        "append": AsyncMock(return_value=[]),
        "load": AsyncMock(return_value=[]),
        "metadata": MagicMock(),
        "persist_session": _persist_session(),
    }
    with (
        patch("routers.clips.check_positive_balance", new=AsyncMock()),
        patch("dna.profile.get_active", new=AsyncMock(return_value=None)),
        patch("dna.profile.get_style_notes", new=AsyncMock(return_value=None)),
        patch("clip_engine.ranking.score_and_rank", new=mocks["score"]),
        patch("clip_engine.ranking.append_ranked_clips", new=mocks["append"]),
        patch("clip_engine.ranking.load_existing_clips", new=mocks["load"]),
        patch("worker.tasks.generate_clip_metadata", new=mocks["metadata"]),
        patch("db.AsyncSessionLocal", new=MagicMock(return_value=mocks["persist_session"])),
    ):
        yield mocks
    app.dependency_overrides.clear()


def test_generate_more_409_when_no_engine_clips(client, _generate_more_mocks) -> None:
    creator = _creator()
    video = _video(creator.id)
    _override(creator, _request_session(video, _signals()))
    # Only a creator-made clip (rank=None) exists — regeneration needs an
    # engine-generated baseline.
    _generate_more_mocks["load"].return_value = [_clip_stub(video.id, rank=None)]

    resp = client.post(f"/videos/{video.id}/clips/generate-more")
    assert resp.status_code == 409
    assert "generate clips first" in resp.json()["detail"]
    _generate_more_mocks["score"].assert_not_awaited()


def test_generate_more_409_at_total_cap(client, _generate_more_mocks) -> None:
    creator = _creator()
    video = _video(creator.id)
    _override(creator, _request_session(video, _signals()))
    _generate_more_mocks["load"].return_value = [
        _clip_stub(video.id, rank=i + 1) for i in range(settings.CLIP_REGEN_TOTAL_CAP)
    ]

    resp = client.post(f"/videos/{video.id}/clips/generate-more")
    assert resp.status_code == 409
    assert "limit" in resp.json()["detail"]
    _generate_more_mocks["score"].assert_not_awaited()


def test_generate_more_happy_path(client, _generate_more_mocks) -> None:
    creator = _creator()
    video = _video(creator.id)
    _override(creator, _request_session(video, _signals()))

    engine_clip = _clip_stub(video.id, rank=1, setup_start_s=5.0, start_s=10.0, end_s=70.0)
    # Creator-made clip: setup NULL must coerce to start_s in the exclusion window.
    manual_clip = _clip_stub(video.id, rank=None, setup_start_s=None, start_s=200.0, end_s=260.0)
    existing = [engine_clip, manual_clip]
    appended = _clip_stub(video.id, rank=2, setup_start_s=400.0, start_s=400.0, end_s=460.0)
    # First load (request session) → existing; second (persist session) → full set.
    _generate_more_mocks["load"].side_effect = [existing, existing + [appended]]
    _generate_more_mocks["score"].return_value = [
        {"setup_start_s": 400.0, "start_s": 400.0, "end_s": 460.0, "peak_s": 430.0, "score": 0.8}
    ]
    _generate_more_mocks["append"].return_value = [appended]

    resp = client.post(f"/videos/{video.id}/clips/generate-more")
    assert resp.status_code == 200
    assert len(resp.json()["clips"]) == 3  # FULL refreshed list, not just the new rows

    score_kwargs = _generate_more_mocks["score"].await_args.kwargs
    assert score_kwargs["max_candidates"] == settings.CLIP_REGEN_BATCH_MAX
    assert score_kwargs["exclude_windows"] == [
        {"setup_start_s": 5.0, "end_s": 70.0},
        {"setup_start_s": 200.0, "end_s": 260.0},  # NULL setup coerced to start_s
    ]
    # Append ran on the reacquired RLS-stamped session, and metadata fill fired.
    append_args = _generate_more_mocks["append"].await_args.args
    assert append_args[0] is _generate_more_mocks["persist_session"]
    assert _generate_more_mocks["persist_session"].info["creator_id"] == creator.id
    _generate_more_mocks["metadata"].delay.assert_called_once_with(str(video.id))


def test_generate_more_no_new_moments_message(client, _generate_more_mocks) -> None:
    creator = _creator()
    video = _video(creator.id)
    _override(creator, _request_session(video, _signals()))

    existing = [_clip_stub(video.id, rank=1)]
    _generate_more_mocks["load"].side_effect = [existing, existing]
    _generate_more_mocks["score"].return_value = []

    resp = client.post(f"/videos/{video.id}/clips/generate-more")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["clips"]) == 1
    assert "No new distinct moments" in body["message"]
    _generate_more_mocks["append"].assert_not_awaited()
    _generate_more_mocks["metadata"].delay.assert_not_called()
