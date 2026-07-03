"""Integration tests for Issue 246 (+ residual) — lifecycle scan on real Postgres.

Real-PG lane (``-m integration``), pattern from
``tests/test_youtube_analytics_purge_integration.py``: seed a multi-creator
product-state fixture, run the real ``_run_lifecycle_scan_async`` sweep, then
execute the real ``_send_notification_async`` task body inline for every
enqueued call (Celery is not running; ``NOTIFY_BACKEND=console`` so no live
provider is ever hit).

Properties purchased:
  * The daily beat scan enqueues the RIGHT lifecycle event per creator
    product-state: nudge for the never-uploaded creator, re-engagement for the
    dormant creator, nothing for the fully active creator.
  * Exactly the expected ``notification_deliveries`` + ``notifications`` rows
    exist afterwards, with the exact dedupe keys ``notify.dedupe`` computes.
  * A second scan run is a no-op (shared 48h cap + UNIQUE dedupe_key) — the
    daily beat re-evaluating the same quiet creator never double-sends.
  * Sunset cap (246 residual): a creator with LIFECYCLE_REENGAGE_MAX_ATTEMPTS
    prior re_engagement ledger rows is skipped forever; one still under the
    cap is not.

The scan is cross-tenant (AdminSessionLocal sweep over ALL creators), so every
assertion is scoped to the creators this test seeds — the shared dev database
may legitimately contain other eligible creators.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from models import (
    Clip,
    ClipFeedback,
    Creator,
    FeedbackAction,
    Notification,
    NotificationChannel,
    NotificationDelivery,
    OnboardingState,
    Video,
    VideoKind,
)
from notify.dedupe import make_dedupe_key
from worker.tasks import _run_lifecycle_scan_async, _send_notification_async

pytestmark = pytest.mark.integration

_MAILING = "CreatorClip, 1 Main St, Springfield, USA"


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed_creator(session: AsyncSession, *, label: str, created_days_ago: int) -> Creator:
    creator = Creator(
        google_sub=f"test_lifecycle_{label}_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_lc_{label}_{uuid.uuid4().hex[:6]}",
        channel_title=f"Lifecycle Test {label}",
        email=f"lifecycle-{label}-{uuid.uuid4().hex[:6]}@example.test",
        onboarding_state=OnboardingState.active,
        created_at=datetime.now(UTC) - timedelta(days=created_days_ago),
    )
    session.add(creator)
    await session.flush()
    return creator


async def _seed_video(session: AsyncSession, creator: Creator) -> Video:
    video = Video(
        creator_id=creator.id,
        youtube_video_id=f"yt_lc_{uuid.uuid4().hex[:8]}",
        kind=VideoKind.long,
        duration_s=600.0,
    )
    session.add(video)
    await session.flush()
    return video


async def _seed_recent_feedback(session: AsyncSession, creator: Creator, video: Video) -> None:
    """A clip reviewed just now — makes the creator 'fully active'."""
    clip = Clip(video_id=video.id, creator_id=creator.id, start_s=0.0, end_s=30.0)
    session.add(clip)
    await session.flush()
    session.add(
        ClipFeedback(
            clip_id=clip.id,
            creator_id=creator.id,
            action=FeedbackAction.upvote,
            created_at=datetime.now(UTC),
        )
    )


async def _cleanup(session: AsyncSession, creator_ids: list[uuid.UUID]) -> None:
    # ON DELETE CASCADE covers videos/clips/feedback/deliveries/notifications/prefs.
    await session.execute(delete(Creator).where(Creator.id.in_(creator_ids)))
    await session.commit()


async def _run_scan_and_send(seeded: set[uuid.UUID]) -> list[tuple[str, str, str]]:
    """Run the real scan, capture the send_notification enqueues for OUR seeded
    creators, then execute the real task body inline for each. Returns the
    executed (creator_id, event_type, entity_id) triples."""
    with (
        patch("config.settings.MAILING_ADDRESS", _MAILING),
        patch("worker.tasks.send_notification") as mock_send,
    ):
        await _run_lifecycle_scan_async()
        calls = [c.args for c in mock_send.delay.call_args_list if uuid.UUID(c.args[0]) in seeded]
        for creator_id, event_type, entity_id, payload in calls:
            await _send_notification_async(creator_id, event_type, entity_id, payload)
    return [(c[0], c[1], c[2]) for c in calls]


async def test_scan_matches_product_state_and_is_idempotent(db_session: AsyncSession) -> None:
    """Three-cohort fixture: never-uploaded → nudge; dormant → re-engagement;
    fully active → nothing. Exactly the right ledger + in-app rows exist with
    the exact dedupe keys, and a second scan run creates no duplicates."""
    nudge_c = await _seed_creator(
        db_session, label="nudge", created_days_ago=settings.LIFECYCLE_NUDGE_AFTER_DAYS + 2
    )
    reengage_c = await _seed_creator(db_session, label="reengage", created_days_ago=60)
    await _seed_video(db_session, reengage_c)  # has a video, no reviews → dormant
    active_c = await _seed_creator(db_session, label="active", created_days_ago=60)
    active_video = await _seed_video(db_session, active_c)
    await _seed_recent_feedback(db_session, active_c, active_video)
    await db_session.commit()
    seeded = {nudge_c.id, reengage_c.id, active_c.id}

    try:
        executed = await _run_scan_and_send(seeded)

        period_window = datetime.now(UTC).toordinal() // settings.LIFECYCLE_INACTIVITY_DAYS
        expected = {
            (str(nudge_c.id), "first_clip_nudge", str(nudge_c.id)),
            (str(reengage_c.id), "re_engagement", f"reengage-{period_window}"),
        }
        assert set(executed) == expected, "scan must enqueue exactly nudge + re-engagement"

        # ── Ledger rows: exactly one per eligible creator, exact dedupe keys ──
        deliveries = (
            (
                await db_session.execute(
                    select(NotificationDelivery).where(NotificationDelivery.creator_id.in_(seeded))
                )
            )
            .scalars()
            .all()
        )
        by_creator = {d.creator_id: d for d in deliveries}
        assert len(deliveries) == 2
        assert by_creator[nudge_c.id].event_type == "first_clip_nudge"
        assert by_creator[nudge_c.id].dedupe_key == make_dedupe_key(
            nudge_c.id, "first_clip_nudge", str(nudge_c.id)
        )
        assert by_creator[reengage_c.id].event_type == "re_engagement"
        assert by_creator[reengage_c.id].dedupe_key == make_dedupe_key(
            reengage_c.id, "re_engagement", f"reengage-{period_window}"
        )
        assert active_c.id not in by_creator, "fully active creator must get nothing"

        # ── In-app notification rows mirror the ledger ────────────────────────
        notif_rows = (
            (
                await db_session.execute(
                    select(Notification.creator_id, Notification.kind).where(
                        Notification.creator_id.in_(seeded)
                    )
                )
            )
            .tuples()
            .all()
        )
        assert sorted(notif_rows) == sorted(
            [(nudge_c.id, "first_clip_nudge"), (reengage_c.id, "re_engagement")]
        )

        # ── Idempotency: the daily beat re-running is a no-op ────────────────
        executed_again = await _run_scan_and_send(seeded)
        assert executed_again == [], "48h shared cap must suppress the re-scan"

        # Even a direct duplicate task delivery (Celery at-least-once) is safe:
        with patch("config.settings.MAILING_ADDRESS", _MAILING):
            for creator_id, event_type, entity_id in executed:
                await _send_notification_async(creator_id, event_type, entity_id, {})

        recount = (
            (
                await db_session.execute(
                    select(NotificationDelivery).where(NotificationDelivery.creator_id.in_(seeded))
                )
            )
            .scalars()
            .all()
        )
        assert len(recount) == 2, "UNIQUE dedupe_key must block duplicate deliveries"
    finally:
        await _cleanup(db_session, list(seeded))


async def test_reengagement_sunsets_after_max_attempts(db_session: AsyncSession) -> None:
    """246 residual: a dormant creator with LIFECYCLE_REENGAGE_MAX_ATTEMPTS prior
    re_engagement ledger rows is never enqueued again; a control creator still
    under the cap IS enqueued (proving the skip is the sunset, not another gate)."""
    now = datetime.now(UTC)
    capped_c = await _seed_creator(db_session, label="sunset", created_days_ago=180)
    await _seed_video(db_session, capped_c)
    under_c = await _seed_creator(db_session, label="undercap", created_days_ago=180)
    await _seed_video(db_session, under_c)

    def _old_delivery(creator: Creator, i: int) -> NotificationDelivery:
        # Older than the 48h shared cap so only the sunset gate can skip them.
        entity = f"reengage-hist-{i}-{uuid.uuid4().hex[:6]}"
        return NotificationDelivery(
            creator_id=creator.id,
            event_type="re_engagement",
            entity_id=entity,
            channel=NotificationChannel.email,
            dedupe_key=make_dedupe_key(creator.id, "re_engagement", entity),
            created_at=now - timedelta(days=14 * (i + 1)),
        )

    for i in range(settings.LIFECYCLE_REENGAGE_MAX_ATTEMPTS):
        db_session.add(_old_delivery(capped_c, i))
    for i in range(settings.LIFECYCLE_REENGAGE_MAX_ATTEMPTS - 1):
        db_session.add(_old_delivery(under_c, i))
    await db_session.commit()

    try:
        with (
            patch("config.settings.MAILING_ADDRESS", _MAILING),
            patch("worker.tasks.send_notification") as mock_send,
        ):
            await _run_lifecycle_scan_async()

        enqueued_ids = {c.args[0] for c in mock_send.delay.call_args_list}
        assert str(capped_c.id) not in enqueued_ids, "at-cap creator must be sunset"
        assert str(under_c.id) in enqueued_ids, "under-cap creator must still be enqueued"
    finally:
        await _cleanup(db_session, [capped_c.id, under_c.id])
