"""
Tests for Issue 14 — static serving + UI shell.
Covers: GET /, static file serving, GET /videos (list endpoint).
"""

import datetime
import uuid
from unittest.mock import AsyncMock, MagicMock

from auth import get_current_creator
from db import get_session
from main import app
from models import IngestStatus, VideoKind

# ── Root and static routes ────────────────────────────────────────────────────


def test_root_returns_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_root_contains_creatorclip(client):
    resp = client.get("/")
    assert b"AutoClip" in resp.content


def test_static_onboarding_served(client):
    resp = client.get("/static/onboarding.html")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_static_review_served(client):
    resp = client.get("/static/review.html")
    assert resp.status_code == 200


def test_static_profile_served(client):
    resp = client.get("/static/profile.html")
    assert resp.status_code == 200


def test_static_insights_served(client):
    resp = client.get("/static/insights.html")
    assert resp.status_code == 200


def test_static_tos_served(client):
    resp = client.get("/static/tos.html")
    assert resp.status_code == 200
    assert b"Terms of Service" in resp.content


def test_static_privacy_served(client):
    resp = client.get("/static/privacy.html")
    assert resp.status_code == 200
    assert b"Privacy Policy" in resp.content


def test_privacy_page_has_limited_use_disclosure(client):
    """COMPLIANCE.md requires the Google Limited Use disclosure in the public Privacy
    Policy before launch (Issue 78g). Pin the canonical language so it can't regress."""
    resp = client.get("/static/privacy.html")
    assert resp.status_code == 200
    text = resp.content.decode()
    assert "Limited Use" in text
    assert "Google API Services User Data Policy" in text
    assert "information received from Google APIs" in text
    # The affirmative no-advertising commitment must be present.
    assert "advertis" in text.lower()


# ── GET /videos list endpoint ─────────────────────────────────────────────────


def test_list_videos_requires_auth(client):
    resp = client.get("/videos")
    assert resp.status_code == 401


def _mock_creator():
    c = MagicMock()
    c.id = uuid.uuid4()
    return c


def _mock_video(creator_id, title="Test video", yt_id="abc123"):
    v = MagicMock()
    v.id = uuid.uuid4()
    v.creator_id = creator_id
    v.youtube_video_id = yt_id
    v.title = title
    v.kind = VideoKind.long
    v.ingest_status = IngestStatus.done
    v.duration_s = 600.0
    v.created_at = datetime.datetime.now(datetime.UTC)
    return v


def _fake_session(videos):
    async def _session():
        session = AsyncMock()
        result = MagicMock()
        result.scalars.return_value = videos
        session.execute = AsyncMock(return_value=result)
        yield session

    return _session


def test_list_videos_returns_list(client):
    creator = _mock_creator()
    video = _mock_video(creator.id)
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session([video])
    try:
        resp = client.get("/videos")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert data[0]["youtube_video_id"] == "abc123"
    assert data[0]["ingest_status"] == "done"


def test_list_videos_empty_returns_empty_list(client):
    creator = _mock_creator()
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session([])
    try:
        resp = client.get("/videos")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json() == []


def test_list_videos_response_has_required_keys(client):
    creator = _mock_creator()
    video = _mock_video(creator.id)
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session([video])
    try:
        resp = client.get("/videos")
    finally:
        app.dependency_overrides.clear()

    item = resp.json()[0]
    for key in ("id", "youtube_video_id", "title", "kind", "ingest_status", "created_at"):
        assert key in item


# ── Issue 90: catalog-only rows excluded from /videos list ───────────────────
#
# `sync_channel_catalog` upserts every video on the creator's channel as a
# Video row with `source_uri=None` so DNA build has the metric set without
# triggering the local clip pipeline. Those rows must NOT appear in the
# dashboard's `/videos` list — they have no ingest pipeline running, would
# show "pending forever," and the dashboard's polling loop would hammer
# `/status` for rows that can never transition.


# ── Issue 91: clips-ready counter filters render_status=done ─────────────────


def test_dashboard_clips_counter_filters_by_render_status():
    """Static-page guard: the dashboard JS must filter clips by render_status,
    not just count them all. Previously the counter showed total clips, but
    only `render_status === 'done'` clips are actually playable in the reviewer
    (`render_uri` is set only on done renders). Showing "12 ready" when none
    are playable is exactly the credibility-corroding UX Issue 91 closes.
    """
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "index.html").read_text()
    assert "render_status === 'done'" in src, (
        "Dashboard clips counter must filter by render_status='done'. "
        "Issue 91 — counter previously included pending/running clips that "
        "the reviewer cannot play."
    )
    # The label switched to "rendered" so the counter name matches what it counts.
    assert "Clips rendered" in src, (
        "Card label should be 'Clips rendered' to match the counter semantics "
        "(it now counts only done renders, not generated clip rows)."
    )


# ── Wave 5: global activity panel wired into every authenticated template ───


def test_activity_panel_library_exists_with_canonical_position():
    """Wave-5 Fix 3: static/activityPanel.js is the floating bottom-right
    widget that surfaces in-progress background tasks across all pages.
    Pins (a) the file exists, (b) the canonical bottom-right Linear/Vercel-
    style position, (c) the dependency contract on activeTasks.js."""
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "activityPanel.js").read_text()

    assert "window.activeTasks" in src or "global.activeTasks" in src, (
        "activityPanel.js MUST consume window.activeTasks — it's the "
        "single source of truth for in-progress tasks (Wave-5 Fix 2)."
    )
    # Industry-standard 2026 floating activity tray position (Linear, Vercel,
    # Notion). Bottom-right doesn't compete with primary content above the
    # fold and stays out of keyboard nav from the top nav.
    assert "bottom" in src and "right" in src, (
        "activityPanel.js must use the canonical bottom-right floating "
        "position; pins the design decision so a future restyle doesn't "
        "regress it without intent."
    )
    # Hidden when nothing's running — the panel only appears when there's
    # actual work to show.
    assert "cc-hidden" in src, "activityPanel.js must hide itself when no tasks are active."


def test_all_authenticated_templates_include_active_tasks_and_panel():
    """Wave-5 Fix 3: every authenticated static template includes BOTH
    activeTasks.js and activityPanel.js so the cross-page task panel
    is present on every page. The user's stated need: 'when going from
    tab to tab, we are not refreshing the information.'

    Static (unauthenticated) pages — privacy.html, tos.html,
    early-access.html — are deliberately excluded; they're public
    marketing/legal surfaces with no user state.
    """
    import pathlib

    static_dir = pathlib.Path(__file__).parent.parent / "static"
    authenticated_templates = [
        "index.html",
        "onboarding.html",
        "insights.html",
        "profile.html",
        "review.html",
        "pricing.html",
    ]

    for name in authenticated_templates:
        src = (static_dir / name).read_text()
        assert "/static/activeTasks.js" in src, (
            f"Wave-5 Fix 3: {name} must include /static/activeTasks.js — "
            f"otherwise the cross-page task state is lost on navigation."
        )
        assert "/static/activityPanel.js" in src, (
            f"Wave-5 Fix 3: {name} must include /static/activityPanel.js — "
            f"otherwise the user cannot see in-progress tasks from this page."
        )


# ── Wave 5: activeTasks.js library exists + exposes documented API ──────────


def test_active_tasks_library_exists_and_exports_api():
    """Wave-5 Fix 2: static/activeTasks.js manages localStorage + SSE
    EventSource resume so background work (DNA build, catalog sync,
    improvement brief, upload chain, render) survives page-to-page
    navigation. This test pins the file exists + exposes the documented
    public API to `window.activeTasks`.
    """
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "activeTasks.js").read_text()

    # The file must declare its localStorage key — pins the namespace.
    assert "creatorclip:active_tasks" in src, (
        "activeTasks.js must use the `creatorclip:active_tasks` localStorage "
        "key (prefix-namespaced per industry-standard practice)."
    )

    # Public API surface: every consumer (the global activity panel +
    # page-specific UI) depends on these being exposed on window.activeTasks.
    for symbol in (
        "registerTask",
        "getActiveTasks",
        "subscribe",
        "removeTask",
    ):
        assert symbol + ":" in src or symbol + " :" in src, (
            f"activeTasks.js must export `{symbol}` on window.activeTasks. "
            f"The global activity panel and page-specific UI both depend on it."
        )

    # The Last-Event-ID resume contract: every received event updates
    # last_event_id so a navigation mid-stream resumes from the right cursor.
    assert "last_event_id" in src, (
        "activeTasks.js must track last_event_id so page navigation mid-stream "
        "resumes from the right XREAD cursor (Issue 86 SSE contract)."
    )

    # Stale entries (> server-side stream TTL of 1h) get garbage-collected
    # on every page load — pins the cleanup invariant.
    assert "STALE_AFTER_MS" in src and "60 * 60 * 1000" in src, (
        "activeTasks.js must GC entries older than 1h (matches the "
        "_STREAM_TTL_SECONDS=3600 in worker/progress.py — beyond this window "
        "the server can't resume the stream anyway)."
    )


def test_list_videos_excludes_catalog_only_rows(client):
    """The SELECT must filter `Video.source_uri IS NOT NULL`. Verified by
    introspecting the SQLAlchemy statement passed to session.execute.
    """
    captured_statements: list = []
    creator = _mock_creator()

    def _capturing_session():
        async def _session():
            session = AsyncMock()
            result = MagicMock()
            result.scalars.return_value = []

            async def _execute(stmt):
                captured_statements.append(stmt)
                return result

            session.execute = _execute
            yield session

        return _session

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _capturing_session()
    try:
        resp = client.get("/videos")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert len(captured_statements) == 1
    compiled = str(captured_statements[0].compile(compile_kwargs={"literal_binds": True}))
    assert "source_uri IS NOT NULL" in compiled, (
        "list_videos must filter Video.source_uri.isnot(None) so catalog-only "
        "(DNA-reference) rows don't pollute the dashboard. (Issue 90)"
    )
