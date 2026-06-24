"""
Auth test suite.

Unit tests (no DB, no network — always pass):
  JWT helpers, URL builder, /auth/login redirect,
  /auth/me without session → 401, /auth/callback invalid state → 400,
  /auth/logout cookie clearing, malformed sub → 401.

Integration tests (require docker compose up + alembic upgrade head):
  Full OAuth callback flow (Google calls mocked), /auth/me with valid session,
  token auto-refresh, cross-creator isolation.
"""

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import jwt
import pytest

from auth import SESSION_COOKIE, create_session_token, decode_session_token, get_current_creator
from config import settings
from youtube.oauth import PUBLISH_SCOPE, build_authorization_url, has_publish_scope

# ── JWT unit tests ────────────────────────────────────────────────────────────


def test_create_session_token_decodes_correctly():
    creator_id = uuid.uuid4()
    token = create_session_token(creator_id)
    payload = decode_session_token(token)
    assert payload["sub"] == str(creator_id)


def test_create_session_token_contains_expiry():
    token = create_session_token(uuid.uuid4())
    payload = decode_session_token(token)
    assert "exp" in payload
    exp = datetime.fromtimestamp(payload["exp"], tz=UTC)
    assert exp > datetime.now(UTC)


def test_decode_session_token_expired_raises():
    past = datetime.now(UTC) - timedelta(hours=2)
    payload = {"sub": str(uuid.uuid4()), "iat": past, "exp": past + timedelta(minutes=1)}
    expired_token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm="HS256")
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_session_token(expired_token)


def test_decode_session_token_invalid_signature_raises():
    token = create_session_token(uuid.uuid4())
    tampered = token[:-4] + "XXXX"
    with pytest.raises(jwt.PyJWTError):
        decode_session_token(tampered)


def test_decode_session_token_wrong_key_raises():
    payload = {
        "sub": str(uuid.uuid4()),
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(minutes=60),
    }
    token = jwt.encode(payload, "wrong_secret", algorithm="HS256")
    with pytest.raises(jwt.InvalidSignatureError):
        decode_session_token(token)


# ── OAuth URL unit tests ──────────────────────────────────────────────────────


def test_build_authorization_url_contains_client_id():
    url = build_authorization_url("test_state")
    assert settings.GOOGLE_OAUTH_CLIENT_ID in url


def test_build_authorization_url_contains_state():
    state = secrets.token_urlsafe(16)
    assert state in build_authorization_url(state)


def test_build_authorization_url_requests_offline_access():
    url = build_authorization_url("s")
    assert "offline" in url
    assert "consent" in url


def test_build_authorization_url_includes_analytics_scope():
    url = build_authorization_url("s")
    assert "yt-analytics" in url


# ── publish scope / incremental consent (Issue 194) ───────────────────────────


def test_base_authorization_url_excludes_upload_scope():
    """Minimum-necessary: the base login flow must NOT request youtube.upload."""
    url = build_authorization_url("s")
    assert "youtube.upload" not in url
    assert "include_granted_scopes" not in url


def test_publish_authorization_url_adds_upload_and_incremental_flag():
    url = build_authorization_url("s", include_publish=True)
    assert "youtube.upload" in url  # write scope requested
    assert "include_granted_scopes=true" in url  # incremental authorization
    assert "yt-analytics" in url  # base scopes still present


def test_has_publish_scope():
    assert has_publish_scope(
        f"openid {PUBLISH_SCOPE} https://www.googleapis.com/auth/youtube.readonly"
    )
    assert not has_publish_scope("openid https://www.googleapis.com/auth/youtube.readonly")
    assert not has_publish_scope("")
    assert not has_publish_scope(None)


def test_connect_publishing_redirects_to_incremental_consent(client):
    """The authed opt-in endpoint redirects into the upload-scope consent flow."""
    from main import app
    from models import Creator

    app.dependency_overrides[get_current_creator] = lambda: AsyncMock(spec=Creator)
    try:
        resp = client.get("/auth/connect-publishing", follow_redirects=False)
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert "accounts.google.com" in loc
    assert "youtube.upload" in loc and "include_granted_scopes=true" in loc
    assert "cc_oauth_state" in resp.cookies


# ── Route unit tests (no DB) ──────────────────────────────────────────────────


def test_login_returns_redirect_to_google(client):
    resp = client.get("/auth/login", follow_redirects=False)
    assert resp.status_code == 302
    assert "accounts.google.com" in resp.headers["location"]


def test_login_sets_state_cookie(client):
    resp = client.get("/auth/login", follow_redirects=False)
    assert "cc_oauth_state" in resp.cookies


def test_me_without_session_returns_401(client):
    resp = client.get("/auth/me")
    assert resp.status_code == 401


def test_me_with_invalid_token_returns_401(client):
    client.cookies.set(SESSION_COOKIE, "not.a.valid.jwt")
    resp = client.get("/auth/me")
    client.cookies.clear()
    assert resp.status_code == 401


def test_logout_clears_session_cookie(client):
    resp = client.post("/auth/logout")
    assert resp.status_code == 200
    # Cookie cleared: either absent or max-age=0
    if SESSION_COOKIE in resp.cookies:
        assert resp.cookies[SESSION_COOKIE] == ""


def test_callback_with_no_state_cookie_returns_400(client):
    resp = client.get("/auth/callback?code=abc&state=xyz", follow_redirects=False)
    assert resp.status_code == 400


def test_callback_with_mismatched_state_returns_400(client):
    client.cookies.set("cc_oauth_state", "wrong_state")
    resp = client.get("/auth/callback?code=abc&state=right_state", follow_redirects=False)
    client.cookies.clear()
    assert resp.status_code == 400


def test_callback_with_google_error_returns_400(client):
    client.cookies.set("cc_oauth_state", "some_state")
    resp = client.get("/auth/callback?error=access_denied", follow_redirects=False)
    client.cookies.clear()
    assert resp.status_code == 400


# ── Malformed sub in JWT payload → 401 (not 500) ─────────────────────────────


def test_me_with_non_uuid_sub_returns_401(client):
    """A valid JWT whose 'sub' is not a UUID must return 401, not 500."""
    payload = {
        "sub": "not-a-uuid",
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(minutes=60),
    }
    token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm="HS256")
    resp = client.get("/auth/me", cookies={SESSION_COOKIE: token})
    assert resp.status_code == 401


def test_me_with_missing_sub_returns_401(client):
    """A valid JWT whose payload lacks the 'sub' key must return 401, not 500."""
    payload = {
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(minutes=60),
    }
    token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm="HS256")
    resp = client.get("/auth/me", cookies={SESSION_COOKIE: token})
    assert resp.status_code == 401


# ── DELETE /me rate limit ─────────────────────────────────────────────────────


def test_delete_me_has_5_per_hour_limit():
    """DELETE /me must carry a 5/hour rate limit."""
    from limiter import limiter

    limits = limiter._route_limits.get("routers.auth.delete_account", [])
    assert any("5" in str(lim.limit) and "hour" in str(lim.limit).lower() for lim in limits), (
        f"Expected 5/hour on delete_account, got: {limits}"
    )


# ── Issue 215: post-OAuth redirect routing (unit — no DB) ────────────────────


def _oauth_callback_mocks(monkeypatch: pytest.MonkeyPatch, *, is_new: bool) -> None:
    """Patch every external call made by the OAuth callback.

    Eliminates DB, Redis, Celery, and network dependencies so these tests run
    without any backing services.  The ``is_new`` parameter controls whether the
    upserted creator is treated as first-ever login (True) or a returning creator
    (False) — the only variable that determines the redirect destination.
    """
    from unittest.mock import AsyncMock, MagicMock

    # Fake Creator ORM row (no DB access required)
    fake_creator = MagicMock()
    fake_creator.id = uuid.uuid4()
    fake_creator.trial_ends_at = None

    # Fake session: synchronous methods (add, flush arg) stay sync; only awaitable
    # methods need AsyncMock.  This avoids the "coroutine was never awaited" warning
    # that arises when AsyncMock wraps SQLAlchemy's synchronous `session.add()`.
    fake_session = MagicMock()
    fake_session.flush = AsyncMock()
    fake_session.commit = AsyncMock()

    monkeypatch.setattr(
        "routers.auth.exchange_code",
        AsyncMock(
            return_value={
                "access_token": "fake_at",
                "refresh_token": "fake_rt",
                "scope": "https://www.googleapis.com/auth/youtube.readonly",
                "expires_in": 3600,
            }
        ),
    )
    monkeypatch.setattr(
        "routers.auth.fetch_creator_identity",
        AsyncMock(
            return_value={
                "google_sub": f"sub_{uuid.uuid4().hex}",
                "email": "test@example.com",
                "channel_id": "UC_test",
                "channel_title": "Test Channel",
            }
        ),
    )
    monkeypatch.setattr(
        "routers.auth.upsert_creator", AsyncMock(return_value=(fake_creator, is_new))
    )
    monkeypatch.setattr("routers.auth.store_or_update_tokens", AsyncMock(return_value=None))

    # grant_minutes is imported lazily inside the is_new block:
    #   `from billing.ledger import grant_minutes`
    # Patch the module-level name so the local import picks up the stub.
    monkeypatch.setattr("billing.ledger.grant_minutes", AsyncMock(return_value=None))

    # Celery catalog-sync + Redis ownership stamp (only exercised on is_new).
    # These are also lazy imports inside the is_new block; patch the canonical
    # module paths rather than `routers.auth.*`.
    fake_task = MagicMock()
    fake_task.id = "fake-task-id"
    monkeypatch.setattr(
        "worker.tasks.sync_channel_catalog",
        MagicMock(delay=MagicMock(return_value=fake_task)),
    )
    monkeypatch.setattr("worker.progress.aset_owner", AsyncMock())

    # log_event is a fire-and-forget side-effect — let it run (it only writes to
    # structlog / stdout, no external calls).

    # DB session flush/commit are async no-ops on the mocked session; but the
    # FastAPI dependency injects the real get_session.  Override it with a
    # session that won't attempt a Postgres connection.
    from db import get_session
    from main import app

    async def _noop_session():
        yield fake_session

    app.dependency_overrides[get_session] = _noop_session


def test_callback_new_creator_redirects_to_walkthrough(client, monkeypatch):
    """Issue 100: a first-ever login (is_new=True) lands on the walkthrough first
    (which then routes to /app/onboarding). Refines Issue 215's redirect target."""
    from db import get_session
    from main import app

    _oauth_callback_mocks(monkeypatch, is_new=True)
    state = secrets.token_urlsafe(16)
    try:
        resp = client.get(
            f"/auth/callback?code=test_code&state={state}",
            cookies={"cc_oauth_state": state},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 302, f"Expected 302, got {resp.status_code}: {resp.text}"
    assert resp.headers["location"] == "/app/walkthrough", (
        f"New creator must land on /app/walkthrough, got: {resp.headers['location']}"
    )
    assert SESSION_COOKIE in resp.cookies, "Session cookie must be set on successful callback"


def test_callback_returning_creator_redirects_to_dashboard(client, monkeypatch):
    """Issue 215: a returning creator (is_new=False) must redirect to /app/dashboard."""
    from db import get_session
    from main import app

    _oauth_callback_mocks(monkeypatch, is_new=False)
    state = secrets.token_urlsafe(16)
    try:
        resp = client.get(
            f"/auth/callback?code=test_code&state={state}",
            cookies={"cc_oauth_state": state},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 302, f"Expected 302, got {resp.status_code}: {resp.text}"
    assert resp.headers["location"] == "/app/dashboard", (
        f"Returning creator must land on /app/dashboard, got: {resp.headers['location']}"
    )
    assert SESSION_COOKIE in resp.cookies, "Session cookie must be set on successful callback"


# ── Issue 300: COPPA minimum-age column + recording path ─────────────────────


def test_creator_model_has_minimum_age_confirmed_at_column():
    """Creator ORM model must declare the minimum_age_confirmed_at column (Issue 300)."""
    from models import Creator

    col = Creator.__table__.columns.get("minimum_age_confirmed_at")
    assert col is not None, "minimum_age_confirmed_at column missing from creators table"
    assert col.nullable, "minimum_age_confirmed_at must be nullable (backward-compat)"


def test_callback_new_creator_records_age_attestation(client, monkeypatch):
    """Issue 300: minimum_age_confirmed_at is stamped in the is_new block.

    Uses the same _oauth_callback_mocks helper as the Issue 215 redirect tests.
    After a successful callback for a new creator, the fake_creator MagicMock
    should have had minimum_age_confirmed_at set to a non-None datetime value.
    MagicMock records attribute writes transparently; we assert via getattr.
    """
    import datetime

    from db import get_session
    from main import app

    _oauth_callback_mocks(monkeypatch, is_new=True)
    # Reach into the patched upsert_creator to get the fake_creator reference
    # so we can inspect what was set on it.  We do this by replacing the mock
    # with one that also captures the creator object it returns.
    from unittest.mock import AsyncMock, MagicMock

    captured: dict = {}

    real_fake_creator = MagicMock()
    real_fake_creator.id = uuid.uuid4()
    real_fake_creator.trial_ends_at = None
    captured["creator"] = real_fake_creator

    monkeypatch.setattr(
        "routers.auth.upsert_creator",
        AsyncMock(return_value=(real_fake_creator, True)),
    )

    state = secrets.token_urlsafe(16)
    try:
        resp = client.get(
            f"/auth/callback?code=code&state={state}",
            cookies={"cc_oauth_state": state},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 302, f"Expected 302, got {resp.status_code}: {resp.text}"
    # MagicMock records attribute assignments; minimum_age_confirmed_at must
    # have been set (not simply accessed, which MagicMock auto-creates as a new mock).
    creator = captured["creator"]
    ts = creator.minimum_age_confirmed_at
    # If the attribute was set to a real datetime, it will not be a MagicMock child.
    assert not isinstance(ts, MagicMock), (
        "minimum_age_confirmed_at was never assigned — still a MagicMock child"
    )
    assert isinstance(ts, datetime.datetime), (
        f"minimum_age_confirmed_at must be a datetime, got {type(ts)}"
    )


# ── Integration tests (require running DB) ────────────────────────────────────


@pytest.mark.integration
def test_callback_creates_creator_and_session(client, mocker):
    """Full OAuth flow: mocked Google APIs, real DB write."""
    state = secrets.token_urlsafe(16)
    test_sub = f"test_sub_{uuid.uuid4().hex}"

    mocker.patch(
        "routers.auth.exchange_code",
        new=AsyncMock(
            return_value={
                "access_token": "test_access_token",
                "refresh_token": "test_refresh_token",
                "scope": "https://www.googleapis.com/auth/youtube.readonly",
                "expires_in": 3600,
            }
        ),
    )
    mocker.patch(
        "routers.auth.fetch_creator_identity",
        new=AsyncMock(
            return_value={
                "google_sub": test_sub,
                "email": "creator@example.com",
                "channel_id": "UC_test_channel",
                "channel_title": "Test Channel",
            }
        ),
    )

    resp = client.get(
        f"/auth/callback?code=test_code&state={state}",
        cookies={"cc_oauth_state": state},
        follow_redirects=False,
    )

    assert resp.status_code == 302
    assert SESSION_COOKIE in resp.cookies
    session_token = resp.cookies[SESSION_COOKIE]
    payload = decode_session_token(session_token)
    assert "sub" in payload


@pytest.mark.integration
def test_me_returns_creator_for_valid_session(client, mocker):
    """GET /auth/me returns creator data when a valid session cookie is present."""
    state = secrets.token_urlsafe(16)
    test_sub = f"test_sub_{uuid.uuid4().hex}"

    mocker.patch(
        "routers.auth.exchange_code",
        new=AsyncMock(
            return_value={
                "access_token": "ta",
                "refresh_token": "tr",
                "scope": "youtube.readonly",
                "expires_in": 3600,
            }
        ),
    )
    mocker.patch(
        "routers.auth.fetch_creator_identity",
        new=AsyncMock(
            return_value={
                "google_sub": test_sub,
                "email": "me@example.com",
                "channel_id": "UC_me",
                "channel_title": "My Channel",
            }
        ),
    )

    # Complete OAuth to get a session token
    cb = client.get(
        f"/auth/callback?code=c&state={state}",
        cookies={"cc_oauth_state": state},
        follow_redirects=False,
    )
    assert cb.status_code == 302
    session_token = cb.cookies[SESSION_COOKIE]

    # Use it
    me = client.get("/auth/me", cookies={SESSION_COOKIE: session_token})
    assert me.status_code == 200
    data = me.json()
    assert data["channel_id"] == "UC_me"
    assert data["channel_title"] == "My Channel"
    assert data["email"] == "me@example.com"


# ── Issue 55: stale JWT for deleted creator → 401 not 500 ────────────────────


def test_get_current_creator_404_when_creator_deleted_after_token_issuance(client):
    """A valid JWT whose creator no longer exists in DB must return 401, not 500."""
    from unittest.mock import AsyncMock, MagicMock

    from db import get_session
    from main import app

    ghost_creator_id = uuid.uuid4()
    token = create_session_token(ghost_creator_id)

    # Mock the DB session so the creator lookup returns None (deleted creator).
    async def _empty_session():
        session = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result)
        yield session

    app.dependency_overrides[get_session] = _empty_session
    try:
        resp = client.get("/auth/me", cookies={SESSION_COOKIE: token})
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 401, (
        f"Expected 401 for deleted creator, got {resp.status_code}: {resp.text}"
    )


@pytest.mark.integration
def test_cross_creator_session_isolation(client, mocker):
    """Two creators can only access their own /auth/me data."""
    for sub, channel in [("sub_a", "UC_a"), ("sub_b", "UC_b")]:
        state = secrets.token_urlsafe(16)
        mocker.patch(
            "routers.auth.exchange_code",
            new=AsyncMock(
                return_value={
                    "access_token": "ta",
                    "refresh_token": "tr",
                    "scope": "y",
                    "expires_in": 3600,
                }
            ),
        )
        mocker.patch(
            "routers.auth.fetch_creator_identity",
            new=AsyncMock(
                return_value={
                    "google_sub": f"test_{sub}_{uuid.uuid4().hex}",
                    "email": None,
                    "channel_id": channel,
                    "channel_title": channel,
                }
            ),
        )
        cb = client.get(
            f"/auth/callback?code=c&state={state}",
            cookies={"cc_oauth_state": state},
            follow_redirects=False,
        )
        assert cb.status_code == 302
        token = cb.cookies[SESSION_COOKIE]

        me = client.get("/auth/me", cookies={SESSION_COOKIE: token})
        assert me.status_code == 200
        assert me.json()["channel_id"] == channel  # only their own channel


# ── Issue 103: Redis fail-open on refresh-lock acquisition ───────────────────


@pytest.mark.asyncio
async def test_oauth_get_valid_access_token_fails_open_on_redis_error(monkeypatch):
    """When Redis raises RedisError during lock acquisition, get_valid_access_token
    must NOT propagate a 500 — it logs a warning and proceeds with the refresh as if
    it acquired the lock (fail-open / circuit-breaker pattern for idempotent backends).
    """
    import uuid
    from datetime import UTC, datetime, timedelta
    from unittest.mock import AsyncMock, MagicMock

    import redis.asyncio as aioredis

    from youtube.oauth import get_valid_access_token

    creator_id = uuid.uuid4()

    # Token row that is near-expiry so the refresh path is triggered.
    mock_row = MagicMock()
    mock_row.expires_at = datetime.now(UTC) + timedelta(minutes=1)  # < 5 min → refresh
    mock_row.access_token_encrypted = b"encrypted"
    mock_row.refresh_token_encrypted = b"encrypted_refresh"

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_row

    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    # Redis.set raises ConnectionError (a subclass of RedisError).
    # Redis.eval is the Lua compare-and-delete that runs in the finally block;
    # it must also be an AsyncMock so the await does not TypeError.
    mock_redis = MagicMock()
    mock_redis.set = AsyncMock(side_effect=aioredis.ConnectionError("broker down"))
    mock_redis.eval = AsyncMock(return_value=0)  # returns 0 when lock not owned (no-op)

    # _do_token_refresh succeeds — returns a fresh access token.
    new_token = "fresh_access_token"
    monkeypatch.setattr(
        "youtube.oauth.get_redis_client",
        lambda: mock_redis,
    )
    monkeypatch.setattr(
        "youtube.oauth._do_token_refresh",
        AsyncMock(return_value=new_token),
    )

    result = await get_valid_access_token(creator_id, mock_session)

    # Refresh must succeed despite the Redis outage.
    assert result == new_token
    # Redis.set was called — the error came from the attempt, not a short-circuit.
    mock_redis.set.assert_called_once()
