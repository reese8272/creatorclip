"""
Integration tests for billing race conditions and Stripe webhook idempotency — Issue 49.

Verifies four load-bearing properties that AsyncMock cannot reproduce:
1. Concurrent deduct_for_video calls on a balance of 5 with two 3-minute requests
   never both succeed — row-level lock semantics guarantee exactly one wins.
2. Duplicate Stripe webhooks with the same stripe_session_id are fulfilled exactly once.
3. Webhooks with an unknown pack_id are gracefully ignored (no MinutePack row created).
4. Webhooks missing required metadata keys are gracefully ignored (no MinutePack row created).

NOTE — webhook response-code finding:
  The webhook handler returns HTTP 200 with {"status": "ignored"} for both unknown
  pack_id and missing metadata, NOT a 4xx. This is intentional per Stripe best practice:
  return 200 so Stripe does not retry; handle the error internally via logging.
  Tests 3 and 4 assert 200, documenting the actual behavior.

Requires a running Postgres with Alembic schema applied (see docker-compose.yml).
"""

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from billing.ledger import deduct_for_video
from config import settings
from models import Creator, MinuteDeduction, MinutePack, OnboardingState

# ── Shared DB fixture (mirrors test_billing_idempotency.py pattern) ────────────


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


# ── Seed / cleanup helpers ─────────────────────────────────────────────────────


async def _seed_creator(session: AsyncSession, *, minutes_balance: int) -> Creator:
    creator = Creator(
        google_sub=f"test_billing_race_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_race_{uuid.uuid4().hex[:6]}",
        channel_title="Race Test Channel",
        onboarding_state=OnboardingState.active,
        minutes_balance=minutes_balance,
    )
    session.add(creator)
    await session.flush()
    await session.commit()
    return creator


async def _seed_video(session: AsyncSession, creator_id: uuid.UUID) -> uuid.UUID:
    """Insert a minimal video row and return its id."""
    from models import IngestStatus, Video, VideoKind

    video = Video(
        creator_id=creator_id,
        youtube_video_id=f"vid_race_{uuid.uuid4().hex[:8]}",
        kind=VideoKind.long,
        ingest_status=IngestStatus.running,
        duration_s=180.0,  # 3 minutes
    )
    session.add(video)
    await session.flush()
    await session.commit()
    return video.id


async def _cleanup_creator(session: AsyncSession, creator_id: uuid.UUID) -> None:
    # CASCADE on creators → videos → minute_deductions handles children.
    await session.execute(delete(Creator).where(Creator.id == creator_id))
    await session.commit()


async def _balance(session: AsyncSession, creator_id: uuid.UUID) -> int:
    return await session.scalar(select(Creator.minutes_balance).where(Creator.id == creator_id))


async def _deduction_count(session: AsyncSession, video_id: uuid.UUID) -> int:
    return await session.scalar(
        select(func.count(MinuteDeduction.id)).where(MinuteDeduction.video_id == video_id)
    )


async def _minute_pack_count(session: AsyncSession, stripe_session_id: str) -> int:
    return await session.scalar(
        select(func.count(MinutePack.id)).where(MinutePack.stripe_session_id == stripe_session_id)
    )


# ── Webhook helper ─────────────────────────────────────────────────────────────


def _make_webhook_event(
    *,
    stripe_session_id: str,
    creator_id: str | None = None,
    pack_id: str | None = None,
    include_metadata: bool = True,
    payment_status: str | None = "paid",
) -> dict:
    """Build a minimal checkout.session.completed event dict.

    ``payment_status`` defaults to ``"paid"`` to satisfy the Issue 206
    fulfillment guard (``routers/billing.py`` rejects anything other than
    ``"paid"``). Pass ``None`` / ``"unpaid"`` to exercise the ignore path.
    """
    meta: dict = {}
    if include_metadata:
        if creator_id is not None:
            meta["creator_id"] = creator_id
        if pack_id is not None:
            meta["pack_id"] = pack_id

    obj: dict = {
        "id": stripe_session_id,
        "customer": None,
        "metadata": meta if include_metadata else None,
    }
    if payment_status is not None:
        obj["payment_status"] = payment_status

    return {
        "type": "checkout.session.completed",
        "data": {"object": obj},
    }


# ── Test 1: concurrent deduct race ────────────────────────────────────────────


@pytest.mark.integration
async def test_concurrent_deduct_race(db_session: AsyncSession):
    """Two goroutines racing to deduct 3 min from a balance of 5 — exactly one wins.

    Validates that the SAVEPOINT + UNIQUE(video_id) constraint prevents both from
    succeeding, and that the balance never drops below zero.
    """
    creator = await _seed_creator(db_session, minutes_balance=5)
    video_id = await _seed_video(db_session, creator.id)

    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _attempt() -> int:
        # Each task uses its own session — simulates two Celery workers.
        async with sf() as session:
            try:
                n = await deduct_for_video(video_id, creator.id, 180.0, session)
                await session.commit()
                return n
            except Exception:
                await session.rollback()
                return -1  # 402 or other failure

    try:
        results = await asyncio.gather(_attempt(), _attempt())

        # Exactly one deduction of 3 minutes must have landed.
        successes = [r for r in results if r == 3]
        assert len(successes) == 1, (
            f"Expected exactly one successful deduction; got results={results}"
        )

        # Balance must not have gone below zero, and must be exactly 5 - 3 = 2.
        async with sf() as fresh:
            final_balance = await _balance(fresh, creator.id)
            assert final_balance >= 0, "Balance went negative — double-spend race occurred"
            assert final_balance == 2, f"Expected balance=2, got {final_balance}"

            count = await _deduction_count(fresh, video_id)
            assert count == 1, f"Expected exactly 1 MinuteDeduction row, got {count}"
    finally:
        await engine.dispose()
        await _cleanup_creator(db_session, creator.id)


# ── Test 2: webhook idempotency — same stripe_session_id ──────────────────────


@pytest.mark.integration
async def test_webhook_idempotency_same_session_id(db_session: AsyncSession):
    """Calling the webhook handler twice with the same stripe_session_id grants once.

    The application-level idempotency check (SELECT before INSERT) prevents a second
    grant. The UNIQUE(stripe_session_id) constraint on minute_packs is the DB-level
    safety net against a concurrent race on this path.

    Important: do NOT override get_session — TestClient runs the handler in its own
    event loop, and sharing the test's AsyncSession across loops triggers
    SQLAlchemy's MissingGreenlet. Let production get_session() build a fresh
    AsyncSessionLocal-backed session per request. For post-call verification, use
    a fresh session from AsyncSessionLocal directly.
    """
    from db import AsyncSessionLocal
    from main import app

    creator = await _seed_creator(db_session, minutes_balance=0)
    stripe_session_id = f"cs_test_{uuid.uuid4().hex}"
    event = _make_webhook_event(
        stripe_session_id=stripe_session_id,
        creator_id=str(creator.id),
        pack_id="starter",
    )

    try:
        with (
            patch("routers.billing.construct_webhook_event", return_value=event),
            TestClient(app) as client,
        ):
            r1 = client.post(
                "/billing/webhook",
                content=json.dumps(event).encode(),
                headers={"stripe-signature": "mocked"},
            )

            r2 = client.post(
                "/billing/webhook",
                content=json.dumps(event).encode(),
                headers={"stripe-signature": "mocked"},
            )

        assert r1.status_code == 200, f"First webhook call failed: {r1.text}"
        assert r1.json()["status"] == "ok"
        assert r2.status_code == 200, f"Second webhook call failed: {r2.text}"
        assert r2.json()["status"] == "already_fulfilled"

        # Exactly one MinutePack row for this Stripe session.
        async with AsyncSessionLocal() as verify:
            count = await _minute_pack_count(verify, stripe_session_id)
            assert count == 1, (
                f"Expected 1 MinutePack row for stripe_session_id={stripe_session_id!r}, "
                f"got {count}"
            )
    finally:
        await _cleanup_creator(db_session, creator.id)


# ── Test 3: webhook unknown pack_id ───────────────────────────────────────────


@pytest.mark.integration
async def test_webhook_unknown_pack_id(db_session: AsyncSession):
    """Webhook with an unknown pack_id is gracefully ignored — no MinutePack row created.

    FINDING: The handler returns HTTP 200 {"status": "ignored"}, NOT a 4xx.
    This follows Stripe best practice: always return 2xx so Stripe does not retry the
    webhook; handle the anomaly internally via logger.error().
    """
    from db import AsyncSessionLocal
    from main import app

    creator = await _seed_creator(db_session, minutes_balance=0)
    stripe_session_id = f"cs_test_{uuid.uuid4().hex}"
    event = _make_webhook_event(
        stripe_session_id=stripe_session_id,
        creator_id=str(creator.id),
        pack_id="nonexistent_pack_xyz",
    )

    try:
        with (
            patch("routers.billing.construct_webhook_event", return_value=event),
            TestClient(app) as client,
        ):
            response = client.post(
                "/billing/webhook",
                content=json.dumps(event).encode(),
                headers={"stripe-signature": "mocked"},
            )

        # Handler returns 200 "ignored", not a 4xx — see module docstring for rationale.
        assert response.status_code == 200
        assert response.json()["status"] == "ignored"

        # No MinutePack row was created.
        async with AsyncSessionLocal() as verify:
            count = await _minute_pack_count(verify, stripe_session_id)
            assert count == 0, f"Expected 0 MinutePack rows, got {count}"
    finally:
        await _cleanup_creator(db_session, creator.id)


# ── Test 4: webhook missing metadata ──────────────────────────────────────────


@pytest.mark.integration
async def test_webhook_missing_metadata(db_session: AsyncSession):
    """Webhook missing creator_id/pack_id metadata is gracefully ignored.

    Covers both: (a) metadata key absent from the dict, and (b) metadata field null.
    FINDING: Returns HTTP 200 {"status": "ignored"} — same reasoning as Test 3.
    """
    from db import AsyncSessionLocal
    from main import app

    creator = await _seed_creator(db_session, minutes_balance=0)
    stripe_session_id = f"cs_test_{uuid.uuid4().hex}"

    # Case (a): metadata present but missing both required keys.
    event_missing_keys = _make_webhook_event(
        stripe_session_id=stripe_session_id,
        # creator_id and pack_id omitted intentionally
    )
    # Case (b): metadata field is None entirely.
    event_null_meta = _make_webhook_event(
        stripe_session_id=f"cs_test_{uuid.uuid4().hex}",
        include_metadata=False,
    )

    try:
        with (
            patch("routers.billing.construct_webhook_event") as mock_event,
            TestClient(app) as client,
        ):
            mock_event.return_value = event_missing_keys
            r_missing = client.post(
                "/billing/webhook",
                content=b"{}",
                headers={"stripe-signature": "mocked"},
            )

            mock_event.return_value = event_null_meta
            r_null = client.post(
                "/billing/webhook",
                content=b"{}",
                headers={"stripe-signature": "mocked"},
            )

        assert r_missing.status_code == 200
        assert r_missing.json()["status"] == "ignored"
        assert r_null.status_code == 200
        assert r_null.json()["status"] == "ignored"

        # Neither call should have created any MinutePack rows for this creator.
        async with AsyncSessionLocal() as verify:
            row_count = await verify.scalar(
                select(func.count(MinutePack.id)).where(MinutePack.creator_id == creator.id)
            )
            assert row_count == 0, f"Expected 0 MinutePack rows, got {row_count}"
    finally:
        await _cleanup_creator(db_session, creator.id)


# ── Test 6: webhook fast-path idempotency uses RLS-correct session ──────────


@pytest.mark.integration
async def test_webhook_fast_path_short_circuits_before_grant(
    db_session: AsyncSession,
):
    """The webhook idempotency fast-path (SELECT MinutePack WHERE stripe_session_id = ?)
    must actually catch a duplicate delivery — the second call should NOT reach
    grant_minutes(). The earlier test_webhook_idempotency_same_session_id only
    asserts the END state (one pack, status=already_fulfilled), which is also
    satisfied when grant_minutes() catches the UNIQUE-constraint IntegrityError.

    Regression for 2026-06-08 SEV1: when the webhook ran on an app-role session
    that hadn't stamped session.info["creator_id"], RLS evaluated to
    `creator_id = NULL` → the SELECT returned 0 rows → fast-path never fired,
    and grant_minutes was reached on every replay. Integrity held by accident.

    This test spies on grant_minutes and asserts it's called exactly ONCE across
    two identical webhook deliveries.
    """
    from db import AsyncSessionLocal
    from main import app

    creator = await _seed_creator(db_session, minutes_balance=0)
    stripe_session_id = f"cs_test_{uuid.uuid4().hex}"
    event = _make_webhook_event(
        stripe_session_id=stripe_session_id,
        creator_id=str(creator.id),
        pack_id="starter",
    )

    # Wrap the real grant_minutes so behavior is unchanged on the first call,
    # then count invocations across both deliveries.
    from billing import ledger as ledger_mod

    real_grant = ledger_mod.grant_minutes
    spy = AsyncMock(side_effect=real_grant)

    try:
        with (
            patch("routers.billing.construct_webhook_event", return_value=event),
            patch("routers.billing.grant_minutes", spy),
            TestClient(app) as client,
        ):
            r1 = client.post(
                "/billing/webhook",
                content=json.dumps(event).encode(),
                headers={"stripe-signature": "mocked"},
            )
            r2 = client.post(
                "/billing/webhook",
                content=json.dumps(event).encode(),
                headers={"stripe-signature": "mocked"},
            )

        assert r1.status_code == 200, r1.text
        assert r1.json()["status"] == "ok"
        assert r2.status_code == 200, r2.text
        assert r2.json()["status"] == "already_fulfilled"

        # The load-bearing assertion: grant_minutes invoked exactly once.
        # If the fast-path is dead under RLS, this count would be 2 even
        # though the END-state assertion (one MinutePack row) still passes.
        assert spy.await_count == 1, (
            f"grant_minutes called {spy.await_count} times — the fast-path "
            "short-circuit failed to fire on the duplicate webhook. The "
            "RLS-context stamp on session.info['creator_id'] is missing."
        )

        async with AsyncSessionLocal() as verify:
            count = await _minute_pack_count(verify, stripe_session_id)
            assert count == 1
    finally:
        await _cleanup_creator(db_session, creator.id)
