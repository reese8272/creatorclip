"""Tests for the GDPR Art. 15/20 data-export endpoints (Issue 249).

The async task body (aggregation across tables + R2 upload) is integration-tested
on staging; here we lock the endpoint contract: poll states, the 202 enqueue, the
single-tenant download (presigned redirect / 409-until-ready).
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from auth import get_current_creator
from db import get_session
from main import app
from models import Creator, DataExport, DataExportStatus


def _creator():
    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    return c


def _row(creator_id, *, status=DataExportStatus.ready, export_uri="s3://b/exports/x.json"):
    r = MagicMock(spec=DataExport)
    r.creator_id = creator_id
    r.status = status
    r.export_uri = export_uri
    r.error = None
    r.requested_at = None
    r.completed_at = None
    r.job_id = "job-1"
    return r


def _session(scalar_return):
    s = AsyncMock()
    s.scalar = AsyncMock(return_value=scalar_return)
    s.add = MagicMock()
    s.flush = AsyncMock()
    s.commit = AsyncMock()
    return s


def _overrides(creator, session):
    app.dependency_overrides[get_current_creator] = lambda: creator

    async def _gs():
        yield session

    app.dependency_overrides[get_session] = _gs


def test_export_status_none_when_no_row(client):
    creator = _creator()
    _overrides(creator, _session(None))
    try:
        resp = client.get("/creators/me/export", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json()["status"] == "none"


def test_export_status_ready(client):
    creator = _creator()
    _overrides(creator, _session(_row(creator.id, status=DataExportStatus.ready)))
    try:
        resp = client.get("/creators/me/export", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()
    assert resp.json()["status"] == "ready"


def test_start_export_returns_202_and_enqueues(client):
    creator = _creator()
    _overrides(creator, _session(None))  # no existing row → create + enqueue
    fake_task = MagicMock(id="task-xyz")
    try:
        with patch("worker.tasks.generate_data_export") as task:
            task.delay.return_value = fake_task
            resp = client.post("/creators/me/export", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 202
    assert resp.json()["task_id"] == "task-xyz"
    task.delay.assert_called_once_with(str(creator.id))


def test_download_redirects_when_ready(client):
    creator = _creator()
    _overrides(creator, _session(_row(creator.id, status=DataExportStatus.ready)))
    try:
        with patch("routers.export.presigned_download_url", return_value="https://signed/export"):
            resp = client.get(
                "/creators/me/export/download", cookies={"session": "x"}, follow_redirects=False
            )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://signed/export"


def test_download_409_until_ready(client):
    creator = _creator()
    _overrides(creator, _session(_row(creator.id, status=DataExportStatus.pending)))
    try:
        resp = client.get("/creators/me/export/download", cookies={"session": "x"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 409


# ── Collector coverage (Issue 391) ───────────────────────────────────────────
#
# _collect_creator_export is a hand-written dict literal with no registry behind
# it, and nothing failed when a table was left out — the collector already omits
# several tables that have rows. These two tests do not try to fix that
# retroactively; they pin the tables whose absence would be a compliance defect,
# so a future refactor cannot silently drop one.


async def _collect_with_stub_batches(creator):
    """Run the collector with _keyset_batches stubbed, returning (result, stmts)."""
    from worker import tasks

    stmts: list = []

    async def _fake_batches(session, stmt, pk_col, batch_size=1000):
        stmts.append(stmt)
        return
        yield  # pragma: no cover - makes this an async generator

    with patch.object(tasks, "_keyset_batches", _fake_batches):
        result = await tasks._collect_creator_export(AsyncMock(), creator)
    return result, stmts


async def test_export_includes_the_creators_edit_documents():
    """The edit document holds cut lists the creator authored — squarely in
    scope for GDPR Art. 20 portability."""
    creator = _creator()
    result, _ = await _collect_with_stub_batches(creator)
    assert "clip_edit_documents" in result


async def test_export_scopes_edit_documents_to_the_creator():
    """Single-tenant by construction: the edit-document select must carry a
    creator_id predicate, not just a clip_id IN (...) that could go unbounded."""
    from models import ClipEditDocument

    creator = _creator()
    result, stmts = await _collect_with_stub_batches(creator)
    assert result["clip_edit_documents"] == []

    edit_stmts = [
        s
        for s in stmts
        if any(
            getattr(f, "name", None) == ClipEditDocument.__tablename__ for f in s.get_final_froms()
        )
    ]
    assert len(edit_stmts) == 1, "expected exactly one edit-document select"
    assert "creator_id" in str(edit_stmts[0].whereclause)
