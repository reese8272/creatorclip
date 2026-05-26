"""
Stripe integration for one-time minute pack purchases.

Uses Stripe Checkout (one-time payment mode) — no subscriptions, no meters.
Each pack is a line item with a price_data block; no pre-configured Stripe
products are required.
"""

import logging

import stripe

from billing.packs import PURCHASABLE_PACKS
from config import settings

logger = logging.getLogger(__name__)


def _client() -> stripe.StripeClient:
    return stripe.StripeClient(settings.STRIPE_SECRET_KEY)


def create_checkout_session(
    pack_id: str,
    creator_id: str,
    stripe_customer_id: str | None,
    success_url: str,
    cancel_url: str,
) -> str:
    """Create a Stripe Checkout session for a minute pack. Returns the hosted URL."""
    pack = PURCHASABLE_PACKS.get(pack_id)
    if pack is None:
        raise ValueError(f"Unknown pack_id: {pack_id!r}")

    per_min = pack.price_cents / pack.minutes
    params: dict = {
        "mode": "payment",
        "line_items": [
            {
                "price_data": {
                    "currency": "usd",
                    "unit_amount": pack.price_cents,
                    "product_data": {
                        "name": f"CreatorClip — {pack.label} Pack ({pack.minutes} minutes)",
                        "description": (
                            f"{pack.minutes} video processing minutes "
                            f"(${per_min / 100:.3f}/min). "
                            "Minutes never expire."
                        ),
                    },
                },
                "quantity": 1,
            }
        ],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "metadata": {"creator_id": creator_id, "pack_id": pack_id},
        "payment_intent_data": {
            "metadata": {"creator_id": creator_id, "pack_id": pack_id}
        },
    }
    if stripe_customer_id:
        params["customer"] = stripe_customer_id
    else:
        params["customer_creation"] = "always"

    session = _client().checkout.sessions.create(params)
    logger.info("billing checkout_session pack=%s creator=%s", pack_id, creator_id)
    return session.url


def construct_webhook_event(payload: bytes, sig_header: str) -> stripe.Event:
    return stripe.Webhook.construct_event(
        payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
    )
