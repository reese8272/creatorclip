"""Tests for POST /clips/{clip_id}/clean/discard (Issue 364).

Covers:
  - discarding clears cleaned_render_uri and returns status=discarded
  - idempotent: second call with nothing pending returns status=noop
  - per-creator isolation -> 404
  - storage purge failure is tolerated -- 200 + column still cleared
  - regression: after discard, a subsequent /clean no longer 409s
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from tests._helpers import stub_get_owned


def _mock_creator():
    from models import Creator

    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    c.minutes_balance = 100
    return c


def _mock_clip(creator_id, cleaned_render_uri="clips/x_clean.mp4"):
    from models import Clip, RenderStatus

    clip = MagicMock(spec=Clip)
    clip.id = uuid.uuid4()
    clip.creator_id = creator_id
    clip.render_status = RenderStatus.done
    clip.render_uri = "clips/x.mp4"
    clip.cleaned_render_uri = cleaned_render_uri
    return clip


def _post_discard(client, clip, creator):
    from auth import get_current_creator
    from db import get_session
    from main import app

    async def _session():
        s = AsyncMock()
        stub_get_owned(s, clip)
        s.commit = AsyncMock()
        yield s

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session
    try:
        resp = client.post(f"/clips/{clip.id}/clean/discard")
    finally:
        app.dependency_overrides.clear()
    return resp


def test_clean_discard_clears_cleaned_render_uri(client):
    creator = _mock_creator()
    clip = _mock_clip(creator.id)

    with patch("worker.storage.adelete_file", AsyncMock(return_value=None)) as mock_delete:
        resp = _post_discard(client, clip, creator)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "discarded"
    assert body["clip_id"] == str(clip.id)
    assert clip.cleaned_render_uri is None
    mock_delete.assert_awaited_once_with("clips/x_clean.mp4")


def test_clean_discard_idempotent_when_no_cleaned_uri(client):
    creator = _mock_creator()
    clip = _mock_clip(creator.id, cleaned_render_uri=None)

    with patch("worker.storage.adelete_file", AsyncMock(return_value=None)) as mock_delete:
        resp = _post_discard(client, clip, creator)

    assert resp.status_code == 200
    assert resp.json()["status"] == "noop"
    mock_delete.assert_not_awaited()


def test_clean_discard_rejects_other_creators_clip(client):
    """Per-creator isolation: foreign clip -> ownership-scoped select finds
    no row -> 404 (not 403, to avoid leaking clip existence)."""
    creator = _mock_creator()
    other = _mock_creator()
    clip = _mock_clip(other.id)

    from auth import get_current_creator
    from db import get_session
    from main import app

    async def _session():
        s = AsyncMock()
        stub_get_owned(s, None)
        yield s

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session
    try:
        resp = client.post(f"/clips/{clip.id}/clean/discard")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 404


def test_clean_discard_tolerates_storage_purge_failure(client):
    """A storage failure must not roll back the DB change or fail the
    request -- mirrors the erasure precedent in routers/auth.py."""
    creator = _mock_creator()
    clip = _mock_clip(creator.id)

    with patch("worker.storage.adelete_file", AsyncMock(side_effect=RuntimeError("r2 down"))):
        resp = _post_discard(client, clip, creator)

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "discarded"
    assert clip.cleaned_render_uri is None


def test_clean_discard_unblocks_subsequent_clean(client):
    """Regression test: after discard, a follow-up /clean call no longer
    409s with pending_clean_or_edit (the bug this issue fixes)."""
    from auth import get_current_creator
    from billing.ledger import check_positive_balance
    from db import get_session
    from main import app

    creator = _mock_creator()
    clip = _mock_clip(creator.id)

    with patch("worker.storage.adelete_file", AsyncMock(return_value=None)):
        discard_resp = _post_discard(client, clip, creator)
    assert discard_resp.status_code == 200
    assert clip.cleaned_render_uri is None

    async def _session():
        s = AsyncMock()
        stub_get_owned(s, clip)
        yield s

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[check_positive_balance] = AsyncMock(return_value=None)
    try:
        with (
            patch("routers.clips.check_positive_balance", AsyncMock(return_value=None)),
            patch(
                "routers.clips.enqueue_stream_task",
                AsyncMock(return_value=(MagicMock(id="t-1"), "/tasks/t-1/events")),
            ),
        ):
            resp = client.post(f"/clips/{clip.id}/clean")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code != 409, resp.text
    assert resp.status_code == 202, resp.text
