"""Issue 125 — Video control model + minutes transparency.

Tests:
  - Structural: AnalysisMode enum exists with the three documented values
  - Structural: Creator.analysis_mode column wired, defaults to auto
  - Structural: migration 0022 creates the column + enum type
  - Behavioral: GET /creators/me exposes analysis_mode
  - Behavioral: PATCH /creators/me/analysis-mode persists + returns the new value
  - Behavioral: PATCH rejects bogus enum values (422)
  - Behavioral: POST /videos/{id}/queue runs start_pipeline on pending; idempotent otherwise
  - Structural: AnalysisQueuedOut carries analytics_available alongside has_metrics
  - Static: profile.html has the intake-mode radio form (3 options)
  - Static: index.html exposes the "what costs minutes" tooltip + uses /videos/{id}/queue
  - Static: analysis.html exposes the explicit analytics-unavailable surface
"""

import pathlib
import uuid as _uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

# ── Structural: models + migration ──────────────────────────────────────────


def test_issue_125_analysis_mode_enum_has_three_values():
    """The control-mode column is the contract for Issue 125; pin the value
    set so a future 'let me add advanced mode' PR can't silently churn it."""
    from models import AnalysisMode

    assert {m.value for m in AnalysisMode} == {"auto", "selective", "manual"}


def test_issue_125_creator_has_analysis_mode_column_default_auto():
    """Creator.analysis_mode must be a NOT NULL column defaulting to auto so
    every pre-Issue-125 row picks up the current implicit behavior on
    migration 0022 backfill."""
    from models import AnalysisMode, Creator

    col = Creator.__table__.columns["analysis_mode"]
    assert col.nullable is False, "analysis_mode must be NOT NULL"
    assert col.server_default is not None, (
        "analysis_mode must carry a server_default so the migration backfills "
        "existing rows without a separate data step."
    )
    # The Python-side default should resolve to AnalysisMode.auto.
    assert col.default.arg == AnalysisMode.auto


def test_issue_125_migration_0022_present_and_references_creators_table():
    """Migration 0022 must add the column + create the enum type."""
    src = (
        pathlib.Path(__file__).parent.parent
        / "alembic"
        / "versions"
        / "0022_creator_analysis_mode.py"
    ).read_text()
    assert 'down_revision = "0021"' in src, "0022 must chain after 0021"
    assert "analysis_mode_enum" in src, "must name the Postgres enum type"
    # Accept either single-line (op.add_column("creators", ...)) or the
    # multi-line form used here (op.add_column(\n    "creators",\n    ...))
    normalized = " ".join(src.replace("'", '"').split())
    assert 'add_column( "creators"' in normalized or 'add_column("creators"' in normalized, (
        "must add a column on the creators table"
    )
    assert "auto" in src and "selective" in src and "manual" in src, (
        "all three documented values must appear in the migration"
    )


# ── Behavioral: GET + PATCH /creators/me/analysis-mode ──────────────────────


def _override_creator(creator):
    """Helper: dep override mirroring tests/_helpers.py::override_current_creator."""
    from fastapi import Request

    async def _override(request: Request):
        request.state.creator_id = creator.id
        return creator

    return _override


def test_issue_125_get_me_returns_analysis_mode(client):
    """The dashboard reads window.__USER__.analysis_mode to decide which
    intake CTA to show; without it on /creators/me the UI defaults to auto
    forever even after a PATCH."""
    from auth import get_current_creator
    from db import get_session
    from main import app
    from models import AnalysisMode, OnboardingState

    fake_creator = MagicMock()
    fake_creator.id = _uuid.uuid4()
    fake_creator.channel_id = "UC_test"
    fake_creator.channel_title = "Test"
    fake_creator.email = "t@t.co"
    # Real enum required: the 2026-06-08 setup_step resolver dispatches on
    # OnboardingState identity, not the .value string.
    fake_creator.onboarding_state = OnboardingState.active
    fake_creator.analysis_mode = AnalysisMode.selective
    fake_creator.created_at = MagicMock()
    fake_creator.created_at.isoformat = lambda: "2026-06-08T00:00:00+00:00"

    async def _fake_session():
        session = AsyncMock()
        result = MagicMock()
        result.scalar_one.return_value = 1  # at least one clip-track video
        session.execute = AsyncMock(return_value=result)
        yield session

    original = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_creator] = _override_creator(fake_creator)
    app.dependency_overrides[get_session] = _fake_session
    try:
        resp = client.get("/creators/me")
        assert resp.status_code == 200, resp.text
        assert resp.json()["analysis_mode"] == "selective"
    finally:
        app.dependency_overrides = original


@pytest.mark.parametrize("new_mode", ["selective", "manual", "auto"])
def test_issue_125_patch_analysis_mode_persists_and_returns(client, new_mode):
    """PATCH happy path: each valid enum value persists onto the creator row
    and round-trips in the response."""
    from auth import get_current_creator
    from db import get_session
    from main import app
    from models import AnalysisMode

    fake_creator = MagicMock()
    fake_creator.id = _uuid.uuid4()
    fake_creator.analysis_mode = AnalysisMode.auto

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.add = MagicMock()

    async def _fake_session():
        yield mock_session

    original = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_creator] = _override_creator(fake_creator)
    app.dependency_overrides[get_session] = _fake_session
    try:
        resp = client.patch(
            "/creators/me/analysis-mode",
            json={"analysis_mode": new_mode},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["analysis_mode"] == new_mode
        # The mutation must hit the session — without session.add(creator)
        # nothing gets flushed when other handlers expire_on_commit the row.
        mock_session.add.assert_called_once()
        mock_session.commit.assert_awaited_once()
        assert fake_creator.analysis_mode.value == new_mode
    finally:
        app.dependency_overrides = original


def test_issue_125_patch_analysis_mode_rejects_unknown_value(client):
    """422 on enum violation — without this an attacker (or a stale UI) could
    set Creator.analysis_mode to anything Postgres accepts as a free string
    if we later relaxed the column."""
    from auth import get_current_creator
    from db import get_session
    from main import app
    from models import AnalysisMode

    fake_creator = MagicMock()
    fake_creator.id = _uuid.uuid4()
    fake_creator.analysis_mode = AnalysisMode.auto

    mock_session = AsyncMock()

    async def _fake_session():
        yield mock_session

    original = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_creator] = _override_creator(fake_creator)
    app.dependency_overrides[get_session] = _fake_session
    try:
        resp = client.patch(
            "/creators/me/analysis-mode",
            json={"analysis_mode": "aggressive"},
        )
        assert resp.status_code == 422, resp.text
        # Mutation must NOT have run on a 422.
        mock_session.commit.assert_not_called()
        assert fake_creator.analysis_mode == AnalysisMode.auto
    finally:
        app.dependency_overrides = original


# ── Behavioral: POST /videos/{id}/queue ─────────────────────────────────────


def test_issue_125_queue_endpoint_triggers_pipeline_on_pending(client, mocker):
    """The /queue endpoint is the user-facing equivalent of `start_pipeline`
    for selective/manual mode. Verify it dispatches to start_pipeline when
    the video is pending + owned by the caller."""
    from auth import get_current_creator
    from db import get_session
    from main import app
    from models import IngestStatus

    creator_id = _uuid.uuid4()
    video_id = _uuid.uuid4()

    fake_creator = MagicMock()
    fake_creator.id = creator_id

    fake_video = MagicMock()
    fake_video.id = video_id
    fake_video.creator_id = creator_id
    fake_video.ingest_status = IngestStatus.pending

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=fake_video)

    async def _fake_session():
        yield mock_session

    # Patch the actual pipeline kickoff so we don't talk to Celery + Redis.
    mock_start = mocker.patch("routers.videos.start_pipeline")

    original = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_creator] = _override_creator(fake_creator)
    app.dependency_overrides[get_session] = _fake_session
    try:
        resp = client.post(f"/videos/{video_id}/queue")
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["queued"] is True
        assert body["status"] == "pending"
        mock_start.assert_called_once_with(str(video_id))
    finally:
        app.dependency_overrides = original


def test_issue_125_queue_endpoint_idempotent_when_not_pending(client, mocker):
    """A second click while ingestion is already running must NOT re-queue —
    that would burn minutes twice and create duplicate work."""
    from auth import get_current_creator
    from db import get_session
    from main import app
    from models import IngestStatus

    creator_id = _uuid.uuid4()
    video_id = _uuid.uuid4()
    fake_creator = MagicMock()
    fake_creator.id = creator_id

    fake_video = MagicMock()
    fake_video.id = video_id
    fake_video.creator_id = creator_id
    fake_video.ingest_status = IngestStatus.running

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=fake_video)

    async def _fake_session():
        yield mock_session

    mock_start = mocker.patch("routers.videos.start_pipeline")

    original = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_creator] = _override_creator(fake_creator)
    app.dependency_overrides[get_session] = _fake_session
    try:
        resp = client.post(f"/videos/{video_id}/queue")
        assert resp.status_code == 202, resp.text
        assert resp.json()["queued"] is False
        mock_start.assert_not_called()
    finally:
        app.dependency_overrides = original


def test_issue_125_queue_endpoint_404_on_other_creators_video(client, mocker):
    """Per-creator isolation: an authenticated caller cannot queue someone
    else's video — even by guessing a UUID."""
    from auth import get_current_creator
    from db import get_session
    from main import app
    from models import IngestStatus

    caller_id = _uuid.uuid4()
    owner_id = _uuid.uuid4()
    video_id = _uuid.uuid4()
    assert caller_id != owner_id

    fake_caller = MagicMock()
    fake_caller.id = caller_id

    fake_video = MagicMock()
    fake_video.id = video_id
    fake_video.creator_id = owner_id  # owned by someone else
    fake_video.ingest_status = IngestStatus.pending

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=fake_video)

    async def _fake_session():
        yield mock_session

    mock_start = mocker.patch("routers.videos.start_pipeline")

    original = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_creator] = _override_creator(fake_caller)
    app.dependency_overrides[get_session] = _fake_session
    try:
        resp = client.post(f"/videos/{video_id}/queue")
        assert resp.status_code == 404, resp.text
        mock_start.assert_not_called()
    finally:
        app.dependency_overrides = original


# ── Structural: analytics_available wired on AnalysisQueuedOut ──────────────


def test_issue_125_analysis_queued_out_has_analytics_available_field():
    """The AnalysisQueuedOut response model must expose analytics_available
    so the UI can render the explicit unavailable surface (analysis.html)."""
    from routers.analysis import AnalysisQueuedOut

    fields = set(AnalysisQueuedOut.model_fields.keys())
    assert "analytics_available" in fields, (
        "AnalysisQueuedOut must carry analytics_available (Issue 125)."
    )
    assert "has_metrics" in fields, "has_metrics must remain alongside as the back-compat alias."


def test_issue_125_analysis_route_populates_analytics_available_identically():
    """Read the source: the route handler must populate analytics_available
    from the same has_metrics value, so the two fields can never drift."""
    src = (pathlib.Path(__file__).parent.parent / "routers" / "analysis.py").read_text()
    # Both fields must appear in the same return dict and reference has_metrics.
    assert '"analytics_available": has_metrics' in src, (
        "analytics_available must be populated directly from has_metrics so "
        "the two stay in lockstep — a free string would diverge silently."
    )


# ── Static: UI surface pins ─────────────────────────────────────────────────


def test_issue_125_profile_html_intake_mode_form_with_three_radios():
    """The profile page must offer all three modes as radios + wire to the
    PATCH endpoint via saveAnalysisMode()."""
    src = (pathlib.Path(__file__).parent.parent / "static" / "profile.html").read_text()
    assert 'id="intake-mode-form"' in src
    for mode in ("auto", "selective", "manual"):
        assert f'value="{mode}"' in src, f"profile.html must offer a radio for analysis_mode={mode}"
    assert "/creators/me/analysis-mode" in src, (
        "profile.html must POST/PATCH to /creators/me/analysis-mode"
    )
    assert "saveAnalysisMode" in src


def test_issue_125_index_html_has_what_costs_minutes_tooltip():
    """The dashboard nav must expose the 'what costs minutes' explainer
    via the existing tooltip system so the user can answer the question
    without leaving the page."""
    src = (pathlib.Path(__file__).parent.parent / "static" / "index.html").read_text()
    # The tooltip system from Issue 124 reads data-tooltip on focusable spans.
    assert "data-tooltip=" in src
    # The honesty copy must mention what's billable AND what's free — pinning
    # both halves prevents a future trim from silently dropping the free side.
    assert "Transcription" in src and "clip generation" in src, (
        "balance tooltip must list the minute-consuming actions"
    )
    assert "always free" in src.lower() or "free." in src.lower(), (
        "balance tooltip must explicitly state what is NOT billed"
    )


def test_issue_125_index_html_queue_button_wired_to_endpoint():
    """The dashboard must surface a Queue CTA for pending videos — and that
    CTA must hit /videos/{id}/queue, not silently call generate-clips."""
    src = (pathlib.Path(__file__).parent.parent / "static" / "index.html").read_text()
    assert "queueVideo(" in src, "dashboard must define a queueVideo() handler"
    assert "/queue" in src, "queueVideo() must POST to the /queue endpoint"
    assert "Queue for analysis" in src, (
        "the user-facing CTA copy must be 'Queue for analysis' (spec)"
    )


def test_issue_125_analysis_html_has_explicit_unavailable_surface():
    """The analysis page must carry a dedicated element for the analytics-
    unavailable copy (with an Ingest CTA), not bury it inline."""
    src = (pathlib.Path(__file__).parent.parent / "static" / "analysis.html").read_text()
    assert 'id="analytics-unavailable"' in src
    # The fallback copy MUST be honest about what runs in metadata-only mode.
    assert "Full analytics unavailable" in src
    assert "Ingest this video" in src
