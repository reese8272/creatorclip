"""
DB-free unit tests for Issue 244: Wire transactional triggers to the fan-out.

Verifies that each of the 6 trigger fire points enqueues exactly one
send_notification with the correct event_type and entity_id.

Tests are designed to run without Postgres, Redis, or Celery — all DB and
Celery interactions are mocked.  Staging-pending integration tests (real
Postgres + Celery) are skipped with an explicit marker.

Trigger points covered:
    1. clips_ready  — _generate_clips_async terminal done
    2. dna_built    — _build_dna_async terminal done
    3. refund_issued — RefundOnFailureTask.on_failure (via _fire_refund_notification_async)
    4. reauth_required — sync_channel_catalog YouTubeAuthError path
    5. trial_ending — _expire_trials_async per-creator loop
    6. balance_low  — billing/ledger.py deduct_for_video when remaining <= threshold
"""

import contextlib
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_creator_uuid() -> uuid.UUID:
    return uuid.uuid4()


# ── Trigger 1: clips_ready ────────────────────────────────────────────────────


class TestClipsReadyTrigger:
    """_generate_clips_async enqueues clips_ready after a successful clip generation."""

    @pytest.mark.asyncio
    async def test_clips_ready_enqueued_on_done(self) -> None:
        """After terminal done, send_notification.delay is called with clips_ready."""
        creator_uuid = _make_creator_uuid()
        video_id = str(uuid.uuid4())

        mock_video = MagicMock()
        mock_video.creator_id = creator_uuid

        # Issue 311: _generate_clips_async now loads the Creator (for the
        # clips_ready email greeting) between the Video and Signals fetches.
        mock_creator = MagicMock()
        mock_creator.channel_title = "Test Channel"

        mock_signals = MagicMock()
        mock_signals.timeline_jsonb = {}

        mock_clips = [MagicMock(), MagicMock()]  # 2 clips

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(side_effect=[mock_video, mock_creator, mock_signals, None])
        mock_session.scalar = AsyncMock(return_value=None)  # no existing done clips
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("db.AdminSessionLocal", return_value=mock_session),
            # Issue 231: per-creator tasks open db.tenant_session → AsyncSessionLocal.
            patch("db.AsyncSessionLocal", return_value=mock_session),
            patch("worker.progress.aemit", new_callable=AsyncMock),
            # Issue 82b split: session-free scoring + reacquired-session persistence.
            patch(
                "clip_engine.ranking.load_existing_clips",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "clip_engine.ranking.score_and_rank",
                new_callable=AsyncMock,
                return_value=[{"rank": 1}, {"rank": 2}],
            ),
            patch(
                "clip_engine.ranking.persist_ranked_clips",
                new_callable=AsyncMock,
                return_value=mock_clips,
            ),
            patch("dna.profile.get_active", new_callable=AsyncMock, return_value=None),
            patch("worker.tasks.send_notification") as mock_send_notif,
            patch("worker.tasks.render_clip.delay"),  # auto-render — don't hit the broker
        ):
            from worker.tasks import _generate_clips_async

            await _generate_clips_async(video_id, str(uuid.uuid4()))

        # Issue 311: payload now carries the per-email vars the clips_ready
        # template needs (creator_name greeting, video_title, absolute review_url)
        # in addition to clip_count. Assert the positional args + the key payload
        # fields rather than exact equality (video_title is a MagicMock attr).
        mock_send_notif.delay.assert_called_once()
        call_args = mock_send_notif.delay.call_args.args
        assert call_args[0] == str(creator_uuid)
        assert call_args[1] == "clips_ready"
        assert call_args[2] == video_id
        payload = call_args[3]
        assert payload["clip_count"] == 2
        assert payload["creator_name"] == "Test Channel"
        assert payload["review_url"].startswith("http")
        assert "video_title" in payload

    @pytest.mark.asyncio
    async def test_clips_ready_not_enqueued_on_error(self) -> None:
        """If clip generation raises, no clips_ready notification is enqueued."""
        video_id = str(uuid.uuid4())

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(side_effect=ValueError("Video not found"))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("db.AdminSessionLocal", return_value=mock_session),
            # Issue 231: per-creator tasks open db.tenant_session → AsyncSessionLocal.
            patch("db.AsyncSessionLocal", return_value=mock_session),
            patch("worker.progress.aemit", new_callable=AsyncMock),
            patch("worker.tasks.send_notification") as mock_send_notif,
        ):
            from worker.tasks import _generate_clips_async

            with pytest.raises(ValueError):
                await _generate_clips_async(video_id, str(uuid.uuid4()))

        mock_send_notif.delay.assert_not_called()


# ── Trigger 2: dna_built ──────────────────────────────────────────────────────


class TestDnaBuiltTrigger:
    """_build_dna_async enqueues dna_built after committing a new DNA draft."""

    @pytest.mark.asyncio
    async def test_dna_built_enqueued_on_done(self) -> None:
        """send_notification.delay is called with dna_built on success."""
        creator_id = str(_make_creator_uuid())

        # Mock the advisory lock, session, and all sub-calls.
        mock_dna = MagicMock()
        mock_dna.version = 1

        mock_creator = MagicMock()
        mock_creator.channel_title = "Test Channel"
        mock_creator.onboarding_state = MagicMock()

        mock_session = AsyncMock()
        # scalar: first call is the idempotency check under advisory lock
        # (SELECT CreatorDna.id WHERE build_job_id == job_id) — must return None so
        # the build proceeds rather than short-circuiting as "already built".
        mock_session.scalar = AsyncMock(return_value=None)
        # execute: advisory lock SELECT (pg_advisory_xact_lock) — return value unused.
        mock_session.execute = AsyncMock(return_value=MagicMock())
        mock_session.get = AsyncMock(return_value=mock_creator)
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()
        mock_session.refresh = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.flush = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("db.AdminSessionLocal", return_value=mock_session),
            # Issue 231: per-creator tasks open db.tenant_session → AsyncSessionLocal.
            patch("db.AsyncSessionLocal", return_value=mock_session),
            patch("worker.progress.aemit", new_callable=AsyncMock),
            patch(
                "dna.builder.build_patterns",
                new_callable=AsyncMock,
                return_value=(
                    {"long_videos_analyzed": 5, "shorts_analyzed": 3},
                    [],
                    [],
                    60,
                    "top",
                    24,
                ),
            ),
            patch("dna.identity.get_current", new_callable=AsyncMock, return_value=None),
            patch("dna.identity.format_for_prompt", return_value=None),
            patch(
                "dna.brief.generate_brief",
                return_value=("Brief text.", {"input_tokens": 100, "output_tokens": 50}),
            ),
            patch(
                "dna.profile.create_draft",
                new_callable=AsyncMock,
                return_value=mock_dna,
            ),
            patch("dna.embeddings.embed_patterns", new_callable=AsyncMock),
            patch("dna.embeddings.embed_brief", new_callable=AsyncMock),
            patch("billing.ledger.record_llm_usage", new_callable=AsyncMock),
            patch("worker.tasks.send_notification") as mock_send_notif,
        ):
            from worker.tasks import _build_dna_async

            await _build_dna_async(creator_id, job_id="test-job-id")

        mock_send_notif.delay.assert_called_once_with(
            creator_id,
            "dna_built",
            creator_id,
            {},
        )


# ── Trigger 3: refund_issued ──────────────────────────────────────────────────


class TestRefundIssuedTrigger:
    """_fire_refund_notification_async enqueues refund_issued after a successful refund."""

    @pytest.mark.asyncio
    async def test_refund_notification_enqueued_with_creator_id(self) -> None:
        """send_notification.delay is called with refund_issued and video_id as entity."""
        creator_uuid = _make_creator_uuid()
        video_uuid = uuid.uuid4()

        mock_session = AsyncMock()
        # scalar returns creator_id from the MinuteDeduction row
        mock_session.scalar = AsyncMock(return_value=creator_uuid)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("db.AdminSessionLocal", return_value=mock_session),
            # Issue 231: per-creator tasks open db.tenant_session → AsyncSessionLocal.
            patch("db.AsyncSessionLocal", return_value=mock_session),
            patch("worker.tasks.send_notification") as mock_send_notif,
        ):
            from worker.tasks import _fire_refund_notification_async

            await _fire_refund_notification_async(video_uuid)

        mock_send_notif.delay.assert_called_once_with(
            str(creator_uuid),
            "refund_issued",
            str(video_uuid),
            {},
        )

    @pytest.mark.asyncio
    async def test_refund_notification_skipped_when_no_deduction_row(self) -> None:
        """If MinuteDeduction row is absent, no notification is enqueued."""
        video_uuid = uuid.uuid4()

        mock_session = AsyncMock()
        mock_session.scalar = AsyncMock(return_value=None)  # no deduction row
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("db.AdminSessionLocal", return_value=mock_session),
            # Issue 231: per-creator tasks open db.tenant_session → AsyncSessionLocal.
            patch("db.AsyncSessionLocal", return_value=mock_session),
            patch("worker.tasks.send_notification") as mock_send_notif,
        ):
            from worker.tasks import _fire_refund_notification_async

            await _fire_refund_notification_async(video_uuid)

        mock_send_notif.delay.assert_not_called()


# ── Trigger 4: reauth_required ────────────────────────────────────────────────


class TestReauthRequiredTrigger:
    """sync_channel_catalog enqueues reauth_required when YouTubeAuthError is raised."""

    def test_reauth_notification_enqueued_on_youtube_auth_error(self) -> None:
        """send_notification.delay is called with reauth_required on YouTubeAuthError."""
        from youtube.errors import YouTubeAuthError

        creator_id = str(_make_creator_uuid())

        auth_err = YouTubeAuthError("authError", 401)
        with (
            patch(
                "worker.tasks._sync_channel_catalog_async",
                side_effect=auth_err,
            ),
            patch("worker.tasks.run_async", side_effect=auth_err),
            patch("worker.tasks.send_notification") as mock_send_notif,
            patch("worker.tasks.log_event"),
        ):
            from worker.tasks import sync_channel_catalog

            with pytest.raises(YouTubeAuthError):
                sync_channel_catalog(creator_id)

        mock_send_notif.delay.assert_called_once_with(
            creator_id,
            "reauth_required",
            creator_id,
            {},
        )

    def test_reauth_notification_not_enqueued_on_transient_error(self) -> None:
        """Non-YouTubeAuthError errors do NOT enqueue reauth_required."""
        creator_id = str(_make_creator_uuid())

        mock_task = MagicMock()

        with (
            patch(
                "worker.tasks.run_async",
                side_effect=RuntimeError("transient"),
            ),
            patch("worker.tasks.send_notification") as mock_send_notif,
            patch("worker.tasks.log_event"),
        ):
            from worker.tasks import sync_channel_catalog

            # Transient error → retry, not re-auth. The task itself raises Retry which
            # we catch here by patching out self.retry to raise RuntimeError.
            with contextlib.suppress(Exception):
                sync_channel_catalog.__wrapped__(mock_task, creator_id)

        mock_send_notif.delay.assert_not_called()


# ── Trigger 5: trial_ending ───────────────────────────────────────────────────


class TestTrialEndingTrigger:
    """_expire_trials_async enqueues trial_ending for each creator whose trial just ended."""

    @pytest.mark.asyncio
    async def test_trial_ending_enqueued_for_expiring_creator(self) -> None:
        """send_notification.delay is called with trial_ending for each expiring creator."""
        creator_uuid = _make_creator_uuid()
        now = datetime.now(UTC)
        trial_ends_at = now  # just expired

        # Mock the DB returning one creator with an expiring trial
        mock_row = (creator_uuid, trial_ends_at, 0)
        mock_result = MagicMock()
        mock_result.all.return_value = [mock_row]

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("db.AdminSessionLocal", return_value=mock_session),
            # Issue 231: per-creator tasks open db.tenant_session → AsyncSessionLocal.
            patch("db.AsyncSessionLocal", return_value=mock_session),
            patch("worker.tasks.send_notification") as mock_send_notif,
        ):
            from worker.tasks import _expire_trials_async

            await _expire_trials_async()

        # Must have been called with trial_ending and the date as entity_id.
        assert mock_send_notif.delay.call_count == 1
        call_args = mock_send_notif.delay.call_args
        assert call_args[0][0] == str(creator_uuid)
        assert call_args[0][1] == "trial_ending"
        # entity_id is the ISO date of trial_ends_at
        assert call_args[0][2] == trial_ends_at.date().isoformat()

    @pytest.mark.asyncio
    async def test_trial_ending_not_enqueued_when_no_expiring_creators(self) -> None:
        """When no creators have expiring trials, no notification is enqueued."""
        mock_result = MagicMock()
        mock_result.all.return_value = []

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("db.AdminSessionLocal", return_value=mock_session),
            # Issue 231: per-creator tasks open db.tenant_session → AsyncSessionLocal.
            patch("db.AsyncSessionLocal", return_value=mock_session),
            patch("worker.tasks.send_notification") as mock_send_notif,
        ):
            from worker.tasks import _expire_trials_async

            await _expire_trials_async()

        mock_send_notif.delay.assert_not_called()


# ── Trigger 6: balance_low ────────────────────────────────────────────────────


class TestBalanceLowTrigger:
    """deduct_for_video stages balance_low in session.info; the after_commit
    listener enqueues it — never before the outer transaction commits
    (Issue 244; post-commit enqueue Issue 352 Batch B)."""

    @staticmethod
    def _mock_session_with_remaining(remaining: int) -> AsyncMock:
        mock_session = AsyncMock()
        mock_session.scalar = AsyncMock(return_value=None)  # no existing deduction
        mock_session.add = MagicMock()
        mock_session.flush = AsyncMock()
        mock_session.info = {}  # real dict — the staging area under test

        mock_execute_result = MagicMock()
        mock_execute_result.fetchone = MagicMock(return_value=(remaining,))
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        mock_savepoint = AsyncMock()
        mock_savepoint.__aenter__ = AsyncMock(return_value=mock_savepoint)
        mock_savepoint.__aexit__ = AsyncMock(return_value=False)
        mock_session.begin_nested = MagicMock(return_value=mock_savepoint)
        return mock_session

    @pytest.mark.asyncio
    async def test_balance_low_staged_but_not_sent_before_commit(self) -> None:
        """Below threshold: the pair is staged in session.info and NOTHING is
        enqueued yet — enqueuing pre-commit could notify for a deduction a
        later rollback undoes."""
        creator_uuid = _make_creator_uuid()
        video_uuid = uuid.uuid4()
        mock_session = self._mock_session_with_remaining(5)

        with (
            patch("worker.tasks.send_notification") as mock_send_notif,
            patch("config.settings") as mock_settings,
        ):
            mock_settings.LOW_BALANCE_THRESHOLD_MINUTES = 10

            from billing.ledger import deduct_for_video

            result = await deduct_for_video(
                video_id=video_uuid,
                creator_id=creator_uuid,
                duration_s=120.0,
                session=mock_session,
            )

        assert result == 2  # 120s = 2 minutes
        mock_send_notif.delay.assert_not_called()

        from billing.ledger import _PENDING_BALANCE_LOW_KEY

        assert mock_session.info[_PENDING_BALANCE_LOW_KEY] == [(str(creator_uuid), str(video_uuid))]

    def test_balance_low_sent_exactly_once_after_real_commit(self) -> None:
        """A real Session commit drains the staged entry through the
        after_commit listener; a second commit does not re-send."""
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import Session

        from billing.ledger import _PENDING_BALANCE_LOW_KEY

        creator_id, video_id = str(uuid.uuid4()), str(uuid.uuid4())
        engine = create_engine("sqlite://")
        with (
            patch("worker.tasks.send_notification") as mock_send_notif,
            Session(engine) as session,
        ):
            session.execute(text("SELECT 1"))
            session.info.setdefault(_PENDING_BALANCE_LOW_KEY, []).append((creator_id, video_id))
            session.commit()
            mock_send_notif.delay.assert_called_once_with(creator_id, "balance_low", video_id, {})

            session.execute(text("SELECT 1"))
            session.commit()
            mock_send_notif.delay.assert_called_once()  # still exactly once

    def test_balance_low_discarded_on_rollback(self) -> None:
        """A rollback drops the staged entry — a later commit on the same
        session must not send a notification for a deduction that never
        persisted."""
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import Session

        from billing.ledger import _PENDING_BALANCE_LOW_KEY

        engine = create_engine("sqlite://")
        with (
            patch("worker.tasks.send_notification") as mock_send_notif,
            Session(engine) as session,
        ):
            session.execute(text("SELECT 1"))  # real DBAPI txn so after_rollback fires
            session.info.setdefault(_PENDING_BALANCE_LOW_KEY, []).append(("c", "v"))
            session.rollback()
            session.execute(text("SELECT 1"))
            session.commit()

        mock_send_notif.delay.assert_not_called()

    @pytest.mark.asyncio
    async def test_balance_low_not_staged_when_above_threshold(self) -> None:
        """Nothing staged (and nothing enqueued) when remaining > threshold."""
        creator_uuid = _make_creator_uuid()
        video_uuid = uuid.uuid4()
        mock_session = self._mock_session_with_remaining(50)

        with (
            patch("worker.tasks.send_notification") as mock_send_notif,
            patch("config.settings") as mock_settings,
        ):
            mock_settings.LOW_BALANCE_THRESHOLD_MINUTES = 10

            from billing.ledger import deduct_for_video

            await deduct_for_video(
                video_id=video_uuid,
                creator_id=creator_uuid,
                duration_s=120.0,
                session=mock_session,
            )

        mock_send_notif.delay.assert_not_called()
        assert mock_session.info == {}

    @pytest.mark.asyncio
    async def test_balance_low_not_enqueued_on_idempotent_skip(self) -> None:
        """When deduction already exists (idempotent skip), no notification is enqueued."""
        creator_uuid = _make_creator_uuid()
        video_uuid = uuid.uuid4()

        mock_session = AsyncMock()
        # existing deduction → idempotent skip returns 0
        mock_session.scalar = AsyncMock(return_value=MagicMock())  # non-None
        mock_session.add = MagicMock()

        with (
            patch("worker.tasks.send_notification") as mock_send_notif,
        ):
            from billing.ledger import deduct_for_video

            result = await deduct_for_video(
                video_id=video_uuid,
                creator_id=creator_uuid,
                duration_s=120.0,
                session=mock_session,
            )

        assert result == 0
        mock_send_notif.delay.assert_not_called()


# ── Copy: no virality promises ────────────────────────────────────────────────


class TestNotificationCopyHonestyConstraint:
    """Structural test: all notification copy strings contain no virality promises."""

    VIRALITY_PROMISE_PHRASES = [
        "viral clips",
        "viral content",
        "go viral",
        "guaranteed viral",
        "guaranteed to",
        "will go viral",
        "make you viral",
    ]

    def test_copy_module_contains_no_virality_promise(self) -> None:
        """All strings in notify/copy.py COPY dict pass the honesty check."""
        from notify.copy import COPY

        for event_type, strings in COPY.items():
            for key, value in strings.items():
                lower = value.lower()
                for phrase in self.VIRALITY_PROMISE_PHRASES:
                    assert phrase not in lower, (
                        f"event_type={event_type!r} key={key!r} contains virality "
                        f"promise {phrase!r}: {value!r}"
                    )

    def test_all_template_txt_files_contain_no_virality_promise(self) -> None:
        """All .txt template files in notify/templates/ pass the honesty check."""
        from pathlib import Path

        templates_dir = Path(__file__).parent.parent / "notify" / "templates"
        txt_files = list(templates_dir.glob("*.txt"))
        assert txt_files, "Expected at least one .txt template file"

        for path in txt_files:
            content = path.read_text(encoding="utf-8").lower()
            for phrase in self.VIRALITY_PROMISE_PHRASES:
                assert phrase not in content, f"{path.name} contains virality promise {phrase!r}"

    def test_all_event_types_have_templates(self) -> None:
        """Every event type that fires a notification has a corresponding template pair."""
        from pathlib import Path

        templates_dir = Path(__file__).parent.parent / "notify" / "templates"
        # The 6 transactional event types wired in Issue 244.
        expected_events = {
            "clips_ready",
            "dna_built",
            "refund_issued",
            "reauth_required",
            "trial_ending",
            "balance_low",
        }
        for event in expected_events:
            assert (templates_dir / f"{event}.txt").exists(), f"Missing template {event}.txt"
            assert (templates_dir / f"{event}.html").exists(), f"Missing template {event}.html"


# ── Staging-pending integration tests ─────────────────────────────────────────


@pytest.mark.skip(reason="staging-pending: needs real Postgres + Celery (Issue 275)")
class TestNotificationsTriggersIntegration:
    """Integration tests requiring a live Postgres instance and Celery worker.

    These tests verify:
    - Each trigger fires exactly one send_notification per event.
    - A duplicate beat tick or task redelivery does not double-send.
    - balance_low fires only on the threshold-crossing deduct, not every deduct below.

    Run these against the GKE staging cluster (Issue 275).
    """

    def test_clips_ready_exactly_once_on_retry(self) -> None:
        raise NotImplementedError("staging-pending")

    def test_dna_built_exactly_once_on_retry(self) -> None:
        raise NotImplementedError("staging-pending")

    def test_balance_low_fires_once_per_video(self) -> None:
        raise NotImplementedError("staging-pending")
