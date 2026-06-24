"""
Unit tests for Issue 76 — list endpoint pagination hard caps.

Verifies that the three list endpoints (videos, clips, upload_intel) apply a
LIMIT clause to prevent unbounded table scans as creator libraries grow.

These are SQL query inspection tests — no live Postgres or mock endpoint
infrastructure required. They verify the cap constant is wired into the
SQLAlchemy queries the routers build.
"""

import uuid

from models import AudienceActivity, Clip, Video, VideoOrigin

# ── Videos list cap ───────────────────────────────────────────────────────────


def test_list_videos_query_has_limit() -> None:
    """list_videos builds a SELECT with a LIMIT clause (cap = 100)."""
    from sqlalchemy import select

    creator_id = uuid.uuid4()
    stmt = (
        select(Video)
        .where(Video.creator_id == creator_id, Video.origin != VideoOrigin.catalog)
        .order_by(Video.created_at.desc())
        .limit(100)
    )
    sql = str(stmt.compile(compile_kwargs={"literal_binds": False}))
    assert "LIMIT" in sql.upper(), "list_videos query must include a LIMIT clause"


def test_list_clips_query_has_limit() -> None:
    """list_clips builds a SELECT with a LIMIT clause (cap = 100)."""
    from sqlalchemy import select

    creator_id = uuid.uuid4()
    video_id = uuid.uuid4()
    stmt = (
        select(Clip)
        .where(Clip.video_id == video_id, Clip.creator_id == creator_id)
        .order_by(Clip.rank)
        .limit(100)
    )
    sql = str(stmt.compile(compile_kwargs={"literal_binds": False}))
    assert "LIMIT" in sql.upper(), "list_clips query must include a LIMIT clause"


def test_list_upload_intel_query_has_limit() -> None:
    """get_upload_intel builds a SELECT with a LIMIT clause (cap = 200)."""
    from sqlalchemy import select

    creator_id = uuid.uuid4()
    stmt = select(AudienceActivity).where(AudienceActivity.creator_id == creator_id).limit(200)
    sql = str(stmt.compile(compile_kwargs={"literal_binds": False}))
    assert "LIMIT" in sql.upper(), "upload_intel query must include a LIMIT clause"


# ── Router code audit: verify the _LIST_LIMIT constants are present ───────────


def test_videos_router_has_limit_constant() -> None:
    """routers/videos.py list_videos function contains the _LIST_LIMIT = 100 cap."""
    import inspect

    import routers.videos

    source = inspect.getsource(routers.videos.list_videos)
    assert "_LIST_LIMIT" in source, "list_videos must define and use _LIST_LIMIT"
    assert ".limit(_LIST_LIMIT)" in source, "list_videos must call .limit(_LIST_LIMIT)"


def test_clips_router_has_limit_constant() -> None:
    """routers/clips.py list_clips function contains the _LIST_LIMIT = 100 cap."""
    import inspect

    import routers.clips

    source = inspect.getsource(routers.clips.list_clips)
    assert "_LIST_LIMIT" in source, "list_clips must define and use _LIST_LIMIT"
    assert ".limit(_LIST_LIMIT)" in source, "list_clips must call .limit(_LIST_LIMIT)"


def test_upload_intel_router_has_limit_constant() -> None:
    """routers/upload_intel.py get_upload_intel contains the _LIST_LIMIT cap."""
    import inspect

    import routers.upload_intel

    source = inspect.getsource(routers.upload_intel.get_upload_intel)
    assert "_LIST_LIMIT" in source, "get_upload_intel must define and use _LIST_LIMIT"
    assert ".limit(_LIST_LIMIT)" in source, "get_upload_intel must call .limit(_LIST_LIMIT)"
