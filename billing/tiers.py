"""
Plan tier definitions and FastAPI enforcement dependencies.

Tier names map 1:1 to values stored in Creator.plan_tier, which are set by
the Stripe webhook handler when a subscription is created or updated.
Creators with no plan_tier (never subscribed) are treated as free tier.
"""

import uuid
from datetime import UTC, datetime

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from db import get_session
from models import Creator, Usage

PLAN_TIERS: dict[str, dict] = {
    "free": {
        "videos_per_month": 2,
        "clips_per_video": 3,
        "render_enabled": False,
    },
    "starter": {
        "videos_per_month": 20,
        "clips_per_video": 8,
        "render_enabled": True,
    },
    "pro": {
        "videos_per_month": None,  # unlimited
        "clips_per_video": 8,
        "render_enabled": True,
    },
}

_ACTIVE_STATUSES = {"active", "trialing"}


def get_tier(creator: Creator) -> dict:
    """Return the tier limits dict for this creator."""
    return PLAN_TIERS.get(creator.plan_tier or "free", PLAN_TIERS["free"])


def is_subscription_active(creator: Creator) -> bool:
    """Free tier is always considered active. Paid tiers require a valid Stripe status."""
    if not creator.plan_tier or creator.plan_tier == "free":
        return True
    return creator.subscription_status in _ACTIVE_STATUSES


async def require_render(creator: Creator = Depends(get_current_creator)) -> Creator:
    """Enforce that the creator's plan includes render access."""
    tier = get_tier(creator)
    if not tier["render_enabled"]:
        raise HTTPException(
            status_code=402,
            detail="Rendering is not available on the free plan. Upgrade to Starter or Pro.",
        )
    if not is_subscription_active(creator):
        raise HTTPException(
            status_code=402,
            detail="Your subscription is not active. Please update your payment method.",
        )
    return creator


async def check_video_limit(
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> Creator:
    """Enforce the monthly video-processing limit for the creator's plan tier."""
    tier = get_tier(creator)
    limit = tier["videos_per_month"]
    if limit is None:
        return creator  # unlimited tier

    period = datetime.now(UTC).strftime("%Y-%m")
    result = await session.execute(
        select(Usage).where(
            Usage.creator_id == creator.id,
            Usage.period == period,
        )
    )
    usage = result.scalar_one_or_none()
    processed = usage.videos_processed if usage else 0

    if processed >= limit:
        raise HTTPException(
            status_code=402,
            detail=f"Monthly video limit reached ({limit} videos on your plan). Upgrade to process more.",
        )
    return creator


async def increment_video_usage(session: AsyncSession, creator_id: uuid.UUID) -> None:
    """Increment the monthly videos_processed counter. Call after accepting a new video."""
    period = datetime.now(UTC).strftime("%Y-%m")
    result = await session.execute(
        select(Usage).where(
            Usage.creator_id == creator_id,
            Usage.period == period,
        )
    )
    usage = result.scalar_one_or_none()
    if usage:
        usage.videos_processed += 1
    else:
        session.add(
            Usage(
                creator_id=creator_id,
                period=period,
                videos_processed=1,
            )
        )
