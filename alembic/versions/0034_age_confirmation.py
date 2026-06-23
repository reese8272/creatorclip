"""Add minimum_age_confirmed_at column to creators (Issue 300)

Revision ID: 0034
Revises: 0033
Create Date: 2026-06-23

Adds one nullable column to ``creators`` that stores the COPPA 13+
minimum-age attestation timestamp:

- ``minimum_age_confirmed_at`` (TIMESTAMPTZ, nullable) — UTC timestamp when
  the creator checked the "I confirm I am 13 or older" checkbox and proceeded
  through Google OAuth for the first time.  NULL on pre-0034 rows ("no
  recorded age attestation" — legacy creators that signed up before the gate
  shipped).

The column is nullable so the migration is backward-compatible with all
existing creator rows (no data migration required).

Age-neutral phrasing ("I confirm I am 13 or older") is the FTC-recommended
screening pattern per the amended COPPA Rule (16 CFR Part 312, effective
2025-06-23): a neutral affirmation avoids a yes/no question that nudges the
answer, and the attestation is COPPA-compliant evidence that the operator
takes reasonable measures to avoid collecting PII from children under 13.

Down_revision = 0033: chains off the clickwrap-consent migration so the
revision history stays linear.
"""

import sqlalchemy as sa

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "creators",
        sa.Column("minimum_age_confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("creators", "minimum_age_confirmed_at")
