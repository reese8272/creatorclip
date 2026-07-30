"""Add creator_style_notes (distilled style preferences)

Revision ID: 0049
Revises: 0048
Create Date: 2026-07-30

Issue 371 — the "why" behind review feedback finally teaches the system:
clip-level feedback tags/notes + video-level style reviews are LLM-distilled
into a compact STYLE PREFERENCES text block. One row per creator (the
``creator_style`` single-row precedent) — NOT columns on creator_dna (which is
versioned with a confirmed/draft lifecycle) or preference_models (pruned to 5
versions, which would delete the notes).

Creator-derived content only; creator_id FK cascades on account deletion
(right-to-erasure). RLS tenant_isolation mirrors 0010/0037/0048.
"""

import sqlalchemy as sa

from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "creator_style_notes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "creator_id",
            sa.Uuid(),
            sa.ForeignKey("creators.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("notes_text", sa.Text(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.Column("last_input_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # Per-creator isolation via RLS on creator_id — mirrors 0010/0037/0048.
    op.execute("ALTER TABLE creator_style_notes ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE creator_style_notes FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON creator_style_notes;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON creator_style_notes
            USING (creator_id = current_setting('app.creator_id', true)::uuid)
            WITH CHECK (creator_id = current_setting('app.creator_id', true)::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON creator_style_notes;")
    op.execute("ALTER TABLE creator_style_notes NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE creator_style_notes DISABLE ROW LEVEL SECURITY;")
    op.drop_table("creator_style_notes")
