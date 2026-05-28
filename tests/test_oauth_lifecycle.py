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


def _make_lock_redis() -> AsyncMock:
    """Return a fake Redis client that always acquires the refresh lock.

    Used by tests that exercise the refresh branch of get_valid_access_token
    so they don't need a live Redis connection.
    """
    mock = AsyncMock()
    mock.set = AsyncMock(return_value=True)  # lock acquired
    mock.eval = AsyncMock(return_value=1)  # Lua release succeeds
    return mock


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
        patch("youtube.oauth.get_redis_client", return_value=_make_lock_redis()),
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
        patch("youtube.oauth.get_redis_client", return_value=_make_lock_redis()),
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


# ── Issue 45: concurrent refresh lock ────────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_refresh_only_calls_google_once():
    """Two concurrent get_valid_access_token calls must only call Google once.

    Coroutine A acquires the Redis lock and performs the refresh.
    Coroutine B sees the lock is taken, waits, then reads the fresh token from DB.
    """
    import asyncio

    from crypto import encrypt
    from youtube.oauth import get_valid_access_token

    creator_id = uuid.uuid4()
    refresh_call_count = {"n": 0}

    # Initial token row — expired, forces the refresh branch.
    initial_expires = datetime.now(UTC) - timedelta(minutes=1)
    # After refresh, coroutine B's re-read should see a future expiry.
    refreshed_expires = datetime.now(UTC) + timedelta(hours=1)

    def _make_row(expires_at):
        row = MagicMock()
        row.refresh_token_encrypted = encrypt("refresh-token")
        row.access_token_encrypted = encrypt("fresh-access-token")
        row.expires_at = expires_at
        row.scope = "scope"
        return row

    # Fake session for coroutine A: returns an expired row on all execute() calls
    # (store_or_update_tokens does a second SELECT inside it, so we need a more
    # permissive mock that returns an existing row for the upsert path).
    session_a = AsyncMock()
    session_a.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=lambda: _make_row(initial_expires))
    )
    session_a.commit = AsyncMock()

    # Fake session for coroutine B: first execute (initial read) returns expired row;
    # subsequent executes (retries) return the refreshed row so B exits without 503.
    session_b_call_count = {"n": 0}

    async def _session_b_execute(_stmt):
        session_b_call_count["n"] += 1
        if session_b_call_count["n"] == 1:
            # First read: token still expired (before A commits)
            return MagicMock(scalar_one_or_none=lambda: _make_row(initial_expires))
        # Subsequent reads: token refreshed
        return MagicMock(scalar_one_or_none=lambda: _make_row(refreshed_expires))

    session_b = AsyncMock()
    session_b.execute = _session_b_execute
    session_b.commit = AsyncMock()

    # Redis mock: A acquires the lock (set returns True); B does not (set returns False).
    lock_set_call_count = {"n": 0}

    async def _fake_set(key, value, nx=False, ex=None):
        lock_set_call_count["n"] += 1
        # First caller acquires; second does not.
        return lock_set_call_count["n"] == 1

    async def _fake_eval(script, num_keys, key, token):
        # Lua release — always succeeds for the test.
        return 1

    fake_redis = AsyncMock()
    fake_redis.set = _fake_set
    fake_redis.eval = _fake_eval

    async def _slow_refresh(refresh_token: str) -> dict:
        """Simulate a Google round-trip that takes 100 ms."""
        refresh_call_count["n"] += 1
        await asyncio.sleep(0.1)
        return {
            "access_token": "fresh-access-token",
            "expires_in": 3600,
            "scope": "scope",
        }

    with (
        patch("youtube.oauth.get_redis_client", return_value=fake_redis),
        patch("youtube.oauth.refresh_access_token", side_effect=_slow_refresh),
        patch("youtube.oauth.asyncio.sleep", new=AsyncMock()),  # fast-forward B's poll sleep
    ):
        token_a, token_b = await asyncio.gather(
            get_valid_access_token(creator_id, session_a),
            get_valid_access_token(creator_id, session_b),
        )

    assert refresh_call_count["n"] == 1, "Google must be called exactly once"
    assert token_a == "fresh-access-token"
    assert token_b == "fresh-access-token"


@pytest.mark.asyncio
async def test_lock_releases_after_success():
    """After a successful refresh the Redis lock key must be deleted."""
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

    # Track eval (Lua release) calls.
    eval_calls: list[tuple] = []

    async def _fake_eval(script, num_keys, key, token):
        eval_calls.append((key, token))
        return 1  # 1 == deleted

    fake_redis = AsyncMock()
    fake_redis.set = AsyncMock(return_value=True)  # always acquires lock
    fake_redis.eval = _fake_eval

    async def _fake_refresh(_refresh_token: str) -> dict:
        return {"access_token": "new-token", "expires_in": 3600, "scope": "scope"}

    with (
        patch("youtube.oauth.get_redis_client", return_value=fake_redis),
        patch("youtube.oauth.refresh_access_token", side_effect=_fake_refresh),
    ):
        token = await get_valid_access_token(creator_id, session_mock)

    assert token == "new-token"
    # The Lua compare-and-delete must have been called exactly once.
    assert len(eval_calls) == 1, "Lock release Lua script must be called after successful refresh"
    released_key = eval_calls[0][0]
    assert str(creator_id) in released_key, "Released key must reference the creator ID"


# ── Issue 51: Expanded OAuth lifecycle tests ──────────────────────────────────


# ── Test 1: refresh happy path ────────────────────────────────────────────────


async def test_refresh_path_success():
    """get_valid_access_token calls Google once and persists the new token."""
    from crypto import decrypt, encrypt
    from youtube.oauth import get_valid_access_token

    creator_id = uuid.uuid4()

    row = MagicMock()
    row.refresh_token_encrypted = encrypt("stored-refresh-token")
    row.access_token_encrypted = encrypt("old-access-token")
    row.expires_at = datetime.now(UTC) - timedelta(minutes=1)  # expired — triggers refresh
    row.scope = "openid https://www.googleapis.com/auth/youtube.readonly"

    session_mock = AsyncMock()
    # First execute: SELECT for get_valid_access_token
    # Second execute: SELECT inside store_or_update_tokens (upsert path)
    session_mock.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: row))
    session_mock.commit = AsyncMock()

    new_payload = {
        "access_token": "brand-new-access-token",
        "refresh_token": "brand-new-refresh-token",
        "expires_in": 3600,
        "scope": row.scope,
    }

    refresh_mock = AsyncMock(return_value=new_payload)

    with (
        patch("youtube.oauth.get_redis_client", return_value=_make_lock_redis()),
        patch("youtube.oauth.refresh_access_token", refresh_mock),
    ):
        result = await get_valid_access_token(creator_id, session_mock)

    # Google was called exactly once with the decrypted stored refresh token.
    refresh_mock.assert_awaited_once_with("stored-refresh-token")

    # The returned value is the new plaintext access token.
    assert result == "brand-new-access-token"

    # session.commit was called (tokens persisted).
    session_mock.commit.assert_awaited()

    # The DB row's access_token_encrypted was updated with the new value.
    assert row.access_token_encrypted != encrypt("old-access-token"), (
        "access_token_encrypted should have been updated on the row"
    )
    assert decrypt(row.access_token_encrypted) == "brand-new-access-token"


# ── Test 2: callback logs no token plaintext ──────────────────────────────────


def test_callback_logs_no_token_plaintext(client, caplog):
    """The OAuth callback must never log plaintext access or refresh tokens."""
    import logging

    from db import get_session
    from main import app

    state = "test-state-for-log-check"

    # A full token payload whose values would be obviously detectable in logs.
    fake_tokens = {
        "access_token": "plaintext-access-LEAK",
        "refresh_token": "plaintext-refresh-LEAK",
        "expires_in": 3600,
        "scope": "openid",
    }
    fake_identity = {
        "google_sub": "sub-12345",
        "email": "creator@example.com",
        "channel_id": "UCtest",
        "channel_title": "Test Channel",
    }

    creator = _make_creator()
    creator.id = uuid.uuid4()

    session_mock = AsyncMock()
    session_mock.flush = AsyncMock()
    session_mock.commit = AsyncMock()
    session_mock.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
    session_mock.add = MagicMock()

    async def _fake_session():
        yield session_mock

    prior_session_override = app.dependency_overrides.get(get_session)
    app.dependency_overrides[get_session] = _fake_session

    try:
        with (
            patch("routers.auth.exchange_code", AsyncMock(return_value=fake_tokens)),
            patch("routers.auth.fetch_creator_identity", AsyncMock(return_value=fake_identity)),
            patch(
                "routers.auth.upsert_creator",
                AsyncMock(return_value=(creator, False)),
            ),
            patch("routers.auth.store_or_update_tokens", AsyncMock()),
            patch("billing.ledger.grant_minutes", AsyncMock()),
            caplog.at_level(logging.DEBUG, logger="routers.auth"),
            caplog.at_level(logging.DEBUG, logger="youtube.oauth"),
        ):
            resp = client.get(
                f"/auth/callback?code=authcode&state={state}",
                cookies={"cc_oauth_state": state},
                follow_redirects=False,
            )
    finally:
        # The successful callback sets a cc_session JWT cookie in TestClient's
        # session-scoped cookie jar. Clear it so subsequent tests don't
        # inadvertently run authenticated against the real DB.
        client.cookies.clear()
        if prior_session_override is None:
            app.dependency_overrides.pop(get_session, None)
        else:
            app.dependency_overrides[get_session] = prior_session_override

    # Callback completed (302 redirect to /).
    assert resp.status_code == 302

    # Neither token plaintext must appear anywhere in the captured log output.
    assert "plaintext-access-LEAK" not in caplog.text, (
        "access_token plaintext leaked into log output"
    )
    assert "plaintext-refresh-LEAK" not in caplog.text, (
        "refresh_token plaintext leaked into log output"
    )


# ── Test 3: authorization URL — exact scopes ──────────────────────────────────


def test_authorization_url_exact_scopes():
    """build_authorization_url includes exactly the five required scopes — no extras."""
    import urllib.parse

    from youtube.oauth import build_authorization_url

    url = build_authorization_url(state="test-state")
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)

    # parse_qs returns lists; scope is a single space-separated string.
    raw_scope = qs["scope"][0]
    actual_scopes = set(raw_scope.split())

    expected_scopes = {
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
        "https://www.googleapis.com/auth/youtube.readonly",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
    }

    assert actual_scopes == expected_scopes, (
        f"Scope set mismatch.\nExpected: {expected_scopes}\nGot:      {actual_scopes}"
    )

    # Explicitly assert the write scope is absent — critical compliance boundary.
    assert "youtube.upload" not in raw_scope, (
        "youtube.upload must NOT appear in the authorization scope"
    )


# ── Test 4: authorization URL forces consent + offline access ─────────────────


def test_authorization_url_forces_consent_for_refresh_token():
    """build_authorization_url must set prompt=consent and access_type=offline."""
    import urllib.parse

    from youtube.oauth import build_authorization_url

    url = build_authorization_url(state="test-state")
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)

    # access_type=offline is required for Google to issue a refresh_token.
    assert qs.get("access_type") == ["offline"], (
        f"Expected access_type=offline, got {qs.get('access_type')}"
    )

    # prompt=consent forces re-issuance of the refresh_token even on reconnect.
    assert qs.get("prompt") == ["consent"], f"Expected prompt=consent, got {qs.get('prompt')}"

    # state must round-trip through the URL unchanged.
    assert qs.get("state") == ["test-state"], f"Expected state=test-state, got {qs.get('state')}"
