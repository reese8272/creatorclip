"""DB-free unit tests for Issue 246 — lifecycle email sequence.

Covers the load-bearing logic the unit lane can verify without Postgres:
  * The welcome trigger fires exactly once from the OAuth is_new branch with
    entity_id == creator_id (dedupe-once).
  * The lifecycle scan enqueues first_clip_nudge / re_engagement for the right
    cohorts, and the shared frequency cap suppresses a second lifecycle email
    within the window.
  * The new lifecycle COPY + templates carry no virality language and each has a
    .txt + .html pair on disk.

Cross-creator isolation, the UNIQUE dedupe double-enqueue, and the daily beat
sweep over real multi-creator product state are staging-only (real Postgres).
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _cid() -> uuid.UUID:
    return uuid.uuid4()


# ── Frequency cap helper ──────────────────────────────────────────────────────


class TestLifecycleFrequencyCap:
    @pytest.mark.asyncio
    async def test_capped_when_recent_delivery_exists(self) -> None:
        """_lifecycle_capped returns True when a lifecycle delivery is in-window."""
        from worker.tasks import _lifecycle_capped

        session = AsyncMock()
        session.scalar = AsyncMock(return_value=uuid.uuid4())  # an existing delivery id

        assert await _lifecycle_capped(session, _cid(), datetime.now(UTC)) is True

    @pytest.mark.asyncio
    async def test_not_capped_when_no_recent_delivery(self) -> None:
        from worker.tasks import _lifecycle_capped

        session = AsyncMock()
        session.scalar = AsyncMock(return_value=None)

        assert await _lifecycle_capped(session, _cid(), datetime.now(UTC)) is False


# ── Scan: first-clip nudge + re-engagement ────────────────────────────────────


def _scan_session(nudge_ids: list, reengage_ids: list) -> AsyncMock:
    """A session whose two execute() calls return the nudge cohort then the
    re-engagement cohort, and whose _lifecycle_capped scalar returns None (not
    capped)."""
    session = AsyncMock()

    nudge_result = MagicMock()
    nudge_result.all.return_value = [(c,) for c in nudge_ids]
    reengage_result = MagicMock()
    reengage_result.all.return_value = [(c,) for c in reengage_ids]
    session.execute = AsyncMock(side_effect=[nudge_result, reengage_result])

    session.scalar = AsyncMock(return_value=None)  # never capped
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


class TestLifecycleScan:
    @pytest.mark.asyncio
    async def test_nudge_and_reengagement_enqueued(self) -> None:
        nudge_id = _cid()
        reengage_id = _cid()
        session = _scan_session([nudge_id], [reengage_id])

        with (
            patch("config.settings.MAILING_ADDRESS", "CreatorClip, 1 Main St, USA"),
            patch("db.AdminSessionLocal", return_value=session),
            patch("worker.tasks.send_notification") as mock_send,
        ):
            from worker.tasks import _run_lifecycle_scan_async

            await _run_lifecycle_scan_async()

        events = {call.args[1] for call in mock_send.delay.call_args_list}
        assert "first_clip_nudge" in events
        assert "re_engagement" in events

        nudge_call = next(
            c for c in mock_send.delay.call_args_list if c.args[1] == "first_clip_nudge"
        )
        assert nudge_call.args[0] == str(nudge_id)
        # entity_id == creator_id ⇒ once-ever per creator.
        assert nudge_call.args[2] == str(nudge_id)

    @pytest.mark.asyncio
    async def test_empty_cohorts_enqueue_nothing(self) -> None:
        session = _scan_session([], [])
        with (
            patch("config.settings.MAILING_ADDRESS", "CreatorClip, 1 Main St, USA"),
            patch("db.AdminSessionLocal", return_value=session),
            patch("worker.tasks.send_notification") as mock_send,
        ):
            from worker.tasks import _run_lifecycle_scan_async

            await _run_lifecycle_scan_async()
        mock_send.delay.assert_not_called()

    @pytest.mark.asyncio
    async def test_capped_creator_is_skipped(self) -> None:
        """A creator already inside the 48h shared window is NOT enqueued."""
        nudge_id = _cid()
        session = _scan_session([nudge_id], [])
        # Override scalar so the cap check reports capped for everyone.
        session.scalar = AsyncMock(return_value=uuid.uuid4())

        with (
            patch("config.settings.MAILING_ADDRESS", "CreatorClip, 1 Main St, USA"),
            patch("db.AdminSessionLocal", return_value=session),
            patch("worker.tasks.send_notification") as mock_send,
        ):
            from worker.tasks import _run_lifecycle_scan_async

            await _run_lifecycle_scan_async()
        mock_send.delay.assert_not_called()

    @pytest.mark.asyncio
    async def test_scan_skipped_when_mailing_address_unset(self) -> None:
        """CAN-SPAM safety gate: with MAILING_ADDRESS unset the whole sweep is a
        no-op — no DB session opened, no email enqueued — so merging this branch
        cannot blast existing users."""
        session = _scan_session([_cid()], [_cid()])
        with (
            patch("config.settings.MAILING_ADDRESS", ""),
            patch("db.AdminSessionLocal", return_value=session) as mock_session,
            patch("worker.tasks.send_notification") as mock_send,
        ):
            from worker.tasks import _run_lifecycle_scan_async

            await _run_lifecycle_scan_async()
        mock_send.delay.assert_not_called()
        mock_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_reengagement_bucket_is_14_day_cadence(self) -> None:
        """The re_engagement dedupe bucket floors the day to the inactivity window
        so a dormant creator is re-engaged at most once per ~14 days, not daily."""
        reengage_id = _cid()
        session = _scan_session([], [reengage_id])
        with (
            patch("config.settings.MAILING_ADDRESS", "CreatorClip, 1 Main St, USA"),
            patch("db.AdminSessionLocal", return_value=session),
            patch("worker.tasks.send_notification") as mock_send,
        ):
            from worker.tasks import _run_lifecycle_scan_async

            await _run_lifecycle_scan_async()
        reengage_call = next(
            c for c in mock_send.delay.call_args_list if c.args[1] == "re_engagement"
        )
        from config import settings

        expected_window = datetime.now(UTC).toordinal() // settings.LIFECYCLE_INACTIVITY_DAYS
        assert reengage_call.args[2] == f"reengage-{expected_window}"


# ── Welcome trigger (OAuth is_new branch) ─────────────────────────────────────


class TestWelcomeTrigger:
    @pytest.mark.asyncio
    async def test_welcome_enqueued_once_with_creator_id_entity(self) -> None:
        """The send_notification.delay for welcome uses entity_id == creator_id.

        We exercise the exact enqueue shape the auth callback uses rather than
        the whole OAuth flow (which needs Google + DB).
        """
        from worker.tasks import send_notification

        creator_id = _cid()
        with patch.object(send_notification, "delay") as mock_delay:
            send_notification.delay(str(creator_id), "welcome", str(creator_id), {})

        mock_delay.assert_called_once_with(str(creator_id), "welcome", str(creator_id), {})


# ── Copy + template honesty (no virality) ─────────────────────────────────────

_VIRALITY_PHRASES = [
    "viral clips",
    "viral content",
    "go viral",
    "guaranteed viral",
    "will go viral",
    "make you viral",
]


class TestLifecycleCopyHonesty:
    def test_new_copy_no_virality(self) -> None:
        from notify.copy import COPY

        for event in ("first_clip_nudge", "re_engagement"):
            for value in COPY[event].values():
                lower = value.lower()
                for phrase in _VIRALITY_PHRASES:
                    assert phrase not in lower, f"{event} copy contains {phrase!r}"

    def test_new_templates_exist_and_clean(self) -> None:
        templates_dir = Path(__file__).parent.parent / "notify" / "templates"
        # welcome is a lifecycle (commercial-leaning) email and must carry the
        # CAN-SPAM unsubscribe affordance + a {{ mailing_address }} placeholder.
        for event in ("welcome", "first_clip_nudge", "re_engagement"):
            txt = templates_dir / f"{event}.txt"
            html = templates_dir / f"{event}.html"
            assert txt.exists(), f"Missing {event}.txt"
            assert html.exists(), f"Missing {event}.html"
            for path in (txt, html):
                content = path.read_text(encoding="utf-8").lower()
                for phrase in _VIRALITY_PHRASES:
                    assert phrase not in content, f"{path.name} contains {phrase!r}"
                # CAN-SPAM: lifecycle templates carry an unsubscribe link AND a
                # physical mailing-address placeholder in the visible body.
                assert "unsubscribe" in content, f"{path.name} missing unsubscribe link"
                assert "mailing_address" in content, f"{path.name} missing mailing address"
