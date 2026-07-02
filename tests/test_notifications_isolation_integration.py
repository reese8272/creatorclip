"""Integration test for Issue 245 residual — RLS isolation on ``notifications``.

Real-Postgres lane (``-m integration``), pattern from
``tests/test_rls_isolation_integration.py``: fixtures seed both creators as the
SUPERUSER, then the assertions run under the non-BYPASSRLS ``creatorclip_app``
role with creator B's ``app.creator_id`` GUC set (migration 0031 put ENABLE +
FORCE + ``tenant_isolation`` on the table).

The property purchased: creator B can neither READ nor DISMISS (UPDATE)
creator A's notification rows even if the application forgets its
``WHERE creator_id = :id`` predicate.
"""

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from models import Creator, Notification, OnboardingState

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def admin_engine():
    """SUPERUSER engine used for fixture setup / teardown."""
    eng = create_async_engine(settings.database_migration_url, pool_pre_ping=True)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db_session(admin_engine):
    factory = async_sessionmaker(admin_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


async def _seed_creator_with_notification(
    session: AsyncSession, *, label: str
) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed one creator plus one notification row; return (creator_id, notification_id)."""
    creator = Creator(
        google_sub=f"test_notif_rls_{label}_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_notif_{label}_{uuid.uuid4().hex[:6]}",
        channel_title=f"Notif RLS Test {label}",
        onboarding_state=OnboardingState.active,
        minutes_balance=100,
    )
    session.add(creator)
    await session.flush()
    notification = Notification(
        creator_id=creator.id,
        kind="clips_ready",
        title=f"Clips ready ({label})",
        body="Your clips are ready to review.",
        link_url="/app/review",
    )
    session.add(notification)
    await session.commit()
    return creator.id, notification.id


async def _cleanup(session: AsyncSession, creator_ids: list[uuid.UUID]) -> None:
    await session.execute(delete(Notification).where(Notification.creator_id.in_(creator_ids)))
    await session.execute(delete(Creator).where(Creator.id.in_(creator_ids)))
    await session.commit()


@pytest.mark.asyncio
async def test_rls_blocks_cross_tenant_notification_read_and_dismiss(admin_engine, db_session):
    """Under the app role with creator B's GUC, A's notification row is invisible
    to an unfiltered SELECT and untouchable by a dismiss-shaped UPDATE."""
    creator_a, notification_a = await _seed_creator_with_notification(db_session, label="A")
    creator_b, _notification_b = await _seed_creator_with_notification(db_session, label="B")

    try:
        factory = async_sessionmaker(admin_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            await s.execute(text("SET LOCAL ROLE creatorclip_app"))
            await s.execute(
                text("SELECT set_config('app.creator_id', :cid, true)"),
                {"cid": str(creator_b)},
            )

            # READ: an unfiltered SELECT never returns A's rows.
            owners = {
                r[0] for r in (await s.execute(text("SELECT creator_id FROM notifications"))).all()
            }
            assert creator_a not in owners, (
                "RLS leak: creator A's notification visible to creator B"
            )
            assert owners == {creator_b}, "creator B must still see their own notification"

            # DISMISS: the row is invisible to UPDATE's USING clause → 0 rows touched.
            result = await s.execute(
                text("UPDATE notifications SET dismissed_at = :now WHERE id = :nid"),
                {"now": datetime.now(UTC), "nid": notification_a},
            )
            assert result.rowcount == 0, "RLS leak: creator B dismissed creator A's notification"
            await s.rollback()

        # Superuser view: A's row is untouched (dismissed_at still NULL).
        dismissed = (
            await db_session.execute(
                text("SELECT dismissed_at FROM notifications WHERE id = :nid"),
                {"nid": notification_a},
            )
        ).scalar_one()
        assert dismissed is None, "creator A's notification must be untouched"
    finally:
        await _cleanup(db_session, [creator_a, creator_b])
