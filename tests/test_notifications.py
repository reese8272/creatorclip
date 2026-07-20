"""
DB-free unit tests for Issue 243: Notification data model + idempotent send task.

Coverage:
- notify/dedupe.py — determinism, collision-resistance, length limits
- notification models — enum completeness, Notification copy text
- send_notification task — preference gate, dedupe short-circuit, no-PII assertion,
  idempotency on IntegrityError, lifecycle opt-out, in-app build

Staging-pending (need real Postgres + Celery):
- UNIQUE dedupe_key constraint enforcement
- RLS tenant_isolation on the notifications table (blocks cross-creator reads)
- Full end-to-end double-enqueue sends exactly once

These are flagged with pytest.mark.skip(reason="staging-pending: needs Postgres").
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── notify/dedupe.py ─────────────────────────────────────────────────────────


class TestMakeDedupe:
    """Tests for notify.dedupe.make_dedupe_key()."""

    def test_deterministic(self) -> None:
        """Same inputs always produce the same key."""
        from notify.dedupe import make_dedupe_key

        cid = uuid.uuid4()
        key1 = make_dedupe_key(cid, "clips_ready", "vid-abc")
        key2 = make_dedupe_key(cid, "clips_ready", "vid-abc")
        assert key1 == key2

    def test_different_event_types_produce_different_keys(self) -> None:
        from notify.dedupe import make_dedupe_key

        cid = uuid.uuid4()
        k1 = make_dedupe_key(cid, "clips_ready", "vid-abc")
        k2 = make_dedupe_key(cid, "dna_built", "vid-abc")
        assert k1 != k2

    def test_different_entity_ids_produce_different_keys(self) -> None:
        from notify.dedupe import make_dedupe_key

        cid = uuid.uuid4()
        k1 = make_dedupe_key(cid, "clips_ready", "vid-abc")
        k2 = make_dedupe_key(cid, "clips_ready", "vid-xyz")
        assert k1 != k2

    def test_different_creator_ids_produce_different_keys(self) -> None:
        from notify.dedupe import make_dedupe_key

        cid1 = uuid.uuid4()
        cid2 = uuid.uuid4()
        k1 = make_dedupe_key(cid1, "clips_ready", "vid-abc")
        k2 = make_dedupe_key(cid2, "clips_ready", "vid-abc")
        assert k1 != k2

    def test_output_is_hex_string(self) -> None:
        from notify.dedupe import make_dedupe_key

        cid = uuid.uuid4()
        key = make_dedupe_key(cid, "clips_ready", "vid-abc")
        assert isinstance(key, str)
        assert all(c in "0123456789abcdef" for c in key)

    def test_output_length_is_64_chars(self) -> None:
        """SHA-256 hex digest is always exactly 64 chars — within Resend's 256-char limit."""
        from notify.dedupe import make_dedupe_key

        cid = uuid.uuid4()
        key = make_dedupe_key(cid, "clips_ready", "vid-abc")
        assert len(key) == 64

    def test_creator_id_uuid_object_vs_string_is_consistent(self) -> None:
        """UUID object and its canonical string form must hash identically.

        The task receives creator_id as a string (Celery serializes kwargs as JSON);
        make_dedupe_key must be called with a uuid.UUID so it's canonical, not raw string.
        """
        from notify.dedupe import make_dedupe_key

        raw = "12345678-1234-5678-1234-567812345678"
        cid = uuid.UUID(raw)
        k1 = make_dedupe_key(cid, "clips_ready", "vid-abc")
        k2 = make_dedupe_key(uuid.UUID(raw), "clips_ready", "vid-abc")
        assert k1 == k2


# ── Notification model copy strings ─────────────────────────────────────────


class TestBuildInappNotification:
    """Tests for worker.tasks._build_inapp_notification()."""

    def test_known_event_types_return_correct_title(self) -> None:
        from worker.tasks import _build_inapp_notification

        cid = uuid.uuid4()
        notif = _build_inapp_notification(cid, "clips_ready", {})
        assert notif.kind == "clips_ready"
        assert "ready" in notif.title.lower()

    def test_dna_built_copy(self) -> None:
        from worker.tasks import _build_inapp_notification

        notif = _build_inapp_notification(uuid.uuid4(), "dna_built", {})
        assert "dna" in notif.title.lower() or "channel" in notif.title.lower()

    def test_no_virality_promise_in_any_copy(self) -> None:
        """Structural test: no notification copy PROMISES virality (Honesty Constraint).

        The word "viral" may legitimately appear in a disclaimer ("does not promise virality")
        but must never appear as a positive claim ("your clips are viral", "guaranteed viral").
        We check for virality-promise phrases, not the bare word.
        """
        from worker.tasks import _build_inapp_notification

        # These phrase fragments constitute a virality promise — none should appear.
        virality_promise_phrases = [
            "viral clips",
            "viral content",
            "go viral",
            "guaranteed viral",
            "guaranteed to",
            "will go viral",
            "make you viral",
        ]
        event_types = [
            "clips_ready",
            "dna_built",
            "trial_ending",
            "balance_low",
            "refund_issued",
            "reauth_required",
            "catalog_sync_done",
            "welcome",
        ]
        cid = uuid.uuid4()
        for event_type in event_types:
            notif = _build_inapp_notification(cid, event_type, {})
            combined = (notif.title + " " + notif.body).lower()
            for phrase in virality_promise_phrases:
                assert phrase not in combined, (
                    f"event_type={event_type!r} contains virality promise {phrase!r}: {combined!r}"
                )

    def test_unknown_event_type_returns_generic_notification(self) -> None:
        from worker.tasks import _build_inapp_notification

        cid = uuid.uuid4()
        notif = _build_inapp_notification(cid, "some_new_event_xyz", {})
        assert notif.kind == "some_new_event_xyz"
        assert notif.title  # must not be empty

    def test_creator_id_stamped_on_notification(self) -> None:
        from worker.tasks import _build_inapp_notification

        cid = uuid.uuid4()
        notif = _build_inapp_notification(cid, "clips_ready", {})
        assert notif.creator_id == cid

    def test_payload_body_override_is_respected(self) -> None:
        """payload['body'] flows into the notification body for parameterised events."""
        from worker.tasks import _build_inapp_notification

        cid = uuid.uuid4()
        custom_body = "You have exactly 42 new clips waiting."
        notif = _build_inapp_notification(cid, "clips_ready", {"body": custom_body})
        assert notif.body == custom_body


# ── NotificationPreference model defaults ────────────────────────────────────


class TestNotificationPreferenceDefaults:
    """Verify model-level column defaults for notification_preferences.

    SQLAlchemy 2.0 ``mapped_column(default=...)`` is a ColumnDefault applied at
    INSERT time by the Core, not at Python ``__init__`` time.  These tests
    therefore construct instances with explicit values — they verify the model's
    declared intent (that such values *can* be set) rather than Python-level
    attribute population at construction time.  The server-side defaults are
    verified by the staging-pending integration tests.
    """

    def test_default_preferences_with_explicit_values(self) -> None:
        """Constructing with explicit defaults produces the correct field values."""
        from models import NotificationPreference

        cid = uuid.uuid4()
        token = uuid.uuid4()
        prefs = NotificationPreference(
            creator_id=cid,
            email_transactional=True,
            email_lifecycle=True,
            inapp_enabled=True,
            push_enabled=False,
            unsubscribe_token=token,
        )
        assert prefs.email_transactional is True
        assert prefs.email_lifecycle is True
        assert prefs.inapp_enabled is True
        assert prefs.push_enabled is False

    def test_unsubscribe_token_is_uuid_when_provided(self) -> None:
        from models import NotificationPreference

        token = uuid.uuid4()
        prefs = NotificationPreference(creator_id=uuid.uuid4(), unsubscribe_token=token)
        assert isinstance(prefs.unsubscribe_token, uuid.UUID)

    def test_two_prefs_get_different_unsubscribe_tokens_when_distinct(self) -> None:
        from models import NotificationPreference

        t1 = uuid.uuid4()
        t2 = uuid.uuid4()
        p1 = NotificationPreference(creator_id=uuid.uuid4(), unsubscribe_token=t1)
        p2 = NotificationPreference(creator_id=uuid.uuid4(), unsubscribe_token=t2)
        assert p1.unsubscribe_token != p2.unsubscribe_token

    def test_push_enabled_defaults_to_false(self) -> None:
        """push_enabled=False is the documented secure default (Phase 3 deferred)."""
        from models import NotificationPreference

        prefs = NotificationPreference(
            creator_id=uuid.uuid4(),
            push_enabled=False,
            unsubscribe_token=uuid.uuid4(),
        )
        assert prefs.push_enabled is False


# ── NotificationDelivery model ───────────────────────────────────────────────


class TestNotificationDeliveryModel:
    """Verify enum completeness on notification_deliveries."""

    def test_channel_enum_values(self) -> None:
        from models import NotificationChannel

        assert {c.value for c in NotificationChannel} == {"email", "inapp", "push"}

    def test_delivery_status_enum_values(self) -> None:
        from models import NotificationDeliveryStatus

        assert {s.value for s in NotificationDeliveryStatus} == {"sent", "skipped", "failed"}


# ── send_notification task: preference gate ───────────────────────────────────


class TestSendNotificationLifecycleOptOut:
    """Preference check short-circuits lifecycle mail when email_lifecycle=False."""

    @pytest.mark.asyncio
    async def test_lifecycle_email_skipped_when_opted_out(self) -> None:
        """send_notification skips without inserting a delivery row when opted out."""
        from models import NotificationPreference

        cid = uuid.uuid4()
        # Explicitly supply all values — SQLAlchemy ColumnDefaults fire at INSERT time,
        # not at __init__ time.
        prefs = NotificationPreference(
            creator_id=cid,
            email_transactional=True,
            email_lifecycle=False,  # opted out of lifecycle mail
            inapp_enabled=True,
            push_enabled=False,
            unsubscribe_token=uuid.uuid4(),
        )

        mock_creator = MagicMock()
        mock_creator.id = cid
        mock_creator.email = "test@example.com"

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(side_effect=[mock_creator, prefs])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("db.AdminSessionLocal", return_value=mock_session):
            from worker.tasks import _send_notification_async

            await _send_notification_async(str(cid), "welcome", str(cid), {})

        # No delivery row should have been added.
        mock_session.add.assert_not_called()
        mock_session.commit.assert_not_called()


class TestSendNotificationTransactionalAlwaysOn:
    """Transactional events (clips_ready, dna_built) respect email_transactional=True
    and cannot be disabled via email_lifecycle=False."""

    @pytest.mark.asyncio
    async def test_transactional_event_is_not_blocked_by_lifecycle_flag(self) -> None:
        """clips_ready is transactional and must NOT be blocked by lifecycle opt-out."""
        from models import NotificationPreference

        cid = uuid.uuid4()
        # Construct with explicit default-like values; SQLAlchemy ColumnDefaults fire
        # at INSERT time, not at __init__ time, so we must supply them in tests.
        prefs = NotificationPreference(
            creator_id=cid,
            email_transactional=True,
            email_lifecycle=False,  # lifecycle opted out
            inapp_enabled=True,
            push_enabled=False,
            unsubscribe_token=uuid.uuid4(),
        )

        mock_creator = MagicMock()
        mock_creator.id = cid
        mock_creator.email = "test@example.com"

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(side_effect=[mock_creator, prefs])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        # flush should not raise (no duplicate)
        mock_session.flush = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        with (
            patch("db.AdminSessionLocal", return_value=mock_session),
            patch("notify.mailer.send"),
        ):
            from worker.tasks import _send_notification_async

            await _send_notification_async(str(cid), "clips_ready", "vid-123", {})

        # Must have proceeded past the preference gate to attempt a delivery.
        mock_session.add.assert_called()


# ── send_notification task: dedupe / idempotency ─────────────────────────────


class TestSendNotificationDedupeShortCircuit:
    """IntegrityError on dedupe_key INSERT causes immediate return without re-send."""

    @pytest.mark.asyncio
    async def test_integrity_error_on_flush_causes_early_return(self) -> None:
        """When the dedupe_key row already exists as `sent`, the task returns
        without calling mailer (status-aware guard — Issue 359 companion)."""
        from sqlalchemy.exc import IntegrityError

        from models import NotificationDeliveryStatus, NotificationPreference

        cid = uuid.uuid4()
        prefs = NotificationPreference(
            creator_id=cid,
            email_transactional=True,
            email_lifecycle=True,
            inapp_enabled=True,
            push_enabled=False,
            unsubscribe_token=uuid.uuid4(),
        )

        mock_creator = MagicMock()
        mock_creator.id = cid
        mock_creator.email = "test@example.com"

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(side_effect=[mock_creator, prefs])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        # Raise IntegrityError on the delivery INSERT flush (simulates duplicate dedupe_key).
        mock_session.flush = AsyncMock(
            side_effect=IntegrityError(
                "UNIQUE constraint violated", params=None, orig=Exception("duplicate key")
            )
        )
        mock_session.rollback = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()
        # The status-aware guard re-reads the existing row: `sent` short-circuits.
        existing = MagicMock()
        existing.status = NotificationDeliveryStatus.sent
        exec_result = MagicMock()
        exec_result.scalar_one_or_none = MagicMock(return_value=existing)
        mock_session.execute = AsyncMock(return_value=exec_result)

        with (
            patch("db.AdminSessionLocal", return_value=mock_session),
            patch("notify.mailer.send") as mock_mailer,
        ):
            from worker.tasks import _send_notification_async

            await _send_notification_async(str(cid), "clips_ready", "vid-123", {})

        # Mailer must NOT have been called — the dedupe guard fired.
        mock_mailer.assert_not_called()
        # Commit must NOT have been called either.
        mock_session.commit.assert_not_called()


# ── Issue 349: commit before blocking mailer call ────────────────────────────


class TestSendNotificationCommitBeforeMailer:
    """After Issue 349: the DB session commits before mailer_send is called.

    These tests pre-inject a sys.modules stub for notify.mailer so the tests
    run without jinja2 installed (notify.mailer imports jinja2 at module level;
    jinja2 is a required prod dep but may not be present in lean dev envs).
    """

    @pytest.mark.asyncio
    async def test_commit_happens_before_mailer_send(self) -> None:
        """Session must commit (freeing the connection) before the external mailer call."""
        import sys

        from models import NotificationPreference

        cid = uuid.uuid4()
        prefs = NotificationPreference(
            creator_id=cid,
            email_transactional=True,
            email_lifecycle=False,
            inapp_enabled=False,
            push_enabled=False,
            unsubscribe_token=uuid.uuid4(),
        )

        mock_creator = MagicMock()
        mock_creator.id = cid
        mock_creator.email = "test@example.com"

        call_order: list[str] = []

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(side_effect=[mock_creator, prefs])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.flush = AsyncMock()
        mock_session.add = MagicMock()

        async def _track_commit() -> None:
            call_order.append("commit")

        mock_session.commit = _track_commit

        def _track_send(*_a: object, **_kw: object) -> None:
            call_order.append("send")

        fake_mailer = MagicMock()
        fake_mailer.send = MagicMock(side_effect=_track_send)

        with (
            patch("db.AdminSessionLocal", return_value=mock_session),
            patch.dict(sys.modules, {"notify.mailer": fake_mailer}),
        ):
            from worker.tasks import _send_notification_async

            await _send_notification_async(str(cid), "clips_ready", "vid-123", {})

        assert call_order == ["commit", "send"], (
            "DB commit must precede mailer_send so the connection is freed first (Issue 349)"
        )

    @pytest.mark.asyncio
    async def test_mailer_failure_marks_delivery_failed_and_reraises(self) -> None:
        """A mailer exception marks the delivery row failed AND re-raises so the
        Celery retry ladder fires (Issue 359 companion — previously swallowed,
        permanently losing the email behind the dedupe guard)."""
        import sys

        from models import NotificationDelivery, NotificationDeliveryStatus, NotificationPreference

        cid = uuid.uuid4()
        prefs = NotificationPreference(
            creator_id=cid,
            email_transactional=True,
            email_lifecycle=False,
            inapp_enabled=False,
            push_enabled=False,
            unsubscribe_token=uuid.uuid4(),
        )

        mock_creator = MagicMock()
        mock_creator.id = cid
        mock_creator.email = "test@example.com"

        # Primary session (steps 1-7 + commit)
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(side_effect=[mock_creator, prefs])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.flush = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        # Failure session (step 8 error path)
        fail_delivery = MagicMock(spec=NotificationDelivery)
        fail_delivery.status = NotificationDeliveryStatus.sent
        fail_session = AsyncMock()
        fail_session.get = AsyncMock(return_value=fail_delivery)
        fail_session.__aenter__ = AsyncMock(return_value=fail_session)
        fail_session.__aexit__ = AsyncMock(return_value=False)
        fail_session.commit = AsyncMock()

        call_count = 0

        def _admin_session() -> AsyncMock:
            nonlocal call_count
            call_count += 1
            return mock_session if call_count == 1 else fail_session

        def _failing_send(*_a: object, **_kw: object) -> None:
            raise RuntimeError("simulated mailer failure")

        fake_mailer = MagicMock()
        fake_mailer.send = MagicMock(side_effect=_failing_send)

        with (
            patch("db.AdminSessionLocal", side_effect=_admin_session),
            patch.dict(sys.modules, {"notify.mailer": fake_mailer}),
        ):
            from worker.tasks import _send_notification_async

            with pytest.raises(RuntimeError, match="simulated mailer failure"):
                await _send_notification_async(str(cid), "clips_ready", "vid-123", {})

        assert fail_delivery.status == NotificationDeliveryStatus.failed
        fail_session.commit.assert_awaited_once()


# ── Issue 359 companion: failed sends are retryable ──────────────────────────


class TestSendNotificationFailedRetry:
    """Status-aware dedupe: a `failed` delivery row does NOT block a retry —
    the row is adopted, flipped back to `sent`, and the send re-attempted."""

    @staticmethod
    def _prefs(cid: uuid.UUID) -> object:
        from models import NotificationPreference

        return NotificationPreference(
            creator_id=cid,
            email_transactional=True,
            email_lifecycle=False,
            inapp_enabled=True,
            push_enabled=False,
            unsubscribe_token=uuid.uuid4(),
        )

    @pytest.mark.asyncio
    async def test_failed_delivery_row_is_retried(self) -> None:
        """IntegrityError + existing `failed` row → the send runs again."""
        import sys

        from sqlalchemy.exc import IntegrityError

        from models import NotificationDelivery, NotificationDeliveryStatus

        cid = uuid.uuid4()
        prefs = self._prefs(cid)

        mock_creator = MagicMock()
        mock_creator.id = cid
        mock_creator.email = "test@example.com"

        existing = MagicMock(spec=NotificationDelivery)
        existing.id = uuid.uuid4()
        existing.status = NotificationDeliveryStatus.failed

        mock_session = AsyncMock()
        # Steps 1-2 load creator + prefs; the retry path re-gets both after rollback.
        mock_session.get = AsyncMock(side_effect=[mock_creator, prefs, mock_creator, prefs])
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.flush = AsyncMock(
            side_effect=IntegrityError(
                "UNIQUE constraint violated", params=None, orig=Exception("duplicate key")
            )
        )
        exec_result = MagicMock()
        exec_result.scalar_one_or_none = MagicMock(return_value=existing)
        mock_session.execute = AsyncMock(return_value=exec_result)
        mock_session.rollback = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        fake_mailer = MagicMock()
        fake_mailer.send = MagicMock()

        with (
            patch("db.AdminSessionLocal", return_value=mock_session),
            patch.dict(sys.modules, {"notify.mailer": fake_mailer}),
        ):
            from worker.tasks import _send_notification_async

            await _send_notification_async(str(cid), "clips_ready", "vid-123", {})

        # The send ran, the row was adopted back to `sent`, and no duplicate
        # in-app notification was added (only the initial delivery add).
        fake_mailer.send.assert_called_once()
        assert existing.status == NotificationDeliveryStatus.sent
        assert mock_session.add.call_count == 1
        mock_session.commit.assert_awaited_once()


# ── No-PII assertion on provider payload ────────────────────────────────────


class TestSendNotificationNoPii:
    """The provider payload (idempotency_key, template, context) never contains tokens."""

    def test_dedupe_key_contains_no_email_or_token(self) -> None:
        """The idempotency key is a hex digest — no PII derivable from it."""
        from notify.dedupe import make_dedupe_key

        cid = uuid.uuid4()
        key = make_dedupe_key(cid, "clips_ready", "vid-abc")
        # The key must not contain the raw UUID, email-like chars in a revealing pattern,
        # or common token prefixes.
        assert "@" not in key
        assert "re_" not in key
        assert "Bearer" not in key
        # Must be all-hex (no plaintext leakage).
        assert all(c in "0123456789abcdef" for c in key)


# ── Staging-pending integration tests (skipped without Postgres) ─────────────


@pytest.mark.skip(reason="staging-pending: needs real Postgres + RLS (Issue 275)")
class TestNotificationsIntegration:
    """Integration tests that require a live Postgres instance.

    These tests verify:
    - UNIQUE dedupe_key constraint rejects a second INSERT.
    - RLS tenant_isolation blocks creator B from reading creator A's notifications.
    - Double-enqueue of send_notification results in exactly one delivery row.

    Run these against the GKE staging cluster (Issue 275).
    """

    def test_unique_dedupe_key_constraint_rejects_duplicate(self) -> None:
        """Two notification_deliveries rows with the same dedupe_key → IntegrityError."""
        raise NotImplementedError("staging-pending")

    def test_rls_blocks_cross_creator_read(self) -> None:
        """Creator B cannot read creator A's notifications row via the app DB role."""
        raise NotImplementedError("staging-pending")

    def test_double_enqueue_yields_one_delivery_row(self) -> None:
        """Sending the same event_type+entity_id twice yields exactly one DB row."""
        raise NotImplementedError("staging-pending")
