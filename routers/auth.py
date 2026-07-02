import asyncio
import contextlib
import logging
import secrets

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from auth import SESSION_COOKIE, create_session_token, get_current_creator
from config import settings
from db import get_session
from dna.onboarding import resolve_setup_step
from flags import flag_enabled
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


class SignupsPausedError(Exception):
    """Raised mid-OAuth when the ``signup`` kill switch is off and the Google
    identity has no existing Creator row. The callback maps it to a friendly
    "beta at capacity" login redirect; the uncommitted new-creator transaction
    is rolled back, so no partial signup is ever persisted. Existing creators
    are unaffected (Issue 284)."""


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
    # Issue 235 — funnel instrumentation: record that an OAuth flow was initiated.
    # creator_id is unknown at this point (pre-auth), so we omit it.
    from event_log import record_event

    asyncio.ensure_future(record_event(source="backend", event="oauth_started"))
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


async def _exchange_and_persist(session: AsyncSession, code: str) -> tuple[Creator, bool, dict]:
    """Exchange the OAuth code, upsert the creator, grant the trial + record consent
    for new creators, and persist tokens — the DB writes in one committed transaction.

    Issue 82b (pool starvation): the Google token/userinfo round-trips run FIRST,
    before any query on ``session``, so no pooled DB connection is checked out
    while waiting on Google. All session use is confined to ``_persist_oauth_grant``,
    which contains no external calls.

    Raises on any failure (``httpx`` for the Google calls, ``SQLAlchemyError`` for
    the DB write) so the caller can map it to a clean redirect rather than leak a
    500 mid-OAuth. Returns ``(creator, is_new, identity)``.
    """
    tokens = await exchange_code(code)
    identity = await fetch_creator_identity(tokens["access_token"])
    creator, is_new = await _persist_oauth_grant(session, tokens, identity)
    return creator, is_new, identity


async def _persist_oauth_grant(
    session: AsyncSession, tokens: dict, identity: dict
) -> tuple[Creator, bool]:
    """Persist the OAuth grant: upsert the creator, gate new signups on the
    ``signup`` kill switch, grant the trial + record consent for new creators,
    and store tokens — all in ONE transaction committed at the end, so a
    ``SignupsPausedError`` raised mid-way rolls the uncommitted Creator row back
    and a paused signup never persists anything (Issue 284). No external calls
    happen here — the session's pooled connection is only held for DB work.
    """
    creator, is_new = await upsert_creator(
        session,
        google_sub=identity["google_sub"],
        email=identity.get("email"),
        channel_id=identity.get("channel_id"),
        channel_title=identity.get("channel_title"),
    )

    # Kill switch (Issue 284): block NEW creator creation only. The commit is
    # at the end of this function, so raising here rolls the uncommitted
    # Creator row back. Existing creators (is_new=False) always pass.
    if is_new and not await flag_enabled("signup"):
        raise SignupsPausedError

    await session.flush()

    # Set the per-creator RLS context for the remainder of this transaction.
    # The callback runs on a bare session with no `app.creator_id` GUC (no
    # creator is authenticated yet), but the writes below (grant_minutes →
    # minute_packs/minute_deductions, store_or_update_tokens → youtube_tokens)
    # all target RLS-FORCED tenant tables (migration 0010). With the Issue 343
    # role split active the app connects as the non-BYPASSRLS `creatorclip_app`
    # role, so those inserts would fail the `tenant_isolation` WITH CHECK
    # against a NULL GUC (SQLSTATE 42501). The `creators` table is RLS-exempt,
    # so upsert_creator above succeeds and gives us the id to scope to here.
    # `is_local=true` keeps the GUC transaction-scoped (wiped on commit), exactly
    # like the `after_begin` listener in db.py does for authenticated requests.
    await session.execute(
        text("SELECT set_config('app.creator_id', :cid, true)"),
        {"cid": str(creator.id)},
    )
    # Also stamp session.info so any transaction this session begins AFTER the
    # commit below is restamped by the after_begin listener (Issue 82b).
    session.info["creator_id"] = creator.id

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

        # Issue 299 — Record the versioned consent artifact in the same
        # transaction as the creator row so the two are always consistent.
        # The affirmative checkbox on the Login page (frontend/src/pages/Login.tsx)
        # gates the OAuth CTA; reaching the callback IS evidence of affirmative
        # acceptance (the OAuth flow cannot be initiated without the checkbox).
        # We store the version strings shown at acceptance so a future re-prompt
        # path can detect material ToS/Privacy changes by comparing stored vs current.
        now_utc = datetime.now(UTC)
        creator.terms_accepted_at = now_utc
        creator.terms_version = settings.TOS_VERSION
        creator.privacy_version = settings.PRIVACY_VERSION
        logger.info(
            "consent recorded creator=%s tos_version=%s privacy_version=%s",
            creator.id,
            settings.TOS_VERSION,
            settings.PRIVACY_VERSION,
        )

        # Issue 300 — Record the COPPA 13+ minimum-age attestation in the same
        # transaction.  The "I confirm I am 13 or older" checkbox on the Login
        # page is a second affirmative gate that must be checked alongside the
        # Issue 299 consent checkbox before the OAuth CTA is active.  Reaching
        # this callback is therefore evidence that the attestation was given.
        creator.minimum_age_confirmed_at = now_utc
        logger.info("age_attestation recorded creator=%s", creator.id)

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
    return creator, is_new


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

    try:
        creator, is_new, identity = await _exchange_and_persist(session, code)
    except HTTPException:
        # Deliberate 4xx (e.g. Google returned no refresh token) — preserve it.
        raise
    except SignupsPausedError:
        # Kill switch (Issue 284): friendly "beta at capacity" redirect with a
        # stable error code — never a stack trace. No PII in the log line.
        await session.rollback()
        logger.info("signup blocked — signup kill switch is off (beta at capacity)")
        return RedirectResponse(url="/app/login?error=signup_paused", status_code=302)
    except httpx.HTTPStatusError as exc:
        # Google token-exchange / identity fetch failed (reused/expired code,
        # invalid_grant, client-secret or redirect mismatch). Map to a clean
        # login redirect instead of leaking a 500 mid-OAuth. Log the status only
        # — never the token or response body.
        await session.rollback()
        logger.warning("OAuth callback exchange failed: HTTP %s", exc.response.status_code)
        return RedirectResponse(url="/app/login?error=oauth_failed", status_code=302)
    except Exception as exc:
        # Catch-all so a DB/schema/Redis/unknown fault can never surface as a 500
        # to a user mid-login (this is what the 2026-06-24 migration-drift outage
        # did). Log the exception TYPE only — SQLAlchemy messages can carry SQL
        # params / PII, which must never hit a log line (COMPLIANCE).
        await session.rollback()
        logger.error("OAuth callback failed: %s", type(exc).__name__)
        return RedirectResponse(url="/app/login?error=oauth_failed", status_code=302)

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

        # Issue 246 — welcome lifecycle email. Fires only inside the is_new
        # branch (post-commit), so it triggers on first email set, never on a
        # re-login. entity_id = creator_id ⇒ the notification_deliveries UNIQUE
        # dedupe_key makes welcome fire exactly once ever per creator. The
        # send_notification task gates lifecycle on prefs.email_lifecycle, so an
        # opted-out creator (theoretically — defaults to on) gets none.
        # Best-effort: a broker hiccup must not break the OAuth redirect.
        from worker.tasks import send_notification

        try:
            await asyncio.to_thread(
                send_notification.delay, str(creator.id), "welcome", str(creator.id), {}
            )
        except Exception as exc:
            logger.warning(
                "auth callback welcome notification enqueue failed creator=%s err=%s",
                creator.id,
                type(exc).__name__,
            )

    from observability import log_event

    log_event(
        "auth_callback_completed",
        creator_id=str(creator.id),
        is_new=is_new,
        channel_id=identity.get("channel_id"),
    )

    # Issue 235 — route the activation-funnel event to the queryable DB sink so
    # per-cohort conversion / TTV queries can be computed.  The existing
    # log_event() call above writes to the file sink only; this writes to
    # event_logs alongside it.  channel_id is omitted — the _redact() boundary
    # in event_log.py would keep it (it's not a sensitive key), but we keep
    # events minimal to creator_id + signal booleans per the taxonomy.
    from event_log import record_event

    asyncio.ensure_future(
        record_event(
            source="backend",
            event="oauth_completed",
            creator_id=creator.id,
            extra={"is_new": is_new},
        )
    )

    # Issue 215: branch on the first-login signal so new creators land on the
    # guided onboarding flow (catalog sync visibly in progress) while returning
    # creators go straight to the dashboard.  `is_new` is set by `upsert_creator`
    # when no prior Creator row existed — it is the canonical first-login signal
    # already used above to gate trial-grant and catalog-sync, so reusing it
    # here is consistent with the rest of the callback.
    if is_new:
        # Issue 100: route new creators to the "what this app does" walkthrough
        # FIRST — its 5 panels explain clips / DNA / setup-vs-payoff / the dashboard
        # status badges / why intake helps, then its "Set up my AutoClip" CTA
        # navigates to /app/onboarding. Previously Issue 215 redirected straight to
        # /app/onboarding, leaving the walkthrough orphaned (reachable only by typing
        # the URL). The funnel-entry event stays "onboarding_viewed" — the walkthrough
        # is step 1 of onboarding. Returning creators → dashboard.
        log_event("onboarding_viewed", creator_id=str(creator.id))
        redirect_url = "/app/walkthrough"
    else:
        redirect_url = "/app/dashboard"

    session_token = create_session_token(creator.id)
    resp = RedirectResponse(url=redirect_url, status_code=302)
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

    # Read + decrypt the refresh token FIRST, then end the transaction so the
    # pooled connection is NOT held across the Google revoke + R2 purge
    # round-trips below (Issue 82b). rollback (not commit) because the read may
    # have failed and left the transaction unusable; nothing needs persisting yet.
    refresh_token: str | None = None
    try:
        from sqlalchemy import select

        from crypto import decrypt
        from models import YoutubeToken

        token_row = (
            await session.execute(select(YoutubeToken).where(YoutubeToken.creator_id == creator_id))
        ).scalar_one_or_none()
        if token_row:
            refresh_token = decrypt(token_row.refresh_token_encrypted)
    except Exception as exc:
        logger.warning("OAuth token read failed for creator %s: %s", creator_id, exc)
    await session.rollback()

    # Revoke Google OAuth refresh token — best effort, don't abort on failure.
    # Revoking the refresh token also invalidates any access tokens issued from it
    # (per Google OAuth 2.0 docs); a 400 with invalid_token / token_revoked means
    # the grant is already gone, which is success for our purposes.
    try:
        if refresh_token is not None:
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

    # Reacquired-transaction RLS stamp (Issue 82b): the rollback above ended the
    # transaction, so the deletes below auto-begin a NEW one. The after_begin
    # listener stamps the `app.creator_id` GUC from session.info — set it
    # explicitly so per-creator isolation holds even for callers (e.g. a future
    # inactive-account sweep) whose session was not built by the auth dependency.
    session.info["creator_id"] = creator_id

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


@router.delete("/me", status_code=204, response_class=Response, response_model=None)
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
