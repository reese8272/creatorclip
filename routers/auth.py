import logging
import secrets

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from auth import SESSION_COOKIE, create_session_token, get_current_creator
from config import settings
from db import get_session
from limiter import limiter
from models import Creator, append_audit
from billing.stripe_client import find_active_subscription_for_email
from youtube.oauth import (
    build_authorization_url,
    exchange_code,
    fetch_creator_identity,
    store_or_update_tokens,
    upsert_creator,
)

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

_STATE_COOKIE = "cc_oauth_state"
_SECURE = settings.ENV == "production"


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

    creator = await upsert_creator(
        session,
        google_sub=identity["google_sub"],
        email=identity.get("email"),
        channel_id=identity.get("channel_id"),
        channel_title=identity.get("channel_title"),
    )
    await session.flush()

    # Grant pro access to comped accounts (owner, beta testers, etc.)
    comped = {e.strip().lower() for e in settings.COMPED_EMAILS.split(",") if e.strip()}
    if creator.email and creator.email.lower() in comped:
        creator.plan_tier = "pro"
        creator.subscription_status = "active"
        logger.info("Comped pro access granted for creator %s", creator.id)

    # Auto-link a pre-purchase Stripe subscription if one exists for this email.
    # This runs when an early-access subscriber connects YouTube after paying.
    elif not creator.stripe_customer_id and creator.email:
        result = find_active_subscription_for_email(creator.email)
        if result:
            stripe_customer_id, plan_tier, subscription_status = result
            creator.stripe_customer_id = stripe_customer_id
            creator.plan_tier = plan_tier
            creator.subscription_status = subscription_status
            logger.info(
                "Auto-linked Stripe subscription for creator %s: tier=%s",
                creator.id,
                plan_tier,
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


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE)
    return {"status": "logged out"}


@router.get("/me")
@limiter.limit("120/minute")
async def me(request: Request, creator: Creator = Depends(get_current_creator)) -> dict:
    """Returns the authenticated creator's profile. No virality predictions made here."""
    return {
        "id": str(creator.id),
        "channel_id": creator.channel_id,
        "channel_title": creator.channel_title,
        "email": creator.email,
        "onboarding_state": creator.onboarding_state.value,
    }


@router.delete("/me", status_code=204)
async def delete_account(
    response: Response,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Right-to-erasure: revoke OAuth, purge media, cascade-delete all creator data."""
    creator_id = creator.id

    # Revoke Google OAuth token — best effort, don't abort on failure
    try:
        from sqlalchemy import select

        from crypto import decrypt
        from models import YoutubeToken

        token_row = (
            await session.execute(select(YoutubeToken).where(YoutubeToken.creator_id == creator_id))
        ).scalar_one_or_none()
        if token_row:
            access_token = decrypt(token_row.access_token_encrypted)
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    "https://oauth2.googleapis.com/revoke",
                    params={"token": access_token},
                )
    except Exception as exc:
        logger.warning("OAuth revocation failed for creator %s: %s", creator_id, exc)

    # Purge all stored media for this creator
    from worker.storage import delete_prefix

    for prefix in (f"source/{creator_id}/", f"clips/{creator_id}/"):
        try:
            n = delete_prefix(prefix)
            if n:
                logger.info("Purged %d object(s) at %s", n, prefix)
        except Exception as exc:
            logger.warning("Storage purge failed for %s: %s", prefix, exc)

    # Audit log before deletion (session still has creator in identity map)
    await append_audit(
        session,
        action="creator.deleted",
        actor=str(creator_id),
        entity_type="creator",
        entity_id=creator_id,
        before={"channel_id": creator.channel_id, "email": creator.email},
    )

    # Cascade delete — DB FKs with ON DELETE CASCADE handle all child rows
    await session.delete(creator)
    await session.commit()

    response.delete_cookie(SESSION_COOKIE)
    logger.info("Account deleted for creator %s", creator_id)
