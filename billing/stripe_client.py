"""
Stripe API client configuration.

Import `stripe` from here so the API key is guaranteed to be set before use.
When STRIPE_SECRET_KEY is empty (local dev without billing), Stripe calls will
raise AuthenticationError — callers should check `is_configured()` first.
"""

import stripe

from config import settings

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
