"""Integration tests for the event_logs sink (Issue 151).

Needs a real Postgres (the no-DB-mocking rule) — runs in the integration CI job.
Covers: UI events persist + are redacted, the request middleware records backend
events, and /api/logs/me enforces per-creator isolation.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from auth import SESSION_COOKIE, create_session_token
from config import settings
from models import Creator, EventLog, OnboardingState


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed_creator(session: AsyncSession) -> Creator:
    creator = Creator(
        google_sub=f"evlog_{uuid.uuid4().hex[:12]}",
        channel_id=f"UC_{uuid.uuid4().hex[:8]}",
        channel_title="Event Log Test",
        email="evlog@example.com",
        onboarding_state=OnboardingState.active,
        minutes_balance=60,
    )
    session.add(creator)
    await session.commit()
    return creator


def _cookie(creator: Creator) -> dict:
    return {SESSION_COOKIE: create_session_token(creator.id)}


@pytest.mark.integration
async def test_ui_activity_persists_and_redacts(client, db_session):
    creator = await _seed_creator(db_session)
    resp = client.post(
        "/api/activity",
        json={
            "page": "/static/profile.html",
            "event_type": "click",
            "target": "rebuild-dna",
            "extra": {"email": "leak@example.com", "note": "ok"},
        },
        cookies=_cookie(creator),
    )
    assert resp.status_code == 204

    row = (
        (
            await db_session.execute(
                select(EventLog)
                .where(EventLog.creator_id == creator.id, EventLog.source == "ui")
                .order_by(EventLog.at.desc())
            )
        )
        .scalars()
        .first()
    )
    assert row is not None
    assert row.event == "click"
    assert row.target == "rebuild-dna"
    # The PII guard: email masked, benign field kept.
    assert row.extra["email"] == "[redacted]"
    assert row.extra["note"] == "ok"


@pytest.mark.integration
async def test_http_request_recorded_by_middleware(client, db_session):
    creator = await _seed_creator(db_session)
    # A non-skipped authed endpoint → one backend http_request row.
    resp = client.get("/creators/niches", cookies=_cookie(creator))
    assert resp.status_code == 200

    row = (
        (
            await db_session.execute(
                select(EventLog).where(
                    EventLog.creator_id == creator.id,
                    EventLog.source == "backend",
                    EventLog.event == "http_request",
                    EventLog.page == "/creators/niches",
                )
            )
        )
        .scalars()
        .first()
    )
    assert row is not None
    assert row.status_code == 200
    assert row.target == "GET"
    assert row.duration_ms is not None


@pytest.mark.integration
async def test_record_event_non_uuid_creator_writes_cid_none(db_session):
    """record_event with a non-UUID creator_id writes a row with creator_id=NULL.

    The row must be persisted (telemetry is not silently dropped) and the
    creator_id column must be NULL (cid coerced from the invalid string).
    """
    import event_log as el

    unique_event = f"non_uuid_{uuid.uuid4().hex[:8]}"
    await el.record_event(
        source="test",
        event=unique_event,
        creator_id="not-a-valid-uuid-at-all",
    )

    row = (
        (
            await db_session.execute(
                select(EventLog).where(EventLog.event == unique_event)
            )
        )
        .scalars()
        .first()
    )
    assert row is not None, "Row must be persisted even when creator_id is not a UUID"
    assert row.creator_id is None, (
        "Non-UUID creator_id must be stored as NULL in the DB"
    )


@pytest.mark.integration
async def test_record_event_row_carries_creator_id(db_session):
    """record_event with a valid UUID creator_id stores it on the row (RLS context)."""
    import event_log as el

    creator = await _seed_creator(db_session)
    unique_event = f"creator_ctx_{uuid.uuid4().hex[:8]}"
    await el.record_event(
        source="test",
        event=unique_event,
        creator_id=str(creator.id),
    )

    row = (
        (
            await db_session.execute(
                select(EventLog).where(
                    EventLog.event == unique_event,
                    EventLog.creator_id == creator.id,
                )
            )
        )
        .scalars()
        .first()
    )
    assert row is not None, "Row must have the correct creator_id"
    assert row.creator_id == creator.id


@pytest.mark.integration
async def test_logs_me_isolated_per_creator(client, db_session):
    a = await _seed_creator(db_session)
    b = await _seed_creator(db_session)

    marker = f"only-A-{uuid.uuid4().hex[:8]}"
    client.post(
        "/api/activity",
        json={"page": "/x", "event_type": "click", "target": marker},
        cookies=_cookie(a),
    )

    a_targets = [
        e["target"] for e in client.get("/api/logs/me", cookies=_cookie(a)).json()["events"]
    ]
    b_targets = [
        e["target"] for e in client.get("/api/logs/me", cookies=_cookie(b)).json()["events"]
    ]

    assert marker in a_targets
    assert marker not in b_targets  # B cannot see A's events
