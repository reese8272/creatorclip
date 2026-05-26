"""
Stripe API client configuration.

Import `stripe` from here so the API key is guaranteed to be set before use.
When STRIPE_SECRET_KEY is empty (local dev without billing), Stripe calls will
raise AuthenticationError — callers should check `is_configured()` first.
"""

import logging

import stripe

from config import settings

logger = logging.getLogger(__name__)

if settings.STRIPE_SECRET_KEY:
    stripe.api_key = settings.STRIPE_SECRET_KEY


def is_configured() -> bool:
    return bool(settings.STRIPE_SECRET_KEY)


def price_id_to_tier(price_id: str) -> str:
    """Map a Stripe price ID to an internal plan tier name."""
    if price_id == settings.STRIPE_PRO_PRICE_ID:
        return "pro"
    if price_id == settings.STRIPE_STARTER_PRICE_ID:
        return "starter"
    return "free"


def find_active_subscription_for_email(email: str) -> tuple[str, str, str] | None:
    """
    Look up a Stripe customer by email and return their active subscription details.
    Returns (customer_id, plan_tier, subscription_status) or None if not found.
    Used in the OAuth callback to auto-link pre-purchase subscribers.
    """
    if not is_configured() or not email:
        return None
    try:
        customers = stripe.Customer.list(email=email, limit=1)
        customer_list = customers.data if hasattr(customers, "data") else customers.get("data", [])
        if not customer_list:
            return None

        customer = customer_list[0]
        customer_id = customer.id if hasattr(customer, "id") else customer["id"]

        subscriptions = stripe.Subscription.list(customer=customer_id, limit=1)
        sub_list = subscriptions.data if hasattr(subscriptions, "data") else subscriptions.get("data", [])
        if not sub_list:
            return None

        sub = sub_list[0]
        status = sub.status if hasattr(sub, "status") else sub.get("status", "")
        if status not in ("active", "trialing"):
            return None

        items = sub.items.data if hasattr(sub, "items") else sub.get("items", {}).get("data", [])
        price_id = ""
        if items:
            price = items[0].price if hasattr(items[0], "price") else items[0].get("price", {})
            price_id = price.id if hasattr(price, "id") else price.get("id", "")

        return customer_id, price_id_to_tier(price_id), status
    except Exception as exc:
        logger.warning("Stripe subscription lookup failed for %s: %s", email, exc)
        return None
