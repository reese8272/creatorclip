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
            data={"youtube_video_id": "abc12345678"},
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
            data={"youtube_video_id": "abc45678901"},
            files={"file": ("video.mp4", io.BytesIO(oversized), "video/mp4")},
        )

    assert resp.status_code == 413
    # Every temp file created during this request must have been cleaned up.
    for p in created_paths:
        assert not p.exists(), f"Temp file not cleaned up: {p}"


# ── test 3: RSS does not balloon for a rejected large upload ──────────────────


# ── Issue 55: just-over-max upload rejects 413 and never touches storage ─────


def test_upload_just_over_max_rejects_413_and_writes_nothing(monkeypatch):
    """A file one byte over UPLOAD_MAX_MB must yield 413 and must not call storage."""
    monkeypatch.setattr("config.settings.UPLOAD_MAX_MB", _TEST_MAX_MB)

    creator = _fake_creator()
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session()

    storage_mock = MagicMock()

    try:
        with (
            patch("routers.videos.check_positive_balance", new_callable=AsyncMock),
            patch("routers.videos.upload_file", storage_mock),
            patch("worker.tasks.start_pipeline"),
            TestClient(app, raise_server_exceptions=False) as c,
        ):
            oversized = b"x" * (_TEST_MAX_MB * _MB + 1)
            resp = c.post(
                "/videos/upload",
                data={"youtube_video_id": "issue55test"},
                files={"file": ("video.mp4", io.BytesIO(oversized), "video/mp4")},
            )
    finally:
        app.dependency_overrides.pop(get_current_creator, None)
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 413, (
        f"Expected 413 for just-over-max upload, got {resp.status_code}: {resp.text}"
    )
    storage_mock.assert_not_called()


# ── Issue 89: duration-aware balance pre-check fires after probe ─────────────


def test_upload_402s_after_probe_when_balance_under_video_minutes(monkeypatch):
    """1-minute balance, 60-minute video → 402 BEFORE R2 PUT, tmp cleaned, no Video row.

    SEV-1 regression: before the fix, `check_positive_balance` only checked
    balance>0; the upload completed, then `_ingest_async`'s deduct silently
    402'd inside Celery, leaving the user with a "failed" video and no
    actionable message.
    """
    from fastapi import HTTPException

    monkeypatch.setattr("config.settings.UPLOAD_MAX_MB", _TEST_MAX_MB)

    creator = _fake_creator()
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session()

    storage_mock = MagicMock()
    start_pipeline_mock = MagicMock()

    # Track every NamedTemporaryFile created — must be cleaned even on 402.
    created_paths: list[Path] = []
    real_ntf = tempfile.NamedTemporaryFile

    def _tracking_ntf(**kwargs):
        ntf = real_ntf(**kwargs)
        created_paths.append(Path(ntf.name))
        return ntf

    # Force the duration probe to return a known 60 minutes so the deduction-
    # predicate check has well-defined inputs. probe_duration_s is sync and
    # called inside asyncio.to_thread — patching the import at the router
    # leaves asyncio.to_thread doing its real thing.
    def _fake_probe(_tmp):
        return 3600.0

    # check_positive_balance passes (the fast gate); check_balance_for_minutes
    # raises 402 (the SEV-1 fix). 402 detail must reference both numbers.
    async def _fake_balance_check(creator_id, minutes_needed, _session):
        assert minutes_needed == 60, (
            f"upload must call check_balance_for_minutes with video_minutes(3600s)=60; "
            f"got {minutes_needed}"
        )
        raise HTTPException(
            status_code=402,
            detail=f"This video needs {minutes_needed} minutes; you have 1.",
        )

    try:
        with (
            patch("routers.videos.check_positive_balance", new_callable=AsyncMock),
            patch("routers.videos.check_balance_for_minutes", side_effect=_fake_balance_check),
            patch("routers.videos.probe_duration_s", _fake_probe),
            patch("routers.videos.upload_file", storage_mock),
            patch("routers.videos.tempfile.NamedTemporaryFile", side_effect=_tracking_ntf),
            patch("routers.videos.start_pipeline", start_pipeline_mock),
            TestClient(app, raise_server_exceptions=False) as c,
        ):
            tiny_payload = b"\x00" * 1024  # 1 KB — under any size limit
            resp = c.post(
                "/videos/upload",
                data={"youtube_video_id": "issue89test"},
                files={"file": ("video.mp4", io.BytesIO(tiny_payload), "video/mp4")},
            )
    finally:
        app.dependency_overrides.pop(get_current_creator, None)
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 402, f"Expected 402, got {resp.status_code}: {resp.text}"
    assert "60" in resp.json()["detail"]
    assert "1" in resp.json()["detail"]
    storage_mock.assert_not_called()
    start_pipeline_mock.assert_not_called()
    # Every temp file must be cleaned even on the 402 short-circuit.
    for p in created_paths:
        assert not p.exists(), f"Temp file not cleaned up on 402: {p}"


# ── Wave-4 Fix 1: upload aset_owner fail-open on Redis-down ─────────────────


def test_upload_returns_stream_url_none_when_aset_owner_redis_down(monkeypatch):
    """Wave-4 Fix 1 (SEV2): a Redis blip during aset_owner MUST NOT 500 the
    upload or prevent the ingest pipeline from starting. The Video row is
    already committed and the chain is enqueued; only the SSE link is lost.

    Same fail-open posture as Wave-3 Fix B (improvement brief router) and
    Wave-3 Fix D (OAuth callback). This test pins the third + final site
    where the invariant must hold.
    """
    import redis

    monkeypatch.setattr("config.settings.UPLOAD_MAX_MB", _TEST_MAX_MB)

    creator = _fake_creator()
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session()

    storage_mock = MagicMock(return_value="local://x")
    start_pipeline_mock = MagicMock()
    aset_owner_mock = AsyncMock(side_effect=redis.ConnectionError("Redis down"))

    async def _fake_balance_check(*args, **kwargs):
        return None

    def _fake_probe(_tmp):
        return 30.0

    try:
        with (
            patch("routers.videos.check_positive_balance", new_callable=AsyncMock),
            patch("routers.videos.check_balance_for_minutes", side_effect=_fake_balance_check),
            patch("routers.videos.probe_duration_s", _fake_probe),
            patch("routers.videos.upload_file", storage_mock),
            patch("routers.videos.start_pipeline", start_pipeline_mock),
            patch("worker.progress.aset_owner", aset_owner_mock),
            TestClient(app, raise_server_exceptions=False) as c,
        ):
            tiny_payload = b"\x00" * 1024
            resp = c.post(
                "/videos/upload",
                data={"youtube_video_id": "issue1up402"},
                files={"file": ("video.mp4", io.BytesIO(tiny_payload), "video/mp4")},
            )
    finally:
        app.dependency_overrides.pop(get_current_creator, None)
        app.dependency_overrides.pop(get_session, None)

    # The upload must succeed even though Redis is down on aset_owner.
    assert resp.status_code == 200, (
        f"Wave-4 Fix 1: Redis-down on aset_owner MUST NOT fail the upload. "
        f"Got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    # The Video was created (we got a real video_id).
    assert body["video_id"]
    # stream_url is None — the client falls back to /videos/{id}/status polling.
    assert body["stream_url"] is None, (
        "Wave-4 Fix 1: when aset_owner fails, stream_url MUST be None so the "
        "client knows to poll instead of subscribing. Mirrors the Wave-3 "
        "Fix B contract in routers/improvement.py."
    )
    # The actual work still runs — start_pipeline was called.
    start_pipeline_mock.assert_called_once_with(body["video_id"])
    # aset_owner was attempted (and raised).
    aset_owner_mock.assert_awaited_once_with(body["video_id"], str(creator.id))


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
            data={"youtube_video_id": "dos12345678"},
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
