"""Make videos.youtube_video_id nullable (Issue 317)

Revision ID: 0035
Revises: 0034
Create Date: 2026-06-24

"Link a video" (paste-a-URL) is retired in favour of "upload a video file":
under the YouTube ToS we never download source media from a link, so a link
alone could only ever sit at ``ingest_status=pending`` forever. The compliant
source is the raw file the creator uploads.

A standalone raw-file upload (e.g. OBS recording, unpublished footage) has no
published YouTube video to point at, so ``youtube_video_id`` becomes nullable.
The ``uq_creator_youtube_video`` unique constraint is unchanged and still
holds: PostgreSQL treats NULLs as distinct, so any number of un-associated
uploads coexist per creator while a provided ID is still deduped per creator.

The column is widened from NOT NULL → NULL, so the upgrade is backward
compatible with every existing row (all currently carry a non-NULL id).

Down_revision = 0034: chains off the COPPA age-confirmation migration so the
revision history stays linear.

The downgrade re-imposes NOT NULL. Any rows inserted as standalone uploads
(NULL id) would block it, so the downgrade first stamps a deterministic
placeholder onto NULL rows before re-adding the constraint — keeping the
migration reversible without data loss of the row itself.
"""

import sqlalchemy as sa

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "videos",
        "youtube_video_id",
        existing_type=sa.String(length=32),
        nullable=True,
    )


def downgrade() -> None:
    # DOWNGRADE-RISK (data-dependent): NULL youtube_video_id rows are overwritten
    # with placeholders below — the original NULL-ness is not recoverable (Issue 296).
    # Stamp a deterministic placeholder onto any standalone-upload rows so the
    # NOT NULL constraint can be re-imposed without dropping data. The first
    # 32 chars of the row's own UUID (hyphens removed) are unique per creator.
    op.execute(
        """
        UPDATE videos
        SET youtube_video_id = LEFT(REPLACE(id::text, '-', ''), 32)
        WHERE youtube_video_id IS NULL
        """
    )
    op.alter_column(
        "videos",
        "youtube_video_id",
        existing_type=sa.String(length=32),
        nullable=False,
    )
