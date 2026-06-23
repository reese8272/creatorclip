"""
Minute pack definitions. These are the only valid purchase units.
Add new packs here; the router and checkout session pick them up automatically.

Per-input-minute pricing taper (Issue 209 — rationale for the volume curve):
The per-minute rate falls as volume increases to reward commitment and offset the
fixed compute cost per video. The taper is:

    Starter  200 min  $18.00  → 9.0 ¢/min
    Regular  500 min  $40.00  → 8.0 ¢/min
    Creator 1000 min  $70.00  → 7.0 ¢/min
    Pro     2000 min $110.00  → 5.5 ¢/min
    Studio  5000 min $225.00  → 4.5 ¢/min
    Stream 10000 min $400.00  → 4.0 ¢/min

The Stream pack (Issue 209) directly addresses the COMPETITIVE_RESEARCH.md
Stage-1 recommendation that per-minute credits "punish 3–8hr streams" — a 4.0 ¢/min
rate below Studio rewards long-form creators without changing the billing primitive.
Per-input-minute is the 2026 category standard (OpusClip, Vizard, Klap). See
docs/DECISIONS.md (Issue 209) and docs/COMPETITIVE_RESEARCH.md reconciliation note.

Frontend (Pricing.tsx) keeps its own PACKS const for rendering — the ideal fix is
to drive it from the /billing/packs API endpoint (zero drift), but that is a
larger refactor than this issue scope. The Stream pack is mirrored in Pricing.tsx
to avoid shipping an inconsistent grid at launch. Follow-up: drive Pricing.tsx
from /billing/packs (tracked as a comment in Pricing.tsx).
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
    Pack("trial", "Free Trial", 60, 0),
    Pack("starter", "Starter", 200, 1800),
    Pack("regular", "Regular", 500, 4000),
    Pack("creator", "Creator", 1000, 7000),
    Pack("pro", "Pro", 2000, 11000),
    Pack("studio", "Studio", 5000, 22500),
    # Issue 209 — Stream pack for long-form/multi-hour VOD creators.
    # 4.0 ¢/min is below Studio (4.5 ¢/min) to reward the volume commitment that
    # long-form creators make. Price: $400 / 10,000 min = 4.0 ¢/min.
    Pack("stream", "Stream", 10000, 40000),
]

PACKS: dict[str, Pack] = {p.id: p for p in ALL_PACKS}

# Packs available for purchase via Stripe (excludes the free trial).
PURCHASABLE_PACKS: dict[str, Pack] = {k: v for k, v in PACKS.items() if v.price_cents > 0}
