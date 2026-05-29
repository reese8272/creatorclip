"""creator_dna.build_job_id + one-confirmed-per-creator index (Issue 63)

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-05-29

Issue 63 — make DNA build idempotent under Celery at-least-once delivery and
enforce the documented "only one confirmed DNA per creator" invariant.

  build_job_id (VARCHAR NULL)
    The Celery task id of the build that created the draft. Stable across a
    redelivery of the same task but unique per user re-request, so a redelivery
    is detected and skipped (no duplicate draft, no duplicate Anthropic/Voyage
    spend). Indexed for the idempotency lookup.

  uq_one_confirmed_dna_per_creator (partial UNIQUE index)
    DB-level guarantee that at most one creator_dna row per creator is in the
    'confirmed' state. Two concurrent confirm_draft() calls can no longer both
    promote a draft — the loser raises IntegrityError and is handled.

NOTE: the partial UNIQUE index creation fails if existing data already has two
confirmed rows for a creator (the bug this prevents). That is the correct, loud
signal — clean up the duplicate before migrating.
"""

import sqlalchemy as sa

from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "creator_dna",
        sa.Column("build_job_id", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_creator_dna_build_job_id",
        "creator_dna",
        ["build_job_id"],
    )
    op.create_index(
        "uq_one_confirmed_dna_per_creator",
        "creator_dna",
        ["creator_id"],
        unique=True,
        postgresql_where=sa.text("status = 'confirmed'"),
    )


def downgrade() -> None:
    op.drop_index("uq_one_confirmed_dna_per_creator", table_name="creator_dna")
    op.drop_index("ix_creator_dna_build_job_id", table_name="creator_dna")
    op.drop_column("creator_dna", "build_job_id")
