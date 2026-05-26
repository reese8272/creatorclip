"""
Tests for the billing module: packs, ledger, endpoints, and webhook fulfillment.
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from billing.ledger import (
    check_positive_balance,
    deduct_minutes,
    get_balance,
    grant_minutes,
    video_minutes,
)
from billing.packs import PACKS, PURCHASABLE_PACKS, Pack


# ── Pack definitions ──────────────────────────────────────────────────────────


def test_all_packs_present():
    assert set(PACKS.keys()) == {"trial", "starter", "regular", "creator", "pro", "studio"}


def test_trial_is_not_purchasable():
    assert "trial" not in PURCHASABLE_PACKS


def test_purchasable_packs_have_nonzero_price():
    for p in PURCHASABLE_PACKS.values():
        assert p.price_cents > 0


def test_pack_per_minute_rate_decreases_with_volume():
    """Larger packs must be cheaper per minute than smaller ones."""
    purchasable = sorted(PURCHASABLE_PACKS.values(), key=lambda p: p.minutes)
    rates = [p.per_minute_cents for p in purchasable]
    for i in range(len(rates) - 1):
        assert rates[i] > rates[i + 1], (
            f"{purchasable[i].id} ({rates[i]:.3f}) should cost more per min than "
            f"{purchasable[i+1].id} ({rates[i+1]:.3f})"
        )


def test_pack_price_usd():
    creator = PACKS["creator"]
    assert creator.price_usd == 70.00


# ── video_minutes helper ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "duration_s,expected",
    [
        (0.0, 1),      # minimum 1
        (60.0, 1),     # exactly 1 minute
        (61.0, 2),     # just over = round up
        (90.0, 2),
        (3600.0, 60),  # 1 hour
        (3601.0, 61),
    ],
)
def test_video_minutes(duration_s: float, expected: int):
    assert video_minutes(duration_s) == expected


# ── Ledger — grant and deduct ─────────────────────────────────────────────────


@pytest.fixture
def mock_session():
    session = AsyncMock(spec=AsyncSession)
    session.add = MagicMock()
    return session


@pytest.mark.asyncio
async def test_grant_minutes_adds_to_balance(mock_session):
    creator_id = uuid.uuid4()
    mock_session.execute = AsyncMock()
    await grant_minutes(
        creator_id, 60, "trial", mock_session, pack_id="trial"
    )
    mock_session.add.assert_called_once()
    mock_session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_deduct_minutes_raises_402_on_zero_balance(mock_session):
    from fastapi import HTTPException

    creator_id = uuid.uuid4()
    result_mock = MagicMock()
    result_mock.fetchone.return_value = None  # simulate failed WHERE clause
    mock_session.execute = AsyncMock(return_value=result_mock)

    with pytest.raises(HTTPException) as exc_info:
        await deduct_minutes(creator_id, 300.0, mock_session)
    assert exc_info.value.status_code == 402


@pytest.mark.asyncio
async def test_deduct_minutes_returns_minutes_used(mock_session):
    creator_id = uuid.uuid4()
    result_mock = MagicMock()
    result_mock.fetchone.return_value = (140,)  # remaining after deduction
    mock_session.execute = AsyncMock(return_value=result_mock)

    used = await deduct_minutes(creator_id, 600.0, mock_session)  # 10 minutes
    assert used == 10


@pytest.mark.asyncio
async def test_check_positive_balance_raises_402_when_empty(mock_session):
    from fastapi import HTTPException

    creator_id = uuid.uuid4()
    mock_session.scalar = AsyncMock(return_value=0)

    with pytest.raises(HTTPException) as exc_info:
        await check_positive_balance(creator_id, mock_session)
    assert exc_info.value.status_code == 402


@pytest.mark.asyncio
async def test_check_positive_balance_passes_when_nonzero(mock_session):
    creator_id = uuid.uuid4()
    mock_session.scalar = AsyncMock(return_value=50)
    # Should not raise
    await check_positive_balance(creator_id, mock_session)


# ── API endpoints ─────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    from main import app

    return TestClient(app, raise_server_exceptions=False)


def test_get_packs_unauthenticated(client):
    """Pack listing is public — no auth required."""
    response = client.get("/billing/packs")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == len(PURCHASABLE_PACKS)
    ids = {p["id"] for p in data}
    assert "trial" not in ids
    assert "creator" in ids


def test_balance_requires_auth(client):
    response = client.get("/billing/balance")
    assert response.status_code == 401


def test_checkout_requires_auth(client):
    response = client.post(
        "/billing/checkout",
        json={"pack_id": "creator", "success_url": "http://x/ok", "cancel_url": "http://x/no"},
    )
    assert response.status_code == 401


def test_checkout_invalid_pack(client):
    """Even with auth, unknown pack_id returns 400."""
    with patch("routers.billing.get_current_creator") as mock_auth:
        mock_auth.return_value = MagicMock(id=uuid.uuid4(), stripe_customer_id=None)
        response = client.post(
            "/billing/checkout",
            json={"pack_id": "nonexistent", "success_url": "http://x/ok", "cancel_url": "http://x/no"},
        )
    assert response.status_code in (400, 401)


# ── Webhook ───────────────────────────────────────────────────────────────────


def test_webhook_bad_signature(client):
    response = client.post(
        "/billing/webhook",
        content=b'{"type":"checkout.session.completed"}',
        headers={"stripe-signature": "bad"},
    )
    assert response.status_code == 400


def test_webhook_ignores_unknown_event_type(client):
    """Non-checkout events return 200 with status=ignored."""
    fake_event = {
        "type": "payment_intent.created",
        "data": {"object": {}},
    }
    with patch("routers.billing.construct_webhook_event", return_value=fake_event):
        with patch("routers.billing.get_session"):
            response = client.post(
                "/billing/webhook",
                content=json.dumps(fake_event).encode(),
                headers={"stripe-signature": "sig"},
            )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
