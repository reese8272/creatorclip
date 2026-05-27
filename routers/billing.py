"""
Billing endpoints: balance, pack listing, Stripe checkout, and webhook fulfillment.
"""

import logging
import uuid

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from billing.ledger import get_balance, grant_minutes
from billing.packs import PURCHASABLE_PACKS
from billing.stripe_client import construct_webhook_event, create_checkout_session
from config import settings
from db import get_session
from limiter import limiter
from models import Creator, MinutePack

router = APIRouter(prefix="/billing", tags=["billing"])
logger = logging.getLogger(__name__)


# ── Schemas ───────────────────────────────────────────────────────────────────


class BalanceOut(BaseModel):
    minutes_balance: int


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


class CheckoutOut(BaseModel):
    checkout_url: str


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/balance", response_model=BalanceOut)
@limiter.limit("120/minute")
async def balance(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> BalanceOut:
    bal = await get_balance(creator.id, session)
    return BalanceOut(minutes_balance=bal)


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
@limiter.limit("10/minute")
async def checkout(
    request: Request,
    body: CheckoutRequest,
    creator: Creator = Depends(get_current_creator),
) -> CheckoutOut:
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Billing not configured")
    if body.pack_id not in PURCHASABLE_PACKS:
        raise HTTPException(status_code=400, detail="Invalid pack_id")
    try:
        url = create_checkout_session(
            pack_id=body.pack_id,
            creator_id=str(creator.id),
            stripe_customer_id=creator.stripe_customer_id,
            success_url=body.success_url,
            cancel_url=body.cancel_url,
        )
    except Exception as exc:
        logger.error("Stripe checkout creation failed: %s", exc)
        raise HTTPException(status_code=502, detail="Could not create checkout session") from exc
    return CheckoutOut(checkout_url=url)


@router.post("/webhook", include_in_schema=False)
async def stripe_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Stripe sends checkout.session.completed here to fulfill pack purchases."""
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = construct_webhook_event(payload, sig)
    except stripe.SignatureVerificationError as exc:
        logger.warning("Stripe webhook bad signature: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid signature") from exc
    except Exception as exc:
        logger.warning("Stripe webhook parse error: %s", exc)
        raise HTTPException(status_code=400, detail="Bad webhook payload") from exc

    if event["type"] != "checkout.session.completed":
        return {"status": "ignored"}

    cs = event["data"]["object"]
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

    # Idempotency: skip if this Stripe session was already fulfilled.
    existing = await session.scalar(
        select(MinutePack.id).where(MinutePack.stripe_session_id == stripe_session_id)
    )
    if existing:
        return {"status": "already_fulfilled"}

    creator_id = uuid.UUID(creator_id_str)

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
    logger.info(
        "billing fulfilled pack=%s creator=%s minutes=%d", pack_id, creator_id, pack.minutes
    )
    return {"status": "ok"}
