"""Add videos.audio_uri — separate the audio derivative from the source video

Revision ID: 0039
Revises: 0038
Create Date: 2026-07-01

The core render loop was broken: ``ingest_video`` extracted the audio to a WAV,
**overwrote the single ``source_uri`` column with the audio key, and deleted the
original mp4**. Transcription/signals only need audio, but the renderer needs the
VIDEO (9:16 active-speaker reframe extracts keyframes) — so every clip render
failed (``ffmpeg -i …wav -vframes 1 …jpg`` → "Output file does not contain any
stream"). No uploaded video could ever produce a playable clip.

This column lets the two derivatives live side by side: ``source_uri`` stays the
original video (what render needs, retained for the ``SOURCE_MEDIA_RETENTION_HOURS``
window per COMPLIANCE.md), and ``audio_uri`` holds the extracted WAV (what
transcribe + signals read). Mirrors the industry-standard mezzanine pattern:
retain the source until all derivatives/renditions are produced, then lifecycle-purge.

Nullable with no default: existing rows keep NULL. They are unrecoverable anyway
(their videos were already deleted by the old code), so no backfill is possible or
attempted. The downgrade drops the column.
"""

import sqlalchemy as sa

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("videos", sa.Column("audio_uri", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("videos", "audio_uri")
