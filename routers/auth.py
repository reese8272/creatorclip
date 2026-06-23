import asyncio
import contextlib
import logging
import secrets

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import SESSION_COOKIE, create_session_token, get_current_creator
from config import settings
from db import get_session
from dna.onboarding import resolve_setup_step
from limiter import creator_key, limiter
from models import Creator, YoutubeToken, append_audit
from routers._schemas import SetupStepOut
from youtube.oauth import (
    build_authorization_url,
    exchange_code,
    fetch_creator_identity,
    has_publish_scope,
    store_or_update_tokens,
    upsert_creator,
)

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

_STATE_COOKIE = "cc_oauth_state"
_SECURE = settings.ENV == "production"


class LogoutOut(BaseModel):
    status: str


class AuthMeOut(BaseModel):
    id: str
    channel_id: str | None
    channel_title: str | None
    email: str | None
    onboarding_state: str
    can_publish: bool = False  # youtube.upload granted (Issue 194)
    # 2026-06-08 — nested aggregate so auth.js can stash window.__SETUP__
    # on every page load. Replaces the old polling of /data-gate + /dna
    # + /videos to infer the next-step CTA.
    setup: SetupStepOut


@router.get("/login")
async def login() -> RedirectResponse:
    state = secrets.token_urlsafe(32)
    resp = RedirectResponse(url=build_authorization_url(state), status_code=302)
    resp.set_cookie(
        _STATE_COOKIE,
        state,
        httponly=True,
        samesite="lax",
        secure=_SECURE,
        max_age=600,
    )
    return resp


@router.get("/connect-publishing")
async def connect_publishing(
    creator: Creator = Depends(get_current_creator),
) -> RedirectResponse:
    """Start incremental consent for the ``youtube.upload`` write scope (Issue 194).

    Authenticated opt-in only — the write scope is never in the base login flow
    (minimum-necessary, COMPLIANCE §1). The shared ``/callback`` stores the
    broadened grant; ``/me``'s ``can_publish`` then reflects it. Going live still
    depends on Google verification + the YouTube API compliance audit.
    """
    state = secrets.token_urlsafe(32)
    resp = RedirectResponse(
        url=build_authorization_url(state, include_publish=True), status_code=302
    )
    resp.set_cookie(
        _STATE_COOKIE,
        state,
        httponly=True,
        samesite="lax",
        secure=_SECURE,
        max_age=600,
    )
    return resp


@router.get("/callback")
async def callback(
    request: Request,
    session: AsyncSession = Depends(get_session),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    if error:
        logger.warning("OAuth error from Google: %s", error)
        raise HTTPException(400, f"Google OAuth error: {error}")

    stored_state = request.cookies.get(_STATE_COOKIE)
    if not stored_state or stored_state != state:
        raise HTTPException(400, "Invalid OAuth state — possible CSRF attempt")

    if not code:
        raise HTTPException(400, "Missing authorization code")

    tokens = await exchange_code(code)
    identity = await fetch_creator_identity(tokens["access_token"])

    creator, is_new = await upsert_creator(
        session,
        google_sub=identity["google_sub"],
        email=identity.get("email"),
        channel_id=identity.get("channel_id"),
        channel_title=identity.get("channel_title"),
    )
    await session.flush()

    if is_new:
        from datetime import UTC, datetime, timedelta

        from billing.ledger import grant_minutes

        await grant_minutes(
            creator.id,
            settings.FREE_TRIAL_MINUTES,
            "trial",
            session,
            pack_id="trial",
        )
        # Issue 126 — stamp trial_ends_at in the same transaction that grants
        # the trial minutes so the two states can never disagree. NULL means
        # "no trial active" for legacy creators that predate this column.
        creator.trial_ends_at = datetime.now(UTC) + timedelta(days=settings.TRIAL_DURATION_DAYS)
        session.add(creator)
        logger.info(
            "trial granted creator=%s minutes=%d trial_ends_at=%s",
            creator.id,
            settings.FREE_TRIAL_MINUTES,
            creator.trial_ends_at.isoformat(),
        )

    await store_or_update_tokens(
        session,
        creator.id,
        access_token=tokens["access_token"],
        refresh_token=tokens.get("refresh_token"),
        scope=tokens.get("scope", ""),
        expires_in=tokens.get("expires_in", 3600),
    )
    await session.commit()

    # Kick off the initial catalog pull so the user's videos land in the DB
    # by the time they reach the onboarding data-gate. Async because the
    # playlistItems + per-video duration fan-out can take >10s on large
    # channels — far longer than the OAuth redirect budget. (Issue 87)
    if is_new:
        import redis as _redis_pkg

        from worker import progress
        from worker.tasks import sync_channel_catalog

        task = await asyncio.to_thread(sync_channel_catalog.delay, str(creator.id))
        # Wave-3 Fix D: stamp ownership so the catalog-sync SSE stream
        # (added in Issue 92) is reachable when Issue 100's onboarding
        # tutorial wires the post-OAuth UI. Same fail-open posture as the
        # other Issue-92 routers — a Redis blip during onboarding logs
        # and continues; the catalog sync itself still runs.
        try:
            await progress.aset_owner(task.id, str(creator.id))
        except _redis_pkg.RedisError as exc:
            logger.warning(
                "auth callback aset_owner failed (Redis down?) task=%s err=%s",
                task.id,
                exc,
            )

    from observability import log_event

    log_event(
        "auth_callback_completed",
        creator_id=str(creator.id),
        is_new=is_new,
        channel_id=identity.get("channel_id"),
    )

    session_token = create_session_token(creator.id)
    resp = RedirectResponse(url="/", status_code=302)
    resp.delete_cookie(_STATE_COOKIE)
    resp.set_cookie(
        SESSION_COOKIE,
        session_token,
        httponly=True,
        samesite="lax",
        secure=_SECURE,
        max_age=settings.JWT_EXPIRY_MINUTES * 60,
    )
    return resp


@router.post("/logout", response_model=LogoutOut)
@limiter.limit("30/minute", key_func=creator_key)
async def logout(request: Request, response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE)
    return {"status": "logged out"}


@router.get("/me", response_model=AuthMeOut)
@limiter.limit("120/minute", key_func=creator_key)
async def me(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Returns the authenticated creator's profile. No virality predictions made here."""
    setup = await resolve_setup_step(creator, session)
    token = (
        await session.execute(select(YoutubeToken).where(YoutubeToken.creator_id == creator.id))
    ).scalar_one_or_none()
    return {
        "id": str(creator.id),
        "channel_id": creator.channel_id,
        "channel_title": creator.channel_title,
        "email": creator.email,
        "onboarding_state": creator.onboarding_state.value,
        "can_publish": has_publish_scope(token.scope if token else None),
        "setup": setup,
    }


async def erase_creator(session: AsyncSession, creator: Creator) -> None:
    """Right-to-erasure helper: revoke OAuth, purge media, delete all creator data.

    Extracted from ``delete_account`` (Issue 250) so a future inactive-account
    sweep (requires [DEC] sign-off — see docs/DECISIONS.md) can reuse this
    logic without duplication. The session is owned by the caller; this helper
    commits once at the end.

    Error posture: OAuth revocation and storage/telemetry purges are best-effort
    and never abort the erasure. Only the DB cascade-delete is authoritative.
    """
    creator_id = creator.id

    # Revoke Google OAuth refresh token — best effort, don't abort on failure.
    # Revoking the refresh token also invalidates any access tokens issued from it
    # (per Google OAuth 2.0 docs); a 400 with invalid_token / token_revoked means
    # the grant is already gone, which is success for our purposes.
    try:
        from sqlalchemy import select

        from crypto import decrypt
        from models import YoutubeToken

        token_row = (
            await session.execute(select(YoutubeToken).where(YoutubeToken.creator_id == creator_id))
        ).scalar_one_or_none()
        if token_row:
            refresh_token = decrypt(token_row.refresh_token_encrypted)
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    "https://oauth2.googleapis.com/revoke",
                    params={"token": refresh_token},
                )
            if resp.status_code == 400:
                body = {}
                with contextlib.suppress(ValueError):
                    body = resp.json()
                err = body.get("error", "")
                if err not in ("invalid_token", "token_revoked"):
                    logger.warning(
                        "OAuth revocation 400 for creator %s with unexpected error=%r",
                        creator_id,
                        err,
                    )
            elif resp.status_code >= 400:
                logger.warning(
                    "OAuth revocation HTTP %s for creator %s",
                    resp.status_code,
                    creator_id,
                )
    except Exception as exc:
        logger.warning("OAuth revocation failed for creator %s: %s", creator_id, exc)

    # Purge all stored media for this creator
    from worker.storage import delete_prefix

    for prefix in (f"source/{creator_id}/", f"clips/{creator_id}/"):
        try:
            # Offload the paginated boto3 list+delete off the event loop. (Issue 67)
            n = await asyncio.to_thread(delete_prefix, prefix)
            if n:
                logger.info("Purged %d object(s) at %s", n, prefix)
        except Exception as exc:
            logger.warning("Storage purge failed for %s: %s", prefix, exc)

    # Purge telemetry on the separate logs engine (Issue 248). event_logs has no
    # FK to creators, so the DB cascade below can't reach it — delete explicitly.
    # Best-effort: a failure must not abort the erasure (mirrors the media purge).
    try:
        from event_log import purge_creator_events

        n = await purge_creator_events(creator_id)
        if n > 0:
            logger.info("Purged %d event_logs row(s) for creator %s", n, creator_id)
    except Exception as exc:
        logger.warning("event_logs purge failed for creator %s: %s", creator_id, exc)

    # Audit the deletion WITHOUT the creator's PII (Issue 247). `audit_log` is
    # never purged and RLS-exempt, so writing email/channel_id here would let
    # erased personal data survive the erasure (GDPR Art. 17 — EDPB CEF 2025).
    # The internal `creator_id` is sufficient evidence-of-erasure: once the
    # creator row is deleted the UUID no longer maps to a person.
    await append_audit(
        session,
        action="creator.deleted",
        actor=str(creator_id),
        entity_type="creator",
        entity_id=creator_id,
    )

    # Cascade delete — DB FKs with ON DELETE CASCADE handle all child rows
    await session.delete(creator)
    await session.commit()

    logger.info("Account deleted for creator %s", creator_id)


@router.delete("/me", status_code=204)
@limiter.limit("5/hour", key_func=creator_key)
async def delete_account(
    request: Request,
    response: Response,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Right-to-erasure: revoke OAuth, purge media, cascade-delete all creator data."""
    await erase_creator(session, creator)
    response.delete_cookie(SESSION_COOKIE)
