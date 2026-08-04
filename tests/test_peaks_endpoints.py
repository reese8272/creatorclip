"""Waveform-serving endpoint (Issue 392).

Isolation here is asserted against a MOCKED session that emulates the get_owned
predicate, exactly as in test_poster_endpoints.py. The authoritative proof lives
in the integration lane, which runs under the real RLS role.
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from auth import get_current_creator
from db import get_session
from main import app
from models import Creator, Video
from tests._helpers import owned_lookup_result

_PEAKS_BODY = json.dumps(
    {
        "version": 1,
        "sample_rate": 16000,
        "samples_per_pixel": 512,
        "bits": 8,
        "length": 2,
        "data": [-30, 40, -20, 25],
    }
).encode()


def _creator() -> MagicMock:
    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    return c


def _video(creator_id, peaks_uri):
    row = MagicMock(spec=Video)
    row.id = uuid.uuid4()
    row.creator_id = creator_id
    row.peaks_uri = peaks_uri
    return row


def _overrides(creator, row):
    async def _session():
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=lambda stmt, *a, **kw: owned_lookup_result(stmt, row)
        )
        yield session

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session


def _clear():
    app.dependency_overrides.clear()


class TestVideoPeaks:
    def test_serves_the_waveform_json_with_a_private_cache_header(self):
        creator = _creator()
        video = _video(creator.id, "s3://bucket/peaks/c/v.json")
        _overrides(creator, video)
        try:
            with patch("routers.videos.aread_bytes", AsyncMock(return_value=_PEAKS_BODY)):
                resp = TestClient(app).get(f"/videos/{video.id}/peaks")
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "application/json"
            # `private` is load-bearing: the bytes are per-creator and must never
            # reach a shared or CDN cache.
            assert "private" in resp.headers["cache-control"]
            body = resp.json()
            # The BBC audiowaveform contract the client decodes against.
            assert body["bits"] == 8
            assert body["samples_per_pixel"] == 512
            assert len(body["data"]) == body["length"] * 2
        finally:
            _clear()

    def test_404_when_the_video_has_no_waveform(self):
        """Normal, not exceptional — the client draws a labelled flat track.

        Any video whose audio was purged before the backfill reached it is
        permanently in this state.
        """
        creator = _creator()
        video = _video(creator.id, None)
        _overrides(creator, video)
        try:
            resp = TestClient(app).get(f"/videos/{video.id}/peaks")
            assert resp.status_code == 404
        finally:
            _clear()

    def test_404_when_the_stored_object_is_gone(self):
        creator = _creator()
        video = _video(creator.id, "s3://bucket/peaks/c/v.json")
        _overrides(creator, video)
        try:
            with patch("routers.videos.aread_bytes", AsyncMock(return_value=None)):
                resp = TestClient(app).get(f"/videos/{video.id}/peaks")
            assert resp.status_code == 404
        finally:
            _clear()

    def test_another_creators_video_is_404_not_403(self):
        """get_owned never distinguishes "missing" from "not yours"."""
        creator = _creator()
        _overrides(creator, None)
        try:
            resp = TestClient(app).get(f"/videos/{uuid.uuid4()}/peaks")
            assert resp.status_code == 404
        finally:
            _clear()

    def test_the_uri_is_never_exposed_in_the_response(self):
        creator = _creator()
        video = _video(creator.id, "s3://bucket/peaks/c/v.json")
        _overrides(creator, video)
        try:
            with patch("routers.videos.aread_bytes", AsyncMock(return_value=_PEAKS_BODY)):
                resp = TestClient(app).get(f"/videos/{video.id}/peaks")
            assert b"s3://" not in resp.content
        finally:
            _clear()
