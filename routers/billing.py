"""
Billing endpoints:
  POST /billing/webhook       — Stripe webhook receiver (no auth, signature-verified)
  GET  /billing/portal        — Return Stripe Customer Portal URL for the current creator
  POST /billing/checkout      — Create a Stripe Checkout session and return the URL
  GET  /billing/status        — Return the current creator's plan tier and subscription status
"""

import logging

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from billing.stripe_client import is_configured, price_id_to_tier
from billing.tiers import PLAN_TIERS, get_tier
from config import settings
from db import get_session
from models import Creator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])

_SUBSCRIPTION_EVENTS = {
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
}


@router.post("/webhook", include_in_schema=False)
async def stripe_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """
    Receive Stripe webhook events. Signature is verified when STRIPE_WEBHOOK_SECRET
    is set. In development without the secret, verification is skipped with a warning.
    """
    body = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    if settings.STRIPE_WEBHOOK_SECRET:
        try:
            event = stripe.Webhook.construct_event(
                body, sig_header, settings.STRIPE_WEBHOOK_SECRET
            )
        except stripe.SignatureVerificationError:
            logger.warning("Stripe webhook signature verification failed")
            raise HTTPException(status_code=400, detail="Invalid signature")
        except Exception as exc:
            logger.error("Stripe webhook parse error: %s", exc)
            raise HTTPException(status_code=400, detail="Malformed webhook payload")
    else:
        import json
        logger.warning("STRIPE_WEBHOOK_SECRET not set — skipping signature verification")
        try:
            event = json.loads(body)
        except Exception:
            raise HTTPException(status_code=400, detail="Malformed webhook payload")

    event_type = event.get("type") if isinstance(event, dict) else event.type
    obj = event.get("data", {}).get("object", {}) if isinstance(event, dict) else event.data.object

    if event_type in _SUBSCRIPTION_EVENTS:
        await _handle_subscription_event(session, event_type, obj)
    elif event_type == "invoice.payment_failed":
        await _handle_payment_failed(session, obj)
    else:
        logger.debug("Unhandled Stripe event: %s", event_type)

    return {"received": True}


async def _handle_subscription_event(session: AsyncSession, event_type: str, sub: dict) -> None:
    customer_id = sub.get("customer")
    status = sub.get("status", "")
    items = sub.get("items", {}).get("data", [])
    price_id = items[0].get("price", {}).get("id", "") if items else ""
    tier = price_id_to_tier(price_id)

    creator = await _creator_by_stripe_id(session, customer_id)
    if not creator:
        logger.warning("Stripe event for unknown customer %s", customer_id)
        return

    if event_type == "customer.subscription.deleted":
        creator.plan_tier = "free"
        creator.subscription_status = "canceled"
    else:
        creator.plan_tier = tier
        creator.subscription_status = status

    await session.commit()
    logger.info(
        "Subscription %s: creator=%s tier=%s status=%s",
        event_type,
        creator.id,
        creator.plan_tier,
        creator.subscription_status,
    )


async def _handle_payment_failed(session: AsyncSession, invoice: dict) -> None:
    customer_id = invoice.get("customer")
    creator = await _creator_by_stripe_id(session, customer_id)
    if not creator:
        logger.warning("Payment failed for unknown customer %s", customer_id)
        return

    creator.subscription_status = "past_due"
    await session.commit()
    logger.info("Payment failed: creator=%s marked past_due", creator.id)


async def _creator_by_stripe_id(session: AsyncSession, stripe_customer_id: str) -> Creator | None:
    if not stripe_customer_id:
        return None
    result = await session.execute(
        select(Creator).where(Creator.stripe_customer_id == stripe_customer_id)
    )
    return result.scalar_one_or_none()


async def _get_or_create_stripe_customer(creator: Creator, session: AsyncSession) -> str:
    """Return the creator's Stripe customer ID, creating one if it doesn't exist."""
    if creator.stripe_customer_id:
        return creator.stripe_customer_id

    customer = stripe.Customer.create(
        email=creator.email or "",
        metadata={"creator_id": str(creator.id)},
    )
    creator.stripe_customer_id = customer.id
    await session.commit()
    return customer.id


@router.get("/status")
async def billing_status(creator: Creator = Depends(get_current_creator)) -> dict:
    tier = get_tier(creator)
    return {
        "plan_tier": creator.plan_tier or "free",
        "subscription_status": creator.subscription_status,
        "render_enabled": tier["render_enabled"],
        "videos_per_month": tier["videos_per_month"],
        "clips_per_video": tier["clips_per_video"],
    }


@router.get("/portal")
async def billing_portal(
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return a Stripe Customer Portal URL so the creator can manage their subscription."""
    if not is_configured():
        raise HTTPException(status_code=503, detail="Billing not configured")

    customer_id = await _get_or_create_stripe_customer(creator, session)
    portal = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"{settings.APP_BASE_URL}/static/index.html",
    )
    return {"portal_url": portal.url}


@router.post("/checkout")
async def create_checkout(
    tier: str,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Create a Stripe Checkout session for the given tier. Returns the checkout URL."""
    if not is_configured():
        raise HTTPException(status_code=503, detail="Billing not configured")

    if tier not in PLAN_TIERS or tier == "free":
        raise HTTPException(status_code=400, detail="Invalid tier. Choose 'starter' or 'pro'.")

    price_id = (
        settings.STRIPE_STARTER_PRICE_ID if tier == "starter" else settings.STRIPE_PRO_PRICE_ID
    )
    if not price_id:
        raise HTTPException(status_code=503, detail=f"Price ID for {tier} not configured")

    customer_id = await _get_or_create_stripe_customer(creator, session)
    checkout = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{settings.APP_BASE_URL}/static/index.html?billing=success",
        cancel_url=f"{settings.APP_BASE_URL}/static/index.html?billing=cancel",
    )
    return {"checkout_url": checkout.url}
