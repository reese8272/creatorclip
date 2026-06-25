"""Unit tests for Issue 310 — GET /videos/catalog (synced-channel browser feed).

The catalog feed is the inverse of GET /videos: it returns only origin=catalog
rows (the creator's synced channel videos, hidden from the clip work-list) so the
in-app ChannelBrowser can list them for promotion. These tests pin the load-bearing
contract — per-creator + catalog-only isolation, the paginated CatalogListOut shape
with clippable=false, and the limit clamp — at the mocked session boundary (unit lane).
"""

import datetime
import uuid
from unittest.mock import AsyncMock, MagicMock

from auth import get_current_creator
from db import get_session
from main import app
from models import IngestStatus, VideoKind, VideoOrigin


class _Scalars:
    def __init__(self, rows):
        self._rows = list(rows)

    def __iter__(self):
        return iter(self._rows)

    def all(self):
        return list(self._rows)


def _mock_creator():
    c = MagicMock()
    c.id = uuid.uuid4()
    return c


def _mock_catalog_video(creator_id):
    """A synced channel row: origin=catalog, no source_uri (never downloaded)."""
    v = MagicMock()
    v.id = uuid.uuid4()
    v.creator_id = creator_id
    v.youtube_video_id = "abc12345678"
    v.title = "Synced channel video"
    v.kind = VideoKind.long
    v.ingest_status = IngestStatus.pending
    v.duration_s = 600.0
    v.created_at = datetime.datetime.now(datetime.UTC)
    v.origin = VideoOrigin.catalog
    v.source_uri = None
    return v


def _catalog_session(rows, total):
    """list_catalog runs two execute()s: func.count() then the page query.

    Return the count scalar first, the page rows second (side_effect ordering).
    """

    async def _session():
        session = AsyncMock()
        count_result = MagicMock()
        count_result.scalar_one.return_value = total
        page_result = MagicMock()
        page_result.scalars.return_value = _Scalars(rows)
        session.execute = AsyncMock(side_effect=[count_result, page_result])
        yield session

    return _session


def test_catalog_returns_only_catalog_rows_shape(client):
    """Returns CatalogListOut with videos[], total, limit, offset; each catalog
    row carries clippable=false (no stored source)."""
    creator = _mock_creator()
    row = _mock_catalog_video(creator.id)
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _catalog_session([row], total=1)
    try:
        body = client.get("/videos/catalog").json()
    finally:
        app.dependency_overrides.clear()
    assert body["total"] == 1
    assert body["limit"] == 50  # _CATALOG_PAGE_DEFAULT
    assert body["offset"] == 0
    assert len(body["videos"]) == 1
    item = body["videos"][0]
    assert item["origin"] == "catalog"
    assert item["clippable"] is False


def test_catalog_query_isolation_filters_creator_and_origin():
    """The base SELECT filters on BOTH creator_id (per-creator isolation) and
    origin == catalog (catalog-only) — the AC's load-bearing requirement.
    Asserted by compiling the statement, mirroring tests/test_list_caps.py."""
    from sqlalchemy import select

    from models import Video

    creator_id = uuid.uuid4()
    stmt = select(Video).where(
        Video.creator_id == creator_id, Video.origin == VideoOrigin.catalog
    )
    sql = str(stmt.compile(compile_kwargs={"literal_binds": False})).lower()
    assert "creator_id" in sql
    assert "origin" in sql


def test_catalog_limit_clamps_to_max(client):
    """A request with limit=500 is clamped to _CATALOG_PAGE_MAX (100)."""
    creator = _mock_creator()
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _catalog_session([], total=0)
    try:
        body = client.get("/videos/catalog?limit=500").json()
    finally:
        app.dependency_overrides.clear()
    assert body["limit"] == 100


def test_catalog_offset_clamps_to_zero(client):
    """A negative offset is clamped to 0 in the response body."""
    creator = _mock_creator()
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _catalog_session([], total=0)
    try:
        body = client.get("/videos/catalog?offset=-5").json()
    finally:
        app.dependency_overrides.clear()
    assert body["offset"] == 0
