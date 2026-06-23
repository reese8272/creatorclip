"""Add versioned consent columns to creators (Issue 299)

Revision ID: 0033
Revises: 0032
Create Date: 2026-06-23

Adds three columns to ``creators`` that store the defensible clickwrap
consent artifact required by the 9th Circuit Chabolla v. ClassPass (2025)
affirmative-consent standard and GDPR Art. 7 recorded-consent requirement:

- ``terms_accepted_at`` (TIMESTAMPTZ, nullable) — UTC timestamp when the
  creator clicked the affirmative "I agree" checkbox and proceeded through
  Google OAuth for the first time.  NULL on pre-0033 rows ("no recorded
  consent" — legacy creators that signed up before the clickwrap shipped).

- ``terms_version`` (VARCHAR(32), nullable) — version string of the
  Terms of Service shown at acceptance time (ISO-8601 date, e.g.
  "2026-06-23").  Recorded from ``settings.TOS_VERSION`` at callback time.
  A future re-prompt path compares stored vs current to detect material
  changes.

- ``privacy_version`` (VARCHAR(32), nullable) — version of the Privacy
  Policy shown at acceptance time.  Recorded from ``settings.PRIVACY_VERSION``.

All columns are nullable so the migration is backward-compatible with
existing creator rows (no data migration needed).

Down_revision = 0032: 0032 is the clip-publication-schedule migration.
This migration chains off it so the revision history stays linear.
"""

import sqlalchemy as sa

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "creators",
        sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "creators",
        sa.Column("terms_version", sa.String(32), nullable=True),
    )
    op.add_column(
        "creators",
        sa.Column("privacy_version", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("creators", "privacy_version")
    op.drop_column("creators", "terms_version")
    op.drop_column("creators", "terms_accepted_at")
