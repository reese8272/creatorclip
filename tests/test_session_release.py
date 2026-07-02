"""Issue 82b — request DB sessions must not be held across long external calls.

Pins the pool-starvation fix on POST /videos/{id}/clips/generate:

1. The request-scoped session is CLOSED before the per-candidate LLM scoring
   round-trip fires, and persistence runs on a DIFFERENT, freshly acquired
   session.
2. HARD criterion: the reacquired persist session has
   ``session.info["creator_id"]`` stamped BEFORE its first query — the RLS GUC
   is emitted per-transaction from ``session.info`` (db.py after_begin), so a
   missing stamp silently disables per-creator isolation.
3. Load shape: 10 concurrent generate calls with the scoring mocked to sleep
   must not exhaust a small connection pool — connections are only checked out
   for the brief read/persist phases, never across the sleep.

Unit lane: DB is faked at the session boundary (no Docker needed).
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import ASGITransport, AsyncClient

from auth import get_current_creator
from db import get_session
from main import app
from models import Creator, IngestStatus, RenderStatus, Signals, Video
from tests._helpers import override_current_creator, owned_lookup_result

# ── Stubs ─────────────────────────────────────────────────────────────────────


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
    s.timeline_jsonb = {}
    return s


def _clip_stub(video_id: uuid.UUID) -> MagicMock:
    c = MagicMock()
    c.id = uuid.uuid4()
    c.video_id = video_id
    c.setup_start_s = 0.0
    c.start_s = 0.0
    c.end_s = 30.0
    c.peak_s = 10.0
    c.score = 0.9
    c.rank = 1
    c.signals_jsonb = {"principle": "Hook in the first 3 seconds", "reasoning": "x"}
    c.render_status = RenderStatus.pending
    c.render_uri = None
    c.cleaned_render_uri = None
    return c


_RANKED = [
    {
        "setup_start_s": 0.0,
        "start_s": 0.0,
        "end_s": 30.0,
        "peak_s": 10.0,
        "score": 0.9,
        "rank": 1,
    }
]


def _request_session(video: MagicMock, signals: MagicMock) -> AsyncMock:
    """Fake request-scoped session: Video via the get_owned ownership select
    (Issue 109e), Signals/Transcript via .get(), empty clip list otherwise."""
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
    """Fake session produced by the reacquired db.AsyncSessionLocal factory."""
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


# ── 1. Session released before scoring; persist uses a distinct session ──────


def test_generate_clips_closes_request_session_before_scoring(client) -> None:
    creator = _creator()
    video = _video(creator.id)
    request_session = _request_session(video, _signals())
    persist_session = _persist_session()
    _override(creator, request_session)

    close_count_when_scoring_fired: list[int] = []
    persist_sessions_seen: list[object] = []

    async def _fake_score(*args, **kwargs):
        close_count_when_scoring_fired.append(request_session.close.await_count)
        return _RANKED

    async def _fake_persist(session, video_id, creator_id, ranked):
        persist_sessions_seen.append(session)
        return [_clip_stub(video.id)]

    with (
        patch("routers.clips.check_positive_balance", new=AsyncMock()),
        patch("dna.profile.get_active", new=AsyncMock(return_value=None)),
        patch("clip_engine.ranking.score_and_rank", new=AsyncMock(side_effect=_fake_score)),
        patch("clip_engine.ranking.persist_ranked_clips", new=AsyncMock(side_effect=_fake_persist)),
        patch("db.AsyncSessionLocal", new=MagicMock(return_value=persist_session)),
    ):
        resp = client.post(f"/videos/{video.id}/clips/generate")

    assert resp.status_code == 200
    assert len(resp.json()["clips"]) == 1
    # The request session was closed BEFORE the LLM scoring call fired.
    assert close_count_when_scoring_fired == [1]
    # Persistence ran on a freshly acquired session, not the request session.
    assert persist_sessions_seen == [persist_session]
    assert persist_sessions_seen[0] is not request_session


# ── 2. HARD criterion: reacquired session stamps creator_id before 1st query ─


def test_reacquired_persist_session_stamps_creator_id(client) -> None:
    """Regression (Issue 82b): the persist-path session MUST carry
    ``session.info["creator_id"]`` before its first query, or the after_begin
    listener emits no RLS GUC and per-creator isolation silently breaks."""
    creator = _creator()
    video = _video(creator.id)
    request_session = _request_session(video, _signals())
    persist_session = _persist_session()
    _override(creator, request_session)

    stamped_at_first_query: list[object] = []

    async def _fake_persist(session, video_id, creator_id, ranked):
        # persist_ranked_clips issues the first query on this session — the
        # stamp must already be present here.
        stamped_at_first_query.append(session.info.get("creator_id"))
        return [_clip_stub(video.id)]

    with (
        patch("routers.clips.check_positive_balance", new=AsyncMock()),
        patch("dna.profile.get_active", new=AsyncMock(return_value=None)),
        patch("clip_engine.ranking.score_and_rank", new=AsyncMock(return_value=_RANKED)),
        patch("clip_engine.ranking.persist_ranked_clips", new=AsyncMock(side_effect=_fake_persist)),
        patch("db.AsyncSessionLocal", new=MagicMock(return_value=persist_session)),
    ):
        resp = client.post(f"/videos/{video.id}/clips/generate")

    assert resp.status_code == 200
    assert stamped_at_first_query == [creator.id]


# ── 3. Load shape: 10 concurrent calls, small pool, LLM sleeping ─────────────


class _FakePool:
    """Bounded fake connection pool: acquire waits at most ``timeout`` seconds
    (a real SQLAlchemy pool blocks, then raises) and tracks peak checkout."""

    def __init__(self, size: int, timeout: float) -> None:
        self._sem = asyncio.Semaphore(size)
        self._timeout = timeout
        self.in_use = 0
        self.max_in_use = 0
        self.exhausted = False

    async def acquire(self) -> None:
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=self._timeout)
        except TimeoutError:
            self.exhausted = True
            raise RuntimeError("connection pool exhausted") from None
        self.in_use += 1
        self.max_in_use = max(self.max_in_use, self.in_use)

    def release(self) -> None:
        self.in_use -= 1
        self._sem.release()


class _PooledSession:
    """Session faked at the connection-lifecycle boundary: a connection is
    checked out lazily on the first DB operation of a transaction and returned
    on commit/rollback/close — mirroring SQLAlchemy autobegin semantics."""

    def __init__(self, pool: _FakePool, video: MagicMock, signals: MagicMock) -> None:
        self._pool = pool
        self._video = video
        self._signals = signals
        self._held = False
        self.info: dict = {}

    async def _checkout(self) -> None:
        if not self._held:
            await self._pool.acquire()
            self._held = True

    def _release(self) -> None:
        if self._held:
            self._pool.release()
            self._held = False

    async def get(self, model, pk):
        await self._checkout()
        if model is Signals:
            return self._signals
        return None

    async def execute(self, stmt=None, *args, **kwargs):
        await self._checkout()
        descriptions = getattr(stmt, "column_descriptions", None)
        entity = descriptions[0]["type"] if descriptions else None
        if entity is Video:  # get_owned ownership select (Issue 109e)
            return owned_lookup_result(stmt, self._video)
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        return result

    async def commit(self) -> None:
        self._release()

    async def rollback(self) -> None:
        self._release()

    async def close(self) -> None:
        self._release()

    async def __aenter__(self) -> "_PooledSession":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        await self.close()
        return False


async def test_ten_concurrent_generates_do_not_exhaust_small_pool() -> None:
    """10 concurrent /clips/generate calls with scoring sleeping 0.2 s against a
    3-connection pool: the refactored path releases the connection before the
    sleep, so no request ever waits out the pool timeout (which would 500)."""
    creator = _creator()
    video = _video(creator.id)
    signals = _signals()
    pool = _FakePool(size=3, timeout=0.1)

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)

    async def _session_gen():
        session = _PooledSession(pool, video, signals)
        try:
            yield session
        finally:
            await session.close()

    app.dependency_overrides[get_session] = _session_gen

    async def _sleepy_score(*args, **kwargs):
        # Stands in for the 30–120 s per-candidate LLM round-trip. If any
        # session were still holding a connection here, 10 concurrent sleeps
        # over a 3-conn pool would trip the pool timeout.
        await asyncio.sleep(0.2)
        return _RANKED

    async def _fake_persist(session, video_id, creator_id, ranked):
        await session.execute(None)  # persist touches the pool…
        await session.commit()  # …and returns the connection
        return [_clip_stub(video.id)]

    try:
        with (
            patch("routers.clips.check_positive_balance", new=AsyncMock()),
            patch("dna.profile.get_active", new=AsyncMock(return_value=None)),
            patch("clip_engine.ranking.score_and_rank", new=AsyncMock(side_effect=_sleepy_score)),
            patch(
                "clip_engine.ranking.persist_ranked_clips",
                new=AsyncMock(side_effect=_fake_persist),
            ),
            patch(
                "db.AsyncSessionLocal",
                new=MagicMock(side_effect=lambda: _PooledSession(pool, video, signals)),
            ),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
                responses = await asyncio.gather(
                    *(ac.post(f"/videos/{video.id}/clips/generate") for _ in range(10))
                )
    finally:
        app.dependency_overrides.clear()

    assert not pool.exhausted, "a request held its DB connection across the LLM sleep"
    assert [r.status_code for r in responses] == [200] * 10
    assert pool.max_in_use <= 3
    assert pool.in_use == 0  # every connection was returned
