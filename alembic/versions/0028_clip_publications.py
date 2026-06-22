"""Add clip_publications — YouTube publish attempts (Issue 195)

Revision ID: 0028
Revises: 0027
Create Date: 2026-06-22

NB: renumbered 0027 → 0028 after the parallel privacy branch's
0027_data_exports landed on main first (avoids two alembic heads).

A tenant table tracking each YouTube publish attempt for a clip.

- ``task_id`` (the Celery task id) is UNIQUE so an at-least-once redelivery is
  idempotent (the task finds the existing row instead of double-posting).
- ``creator_id`` is direct, so it gets the same ``tenant_isolation`` RLS policy
  (ENABLE + FORCE) as the parent tables in 0010 / 0026.
- Issue 196 extends this table with ``scheduled_at`` / ``platform`` for the
  scheduled-publish flow; this migration creates the base.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "clip_publications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "clip_id",
            UUID(as_uuid=True),
            sa.ForeignKey("clips.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "creator_id",
            UUID(as_uuid=True),
            sa.ForeignKey("creators.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("youtube_video_id", sa.String(32), nullable=True),
        sa.Column(
            "status",
            sa.Enum("pending", "running", "done", "failed", name="publish_status_enum"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # UNIQUE task_id = the idempotency key for at-least-once redelivery.
    op.create_unique_constraint("uq_clip_publications_task_id", "clip_publications", ["task_id"])
    op.create_index("ix_clip_publications_creator_id", "clip_publications", ["creator_id"])
    op.create_index("ix_clip_publications_clip_id", "clip_publications", ["clip_id"])

    # RLS on creator_id — same tenant_isolation pattern as 0010 / 0026.
    op.execute("ALTER TABLE clip_publications ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE clip_publications FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON clip_publications;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON clip_publications
            USING (creator_id = current_setting('app.creator_id', true)::uuid)
            WITH CHECK (creator_id = current_setting('app.creator_id', true)::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON clip_publications;")
    op.execute("ALTER TABLE clip_publications NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE clip_publications DISABLE ROW LEVEL SECURITY;")
    op.drop_index("ix_clip_publications_clip_id", table_name="clip_publications")
    op.drop_index("ix_clip_publications_creator_id", table_name="clip_publications")
    op.drop_constraint("uq_clip_publications_task_id", "clip_publications", type_="unique")
    op.drop_table("clip_publications")
    op.execute("DROP TYPE IF EXISTS publish_status_enum;")
