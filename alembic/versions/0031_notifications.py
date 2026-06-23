"""Add notification_preferences, notification_deliveries, notifications (Issue 243)

Revision ID: 0031
Revises: 0030
Create Date: 2026-06-23

Three tables for the notification infrastructure:

- ``notification_preferences``  — per-creator consent + channel opt-out state.
  Primary key is ``creator_id`` (one row/creator); no RLS policy needed because
  every application query is keyed on creator_id directly (no cross-tenant query
  is possible without supplying the wrong creator_id, which the session JWT
  prevents).

- ``notification_deliveries`` — idempotency ledger (Inbox pattern).
  ``dedupe_key`` UNIQUE = SHA-256(creator_id:event_type:entity_id) is the
  primary deduplication guard so Celery at-least-once redelivery cannot
  double-send.  No RLS policy (internal audit table; not exposed via
  creator-facing API).

- ``notifications`` — durable in-app notification center (Issue 81 / Issue 243).
  RLS ENABLE + FORCE + tenant_isolation policy mirrors chat_conversations
  (migration 0026) so the database enforces per-creator isolation at the row
  level, independently of the application layer.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── notification_preferences ─────────────────────────────────────────────
    op.create_table(
        "notification_preferences",
        sa.Column(
            "creator_id",
            UUID(as_uuid=True),
            sa.ForeignKey("creators.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        # Legally always-on (CAN-SPAM / GDPR Art. 6(1)(b) — relationship mail).
        sa.Column(
            "email_transactional",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        # Lifecycle / commercial-leaning mail — unsubscribable.
        sa.Column(
            "email_lifecycle",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "inapp_enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        # Web push deferred to Phase 3 — defaults off.
        sa.Column(
            "push_enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        # UUID4 token for no-auth one-click unsubscribe GET /unsubscribe/{token}.
        sa.Column(
            "unsubscribe_token",
            UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_unique_constraint(
        "uq_notification_preferences_unsubscribe_token",
        "notification_preferences",
        ["unsubscribe_token"],
    )

    # ── notification_deliveries ──────────────────────────────────────────────
    op.create_table(
        "notification_deliveries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "creator_id",
            UUID(as_uuid=True),
            sa.ForeignKey("creators.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.String(128), nullable=False),
        sa.Column(
            "channel",
            sa.Enum("email", "inapp", "push", name="notification_channel_enum"),
            nullable=False,
        ),
        # SHA-256(creator_id:event_type:entity_id) — 64 hex chars.
        # UNIQUE is the primary deduplication guard for at-least-once Celery delivery.
        sa.Column("dedupe_key", sa.String(64), nullable=False),
        # Resend message id for deliverability debugging — no PII, opaque provider id.
        sa.Column("provider_message_id", sa.String(128), nullable=True),
        sa.Column(
            "status",
            sa.Enum("sent", "skipped", "failed", name="notification_delivery_status_enum"),
            nullable=False,
            server_default="sent",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_unique_constraint(
        "uq_notification_deliveries_dedupe_key",
        "notification_deliveries",
        ["dedupe_key"],
    )
    op.create_index(
        "ix_notification_deliveries_creator_id",
        "notification_deliveries",
        ["creator_id"],
    )

    # ── notifications (in-app center) ────────────────────────────────────────
    op.create_table(
        "notifications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "creator_id",
            UUID(as_uuid=True),
            sa.ForeignKey("creators.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(64), nullable=False, index=True),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("link_url", sa.String(512), nullable=True),
        # NULL = unread.
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True),
        # NULL = not dismissed.
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_notifications_creator_id", "notifications", ["creator_id"])

    # RLS on notifications — mirrors chat_conversations in 0026.
    # ENABLE + FORCE + tenant_isolation so the DB enforces per-creator isolation
    # independently of the application layer.
    op.execute("ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE notifications FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON notifications;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON notifications
            USING (creator_id = current_setting('app.creator_id', true)::uuid)
            WITH CHECK (creator_id = current_setting('app.creator_id', true)::uuid);
        """
    )


def downgrade() -> None:
    # notifications
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON notifications;")
    op.execute("ALTER TABLE notifications NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE notifications DISABLE ROW LEVEL SECURITY;")
    op.drop_index("ix_notifications_creator_id", table_name="notifications")
    op.drop_table("notifications")

    # notification_deliveries
    op.drop_index(
        "ix_notification_deliveries_creator_id",
        table_name="notification_deliveries",
    )
    op.drop_constraint(
        "uq_notification_deliveries_dedupe_key",
        "notification_deliveries",
        type_="unique",
    )
    op.drop_table("notification_deliveries")
    op.execute("DROP TYPE IF EXISTS notification_delivery_status_enum;")
    op.execute("DROP TYPE IF EXISTS notification_channel_enum;")

    # notification_preferences
    op.drop_constraint(
        "uq_notification_preferences_unsubscribe_token",
        "notification_preferences",
        type_="unique",
    )
    op.drop_table("notification_preferences")
