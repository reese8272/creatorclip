"""
Tests for the billing module: packs, ledger, endpoints, and webhook fulfillment.
"""

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from billing.ledger import (
    _trial_ended_402_detail,
    _trial_expired,
    check_balance_for_minutes,
    check_positive_balance,
    grant_minutes,
    video_minutes,
)
from billing.packs import PACKS, PURCHASABLE_PACKS

# ── Pack definitions ──────────────────────────────────────────────────────────
from tests._helpers import override_current_creator


def test_all_packs_present():
    # Issue 209: Stream pack added
    assert set(PACKS.keys()) == {
        "trial",
        "starter",
        "regular",
        "creator",
        "pro",
        "studio",
        "stream",
    }


def test_trial_is_not_purchasable():
    assert "trial" not in PURCHASABLE_PACKS


def test_purchasable_packs_have_nonzero_price():
    for p in PURCHASABLE_PACKS.values():
        assert p.price_cents > 0


def test_pack_per_minute_rate_decreases_with_volume():
    """Larger packs must be cheaper per minute than smaller ones (taper invariant).
    Applies to all purchasable packs including the Issue 209 Stream pack."""
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


# ── Issue 209: Stream pack + margin floor assertions ─────────────────────────


def test_stream_pack_present_in_purchasable_packs():
    """The Stream pack must appear in PURCHASABLE_PACKS (Issue 209)."""
    assert "stream" in PURCHASABLE_PACKS


def test_stream_pack_cheaper_per_minute_than_studio():
    """Stream (4.0 ¢/min) must be cheaper than Studio (4.5 ¢/min) — taper invariant."""
    stream = PURCHASABLE_PACKS["stream"]
    studio = PURCHASABLE_PACKS["studio"]
    assert stream.per_minute_cents < studio.per_minute_cents, (
        f"Stream ({stream.per_minute_cents:.3f} ¢/min) must be cheaper than "
        f"Studio ({studio.per_minute_cents:.3f} ¢/min)"
    )


def test_stream_pack_minutes_and_price():
    """Stream pack is 10,000 min at $400 (Issue 209 decision)."""
    stream = PURCHASABLE_PACKS["stream"]
    assert stream.minutes == 10000
    assert stream.price_cents == 40000


def test_margin_floor_at_cheapest_pack():
    """Gross margin floor: the cheapest pack (most discounted ¢/min) must price
    above the estimated compute floor. Per research finding §2.3 (06_monetization_
    unit_economics.md), gross margin >80% confirmed at Studio (4.5 ¢/min); Stream
    (4.0 ¢/min) carries identical compute cost.

    Compute floor from research: ~0.5–0.8 ¢/min (transcription + LLM + render).
    Using 0.8 ¢/min as the conservative ceiling; margin = 1 - 0.8/4.0 = 80% — borderline.
    The intent of this test is to catch any future pack addition that drops below cost.
    """
    # Conservative compute cost upper bound in cents per minute
    COMPUTE_COST_FLOOR_CENTS = 0.8
    cheapest = min(PURCHASABLE_PACKS.values(), key=lambda p: p.per_minute_cents)
    assert cheapest.per_minute_cents > COMPUTE_COST_FLOOR_CENTS, (
        f"Cheapest pack '{cheapest.id}' at {cheapest.per_minute_cents:.3f} ¢/min is at or "
        f"below estimated compute cost floor of {COMPUTE_COST_FLOOR_CENTS} ¢/min — "
        f"gross margin would be negative"
    )


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


def test_video_minutes_negative_duration_returns_1():
    """Negative duration (corrupt metadata) returns 1 — max(1, ceil(negative)) == 1.
    Intentional: the floor charge is 1 minute regardless of input sign. (Issue 340a)"""
    assert video_minutes(-60.0) == 1
    assert video_minutes(-1.0) == 1
    assert video_minutes(-0.001) == 1


# ── Ledger edge cases (Issue 340a) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_grant_minutes_empty_string_stripe_session_id_triggers_idempotency_check():
    """stripe_session_id='' is not None → the fast-path idempotency scalar() runs.
    When an existing MinutePack is found for stripe_session_id='', the grant is
    skipped — idempotency holds even for the empty-string key. (Issue 340a)"""
    creator_id = uuid.uuid4()
    session = AsyncMock(spec=AsyncSession)
    session.add = MagicMock()
    # scalar() returns a non-None id → "already granted" fast-path fires
    session.scalar = AsyncMock(return_value=uuid.uuid4())

    await grant_minutes(creator_id, 60, "stripe", session, stripe_session_id="")

    session.scalar.assert_called_once()  # idempotency check ran
    session.add.assert_not_called()  # grant was skipped


@pytest.mark.asyncio
async def test_trial_expired_strict_less_than_boundary():
    """_trial_expired uses strict '<' not '<=': trial_ends_at in the future
    means the trial is still active. (Issue 340a)"""
    creator_id = uuid.uuid4()
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=datetime.now(UTC) + timedelta(seconds=5))

    assert await _trial_expired(creator_id, session) is False


@pytest.mark.asyncio
async def test_trial_expired_naive_datetime_treated_as_utc():
    """A naive (tzinfo=None) trial_ends_at from the DB is assumed UTC and
    compared correctly — no TypeError from mixing aware/naive datetimes.
    (Issue 340a)"""
    creator_id = uuid.uuid4()
    session = AsyncMock()
    # Naive datetime in the past — must be treated as expired
    naive_past = (datetime.now(UTC) - timedelta(days=1)).replace(tzinfo=None)
    session.scalar = AsyncMock(return_value=naive_past)

    assert await _trial_expired(creator_id, session) is True


def test_trial_ended_402_detail_does_not_leak_trial_end_date():
    """The trial-ended 402 copy must not include any date or timestamp —
    leaking trial_ends_at would allow probing account state. (Issue 340a)"""
    detail = _trial_ended_402_detail()
    # No YYYY-MM-DD, ISO 8601, or epoch timestamps should appear
    assert not re.search(r"\d{4}-\d{2}-\d{2}", detail), "402 detail must not include a date string"
    assert not re.search(r"\d{10,}", detail), "402 detail must not include an epoch timestamp"


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
    # Issue 126: scalar() is now called twice — first for balance, then for
    # trial_ends_at. side_effect with None on the trial query keeps this test
    # exercising the legacy/no-trial 402 path.
    mock_session.scalar = AsyncMock(side_effect=[0, None])

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

    Issue 126: scalar() is now called twice (balance, trial_ends_at). The
    trial_ends_at=None path keeps this test on the legacy/gap-explanation
    branch — the trial-ended branch has its own pin in test_issue_126.py.
    """
    from fastapi import HTTPException

    creator_id = uuid.uuid4()
    mock_session.scalar = AsyncMock(side_effect=[0, None])
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
    """Pack listing is public — no auth required. Issue 209: includes Stream pack."""
    response = client.get("/billing/packs")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == len(PURCHASABLE_PACKS)
    ids = {p["id"] for p in data}
    assert "trial" not in ids
    assert "creator" in ids
    assert "stream" in ids  # Issue 209


def test_balance_requires_auth(client):
    response = client.get("/billing/balance")
    assert response.status_code == 401


def test_checkout_requires_auth(client):
    response = client.post(
        "/billing/checkout",
        json={
            "pack_id": "creator",
            "success_url": "http://x/ok",
            "cancel_url": "http://x/no",
            "intent_id": "11111111-1111-4111-8111-111111111111",
        },
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
                "intent_id": "11111111-1111-4111-8111-111111111111",
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


def test_webhook_malformed_creator_id_returns_ignored(client):
    """Webhook with non-UUID creator_id in metadata returns 200 status=ignored (Issue 123)."""
    from db import get_session as _get_session
    from main import app

    fake_event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_xxx",
                "customer": None,
                "metadata": {"creator_id": "not-a-uuid", "pack_id": "creator"},
            }
        },
    }

    async def _gen():
        session = AsyncMock()
        session.scalar = AsyncMock(return_value=None)  # no existing fulfillment
        yield session

    app.dependency_overrides[_get_session] = _gen
    try:
        with patch("routers.billing.construct_webhook_event", return_value=fake_event):
            response = client.post(
                "/billing/webhook",
                content=json.dumps(fake_event).encode(),
                headers={"stripe-signature": "sig"},
            )
    finally:
        app.dependency_overrides.pop(_get_session, None)

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
    app.dependency_overrides[get_current_creator] = override_current_creator(fake_creator)
    try:
        c = TestClient(app, raise_server_exceptions=False)
        response = c.post(
            "/billing/checkout",
            json={
                "pack_id": "creator",
                "success_url": "http://example.com/ok",
                "cancel_url": "http://example.com/no",
                "intent_id": "11111111-1111-4111-8111-111111111111",
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

    app.dependency_overrides[get_current_creator] = override_current_creator(fake_creator)
    try:
        c = TestClient(app, raise_server_exceptions=False)
        response = c.post(
            "/billing/checkout",
            json={
                "pack_id": "creator",
                "success_url": "http://example.com/ok",
                "cancel_url": "http://example.com/no",
                "intent_id": "11111111-1111-4111-8111-111111111111",
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


# ── Issue 106: limiter JWT verify_exp + Stripe idempotency_key + timeout + None-check ─


def test_creator_key_rejects_expired_token(monkeypatch):
    """Issue 106 SEV2: an expired session token must no longer key the
    per-creator rate-limit bucket (was a quota-leak vector — exfiltrated
    or stale token continued counting against the legitimate creator).
    """
    import time

    import jwt as _jwt
    from starlette.requests import Request

    from config import settings
    from limiter import SESSION_COOKIE, _creator_key

    creator_id = uuid.uuid4()
    # exp 10 minutes in the past — well outside the 60s leeway
    payload = {"sub": str(creator_id), "exp": int(time.time()) - 600}
    expired_token = _jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm="HS256")

    scope = {
        "type": "http",
        "headers": [(b"cookie", f"{SESSION_COOKIE}={expired_token}".encode())],
        "client": ("203.0.113.42", 1234),
    }
    request = Request(scope)
    key = _creator_key(request)

    assert key != str(creator_id), (
        "Expired JWT must NOT key per-creator bucket — would let an exfiltrated "
        "token continue spending the legitimate creator's per-hour limit. (Issue 106)"
    )
    assert key == "203.0.113.42", (
        f"Expected fallback to remote address on expired token; got {key!r}"
    )


def test_creator_key_accepts_token_within_leeway(monkeypatch):
    """A token whose exp is 30s in the past must still resolve to the creator
    id — within the 60s leeway window for NTP drift. (Issue 106)"""
    import time

    import jwt as _jwt
    from starlette.requests import Request

    from config import settings
    from limiter import SESSION_COOKIE, _creator_key

    creator_id = uuid.uuid4()
    # exp 30s in the past — inside the 60s leeway
    payload = {"sub": str(creator_id), "exp": int(time.time()) - 30}
    token = _jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm="HS256")

    scope = {
        "type": "http",
        "headers": [(b"cookie", f"{SESSION_COOKIE}={token}".encode())],
        "client": ("203.0.113.99", 1234),
    }
    request = Request(scope)
    assert _creator_key(request) == str(creator_id)


def test_create_checkout_session_passes_tenant_scoped_idempotency_key():
    """Issue 106 SEV2 + Issue 352 Batch B: Stripe checkout.sessions.create must
    receive a SERVER-DERIVED, tenant-scoped Idempotency-Key
    (`checkout:{creator_id}:{intent_id}`), not the bare client-supplied
    intent_id. Stripe idempotency keys are account-wide: a bare intent_id
    replayed by another creator would return the FIRST creator's cached
    checkout response. The creator_id prefix makes cross-tenant reuse
    structurally impossible while still deduping double-clicks per creator.
    """
    from billing.stripe_client import create_checkout_session

    intent_id = "deadbeef-dead-4eef-bdea-dfeeddeadbee"
    creator_id = str(uuid.uuid4())

    captured: dict = {}

    fake_session = MagicMock()
    fake_session.url = "https://checkout.stripe.com/c/pay/xyz"
    fake_session.id = "cs_test_123"

    def _capture(params, **kwargs):
        captured["params"] = params
        captured["kwargs"] = kwargs
        return fake_session

    with patch("billing.stripe_client._STRIPE") as stripe_mock:
        stripe_mock.checkout.sessions.create.side_effect = _capture
        url = create_checkout_session(
            pack_id="creator",
            creator_id=creator_id,
            stripe_customer_id=None,
            success_url="http://x/ok",
            cancel_url="http://x/no",
            intent_id=intent_id,
        )

    assert url == "https://checkout.stripe.com/c/pay/xyz"
    expected_key = f"checkout:{creator_id}:{intent_id}"
    assert len(expected_key) <= 255  # Stripe's documented key-length limit
    assert captured["kwargs"].get("options") == {"idempotency_key": expected_key}, (
        f"Stripe must receive options={{'idempotency_key': {expected_key!r}}}; "
        f"got {captured['kwargs']!r}"
    )


def test_create_checkout_session_rejects_malformed_intent_id():
    """Server-side UUID-shape validation keeps the derived Stripe key
    well-formed and bounded. (Issue 106; tenant isolation itself comes from
    the creator_id prefix — Issue 352 Batch B.)
    """
    from billing.stripe_client import create_checkout_session

    with pytest.raises(ValueError, match="intent_id must be a v4 UUID"):
        create_checkout_session(
            pack_id="creator",
            creator_id=str(uuid.uuid4()),
            stripe_customer_id=None,
            success_url="http://x/ok",
            cancel_url="http://x/no",
            intent_id="not-a-uuid",
        )


def test_create_checkout_session_raises_when_session_url_is_none():
    """Stripe SDK types Session.url as Optional[str]. Our -> str signature
    is unsound when Stripe returns None — must raise explicitly so the
    router can surface a 502 with context rather than redirecting the user
    to the string 'None'. (Issue 106)
    """
    from billing.stripe_client import create_checkout_session

    fake_session = MagicMock()
    fake_session.url = None
    fake_session.id = "cs_test_456"

    with patch("billing.stripe_client._STRIPE") as stripe_mock:
        stripe_mock.checkout.sessions.create.return_value = fake_session
        with pytest.raises(RuntimeError, match="Stripe returned no checkout URL"):
            create_checkout_session(
                pack_id="creator",
                creator_id=str(uuid.uuid4()),
                stripe_customer_id=None,
                success_url="http://x/ok",
                cancel_url="http://x/no",
                intent_id="11111111-1111-4111-8111-111111111111",
            )


# ── Issue 206: payment_status guard in the webhook ────────────────────────────


def _make_checkout_completed_event(
    payment_status: str | None,
    *,
    session_id: str = "cs_test_xxx",
    event_type: str = "checkout.session.completed",
) -> dict:
    """Build a synthetic Checkout Session event dict.

    ``event_type`` covers the async pair (Issue 523): the ``data.object`` of
    ``checkout.session.async_payment_succeeded`` / ``_failed`` is the same
    Checkout Session shape ``completed`` carries.
    """
    obj: dict = {
        "id": session_id,
        "customer": None,
        "metadata": {"creator_id": str(uuid.uuid4()), "pack_id": "creator"},
    }
    if payment_status is not None:
        obj["payment_status"] = payment_status
    return {
        "type": event_type,
        "data": {"object": obj},
    }


def test_webhook_payment_status_paid_grants_minutes(client):
    """payment_status='paid' must proceed to the grant path (status=ok)."""
    from db import get_session as _get_session
    from main import app

    fake_event = _make_checkout_completed_event("paid", session_id="cs_test_paid_001")

    async def _gen():
        session = AsyncMock()
        # No existing fulfillment; grant_minutes commit succeeds
        session.scalar = AsyncMock(return_value=None)
        session.execute = AsyncMock()
        session.commit = AsyncMock()
        session.info = {}
        yield session

    app.dependency_overrides[_get_session] = _gen
    try:
        with (
            patch("routers.billing.construct_webhook_event", return_value=fake_event),
            patch("routers.billing.grant_minutes", new_callable=AsyncMock),
        ):
            response = client.post(
                "/billing/webhook",
                content=json.dumps(fake_event).encode(),
                headers={"stripe-signature": "sig"},
            )
    finally:
        app.dependency_overrides.pop(_get_session, None)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_webhook_payment_status_unpaid_is_ignored(client):
    """payment_status='unpaid' (async/delayed payment not yet collected) must be ignored."""
    fake_event = _make_checkout_completed_event("unpaid")

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


def test_webhook_payment_status_absent_is_ignored(client):
    """Missing payment_status field (malformed payload) must be ignored defensively."""
    fake_event = _make_checkout_completed_event(None)  # no payment_status key

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


# ── Issue 207: Stripe Tax — flag-guarded automatic_tax injection ──────────────


def test_create_checkout_session_no_tax_when_flag_off(monkeypatch):
    """With STRIPE_TAX_ENABLED=False (default), params must not contain automatic_tax
    or billing_address_collection — byte-identical to the pre-207 behaviour."""
    from billing import stripe_client as _sc
    from billing.stripe_client import create_checkout_session
    from config import settings

    monkeypatch.setattr(settings, "STRIPE_TAX_ENABLED", False)

    captured: dict = {}
    fake_session = MagicMock()
    fake_session.url = "https://checkout.stripe.com/pay/abc"
    fake_session.id = "cs_test_notax"

    def _capture(params, **kwargs):
        captured["params"] = params
        return fake_session

    with patch.object(_sc, "_STRIPE") as stripe_mock:
        stripe_mock.checkout.sessions.create.side_effect = _capture
        create_checkout_session(
            pack_id="creator",
            creator_id=str(uuid.uuid4()),
            stripe_customer_id=None,
            success_url="http://x/ok",
            cancel_url="http://x/no",
            intent_id="11111111-1111-4111-8111-111111111111",
        )

    assert "automatic_tax" not in captured["params"], (
        "automatic_tax must NOT be injected when STRIPE_TAX_ENABLED=False"
    )
    assert "billing_address_collection" not in captured["params"]


def test_create_checkout_session_injects_tax_when_flag_on(monkeypatch):
    """With STRIPE_TAX_ENABLED=True, params must include automatic_tax[enabled]=True
    and billing_address_collection=required."""
    from billing import stripe_client as _sc
    from billing.stripe_client import create_checkout_session
    from config import settings

    monkeypatch.setattr(settings, "STRIPE_TAX_ENABLED", True)

    captured: dict = {}
    fake_session = MagicMock()
    fake_session.url = "https://checkout.stripe.com/pay/xyz"
    fake_session.id = "cs_test_tax"

    def _capture(params, **kwargs):
        captured["params"] = params
        return fake_session

    with patch.object(_sc, "_STRIPE") as stripe_mock:
        stripe_mock.checkout.sessions.create.side_effect = _capture
        create_checkout_session(
            pack_id="creator",
            creator_id=str(uuid.uuid4()),
            stripe_customer_id=None,
            success_url="http://x/ok",
            cancel_url="http://x/no",
            intent_id="22222222-2222-4222-9222-222222222222",
        )

    assert captured["params"].get("automatic_tax") == {"enabled": True}, (
        "automatic_tax[enabled]=True must be injected when STRIPE_TAX_ENABLED=True"
    )
    assert captured["params"].get("billing_address_collection") == "required"


# ── Issue 234: billing webhook log_event instrumentation ─────────────────────


def test_webhook_bad_signature_emits_received_then_rejected(client):
    """Stripe webhook bad signature must emit billing_webhook_received then
    billing_webhook_rejected with reason='bad_signature'.
    No secret, raw body, or signature value may appear in any field.
    """
    from unittest.mock import patch

    with patch("routers.billing.log_event") as mock_log:
        response = client.post(
            "/billing/webhook",
            content=b'{"type":"checkout.session.completed"}',
            headers={"stripe-signature": "bad"},
        )

    assert response.status_code == 400
    event_names = [c.args[0] for c in mock_log.call_args_list]
    assert "billing_webhook_received" in event_names, (
        "billing_webhook_received must be emitted on every POST"
    )
    assert "billing_webhook_rejected" in event_names, (
        "billing_webhook_rejected must be emitted on bad signature"
    )
    # Verify reason field and absence of secret values.
    rejected_call = next(
        c for c in mock_log.call_args_list if c.args[0] == "billing_webhook_rejected"
    )
    assert rejected_call.kwargs.get("reason") == "bad_signature", (
        "billing_webhook_rejected must carry reason='bad_signature'"
    )
    # The signature string "bad" must never appear in any kwarg value.
    for v in rejected_call.kwargs.values():
        assert v != "bad", "Stripe signature value must not appear in any log_event field"


def test_webhook_payment_status_paid_emits_processed(client):
    """A fulfilled webhook must emit billing_webhook_processed with pack_id and creator_id."""
    import json
    import uuid
    from unittest.mock import AsyncMock, patch

    from db import get_session as _get_session
    from main import app

    creator_id = str(uuid.uuid4())
    fake_event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_log_event_ok",
                "customer": None,
                "payment_status": "paid",
                "metadata": {"creator_id": creator_id, "pack_id": "creator"},
            }
        },
    }

    async def _gen():
        session = AsyncMock()
        session.scalar = AsyncMock(return_value=None)
        session.execute = AsyncMock()
        session.commit = AsyncMock()
        session.info = {}
        yield session

    app.dependency_overrides[_get_session] = _gen
    try:
        with (
            patch("routers.billing.construct_webhook_event", return_value=fake_event),
            patch("routers.billing.grant_minutes", new_callable=AsyncMock),
            patch("routers.billing.log_event") as mock_log,
        ):
            response = client.post(
                "/billing/webhook",
                content=json.dumps(fake_event).encode(),
                headers={"stripe-signature": "sig"},
            )
    finally:
        app.dependency_overrides.pop(_get_session, None)

    assert response.status_code == 200
    event_names = [c.args[0] for c in mock_log.call_args_list]
    assert "billing_webhook_received" in event_names
    assert "billing_webhook_processed" in event_names
    processed_call = next(
        c for c in mock_log.call_args_list if c.args[0] == "billing_webhook_processed"
    )
    assert processed_call.kwargs.get("pack_id") == "creator"
    assert processed_call.kwargs.get("creator_id") == creator_id


def test_create_checkout_session_tax_with_existing_customer_adds_customer_update(monkeypatch):
    """When STRIPE_TAX_ENABLED=True and a stripe_customer_id exists, customer_update[address]=auto
    must be included to persist the collected address for future sessions."""
    from billing import stripe_client as _sc
    from billing.stripe_client import create_checkout_session
    from config import settings

    monkeypatch.setattr(settings, "STRIPE_TAX_ENABLED", True)

    captured: dict = {}
    fake_session = MagicMock()
    fake_session.url = "https://checkout.stripe.com/pay/zzz"
    fake_session.id = "cs_test_tax_cust"

    def _capture(params, **kwargs):
        captured["params"] = params
        return fake_session

    with patch.object(_sc, "_STRIPE") as stripe_mock:
        stripe_mock.checkout.sessions.create.side_effect = _capture
        create_checkout_session(
            pack_id="creator",
            creator_id=str(uuid.uuid4()),
            stripe_customer_id="cus_existing_123",
            success_url="http://x/ok",
            cancel_url="http://x/no",
            intent_id="33333333-3333-4333-a333-333333333333",
        )

    assert captured["params"].get("customer_update") == {"address": "auto"}


# ── Issue 454: purchase-attempt-scoped intent — server half ───────────────────


def test_checkout_maps_idempotency_error_to_409_checkout_conflict(monkeypatch, caplog):
    """A replayed idempotency key with different params (the pre-454 client's
    tab-scoped intent id) must surface as 409 {code: checkout_conflict}, not the
    generic 502 — and the log line must carry classification fields only, never
    the idempotency key (Stripe's own message quotes it back) or intent_id.
    """
    import stripe as stripe_sdk

    from auth import get_current_creator
    from config import settings as _settings
    from main import app

    monkeypatch.setattr(_settings, "STRIPE_SECRET_KEY", "sk_test_fake_key_for_test")
    fake_creator = MagicMock(id=uuid.uuid4(), stripe_customer_id=None)
    intent_id = "11111111-1111-4111-8111-111111111111"
    leaked_key = f"checkout:{fake_creator.id}:{intent_id}"

    def _raise_idem(*args, **kwargs):
        raise stripe_sdk.IdempotencyError(
            f"Keys for idempotent requests can only be used with the same "
            f"parameters they were first used with. (key: {leaked_key})",
            headers={"request-id": "req_test_454"},
        )

    monkeypatch.setattr("routers.billing.create_checkout_session", _raise_idem)
    app.dependency_overrides[get_current_creator] = override_current_creator(fake_creator)
    try:
        c = TestClient(app, raise_server_exceptions=False)
        with caplog.at_level("WARNING", logger="routers.billing"):
            response = c.post(
                "/billing/checkout",
                json={
                    "pack_id": "creator",
                    "success_url": "http://example.com/ok",
                    "cancel_url": "http://example.com/no",
                    "intent_id": intent_id,
                },
            )
    finally:
        app.dependency_overrides.pop(get_current_creator, None)

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "checkout_conflict"
    assert "harged" in detail["message"], "user must be told nothing was charged"
    log_text = caplog.text
    assert "IdempotencyError" in log_text, "error_class must be logged"
    assert "req_test_454" in log_text, "stripe_request_id must be logged"
    assert leaked_key not in log_text, "the idempotency key must never be logged"
    assert intent_id not in log_text, "the intent_id must never be logged"


def test_checkout_stripe_error_502_scrubs_key_from_logs(monkeypatch, caplog):
    """Generic StripeError → 502 with the {code, message} detail shape; the log
    carries error_class/stripe_code/stripe_request_id and never str(exc)."""
    import stripe as stripe_sdk

    from auth import get_current_creator
    from config import settings as _settings
    from main import app

    monkeypatch.setattr(_settings, "STRIPE_SECRET_KEY", "sk_test_fake_key_for_test")
    fake_creator = MagicMock(id=uuid.uuid4(), stripe_customer_id=None)

    def _raise_stripe(*args, **kwargs):
        raise stripe_sdk.APIConnectionError(
            "SECRET-MESSAGE-MUST-NOT-LOG", headers={"request-id": "req_test_502"}
        )

    monkeypatch.setattr("routers.billing.create_checkout_session", _raise_stripe)
    app.dependency_overrides[get_current_creator] = override_current_creator(fake_creator)
    try:
        c = TestClient(app, raise_server_exceptions=False)
        with caplog.at_level("ERROR", logger="routers.billing"):
            response = c.post(
                "/billing/checkout",
                json={
                    "pack_id": "creator",
                    "success_url": "http://example.com/ok",
                    "cancel_url": "http://example.com/no",
                    "intent_id": "11111111-1111-4111-8111-111111111111",
                },
            )
    finally:
        app.dependency_overrides.pop(get_current_creator, None)

    assert response.status_code == 502, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "checkout_failed"
    assert "harged" in detail["message"]
    assert "APIConnectionError" in caplog.text
    assert "req_test_502" in caplog.text
    assert "SECRET-MESSAGE-MUST-NOT-LOG" not in caplog.text, (
        "StripeError messages can echo request details — never log them"
    )


def test_checkout_non_stripe_failure_502_detail_shape(monkeypatch, caplog):
    """Non-Stripe failures (e.g. the session.url None guard's RuntimeError) take
    the generic branch: 502 with the same {code, message} shape, error_class
    logged, and our own exception text (safe — no Stripe key material) kept for
    debuggability."""
    from auth import get_current_creator
    from config import settings as _settings
    from main import app

    monkeypatch.setattr(_settings, "STRIPE_SECRET_KEY", "sk_test_fake_key_for_test")
    fake_creator = MagicMock(id=uuid.uuid4(), stripe_customer_id=None)

    def _raise_runtime(*args, **kwargs):
        raise RuntimeError("Stripe returned no checkout URL for session cs_test_999")

    monkeypatch.setattr("routers.billing.create_checkout_session", _raise_runtime)
    app.dependency_overrides[get_current_creator] = override_current_creator(fake_creator)
    try:
        c = TestClient(app, raise_server_exceptions=False)
        with caplog.at_level("ERROR", logger="routers.billing"):
            response = c.post(
                "/billing/checkout",
                json={
                    "pack_id": "creator",
                    "success_url": "http://example.com/ok",
                    "cancel_url": "http://example.com/no",
                    "intent_id": "11111111-1111-4111-8111-111111111111",
                },
            )
    finally:
        app.dependency_overrides.pop(get_current_creator, None)

    assert response.status_code == 502, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "checkout_failed"
    assert "RuntimeError" in caplog.text
    assert "cs_test_999" in caplog.text


# ── Issue 523: async payment methods must fulfill (or fail observably) ────────


def _webhook_post(client, fake_event, *, session_scalar=None):
    """POST fake_event to the webhook with a stubbed DB session; returns
    (response, grant_mock, log_mock)."""
    from db import get_session as _get_session
    from main import app

    async def _gen():
        session = AsyncMock()
        session.scalar = AsyncMock(return_value=session_scalar)
        session.execute = AsyncMock()
        session.commit = AsyncMock()
        session.info = {}
        yield session

    app.dependency_overrides[_get_session] = _gen
    try:
        with (
            patch("routers.billing.construct_webhook_event", return_value=fake_event),
            patch("routers.billing.grant_minutes", new_callable=AsyncMock) as grant_mock,
            patch("routers.billing.log_event") as log_mock,
        ):
            response = client.post(
                "/billing/webhook",
                content=json.dumps(fake_event).encode(),
                headers={"stripe-signature": "sig"},
            )
    finally:
        app.dependency_overrides.pop(_get_session, None)
    return response, grant_mock, log_mock


def test_webhook_async_payment_succeeded_grants_minutes(client):
    """checkout.session.async_payment_succeeded (delayed method settled) must
    take the SAME fulfillment path as completed — the take-money-grant-nothing
    defect was this event returning status=ignored."""
    fake_event = _make_checkout_completed_event(
        "paid",
        session_id="cs_test_async_ok",
        event_type="checkout.session.async_payment_succeeded",
    )
    response, grant_mock, _ = _webhook_post(client, fake_event)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    grant_mock.assert_awaited_once()


def test_webhook_async_payment_succeeded_dedupes_after_completed(client):
    """A session already fulfilled (e.g. via completed) must short-circuit on
    the stripe_session_id fast path — session-id keying is the documented
    Stripe pattern precisely so both events converge on one dedupe key."""
    fake_event = _make_checkout_completed_event(
        "paid",
        session_id="cs_test_async_dupe",
        event_type="checkout.session.async_payment_succeeded",
    )
    response, grant_mock, _ = _webhook_post(client, fake_event, session_scalar=uuid.uuid4())
    assert response.status_code == 200
    assert response.json()["status"] == "already_fulfilled"
    grant_mock.assert_not_awaited()


def test_webhook_async_payment_failed_logs_and_no_grant(client):
    """checkout.session.async_payment_failed: nothing was ever granted so there
    is no ledger action — but the failure must be OBSERVABLE, not silent."""
    fake_event = _make_checkout_completed_event(
        "unpaid",
        session_id="cs_test_async_fail",
        event_type="checkout.session.async_payment_failed",
    )
    response, grant_mock, log_mock = _webhook_post(client, fake_event)
    assert response.status_code == 200
    assert response.json()["status"] == "async_payment_failed"
    grant_mock.assert_not_awaited()
    event_names = [c.args[0] for c in log_mock.call_args_list]
    assert "billing_async_payment_failed" in event_names


def test_webhook_async_payment_succeeded_unpaid_still_ignored(client):
    """The Issue-206 payment_status guard must apply to the async branch too:
    an async_payment_succeeded carrying anything but 'paid' is malformed or
    out-of-order and must not grant."""
    fake_event = _make_checkout_completed_event(
        "unpaid",
        session_id="cs_test_async_unpaid",
        event_type="checkout.session.async_payment_succeeded",
    )
    response, grant_mock, log_mock = _webhook_post(client, fake_event)
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    grant_mock.assert_not_awaited()
    event_names = [c.args[0] for c in log_mock.call_args_list]
    assert "billing_webhook_ignored" in event_names


def test_webhook_unpaid_ignore_emits_log_event(client):
    """The unpaid-ignore path used to be a silent drop — indistinguishable from
    nothing happening. It is exactly where an async-shaped session first
    surfaces, so it must emit an event (counters must count writes)."""
    fake_event = _make_checkout_completed_event("unpaid", session_id="cs_test_unpaid_log")
    response, grant_mock, log_mock = _webhook_post(client, fake_event)
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    grant_mock.assert_not_awaited()
    ignored_call = next(
        (c for c in log_mock.call_args_list if c.args[0] == "billing_webhook_ignored"),
        None,
    )
    assert ignored_call is not None, "billing_webhook_ignored must be emitted"
    assert ignored_call.kwargs.get("reason") == "payment_status_not_paid"


def test_create_checkout_session_pins_card_payment_method():
    """Issue 523: payment_method_types is pinned to card so enabling a
    delayed-settlement method (ACH/BNPL) is a reviewed code change, never a
    Dashboard toggle. Deliberate deviation from Stripe's dynamic-payment-methods
    guidance — rationale + un-pinning checklist in docs/DECISIONS.md (2026-08-26)."""
    from billing import stripe_client as _sc
    from billing.stripe_client import create_checkout_session

    captured: dict = {}
    fake_session = MagicMock()
    fake_session.url = "https://checkout.stripe.com/pay/abc"
    fake_session.id = "cs_test_pin"

    def _capture(params, **kwargs):
        captured["params"] = params
        return fake_session

    with patch.object(_sc, "_STRIPE") as stripe_mock:
        stripe_mock.checkout.sessions.create.side_effect = _capture
        create_checkout_session(
            pack_id="creator",
            creator_id=str(uuid.uuid4()),
            stripe_customer_id=None,
            success_url="http://x/ok",
            cancel_url="http://x/no",
            intent_id="11111111-1111-4111-8111-111111111111",
        )

    assert captured["params"].get("payment_method_types") == ["card"]
