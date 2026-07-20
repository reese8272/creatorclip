"""
Tests for Issue 14 — static serving + UI shell.
Covers: GET /, static file serving, GET /videos (list endpoint).
"""

import datetime
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from auth import get_current_creator
from db import get_session
from main import _SPA_BUILT, app
from models import IngestStatus, OnboardingState, VideoKind, VideoOrigin

# ── Root and static routes ────────────────────────────────────────────────────


@pytest.mark.skipif(_SPA_BUILT, reason="SPA bundle built — `/` redirects instead (see below)")
def test_root_returns_404_without_spa_bundle(client):
    # Issue 226: legacy static/index.html retired (XSS surface removal).
    # Without the SPA build, `/` returns 404 instead of a legacy HTML page.
    resp = client.get("/")
    assert resp.status_code == 404


@pytest.mark.skipif(not _SPA_BUILT, reason="no SPA bundle — `/` serves the legacy index")
def test_root_redirects_to_spa_when_built(client):
    # Issue 85g cutover: once the SPA is built, `/` is the React app's front door.
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/app/dashboard"


@pytest.mark.skipif(not _SPA_BUILT, reason="no SPA bundle to serve files from")
def test_spa_serves_public_assets_before_shell_fallback(client):
    # The Chip mascot sprites live in dist/chip/ (Vite-copied public/ assets).
    # The /app catch-all must serve them as real files — not return index.html,
    # which rendered the <img> blank in production.
    resp = client.get("/app/chip/chip-book.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


@pytest.mark.skipif(not _SPA_BUILT, reason="no SPA bundle to serve files from")
def test_spa_falls_back_to_shell_for_client_routes(client):
    # A non-file path (a React Router client route) still returns the SPA shell.
    resp = client.get("/app/dashboard")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")


# ── Issue 226: retired legacy HTML pages must return 404 ─────────────────────
# The React SPA is canonical. Legacy pages removed to eliminate XSS attack
# surface (stored-XSS via innerHTML of LLM/YouTube output — Issues 138, 149).
# Only tos.html and privacy.html are retained (legal/OAuth verification gates).

_RETIRED_LEGACY_PAGES = [
    "analysis.html",
    "index.html",
    "insights.html",
    "login.html",
    "onboarding.html",
    "pricing.html",
    "profile.html",
    "review.html",
    "walkthrough.html",
]


def test_retired_legacy_html_pages_return_404(client):
    """Issue 226: every retired legacy HTML page must return 404.
    These pages were removed to eliminate the XSS attack surface (OWASP LLM05:2025).
    tos.html and privacy.html are NOT retired (legal/OAuth requirements).
    """
    for page in _RETIRED_LEGACY_PAGES:
        resp = client.get(f"/static/{page}")
        assert resp.status_code == 404, (
            f"/static/{page} must return 404 — the page was retired in Issue 226 "
            f"to close the XSS attack surface (OWASP LLM05:2025, Issues 138/149)."
        )


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


def _mock_creator(onboarding_state=OnboardingState.active):
    c = MagicMock()
    c.id = uuid.uuid4()
    c.onboarding_state = onboarding_state
    return c


def _mock_video(creator_id, title="Test video", yt_id="abc123"):
    v = MagicMock()
    v.id = uuid.uuid4()
    v.creator_id = creator_id
    v.youtube_video_id = yt_id
    v.title = title
    v.kind = VideoKind.long
    v.ingest_status = IngestStatus.done
    v.failure_reason = None
    v.duration_s = 600.0
    v.created_at = datetime.datetime.now(datetime.UTC)
    v.origin = VideoOrigin.upload
    v.source_uri = f"source/{creator_id}/{yt_id}.mp4"
    return v


def _fake_session(videos):
    async def _session():
        session = AsyncMock()
        result = MagicMock()
        result.scalars.return_value = videos
        session.execute = AsyncMock(return_value=result)
        yield session

    return _session


def test_list_videos_returns_envelope_with_videos(client):
    """``/videos`` returns the ``VideoListOut`` envelope (DECISIONS 2026-06-08).

    Populated case: ``state == "populated"``, no empty-state copy, and the
    items themselves carry the same per-row fields as before so downstream
    consumers only have to learn ``body.videos`` instead of ``body``.
    """
    creator = _mock_creator()
    video = _mock_video(creator.id)
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session([video])
    try:
        resp = client.get("/videos")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "populated"
    assert body["message"] is None
    assert body["next_action"] is None
    assert body["videos"][0]["youtube_video_id"] == "abc123"
    assert body["videos"][0]["ingest_status"] == "done"


def test_list_videos_empty_returns_envelope_with_guidance(client):
    """Empty + onboarding_state=active → ``empty_initial`` envelope with a
    "Link your first video" next step. The frontend renders the server
    message verbatim so the dashboard is never silently blank.
    """
    creator = _mock_creator(onboarding_state=OnboardingState.active)
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session([])
    try:
        resp = client.get("/videos")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["videos"] == []
    assert body["state"] == "empty_initial"
    # State==active still falls through to the same "link a video" copy because
    # the only onboarding_state that gates the message is `connected` (no DNA yet);
    # any later state means the creator is past the "connect" step.
    assert body["message"] is not None


def test_list_videos_empty_connected_state_suggests_link_video(client):
    """Brand-new creator (just connected, no DNA, no videos) gets a
    link-a-video next action with ``action_type="open_form"`` — the
    frontend uses that to expand the link form inline instead of
    navigating away.
    """
    creator = _mock_creator(onboarding_state=OnboardingState.connected)
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session([])
    try:
        resp = client.get("/videos")
    finally:
        app.dependency_overrides.clear()

    body = resp.json()
    assert body["state"] == "empty_initial"
    assert body["next_action"]["action_type"] == "open_form"
    # next_action repointed /static/index.html → /app/dashboard in the SPA cutover (85g).
    assert "/app/dashboard" in body["next_action"]["url"]


def test_list_videos_response_has_required_keys(client):
    creator = _mock_creator()
    video = _mock_video(creator.id)
    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session([video])
    try:
        resp = client.get("/videos")
    finally:
        app.dependency_overrides.clear()

    item = resp.json()["videos"][0]
    for key in (
        "id",
        "youtube_video_id",
        "title",
        "kind",
        "ingest_status",
        "created_at",
        "origin",
        "clippable",
    ):
        assert key in item
    # Issue 139: an uploaded video is clip-trackable; provenance is surfaced.
    assert item["origin"] == "upload"
    assert item["clippable"] is True


# ── Issue 90: catalog-only rows excluded from /videos list ───────────────────
#
# `sync_channel_catalog` upserts every video on the creator's channel as a
# Video row with `source_uri=None` so DNA build has the metric set without
# triggering the local clip pipeline. Those rows must NOT appear in the
# dashboard's `/videos` list — they have no ingest pipeline running, would
# show "pending forever," and the dashboard's polling loop would hammer
# `/status` for rows that can never transition.


# ── Issue 91: clips-ready counter filters render_status=done ─────────────────


@pytest.mark.skip(
    reason="Issue 226: static/index.html retired — XSS surface removed. "
    "The React SPA (frontend/src/) is now the canonical surface."
)
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


@pytest.mark.skip(
    reason="Issue 226: all authenticated legacy HTML templates retired — "
    "React SPA (frontend/src/) is now the canonical surface."
)
def test_all_authenticated_templates_include_active_tasks_and_panel():
    """Wave-5 Fix 3: every authenticated static template includes BOTH
    activeTasks.js and activityPanel.js so the cross-page task panel
    is present on every page. The user's stated need: 'when going from
    tab to tab, we are not refreshing the information.'

    Static (unauthenticated) pages — privacy.html, tos.html — are
    deliberately excluded; they're public legal surfaces with no user state.
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


@pytest.mark.skip(reason="Issue 226: static/index.html retired — React SPA is canonical.")
def test_link_video_input_accepts_full_urls():
    """The Link-a-video input must accept full YouTube URLs, not just bare IDs.
    Users naturally paste share URLs; the extractYouTubeId() helper must strip
    them to the 11-char ID before the form submits to /videos/link."""
    import pathlib
    import re

    src = (pathlib.Path(__file__).parent.parent / "static" / "index.html").read_text()

    assert "extractYouTubeId" in src, (
        "index.html must define extractYouTubeId() to normalise pasted URLs"
    )
    # youtu.be short-link path is the most common share format
    assert "youtu.be" in src, "extractYouTubeId must handle youtu.be short links"
    # watch?v= is the standard desktop URL
    assert "searchParams.get('v')" in src or 'searchParams.get("v")' in src, (
        "extractYouTubeId must handle youtube.com/watch?v=ID URLs"
    )
    # /shorts/ links are common for Shorts (regex in source uses \/shorts\/)
    assert "shorts" in src, "extractYouTubeId must handle youtube.com/shorts/ID URLs"
    # linkVideo() must call the extractor, not use the raw input directly
    extractor_call = re.search(r"extractYouTubeId\s*\(", src)
    assert extractor_call, "linkVideo() must call extractYouTubeId() on the raw input"


def test_design_tokens_file_exists_with_canonical_linear_palette():
    """Issue 99 Phase A: static/_design-tokens.css is the canonical
    design system. Pin (a) the file exists, (b) it imports Inter +
    JetBrains Mono, (c) the Linear-locked palette values are defined
    so a future "let's brighten this" PR can't silently regress the
    direction the user explicitly picked from the 8-option survey.

    See docs/DECISIONS.md "2026-05-31 — Issue 99 design direction"
    for the full rationale and what was rejected.
    """
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "_design-tokens.css").read_text()

    # Google Fonts import for Inter + JetBrains Mono with font-display: swap
    # so the system fallback renders instantly while the variable fonts load.
    assert "fonts.googleapis.com" in src, (
        "_design-tokens.css must @import Inter + JetBrains Mono from "
        "Google Fonts (the picked typography pairing for Issue 99)."
    )
    assert "family=Inter" in src and "family=JetBrains+Mono" in src, (
        "_design-tokens.css must load BOTH Inter (sans) and JetBrains Mono "
        "(data register) — the two halves of the Linear/mono composition."
    )
    assert "display=swap" in src, (
        "Google Fonts URL must include display=swap so the system "
        "fallback renders instantly; otherwise we get FOIT (flash of "
        "invisible text) and the page looks broken for ~200ms."
    )

    # The Linear-locked palette. These exact values were picked from the
    # researched menu and are locked in docs/DECISIONS.md.
    palette = {
        "--color-bg": "#0a0a0a",
        "--color-surface": "#111111",
        "--color-border": "#1f1f1f",
        "--color-text": "#ededed",
        "--color-accent": "#5e6ad2",  # Linear indigo
    }
    for var_name, expected_value in palette.items():
        assert f"{var_name}:" in src, (
            f"_design-tokens.css must define {var_name} — locked in the Issue 99 design system."
        )
        assert expected_value in src, (
            f"_design-tokens.css must use {expected_value} for {var_name} — "
            f"the value the user picked from the Linear option. Changing "
            f"this is a design-direction shift that belongs in DECISIONS, "
            f"not a quiet edit."
        )

    # The mono data register is a load-bearing concept — pin the .mono
    # utility class so Phase C consumers can rely on it.
    assert ".mono {" in src, (
        "_design-tokens.css must expose a .mono utility class for the "
        "data register (clip metadata, transcript timestamps, IDs, "
        "durations, scores). Phase C of Issue 99 retrofits these "
        "surfaces onto this class."
    )


@pytest.mark.skip(reason="Issue 226: static/review.html retired — React SPA is canonical.")
def test_review_page_exposes_why_this_clip_panel():
    """Issue 94 — clip transparency. review.html must surface the
    Claude-authored reasoning + cited principle + score + timing
    structure for every clip via a 'Why this clip?' expander. Each
    clip in the queue already has principle/reasoning fields in the
    /clips response (ClipOut); this pin guards the UI consumption."""
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "review.html").read_text()

    assert 'id="why-clip"' in src and "Why this clip?" in src, (
        "review.html must include a Why-this-clip details panel (Issue 94)."
    )
    # The four fields the panel surfaces must be populated by loadClip
    for slot in ("why-principle", "why-reasoning", "why-score", "why-timing"):
        assert f'id="{slot}"' in src, (
            f"Why-this-clip panel must include the {slot} populated "
            f"by loadClip — Issue 94 transparency contract."
        )
    # The Claude reasoning field must be consumed from the API response
    assert "clip.reasoning" in src, (
        "review.html must consume clip.reasoning (already on ClipOut "
        "from the scoring engine). Issue 94's whole point is surfacing it."
    )
    # First-clip open behavior — teaches the affordance once
    assert "userToggled" in src, (
        "The expander must auto-open on the first clip to teach the "
        "affordance, then respect the user's subsequent toggles."
    )


@pytest.mark.skip(reason="Issue 226: static/insights.html retired — React SPA is canonical.")
def test_insights_page_consumes_new_insights_endpoint():
    """Issue 93 — rebuilt insights.html surfaces channel totals, DNA
    snapshot, top + bottom performers, upload windows, improvement
    brief. The Channel snapshot and DNA grid panels depend on
    GET /creators/me/insights — pin the consumption."""
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "insights.html").read_text()

    assert "/creators/me/insights" in src, (
        "insights.html must fetch /creators/me/insights — the new "
        "single-call aggregation endpoint (Issue 93)."
    )
    # Pin the new panels exist (the old version had only 2 panels)
    for panel_id in ("totals-grid", "dna-grid", "top-performers", "bottom-performers"):
        assert f'id="{panel_id}"' in src, (
            f"insights.html must include the {panel_id} panel introduced "
            f"by Issue 93. The pre-rebuild version had only timing + brief."
        )
    # Still has the existing upload-windows + brief panels
    assert "/creators/me/upload-intel" in src
    assert "/creators/me/improvement-brief" in src


@pytest.mark.skip(reason="Issue 226: static/insights.html retired — React SPA is canonical.")
def test_insights_performers_have_sort_control():
    """Issue 149 — the Top/Underperformers panels expose a Sort control
    (default score high→low; flip to low→high or A–Z) and render via the
    client-side renderPerfPanel(). Also pins the XSS-escape regression: the
    performer title/kind/id (YouTube-sourced) must go through escapeHtml in
    the innerHTML render (Issue 138's sweep had missed this row)."""
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "insights.html").read_text()

    # Sort dropdowns wired to the renderer for both panels.
    for sel_id in ('id="top-sort"', 'id="bottom-sort"'):
        assert sel_id in src
    assert 'onchange="renderPerfPanel(' in src
    assert 'value="score-desc"' in src and 'value="title"' in src
    assert "function renderPerfPanel" in src and "function _sortPerf" in src

    # The default option must be score high→low (descending).
    assert src.index('value="score-desc"') < src.index('value="score-asc"')

    # XSS regression: titles are escaped in the performer row render.
    assert "escapeHtml(p.title" in src
    assert "escapeHtml(p.kind)" in src


@pytest.mark.skip(reason="Issue 226: static/walkthrough.html retired — React SPA is canonical.")
def test_walkthrough_page_exists_with_five_panels():
    """Issue 100 — first-run walkthrough has exactly 5 panels (the
    user-locked structure: what-this-is / DNA / what-a-clip-is /
    badges / tell-us-about-you). Pinning the count prevents a future
    panel addition from silently throwing off the dots indicator
    or the keyboard-nav cap."""
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "walkthrough.html").read_text()

    assert 'href="/static/_design-tokens.css"' in src, (
        "walkthrough.html must use the shared design system."
    )
    # 5 panels — pin each data-panel attribute
    for n in range(1, 6):
        assert f'data-panel="{n}"' in src, (
            f"walkthrough.html must include panel {n} (Issue 100 5-panel structure)."
        )
    assert "const PANELS = 5;" in src, (
        "Panel count constant must stay at 5 — drives the keyboard-nav cap and the dots indicator."
    )
    # The required honesty disclaimer (CLAUDE.md North Star constraint).
    # Match on the substring that's invariant across line breaks.
    assert "promise virality" in src, (
        "walkthrough.html must include the honesty disclaimer (CLAUDE.md no-virality rule)."
    )
    # The completion flag — auth.js reads this to skip the walkthrough
    # on subsequent visits.
    assert "creatorclip:walkthrough_seen" in src, (
        "walkthrough.html must set the localStorage flag on completion "
        "so auth.js's first-run gate doesn't re-redirect."
    )


@pytest.mark.skip(
    reason="Issue 226: static/walkthrough.html retired — React SPA is canonical. "
    "auth.js walkthrough redirect to legacy page is obsolete."
)
def test_auth_js_redirects_new_creators_to_walkthrough():
    """Issue 100 — auth.js's first-run gate. New creators
    (onboarding_state = 'connected', walkthrough not yet seen, not
    already on a setup surface) must be routed to /static/walkthrough.html.

    Pin all four gate conditions so a future "let me simplify this"
    PR can't accidentally break the redirect loop guards."""
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "auth.js").read_text()

    # The state check — only first-run creators trigger
    assert "onboarding_state === 'connected'" in src, (
        "auth.js gate must trigger only on onboarding_state='connected' "
        "— established creators with state=active should never see the "
        "walkthrough again."
    )
    # The localStorage skip — walkthrough is one-time
    assert "creatorclip:walkthrough_seen" in src, (
        "auth.js must check the localStorage flag set by walkthrough.html "
        "on completion so we don't loop the creator through it on every "
        "session refresh."
    )
    # The on-setup-surface guard — prevents redirect loop FROM
    # walkthrough.html and onboarding.html
    assert "walkthrough.html" in src and "onboarding.html" in src, (
        "auth.js must exempt walkthrough.html AND onboarding.html from "
        "the redirect — otherwise the user gets caught in a loop the "
        "moment they reach either page."
    )
    # The redirect destination
    assert "/static/walkthrough.html" in src, (
        "auth.js must redirect to the walkthrough page when the gate fires."
    )


@pytest.mark.skip(reason="Issue 226: static/onboarding.html retired — React SPA is canonical.")
def test_onboarding_intake_is_mandatory():
    """Issue 100 — intake step on onboarding.html is no longer skippable.
    The 'Skip for now' button was removed (Issue 83's optional decision
    explicitly superseded). Pin both halves: button gone, Build DNA
    locked until identity exists."""
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "onboarding.html").read_text()

    assert "skipIdentity" not in src, (
        "Issue 100 superseded Issue 83's optional intake — the "
        "'Skip for now' button + skipIdentity() function must be gone."
    )
    # Build DNA button starts disabled (gated on identity-exists)
    assert 'id="build-dna-btn"' in src
    # The disabled attribute must be present near build-dna-btn — search
    # the snippet to be precise about it being the initial state.
    btn_idx = src.find('id="build-dna-btn"')
    btn_snippet = src[btn_idx : btn_idx + 200]
    assert "disabled" in btn_snippet, (
        "Build DNA button must start disabled — Issue 100 gates step 4 "
        "on step 3 completion. _enableDnaBuild flips the lock after "
        "identity is saved (or already on file)."
    )
    # The gating helpers must exist
    assert "_enableDnaBuild" in src and "_lockDnaBuild" in src and "_checkIdentityExists" in src, (
        "Issue 100 gating helpers (_checkIdentityExists, _enableDnaBuild, "
        "_lockDnaBuild) must be defined."
    )


def test_all_templates_use_design_tokens():
    """Issue 99 Phase B (full rollout): every remaining static template must link
    the shared `_design-tokens.css` and consume at least one `--color-*`
    semantic token.

    Issue 226: legacy HTML templates (index, onboarding, insights, profile,
    review, pricing, walkthrough, analysis, login) have been retired. This test
    now only covers the retained legal pages (tos.html, privacy.html)."""
    import pathlib

    static_dir = pathlib.Path(__file__).parent.parent / "static"
    # Only tos.html, privacy.html, and accessibility.html remain after Issue 226 retirement.
    # accessibility.html added by Issue 301.
    templates = [
        "tos.html",
        "privacy.html",
        "accessibility.html",
    ]
    for name in templates:
        src = (static_dir / name).read_text()
        assert 'href="/static/_design-tokens.css"' in src, (
            f"{name} must link /static/_design-tokens.css — the shared "
            f"design system. (Issue 99 Phase B retrofit.)"
        )
        # Some pages use only --color-text, others --color-bg, etc. — assert
        # the prefix appears at all rather than enumerating every var.
        assert "var(--color-" in src, (
            f"{name} must consume at least one --color-* semantic token "
            f"from _design-tokens.css. Raw hex values inline are the "
            f"pre-Phase-B pattern this test guards against."
        )


@pytest.mark.skip(reason="Issue 226: static/pricing.html retired — React SPA is canonical.")
def test_pricing_page_uses_design_tokens():
    """Issue 99 Phase A: pricing.html is the proof retrofit. It must
    consume the shared _design-tokens.css (not redefine its own palette
    inline as the Wave 7 stopgap did). Each subsequent template that
    retrofits onto the design system inherits this same assertion.
    """
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "pricing.html").read_text()

    assert 'href="/static/style.css"' not in src, (
        "pricing.html must not link the never-existed /static/style.css. "
        "(Wave 7 dropped this link; Issue 99 Phase A replaces it with "
        "the canonical /static/_design-tokens.css.)"
    )
    assert 'href="/static/_design-tokens.css"' in src, (
        "pricing.html must link /static/_design-tokens.css — the shared "
        "design system. (Issue 99 Phase A proof retrofit.)"
    )
    # The page-specific styles must consume the new semantic token names,
    # not redefine inline palette vars under the old --bg / --surface /
    # --accent names (those were the Wave 7 stopgap).
    assert "var(--color-bg)" in src or "var(--color-surface)" in src, (
        "pricing.html must consume the new --color-* tokens from "
        "_design-tokens.css instead of the Wave-7 inline --bg / "
        "--surface palette."
    )


@pytest.mark.skip(reason="Issue 226: static/index.html retired — React SPA is canonical.")
def test_dashboard_registers_in_flight_ingests_with_active_tasks():
    """Wave 6 Fix D: the dashboard's loadVideos() must call into
    window.activeTasks.registerTask for any video in pending/running
    ingest state, so the floating activity panel (Wave-5 Fix 3) surfaces
    upload-pipeline progress on the dashboard. Without this wiring the
    panel was hidden 100% of the time on /static/index.html — a creator
    who uploaded mid-session and navigated to the dashboard saw no
    indication that work was in flight, despite the worker chain
    actively emitting to task:{video_id}:events.
    """
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "index.html").read_text()

    # The registrar function must exist + be called from loadVideos.
    assert "_registerInFlightIngests" in src, (
        "index.html must define _registerInFlightIngests so loadVideos can "
        "surface upload-pipeline streams in the Wave-5 activity panel."
    )
    # The task_id key matches the upload chain's stream key
    # (worker emits to task:{video_id}:events keyed by video.id).
    assert "task_id: v.id" in src, (
        "_registerInFlightIngests must use v.id as task_id — that's the "
        "stream key the upload chain emits to (Issue 92 / Wave-3 Fix E)."
    )
    # stream_url must point at the SSE endpoint for this task.
    assert "stream_url: `/tasks/${v.id}/events`" in src, (
        "_registerInFlightIngests must build stream_url as "
        "/tasks/{video_id}/events to subscribe to the upload pipeline."
    )
    # The 'upload_pipeline' kind is the canonical entry from the
    # activeTasks.js entry-shape contract (see static/activeTasks.js).
    assert "kind: 'upload_pipeline'" in src, (
        "Use the canonical 'upload_pipeline' kind from the activeTasks.js "
        "entry-shape contract — same vocabulary as catalog_sync / dna_build / "
        "improvement_brief on the other surfaces."
    )


@pytest.mark.skip(
    reason="Issue 226: static/index.html, insights.html, profile.html, review.html "
    "all retired — React SPA is canonical."
)
def test_authenticated_templates_link_to_pricing_in_nav():
    """Wave 6 Fix B: index/insights/profile/review must expose Pricing in the
    main nav so creators can reach the billing surface. Pricing.html is fully
    wired to /billing/balance and /billing/checkout, but pre-Wave-6 it had
    zero entry points — minutes couldn't be bought without typing the URL.

    Onboarding.html is intentionally excluded — its minimal nav (brand +
    logout only) is the canonical focused-single-task pattern; cluttering
    it with Pricing would degrade the setup flow.
    """
    import pathlib

    static_dir = pathlib.Path(__file__).parent.parent / "static"
    for name in ("index.html", "insights.html", "profile.html", "review.html"):
        src = (static_dir / name).read_text()
        # The nav anchor must be present — using `> Pricing<` ensures we
        # match the anchor text, not a stray Pricing mention in JS.
        assert '"/static/pricing.html">Pricing<' in src, (
            f"Wave-6 Fix B: {name} nav must include a Pricing link to "
            f"/static/pricing.html — otherwise billing is unreachable from "
            f"the main app surface."
        )


def test_every_retained_template_has_legal_footer():
    """Wave 6 Fix B: retained static templates (tos.html, privacy.html) must
    link to both /static/tos.html and /static/privacy.html in a footer.

    Issue 226: legacy authenticated templates retired. Only tos.html and
    privacy.html are retained. They are the TOS/privacy pages themselves —
    they must still cross-link each other (industry-standard SaaS pattern).
    """
    import pathlib

    static_dir = pathlib.Path(__file__).parent.parent / "static"
    # Only the three retained legal pages remain after Issue 226 + Issue 301.
    # accessibility.html added by Issue 301.
    templates = [
        "tos.html",
        "privacy.html",
        "accessibility.html",
    ]
    for name in templates:
        src = (static_dir / name).read_text()
        assert "<footer" in src, (
            f"Wave-6 Fix B: {name} must include a <footer> element "
            f"(industry-standard SaaS pattern for legal links)."
        )
        assert "/static/tos.html" in src, (
            f"Wave-6 Fix B: {name} footer must link to /static/tos.html — "
            f"required pre-launch surface (CLAUDE.md line 174)."
        )
        assert "/static/privacy.html" in src, (
            f"Wave-6 Fix B: {name} footer must link to /static/privacy.html — "
            f"required pre-launch surface + Google OAuth verification gate "
            f"(Issue 29)."
        )


@pytest.mark.skip(reason="Issue 226: static/profile.html retired — React SPA is canonical.")
def test_profile_page_exposes_api_keys_section():
    """Issue 95 frontend — profile.html surfaces the API-key management
    card for the OBS companion app. Pins:
      (a) section + form + list container exist;
      (b) JS wires the three backend endpoints (list/create/revoke);
      (c) the one-time-reveal modal is present with the canonical
          security copy (industry-standard GitHub/OpenAI/Anthropic wording);
      (d) the revoke confirm modal includes the destructive-action
          warning (industry-standard GitHub/Stripe wording);
      (e) the masked-prefix list rendering uses the mono data register
          (Issue 99 Phase C convention for IDs/tokens).
    Without these pins a future "let me simplify this" PR could silently
    regress the one-time-reveal pattern or the revoke confirmation —
    both of which are load-bearing for key security and for not letting
    a creator self-lock their OBS upload pipeline.
    """
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "profile.html").read_text()

    # (a) section + form + list container
    assert 'class="api-keys-section"' in src, (
        "profile.html must include the API-keys section (Issue 95 frontend AC)."
    )
    assert 'id="api-keys-create-form"' in src
    assert 'id="api-keys-list-container"' in src
    assert 'id="api-key-name"' in src
    assert 'placeholder="OBS MacBook"' in src, (
        "Name field should use a concrete OBS-device placeholder — the "
        "convention surfaced by industry research (Stripe/GitHub/Linear)."
    )

    # (b) JS wires all three backend endpoints
    assert "'/creators/me/api-keys'" in src, (
        "profile.html JS must call GET/POST /creators/me/api-keys."
    )
    assert "method: 'POST'" in src and "method: 'DELETE'" in src, (
        "profile.html JS must call POST (create) + DELETE (revoke) on /creators/me/api-keys."
    )

    # (c) one-time reveal modal + canonical security copy
    assert 'id="reveal-modal"' in src, (
        "Reveal modal must exist — industry-standard one-time reveal "
        "pattern (Stripe/GitHub/Linear). Inline reveal is rejected: "
        "too easy to dismiss accidentally."
    )
    assert "won't be able to see it again" in src, (
        "Reveal modal must include the canonical 'won't be able to see "
        "it again' warning (GitHub/OpenAI/Anthropic phrasing)."
    )
    assert 'id="reveal-copy-btn"' in src, (
        "Reveal modal must include a Copy button next to the key input."
    )

    # (d) revoke confirm modal + destructive-action wording
    assert 'id="revoke-modal"' in src, (
        "Revoke must be confirmed via a modal (industry standard — no "
        "major product allows single-click revoke). Without this, a "
        "creator can accidentally self-lock the OBS upload pipeline."
    )
    assert "This cannot be undone" in src, (
        "Revoke modal must warn that the action cannot be undone (GitHub canonical phrasing)."
    )
    assert "stop working immediately" in src, (
        "Revoke modal must make the immediacy explicit — applications "
        "stop working the moment we soft-delete the key."
    )

    # (e) masked-prefix display in the mono data register
    assert "api-key-prefix" in src, (
        "Listed keys must be rendered as masked prefix (ack_xxxxxxxx••...)."
    )
    assert "var(--font-mono)" in src, (
        "Key prefix should be rendered in the mono data register (Issue 99 "
        "Phase C convention — IDs/tokens use JetBrains Mono with tnum)."
    )

    # The companion-app context line — without this users have no idea
    # what these keys are for.
    assert "OBS companion app" in src or "companion app" in src, (
        "Section subhead must explain these keys are for the OBS companion "
        "app — otherwise the surface is meaningless to first-time visitors."
    )


def test_list_videos_excludes_catalog_only_rows(client):
    """The SELECT must filter out catalog-only rows via `Video.origin != catalog`
    (Issue 139 — replaced the old `source_uri IS NOT NULL` heuristic that wrongly
    hid linked videos). Verified by introspecting the SQLAlchemy statement.
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
    assert "origin != 'catalog'" in compiled or "origin <> 'catalog'" in compiled, (
        "list_videos must filter Video.origin != catalog so catalog-only "
        "(DNA-reference) rows don't pollute the dashboard, while linked videos "
        "still appear. (Issue 139, supersedes Issue 90's source_uri heuristic)"
    )


# ── Issue 104: every @limiter.limit in routers/*.py uses key_func=creator_key ─


def test_all_router_limit_decorators_use_creator_key():
    """Static-grep guard: every @limiter.limit(...) decorator in routers/*.py
    must include either key_func=creator_key (per-creator bucketing — the
    default for authenticated routes per Issue 104) OR
    key_func=get_remote_address (per-IP bucketing — for routes that legitimately
    have no session, e.g. /billing/webhook from Stripe per Issue 110).

    A bare `@limiter.limit(...)` with no key_func silently regresses to the
    slowapi default (also IP), which is correct for /webhook by accident but
    wrong for any authenticated route — so we require the kwarg to be EXPLICIT.

    This prevents a future 'quick add an endpoint' PR from accidentally
    omitting the kwarg and silently regressing on authenticated routes.
    (Issue 104, expanded by Issue 110)
    """
    import pathlib
    import re

    routers_dir = pathlib.Path(__file__).parent.parent / "routers"
    limit_re = re.compile(r"@limiter\.limit\(")
    keyfunc_re = re.compile(r"key_func\s*=\s*(creator_key|get_remote_address)")

    violations: list[str] = []
    for py_file in sorted(routers_dir.glob("*.py")):
        src = py_file.read_text()
        lines = src.splitlines()
        for lineno, line in enumerate(lines, start=1):
            if limit_re.search(line):
                snippet = line
                if lineno < len(lines):
                    snippet += "\n" + lines[lineno]
                if not keyfunc_re.search(snippet):
                    violations.append(f"{py_file.name}:{lineno}: {line.strip()!r}")

    assert not violations, (
        "The following @limiter.limit decorators are missing an explicit "
        "key_func= (must be creator_key for authenticated routes, or "
        "get_remote_address for unauthenticated routes like /billing/webhook):\n"
        + "\n".join(f"  {v}" for v in violations)
    )


# ── Issues 113–119: new UI surfaces ──────────────────────────────────────────


@pytest.mark.skip(
    reason="Issue 226: static/index.html, profile.html, review.html, insights.html "
    "retired — React SPA is canonical."
)
def test_nav_balance_and_help_in_all_main_pages():
    """Issue 113: every main authenticated page must expose:
    - id="nav-balance" for the minutes-remaining chip
    - a link to /static/walkthrough.html with class="nav-help" for the ? button
    """
    import pathlib

    static_dir = pathlib.Path(__file__).parent.parent / "static"
    for name in ("index.html", "profile.html", "review.html", "insights.html"):
        src = (static_dir / name).read_text()
        assert 'id="nav-balance"' in src, (
            f'Issue 113: {name} must include id="nav-balance" in the nav '
            f"for the minutes-remaining display."
        )
        assert "/static/walkthrough.html" in src and "nav-help" in src, (
            f"Issue 113: {name} must include a .nav-help link to walkthrough.html."
        )


@pytest.mark.skip(reason="Issue 226: static/profile.html retired — React SPA is canonical.")
def test_profile_dna_section_is_collapsible():
    """Issue 114: the Creator DNA section must be wrapped in a <details> element
    so it doesn't dominate the profile page by default."""
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "profile.html").read_text()
    assert "<details" in src and 'id="dna-section"' in src, (
        "Issue 114: profile.html DNA section must use a <details> element."
    )
    assert 'id="sync-chip"' in src, (
        "Issue 114: profile.html must include the sync-chip for DNA sync status."
    )
    assert "Synced with DNA" in src or "Not synced with DNA" in src, (
        "Issue 114: sync chip must show 'Synced with DNA' / 'Not synced with DNA' labels."
    )


@pytest.mark.skip(reason="Issue 226: static/profile.html retired — React SPA is canonical.")
def test_profile_rebuild_wires_streaming():
    """Issue 116: profile.html must load progressStream.js and wire rebuildDna()
    to subscribe to the SSE task stream from the build_dna endpoint."""
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "profile.html").read_text()
    assert "/static/progressStream.js" in src, (
        "Issue 116: profile.html must load progressStream.js."
    )
    assert "subscribeToTaskStream" in src, (
        "Issue 116: rebuildDna() must call subscribeToTaskStream to show live progress."
    )
    assert 'id="rebuild-stream"' in src, (
        "Issue 116: profile.html must include the rebuild stream output element."
    )


@pytest.mark.skip(reason="Issue 226: static/index.html retired — React SPA is canonical.")
def test_dashboard_has_analytics_panel_with_period_select():
    """Issue 115: the dashboard must show a YouTube Analytics panel with a
    period dropdown (7d / 28d / 90d / all) fetching /creators/me/insights/analytics."""
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "index.html").read_text()
    assert "/creators/me/insights/analytics" in src, (
        "Issue 115: index.html must fetch /creators/me/insights/analytics."
    )
    assert 'id="period-select"' in src, (
        "Issue 115: dashboard must include a period <select> for the analytics panel."
    )
    assert 'id="analytics-grid"' in src, (
        "Issue 115: dashboard must include analytics-grid for the metrics display."
    )


@pytest.mark.skip(reason="Issue 226: static/review.html retired — React SPA is canonical.")
def test_review_page_has_structured_feedback_panel():
    """Issue 118: review.html must include the multi-select structured feedback
    panel for approve and deny actions."""
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "review.html").read_text()
    assert 'id="feedback-panel"' in src, (
        "Issue 118: review.html must include the structured feedback panel."
    )
    assert "openFeedbackPanel" in src, "Issue 118: Keep/Drop buttons must open the feedback panel."
    assert "submitTaggedFeedback" in src, (
        "Issue 118: feedback must be submitted with tags via submitTaggedFeedback()."
    )
    assert "feedback_tags" in src, "Issue 118: feedback payload must include feedback_tags field."


@pytest.mark.skip(reason="Issue 226: static/review.html retired — React SPA is canonical.")
def test_review_page_has_style_picker():
    """Issue 119: review.html must include the clip style picker with subtitle
    and background controls, and an applyStyle() function."""
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "review.html").read_text()
    assert 'id="style-subtitle"' in src, (
        "Issue 119: review.html must include subtitle style selector."
    )
    assert 'id="style-background"' in src, (
        "Issue 119: review.html must include background fill selector."
    )
    assert "applyStyle" in src, "Issue 119: review.html must include applyStyle() function."


@pytest.mark.skip(reason="Issue 226: static/insights.html retired — React SPA is canonical.")
def test_insights_page_has_ai_analysis_and_saved_panels():
    """Issue 117: insights.html must support per-performer AI analysis
    (analyze button + /analyze-performer endpoint) and a saved insights panel."""
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "insights.html").read_text()
    assert "analyzePerformer" in src, (
        "Issue 117: insights.html must have analyzePerformer() function."
    )
    assert "/creators/me/insights/analyze-performer" in src, (
        "Issue 117: must POST to /creators/me/insights/analyze-performer."
    )
    assert 'id="saved-panel"' in src, "Issue 117: must include saved insights panel."
    assert "saveInsight" in src, "Issue 117: must have saveInsight() for bookmarking analyses."


# ── Issue 136 — dark editor mode + marketing hero ────────────────────────


@pytest.mark.skip(reason="Issue 226: static/review.html retired — React SPA is canonical.")
def test_issue_136_review_html_uses_editor_shell_and_dark_tokens():
    """review.html opts into editor mode + uses ONLY --editor-* tokens —
    no hardcoded hex in the new layout markup (Issue 136 acceptance)."""
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "review.html").read_text()
    assert '<link rel="stylesheet" href="/static/editor-layout.css">' in src
    # Issue 137 added `app-page` alongside `editor-page` on <body> for the
    # shared aurora/glass shell, so the class attribute is now a list. The
    # load-bearing assertion is that `editor-page` is still in the class list.
    assert (
        'class="editor-page"' in src
        or 'class="editor-page ' in src
        or 'class="editor-page\t' in src
        or "editor-page app-page" in src
    ), "review.html <body> must carry .editor-page to opt into dark mode."
    assert 'class="editor-shell"' in src, "Three-pane shell wrapper must be present."
    assert 'class="editor-transcript"' in src, "Always-visible transcript pane must be present."
    assert 'class="editor-tools"' in src, "Icon-strip nav must be present."
    # Tool drawer triggers — every Issue-119/133/134/135 panel must be a tool target.
    for tool in ("style", "clean", "why", "feedback"):
        assert f'data-tool-trigger="{tool}"' in src, (
            f"review.html must expose a tool trigger for {tool}"
        )
        assert f'data-tool="{tool}"' in src, f"review.html must declare a drawer panel for {tool}"
    # Existing IDs from prior issues survived the restructure.
    for css_id in (
        "clip-player",
        "clip-meta",
        "ed-words",
        "ed-status",
        "style-subtitle",
        "style-background",
        "clean-warning",
    ):
        assert f'id="{css_id}"' in src, (
            f"Issue 136 restructure must preserve #{css_id} for existing handlers."
        )


@pytest.mark.skip(reason="Issue 226: static/index.html retired — React SPA is canonical.")
def test_issue_136_index_html_pre_auth_hero_block():
    """Issue 136: index.html carries a hero block + opts into anonymous
    rendering so the pre-auth landing shows up for logged-out visitors."""
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "index.html").read_text()
    assert '<link rel="stylesheet" href="/static/hero.css">' in src
    assert "data-allow-anonymous" in src, (
        "<body> must carry data-allow-anonymous to opt into hero gate."
    )
    assert 'class="hero"' in src, "Hero section must exist in the template."
    assert 'id="hero-url"' in src, "Hero must include a YouTube URL input."
    assert "heroSubmit" in src, "Hero must wire the URL form to a submit handler."
    # Honesty constraint — CLAUDE.md requires the disclaimer to appear on every
    # interface; in hero mode the existing nav-bar disclaimer is hidden, so a
    # second hero-scoped copy must exist.
    assert "predicts fit" in src and "not promise" in src.lower(), (
        "Hero must surface the honesty disclaimer (no virality promise)."
    )
    # YouTube URL regex is client-side; server validates again.
    assert "youtube.com" in src and "youtu.be" in src, (
        "Hero validation must accept both youtube.com and youtu.be."
    )
    # Existing dashboard surface is still in the template (no regression
    # when the user IS authenticated).
    assert 'id="video-tbody"' in src, "Dashboard table must survive the hero addition."


# ── Cache-busting middleware (post-Issue-136 follow-up) ──────────────────


def test_static_cachebust_middleware_appends_version_to_css(client):
    """`StaticCacheBustMiddleware` rewrites text/html responses so every
    /static/X.css and /static/X.js reference picks up ?v=<STATIC_VERSION>.
    Each deploy bumps STATIC_VERSION (built from the git SHA) so Cloudflare
    treats the asset URL as new and stops serving the old cached copy.

    Issue 226/148: only the retained legal pages remain under /static — each
    must serve 200 and reference the cache-busted /static/_design-tokens.css.
    """
    from config import settings

    expected_suffix = f"?v={settings.STATIC_VERSION}"
    for path in ("/static/tos.html", "/static/privacy.html", "/static/accessibility.html"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert f"/static/_design-tokens.css{expected_suffix}" in resp.text, (
            f"{path}: CSS link must carry the cache-busting query string."
        )


def test_static_cachebust_middleware_skips_non_html(client):
    """The middleware must NOT rewrite CSS/JS bodies — only text/html
    responses. The CSS content is the source of truth and tests + Layer 0
    inspect it character-for-character."""
    resp = client.get("/static/_design-tokens.css")
    assert resp.status_code == 200
    # CSS body should be the raw stylesheet content, not rewritten HTML.
    assert "?v=" not in resp.text.split("\n", 1)[0], (
        "First line of _design-tokens.css should not be touched by the HTML rewriter."
    )


def test_static_cachebust_middleware_is_idempotent_on_existing_query(client):
    """If a future template hard-codes its own ?v=, the middleware must
    leave it alone — the regex matches only paths with no query string."""
    # Inject a request to a known HTML; we can't easily forge "existing ?v="
    # from a real template, but we can assert the regex shape directly.
    from main import _STATIC_CACHEBUST_RE, _rewrite_static

    raw = b'<link href="/static/foo.css?v=manual"><link href="/static/bar.css">'
    out = _rewrite_static(raw, "abc123")
    assert b"/static/foo.css?v=manual" in out, "Existing ?v= must be preserved."
    assert b"/static/bar.css?v=abc123" in out, "Bare path must pick up the new ?v=."
    # Sanity: regex itself ignores ?-containing paths.
    assert _STATIC_CACHEBUST_RE.search(b'href="/static/foo.css?v=manual"') is None


def test_static_cachebust_middleware_sets_no_store_on_html(client):
    """HTML responses must carry Cache-Control: no-store so browsers never
    cache them via ETag/Last-Modified.  Stale browser-cached HTML retains old
    ?v= strings that point at CDN-cached old CSS — the only safe fix is to
    prevent browser caching of HTML entirely.

    Issue 226: /static/index.html and /static/insights.html retired.
    Now verified against retained legal pages (tos.html, privacy.html).
    Note: / returns 404 when SPA is not built (Issue 226 — legacy index retired).
    """
    for path in ("/static/tos.html", "/static/privacy.html", "/static/accessibility.html"):
        resp = client.get(path)
        assert resp.status_code == 200
        cc = resp.headers.get("cache-control", "")
        assert "no-store" in cc, f"{path}: Cache-Control must include no-store but got {cc!r}"


def test_static_cachebust_middleware_strips_etag_from_html(client):
    """ETag and Last-Modified must be absent from rewritten HTML responses.
    Keeping them allows browsers to send If-None-Match and receive a 304 that
    bypasses the middleware, leaving the browser stuck on stale HTML.

    Issue 226: / returns 404 when SPA not built (legacy index.html retired).
    Verify against retained tos.html instead.
    """
    resp = client.get("/static/tos.html")
    assert resp.status_code == 200
    assert "etag" not in resp.headers, (
        "ETag must be stripped from HTML responses to prevent conditional-GET bypass."
    )
    assert "last-modified" not in resp.headers, (
        "Last-Modified must be stripped from HTML responses."
    )


def test_static_cachebust_middleware_preserves_etag_on_css(client):
    """ETag/Last-Modified must be kept on CSS/JS responses — the middleware
    must not touch non-HTML assets."""
    resp = client.get("/static/_design-tokens.css")
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("text/css")
    # StaticFiles sets ETag on files; it must still be present after the middleware.
    assert "etag" in resp.headers, (
        "CSS ETag must be preserved — browser caching of CSS is desirable."
    )


# ── Issue 137 — project-wide UI overhaul + horizontal-overflow guard ─────


_ISSUE_137_AUTHENTICATED_PAGES = (
    "index.html",
    "insights.html",
    "profile.html",
    "onboarding.html",
    "analysis.html",
    "pricing.html",
    "walkthrough.html",
    "review.html",
)


@pytest.mark.skip(
    reason="Issue 226: all legacy authenticated HTML templates retired — "
    "React SPA (frontend/src/) is the canonical surface."
)
def test_issue_137_authenticated_pages_link_page_shell_and_opt_in():
    """Every authenticated template must link page-shell.css AND carry
    `app-page` on <body>. Without both, the page won't pick up the unified
    aurora + soft-card aesthetic the user asked for."""
    import pathlib

    static_dir = pathlib.Path(__file__).parent.parent / "static"
    for page in _ISSUE_137_AUTHENTICATED_PAGES:
        src = (static_dir / page).read_text()
        assert "/static/page-shell.css" in src, (
            f"{page}: must <link> /static/page-shell.css for the shared shell."
        )
        # Body must carry `app-page` (review.html keeps editor-page too;
        # the class list ordering is not pinned, just presence).
        assert 'class="app-page"' in src or 'class="editor-page app-page"' in src, (
            f"{page}: <body> must include the app-page class to opt into the page-shell rules."
        )


@pytest.mark.skip(reason="Issue 226: static/index.html retired — React SPA is canonical.")
def test_issue_137_index_video_table_wrapped_for_overflow():
    """Issue 137: the dashboard's video table was the load-bearing source of
    horizontal scroll (4 columns + 2-button action cell). It must be wrapped
    in .table-wrap so any table overflow lives inside the wrapper, never on
    the page. The action cell must use .action-row so buttons flex-wrap on
    narrow viewports."""
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "index.html").read_text()
    assert 'class="table-wrap"' in src, (
        "index.html: the .video-table must be wrapped in a .table-wrap so "
        "horizontal scroll is scoped to the data, not the page."
    )
    # Action-row class must be present in the JS-rendered cell so multiple
    # buttons (Generate + Titles / X clips + Titles) wrap on narrow viewports.
    assert "action-row" in src, (
        "index.html: action button rows must use .action-row so buttons "
        "wrap on narrow viewports instead of forcing horizontal scroll."
    )


def test_issue_137_decisions_md_logs_issue99_reversal():
    """Issue 137 explicitly reverses the Issue-99 'sharp-utility-only on
    data pages' split (and the Issue-136 redirect's 'don't touch data
    pages' note). Per CLAUDE.md, every deviation from a documented
    decision must appear in docs/DECISIONS.md with what / why / source /
    date — pin the entry so it can't drift out."""
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "docs" / "DECISIONS.md").read_text()
    assert "Issue 137" in src, (
        "docs/DECISIONS.md must carry an Issue 137 entry documenting the "
        "reversal of the Issue-99 data-vs-marketing visual split."
    )


@pytest.mark.skip(
    reason="Issue 226: static/index.html retired — React SPA is canonical. "
    "Cache-bust middleware behavior covered by test_static_cachebust_middleware_appends_version_to_css."
)
def test_issue_137_page_shell_loaded_via_html_route_and_cachebust_applied(client):
    """The page-shell stylesheet must actually load on the rendered HTML —
    not just exist on disk — and the cache-bust middleware must append a
    ?v=<sha> to its <link>, the same way it does for the other shared
    stylesheets. Regression-pins the rewriter against an unintentional
    skip rule."""
    body = client.get("/static/index.html").text
    assert "/static/page-shell.css" in body, (
        "page-shell.css must appear in the rendered index.html so the "
        "shared shell actually loads in the browser."
    )
    # Cache-bust query string format from middleware: ?v=sha-<commit>
    assert "/static/page-shell.css?v=" in body, (
        "page-shell.css must carry a cache-bust ?v=<sha> query string so "
        "deploys force a fresh fetch."
    )


# ── Issue 138 — SEV1 #1/#2: XSS sinks + shared escapeHtml + dead-id fix ────────


@pytest.mark.skip(
    reason="Issue 226: static/index.html retired — XSS risk eliminated structurally "
    "(Issue 226). The React SPA uses JSX with encoding by default."
)
def test_index_escapes_third_party_video_title():
    """SEV1 #1: YouTube titles (third-party via /videos/link) must be escaped
    before reaching tbody.innerHTML — otherwise a hostile title is stored XSS
    on the dashboard."""
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "index.html").read_text()
    assert '<script src="/static/util.js"></script>' in src
    assert "escapeHtml(v.title" in src
    assert "escapeHtml(v.youtube_video_id)" in src
    # The raw unescaped sink must be gone.
    assert "${v.title || '—'}" not in src


@pytest.mark.skip(
    reason="Issue 226: static/insights.html retired — XSS risk eliminated structurally."
)
def test_insights_escapes_llm_and_persisted_content():
    """SEV1 #1: LLM analysis output (reflected) and persisted saved-insights
    (stored XSS on every page load) must be escaped before innerHTML."""
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "insights.html").read_text()
    assert '<script src="/static/util.js"></script>' in src
    assert "escapeHtml(data.content)" in src
    assert "escapeHtml(ins.title" in src
    assert "escapeHtml(ins.content)" in src
    # Raw stored-XSS sinks gone.
    assert "${ins.content}" not in src
    assert "${data.content} <button" not in src


@pytest.mark.skip(
    reason="Issue 226: static/analysis.html retired — XSS risk eliminated structurally."
)
def test_analysis_escape_includes_apostrophe_via_shared_util():
    """SEV1 #1: analysis.html must use the shared escaper (which escapes the
    apostrophe), not the old local `_esc` that missed it."""
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "analysis.html").read_text()
    assert '<script src="/static/util.js"></script>' in src
    assert "_esc = window.escapeHtml" in src


@pytest.mark.skip(
    reason="Issue 226: static/analysis.html retired — XSS risk eliminated structurally."
)
def test_analysis_ingest_cta_uses_urlraw_not_dead_id():
    """SEV1 #2: the 'Ingest this video' CTA built the URL from a non-existent
    element id ('youtube_url'; the real input is 'url-input'), throwing a
    TypeError that killed the entire non-catalog analysis path. It must build
    from the in-scope urlRaw instead."""
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "analysis.html").read_text()
    assert "getElementById('youtube_url')" not in src, (
        "Dead element id must be gone — it throws and kills the analysis path."
    )
    assert "'https://www.youtube.com/watch?v=' + urlRaw" in src


def test_eyebrow_label_tracking_is_tokenized():
    """Issue 147 — the --tracking-eyebrow token must exist in _design-tokens.css.

    Issue 226: legacy HTML templates retired. Per-template assertion removed.
    Token existence is still pinned here; React SPA enforces usage via Tailwind/CSS.
    """
    import pathlib

    static = pathlib.Path(__file__).parent.parent / "static"
    tokens = (static / "_design-tokens.css").read_text()
    assert "--tracking-eyebrow:" in tokens, (
        "_design-tokens.css must define --tracking-eyebrow (one canonical "
        "label letter-spacing) — Issue 147."
    )


@pytest.mark.skip(
    reason="Issue 226: static/analysis.html and pricing.html retired — "
    "React SPA is canonical. Type scale enforcement happens in frontend/src/."
)
def test_page_title_scale_is_unified():
    """Issue 148 — page-opener titles had drifted across the type scale
    (analysis at --text-lg, pricing at --text-2xl, others at --text-xl), so
    the same page-title role rendered three different sizes. Pin that the two
    outliers were normalized to --text-xl and that no core page reintroduces a
    --text-2xl page title."""
    import pathlib

    static = pathlib.Path(__file__).parent.parent / "static"
    # analysis page h1 was --text-lg → --text-xl
    analysis = (static / "analysis.html").read_text()
    assert "font-size: var(--text-lg);  /* unified page-title" not in analysis
    # pricing hero h1 was --text-2xl → --text-xl
    pricing = (static / "pricing.html").read_text()
    assert "var(--text-2xl)" not in pricing, (
        "pricing.html page title must use the unified --text-xl scale (Issue 148)."
    )


# ── Issue 226: CI grep — dangerouslySetInnerHTML in frontend/src/ ─────────────


def test_react_spa_has_zero_dangerouslysetinnerhtml():
    """Issue 226 (OWASP LLM05:2025): the React SPA must have zero
    dangerouslySetInnerHTML usages. JSX encodes by default; any opt-in via
    dangerouslySetInnerHTML re-introduces a DOM XSS sink structurally equivalent
    to the innerHTML pattern that produced Issues 138 and 149 in the legacy UI.

    This CI grep runs on every test run to catch accidental regressions.
    """
    import pathlib

    frontend_src = pathlib.Path(__file__).parent.parent / "frontend" / "src"
    if not frontend_src.is_dir():
        # No frontend checkout in this environment — skip gracefully.
        return

    violations: list[str] = []
    for ts_file in sorted(frontend_src.rglob("*.tsx")) + sorted(frontend_src.rglob("*.ts")):
        content = ts_file.read_text()
        if "dangerouslySetInnerHTML" in content:
            # Find line numbers for clarity.
            for lineno, line in enumerate(content.splitlines(), start=1):
                if "dangerouslySetInnerHTML" in line:
                    violations.append(
                        f"{ts_file.relative_to(frontend_src)}:{lineno}: {line.strip()}"
                    )

    assert not violations, (
        "Issue 226 (OWASP LLM05:2025): zero dangerouslySetInnerHTML allowed in "
        "frontend/src/. Found:\n" + "\n".join(f"  {v}" for v in violations)
    )


# ── Issue 229: HTTP security-headers middleware ───────────────────────────────


def test_security_headers_present_on_every_response(client):
    """Issue 229 (OWASP Secure Headers Project): every response must carry the
    OWASP baseline security headers. Verified on two surfaces: a JSON API
    endpoint and a retained static HTML page.

    HSTS is only emitted in production (ENV=production); absence in development
    is correct behaviour, separately asserted in test_hsts_absent_in_development.
    """
    for path in ("/health", "/static/tos.html"):
        resp = client.get(path)
        assert resp.status_code in (200, 404)
        assert resp.headers.get("content-security-policy"), (
            f"{path}: Content-Security-Policy must be present (Issue 229)."
        )
        csp = resp.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in csp, (
            f"{path}: CSP must include frame-ancestors 'none' — clickjacking defence."
        )
        assert "default-src" in csp, f"{path}: CSP must include default-src directive."
        # 2026-07-20 assessment: the SPA @imports Google Fonts — without these
        # directives the browser blocks the stylesheet/woff2 and prod silently
        # falls back to system fonts (regression since Issue 229).
        assert "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com" in csp, (
            f"{path}: CSP style-src must allow the Google Fonts stylesheet + the "
            "retained static pages' inline <style> blocks."
        )
        assert "font-src 'self' https://fonts.gstatic.com" in csp, (
            f"{path}: CSP font-src must allow fonts.gstatic.com (woff2 files)."
        )
        assert resp.headers.get("x-frame-options") == "DENY", (
            f"{path}: X-Frame-Options must be DENY (defence-in-depth for legacy browsers)."
        )
        assert resp.headers.get("x-content-type-options") == "nosniff", (
            f"{path}: X-Content-Type-Options must be nosniff."
        )
        assert resp.headers.get("referrer-policy") == "no-referrer", (
            f"{path}: Referrer-Policy must be no-referrer."
        )


def test_hsts_absent_in_development(client):
    """Issue 229: HSTS must NOT be emitted in development (ENV=development).
    Emitting HSTS on a non-TLS dev host causes browsers to refuse HTTP
    connections to localhost — HSTS is gated on ENV='production'."""
    from config import settings

    assert settings.ENV != "production", (
        "This test assumes ENV=development. If running in production, skip."
    )
    resp = client.get("/health")
    assert "strict-transport-security" not in resp.headers, (
        "HSTS must be absent in development (ENV != 'production'). "
        "It is only emitted in production to avoid breaking non-TLS dev hosts."
    )


def test_hsts_emitted_in_production(client, monkeypatch):
    """Issue 229: Strict-Transport-Security must be present when ENV=production."""
    monkeypatch.setattr("config.settings.ENV", "production")
    resp = client.get("/health")
    hsts = resp.headers.get("strict-transport-security", "")
    assert "max-age=63072000" in hsts, (
        "HSTS max-age must be 63072000 (2 years) — OWASP Secure Headers recommendation."
    )
    assert "includeSubDomains" in hsts, (
        "HSTS must include includeSubDomains — protects all subdomains."
    )


def test_csp_contains_frame_ancestors_none(client):
    """Issue 229: frame-ancestors 'none' is the structural clickjacking defence.
    Previously absent — a CSP with this directive would have been the backstop
    in the two prior XSS incidents (Issues 138, 149).
    """
    resp = client.get("/health")
    csp = resp.headers.get("content-security-policy", "")
    assert "frame-ancestors 'none'" in csp, (
        "CSP must include frame-ancestors 'none' — prevents clickjacking of the "
        "OAuth flow and the SPA. OWASP Secure Headers Project 2025."
    )


# ── Issue 252: Privacy Policy GDPR Art. 13-14 / CCPA rewrite ─────────────────


def test_privacy_page_names_all_subprocessors(client):
    """Issue 252: privacy.html must name every sub-processor from SUBPROCESSORS.md
    so GDPR Art. 13(1)(e) (disclosure of recipients) is satisfied in the public
    privacy notice."""
    resp = client.get("/static/privacy.html")
    assert resp.status_code == 200
    text = resp.content.decode()
    for vendor in ("Anthropic", "Voyage AI", "Deepgram", "Cloudflare R2", "Stripe", "Google"):
        assert vendor in text, (
            f"privacy.html must name sub-processor '{vendor}' (Issue 252 — GDPR Art. 13 disclosure)."
        )


def test_privacy_page_has_ccpa_do_not_sell_statement(client):
    """Issue 252: privacy.html must include a structured CCPA notice-at-collection
    section with the explicit 'do not sell or share' statement."""
    resp = client.get("/static/privacy.html")
    assert resp.status_code == 200
    text = resp.content.decode()
    assert "do not sell or share" in text.lower(), (
        "privacy.html must include the CCPA 'do not sell or share' statement "
        "(Issue 252 — CCPA notice-at-collection requirement)."
    )
    assert "California" in text, (
        "privacy.html must include a California residents (CCPA) section (Issue 252)."
    )


def test_privacy_page_states_standard_contractual_clauses(client):
    """Issue 252: privacy.html must state the international transfer mechanism.
    All sub-processors are US-based; SCCs are the operative mechanism."""
    resp = client.get("/static/privacy.html")
    assert resp.status_code == 200
    text = resp.content.decode()
    assert "Standard Contractual Clauses" in text, (
        "privacy.html must state 'Standard Contractual Clauses' as the EEA→US "
        "transfer mechanism (Issue 252 — GDPR Art. 46(2)(c))."
    )


def test_privacy_page_demographics_aggregation_disclosure(client):
    """Issue 252: privacy.html must clarify that YouTube audience demographics are
    aggregated/anonymised by YouTube before delivery — no individual viewer PII stored."""
    resp = client.get("/static/privacy.html")
    assert resp.status_code == 200
    text = resp.content.decode()
    assert "aggregated" in text.lower(), (
        "privacy.html must disclose that audience demographics are aggregated "
        "(Issue 252 — audience data disclosure)."
    )
    assert "anonymised" in text.lower() or "anonymized" in text.lower(), (
        "privacy.html must clarify that demographics are anonymised (Issue 252)."
    )


def test_privacy_page_has_breach_contact(client):
    """Issue 252: privacy.html must include a breach contact reference."""
    resp = client.get("/static/privacy.html")
    assert resp.status_code == 200
    text = resp.content.decode()
    assert "breach" in text.lower(), (
        "privacy.html must reference data breach contact / notification (Issue 252)."
    )
    assert "reesepludwick@gmail.com" in text, (
        "privacy.html must include the breach contact email (Issue 252)."
    )


def test_privacy_page_has_cookies_clause(client):
    """Issue 252: privacy.html must have a cookies section disclosing strictly-necessary
    cookies only (session JWT + OAuth-state) with no consent banner required."""
    resp = client.get("/static/privacy.html")
    assert resp.status_code == 200
    text = resp.content.decode()
    assert "cookie" in text.lower(), (
        "privacy.html must include a Cookies section (Issue 252 — ePrivacy compliance)."
    )
    assert "strictly" in text.lower(), (
        "privacy.html cookies clause must state strictly-necessary cookies only (Issue 252)."
    )


# ── Issue 301: Accessibility Statement ───────────────────────────────────────


def test_accessibility_page_served(client):
    """Issue 301: GET /static/accessibility.html must return 200."""
    resp = client.get("/static/accessibility.html")
    assert resp.status_code == 200


def test_accessibility_page_has_required_clauses(client):
    """Issue 301: accessibility.html must contain the EAA/WCAG 2.1 required elements:
    conformance standard, conformance status, feedback mechanism."""
    resp = client.get("/static/accessibility.html")
    assert resp.status_code == 200
    text = resp.content.decode()
    assert "WCAG 2.1" in text, (
        "accessibility.html must cite WCAG 2.1 (Issue 301 — EAA / EN 301 549 requirement)."
    )
    assert "EN 301 549" in text, (
        "accessibility.html must reference EN 301 549 (Issue 301 — EAA harmonised standard)."
    )
    assert "mailto:" in text, (
        "accessibility.html must include a mailto: feedback mechanism (Issue 301 — EAA Annex V)."
    )


def test_footer_tsx_has_accessibility_link():
    """Issue 301: frontend/src/components/Footer.tsx must include an Accessibility link
    pointing to /static/accessibility.html."""
    import pathlib

    footer_path = (
        pathlib.Path(__file__).parent.parent / "frontend" / "src" / "components" / "Footer.tsx"
    )
    if not footer_path.exists():
        return  # frontend not checked out
    src = footer_path.read_text()
    assert "accessibility" in src.lower(), (
        "Footer.tsx must include an Accessibility link (Issue 301 — EAA footer requirement)."
    )
    assert "accessibility.html" in src, (
        "Footer.tsx must link to /static/accessibility.html (Issue 301)."
    )
