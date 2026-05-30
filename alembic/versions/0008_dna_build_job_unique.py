"""creator_dna.build_job_id partial UNIQUE (Issue 76 — build_dna concurrent idempotency)

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-05-29

Issue 76 — the `build_dna` idempotency key (`build_job_id`, the Celery task id) was
only *indexed* (0005), not unique. The serial-redelivery short-circuit was safe, but
two *concurrent* deliveries of the same task id could both pass the existence check and
both run the paid Anthropic brief + Voyage embeddings before colliding on
`uq_dna_creator_version`.

The application fix takes a per-creator `pg_advisory_xact_lock` at the top of the build
transaction and re-checks `build_job_id` under it, so the second delivery blocks, re-reads
the committed draft, and short-circuits before any paid call. This migration is the
structural backstop: a partial UNIQUE index on `build_job_id` (WHERE NOT NULL) so the DB
refuses a duplicate draft for one job id even if the lock path is ever bypassed. It
replaces the plain `ix_creator_dna_build_job_id` (the unique index serves the lookup too).

NOTE: creating the UNIQUE index fails loudly if existing data already has two rows with
the same non-null `build_job_id` — that is the correct signal; clean up the duplicate
draft before migrating.
"""

import sqlalchemy as sa

from alembic import op

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_creator_dna_build_job_id", table_name="creator_dna")
    op.create_index(
        "uq_creator_dna_build_job_id",
        "creator_dna",
        ["build_job_id"],
        unique=True,
        postgresql_where=sa.text("build_job_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_creator_dna_build_job_id", table_name="creator_dna")
    op.create_index(
        "ix_creator_dna_build_job_id",
        "creator_dna",
        ["build_job_id"],
    )
