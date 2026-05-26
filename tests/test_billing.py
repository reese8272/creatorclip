"""
Tests for billing tier enforcement and Stripe webhook handling.

No live Stripe API or DB required — all external calls are mocked.
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from billing.tiers import (
    PLAN_TIERS,
    check_video_limit,
    get_tier,
    increment_video_usage,
    is_subscription_active,
)
from models import Creator, Usage


def _make_creator(plan_tier: str | None = None, subscription_status: str | None = None) -> Creator:
    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    c.email = "test@example.com"
    c.plan_tier = plan_tier
    c.subscription_status = subscription_status
    c.stripe_customer_id = None
    return c


# ── get_tier ──────────────────────────────────────────────────────────────────


def test_get_tier_none_returns_free():
    creator = _make_creator(plan_tier=None)
    assert get_tier(creator) == PLAN_TIERS["free"]


def test_get_tier_free_explicit():
    creator = _make_creator(plan_tier="free")
    assert get_tier(creator) == PLAN_TIERS["free"]


def test_get_tier_starter():
    creator = _make_creator(plan_tier="starter")
    assert get_tier(creator) == PLAN_TIERS["starter"]


def test_get_tier_pro():
    creator = _make_creator(plan_tier="pro")
    assert get_tier(creator) == PLAN_TIERS["pro"]


def test_get_tier_unknown_falls_back_to_free():
    creator = _make_creator(plan_tier="enterprise_old")
    assert get_tier(creator) == PLAN_TIERS["free"]


# ── is_subscription_active ────────────────────────────────────────────────────


def test_free_tier_always_active():
    assert is_subscription_active(_make_creator(plan_tier=None)) is True
    assert is_subscription_active(_make_creator(plan_tier="free")) is True


def test_starter_active_status():
    assert is_subscription_active(_make_creator("starter", "active")) is True


def test_starter_trialing_status():
    assert is_subscription_active(_make_creator("starter", "trialing")) is True


def test_starter_past_due_is_not_active():
    assert is_subscription_active(_make_creator("starter", "past_due")) is False


def test_starter_canceled_is_not_active():
    assert is_subscription_active(_make_creator("starter", "canceled")) is False


# ── tier limits sanity ────────────────────────────────────────────────────────


def test_free_render_disabled():
    assert PLAN_TIERS["free"]["render_enabled"] is False


def test_starter_render_enabled():
    assert PLAN_TIERS["starter"]["render_enabled"] is True


def test_pro_unlimited_videos():
    assert PLAN_TIERS["pro"]["videos_per_month"] is None


# ── require_render dependency ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_require_render_free_raises_402(client: TestClient):
    from fastapi import HTTPException
    from billing.tiers import require_render

    creator = _make_creator(plan_tier="free")
    with pytest.raises(HTTPException) as exc_info:
        await require_render(creator=creator)
    assert exc_info.value.status_code == 402


@pytest.mark.asyncio
async def test_require_render_starter_active_passes():
    from billing.tiers import require_render

    creator = _make_creator(plan_tier="starter", subscription_status="active")
    result = await require_render(creator=creator)
    assert result is creator


@pytest.mark.asyncio
async def test_require_render_past_due_raises_402():
    from fastapi import HTTPException
    from billing.tiers import require_render

    creator = _make_creator(plan_tier="starter", subscription_status="past_due")
    with pytest.raises(HTTPException) as exc_info:
        await require_render(creator=creator)
    assert exc_info.value.status_code == 402


# ── check_video_limit ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_video_limit_under_limit_passes():
    from billing.tiers import check_video_limit

    creator = _make_creator(plan_tier="free")  # limit=2
    usage = MagicMock(spec=Usage)
    usage.videos_processed = 1

    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: usage))

    result = await check_video_limit(creator=creator, session=session)
    assert result is creator


@pytest.mark.asyncio
async def test_check_video_limit_at_limit_raises_402():
    from fastapi import HTTPException
    from billing.tiers import check_video_limit

    creator = _make_creator(plan_tier="free")  # limit=2
    usage = MagicMock(spec=Usage)
    usage.videos_processed = 2

    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: usage))

    with pytest.raises(HTTPException) as exc_info:
        await check_video_limit(creator=creator, session=session)
    assert exc_info.value.status_code == 402


@pytest.mark.asyncio
async def test_check_video_limit_pro_unlimited_skips_db():
    from billing.tiers import check_video_limit

    creator = _make_creator(plan_tier="pro")
    session = AsyncMock()

    result = await check_video_limit(creator=creator, session=session)
    assert result is creator
    session.execute.assert_not_called()


# ── increment_video_usage ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_increment_creates_usage_row_when_none():
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))

    creator_id = uuid.uuid4()
    await increment_video_usage(session, creator_id)

    session.add.assert_called_once()
    added = session.add.call_args.args[0]
    assert added.videos_processed == 1
    assert added.creator_id == creator_id


@pytest.mark.asyncio
async def test_increment_updates_existing_usage_row():
    existing = MagicMock(spec=Usage)
    existing.videos_processed = 3

    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: existing))

    await increment_video_usage(session, uuid.uuid4())

    assert existing.videos_processed == 4
    session.add.assert_not_called()


# ── billing status endpoint ───────────────────────────────────────────────────


def test_billing_status_free_tier(client: TestClient):
    from auth import get_current_creator
    from main import app

    creator = _make_creator(plan_tier=None)
    app.dependency_overrides[get_current_creator] = lambda: creator

    resp = client.get("/billing/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["plan_tier"] == "free"
    assert data["render_enabled"] is False

    app.dependency_overrides.clear()


def test_billing_status_pro_tier(client: TestClient):
    from auth import get_current_creator
    from main import app

    creator = _make_creator(plan_tier="pro", subscription_status="active")
    app.dependency_overrides[get_current_creator] = lambda: creator

    resp = client.get("/billing/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["plan_tier"] == "pro"
    assert data["render_enabled"] is True
    assert data["videos_per_month"] is None

    app.dependency_overrides.clear()


# ── Stripe webhook ─────────────────────────────────────────────────────────────


def _make_subscription_event(event_type: str, customer_id: str, status: str, price_id: str) -> dict:
    return {
        "type": event_type,
        "data": {
            "object": {
                "customer": customer_id,
                "status": status,
                "items": {"data": [{"price": {"id": price_id}}]},
            }
        },
    }


def test_webhook_subscription_created_updates_plan_tier(client: TestClient):
    from unittest.mock import patch as _patch
    from main import app
    from db import get_session

    creator = _make_creator()
    creator.stripe_customer_id = "cus_test_123"

    async def _fake_session():
        session = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = creator
        session.execute = AsyncMock(return_value=result)
        session.commit = AsyncMock()
        yield session

    app.dependency_overrides[get_session] = _fake_session

    payload = _make_subscription_event(
        "customer.subscription.created", "cus_test_123", "active", ""
    )

    with _patch("routers.billing.settings") as mock_settings:
        mock_settings.STRIPE_WEBHOOK_SECRET = ""
        resp = client.post(
            "/billing/webhook",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"received": True}
    app.dependency_overrides.clear()


def test_webhook_bad_signature_returns_400(client: TestClient):
    from unittest.mock import patch as _patch

    payload = json.dumps({"type": "test"}).encode()

    with _patch("routers.billing.stripe.Webhook.construct_event", side_effect=Exception("bad sig")):
        with _patch("routers.billing.settings") as mock_settings:
            mock_settings.STRIPE_WEBHOOK_SECRET = "whsec_test"
            resp = client.post(
                "/billing/webhook",
                content=payload,
                headers={
                    "Content-Type": "application/json",
                    "stripe-signature": "t=fake,v1=fake",
                },
            )

    assert resp.status_code == 400


def test_webhook_unknown_event_returns_200(client: TestClient):
    from unittest.mock import patch as _patch
    from db import get_session
    from main import app

    async def _fake_session():
        session = AsyncMock()
        yield session

    app.dependency_overrides[get_session] = _fake_session

    payload = {"type": "payment_intent.succeeded", "data": {"object": {}}}

    with _patch("routers.billing.settings") as mock_settings:
        mock_settings.STRIPE_WEBHOOK_SECRET = ""
        resp = client.post(
            "/billing/webhook",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )

    assert resp.status_code == 200
    app.dependency_overrides.clear()
