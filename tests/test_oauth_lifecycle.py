"""
Tests for Issue 36 — OAuth token lifecycle hardening.

Covers:
  (a) DELETE /auth/me revokes the refresh_token (not the access_token) at Google,
      and tolerates 400 invalid_token / token_revoked as success.
  (b) get_valid_access_token deletes the YoutubeToken row when Google returns
      400 invalid_grant during refresh.
  (c) YouTube Data API _get_json retries quotaExceeded but raises YouTubeAuthError
      on authError without retrying.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from auth import SESSION_COOKIE, create_session_token, get_current_creator
from db import get_session
from main import app
from youtube.errors import YouTubeAuthError

# ── Test helpers ──────────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status_code: int, body: dict | None = None):
        self.status_code = status_code
        self._body = body or {}
        self.request = httpx.Request("GET", "https://example.com")

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"{self.status_code}", request=self.request, response=self)


def _make_creator():
    c = MagicMock()
    c.id = uuid.uuid4()
    c.channel_id = "UCtest"
    c.email = "test@example.com"
    return c


def _session_cookie_for(creator) -> dict:
    return {SESSION_COOKIE: create_session_token(creator.id)}


# ── (a) delete_account revokes the refresh token ──────────────────────────────


def test_delete_account_revokes_refresh_token(client):
    """The revoke URL must be hit with the decrypted refresh token, not the access token."""
    from crypto import encrypt

    creator = _make_creator()
    token_row = MagicMock()
    token_row.access_token_encrypted = encrypt("access-secret")
    token_row.refresh_token_encrypted = encrypt("refresh-secret")

    session_mock = AsyncMock()
    session_mock.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: token_row))
    session_mock.delete = AsyncMock()
    session_mock.commit = AsyncMock()
    session_mock.add = MagicMock()

    async def _session():
        yield session_mock

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session

    captured: dict = {}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, params=None, **kwargs):
            captured["url"] = url
            captured["params"] = params or {}
            return _FakeResponse(200)

    try:
        with (
            patch("worker.storage.delete_prefix", return_value=0),
            patch("routers.auth.httpx.AsyncClient", FakeAsyncClient),
        ):
            resp = client.delete("/auth/me", cookies=_session_cookie_for(creator))
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 204
    assert captured["url"] == "https://oauth2.googleapis.com/revoke"
    assert captured["params"].get("token") == "refresh-secret"


def test_delete_account_tolerates_400_invalid_token(client):
    """400 invalid_token from Google means the grant is already gone — that's success."""
    from crypto import encrypt

    creator = _make_creator()
    token_row = MagicMock()
    token_row.access_token_encrypted = encrypt("access-secret")
    token_row.refresh_token_encrypted = encrypt("refresh-secret")

    session_mock = AsyncMock()
    session_mock.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: token_row))
    session_mock.delete = AsyncMock()
    session_mock.commit = AsyncMock()
    session_mock.add = MagicMock()

    async def _session():
        yield session_mock

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, params=None, **kwargs):
            return _FakeResponse(400, {"error": "invalid_token"})

    try:
        with (
            patch("worker.storage.delete_prefix", return_value=0),
            patch("routers.auth.httpx.AsyncClient", FakeAsyncClient),
        ):
            resp = client.delete("/auth/me", cookies=_session_cookie_for(creator))
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 204
    session_mock.delete.assert_called_once_with(creator)


# ── (b) get_valid_access_token deletes the row on invalid_grant ───────────────


@pytest.mark.asyncio
async def test_get_valid_access_token_deletes_row_on_invalid_grant():
    """When refresh returns 400 invalid_grant, the YoutubeToken row must be deleted."""
    from crypto import encrypt
    from youtube.oauth import get_valid_access_token

    creator_id = uuid.uuid4()
    row = MagicMock()
    row.refresh_token_encrypted = encrypt("dead-refresh")
    row.access_token_encrypted = encrypt("dead-access")
    row.expires_at = datetime.now(UTC) - timedelta(minutes=1)  # forces refresh
    row.scope = "scope"

    session_mock = AsyncMock()
    session_mock.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: row))
    session_mock.commit = AsyncMock()

    async def fake_refresh(_):
        resp = _FakeResponse(400, {"error": "invalid_grant"})
        raise httpx.HTTPStatusError("400", request=resp.request, response=resp)

    with (
        patch("youtube.oauth.refresh_access_token", side_effect=fake_refresh),
        pytest.raises(HTTPException) as exc_info,
    ):
        await get_valid_access_token(creator_id, session_mock)

    assert exc_info.value.status_code == 401
    # The session.execute was called twice: once for select, once for delete
    assert session_mock.execute.await_count >= 2
    session_mock.commit.assert_awaited()


@pytest.mark.asyncio
async def test_get_valid_access_token_keeps_row_on_other_400():
    """Non-invalid_grant refresh errors should NOT delete the row (could be transient)."""
    from crypto import encrypt
    from youtube.oauth import get_valid_access_token

    creator_id = uuid.uuid4()
    row = MagicMock()
    row.refresh_token_encrypted = encrypt("refresh-token")
    row.access_token_encrypted = encrypt("access-token")
    row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    row.scope = "scope"

    session_mock = AsyncMock()
    session_mock.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: row))
    session_mock.commit = AsyncMock()

    async def fake_refresh(_):
        resp = _FakeResponse(400, {"error": "invalid_client"})
        raise httpx.HTTPStatusError("400", request=resp.request, response=resp)

    with (
        patch("youtube.oauth.refresh_access_token", side_effect=fake_refresh),
        pytest.raises(HTTPException),
    ):
        await get_valid_access_token(creator_id, session_mock)

    # Only the SELECT — no DELETE on commit path
    assert session_mock.execute.await_count == 1
    session_mock.commit.assert_not_awaited()


# ── (c) data_api _get_json: retry quotaExceeded, raise on authError ───────────


@pytest.mark.asyncio
async def test_get_json_retries_on_quota_exceeded():
    """403 quotaExceeded should retry with backoff, then succeed."""
    from youtube import data_api

    call_count = {"n": 0}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers=None, params=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _FakeResponse(
                    403,
                    {
                        "error": {
                            "errors": [{"reason": "quotaExceeded"}],
                            "code": 403,
                        }
                    },
                )
            return _FakeResponse(200, {"items": [{"id": "ok"}]})

    with (
        patch("youtube.data_api.httpx.AsyncClient", FakeAsyncClient),
        patch("youtube.data_api.consume", new=AsyncMock()),
        patch("youtube.data_api.asyncio.sleep", new=AsyncMock()),
    ):
        result = await data_api._get_json("tok", "https://x/y", {})

    assert result == {"items": [{"id": "ok"}]}
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_get_json_raises_auth_error_without_retry():
    """403 authError must raise YouTubeAuthError on the first response — no retries."""
    from youtube import data_api

    call_count = {"n": 0}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers=None, params=None):
            call_count["n"] += 1
            return _FakeResponse(
                403,
                {"error": {"errors": [{"reason": "authError"}], "code": 403}},
            )

    with (
        patch("youtube.data_api.httpx.AsyncClient", FakeAsyncClient),
        patch("youtube.data_api.consume", new=AsyncMock()),
        patch("youtube.data_api.asyncio.sleep", new=AsyncMock()),
        pytest.raises(YouTubeAuthError) as exc_info,
    ):
        await data_api._get_json("tok", "https://x/y", {})

    assert exc_info.value.reason == "authError"
    assert exc_info.value.status_code == 403
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_get_json_raises_on_401():
    """401 should also raise YouTubeAuthError without retrying."""
    from youtube import data_api

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers=None, params=None):
            return _FakeResponse(401, {})

    with (
        patch("youtube.data_api.httpx.AsyncClient", FakeAsyncClient),
        patch("youtube.data_api.consume", new=AsyncMock()),
        pytest.raises(YouTubeAuthError) as exc_info,
    ):
        await data_api._get_json("tok", "https://x/y", {})

    assert exc_info.value.status_code == 401


# ── (c) analytics _fetch_report: same classifier path ─────────────────────────


@pytest.mark.asyncio
async def test_fetch_report_raises_on_account_closed():
    """Permanent 403 reasons other than authError must also raise YouTubeAuthError."""
    from youtube import analytics

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers=None, params=None):
            return _FakeResponse(
                403,
                {"error": {"errors": [{"reason": "accountClosed"}], "code": 403}},
            )

    with (
        patch("youtube.analytics.httpx.AsyncClient", FakeAsyncClient),
        patch("youtube.analytics.consume", new=AsyncMock()),
        pytest.raises(YouTubeAuthError) as exc_info,
    ):
        await analytics._fetch_report("tok", {})

    assert exc_info.value.reason == "accountClosed"


# ── Worker cleanup path on YouTubeAuthError ───────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_analytics_deletes_token_row_on_auth_error():
    """When sync raises YouTubeAuthError, the beat loop must delete the YoutubeToken row."""
    from worker import tasks

    creator = MagicMock()
    creator.id = uuid.uuid4()
    creator.channel_id = "UCx"

    delete_called = {"n": 0}

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, stmt):
            text = str(stmt).lower()
            result = MagicMock()
            if "delete" in text:
                delete_called["n"] += 1
                return result
            result.scalars = lambda: iter([creator])
            return result

        async def commit(self):
            return None

        async def rollback(self):
            return None

    with (
        patch("worker.tasks.AsyncSessionLocal", FakeSession),
        patch("worker.tasks.remaining", new=AsyncMock(return_value=10000)),
        patch("youtube.oauth.get_valid_access_token", new=AsyncMock(return_value="tok")),
        patch(
            "youtube.analytics.sync_video_analytics",
            new=AsyncMock(side_effect=YouTubeAuthError("authError", 403)),
        ),
        patch("youtube.analytics.sync_audience_data", new=AsyncMock()),
    ):
        await tasks._refresh_youtube_analytics_async()

    assert delete_called["n"] >= 1
