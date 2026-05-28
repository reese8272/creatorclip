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

from auth import SESSION_COOKIE, create_session_token, decode_session_token
from config import settings
from youtube.oauth import build_authorization_url

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
