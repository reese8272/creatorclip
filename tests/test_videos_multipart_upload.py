"""
Tests for Issue 395 — presigned direct-to-R2 multipart upload.

Load-bearing coverage (80/20):
  * per-creator isolation: foreign / malformed keys are refused with 403
  * create: declared-size 413, advisory 402/409, happy path key shape
  * complete: ordering (quota before CompleteMultipartUpload), oversize HEAD
    cleanup, stamp-before-start, NoSuchUpload idempotency, IntegrityError 409
    WITHOUT object deletion
  * abort idempotency
  * storage helpers: presign params, ListParts pagination, part sorting

No DB, no storage, no Celery — auth + session + storage are mocked so the suite
runs without Docker (unit lane). The r2 backend is monkeypatched on since the
test default is STORAGE_BACKEND=local.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from auth import get_current_creator
from db import get_session
from main import app

# ── helpers ───────────────────────────────────────────────────────────────────

_GB = 1024**3


def _fake_session():
    async def _gen():
        session = AsyncMock()
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


_CREATOR = _fake_creator()


def _own_key(token: str = "a" * 11, suffix: str = ".mp4") -> str:
    return f"source/{_CREATOR.id}/{token}{suffix}"


@pytest.fixture
def mp_client(monkeypatch):
    """TestClient with auth + session mocked and the r2 backend enabled."""
    monkeypatch.setattr("config.settings.STORAGE_BACKEND", "r2")
    monkeypatch.setattr("config.settings.R2_BUCKET", "test-bucket")

    app.dependency_overrides[get_current_creator] = lambda: _CREATOR
    app.dependency_overrides[get_session] = _fake_session()
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_current_creator, None)
        app.dependency_overrides.pop(get_session, None)


# ── isolation: key validation is the write-path boundary ─────────────────────


@pytest.mark.parametrize(
    "bad_key",
    [
        f"source/{uuid.uuid4()}/{'a' * 11}.mp4",  # another creator's namespace
        "source/../../etc/passwd",  # traversal
        "clips/own-something.mp4",  # wrong prefix
        "",  # empty
        None,  # placeholder replaced below with own key + bad suffix
    ],
)
def test_presign_refuses_foreign_or_malformed_keys(mp_client, bad_key):
    key = bad_key if bad_key is not None else _own_key(suffix=".exe")
    resp = mp_client.post(
        "/videos/uploads/upl-1/parts/1/presign",
        json={"key": key},
    )
    assert resp.status_code == 403, f"key={key!r} → {resp.status_code}: {resp.text}"


def test_complete_refuses_foreign_key(mp_client):
    resp = mp_client.post(
        "/videos/uploads/upl-1/complete",
        json={
            "key": f"source/{uuid.uuid4()}/{'b' * 11}.mp4",
            "parts": [{"part_number": 1, "etag": "e1"}],
        },
    )
    assert resp.status_code == 403


def test_list_and_abort_refuse_foreign_key(mp_client):
    foreign = f"source/{uuid.uuid4()}/{uuid.uuid4().hex}.mp4"
    assert mp_client.get("/videos/uploads/upl-1/parts", params={"key": foreign}).status_code == 403
    assert mp_client.post("/videos/uploads/upl-1/abort", json={"key": foreign}).status_code == 403


def test_uuid_hex_token_is_accepted(mp_client):
    """The standalone-upload key shape (uuid4 hex token) passes validation."""
    with patch("routers.videos.presign_upload_part", return_value="https://signed") as presign:
        resp = mp_client.post(
            "/videos/uploads/upl-1/parts/3/presign",
            json={"key": _own_key(token=uuid.uuid4().hex)},
        )
    assert resp.status_code == 200
    assert resp.json() == {"url": "https://signed", "expires_in": 900}
    assert presign.call_args.args[2] == 3  # part_number forwarded


# ── config + proxy mode ───────────────────────────────────────────────────────


def test_upload_config_multipart_mode(mp_client):
    resp = mp_client.get("/videos/uploads/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "multipart"
    assert body["part_size_bytes"] == 25 * 1024 * 1024
    assert body["max_file_bytes"] == 20 * _GB


def test_proxy_mode_blocks_multipart_endpoints(monkeypatch):
    """On the local backend (dev) create must 409 and config must say proxy."""
    app.dependency_overrides[get_current_creator] = lambda: _CREATOR
    app.dependency_overrides[get_session] = _fake_session()
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            assert c.get("/videos/uploads/config").json()["mode"] == "proxy"
            resp = c.post(
                "/videos/uploads",
                json={"filename": "a.mp4", "size_bytes": 123},
            )
            assert resp.status_code == 409
    finally:
        app.dependency_overrides.pop(get_current_creator, None)
        app.dependency_overrides.pop(get_session, None)


# ── create ────────────────────────────────────────────────────────────────────


def test_create_413_on_declared_oversize(mp_client, monkeypatch):
    monkeypatch.setattr("routers.videos.check_positive_balance", AsyncMock())
    resp = mp_client.post(
        "/videos/uploads",
        json={"filename": "big.mp4", "size_bytes": 21 * _GB},
    )
    assert resp.status_code == 413
    assert "GB limit" in resp.json()["detail"]


def test_create_422_on_bad_suffix(mp_client, monkeypatch):
    monkeypatch.setattr("routers.videos.check_positive_balance", AsyncMock())
    resp = mp_client.post(
        "/videos/uploads",
        json={"filename": "video.avi", "size_bytes": 100},
    )
    assert resp.status_code == 422
    assert "Unsupported file type" in resp.json()["detail"]


def test_create_402_propagates_balance_check(mp_client, monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(
        "routers.videos.check_positive_balance",
        AsyncMock(side_effect=HTTPException(status_code=402, detail="No minutes")),
    )
    resp = mp_client.post(
        "/videos/uploads",
        json={"filename": "video.mp4", "size_bytes": 100},
    )
    assert resp.status_code == 402


def test_create_409_on_duplicate_association(mp_client, monkeypatch):
    monkeypatch.setattr("routers.videos.check_positive_balance", AsyncMock())

    dup = MagicMock()
    dup.scalar_one_or_none.return_value = MagicMock()  # existing row

    async def _gen():
        session = AsyncMock()
        session.execute = AsyncMock(return_value=dup)
        session.commit = AsyncMock()
        yield session

    app.dependency_overrides[get_session] = _gen
    resp = mp_client.post(
        "/videos/uploads",
        json={"filename": "v.mp4", "size_bytes": 100, "youtube_video_id": "abc12345678"},
    )
    assert resp.status_code == 409


def test_create_happy_path_mints_key_and_upload_id(mp_client, monkeypatch):
    monkeypatch.setattr("routers.videos.check_positive_balance", AsyncMock())
    monkeypatch.setattr("routers.videos.check_balance_for_minutes", AsyncMock())
    with patch(
        "routers.videos.acreate_multipart_upload", AsyncMock(return_value="upl-777")
    ) as create:
        resp = mp_client.post(
            "/videos/uploads",
            json={
                "filename": "recording.MP4",
                "size_bytes": 3 * _GB,
                "content_type": "video/mp4",
                "youtube_video_id": "abc12345678",
                "duration_s": 1800.0,
            },
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["upload_id"] == "upl-777"
    assert body["key"] == f"source/{_CREATOR.id}/abc12345678.mp4"  # suffix lowercased
    assert body["part_size_bytes"] == 25 * 1024 * 1024
    assert create.call_args.args == (body["key"], "video/mp4")


# ── complete ──────────────────────────────────────────────────────────────────


def _complete_body(**overrides):
    body = {
        "key": _own_key(),
        "parts": [{"part_number": 2, "etag": "e2"}, {"part_number": 1, "etag": "e1"}],
        "duration_s": 30.0,
    }
    body.update(overrides)
    return body


def _refresh_with_id():
    async def _refresh(obj):
        obj.id = uuid.uuid4()

    return AsyncMock(side_effect=_refresh)


def test_complete_happy_path(mp_client, monkeypatch):
    """Row registered with duration_s=None + provisional kind; stamp runs before
    start_pipeline; response mirrors the legacy upload contract."""
    monkeypatch.setattr("routers.videos.check_positive_balance", AsyncMock())
    monkeypatch.setattr("routers.videos.check_balance_for_minutes", AsyncMock())

    added = []
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result)
    session.add = MagicMock(side_effect=added.append)
    session.commit = AsyncMock()
    session.refresh = _refresh_with_id()

    async def _gen():
        yield session

    app.dependency_overrides[get_session] = _gen

    order = []

    async def _stamp(*a, **k):
        order.append("stamp")
        return "/tasks/x/events"

    with (
        patch("routers.videos.acomplete_multipart_upload", AsyncMock()) as complete,
        patch("routers.videos.ahead_object", AsyncMock(return_value={"size": 123, "etag": "e"})),
        patch("routers.videos.adelete_file", AsyncMock()) as delete,
        patch("routers.videos.stamp_stream_owner", AsyncMock(side_effect=_stamp)),
        patch(
            "routers.videos.start_pipeline", MagicMock(side_effect=lambda _: order.append("start"))
        ),
    ):
        resp = mp_client.post("/videos/uploads/upl-1/complete", json=_complete_body())

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["stream_url"] == "/tasks/x/events"
    # Parts forwarded as dicts (storage helper sorts them).
    sent_parts = complete.call_args.args[2]
    assert {p["part_number"] for p in sent_parts} == {1, 2}
    # Row shape: authoritative duration/kind deferred to the worker probe.
    video = added[0]
    assert video.duration_s is None
    assert video.kind.value == "short"  # provisional, from the 30 s advisory duration
    assert video.source_uri == f"s3://test-bucket/{_own_key()}"
    assert order == ["stamp", "start"], "stamp_stream_owner must run before start_pipeline"
    delete.assert_not_called()


def test_complete_402_runs_before_storage_complete(mp_client, monkeypatch):
    """A drained balance must NOT consume the multipart session — after top-up
    the client re-calls complete without re-uploading."""
    from fastapi import HTTPException

    monkeypatch.setattr(
        "routers.videos.check_positive_balance",
        AsyncMock(side_effect=HTTPException(status_code=402, detail="No minutes")),
    )
    with patch("routers.videos.acomplete_multipart_upload", AsyncMock()) as complete:
        resp = mp_client.post("/videos/uploads/upl-1/complete", json=_complete_body())
    assert resp.status_code == 402
    complete.assert_not_called()


def test_complete_oversize_head_deletes_and_413(mp_client, monkeypatch):
    monkeypatch.setattr("routers.videos.check_positive_balance", AsyncMock())
    monkeypatch.setattr("routers.videos.check_balance_for_minutes", AsyncMock())
    with (
        patch("routers.videos.acomplete_multipart_upload", AsyncMock()),
        patch(
            "routers.videos.ahead_object",
            AsyncMock(return_value={"size": 21 * _GB, "etag": "e"}),
        ),
        patch("routers.videos.adelete_file", AsyncMock()) as delete,
        patch("routers.videos.start_pipeline", MagicMock()) as start,
    ):
        resp = mp_client.post("/videos/uploads/upl-1/complete", json=_complete_body())
    assert resp.status_code == 413
    delete.assert_awaited_once()
    start.assert_not_called()


def test_complete_no_such_upload_is_idempotent_when_row_exists(mp_client, monkeypatch):
    """A lost response + client retry: NoSuchUpload but the row for this exact
    object exists — re-return it instead of 404."""
    from worker.storage import MultipartUploadNotFound

    monkeypatch.setattr("routers.videos.check_positive_balance", AsyncMock())
    monkeypatch.setattr("routers.videos.check_balance_for_minutes", AsyncMock())

    existing_video = MagicMock()
    existing_video.id = uuid.uuid4()
    existing_video.ingest_status.value = "running"

    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing_video
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()

    async def _gen():
        yield session

    app.dependency_overrides[get_session] = _gen

    with patch(
        "routers.videos.acomplete_multipart_upload",
        AsyncMock(side_effect=MultipartUploadNotFound()),
    ):
        resp = mp_client.post("/videos/uploads/upl-1/complete", json=_complete_body())
    assert resp.status_code == 200
    assert resp.json()["video_id"] == str(existing_video.id)
    assert resp.json()["status"] == "running"


def test_complete_no_such_upload_404_when_no_row(mp_client, monkeypatch):
    from worker.storage import MultipartUploadNotFound

    monkeypatch.setattr("routers.videos.check_positive_balance", AsyncMock())
    monkeypatch.setattr("routers.videos.check_balance_for_minutes", AsyncMock())
    with patch(
        "routers.videos.acomplete_multipart_upload",
        AsyncMock(side_effect=MultipartUploadNotFound()),
    ):
        resp = mp_client.post("/videos/uploads/upl-1/complete", json=_complete_body())
    assert resp.status_code == 404


def test_complete_integrity_race_409_without_object_delete(mp_client, monkeypatch):
    """The losing insert must NOT delete the object — an associated upload's key
    is deterministic, so the winning row may point at this exact object."""
    from sqlalchemy.exc import IntegrityError

    monkeypatch.setattr("routers.videos.check_positive_balance", AsyncMock())
    monkeypatch.setattr("routers.videos.check_balance_for_minutes", AsyncMock())

    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result)
    session.add = MagicMock()
    # First commit releases the connection; the insert commit raises.
    session.commit = AsyncMock(side_effect=[None, IntegrityError("dup", None, Exception())])
    session.rollback = AsyncMock()

    async def _gen():
        yield session

    app.dependency_overrides[get_session] = _gen

    with (
        patch("routers.videos.acomplete_multipart_upload", AsyncMock()),
        patch("routers.videos.ahead_object", AsyncMock(return_value={"size": 5, "etag": "e"})),
        patch("routers.videos.adelete_file", AsyncMock()) as delete,
    ):
        resp = mp_client.post("/videos/uploads/upl-1/complete", json=_complete_body())
    assert resp.status_code == 409
    delete.assert_not_called()


# ── abort ─────────────────────────────────────────────────────────────────────


def test_abort_is_idempotent_on_missing_upload(mp_client):
    from worker.storage import MultipartUploadNotFound

    with patch(
        "routers.videos.aabort_multipart_upload",
        AsyncMock(side_effect=MultipartUploadNotFound()),
    ):
        resp = mp_client.post("/videos/uploads/upl-1/abort", json={"key": _own_key()})
    assert resp.status_code == 200
    assert resp.json() == {"aborted": True}


# ── list parts ────────────────────────────────────────────────────────────────


def test_list_parts_endpoint_maps_404(mp_client):
    from worker.storage import MultipartUploadNotFound

    with patch(
        "routers.videos.alist_upload_parts",
        AsyncMock(side_effect=MultipartUploadNotFound()),
    ):
        resp = mp_client.get("/videos/uploads/upl-1/parts", params={"key": _own_key()})
    assert resp.status_code == 404


# ── storage helpers (worker/storage.py) ──────────────────────────────────────


def test_presign_upload_part_signs_expected_params(monkeypatch):
    monkeypatch.setattr("config.settings.STORAGE_BACKEND", "r2")
    monkeypatch.setattr("config.settings.R2_BUCKET", "test-bucket")
    fake = MagicMock()
    fake.generate_presigned_url.return_value = "https://signed-part"
    with patch("worker.storage._r2", return_value=fake):
        from worker.storage import presign_upload_part

        url = presign_upload_part("source/c/k.mp4", "upl-9", 7, expires_s=900)
    assert url == "https://signed-part"
    call = fake.generate_presigned_url.call_args
    assert call.args[0] == "upload_part"
    assert call.kwargs["Params"] == {
        "Bucket": "test-bucket",
        "Key": "source/c/k.mp4",
        "UploadId": "upl-9",
        "PartNumber": 7,
    }
    assert call.kwargs["ExpiresIn"] == 900


def test_list_upload_parts_paginates(monkeypatch):
    monkeypatch.setattr("config.settings.STORAGE_BACKEND", "r2")
    monkeypatch.setattr("config.settings.R2_BUCKET", "test-bucket")
    fake = MagicMock()
    fake.list_parts.side_effect = [
        {
            "Parts": [{"PartNumber": 1, "Size": 10, "ETag": "e1"}],
            "IsTruncated": True,
            "NextPartNumberMarker": 1,
        },
        {
            "Parts": [{"PartNumber": 2, "Size": 20, "ETag": "e2"}],
            "IsTruncated": False,
        },
    ]
    with patch("worker.storage._r2", return_value=fake):
        from worker.storage import list_upload_parts

        parts = list_upload_parts("source/c/k.mp4", "upl-9")
    assert parts == [
        {"part_number": 1, "size": 10, "etag": "e1"},
        {"part_number": 2, "size": 20, "etag": "e2"},
    ]
    assert fake.list_parts.call_count == 2
    assert fake.list_parts.call_args_list[1].kwargs["PartNumberMarker"] == 1


def test_complete_multipart_upload_sorts_parts(monkeypatch):
    monkeypatch.setattr("config.settings.STORAGE_BACKEND", "r2")
    monkeypatch.setattr("config.settings.R2_BUCKET", "test-bucket")
    fake = MagicMock()
    with patch("worker.storage._r2", return_value=fake):
        from worker.storage import complete_multipart_upload

        uri = complete_multipart_upload(
            "source/c/k.mp4",
            "upl-9",
            [{"part_number": 2, "etag": "e2"}, {"part_number": 1, "etag": "e1"}],
        )
    assert uri == "s3://test-bucket/source/c/k.mp4"
    manifest = fake.complete_multipart_upload.call_args.kwargs["MultipartUpload"]
    assert [p["PartNumber"] for p in manifest["Parts"]] == [1, 2]


def test_multipart_helpers_refuse_local_backend():
    """Programming-error backstop: the router gates on mode, but the helpers
    must never silently no-op on the local backend."""
    from worker.storage import create_multipart_upload

    with pytest.raises(RuntimeError):
        create_multipart_upload("source/c/k.mp4")
