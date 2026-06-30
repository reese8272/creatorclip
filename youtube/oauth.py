"""
YouTube OAuth 2.0 flow and token management.

All external HTTP calls go through private _call_* helpers so they can be patched
in tests without touching real Google endpoints. Never call Google URLs directly
outside this module.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
import redis.asyncio as aioredis
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from crypto import decrypt, encrypt
from models import Creator, OnboardingState, YoutubeToken
from youtube import _http
from youtube._redis import get_redis_client
from youtube.data_api import clamp_ingest_field

logger = logging.getLogger(__name__)

# ── Refresh-lock constants ─────────────────────────────────────────────────────

_LOCK_TTL_S = 10  # seconds — covers one Google token-refresh round trip
_LOCK_RETRY_COUNT = 3
_LOCK_RETRY_SLEEP_S = 0.2  # 200 ms between retries

# Canonical Lua compare-and-delete: only release the lock if it is still ours.
# Returns 1 on successful delete, 0 if the key has already expired or was
# taken by another worker (our TTL lapsed mid-flight).
_LUA_RELEASE_LOCK = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

# Write scope for publishing (Issue 194). NOT in the base login SCOPES —
# minimum-necessary (COMPLIANCE §1): requested only when a creator opts into
# publishing, via incremental authorization (`include_granted_scopes=true`).
# Going live with uploads requires Google OAuth verification + the YouTube API
# compliance audit (launch dependency — see docs/DECISIONS.md 2026-06-22).
PUBLISH_SCOPE = "https://www.googleapis.com/auth/youtube.upload"


def has_publish_scope(scope: str | None) -> bool:
    """True when a stored grant includes the youtube.upload write scope. The
    scope string is the single source of truth for 'publishing enabled' — no
    separate column (Issue 194)."""
    return scope is not None and PUBLISH_SCOPE in scope.split()


_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v2/userinfo"
_CHANNELS_ENDPOINT = "https://www.googleapis.com/youtube/v3/channels"


# ── URL builder ───────────────────────────────────────────────────────────────


def build_authorization_url(state: str, *, include_publish: bool = False) -> str:
    # Incremental authorization (Issue 194): the publish opt-in requests the write
    # scope on top of whatever the creator already granted. `include_granted_scopes`
    # tells Google to re-present the prior grant so we keep one combined token
    # instead of juggling two. Base login leaves `include_publish=False` → read-only.
    scopes = [*SCOPES, PUBLISH_SCOPE] if include_publish else SCOPES
    params = {
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": settings.OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        # `consent`        — always re-issue a refresh_token, even on reconnect.
        # `select_account` — force Google's account picker so a user who just
        #                    logged out actually sees a sign-in surface rather
        #                    than being silently re-authenticated into the
        #                    same Google session. (Assessment 2026-06-08 fix.)
        "prompt": "consent select_account",
        "state": state,
    }
    if include_publish:
        params["include_granted_scopes"] = "true"
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)


# ── Mockable HTTP helpers ─────────────────────────────────────────────────────


def _is_invalid_grant(response: httpx.Response) -> bool:
    """Detect Google's `{"error": "invalid_grant"}` payload on a 400 token response."""
    try:
        return response.json().get("error") == "invalid_grant"
    except ValueError:
        return False


async def _call_token_endpoint(data: dict) -> dict:
    # Shared timeout-bounded client — token refresh is on the hot path of every
    # near-expiry request; a per-call client (no timeout) could hang it. (Issue 72)
    resp = await _http.client().post(_TOKEN_ENDPOINT, data=data)
    resp.raise_for_status()
    return resp.json()


async def _call_userinfo(access_token: str) -> dict:
    resp = await _http.client().get(
        _USERINFO_ENDPOINT,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    resp.raise_for_status()
    return resp.json()


async def _call_youtube_channels(access_token: str) -> dict:
    resp = await _http.client().get(
        _CHANNELS_ENDPOINT,
        params={"part": "snippet", "mine": "true"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    resp.raise_for_status()
    return resp.json()


# ── Public OAuth operations ───────────────────────────────────────────────────


async def exchange_code(code: str) -> dict:
    return await _call_token_endpoint(
        {
            "code": code,
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
            "redirect_uri": settings.OAUTH_REDIRECT_URI,
            "grant_type": "authorization_code",
        }
    )


async def refresh_access_token(refresh_token: str) -> dict:
    return await _call_token_endpoint(
        {
            "refresh_token": refresh_token,
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
            "grant_type": "refresh_token",
        }
    )


async def fetch_creator_identity(access_token: str) -> dict:
    """Returns {google_sub, email, channel_id, channel_title}."""
    user_info, channels = (
        await _call_userinfo(access_token),
        await _call_youtube_channels(access_token),
    )
    channel = (channels.get("items") or [{}])[0]
    # Issue 340c: clamp channel_title at ingest boundary so adversarially-crafted
    # YouTube channel names cannot act as prompt-injection payloads in LLM calls.
    raw_channel_title: str | None = channel.get("snippet", {}).get("title")
    return {
        "google_sub": user_info["id"],
        "email": user_info.get("email"),
        "channel_id": channel.get("id"),
        "channel_title": clamp_ingest_field(
            raw_channel_title, settings.MAX_INGESTED_CHANNEL_TITLE_CHARS
        ),
    }


# ── DB helpers ────────────────────────────────────────────────────────────────


async def upsert_creator(
    session: AsyncSession,
    *,
    google_sub: str,
    email: str | None,
    channel_id: str | None,
    channel_title: str | None,
) -> tuple[Creator, bool]:
    result = await session.execute(select(Creator).where(Creator.google_sub == google_sub))
    creator = result.scalar_one_or_none()
    is_new = creator is None
    if creator is None:
        creator = Creator(
            google_sub=google_sub,
            email=email,
            channel_id=channel_id,
            channel_title=channel_title,
            onboarding_state=OnboardingState.connected,
        )
        session.add(creator)
    else:
        creator.email = email
        creator.channel_id = channel_id
        creator.channel_title = channel_title
    return creator, is_new


async def store_or_update_tokens(
    session: AsyncSession,
    creator_id: uuid.UUID,
    *,
    access_token: str,
    refresh_token: str | None,
    scope: str,
    expires_in: int,
) -> None:
    expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
    result = await session.execute(
        select(YoutubeToken).where(YoutubeToken.creator_id == creator_id)
    )
    row = result.scalar_one_or_none()

    if row is None:
        if not refresh_token:
            raise HTTPException(400, "Google did not return a refresh token — please reconnect")
        session.add(
            YoutubeToken(
                creator_id=creator_id,
                access_token_encrypted=encrypt(access_token),
                refresh_token_encrypted=encrypt(refresh_token),
                scope=scope,
                expires_at=expires_at,
            )
        )
    else:
        row.access_token_encrypted = encrypt(access_token)
        if refresh_token:
            row.refresh_token_encrypted = encrypt(refresh_token)
        row.scope = scope
        row.expires_at = expires_at
        row.updated_at = datetime.now(UTC)


async def _do_token_refresh(
    creator_id: uuid.UUID,
    session: AsyncSession,
    row: YoutubeToken,
) -> str:
    """Perform the actual Google token refresh + DB commit.

    Called only by the worker that holds the per-creator Redis advisory lock.
    Returns the new plaintext access token.

    Audit fix (Issue-135 audit, scale-checklist D): writes happen on an
    INTERNAL ``AdminSessionLocal`` scoped to this function, NOT the
    caller-owned ``session``. Previously, calling ``session.commit()`` here
    flushed every pending write in the caller's transaction (request handler
    or Celery task) — silently committing unrelated work. The caller-owned
    ``session`` is now read-only from this function's perspective.
    """
    from db import AdminSessionLocal

    stored_refresh = decrypt(row.refresh_token_encrypted)
    try:
        new_tokens = await refresh_access_token(stored_refresh)
    except httpx.HTTPStatusError as exc:
        # Per OAuth 2.0 RFC 6749 §5.2, invalid_grant is permanent — the refresh
        # token has been revoked, expired (6mo unused), or invalidated by a
        # password reset. Discard the row so we stop wasting quota retrying.
        if exc.response.status_code == 400 and _is_invalid_grant(exc.response):
            logger.warning(
                "Refresh token invalid_grant for creator %s — deleting YoutubeToken row",
                creator_id,
            )
            async with AdminSessionLocal() as internal:
                await internal.execute(
                    delete(YoutubeToken).where(YoutubeToken.creator_id == creator_id)
                )
                await internal.commit()
        else:
            logger.warning("Token refresh failed for creator %s: %s", creator_id, exc)
        raise HTTPException(
            401, "OAuth token refresh failed — please reconnect your YouTube account"
        ) from exc

    async with AdminSessionLocal() as internal:
        await store_or_update_tokens(
            internal,
            creator_id,
            access_token=new_tokens["access_token"],
            refresh_token=new_tokens.get("refresh_token"),
            scope=new_tokens.get("scope", row.scope),
            expires_in=new_tokens.get("expires_in", 3600),
        )
        await internal.commit()
    # Refresh the caller's view of the row so subsequent reads in the same
    # transaction see the new token without re-querying.
    await session.refresh(row)
    return new_tokens["access_token"]


async def get_valid_access_token(creator_id: uuid.UUID, session: AsyncSession) -> str:
    """Return a valid access token, refreshing from Google if expiry is within 5 minutes.

    A per-creator Redis advisory lock (SET NX EX 10) prevents concurrent Celery workers
    or FastAPI requests from issuing duplicate refresh calls for the same creator.
    The lock is released with a Lua compare-and-delete to avoid deleting another
    worker's lock if our TTL expired mid-flight.

    If another worker holds the lock we sleep 200 ms and re-read the token row up to
    three times. If the row is still expired after all retries we return 503.
    """
    result = await session.execute(
        select(YoutubeToken).where(YoutubeToken.creator_id == creator_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(401, "No OAuth tokens found — please reconnect your YouTube account")

    if row.expires_at is not None and row.expires_at - datetime.now(UTC) >= timedelta(minutes=5):
        # Token is still valid — fast path, no Redis involved.
        return decrypt(row.access_token_encrypted)

    logger.info("Refreshing access token for creator %s", creator_id)

    redis_client = get_redis_client()
    lock_key = f"refresh-lock:{creator_id}"
    # A unique value lets the Lua script confirm we still own the lock before deleting it.
    lock_token = str(uuid.uuid4())

    try:
        acquired: bool = await redis_client.set(lock_key, lock_token, nx=True, ex=_LOCK_TTL_S)
    except aioredis.RedisError as exc:
        # Redis is unreachable — fail open so a broker outage does not 500 the token-refresh
        # path. Google's refresh endpoint is idempotent and the DB write is idempotent on
        # creator_id, so a lockless refresh is safe. (AWS/Netflix/Shopify circuit-breaker
        # convention for opportunistic locks guarding idempotent backends.)
        logger.warning(
            "Redis unavailable during refresh-lock acquisition for creator %s (%s) — "
            "proceeding without lock",
            creator_id,
            exc,
        )
        acquired = True

    if acquired:
        try:
            return await _do_token_refresh(creator_id, session, row)
        finally:
            # Only release if the value is still ours — Lua compare-and-delete.
            await redis_client.eval(_LUA_RELEASE_LOCK, 1, lock_key, lock_token)  # type: ignore[misc]  # SDK/stub typing lag (Issue 78c)
    else:
        # Another worker is refreshing. Poll until it finishes.
        for attempt in range(_LOCK_RETRY_COUNT):
            await asyncio.sleep(_LOCK_RETRY_SLEEP_S)
            # Re-read the row so we see whatever the lock holder committed in its own
            # session. populate_existing forces the ORM to overwrite our identity-map
            # copy from the DB — without it `expire_on_commit=False` returns the stale
            # cached instance and the waiter 503s even though a fresh token exists. (Issue A)
            fresh_result = await session.execute(
                select(YoutubeToken)
                .where(YoutubeToken.creator_id == creator_id)
                .execution_options(populate_existing=True)
            )
            fresh_row = fresh_result.scalar_one_or_none()
            if fresh_row is None:
                # The lock holder found invalid_grant and deleted the row.
                raise HTTPException(
                    401, "No OAuth tokens found — please reconnect your YouTube account"
                )
            if fresh_row.expires_at is not None and fresh_row.expires_at - datetime.now(UTC) >= timedelta(minutes=5):
                return decrypt(fresh_row.access_token_encrypted)
            logger.debug(
                "Token still expired for creator %s after retry %d/%d",
                creator_id,
                attempt + 1,
                _LOCK_RETRY_COUNT,
            )

        raise HTTPException(503, "Token refresh in progress; please retry")
