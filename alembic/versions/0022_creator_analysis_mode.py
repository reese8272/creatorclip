"""Add Creator.analysis_mode enum (Issue 125)

Revision ID: 0022
Revises: 0021
Create Date: 2026-06-08
"""

import sqlalchemy as sa

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


_ENUM_NAME = "analysis_mode_enum"
_ENUM_VALUES = ("auto", "selective", "manual")


def upgrade() -> None:
    # Create the Postgres ENUM type explicitly so we control the name + the
    # server_default below resolves correctly. SQLAlchemy's sa.Enum with
    # `create_constraint=False` would otherwise auto-create the type on first
    # column reference, but the explicit create makes downgrade reversible
    # and predictable.
    analysis_mode = sa.Enum(*_ENUM_VALUES, name=_ENUM_NAME)
    analysis_mode.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "creators",
        sa.Column(
            "analysis_mode",
            analysis_mode,
            nullable=False,
            # server_default lets existing rows backfill without a separate
            # data migration — every pre-Issue-125 creator becomes 'auto',
            # preserving today's implicit behavior.
            server_default="auto",
        ),
    )


def downgrade() -> None:
    op.drop_column("creators", "analysis_mode")
    sa.Enum(*_ENUM_VALUES, name=_ENUM_NAME).drop(op.get_bind(), checkfirst=True)
