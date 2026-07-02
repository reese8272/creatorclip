"""Issue 352 Batch C — link/upload double-submit race returns 409, not 500.

Two concurrent same-id submits both pass the dedupe SELECT; the loser violates
UNIQUE(creator_id, youtube_video_id) at commit(). Unit-lane simulation: the
session's commit raises IntegrityError (what Postgres surfaces to the loser)
and the endpoint must map it to the same clean 409 as the check-first path.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.exc import IntegrityError

from auth import get_current_creator
from db import get_session
from main import app
from tests._helpers import override_current_creator


def _race_session():
    """Session whose dedupe SELECT finds nothing but whose commit raises
    IntegrityError — the loser of a concurrent same-id double submit."""

    async def _gen():
        session = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result)
        session.add = MagicMock()
        session.commit = AsyncMock(side_effect=IntegrityError("INSERT", {}, Exception("dup")))
        session.rollback = AsyncMock()
        yield session

    return _gen


def test_link_video_double_submit_returns_409(client):
    creator = MagicMock()
    creator.id = uuid.uuid4()
    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _race_session()
    try:
        resp = client.post("/videos/link", data={"youtube_video_id": "abc12345678"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 409, f"expected 409, got {resp.status_code}: {resp.text}"
    assert resp.json()["detail"] == "Video already registered"
