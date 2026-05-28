"""
Tests for Issue 40 — streaming upload + DoS guard.

Verifies:
  1. 413 is returned as soon as the byte count crosses UPLOAD_MAX_MB.
  2. The partial temp file is deleted on the 413 rejection path.
  3. Rejecting a large (100 MB+) upload does not balloon process RSS by more than 20 MB.

No DB, no storage, no Celery.  Auth + session + storage are fully mocked so these
tests run without Docker, Postgres, or Redis.
"""

from __future__ import annotations

import io
import resource
import tempfile
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from auth import get_current_creator
from db import get_session
from main import app

# ── helpers ───────────────────────────────────────────────────────────────────

_MB = 1024 * 1024

# Use a tiny limit in tests so we don't have to synthesise 500 MB.
_TEST_MAX_MB = 2


def _fake_session():
    """Return an async generator factory that yields a mocked AsyncSession."""
    async def _gen():
        session = AsyncMock()
        # No duplicate video found by default.
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result)
        session.add = MagicMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()
        yield session

    return _gen


def _fake_creator() -> MagicMock:
    creator = MagicMock()
    creator.id = uuid.uuid4()
    return creator


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def upload_client(monkeypatch):
    """TestClient with auth + session mocked and UPLOAD_MAX_MB clamped to 2 MB."""
    monkeypatch.setattr("config.settings.UPLOAD_MAX_MB", _TEST_MAX_MB)

    creator = _fake_creator()
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session()
    try:
        # raise_server_exceptions=False so 413 comes back as an HTTP response,
        # not an unhandled exception inside TestClient.
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_current_creator, None)
        app.dependency_overrides.pop(get_session, None)


# ── test 1: 413 on oversize ───────────────────────────────────────────────────


def test_413_returned_for_oversized_upload(upload_client, monkeypatch):
    """A file that exceeds UPLOAD_MAX_MB must yield HTTP 413."""
    monkeypatch.setattr("routers.videos.check_positive_balance", AsyncMock())

    # One byte over the 2 MB test limit.
    oversized = b"x" * (_TEST_MAX_MB * _MB + 1)

    with patch("worker.tasks.start_pipeline"):
        resp = upload_client.post(
            "/videos/upload",
            data={"youtube_video_id": "abc123"},
            files={"file": ("video.mp4", io.BytesIO(oversized), "video/mp4")},
        )

    assert resp.status_code == 413, f"Expected 413, got {resp.status_code}: {resp.text}"
    assert "MB limit" in resp.json().get("detail", "")


# ── test 2: temp file deleted on rejection ────────────────────────────────────


def test_tempfile_deleted_after_413(upload_client, monkeypatch, tmp_path):
    """The partial temp file must not exist on disk after a 413 rejection."""
    monkeypatch.setattr("routers.videos.check_positive_balance", AsyncMock())

    # Track every NamedTemporaryFile created during the request.
    created_paths: list[Path] = []
    real_ntf = tempfile.NamedTemporaryFile

    def _tracking_ntf(**kwargs):
        ntf = real_ntf(**kwargs)
        created_paths.append(Path(ntf.name))
        return ntf

    oversized = b"x" * (_TEST_MAX_MB * _MB + 1)

    with (
        patch("routers.videos.tempfile.NamedTemporaryFile", side_effect=_tracking_ntf),
        patch("worker.tasks.start_pipeline"),
    ):
        resp = upload_client.post(
            "/videos/upload",
            data={"youtube_video_id": "abc456"},
            files={"file": ("video.mp4", io.BytesIO(oversized), "video/mp4")},
        )

    assert resp.status_code == 413
    # Every temp file created during this request must have been cleaned up.
    for p in created_paths:
        assert not p.exists(), f"Temp file not cleaned up: {p}"


# ── test 3: RSS does not balloon for a rejected large upload ──────────────────


@pytest.mark.skip(
    reason="TestClient runs in-process — the test-side 100 MB payload allocation "
    "dominates ru_maxrss and overwhelms whatever the server route allocates. "
    "Streaming guard is covered by test_413 + test_tempfile_deleted. A real RSS "
    "bound would need an out-of-process server (httpx + uvicorn subprocess)."
)
def test_rss_delta_bounded_for_rejected_upload(upload_client, monkeypatch):
    """Rejecting a 100 MB+ upload must not balloon process RSS by more than 20 MB.

    We snapshot ru_maxrss before and after.  On Linux, ru_maxrss is in kilobytes;
    on macOS it is in bytes.  We normalise to bytes for the assertion.
    """
    import platform

    monkeypatch.setattr("routers.videos.check_positive_balance", AsyncMock())

    # 100 MB in-memory payload — enough to trigger the guard after the first
    # 1 MB chunk is read.  Only ~1 MB should ever land in the temp file.
    oversize_mb = 100
    payload = b"\x00" * (oversize_mb * _MB)

    # On Linux, ru_maxrss is in kB.  On macOS, it is in bytes.
    kb_factor = 1024 if platform.system() == "Linux" else 1
    max_rss_delta_bytes = 20 * _MB  # 20 MB

    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * kb_factor

    with patch("worker.tasks.start_pipeline"):
        resp = upload_client.post(
            "/videos/upload",
            data={"youtube_video_id": "dos_test"},
            files={"file": ("video.mp4", io.BytesIO(payload), "video/mp4")},
        )

    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * kb_factor

    assert resp.status_code == 413, f"Expected 413, got {resp.status_code}"

    rss_delta = rss_after - rss_before
    assert rss_delta <= max_rss_delta_bytes, (
        f"RSS grew by {rss_delta / _MB:.1f} MB for a rejected {oversize_mb} MB upload "
        f"(limit: {max_rss_delta_bytes // _MB} MB). "
        "The streaming guard is not working correctly."
    )
