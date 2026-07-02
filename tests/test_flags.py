"""Feature flags / kill switches (Issue 284) — 80/20 suite.

Covers: resolution order (DB row → env default → hard ON), TTL caching +
expiry, fail-open on DB error (warn once), the audited set_flag upsert, and
each of the four kill-switch gates blocking when disabled / passing when
enabled. Unit lane: sessions are mocked at the boundary.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import flags
from auth import get_current_creator
from db import get_session
from main import app
from models import Creator, FeatureFlag
from tests._helpers import override_current_creator, stub_get_owned

# ── Helpers ────────────────────────────────────────────────────────────────────


class _FakeSessionFactory:
    """Duck-typed async_sessionmaker: counts opens; returns a fixed row or raises."""

    def __init__(self, row: FeatureFlag | None = None, exc: Exception | None = None) -> None:
        self.row = row
        self.exc = exc
        self.calls = 0

    def __call__(self) -> Any:
        factory = self

        class _CM:
            async def __aenter__(self) -> Any:
                factory.calls += 1
                if factory.exc is not None:
                    raise factory.exc
                session = AsyncMock()
                session.get = AsyncMock(return_value=factory.row)
                return session

            async def __aexit__(self, *exc: object) -> bool:
                return False

        return _CM()


def _seed_cache(key: str, enabled: bool) -> None:
    """Pre-resolve a flag in the TTL cache so gates never touch a DB."""
    flags._cache[key] = (time.monotonic(), enabled)


def _make_creator() -> MagicMock:
    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    return c


def _override_session(session: Any) -> Any:
    async def _gen() -> Any:
        yield session

    return _gen


@pytest.fixture(autouse=True)
def _fresh_flag_state():
    flags._reset_cache()
    yield
    flags._reset_cache()
    app.dependency_overrides.clear()


# ── Resolution order ───────────────────────────────────────────────────────────


async def test_db_row_overrides_env_default() -> None:
    row = FeatureFlag(key="llm_generation", enabled=False, updated_by="ops")
    factory = _FakeSessionFactory(row=row)
    assert await flags.flag_enabled("llm_generation", session_factory=factory) is False


async def test_missing_row_falls_back_to_env_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flags.settings, "FLAG_SIGNUP_ENABLED", False)
    factory = _FakeSessionFactory(row=None)
    assert await flags.flag_enabled("signup", session_factory=factory) is False


async def test_unknown_key_is_hard_default_on() -> None:
    factory = _FakeSessionFactory(row=None)
    assert await flags.flag_enabled("no_such_flag", session_factory=factory) is True


# ── TTL cache ──────────────────────────────────────────────────────────────────


async def test_ttl_caches_within_window_and_rereads_after_expiry() -> None:
    row = FeatureFlag(key="render_intake", enabled=True, updated_by="ops")
    factory = _FakeSessionFactory(row=row)

    assert await flags.flag_enabled("render_intake", session_factory=factory) is True
    assert await flags.flag_enabled("render_intake", session_factory=factory) is True
    assert factory.calls == 1  # second call served from cache

    row.enabled = False  # operator flips the row
    ts, val = flags._cache["render_intake"]
    flags._cache["render_intake"] = (ts - flags.FLAG_TTL_S - 1, val)  # expire the entry
    assert await flags.flag_enabled("render_intake", session_factory=factory) is False
    assert factory.calls == 2


# ── Fail-open ──────────────────────────────────────────────────────────────────


async def test_fail_open_on_db_error_warns_once(caplog: pytest.LogCaptureFixture) -> None:
    factory = _FakeSessionFactory(exc=RuntimeError("db down"))
    with caplog.at_level(logging.WARNING, logger="flags"):
        assert await flags.flag_enabled("llm_generation", session_factory=factory) is True
        flags._cache.clear()  # force a re-query without resetting warn-once state
        assert await flags.flag_enabled("llm_generation", session_factory=factory) is True
    warnings = [r for r in caplog.records if "failing open" in r.message]
    assert len(warnings) == 1


# ── set_flag upsert + audited event ────────────────────────────────────────────


async def test_set_flag_upserts_and_emits_flag_flipped() -> None:
    session = AsyncMock()
    session.get = AsyncMock(return_value=None)
    session.add = MagicMock()
    _seed_cache("signup", True)  # must be invalidated by the flip

    with (
        patch("flags.log_event") as log_event_mock,
        patch("event_log.record_event", new=AsyncMock()) as record_event_mock,
    ):
        row = await flags.set_flag(
            "signup", False, updated_by="reese", reason="beta full", session=session
        )

    assert row.enabled is False and row.updated_by == "reese" and row.reason == "beta full"
    session.add.assert_called_once()
    session.commit.assert_awaited_once()
    assert "signup" not in flags._cache
    assert log_event_mock.call_args[0][0] == "flag_flipped"
    assert record_event_mock.await_args.kwargs["event"] == "flag_flipped"


# ── Gate: llm_generation (routes) ──────────────────────────────────────────────


def test_llm_gate_blocks_when_disabled(client) -> None:
    _seed_cache("llm_generation", False)
    app.dependency_overrides[get_current_creator] = override_current_creator(_make_creator())
    app.dependency_overrides[get_session] = _override_session(AsyncMock())

    resp = client.post(f"/creators/me/videos/{uuid.uuid4()}/titles")
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "llm_generation_disabled"


def test_llm_gate_passes_when_enabled(client) -> None:
    _seed_cache("llm_generation", True)
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=None)  # video not found → 404 past the gate
    app.dependency_overrides[get_current_creator] = override_current_creator(_make_creator())
    app.dependency_overrides[get_session] = _override_session(session)

    with patch("routers.titles.check_positive_balance", new=AsyncMock()):
        resp = client.post(f"/creators/me/videos/{uuid.uuid4()}/titles")
    assert resp.status_code == 404  # gate passed; route body ran


# ── Gate: render_intake (routes) ───────────────────────────────────────────────


def test_render_gate_blocks_when_disabled(client) -> None:
    _seed_cache("render_intake", False)
    app.dependency_overrides[get_current_creator] = override_current_creator(_make_creator())
    app.dependency_overrides[get_session] = _override_session(AsyncMock())

    resp = client.post(f"/clips/{uuid.uuid4()}/render")
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "render_intake_disabled"


def test_render_gate_passes_when_enabled(client) -> None:
    _seed_cache("render_intake", True)
    session = AsyncMock()
    stub_get_owned(session, None)  # clip not found → 404 past the gate
    app.dependency_overrides[get_current_creator] = override_current_creator(_make_creator())
    app.dependency_overrides[get_session] = _override_session(session)

    with patch("routers.clips.check_positive_balance", new=AsyncMock()):
        resp = client.post(f"/clips/{uuid.uuid4()}/render")
    assert resp.status_code == 404


# ── Gate: signup (OAuth callback) ──────────────────────────────────────────────


async def test_signup_gate_raises_for_new_creator_only() -> None:
    from routers.auth import SignupsPausedError, _exchange_and_persist

    _seed_cache("signup", False)
    session = AsyncMock()
    creator = _make_creator()
    with (
        patch("routers.auth.exchange_code", new=AsyncMock(return_value={"access_token": "t"})),
        patch(
            "routers.auth.fetch_creator_identity",
            new=AsyncMock(return_value={"google_sub": "sub"}),
        ),
        patch("routers.auth.upsert_creator", new=AsyncMock(return_value=(creator, True))),
        pytest.raises(SignupsPausedError),
    ):
        await _exchange_and_persist(session, "code")


async def test_signup_gate_allows_existing_creator() -> None:
    from routers.auth import _exchange_and_persist

    _seed_cache("signup", False)
    session = AsyncMock()
    creator = _make_creator()
    with (
        patch("routers.auth.exchange_code", new=AsyncMock(return_value={"access_token": "t"})),
        patch(
            "routers.auth.fetch_creator_identity",
            new=AsyncMock(return_value={"google_sub": "sub"}),
        ),
        patch("routers.auth.upsert_creator", new=AsyncMock(return_value=(creator, False))),
        patch("routers.auth.store_or_update_tokens", new=AsyncMock()),
    ):
        got, is_new, _ = await _exchange_and_persist(session, "code")
    assert got is creator and is_new is False


def test_signup_gate_redirects_with_beta_at_capacity(client) -> None:
    from routers.auth import SignupsPausedError

    app.dependency_overrides[get_session] = _override_session(AsyncMock())
    client.cookies.set("cc_oauth_state", "s")
    with patch(
        "routers.auth._exchange_and_persist", new=AsyncMock(side_effect=SignupsPausedError())
    ):
        resp = client.get("/auth/callback?code=x&state=s", follow_redirects=False)
    client.cookies.clear()
    assert resp.status_code == 302
    assert resp.headers["location"] == "/app/login?error=signup_paused"


# ── Gate: youtube_publish (worker task) ────────────────────────────────────────


async def test_publish_gate_records_failed_row_and_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import db
    from models import PublishStatus
    from worker.tasks import _publish_to_youtube_async
    from youtube.publish import YouTubeUploadError

    _seed_cache("youtube_publish", False)

    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None  # no existing publication row
    session.execute = AsyncMock(return_value=result)
    clip = MagicMock()
    clip.creator_id = uuid.uuid4()
    session.get = AsyncMock(return_value=clip)
    session.add = MagicMock()

    class _CM:
        async def __aenter__(self) -> Any:
            return session

        async def __aexit__(self, *exc: object) -> bool:
            return False

    # Issue 231: the bootstrap uses AdminSessionLocal; the publication write
    # runs on tenant_session → AsyncSessionLocal. Same mock serves both.
    monkeypatch.setattr(db, "AdminSessionLocal", lambda: _CM())
    monkeypatch.setattr(db, "AsyncSessionLocal", lambda: _CM())

    with pytest.raises(YouTubeUploadError, match="youtube_publish_disabled"):
        await _publish_to_youtube_async("task-1", str(uuid.uuid4()))

    pub = session.add.call_args[0][0]
    assert pub.status == PublishStatus.failed
    assert pub.error == "youtube_publish_disabled"
    session.commit.assert_awaited_once()
