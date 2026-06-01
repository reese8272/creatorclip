"""
Stripe integration for one-time minute pack purchases.

Uses Stripe Checkout (one-time payment mode) — no subscriptions, no meters.
Each pack is a line item with a price_data block; no pre-configured Stripe
products are required.

Issue 106 hardening:
- `create_checkout_session` now accepts a client-supplied v4 UUID `intent_id`
  and passes it as Stripe's `Idempotency-Key`. Double-click / router retry
  within Stripe's 24h idempotency window dedupes to a single Checkout
  session, closing the double-pay risk if a user happens to complete both
  flows from a back-button / parallel-tab scenario. Pattern matches Stripe's
  primary documented recommendation (client UUID in sessionStorage).
- `_STRIPE` client now carries an explicit `STRIPE_TIMEOUT_S` HTTP timeout
  (default 10s). The SDK default is ~80s; one stuck Stripe call would pin
  an `asyncio.to_thread` executor slot for that long.
- `session.url` None-check: Stripe SDK types `Session.url` as `Optional[str]`,
  but our `-> str` return is unsound when Stripe returns None. Raise a
  clear `RuntimeError` so the router can surface a 502 with context.
"""

import logging
import uuid

import stripe

from billing.packs import PURCHASABLE_PACKS
from config import settings

logger = logging.getLogger(__name__)

stripe.max_network_retries = 3

_STRIPE = stripe.StripeClient(
    settings.STRIPE_SECRET_KEY,
    http_client=stripe.HTTPXClient(timeout=settings.STRIPE_TIMEOUT_S),
)


def create_checkout_session(
    pack_id: str,
    creator_id: str,
    stripe_customer_id: str | None,
    success_url: str,
    cancel_url: str,
    intent_id: str,
) -> str:
    """Create a Stripe Checkout session for a minute pack. Returns the hosted URL.

    `intent_id` must be a v4 UUID generated client-side on /pricing page
    load and persisted in sessionStorage. Used as the Stripe Idempotency-Key
    so double-clicks or router retries within Stripe's 24h window collapse
    to a single Checkout session.
    """
    pack = PURCHASABLE_PACKS.get(pack_id)
    if pack is None:
        raise ValueError(f"Unknown pack_id: {pack_id!r}")

    # Validate UUID shape before passing to Stripe — closes the vector
    # where a client sends a garbage string that happens to collide with
    # another creator's idempotency key.
    try:
        uuid.UUID(intent_id, version=4)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"intent_id must be a v4 UUID: {intent_id!r}") from exc

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
        "payment_intent_data": {"metadata": {"creator_id": creator_id, "pack_id": pack_id}},
    }
    if stripe_customer_id:
        params["customer"] = stripe_customer_id
    else:
        params["customer_creation"] = "always"

    session = _STRIPE.checkout.sessions.create(
        params,
        options={"idempotency_key": intent_id},
    )
    if session.url is None:
        raise RuntimeError(
            f"Stripe returned no checkout URL for session {session.id}"
        )
    logger.info("billing checkout_session pack=%s creator=%s", pack_id, creator_id)
    return session.url


def construct_webhook_event(payload: bytes, sig_header: str) -> stripe.Event:
    return stripe.Webhook.construct_event(payload, sig_header, settings.STRIPE_WEBHOOK_SECRET)
