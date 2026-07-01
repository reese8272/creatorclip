"""Rung-2 local reproduction: run the REAL render path on a real file, isolated.

Seeds a creator/video/clip in local Postgres, points source_uri at a local mp4,
then drives worker.tasks._render_clip_async directly (same code the auto-render
batch calls per clip). Reports render_status + render_uri + output existence, then
cleans up. Zero prod impact, no minutes, no R2 (STORAGE_BACKEND=local).

Usage: .venv/bin/python scripts/repro_render.py "<path-to.mp4>" [start_s] [end_s]
"""

import os
import sys
import uuid
from pathlib import Path

from cryptography.fernet import Fernet

# Env must be set BEFORE importing app modules (config fails fast on missing).
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
from worker.tasks import _render_clip_async  # noqa: E402

VIDEO = Path(sys.argv[1]).resolve()
START = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
END = float(sys.argv[3]) if len(sys.argv) > 3 else 10.0


async def main() -> int:
    if not VIDEO.exists():
        print(f"FAIL: video not found: {VIDEO}")
        return 2
    db.recreate_engine()
    cid, vid, clipid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with db.AdminSessionLocal() as s:
        s.add(Creator(id=cid, google_sub=f"repro_{cid}"))
        s.add(
            Video(
                id=vid,
                creator_id=cid,
                kind=VideoKind.long,
                origin=VideoOrigin.upload,
                ingest_status=IngestStatus.done,
                source_uri=str(VIDEO),  # non-null so the plan-load source check passes
                duration_s=END + 60,
            )
        )
        s.add(
            Clip(
                id=clipid,
                video_id=vid,
                creator_id=cid,
                start_s=START,
                end_s=END,
                setup_start_s=START,
                peak_s=(START + END) / 2,
                format=ClipFormat.short,
                render_status=RenderStatus.pending,
            )
        )
        await s.commit()
    print(f"seeded clip={clipid} range={START}-{END}s src={VIDEO.name}")

    rc = 0
    try:
        await _render_clip_async(str(clipid), src=VIDEO)
        print("render call returned without raising")
    except Exception as exc:  # noqa: BLE001 — this is the diagnostic
        import traceback

        traceback.print_exc()
        print(f"RENDER RAISED: {type(exc).__name__}: {exc}")
        rc = 1

    async with db.AdminSessionLocal() as s:
        clip = await s.get(Clip, clipid)
        uri = clip.render_uri
        status = clip.render_status
    print(f"RESULT: render_status={status.value} render_uri={uri}")
    if uri:
        out = Path(uri) if not uri.startswith("s3://") else None
        if out:
            exists = out.exists()
            size = out.stat().st_size if exists else 0
            print(f"OUTPUT FILE: {out} exists={exists} size={size}")

    async with db.AdminSessionLocal() as s:
        await s.execute(delete(Clip).where(Clip.id == clipid))
        await s.execute(delete(Video).where(Video.id == vid))
        await s.execute(delete(Creator).where(Creator.id == cid))
        await s.commit()
    print("cleaned up seeded rows")
    return rc


sys.exit(asyncio.run(main()))
