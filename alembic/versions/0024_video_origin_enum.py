"""Add Video.origin enum (Issue 139)

Revision ID: 0024
Revises: 0023
Create Date: 2026-06-16
"""

import sqlalchemy as sa

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


_ENUM_NAME = "video_origin_enum"
_ENUM_VALUES = ("catalog", "link", "upload")


def upgrade() -> None:
    # Explicit ENUM create (mirrors 0022) so downgrade is reversible.
    video_origin = sa.Enum(*_ENUM_VALUES, name=_ENUM_NAME)
    video_origin.create(op.get_bind(), checkfirst=True)

    # Add nullable first so existing rows don't violate NOT NULL, then backfill
    # from source_uri — the column we're replacing as the discriminator.
    op.add_column("videos", sa.Column("origin", video_origin, nullable=True))

    # Backfill: a stored source_uri means the row was uploaded; everything else
    # was a catalog/DNA reference. We derive from source_uri rather than a
    # blanket server_default because a default of 'upload' (or 'link') would
    # wrongly resurface every catalog-only row that Issue 90 deliberately hid.
    # NOTE: pre-existing linked rows (source_uri NULL) backfill to 'catalog' and
    # stay hidden — they're indistinguishable from catalog rows in old data and
    # are unrecoverable. The fix is forward-looking (new links set origin='link').
    op.execute("UPDATE videos SET origin = 'upload' WHERE source_uri IS NOT NULL")
    op.execute("UPDATE videos SET origin = 'catalog' WHERE source_uri IS NULL")

    # Now lock it down. New rows always set origin explicitly in the app layer;
    # the model default ('upload') is the safety net for any direct insert.
    op.alter_column("videos", "origin", nullable=False, server_default="upload")


def downgrade() -> None:
    op.drop_column("videos", "origin")
    sa.Enum(*_ENUM_VALUES, name=_ENUM_NAME).drop(op.get_bind(), checkfirst=True)
