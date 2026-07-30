"""Tests for PATCH /clips/{clip_id} — applied publish metadata (W1 metadata lane).

Covers the tri-state contract (absent = untouched, null = clear, string = set),
the YouTube videos.insert limits (title ≤100 CHARS, description ≤5000 UTF-8
BYTES, no angle brackets → 422, never silent truncation), and ownership 404.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

from auth import get_current_creator
from db import get_session
from main import app
from models import Clip, Creator, RenderStatus
from tests._helpers import stub_get_owned


def _mock_creator() -> MagicMock:
    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    return c


def _mock_clip(
    creator_id: uuid.UUID,
    *,
    applied_title: str | None = None,
    applied_description: str | None = None,
) -> MagicMock:
    """Clip mock with every field _clip_response serializes set to a real value
    (ClipOut response validation rejects bare MagicMock attributes)."""
    clip = MagicMock(spec=Clip)
    clip.id = uuid.uuid4()
    clip.creator_id = creator_id
    clip.video_id = uuid.uuid4()
    clip.setup_start_s = 1.0
    clip.start_s = 1.0
    clip.end_s = 31.0
    clip.peak_s = 20.0
    clip.score = 0.5
    clip.rank = 1
    clip.signals_jsonb = {"principle": "P1", "reasoning": "r"}
    clip.render_status = RenderStatus.pending
    clip.render_uri = None
    clip.cleaned_render_uri = None
    clip.applied_title = applied_title
    clip.applied_description = applied_description
    clip.style_preset = None  # Issue 373: _clip_response derives `aspect` from it
    return clip


def _patch(client, clip, creator, json_body, *, found: bool = True):
    async def _session():
        session = AsyncMock()
        stub_get_owned(session, clip if found else None)
        yield session

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _session
    try:
        return client.patch(f"/clips/{clip.id}", json=json_body, cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()


def test_patch_sets_both_fields(client):
    creator = _mock_creator()
    clip = _mock_clip(creator.id)

    resp = _patch(client, clip, creator, {"applied_title": "My Title", "applied_description": "d"})

    assert resp.status_code == 200
    assert clip.applied_title == "My Title"
    assert clip.applied_description == "d"
    body = resp.json()
    assert body["applied_title"] == "My Title"
    assert body["applied_description"] == "d"


def test_patch_null_clears_only_the_sent_field(client):
    """Tri-state: explicit null clears applied_title; the omitted description
    key must leave the stored value untouched."""
    creator = _mock_creator()
    clip = _mock_clip(creator.id, applied_title="Old", applied_description="keep me")

    resp = _patch(client, clip, creator, {"applied_title": None})

    assert resp.status_code == 200
    assert clip.applied_title is None
    assert clip.applied_description == "keep me"


def test_patch_stripped_empty_title_normalizes_to_none(client):
    """'' / whitespace-only can never be stored — a falsy '' would silently
    trigger the publish fallback while looking 'set' in the UI."""
    creator = _mock_creator()
    clip = _mock_clip(creator.id, applied_title="Old")

    resp = _patch(client, clip, creator, {"applied_title": "   "})

    assert resp.status_code == 200
    assert clip.applied_title is None


def test_patch_title_over_100_chars_422(client):
    creator = _mock_creator()
    clip = _mock_clip(creator.id, applied_title="Old")

    resp = _patch(client, clip, creator, {"applied_title": "x" * 101})

    assert resp.status_code == 422
    assert clip.applied_title == "Old"  # no silent truncation, nothing stored


def test_patch_angle_brackets_rejected_422(client):
    creator = _mock_creator()
    clip = _mock_clip(creator.id)

    assert _patch(client, clip, creator, {"applied_title": "a <b> c"}).status_code == 422
    assert _patch(client, clip, creator, {"applied_description": "x > y"}).status_code == 422
    assert clip.applied_title is None
    assert clip.applied_description is None


def test_patch_description_limit_is_utf8_bytes_not_chars(client):
    """YouTube's 5000 description limit is UTF-8 BYTES: 2 600 'é' (2 bytes each)
    is only 2 600 characters but 5 200 bytes → 422. The same count of ASCII
    characters passes."""
    creator = _mock_creator()
    clip = _mock_clip(creator.id)

    assert _patch(client, clip, creator, {"applied_description": "é" * 2600}).status_code == 422
    assert _patch(client, clip, creator, {"applied_description": "e" * 2600}).status_code == 200
    assert clip.applied_description == "e" * 2600


def test_patch_foreign_clip_404(client):
    """Missing and foreign rows are indistinguishable: get_owned's scoped
    select returns no row → 404, and nothing is written."""
    creator = _mock_creator()
    clip = _mock_clip(uuid.uuid4())  # owned by someone else

    resp = _patch(client, clip, creator, {"applied_title": "hijack"}, found=False)

    assert resp.status_code == 404
    assert clip.applied_title is None
