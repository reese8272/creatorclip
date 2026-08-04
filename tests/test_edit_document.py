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


# ── The render path reads the document (Issue 391, PR B) ─────────────────────
#
# POST /clips/{id}/cuts is PAID, flag-gated and budget-checked, and it feeds a
# Celery task whose idempotency probe silently drops an edit if a cleaned
# artifact already exists. These tests pin the parts that must not move.


def _render_clip(creator_id, duration_s=CLIP_DURATION_S):
    from models import Clip, RenderStatus

    clip = MagicMock(spec=Clip)
    clip.id = uuid.uuid4()
    clip.creator_id = creator_id
    clip.video_id = uuid.uuid4()
    clip.setup_start_s = 0.0
    clip.start_s = 0.0
    clip.end_s = duration_s
    clip.render_status = RenderStatus.done
    clip.render_uri = "clips/x.mp4"
    clip.cleaned_render_uri = None
    return clip


def _post_cuts(creator, clip, body, *, scalar_results=None):
    """POST /cuts with the Celery enqueue and balance check stubbed out."""
    from unittest.mock import patch

    from main import app

    _install(creator, clip, scalar_results=scalar_results)
    with (
        patch("routers.clips.check_positive_balance", AsyncMock(return_value=None)),
        patch("worker.tasks.edit_clip") as mock_task,
        patch("worker.progress.aset_owner", AsyncMock()),
    ):
        mock_task.delay.return_value = MagicMock(id="task-edit-1")
        try:
            resp = client_ref["client"].post(f"/clips/{clip.id}/cuts", json=body)
        finally:
            app.dependency_overrides.clear()
    return resp, mock_task


client_ref: dict = {}


@pytest.fixture(autouse=True)
def _capture_client(client):
    client_ref["client"] = client
    yield


def test_render_uses_the_document_not_the_posted_segments(client):
    """The whole point of PR B: what gets rendered is what the server holds."""
    creator = _mock_creator()
    clip = _render_clip(creator.id)
    doc = {"version": 1, "cuts": [_cut("a", 2.0, 4.0)], "last_applied_at": None}

    resp, mock_task = _post_cuts(
        creator, clip, {"base_revision": 3}, scalar_results=[_mock_row(3, doc)]
    )

    assert resp.status_code == 202, resp.text
    # The Celery payload is built from the DOCUMENT.
    args = mock_task.delay.call_args[0]
    assert args[1] == [[2.0, 4.0]]


def test_render_409s_when_the_document_moved_on(client):
    """A creator who kept editing in another tab after pressing Export must not
    spend a paid render slot on the older list."""
    creator = _mock_creator()
    clip = _render_clip(creator.id)
    doc = {"version": 1, "cuts": [_cut("newer", 1.0, 2.0)], "last_applied_at": None}

    resp, mock_task = _post_cuts(
        creator, clip, {"base_revision": 3}, scalar_results=[_mock_row(9, doc)]
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "stale_revision"
    assert resp.json()["detail"]["revision"] == 9
    mock_task.delay.assert_not_called()


def test_render_still_applies_the_full_caps_to_the_document(client):
    """The render boundary keeps BOTH caps even though save does not."""
    creator = _mock_creator()
    clip = _render_clip(creator.id)
    doc = {"version": 1, "cuts": [_cut("a", 0.0, 54.0)], "last_applied_at": None}

    resp, mock_task = _post_cuts(
        creator, clip, {"base_revision": 1}, scalar_results=[_mock_row(1, doc)]
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "removed_too_much"
    mock_task.delay.assert_not_called()


def test_render_keeps_the_pending_clean_or_edit_409(client):
    """SEV1 regression pin. Losing this guard lets the worker's idempotency probe
    silently drop the creator's edit."""
    creator = _mock_creator()
    clip = _render_clip(creator.id)
    clip.cleaned_render_uri = "clips/x_edit.mp4"

    resp, mock_task = _post_cuts(creator, clip, {"base_revision": 0})

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "pending_clean_or_edit"
    mock_task.delay.assert_not_called()


def test_render_post_keeps_its_full_dependency_list():
    """SEV1 regression pin, read off the SOURCE rather than FastAPI internals.

    The document endpoints deliberately carry none of these — that asymmetry is
    the design ("saving is always allowed; exporting is gated"), so it is exactly
    the kind of thing a later refactor could "harmonise" away. Pin both halves.
    """
    import ast
    import copy
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[1] / "routers" / "clips.py"
    tree = ast.parse(src.read_text())

    def _code(node):
        """Decorators + body, with the DOCSTRING stripped.

        Without this the assertions match the prose that explains why a guard is
        absent — `put_edit_document`'s docstring names `pending_clean_or_edit`
        precisely to say it does not have one.
        """
        stripped = copy.deepcopy(node)
        if (
            stripped.body
            and isinstance(stripped.body[0], ast.Expr)
            and isinstance(stripped.body[0].value, ast.Constant)
            and isinstance(stripped.body[0].value.value, str)
        ):
            stripped.body = stripped.body[1:]
        return ast.unparse(stripped)

    handlers = {
        node.name: _code(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
    }

    # ast.unparse normalises string quoting, so match on the bare tokens.
    submit = handlers["submit_cuts"]
    for needed in (
        "require_flag",
        "render_intake",
        "require_budget",
        "check_positive_balance",
        "20/hour",
        "RENDER_DAILY_LIMIT",
        "pending_clean_or_edit",
    ):
        assert needed in submit, f"submit_cuts lost {needed!r} — it is a PAID, gated route"

    for name in ("get_edit_document", "put_edit_document"):
        doc_handler = handlers[name]
        assert "require_flag" not in doc_handler, f"{name} must not be kill-switched"
        assert "require_budget" not in doc_handler, f"{name} must not be budget-gated"
        assert "check_positive_balance" not in doc_handler, (
            f"{name} must not check the balance — a creator at zero must still "
            "be able to see and save their work"
        )
    assert "pending_clean_or_edit" not in handlers["put_edit_document"], (
        "the pending-render 409 is a render-queue invariant, not an editing one; "
        "blocking saves while a render is pending is the failure Issue 391 removes"
    )


def test_render_rejects_an_empty_body(client):
    """Neither base_revision nor segments — a 422, not a 500."""
    creator = _mock_creator()
    clip = _render_clip(creator.id)
    resp, mock_task = _post_cuts(creator, clip, {})
    assert resp.status_code == 422, resp.text
    mock_task.delay.assert_not_called()


def test_clean_confirm_clears_the_document_and_returns_the_new_revision(client):
    """Confirm bakes the edit into render_uri, so the document must be emptied or
    the next export cuts the same spans out of an already-shortened render."""

    from main import app

    creator = _mock_creator()
    clip = _render_clip(creator.id)
    clip.cleaned_render_uri = "clips/x_edit.mp4"

    captured = _install(creator, clip, execute_after_owned=[_result_rows((4,))])
    try:
        resp = client.post(f"/clips/{clip.id}/clean/confirm")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "swapped"
    assert resp.json()["edit_revision"] == 4

    # The second statement is the document clear, and it must be an UPDATE.
    assert "UPDATE clip_edit_documents" in str(captured[1]).replace("\n", " ")


def test_clean_discard_does_not_touch_the_document(client):
    """The creator rejected that render; their cuts still describe an edit that
    has NOT been applied. Clearing here would delete work they never applied."""
    from unittest.mock import patch

    from main import app

    creator = _mock_creator()
    clip = _render_clip(creator.id)
    clip.cleaned_render_uri = "clips/x_edit.mp4"

    captured = _install(creator, clip)
    try:
        with patch("worker.storage.adelete_file", AsyncMock()):
            resp = client.post(f"/clips/{clip.id}/clean/discard")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "discarded"
    statements = " ".join(str(s).replace("\n", " ") for s in captured)
    assert "clip_edit_documents" not in statements, "discard must leave the edit document alone"
