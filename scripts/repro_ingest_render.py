"""Rung-2 verification of the migration-0039 fix: real ingest → render, locally.

Proves the fixed contract end to end: `_ingest_async` extracts audio to `audio_uri`
but RETAINS the original video on `source_uri`, and `_render_clip_async` then renders
a real 9:16 clip from that retained video. This is the exact path that was broken in
prod (ingest used to overwrite source_uri with audio + delete the mp4). Local only —
no prod, no R2 (STORAGE_BACKEND=local), no minutes spent externally.

Usage: PYTHONPATH=. .venv/bin/python scripts/repro_ingest_render.py "<path-to.mp4>"
"""

import os
import sys
import uuid
from pathlib import Path

from cryptography.fernet import Fernet

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://creatorclip:dev_password@localhost:5432/creatorclip"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-not-real")
os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "x")
os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "x")
os.environ.setdefault("OAUTH_REDIRECT_URI", "http://localhost:8000/auth/callback")
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-32-bytes-minimum-!")
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:8000")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("LOCAL_MEDIA_DIR", "/tmp/cc-repro-media")
os.environ.setdefault("LOG_DIR", "")

import asyncio  # noqa: E402

from sqlalchemy import delete  # noqa: E402

import db  # noqa: E402
from models import (  # noqa: E402
    Clip,
    ClipFormat,
    Creator,
    IngestStatus,
    RenderStatus,
    Video,
    VideoKind,
    VideoOrigin,
)
from worker.tasks import _ingest_async, _render_clip_async  # noqa: E402

VIDEO = Path(sys.argv[1]).resolve()


async def main() -> int:
    if not VIDEO.exists():
        print(f"FAIL: video not found: {VIDEO}")
        return 2
    db.recreate_engine()
    cid, vid, clipid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with db.AdminSessionLocal() as s:
        s.add(Creator(id=cid, google_sub=f"repro_{cid}", minutes_balance=1000))
        s.add(
            Video(
                id=vid,
                creator_id=cid,
                kind=VideoKind.long,
                origin=VideoOrigin.upload,
                ingest_status=IngestStatus.pending,
                source_uri=str(VIDEO),  # the uploaded video
            )
        )
        await s.commit()

    rc = 0
    try:
        # 1) Real ingest: extracts audio via ffmpeg, should set audio_uri + KEEP source_uri.
        await _ingest_async(str(vid))
        async with db.AdminSessionLocal() as s:
            v = await s.get(Video, vid)
            print(f"after ingest: source_uri={v.source_uri}")
            print(f"              audio_uri={v.audio_uri}")
        assert v.source_uri == str(VIDEO), "source_uri must still be the ORIGINAL VIDEO"
        assert v.audio_uri and v.audio_uri.endswith(".wav"), "audio_uri must hold the WAV"
        assert VIDEO.exists(), "the original video file must NOT be deleted"
        print("PASS: video retained on source_uri, audio on audio_uri, mp4 not deleted")

        # 2) Render a clip from the retained video (src=None → downloads source_uri).
        async with db.AdminSessionLocal() as s:
            s.add(
                Clip(
                    id=clipid,
                    video_id=vid,
                    creator_id=cid,
                    start_s=0.0,
                    end_s=8.0,
                    setup_start_s=0.0,
                    peak_s=4.0,
                    format=ClipFormat.short,
                    render_status=RenderStatus.pending,
                )
            )
            await s.commit()
        await _render_clip_async(str(clipid))
        async with db.AdminSessionLocal() as s:
            c = await s.get(Clip, clipid)
            print(f"after render: render_status={c.render_status.value} render_uri={c.render_uri}")
        assert c.render_status == RenderStatus.done and c.render_uri, "clip must render to done"
        out = Path(c.render_uri)
        assert out.exists() and out.stat().st_size > 0, "rendered clip file must exist + be non-empty"
        print(f"PASS: clip rendered from retained video → {out} ({out.stat().st_size} bytes)")
    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"FAIL: {type(exc).__name__}: {exc}")
        rc = 1
    finally:
        async with db.AdminSessionLocal() as s:
            await s.execute(delete(Clip).where(Clip.video_id == vid))
            await s.execute(delete(Video).where(Video.id == vid))
            await s.execute(delete(Creator).where(Creator.id == cid))
            await s.commit()
        print("cleaned up seeded rows")
    return rc


sys.exit(asyncio.run(main()))
