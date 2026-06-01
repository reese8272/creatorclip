"""Issue 113 — UNIQUE(creator_id) on improvement_briefs

Revision ID: 0016_improvement_brief_unique
Revises: 0015_creator_api_keys

The improvement-brief debounce (Issue 110) uses SELECT FOR UPDATE SKIP LOCKED
to prevent double-fire on concurrent re-POSTs to an existing row. However, two
truly-concurrent first-ever POSTs (no row exists yet for the creator) both skip
the lock, both insert, and without this constraint both succeed — firing the
Anthropic call twice and creating two rows.

This constraint makes the second insert fail with IntegrityError, which the
router catches, rolls back, re-queries, and returns the winning row's task_id.
"""

from alembic import op

revision = "0016_improvement_brief_unique"
down_revision = "0015_creator_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_improvement_briefs_creator_id",
        "improvement_briefs",
        ["creator_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_improvement_briefs_creator_id",
        "improvement_briefs",
        type_="unique",
    )
