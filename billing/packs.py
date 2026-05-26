"""
Minute pack definitions. These are the only valid purchase units.
Add new packs here; the router and checkout session pick them up automatically.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Pack:
    id: str
    label: str
    minutes: int
    price_cents: int

    @property
    def price_usd(self) -> float:
        return self.price_cents / 100

    @property
    def per_minute_cents(self) -> float:
        if self.price_cents == 0:
            return 0.0
        return self.price_cents / self.minutes


# Ordered from smallest to largest for display purposes.
ALL_PACKS: list[Pack] = [
    Pack("trial",   "Free Trial", 60,   0),
    Pack("starter", "Starter",    200,  1800),
    Pack("regular", "Regular",    500,  4000),
    Pack("creator", "Creator",    1000, 7000),
    Pack("pro",     "Pro",        2000, 11000),
    Pack("studio",  "Studio",     5000, 22500),
]

PACKS: dict[str, Pack] = {p.id: p for p in ALL_PACKS}

# Packs available for purchase via Stripe (excludes the free trial).
PURCHASABLE_PACKS: dict[str, Pack] = {k: v for k, v in PACKS.items() if v.price_cents > 0}
