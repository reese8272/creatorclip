"""
YouTube OAuth 2.0 flow and token management.

All external HTTP calls go through private _call_* helpers so they can be patched
in tests without touching real Google endpoints. Never call Google URLs directly
outside this module.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from crypto import decrypt, encrypt
from models import Creator, OnboardingState, YoutubeToken

logger = logging.getLogger(__name__)

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v2/userinfo"
_CHANNELS_ENDPOINT = "https://www.googleapis.com/youtube/v3/channels"


# ── URL builder ───────────────────────────────────────────────────────────────


def build_authorization_url(state: str) -> str:
    params = {
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": settings.OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",  # always return refresh_token, even on reconnect
        "state": state,
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)


# ── Mockable HTTP helpers ─────────────────────────────────────────────────────


def _is_invalid_grant(response: httpx.Response) -> bool:
    """Detect Google's `{"error": "invalid_grant"}` payload on a 400 token response."""
    try:
        return response.json().get("error") == "invalid_grant"
    except ValueError:
        return False


async def _call_token_endpoint(data: dict) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(_TOKEN_ENDPOINT, data=data)
        resp.raise_for_status()
        return resp.json()


async def _call_userinfo(access_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _USERINFO_ENDPOINT,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()


async def _call_youtube_channels(access_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
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
    return {
        "google_sub": user_info["id"],
        "email": user_info.get("email"),
        "channel_id": channel.get("id"),
        "channel_title": channel.get("snippet", {}).get("title"),
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
    if is_new:
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


async def get_valid_access_token(creator_id: uuid.UUID, session: AsyncSession) -> str:
    """Returns a valid access token, refreshing from the DB if it expires within 5 minutes."""
    result = await session.execute(
        select(YoutubeToken).where(YoutubeToken.creator_id == creator_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(401, "No OAuth tokens found — please reconnect your YouTube account")

    if row.expires_at - datetime.now(UTC) < timedelta(minutes=5):
        logger.info("Refreshing access token for creator %s", creator_id)
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
                await session.execute(
                    delete(YoutubeToken).where(YoutubeToken.creator_id == creator_id)
                )
                await session.commit()
            else:
                logger.warning("Token refresh failed for creator %s: %s", creator_id, exc)
            raise HTTPException(
                401, "OAuth token refresh failed — please reconnect your YouTube account"
            ) from exc
        await store_or_update_tokens(
            session,
            creator_id,
            access_token=new_tokens["access_token"],
            refresh_token=new_tokens.get("refresh_token"),
            scope=new_tokens.get("scope", row.scope),
            expires_in=new_tokens.get("expires_in", 3600),
        )
        await session.commit()
        return new_tokens["access_token"]

    return decrypt(row.access_token_encrypted)
