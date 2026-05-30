"""Creator stated identity (Issue 83 — append-only versioned intake)

Revision ID: 0012_creator_identity
Revises: 0011_widen_pack_id

Adds the ``creator_identity`` table — the creator's self-described "who I am /
who I'm for / what I won't do" layer that augments the inferred ``creator_dna``.
Append-only: each ``POST /creators/me/identity`` creates a new row and stamps
``superseded_at`` on the prior current row. Partial UNIQUE on
``(creator_id) WHERE superseded_at IS NULL`` is the DB-level guarantee that at
most one row per creator is "current" at any time (matches the
``uq_one_confirmed_dna_per_creator`` pattern in ``creator_dna``).

Fields follow the 2026 industry-standard intake shape (per DECISIONS entry):
- ``niches`` — JSONB array of YouTube Data API category IDs (strings)
- ``audience_summary`` — required free text (1–3 sentences)
- ``content_pillars`` / ``tone_tags`` / ``hard_nos`` — optional JSONB arrays
- ``mission`` / ``style_sample`` — optional narrative fields
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0012_creator_identity"
down_revision = "0011_widen_pack_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "creator_identity",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "creator_id",
            sa.Uuid(),
            sa.ForeignKey("creators.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("niches", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("audience_summary", sa.Text(), nullable=False),
        sa.Column("content_pillars", JSONB(), nullable=True),
        sa.Column("tone_tags", JSONB(), nullable=True),
        sa.Column("hard_nos", JSONB(), nullable=True),
        sa.Column("mission", sa.Text(), nullable=True),
        sa.Column("style_sample", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("creator_id", "version", name="uq_identity_creator_version"),
    )
    # At most one "current" identity per creator. Mirrors the dna partial-unique
    # pattern. Non-deferrable, so the supersede→insert must run in the right
    # order inside one transaction (or the new INSERT fails).
    op.create_index(
        "uq_one_current_identity_per_creator",
        "creator_identity",
        ["creator_id"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )
    # History lookups (most-recent-first) for the version-list endpoint.
    op.create_index(
        "ix_creator_identity_creator_version",
        "creator_identity",
        ["creator_id", sa.text("version DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_creator_identity_creator_version", table_name="creator_identity")
    op.drop_index("uq_one_current_identity_per_creator", table_name="creator_identity")
    op.drop_table("creator_identity")
