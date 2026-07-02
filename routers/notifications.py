"""Notification center, consent preferences, and one-click unsubscribe (Issue 245).

Three surfaces sit on top of the Issue 243/244 notification infra:

  * The authed notification center — list + dismiss the current creator's
    durable in-app ``notifications`` rows.  Isolation is RLS (FORCE on the
    ``notifications`` table) plus a defence-in-depth ``creator_id`` predicate on
    every query, mirroring ``routers/export.py``.
  * Authed preference read/write — GET/PATCH the creator's
    ``notification_preferences`` row.  ``email_transactional`` is legally
    always-on (CAN-SPAM / GDPR Art. 6(1)(b)); it is intentionally absent from
    the PATCH request model so a crafted body can never disable it.
  * No-auth unsubscribe — GET + POST /unsubscribe/{token}.  The token is the
    unguessable UUID4 ``unsubscribe_token`` keyed on the (no-RLS) preferences
    table; both handlers flip ``email_lifecycle`` off and return a generic
    response that never reveals which email/creator the token maps to.  POST is
    the RFC 8058 one-click endpoint (mail receivers such as Gmail/Yahoo issue an
    HTTPS POST to the ``List-Unsubscribe`` URL); GET is the human landing page
    behind the same link.  Must stay live ≥30 days — no rotation here.
"""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from db import AdminSessionLocal, get_session
from limiter import creator_key, limiter
from models import Creator, Notification, NotificationPreference

router = APIRouter(prefix="/api/notifications", tags=["notifications"])
# Unauthenticated one-click unsubscribe lives on its own prefix so it does not
# inherit get_current_creator (the email recipient has no session cookie).
unsubscribe_router = APIRouter(prefix="/unsubscribe", tags=["notifications"])
logger = logging.getLogger(__name__)

_NOTIFICATION_LIST_LIMIT = 50


# ── Pydantic surface ──────────────────────────────────────────────────────────


class NotificationOut(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    body: str
    link_url: str | None
    seen_at: datetime | None
    created_at: datetime


class NotificationListOut(BaseModel):
    items: list[NotificationOut]
    unread_count: int


class PreferencesOut(BaseModel):
    email_transactional: bool
    email_lifecycle: bool
    inapp_enabled: bool
    push_enabled: bool


class PreferencesPatch(BaseModel):
    # email_transactional is deliberately OMITTED: it is legally always-on, so a
    # crafted PATCH body cannot disable it (the server never reads such a field).
    email_lifecycle: bool | None = None
    inapp_enabled: bool | None = None
    push_enabled: bool | None = None


# ── Notification center (authed) ──────────────────────────────────────────────


@router.get("", response_model=NotificationListOut)
@limiter.limit("120/minute", key_func=creator_key)
async def list_notifications(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> NotificationListOut:
    """List the current creator's undismissed notifications, newest first.

    Stamps ``seen_at`` on any unseen rows in the same transaction so the
    unread→read transition is durable once the center has been opened.
    """
    rows = (
        await session.scalars(
            select(Notification)
            .where(Notification.creator_id == creator.id)
            .where(Notification.dismissed_at.is_(None))
            .order_by(Notification.created_at.desc())
            .limit(_NOTIFICATION_LIST_LIMIT)
        )
    ).all()

    now = datetime.now(UTC)
    unread_count = 0
    for row in rows:
        if row.seen_at is None:
            unread_count += 1
            row.seen_at = now
    await session.commit()

    items = [
        NotificationOut(
            id=row.id,
            kind=row.kind,
            title=row.title,
            body=row.body,
            link_url=row.link_url,
            seen_at=row.seen_at,
            created_at=row.created_at,
        )
        for row in rows
    ]
    return NotificationListOut(items=items, unread_count=unread_count)


@router.post("/{notification_id}/dismiss", response_model=NotificationOut)
@limiter.limit("120/minute", key_func=creator_key)
async def dismiss_notification(
    request: Request,
    notification_id: uuid.UUID,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> NotificationOut:
    """Dismiss one notification owned by the caller.

    Loads by id AND creator_id so a non-owned/unknown id returns 404 without
    leaking whether the row exists for another creator.
    """
    row = await session.scalar(
        select(Notification)
        .where(Notification.id == notification_id)
        .where(Notification.creator_id == creator.id)
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")

    row.dismissed_at = datetime.now(UTC)
    await session.commit()
    return NotificationOut(
        id=row.id,
        kind=row.kind,
        title=row.title,
        body=row.body,
        link_url=row.link_url,
        seen_at=row.seen_at,
        created_at=row.created_at,
    )


# ── Preferences (authed) ──────────────────────────────────────────────────────


async def _get_or_create_prefs(
    session: AsyncSession, creator_id: uuid.UUID
) -> NotificationPreference:
    """Return the creator's preference row, lazy-creating defaults if absent.

    Mirrors the lazy-create in ``send_notification`` so the API and the worker
    agree on the default row shape.
    """
    prefs = await session.get(NotificationPreference, creator_id)
    if prefs is None:
        prefs = NotificationPreference(creator_id=creator_id)
        session.add(prefs)
        await session.flush()
    return prefs


@router.get("/preferences", response_model=PreferencesOut)
@limiter.limit("120/minute", key_func=creator_key)
async def get_preferences(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> PreferencesOut:
    """Return the current creator's notification preferences (defaults if none)."""
    prefs = await _get_or_create_prefs(session, creator.id)
    await session.commit()
    return PreferencesOut(
        email_transactional=prefs.email_transactional,
        email_lifecycle=prefs.email_lifecycle,
        inapp_enabled=prefs.inapp_enabled,
        push_enabled=prefs.push_enabled,
    )


@router.patch("/preferences", response_model=PreferencesOut)
@limiter.limit("60/minute", key_func=creator_key)
async def update_preferences(
    request: Request,
    patch: PreferencesPatch,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> PreferencesOut:
    """Update the unsubscribable channels.

    Only ``email_lifecycle`` / ``inapp_enabled`` / ``push_enabled`` are
    accepted.  ``email_transactional`` is not a field on the request model, so
    it can never be disabled here — transactional mail is legally always-on.
    """
    prefs = await _get_or_create_prefs(session, creator.id)
    if patch.email_lifecycle is not None:
        prefs.email_lifecycle = patch.email_lifecycle
    if patch.inapp_enabled is not None:
        prefs.inapp_enabled = patch.inapp_enabled
    if patch.push_enabled is not None:
        prefs.push_enabled = patch.push_enabled
    prefs.updated_at = datetime.now(UTC)
    await session.commit()
    return PreferencesOut(
        email_transactional=prefs.email_transactional,
        email_lifecycle=prefs.email_lifecycle,
        inapp_enabled=prefs.inapp_enabled,
        push_enabled=prefs.push_enabled,
    )


# ── One-click unsubscribe (no auth) ───────────────────────────────────────────

_UNSUBSCRIBE_CONFIRMED_HTML = (
    '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
    '<title>Unsubscribed</title></head><body style="font-family: sans-serif; '
    'max-width: 480px; margin: 48px auto; padding: 0 24px; color: #1a1a1a;">'
    "<h2>You've been unsubscribed</h2>"
    "<p>You will no longer receive lifecycle emails (welcome, nudges, "
    "re-engagement) from AutoClip. Transactional emails — such as clip-ready and "
    "refund notices — are required for your account and will continue.</p>"
    "<p>You can re-enable lifecycle emails any time from Settings.</p>"
    "</body></html>"
)

_UNSUBSCRIBE_NOT_FOUND_HTML = (
    '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
    '<title>Link not recognised</title></head><body style="font-family: sans-serif; '
    'max-width: 480px; margin: 48px auto; padding: 0 24px; color: #1a1a1a;">'
    "<h2>This link is no longer valid</h2>"
    "<p>We couldn't process this unsubscribe link. If you continue to receive "
    "emails you didn't expect, please update your preferences from Settings.</p>"
    "</body></html>"
)


async def _flip_lifecycle_off(token: uuid.UUID) -> bool:
    """Flip ``email_lifecycle`` off for the preference row matching ``token``.

    Idempotent: flipping an already-False preference is a no-op success.
    Returns True when the token matched a preference row, False otherwise —
    the caller renders the generic (no email / creator id) response either way.

    Uses an admin (BYPASSRLS) session because there is no creator request
    context to set the RLS GUC; the ``notification_preferences`` table has no
    RLS policy and is keyed by the unique ``unsubscribe_token``, so the direct
    lookup is single-row and safe.
    """
    async with AdminSessionLocal() as session:
        prefs = await session.scalar(
            select(NotificationPreference).where(NotificationPreference.unsubscribe_token == token)
        )
        if prefs is None:
            logger.info("unsubscribe: token not found")
            return False

        if prefs.email_lifecycle:
            prefs.email_lifecycle = False
            prefs.updated_at = datetime.now(UTC)
            await session.commit()
            logger.info("unsubscribe: creator %s opted out of lifecycle email", prefs.creator_id)
        else:
            logger.info("unsubscribe: creator %s already opted out — no-op", prefs.creator_id)
        return True


@unsubscribe_router.get("/{token}", response_class=HTMLResponse)
@limiter.limit("30/minute", key_func=get_remote_address)
async def unsubscribe(request: Request, token: uuid.UUID) -> HTMLResponse:
    """Human landing page for the unsubscribe link (lifecycle emails).

    Unauthenticated by design — the email recipient has no session.  The token
    is the unguessable UUID4 ``unsubscribe_token``; a malformed (non-UUID) path
    param yields 422 before this handler runs.  A token that matches no
    preference returns a generic 404 that reveals no email or creator id.
    """
    if not await _flip_lifecycle_off(token):
        return HTMLResponse(content=_UNSUBSCRIBE_NOT_FOUND_HTML, status_code=404)
    return HTMLResponse(content=_UNSUBSCRIBE_CONFIRMED_HTML, status_code=200)


@unsubscribe_router.post("/{token}", response_class=PlainTextResponse)
@limiter.limit("30/minute", key_func=get_remote_address)
async def unsubscribe_one_click(request: Request, token: uuid.UUID) -> PlainTextResponse:
    """RFC 8058 one-click unsubscribe — the POST mail receivers actually send.

    Lifecycle emails advertise ``List-Unsubscribe-Post: List-Unsubscribe=One-Click``
    (worker/tasks.py), so RFC 8058 receivers (Gmail, Yahoo) perform an HTTPS
    POST to the ``List-Unsubscribe`` URL with no user interaction.  Machines
    consume the status code, not a page, so this returns plain text.  Same
    semantics as the GET landing page: idempotent flip, generic 404 on an
    unknown token (no existence leak).
    """
    if not await _flip_lifecycle_off(token):
        return PlainTextResponse(content="Not found", status_code=404)
    return PlainTextResponse(content="Unsubscribed", status_code=200)
