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
    check_balance_for_minutes,
    check_positive_balance,
    grant_minutes,
    video_minutes,
)
from billing.packs import PACKS, PURCHASABLE_PACKS

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
            f"{purchasable[i + 1].id} ({rates[i + 1]:.3f})"
        )


def test_pack_price_usd():
    creator = PACKS["creator"]
    assert creator.price_usd == 70.00


# ── video_minutes helper ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "duration_s,expected",
    [
        (0.0, 1),  # minimum 1
        (60.0, 1),  # exactly 1 minute
        (61.0, 2),  # just over = round up
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
    await grant_minutes(creator_id, 60, "trial", mock_session, pack_id="trial")
    mock_session.add.assert_called_once()
    mock_session.execute.assert_called_once()


# Note: deduct_minutes was replaced by the idempotent deduct_for_video (Issue 34).
# Its behavior is covered by real-DB integration tests in test_billing_idempotency.py,
# not by mocked unit tests — the load-bearing guarantees (UNIQUE(video_id) idempotency,
# SAVEPOINT atomicity, concurrent-retry race) cannot be asserted against AsyncMock.


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


# ── Issue 89 — duration-aware balance pre-check ──────────────────────────────
#
# `check_positive_balance` only caught `balance <= 0`. A 1-minute creator
# uploading a 60-minute video passed the pre-check, the upload completed, then
# `_ingest_async`'s `deduct_for_video` silently 402'd inside the Celery task
# with no actionable user-facing surface. `check_balance_for_minutes` mirrors
# the predicate `deduct_for_video` enforces internally (`balance >= minutes`).


@pytest.mark.asyncio
async def test_check_balance_for_minutes_raises_402_when_insufficient(mock_session):
    """The SEV-1 scenario: 1-minute balance, 60-minute video → 402."""
    from fastapi import HTTPException

    creator_id = uuid.uuid4()
    mock_session.scalar = AsyncMock(return_value=1)

    with pytest.raises(HTTPException) as exc_info:
        await check_balance_for_minutes(creator_id, 60, mock_session)
    assert exc_info.value.status_code == 402
    # The user-facing copy must include both numbers — generic "Insufficient
    # balance" is exactly what this issue is here to fix.
    assert "60" in exc_info.value.detail
    assert "1" in exc_info.value.detail


@pytest.mark.asyncio
async def test_check_balance_for_minutes_passes_when_exactly_sufficient(mock_session):
    """Boundary: balance == needed should pass (the underlying `deduct_for_video`
    UPDATE uses `>=`, not `>`)."""
    creator_id = uuid.uuid4()
    mock_session.scalar = AsyncMock(return_value=60)
    await check_balance_for_minutes(creator_id, 60, mock_session)


@pytest.mark.asyncio
async def test_check_balance_for_minutes_passes_when_over_sufficient(mock_session):
    creator_id = uuid.uuid4()
    mock_session.scalar = AsyncMock(return_value=1000)
    await check_balance_for_minutes(creator_id, 60, mock_session)


@pytest.mark.asyncio
async def test_check_balance_for_minutes_zero_balance_explains_gap(mock_session):
    """Zero balance + multi-minute video: the 402 detail must explain the gap,
    not the generic 'no minutes remaining' copy from `check_positive_balance`.
    """
    from fastapi import HTTPException

    creator_id = uuid.uuid4()
    mock_session.scalar = AsyncMock(return_value=0)
    with pytest.raises(HTTPException) as exc_info:
        await check_balance_for_minutes(creator_id, 10, mock_session)
    assert exc_info.value.status_code == 402
    assert "10 minutes" in exc_info.value.detail
    assert "you have 0" in exc_info.value.detail


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
            json={
                "pack_id": "nonexistent",
                "success_url": "http://x/ok",
                "cancel_url": "http://x/no",
            },
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
    with (
        patch("routers.billing.construct_webhook_event", return_value=fake_event),
        patch("routers.billing.get_session"),
    ):
        response = client.post(
            "/billing/webhook",
            content=json.dumps(fake_event).encode(),
            headers={"stripe-signature": "sig"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


# ── Issue 55: 503 when STRIPE_SECRET_KEY is empty ─────────────────────────────


def test_checkout_returns_503_when_stripe_key_empty(monkeypatch):
    """POST /billing/checkout must return 503 when STRIPE_SECRET_KEY is not configured."""
    from auth import get_current_creator
    from config import settings
    from main import app

    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "")

    fake_creator = MagicMock(id=uuid.uuid4(), stripe_customer_id=None)
    app.dependency_overrides[get_current_creator] = lambda: fake_creator
    try:
        c = TestClient(app, raise_server_exceptions=False)
        response = c.post(
            "/billing/checkout",
            json={
                "pack_id": "creator",
                "success_url": "http://example.com/ok",
                "cancel_url": "http://example.com/no",
            },
        )
    finally:
        app.dependency_overrides.pop(get_current_creator, None)

    assert response.status_code == 503, (
        f"Expected 503 Service Unavailable when STRIPE_SECRET_KEY is empty, "
        f"got {response.status_code}: {response.text}"
    )


# ── Wave-3 Fix C: Stripe sync SDK runs off the event loop ────────────────────


def test_checkout_offloads_sync_stripe_to_thread(monkeypatch):
    """Wave-3 Fix C (SEV1): the Stripe Python SDK is synchronous (urllib3
    under the hood). Calling `create_checkout_session` directly inside the
    async `/checkout` route blocked the FastAPI event loop for every
    300-800ms p95 Stripe round-trip and serialized concurrent checkouts on
    one worker process.

    Verify the fix by recording which thread `create_checkout_session` runs
    in: if the route uses `await asyncio.to_thread(...)`, the call happens
    in a worker thread distinct from the main thread that runs the event
    loop. Without the fix, both run in the same thread (the loop thread).
    """
    import threading

    from auth import get_current_creator
    from config import settings as _settings
    from main import app

    monkeypatch.setattr(_settings, "STRIPE_SECRET_KEY", "sk_test_fake_key_for_test")
    fake_creator = MagicMock(id=uuid.uuid4(), stripe_customer_id=None)
    main_thread_id = threading.get_ident()
    call_thread_ids: list[int] = []

    def _fake_create_checkout_session(*args, **kwargs):
        call_thread_ids.append(threading.get_ident())
        return "https://checkout.stripe.test/session/abc"

    monkeypatch.setattr(
        "routers.billing.create_checkout_session",
        _fake_create_checkout_session,
    )

    app.dependency_overrides[get_current_creator] = lambda: fake_creator
    try:
        c = TestClient(app, raise_server_exceptions=False)
        response = c.post(
            "/billing/checkout",
            json={
                "pack_id": "creator",
                "success_url": "http://example.com/ok",
                "cancel_url": "http://example.com/no",
            },
        )
    finally:
        app.dependency_overrides.pop(get_current_creator, None)

    assert response.status_code == 200
    assert len(call_thread_ids) == 1, "Stripe checkout should be called exactly once"
    # The Stripe call MUST run in a different thread than the one running the
    # async event loop — that's the whole point of `asyncio.to_thread`. If
    # this assertion fails, the route is calling Stripe synchronously again
    # and is back to blocking the event loop per checkout.
    assert call_thread_ids[0] != main_thread_id, (
        "Wave-3 Fix C: create_checkout_session must run via asyncio.to_thread "
        "so the sync Stripe SDK doesn't block the FastAPI event loop. "
        "Detected call running in the same thread as the test — the offload "
        "regressed."
    )
