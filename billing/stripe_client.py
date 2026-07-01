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
from typing import Any

import stripe

from billing.packs import PURCHASABLE_PACKS
from config import settings

logger = logging.getLogger(__name__)

_STRIPE = stripe.StripeClient(
    settings.STRIPE_SECRET_KEY,
    http_client=stripe.HTTPXClient(timeout=settings.STRIPE_TIMEOUT_S),
    max_network_retries=3,
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
    params: dict[str, Any] = {
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
        if settings.STRIPE_TAX_ENABLED:
            # Persist the collected billing address back to the customer record so
            # future sessions can pre-fill it. Required when a customer already exists
            # — Stripe docs recommend customer_update[address]=auto for returning customers.
            # Source: https://docs.stripe.com/tax/checkout/page
            params["customer_update"] = {"address": "auto"}
    else:
        params["customer_creation"] = "always"

    # Issue 207 — Stripe Tax (flag-guarded).
    # When STRIPE_TAX_ENABLED=true, inject automatic_tax so Stripe computes and
    # collects the correct tax for the buyer's jurisdiction. The flag is False by
    # default and must only be flipped after ≥1 active tax registration exists in
    # Tax > Registrations. Enabling without a registration causes $0 tax collection
    # (documented safe — no error) but is unnecessary. billing_address_collection
    # is required so Stripe has the buyer's address to determine jurisdiction.
    # Source: https://docs.stripe.com/tax/checkout/page
    if settings.STRIPE_TAX_ENABLED:
        params["automatic_tax"] = {"enabled": True}
        params["billing_address_collection"] = "required"

    session = _STRIPE.checkout.sessions.create(
        params,
        options={"idempotency_key": intent_id},
    )
    if session.url is None:
        raise RuntimeError(f"Stripe returned no checkout URL for session {session.id}")
    logger.info("billing checkout_session pack=%s creator=%s", pack_id, creator_id)
    return session.url


def construct_webhook_event(payload: bytes, sig_header: str) -> stripe.Event:
    return stripe.Webhook.construct_event(payload, sig_header, settings.STRIPE_WEBHOOK_SECRET)


def list_recent_paid_sessions(lookback_hours: int) -> list[dict]:
    """Return Checkout sessions from the last *lookback_hours* with payment_status=='paid'.

    Uses the Stripe Sessions list API with ``status='complete'`` and a ``created[gte]``
    filter, then filters client-side for ``payment_status == 'paid'`` (the API does not
    support ``payment_status`` as a server-side filter — confirmed from Stripe docs).

    Pagination is cursor-based (``starting_after``): pages until ``has_more=False`` or
    the oldest session's ``created`` timestamp falls before the lookback window.

    Returns a list of plain dicts (the session object fields we need), not Stripe objects,
    so the caller does not depend on the Stripe SDK model at runtime.
    """
    from datetime import UTC, datetime, timedelta

    cutoff_ts = int((datetime.now(UTC) - timedelta(hours=lookback_hours)).timestamp())
    paid_sessions: list[dict] = []
    last_id: str | None = None

    while True:
        params: dict[str, object] = {
            "status": "complete",
            "created": {"gte": cutoff_ts},
            "limit": 100,
        }
        if last_id is not None:
            params["starting_after"] = last_id

        page = _STRIPE.checkout.sessions.list(params)

        for session in page.data:
            if session.get("payment_status") == "paid":
                paid_sessions.append(
                    {
                        "id": session["id"],
                        "payment_status": session.get("payment_status"),
                        "metadata": session.get("metadata") or {},
                        "customer": session.get("customer"),
                    }
                )

        if not page.has_more:
            break

        # Cursor: last session id on this page
        last_id = page.data[-1]["id"]

        # Stop paging if we have reached sessions older than the lookback window
        oldest_created = page.data[-1].get("created", 0)
        if oldest_created < cutoff_ts:
            break

    logger.info(
        "billing reconcile list_recent_paid_sessions lookback_hours=%d found=%d",
        lookback_hours,
        len(paid_sessions),
    )
    return paid_sessions
