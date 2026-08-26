"""
Billing endpoints: balance, pack listing, Stripe checkout, and webhook fulfillment.
"""

import asyncio
import logging
import uuid

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import UUID4, BaseModel
from slowapi.util import get_remote_address
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from billing.ledger import get_balance, grant_minutes
from billing.packs import PURCHASABLE_PACKS
from billing.stripe_client import construct_webhook_event, create_checkout_session
from config import settings
from db import get_session
from limiter import creator_key, limiter
from models import Creator, MinutePack
from observability import log_event

router = APIRouter(prefix="/billing", tags=["billing"])
logger = logging.getLogger(__name__)


# ── Schemas ───────────────────────────────────────────────────────────────────


class BalanceOut(BaseModel):
    minutes_balance: int
    # Issue 126 — trial transparency. trial_ends_at is the absolute UTC datetime
    # the trial expires; trial_active = now < trial_ends_at; trial_days_remaining
    # is the ceiling (so "ends in 1 day" includes anything under 24h). low_balance
    # fires below LOW_BALANCE_THRESHOLD_MINUTES so the UI can light up the chip
    # and render pre-action warnings without a second round-trip.
    trial_ends_at: str | None = None
    trial_active: bool = False
    trial_days_remaining: int | None = None
    low_balance: bool = False


class PackOut(BaseModel):
    id: str
    label: str
    minutes: int
    price_cents: int
    price_usd: float
    per_minute_usd: float


class CheckoutRequest(BaseModel):
    pack_id: str
    success_url: str
    cancel_url: str
    # Client-supplied v4 UUID scoped to ONE purchase attempt (Issue 454; key
    # derivation from Issue 106): the client mints it on the first click of an
    # attempt and discards it when the request settles, so concurrent clicks in
    # the same attempt share a Stripe Idempotency-Key while every new attempt
    # (different pack, retry, repeat purchase) sends a fresh one — Stripe errors
    # when a key is replayed with different params and replays the cached
    # (already-consumed) Session when it is replayed with the same ones.
    # Pydantic UUID4 validates v4 shape before the value reaches Stripe.
    intent_id: UUID4


class CheckoutOut(BaseModel):
    checkout_url: str


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/balance", response_model=BalanceOut)
@limiter.limit("120/minute", key_func=creator_key)
async def balance(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> BalanceOut:
    from datetime import UTC, datetime
    from math import ceil

    bal = await get_balance(creator.id, session)
    # Issue 126 — derived trial-status fields. NULL trial_ends_at => no trial.
    trial_ends_at = creator.trial_ends_at
    trial_active = False
    trial_days_remaining: int | None = None
    if trial_ends_at is not None:
        now = datetime.now(UTC)
        # SQLAlchemy may hand back a naive datetime depending on the column's
        # `timezone=True` round-trip; normalize defensively before comparing.
        if trial_ends_at.tzinfo is None:
            trial_ends_at = trial_ends_at.replace(tzinfo=UTC)
        delta = trial_ends_at - now
        trial_active = delta.total_seconds() > 0
        if trial_active:
            # ceil so "ends in 18 hours" renders as "1 day" — matches user
            # expectations for countdown banners (Userpilot guidance).
            trial_days_remaining = max(1, ceil(delta.total_seconds() / 86400))
        else:
            trial_days_remaining = 0
    return BalanceOut(
        minutes_balance=bal,
        trial_ends_at=trial_ends_at.isoformat() if trial_ends_at else None,
        trial_active=trial_active,
        trial_days_remaining=trial_days_remaining,
        low_balance=bal < settings.LOW_BALANCE_THRESHOLD_MINUTES,
    )


@router.get("/packs", response_model=list[PackOut])
async def list_packs() -> list[PackOut]:
    return [
        PackOut(
            id=p.id,
            label=p.label,
            minutes=p.minutes,
            price_cents=p.price_cents,
            price_usd=p.price_usd,
            per_minute_usd=round(p.per_minute_cents / 100, 4),
        )
        for p in PURCHASABLE_PACKS.values()
    ]


@router.post("/checkout", response_model=CheckoutOut)
@limiter.limit("10/minute", key_func=creator_key)
async def checkout(
    request: Request,
    body: CheckoutRequest,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> CheckoutOut:
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Billing not configured")
    if body.pack_id not in PURCHASABLE_PACKS:
        raise HTTPException(status_code=400, detail="Invalid pack_id")
    # Issue 82b: get_current_creator's lookup began a transaction on this request's
    # session (dependency caching hands us the same instance), which would pin a
    # pooled connection across the 300–800 ms Stripe round-trip. Commit ends the
    # read-only transaction and returns the connection; no DB use follows.
    await session.commit()
    try:
        # Wave-3 Fix C: Stripe's StripeClient is sync (urllib3 under the hood);
        # calling it directly inside this async route blocks the FastAPI event
        # loop for the 300-800ms p95 Stripe round-trip and serializes
        # concurrent checkouts on one worker process. Offload to a thread so
        # the loop stays free for other requests. (SEV1 from post-Wave-2
        # /assess; same recipe Issue 78d used for transcription + Voyage.)
        url = await asyncio.to_thread(
            create_checkout_session,
            body.pack_id,
            str(creator.id),
            creator.stripe_customer_id,
            body.success_url,
            body.cancel_url,
            str(body.intent_id),
        )
    except stripe.IdempotencyError as exc:
        # A stale attempt key was replayed with different params (Issue 454 —
        # pre-fix clients reuse one tab-scoped id across attempts). Nothing was
        # charged; the client should retry, which mints a fresh attempt id.
        # Stripe's exception MESSAGE quotes the idempotency key back, so log
        # classification fields only — never str(exc), the key, or intent_id.
        logger.warning(
            "billing checkout idempotency conflict error_class=%s stripe_type=%s "
            "stripe_code=%s stripe_request_id=%s",
            type(exc).__name__,
            getattr(getattr(exc, "error", None), "type", None),
            getattr(exc, "code", None),
            getattr(exc, "request_id", None),
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "checkout_conflict",
                "message": (
                    "This purchase attempt conflicted with an earlier one. "
                    "Nothing was charged — please try again."
                ),
            },
        ) from exc
    except stripe.StripeError as exc:
        # Same logging rule as above: StripeError messages can echo request
        # details; the request_id is the safe handle for dashboard lookup.
        logger.error(
            "billing checkout failed error_class=%s stripe_type=%s "
            "stripe_code=%s stripe_request_id=%s http_status=%s",
            type(exc).__name__,
            getattr(getattr(exc, "error", None), "type", None),
            getattr(exc, "code", None),
            getattr(exc, "request_id", None),
            getattr(exc, "http_status", None),
        )
        raise HTTPException(
            status_code=502,
            detail={
                "code": "checkout_failed",
                "message": "Could not create checkout session. Nothing was charged.",
            },
        ) from exc
    except Exception as exc:
        # Non-Stripe failure (e.g. the session.url None guard). Our own message
        # text is safe to log and carries no Stripe key material.
        logger.error("billing checkout failed error_class=%s: %s", type(exc).__name__, exc)
        raise HTTPException(
            status_code=502,
            detail={
                "code": "checkout_failed",
                "message": "Could not create checkout session. Nothing was charged.",
            },
        ) from exc
    return CheckoutOut(checkout_url=url)


# Issue 523 — both fulfillment events take the SAME path below: the
# data.object of async_payment_succeeded is a Checkout Session with
# payment_status now "paid", exactly the shape completed carries, and the
# stripe_session_id idempotency key is what Stripe documents for precisely
# this pair (a card purchase fulfills via completed; a delayed method via
# async_payment_succeeded days later; both converge on one dedupe key).
_FULFILLMENT_EVENTS = frozenset(
    {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
    }
)


@router.post("/webhook", include_in_schema=False)
@limiter.limit("60/minute", key_func=get_remote_address)
async def stripe_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Fulfill pack purchases from Stripe Checkout events.

    Handles checkout.session.completed and (Issue 523) the async pair:
    async_payment_succeeded fulfills through the identical path, and
    async_payment_failed is logged — nothing was granted, so there is no
    ledger action, but the failure must be observable.

    Rate-limited per source IP at the Stripe-published webhook delivery rate.
    Sits in front of the signature check so a flood of bad-signature payloads
    can't burn worker threads on the validation path. (Issue 110)
    """
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    log_event("billing_webhook_received")
    try:
        event = construct_webhook_event(payload, sig)
    except stripe.SignatureVerificationError as exc:
        log_event("billing_webhook_rejected", reason="bad_signature")
        raise HTTPException(status_code=400, detail="Invalid signature") from exc
    except Exception as exc:
        log_event("billing_webhook_rejected", reason="parse_error")
        logger.warning("Stripe webhook parse error: %s", exc)
        raise HTTPException(status_code=400, detail="Bad webhook payload") from exc

    if event["type"] == "checkout.session.async_payment_failed":
        # Issue 523 — the delayed payment did not land. The completed event was
        # (correctly) ignored as unpaid, so nothing was ever granted and there
        # is no ledger action to take — but a customer whose money bounced must
        # be visible in the event log, not silently dropped.
        failed_cs = event["data"]["object"]
        failed_meta = failed_cs.get("metadata") or {}
        log_event(
            "billing_async_payment_failed",
            pack_id=failed_meta.get("pack_id"),
            creator_id=failed_meta.get("creator_id"),
        )
        logger.warning(
            "billing async payment failed session=%s pack=%s",
            failed_cs.get("id"),
            failed_meta.get("pack_id"),
        )
        return {"status": "async_payment_failed"}

    if event["type"] not in _FULFILLMENT_EVENTS:
        return {"status": "ignored"}

    cs = event["data"]["object"]

    # Issue 206 — payment_status guard.
    # Stripe's fulfillment docs explicitly require checking payment_status before
    # granting fulfillment. For one-time purchasable packs 'no_payment_required'
    # is not a valid outcome (price_cents > 0 for all PURCHASABLE_PACKS), so the
    # narrower guard `== 'paid'` is correct here — async/delayed methods (ACH,
    # bank transfer, BNPL) complete the session flow but defer collection;
    # checkout.session.async_payment_succeeded (handled above via
    # _FULFILLMENT_EVENTS since Issue 523) fires later when payment actually
    # lands. Absent payment_status (malformed/unknown payload) is also rejected.
    # Observable, not a silent drop (Issue 523): an unpaid completion is the
    # first surfacing of an async-shaped session.
    # Source: https://docs.stripe.com/checkout/fulfillment
    if cs.get("payment_status") != "paid":
        log_event(
            "billing_webhook_ignored",
            reason="payment_status_not_paid",
            event_type=event["type"],
        )
        logger.info(
            "billing webhook ignored session=%s event=%s payment_status=%s",
            cs.get("id"),
            event["type"],
            cs.get("payment_status"),
        )
        return {"status": "ignored"}

    meta = cs.get("metadata") or {}
    creator_id_str = meta.get("creator_id")
    pack_id = meta.get("pack_id")
    stripe_customer = cs.get("customer")
    stripe_session_id = cs.get("id")

    if not creator_id_str or not pack_id:
        logger.error("Webhook missing metadata: %s", meta)
        return {"status": "ignored"}

    pack = PURCHASABLE_PACKS.get(pack_id)
    if pack is None:
        logger.error("Webhook unknown pack_id: %s", pack_id)
        return {"status": "ignored"}

    try:
        creator_id = uuid.UUID(creator_id_str)
    except ValueError:
        logger.error("Webhook malformed creator_id: %r", creator_id_str)
        return {"status": "ignored"}

    # Stamp creator_id BEFORE the idempotency query so the RLS policy
    # (creator_id = current_setting('app.creator_id', true)::uuid) actually
    # matches the row. Without this, RLS reads NULL → query returns 0 rows →
    # the fast-path short-circuit never fires, and integrity rests entirely
    # on grant_minutes() catching the UNIQUE constraint downstream. Same
    # class of omission the worker async helpers fixed earlier this cycle.
    # (Assessment 2026-06-08 SEV1 fix.)
    session.info["creator_id"] = str(creator_id)

    # Idempotency: skip if this Stripe session was already fulfilled.
    existing = await session.scalar(
        select(MinutePack.id).where(MinutePack.stripe_session_id == stripe_session_id)
    )
    if existing:
        return {"status": "already_fulfilled"}

    if stripe_customer:
        await session.execute(
            update(Creator)
            .where(Creator.id == creator_id)
            .values(stripe_customer_id=stripe_customer)
        )

    await grant_minutes(
        creator_id=creator_id,
        minutes=pack.minutes,
        reason="purchase",
        session=session,
        pack_id=pack_id,
        stripe_session_id=stripe_session_id,
        price_cents=pack.price_cents,
    )
    await session.commit()
    log_event(
        "billing_webhook_processed",
        pack_id=pack_id,
        creator_id=str(creator_id),
        event_type=event["type"],
    )
    logger.info(
        "billing fulfilled pack=%s creator=%s minutes=%d", pack_id, creator_id, pack.minutes
    )
    return {"status": "ok"}
