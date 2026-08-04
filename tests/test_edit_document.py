"""Issue 391 — the server-side edit document: structural validation + endpoints.

The load-bearing property under test is the SPLIT between the two validators.
``validate_document_structure`` (save) must accept an edit that
``validate_user_cuts`` (render) rejects, because a work-in-progress edit is
routinely past the 5s-kept / 85%-removed caps and refusing to SAVE it would
destroy the creator's work — the exact defect this issue removes.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from clip_engine.edits import (
    MAX_CUTS,
    SUPPORTED_DOC_VERSION,
    CutValidationError,
    empty_document,
    validate_document_structure,
    validate_user_cuts,
)
from tests._helpers import override_current_creator, owned_result

CLIP_DURATION_S = 60.0


def _cut(cut_id, start, end):
    return {"id": cut_id, "start_s": start, "end_s": end}


# ── validate_document_structure ──────────────────────────────────────────────


def test_canonicalises_and_sorts_cuts():
    doc = {"version": 1, "cuts": [_cut("b", 30.0, 35.0), _cut("a", 10.0, 12.0)]}
    out = validate_document_structure(doc, CLIP_DURATION_S)
    assert [c["id"] for c in out["cuts"]] == ["a", "b"]
    assert out["version"] == SUPPORTED_DOC_VERSION
    assert out["last_applied_at"] is None


def test_empty_cut_list_is_valid_for_saving():
    """Unlike the render boundary, which raises ``empty``. Clearing every cut is
    a legitimate edit the creator must be able to persist."""
    out = validate_document_structure({"version": 1, "cuts": []}, CLIP_DURATION_S)
    assert out["cuts"] == []
    with pytest.raises(CutValidationError) as exc:
        validate_user_cuts([], clip_duration_s=CLIP_DURATION_S)
    assert exc.value.code == "empty"


def test_save_accepts_a_document_the_render_boundary_rejects():
    """THE test for the split. 90% removed saves; the same cuts fail at render."""
    doc = {"version": 1, "cuts": [_cut("a", 0.0, 54.0)]}

    out = validate_document_structure(doc, CLIP_DURATION_S)
    assert out["cuts"][0]["end_s"] == 54.0

    with pytest.raises(CutValidationError) as exc:
        validate_user_cuts([(0.0, 54.0)], clip_duration_s=CLIP_DURATION_S)
    assert exc.value.code == "removed_too_much"


def test_rejects_newer_schema_version():
    with pytest.raises(CutValidationError) as exc:
        validate_document_structure(
            {"version": SUPPORTED_DOC_VERSION + 1, "cuts": []}, CLIP_DURATION_S
        )
    assert exc.value.code == "unsupported_version"


def test_rejects_too_many_cuts():
    cuts = [_cut(f"c{i}", float(i) * 0.2, float(i) * 0.2 + 0.1) for i in range(MAX_CUTS + 1)]
    with pytest.raises(CutValidationError) as exc:
        validate_document_structure({"version": 1, "cuts": cuts}, CLIP_DURATION_S)
    assert exc.value.code == "too_many_cuts"


def test_rejects_duplicate_cut_ids():
    doc = {"version": 1, "cuts": [_cut("dup", 1.0, 2.0), _cut("dup", 5.0, 6.0)]}
    with pytest.raises(CutValidationError) as exc:
        validate_document_structure(doc, CLIP_DURATION_S)
    assert exc.value.code == "duplicate_cut_id"


@pytest.mark.parametrize(
    ("cuts", "code"),
    [
        ([_cut("a", 1.0, 5.0), _cut("b", 4.0, 8.0)], "overlap"),
        ([_cut("a", -1.0, 5.0)], "out_of_bounds"),
        ([_cut("a", 1.0, CLIP_DURATION_S + 10)], "out_of_bounds"),
        ([_cut("a", 5.0, 5.0)], "invalid_segment"),
        ([_cut("a", float("nan"), 5.0)], "invalid_segment"),
        ([_cut("a", float("inf"), 5.0)], "invalid_segment"),
        ([{"start_s": 1.0, "end_s": 2.0}], "invalid_segment"),
        ([_cut("a", "x", 2.0)], "invalid_segment"),
    ],
)
def test_structural_rejections(cuts, code):
    with pytest.raises(CutValidationError) as exc:
        validate_document_structure({"version": 1, "cuts": cuts}, CLIP_DURATION_S)
    assert exc.value.code == code


def test_rejects_non_object_document():
    with pytest.raises(CutValidationError):
        validate_document_structure([], CLIP_DURATION_S)


# ── Endpoints ────────────────────────────────────────────────────────────────


def _mock_creator():
    from models import Creator

    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    return c


def _mock_clip(creator_id, duration_s=CLIP_DURATION_S):
    from models import Clip

    clip = MagicMock(spec=Clip)
    clip.id = uuid.uuid4()
    clip.creator_id = creator_id
    clip.setup_start_s = 0.0
    clip.start_s = 0.0
    clip.end_s = duration_s
    return clip


def _mock_row(revision, doc):
    from datetime import UTC, datetime

    from models import ClipEditDocument

    row = MagicMock(spec=ClipEditDocument)
    row.revision = revision
    row.doc = doc
    row.updated_at = datetime(2026, 8, 4, tzinfo=UTC)
    return row


def _install(creator, clip, *, execute_after_owned=None, scalar_results=None):
    """Wire dependency overrides and return the captured statement list.

    ``get_owned`` consumes the first ``session.execute``; anything the handler
    issues after that comes from ``execute_after_owned``. Pass ``clip=None`` to
    simulate a missing/foreign clip (→ 404).
    """
    from auth import get_current_creator
    from db import get_session
    from main import app

    captured: list = []
    remaining = list(execute_after_owned or [])
    scalars = list(scalar_results or [])

    async def _execute(stmt, *args, **kwargs):
        captured.append(stmt)
        if len(captured) == 1:
            return owned_result(clip)
        return remaining.pop(0) if remaining else MagicMock()

    async def _scalar(*args, **kwargs):
        return scalars.pop(0) if scalars else None

    async def _session():
        s = AsyncMock()
        s.execute = AsyncMock(side_effect=_execute)
        s.scalar = AsyncMock(side_effect=_scalar)
        s.commit = AsyncMock()
        s.rollback = AsyncMock()
        yield s

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _session
    return captured


def _result_rows(*rows):
    """A mock execute-result whose ``.first()`` returns ``rows[0]`` or None."""
    res = MagicMock()
    res.first.return_value = rows[0] if rows else None
    return res


def test_get_synthesises_empty_document_at_revision_zero(client):
    """No row yet → revision 0 and an empty doc. A GET must not write."""
    from main import app

    creator = _mock_creator()
    clip = _mock_clip(creator.id)
    _install(creator, clip, scalar_results=[None])
    try:
        resp = client.get(f"/clips/{clip.id}/edit-document")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["revision"] == 0
    assert body["doc"] == empty_document()
    assert body["updated_at"] is None
    assert body["clip_duration_s"] == CLIP_DURATION_S


def test_get_returns_the_stored_document(client):
    from main import app

    creator = _mock_creator()
    clip = _mock_clip(creator.id)
    doc = {"version": 1, "cuts": [_cut("a", 1.0, 2.0)], "last_applied_at": None}
    _install(creator, clip, scalar_results=[_mock_row(7, doc)])
    try:
        resp = client.get(f"/clips/{clip.id}/edit-document")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    assert resp.json()["revision"] == 7
    assert resp.json()["doc"] == doc


def test_get_404s_for_another_creators_clip(client):
    from main import app

    creator = _mock_creator()
    clip = _mock_clip(uuid.uuid4())
    _install(creator, None)
    try:
        resp = client.get(f"/clips/{clip.id}/edit-document")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 404


def test_put_writes_and_returns_the_new_revision(client):
    from datetime import UTC, datetime

    from main import app

    creator = _mock_creator()
    clip = _mock_clip(creator.id)
    captured = _install(
        creator,
        clip,
        execute_after_owned=[_result_rows((3, datetime(2026, 8, 4, tzinfo=UTC)))],
    )
    try:
        resp = client.put(
            f"/clips/{clip.id}/edit-document",
            json={"base_revision": 2, "doc": {"version": 1, "cuts": [_cut("a", 1.0, 2.0)]}},
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    assert resp.json()["revision"] == 3
    assert resp.json()["doc"]["cuts"][0]["id"] == "a"

    # creator_id comes from the authenticated creator, NEVER the request body.
    upsert = captured[1]
    compiled = upsert.compile()
    assert compiled.params["creator_id"] == creator.id


def test_put_409s_on_a_stale_revision_and_carries_the_current_document(client):
    """Zero rows back from the compare-and-set means someone else advanced the
    revision. The 409 carries the current doc so the client can offer a choice
    without a second round trip — and never auto-merges."""
    from main import app

    creator = _mock_creator()
    clip = _mock_clip(creator.id)
    current = {"version": 1, "cuts": [_cut("theirs", 5.0, 6.0)], "last_applied_at": None}
    _install(
        creator,
        clip,
        execute_after_owned=[_result_rows()],  # .first() -> None
        scalar_results=[_mock_row(9, current)],
    )
    try:
        resp = client.put(
            f"/clips/{clip.id}/edit-document",
            json={"base_revision": 4, "doc": {"version": 1, "cuts": [_cut("mine", 1.0, 2.0)]}},
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "stale_revision"
    assert detail["revision"] == 9
    assert detail["doc"] == current


def test_put_409s_rather_than_resurrecting_a_vanished_document(client):
    """A stored row is always at revision >= 1, so a non-zero base_revision that
    lands on the INSERT branch (returned revision 1) means the row the client
    meant to update is gone. Creating a fresh one under a new lineage would be a
    silent divergence — 409 and let the creator choose."""
    from datetime import UTC, datetime

    from main import app

    creator = _mock_creator()
    clip = _mock_clip(creator.id)
    _install(
        creator,
        clip,
        execute_after_owned=[_result_rows((1, datetime(2026, 8, 4, tzinfo=UTC)))],
        scalar_results=[None],
    )
    try:
        resp = client.put(
            f"/clips/{clip.id}/edit-document",
            json={"base_revision": 5, "doc": {"version": 1, "cuts": []}},
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "stale_revision"


def test_put_422s_on_a_structurally_invalid_document(client):
    from main import app

    creator = _mock_creator()
    clip = _mock_clip(creator.id)
    _install(creator, clip)
    try:
        resp = client.put(
            f"/clips/{clip.id}/edit-document",
            json={
                "base_revision": 0,
                "doc": {"version": 1, "cuts": [_cut("a", 1.0, 5.0), _cut("b", 4.0, 8.0)]},
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "overlap"


def test_put_accepts_a_document_past_the_render_caps(client):
    """Saving is always allowed; exporting is gated. Without this test a future
    contributor will "fix" the split by applying the render caps at save."""
    from datetime import UTC, datetime

    from main import app

    creator = _mock_creator()
    clip = _mock_clip(creator.id)
    _install(
        creator,
        clip,
        execute_after_owned=[_result_rows((1, datetime(2026, 8, 4, tzinfo=UTC)))],
    )
    try:
        resp = client.put(
            f"/clips/{clip.id}/edit-document",
            json={
                "base_revision": 0,
                # 90% of the clip removed — far past MAX_REMOVED_PCT.
                "doc": {"version": 1, "cuts": [_cut("a", 0.0, 54.0)]},
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text


def test_put_rejects_a_negative_base_revision(client):
    from main import app

    creator = _mock_creator()
    clip = _mock_clip(creator.id)
    _install(creator, clip)
    try:
        resp = client.put(
            f"/clips/{clip.id}/edit-document",
            json={"base_revision": -1, "doc": {"version": 1, "cuts": []}},
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 422
