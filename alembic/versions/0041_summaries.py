"""Add summaries — stream-VOD recap artifacts (Issue 190)

Revision ID: 0041
Revises: 0040
Create Date: 2026-07-02

A tenant table for multi-segment recap artifacts ("upload a past-stream VOD,
get a 5-10 min recap"). A dedicated table rather than overloading ``clips``:
a montage's many (start,end) segments do not fit a single start_s/end_s row.

- ``segments`` is a JSONB list; element shape (consumed verbatim by the
  Issue 191 renderer): {start_s, end_s, score, principle, rationale}.
- ``render_status`` reuses the existing ``render_status_enum`` type (0001);
  ``summary_status_enum`` is new to this migration.
- ``creator_id`` is direct, so it gets the same ``tenant_isolation`` RLS
  policy (ENABLE + FORCE, GUC predicate) as 0010 / 0037 / 0038.
- FK cascades purge rows on account/video deletion (right-to-erasure).
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

# Both enums are declared with the DIALECT type and create_type=False so the
# CREATE TABLE never emits CREATE TYPE. On generic ``sa.Enum`` the kwarg is
# dialect-dependent and was silently ineffective under the prod VM's
# SQLAlchemy, emitting a duplicate ``CREATE TYPE render_status_enum`` that
# aborted the 2026-07-02 deploy. ``summary_status_enum`` is created explicitly
# with checkfirst so a re-run after a partial failure is also safe.
_render_status = postgresql.ENUM(
    "pending",
    "running",
    "done",
    "failed",
    name="render_status_enum",
    create_type=False,
)
_summary_status = postgresql.ENUM(
    "pending", "ready", "failed", name="summary_status_enum", create_type=False
)


def upgrade() -> None:
    _summary_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "summaries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "creator_id",
            sa.Uuid(),
            sa.ForeignKey("creators.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "video_id",
            sa.Uuid(),
            sa.ForeignKey("videos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_duration_s", sa.Integer(), nullable=False),
        sa.Column(
            "segments",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("dna_version", sa.Integer(), nullable=True),
        sa.Column("render_uri", sa.Text(), nullable=True),
        sa.Column(
            "render_status",
            # Type already exists (0001_initial_schema); never re-created here.
            _render_status,
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "status",
            _summary_status,
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_summaries_creator_id", "summaries", ["creator_id"])
    op.create_index("ix_summaries_video_id", "summaries", ["video_id"])

    # RLS on creator_id — same tenant_isolation pattern as 0010 / 0037 / 0038.
    op.execute("ALTER TABLE summaries ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE summaries FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON summaries;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON summaries
            USING (creator_id = current_setting('app.creator_id', true)::uuid)
            WITH CHECK (creator_id = current_setting('app.creator_id', true)::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON summaries;")
    op.execute("ALTER TABLE summaries NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE summaries DISABLE ROW LEVEL SECURITY;")
    op.drop_index("ix_summaries_video_id", table_name="summaries")
    op.drop_index("ix_summaries_creator_id", table_name="summaries")
    op.drop_table("summaries")
    # render_status_enum predates this migration (0001) and is NOT dropped.
    op.execute("DROP TYPE IF EXISTS summary_status_enum;")
