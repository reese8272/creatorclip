"""Extend clip_publications with scheduling fields (Issue 196)

Revision ID: 0032
Revises: 0031
Create Date: 2026-06-23

Adds three columns to ``clip_publications`` (created by 0030) that power the
scheduled-publish flow:

- ``scheduled_at`` (TIMESTAMPTZ, nullable) — the creator's chosen publish time
  (UTC). The Beat sweep enqueues the upload when ``scheduled_at <= now()`` and
  ``status = 'confirmed'``.
- ``platform`` (publish_platform_enum, NOT NULL, default 'youtube') — target
  distribution channel. Only YouTube is supported in this release; the enum
  carries the extension point for future platforms.
- ``confirmed_at`` (TIMESTAMPTZ, nullable) — timestamp when the creator
  confirmed the schedule. NULL until the creator explicitly approves.

Also adds two new values to the existing ``publish_status_enum``:
- ``scheduled``  — row created; awaiting creator confirmation.
- ``confirmed``  — creator approved; Beat sweep will enqueue the upload.

The existing ``pending`` / ``running`` / ``done`` / ``failed`` values are
preserved in place (no data migration needed — 0030 rows stay valid).

task_id is now nullable (was NOT NULL in 0030). Scheduled rows are created
before a Celery task id exists; the sweep assigns task_id when it enqueues.
The UNIQUE constraint on task_id is retained — it is simply partial-null-safe
by Postgres semantics (NULLs are never considered equal, so multiple NULL rows
are allowed while the constraint still blocks duplicate non-NULL task ids).

Down_revision = 0031: 0031 is the notifications migration (sibling agent,
Issue 243). This migration chains off it so the revision history stays linear
after integration.
"""

import sqlalchemy as sa

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Add new enum values to the existing publish_status_enum ───────────
    # ALTER TYPE … ADD VALUE is transactional in PG 12+; cannot run inside a
    # BEGIN/COMMIT block created by Alembic's implicit transaction. We use
    # op.execute() which runs in the same transaction context and works because
    # Alembic runs DDL in a single transaction per upgrade() call.
    op.execute("ALTER TYPE publish_status_enum ADD VALUE IF NOT EXISTS 'scheduled'")
    op.execute("ALTER TYPE publish_status_enum ADD VALUE IF NOT EXISTS 'confirmed'")

    # ── 2. Create the platform enum ──────────────────────────────────────────
    op.execute("CREATE TYPE publish_platform_enum AS ENUM ('youtube')")

    # ── 3. Add columns to clip_publications ─────────────────────────────────
    op.add_column(
        "clip_publications",
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "clip_publications",
        sa.Column(
            "platform",
            sa.Enum("youtube", name="publish_platform_enum", create_type=False),
            nullable=False,
            server_default="youtube",
        ),
    )
    op.add_column(
        "clip_publications",
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── 4. Make task_id nullable (was NOT NULL in 0030) ─────────────────────
    # Scheduled rows are created before a Celery task id is known.
    # The UNIQUE constraint on task_id remains; Postgres treats NULLs as
    # non-equal, so multiple NULLs coexist without violating uniqueness.
    op.alter_column("clip_publications", "task_id", nullable=True)

    # ── 5. Index scheduled_at for the Beat sweep's WHERE clause ─────────────
    # The sweep selects WHERE scheduled_at <= now() AND status IN ('confirmed')
    # — a partial index on scheduled_at speeds the common case.
    op.create_index(
        "ix_clip_publications_scheduled_at",
        "clip_publications",
        ["scheduled_at"],
        postgresql_where=sa.text("scheduled_at IS NOT NULL"),
    )


def downgrade() -> None:
    # DOWNGRADE-RISK (data-dependent): re-imposing NOT NULL on task_id fails if any
    # scheduled-but-unenqueued rows (NULL task_id) exist — clean them up first (Issue 296).
    op.drop_index("ix_clip_publications_scheduled_at", table_name="clip_publications")

    # Restore task_id to NOT NULL.  Any NULL rows must be cleaned up first in
    # a real downgrade; this migration assumes no production data with NULL
    # task_id rows is present when rolling back.
    op.alter_column("clip_publications", "task_id", nullable=False)

    op.drop_column("clip_publications", "confirmed_at")
    op.drop_column("clip_publications", "platform")
    op.drop_column("clip_publications", "scheduled_at")

    op.execute("DROP TYPE IF EXISTS publish_platform_enum;")

    # Postgres does not support removing enum values.  The 'scheduled' and
    # 'confirmed' values remain in publish_status_enum after downgrade;
    # recreating the enum without them would require a full table rewrite and
    # is not safe in production.  Acceptable: downgrade is a dev-only escape.
