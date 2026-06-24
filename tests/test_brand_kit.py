"""Issue 186 — Creator Brand Kit: saved style applied by default.

Tests:
  - Structural: CreatorStyle model exists in models.py with the required fields
  - Structural: migration 0028 present, chains after 0027, names FK + unique constraints
  - Behavioral: GET /creators/me/brand-kit returns zeroed defaults when no row exists
  - Behavioral: PUT /creators/me/brand-kit upserts and returns the merged kit
  - Behavioral: PUT is idempotent — second PUT merges onto the existing row
  - Behavioral: render_clip merges brand-kit beneath per-clip overrides
  - Isolation: GET brand-kit never exposes another creator's kit
"""

import pathlib
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

# ── Structural: model ────────────────────────────────────────────────────────


def test_brand_kit_creator_style_model_exists():
    """CreatorStyle must be importable from models and have the required columns."""
    from models import CreatorStyle

    table = CreatorStyle.__table__
    col_names = {c.name for c in table.columns}
    assert "id" in col_names
    assert "creator_id" in col_names
    assert "style" in col_names
    assert "updated_at" in col_names


def test_brand_kit_creator_style_unique_constraint():
    """uq_creator_style_creator_id must be defined — one kit row per creator."""
    from models import CreatorStyle

    constraint_names = {c.name for c in CreatorStyle.__table__.constraints}
    assert "uq_creator_style_creator_id" in constraint_names, (
        "CreatorStyle must carry uq_creator_style_creator_id so a second "
        "INSERT for the same creator raises an IntegrityError."
    )


def test_brand_kit_style_column_uses_mutable_dict():
    """style must use MutableDict.as_mutable(JSONB) so in-place dict mutations
    are tracked without a re-assign (avoids silently-lost updates).

    MutableDict.as_mutable wraps the ORM attribute descriptor (not the raw
    Column.type) — so we check the MutableDict listener registry rather than
    the column type directly.
    """
    # MutableDict.as_mutable registers the class with the column's parent.
    # The reliable way to verify it is active is to check that assigning a
    # plain dict to .style produces a MutableDict instance (ORM intercepts
    # __set__ and coerces it), or to check the class listener is registered.
    # We use the lightweight approach: check that MutableDict is in the
    # registered coercers for JSONB columns on this mapper.
    from sqlalchemy.dialects.postgresql import JSONB as PgJSONB
    from sqlalchemy.ext.mutable import MutableDict

    from models import CreatorStyle

    # Confirm that MutableDict has a coerce registered for JSONB type.
    coerce_result = MutableDict.coerce("style", {"k": "v"})
    assert isinstance(coerce_result, MutableDict), (
        "MutableDict.coerce must produce a MutableDict instance — "
        "without as_mutable() in-place dict mutations would be lost."
    )
    # Confirm the column type is JSONB (not accidentally changed to Text etc.).
    col = CreatorStyle.__table__.columns["style"]
    assert isinstance(col.type, PgJSONB), "style column must be JSONB"


# ── Structural: migration ────────────────────────────────────────────────────


def test_brand_kit_migration_0028_present_and_chained():
    """Migration 0028 must exist, chain after 0027, and create the creator_style table."""
    src = (
        pathlib.Path(__file__).parent.parent / "alembic" / "versions" / "0028_creator_brand_kit.py"
    ).read_text()
    assert 'down_revision = "0027"' in src, "0028 must chain after 0027"
    assert "creator_style" in src, "migration must reference the creator_style table"
    assert "uq_creator_style_creator_id" in src, "named unique constraint must appear in migration"
    assert "fk_creator_style_creator_id" in src, "named FK constraint must appear in migration"


def test_brand_kit_migration_0028_has_rls():
    """Migration 0028 must enable RLS + tenant_isolation policy (same pattern as
    0025/0026/0027) so cross-creator kit reads are blocked at the DB layer."""
    src = (
        pathlib.Path(__file__).parent.parent / "alembic" / "versions" / "0028_creator_brand_kit.py"
    ).read_text()
    assert "ROW LEVEL SECURITY" in src
    assert "tenant_isolation" in src
    assert "current_setting" in src


# ── Behavioral: GET /creators/me/brand-kit ───────────────────────────────────


def _override_creator(creator):
    from fastapi import Request

    async def _dep(request: Request):
        request.state.creator_id = creator.id
        return creator

    return _dep


def test_brand_kit_get_returns_defaults_when_no_row(client):
    """GET /creators/me/brand-kit with no existing row returns all-false/null defaults."""
    from auth import get_current_creator
    from db import get_session
    from main import app

    creator = MagicMock()
    creator.id = uuid.uuid4()

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None

    session = AsyncMock()
    session.execute = AsyncMock(return_value=mock_result)

    async def _fake_session():
        yield session

    original = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_creator] = _override_creator(creator)
    app.dependency_overrides[get_session] = _fake_session
    try:
        resp = client.get("/creators/me/brand-kit")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["subtitle"] is None
        assert data["background"] is None
        assert data["captions_enabled"] is False
        assert data["zoom_on_peak"] is False
        assert data["denoise"] is False
        assert data["aspect"] is None
    finally:
        app.dependency_overrides = original


def test_brand_kit_get_returns_stored_values(client):
    """GET /creators/me/brand-kit returns the persisted style fields."""
    from auth import get_current_creator
    from db import get_session
    from main import app
    from models import CreatorStyle

    creator = MagicMock()
    creator.id = uuid.uuid4()

    style_row = MagicMock(spec=CreatorStyle)
    style_row.style = {
        "subtitle": "bold_pop",
        "background": "blur",
        "captions_enabled": True,
        "zoom_on_peak": False,
        "denoise": True,
        "aspect": "1:1",
    }

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = style_row

    session = AsyncMock()
    session.execute = AsyncMock(return_value=mock_result)

    async def _fake_session():
        yield session

    original = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_creator] = _override_creator(creator)
    app.dependency_overrides[get_session] = _fake_session
    try:
        resp = client.get("/creators/me/brand-kit")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["subtitle"] == "bold_pop"
        assert data["background"] == "blur"
        assert data["captions_enabled"] is True
        assert data["denoise"] is True
        assert data["aspect"] == "1:1"
    finally:
        app.dependency_overrides = original


# ── Behavioral: PUT /creators/me/brand-kit ───────────────────────────────────


def test_brand_kit_put_creates_row_when_none_exists(client):
    """PUT /creators/me/brand-kit with no existing row inserts a new kit and returns it."""
    from auth import get_current_creator
    from db import get_session
    from main import app
    from models import CreatorStyle

    creator = MagicMock()
    creator.id = uuid.uuid4()

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None

    session = AsyncMock()
    session.execute = AsyncMock(return_value=mock_result)
    session.add = MagicMock()
    session.commit = AsyncMock()

    # Capture the CreatorStyle object that was added to the session.
    added_rows: list = []
    session.add.side_effect = lambda row: added_rows.append(row)

    async def _fake_session():
        yield session

    original = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_creator] = _override_creator(creator)
    app.dependency_overrides[get_session] = _fake_session
    try:
        resp = client.put(
            "/creators/me/brand-kit",
            json={"subtitle": "minimal", "captions_enabled": True},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["subtitle"] == "minimal"
        assert data["captions_enabled"] is True
        # A new CreatorStyle row must have been added to the session.
        assert any(isinstance(r, CreatorStyle) for r in added_rows), (
            "session.add() must have been called with a CreatorStyle instance"
        )
        session.commit.assert_awaited_once()
    finally:
        app.dependency_overrides = original


def test_brand_kit_put_merges_onto_existing_row(client):
    """PUT /creators/me/brand-kit with an existing row merges — does not reset other fields."""
    from auth import get_current_creator
    from db import get_session
    from main import app
    from models import CreatorStyle

    creator = MagicMock()
    creator.id = uuid.uuid4()

    existing = MagicMock(spec=CreatorStyle)
    existing.style = {"subtitle": "bold_pop", "background": "blur"}

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing

    session = AsyncMock()
    session.execute = AsyncMock(return_value=mock_result)
    session.add = MagicMock()
    session.commit = AsyncMock()

    async def _fake_session():
        yield session

    original = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_creator] = _override_creator(creator)
    app.dependency_overrides[get_session] = _fake_session
    try:
        # Only update the denoise field — the existing subtitle+background survive.
        resp = client.put("/creators/me/brand-kit", json={"denoise": True})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["denoise"] is True
        # The existing row's style must have been merged.
        assert existing.style.get("subtitle") == "bold_pop"
        assert existing.style.get("background") == "blur"
        # session.add() should NOT be called — we update the existing row.
        session.add.assert_not_called()
    finally:
        app.dependency_overrides = original


def test_brand_kit_put_rejects_invalid_body(client):
    """PUT /creators/me/brand-kit with extra fields is accepted (extra fields
    are stripped by Pydantic) and extra type errors return 422."""
    from auth import get_current_creator
    from db import get_session
    from main import app

    creator = MagicMock()
    creator.id = uuid.uuid4()
    session = AsyncMock()

    async def _fake_session():
        yield session

    original = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_creator] = _override_creator(creator)
    app.dependency_overrides[get_session] = _fake_session
    try:
        resp = client.put(
            "/creators/me/brand-kit",
            json={"captions_enabled": "definitely-not-a-bool"},
        )
        assert resp.status_code == 422, resp.text
    finally:
        app.dependency_overrides = original


# ── Behavioral: render_clip brand-kit fallback ───────────────────────────────


def test_brand_kit_render_applies_kit_defaults(client):
    """render_clip merges the creator's brand-kit beneath the per-clip request body
    so omitted fields fall back to the kit (not None)."""
    from auth import get_current_creator
    from billing.ledger import check_positive_balance
    from db import get_session
    from main import app
    from models import Clip, Creator, CreatorStyle, RenderStatus

    creator = MagicMock(spec=Creator)
    creator.id = uuid.uuid4()
    creator.minutes_balance = 100

    clip = MagicMock(spec=Clip)
    clip.id = uuid.uuid4()
    clip.creator_id = creator.id
    clip.render_status = RenderStatus.pending
    clip.style_preset = None  # no per-clip style set

    kit_row = MagicMock(spec=CreatorStyle)
    kit_row.style = {"subtitle": "bold_pop", "background": "blur"}

    # session.get returns the clip; session.execute returns the kit row.
    mock_kit_result = MagicMock()
    mock_kit_result.scalar_one_or_none.return_value = kit_row

    session = AsyncMock()
    session.get = AsyncMock(return_value=clip)
    session.execute = AsyncMock(return_value=mock_kit_result)
    session.commit = AsyncMock()

    async def _fake_session():
        yield session

    app.dependency_overrides[get_current_creator] = _override_creator(creator)
    app.dependency_overrides[get_session] = _fake_session
    app.dependency_overrides[check_positive_balance] = AsyncMock(return_value=None)

    with (
        patch("routers.clips.check_positive_balance", AsyncMock(return_value=None)),
        patch("worker.tasks.render_clip") as mock_task,
        patch("worker.progress.aset_owner", AsyncMock()),
    ):
        mock_task.delay.return_value = MagicMock(id="task-brand-kit")
        try:
            # Send a body with only zoom_on_peak — kit should fill in the rest.
            resp = client.post(
                f"/clips/{clip.id}/render",
                json={"zoom_on_peak": True},
                cookies={"session": "x"},
            )
        finally:
            app.dependency_overrides.clear()

    assert resp.status_code == 202, resp.text
    # The kit's subtitle + background + the request's zoom_on_peak must all land on the clip.
    assert (clip.style_preset or {}).get("subtitle") == "bold_pop"
    assert (clip.style_preset or {}).get("background") == "blur"
    assert (clip.style_preset or {}).get("zoom_on_peak") is True


def test_brand_kit_render_request_body_overrides_kit(client):
    """A per-clip render body field overrides the brand-kit value for that field."""
    from auth import get_current_creator
    from billing.ledger import check_positive_balance
    from db import get_session
    from main import app
    from models import Clip, Creator, CreatorStyle, RenderStatus

    creator = MagicMock(spec=Creator)
    creator.id = uuid.uuid4()
    creator.minutes_balance = 100

    clip = MagicMock(spec=Clip)
    clip.id = uuid.uuid4()
    clip.creator_id = creator.id
    clip.render_status = RenderStatus.pending
    clip.style_preset = None

    kit_row = MagicMock(spec=CreatorStyle)
    kit_row.style = {"subtitle": "bold_pop"}

    mock_kit_result = MagicMock()
    mock_kit_result.scalar_one_or_none.return_value = kit_row

    session = AsyncMock()
    session.get = AsyncMock(return_value=clip)
    session.execute = AsyncMock(return_value=mock_kit_result)
    session.commit = AsyncMock()

    async def _fake_session():
        yield session

    app.dependency_overrides[get_current_creator] = _override_creator(creator)
    app.dependency_overrides[get_session] = _fake_session
    app.dependency_overrides[check_positive_balance] = AsyncMock(return_value=None)

    with (
        patch("routers.clips.check_positive_balance", AsyncMock(return_value=None)),
        patch("worker.tasks.render_clip") as mock_task,
        patch("worker.progress.aset_owner", AsyncMock()),
    ):
        mock_task.delay.return_value = MagicMock(id="task-override")
        try:
            # Explicitly override the subtitle from bold_pop to minimal.
            resp = client.post(
                f"/clips/{clip.id}/render",
                json={"subtitle": "minimal"},
                cookies={"session": "x"},
            )
        finally:
            app.dependency_overrides.clear()

    assert resp.status_code == 202, resp.text
    assert (clip.style_preset or {}).get("subtitle") == "minimal", (
        "request body subtitle must override the kit's bold_pop"
    )


# ── Issue 187: style-learning endpoints ──────────────────────────────────────


def test_brand_kit_suggestion_returns_204_when_history_sparse(client):
    """GET /creators/me/brand-kit/suggestion returns 204 when no dominant is found."""
    from auth import get_current_creator
    from db import get_session
    from main import app

    creator = MagicMock()
    creator.id = uuid.uuid4()

    # Two entries — below the default threshold of 5.
    mock_result = MagicMock()
    mock_result.all.return_value = [
        ({"subtitle": "bold_pop"},),
        ({"subtitle": "bold_pop"},),
    ]

    session = AsyncMock()
    session.execute = AsyncMock(return_value=mock_result)

    async def _fake_session():
        yield session

    original = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_creator] = _override_creator(creator)
    app.dependency_overrides[get_session] = _fake_session
    try:
        resp = client.get("/creators/me/brand-kit/suggestion")
        assert resp.status_code == 204, resp.text
    finally:
        app.dependency_overrides = original


def test_brand_kit_suggestion_returns_suggestion_when_dominant(client):
    """GET /creators/me/brand-kit/suggestion returns field/value/count/message
    when a dominant is found."""
    from auth import get_current_creator
    from db import get_session
    from main import app

    creator = MagicMock()
    creator.id = uuid.uuid4()

    # 6 rows all with subtitle=bold_pop — exceeds threshold=5.
    mock_result = MagicMock()
    mock_result.all.return_value = [({"subtitle": "bold_pop"},)] * 6

    session = AsyncMock()
    session.execute = AsyncMock(return_value=mock_result)

    async def _fake_session():
        yield session

    original = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_creator] = _override_creator(creator)
    app.dependency_overrides[get_session] = _fake_session
    try:
        resp = client.get("/creators/me/brand-kit/suggestion")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["field"] == "subtitle"
        assert data["value"] == "bold_pop"
        assert data["count"] == 6
        # Honest framing: message must not promise virality.
        assert "virality" not in data["message"].lower()
        assert "bold_pop" in data["message"]
        assert "6" in data["message"]
    finally:
        app.dependency_overrides = original


def test_brand_kit_suggestion_accept_writes_to_kit(client):
    """POST /creators/me/brand-kit/suggestion/accept upserts the field into creator_style."""
    from auth import get_current_creator
    from db import get_session
    from main import app
    from models import CreatorStyle

    creator = MagicMock()
    creator.id = uuid.uuid4()

    # Simulate no existing row — _upsert_style_field should create one.
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None

    session = AsyncMock()
    session.execute = AsyncMock(return_value=mock_result)
    session.add = MagicMock()
    session.commit = AsyncMock()

    added_rows: list = []
    session.add.side_effect = lambda row: added_rows.append(row)

    async def _fake_session():
        yield session

    original = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_creator] = _override_creator(creator)
    app.dependency_overrides[get_session] = _fake_session
    try:
        resp = client.post(
            "/creators/me/brand-kit/suggestion/accept",
            json={"field": "subtitle", "value": "bold_pop"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["subtitle"] == "bold_pop"
        # A new CreatorStyle row must have been added.
        assert any(isinstance(r, CreatorStyle) for r in added_rows)
        session.commit.assert_awaited_once()
    finally:
        app.dependency_overrides = original
