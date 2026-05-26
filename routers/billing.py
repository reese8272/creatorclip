"""
Billing endpoints:
  POST /billing/webhook       — Stripe webhook receiver (no auth, signature-verified)
  POST /billing/early-access  — Pre-auth checkout for early-access subscribers (no auth)
  GET  /billing/portal        — Return Stripe Customer Portal URL for the current creator
  POST /billing/checkout      — Create a Stripe Checkout session and return the URL
  GET  /billing/status        — Return the current creator's plan tier and subscription status
"""

import logging

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr
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

    if event_type == "checkout.session.completed":
        await _handle_checkout_completed(obj)
    elif event_type in _SUBSCRIPTION_EVENTS:
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


async def _handle_checkout_completed(checkout_obj: dict) -> None:
    """
    Fires when a Stripe Checkout session is paid. Retrieves the Google account
    email from the custom field and logs a prominent admin notification so the
    user can be added as a test user in Google Cloud Console.
    """
    session_id = checkout_obj.get("id") if isinstance(checkout_obj, dict) else checkout_obj.id
    customer_id = checkout_obj.get("customer") if isinstance(checkout_obj, dict) else checkout_obj.customer

    google_email: str | None = None
    try:
        full_session = stripe.checkout.Session.retrieve(session_id, expand=["custom_fields"])
        fields = (
            full_session.get("custom_fields", [])
            if isinstance(full_session, dict)
            else (full_session.custom_fields or [])
        )
        for field in fields:
            key = field.get("key") if isinstance(field, dict) else field.key
            if key == "google_account_email":
                text = field.get("text", {}) if isinstance(field, dict) else field.text
                google_email = (
                    text.get("value") if isinstance(text, dict) else getattr(text, "value", None)
                )
    except Exception as exc:
        logger.warning("Could not retrieve checkout session %s custom fields: %s", session_id, exc)

    # Fall back to the session's customer_details email if no custom field was collected
    if not google_email:
        details = (
            checkout_obj.get("customer_details", {})
            if isinstance(checkout_obj, dict)
            else getattr(checkout_obj, "customer_details", None) or {}
        )
        google_email = (
            details.get("email") if isinstance(details, dict) else getattr(details, "email", None)
        )

    if google_email:
        try:
            stripe.Customer.modify(customer_id, metadata={"google_account_email": google_email})
        except Exception as exc:
            logger.warning("Could not update Stripe customer metadata: %s", exc)

        # Highly visible so it's impossible to miss in logs / monitoring
        logger.info(
            "=" * 60 + "\nNEW SUBSCRIBER — ADD TEST USER IN GOOGLE CLOUD CONSOLE\n"
            "Email: %s\nStripe customer: %s\n" + "=" * 60,
            google_email,
            customer_id,
        )
    else:
        logger.info(
            "NEW SUBSCRIBER — Stripe customer %s (no Google email captured)", customer_id
        )


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


class EarlyAccessRequest(BaseModel):
    google_email: EmailStr
    tier: str = "starter"


@router.post("/early-access")
async def early_access_checkout(body: EarlyAccessRequest) -> dict:
    """
    Pre-auth checkout for early-access subscribers. No YouTube connection required.
    Creates a Stripe customer keyed to their Google account email and returns a
    Checkout URL. After payment, the subscription is auto-linked when they connect
    their YouTube account via OAuth.
    """
    if not is_configured():
        raise HTTPException(status_code=503, detail="Billing not configured")

    if body.tier not in PLAN_TIERS or body.tier == "free":
        raise HTTPException(status_code=400, detail="Invalid tier. Choose 'starter' or 'pro'.")

    price_id = (
        settings.STRIPE_STARTER_PRICE_ID
        if body.tier == "starter"
        else settings.STRIPE_PRO_PRICE_ID
    )
    if not price_id:
        raise HTTPException(status_code=503, detail=f"Price ID for {body.tier} not configured")

    # Find or create Stripe customer keyed to their Google account email
    existing = stripe.Customer.list(email=body.google_email, limit=1)
    existing_list = existing.data if hasattr(existing, "data") else existing.get("data", [])
    if existing_list:
        customer_id = (
            existing_list[0].id
            if hasattr(existing_list[0], "id")
            else existing_list[0]["id"]
        )
    else:
        customer = stripe.Customer.create(
            email=body.google_email,
            metadata={"google_account_email": body.google_email},
        )
        customer_id = customer.id if hasattr(customer, "id") else customer["id"]

    checkout = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        custom_fields=[
            {
                "key": "google_account_email",
                "label": {"type": "custom", "custom": "Google account email"},
                "type": "text",
                "optional": False,
            }
        ],
        success_url=f"{settings.APP_BASE_URL}/static/early-access.html?payment=success",
        cancel_url=f"{settings.APP_BASE_URL}/static/early-access.html",
    )
    url = checkout.url if hasattr(checkout, "url") else checkout["url"]
    return {"checkout_url": url}


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
