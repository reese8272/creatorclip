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
    extractor_call = re.search(r'extractYouTubeId\s*\(', src)
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
        "--color-bg":        "#0a0a0a",
        "--color-surface":   "#111111",
        "--color-border":    "#1f1f1f",
        "--color-text":      "#ededed",
        "--color-accent":    "#5e6ad2",  # Linear indigo
    }
    for var_name, expected_value in palette.items():
        assert f"{var_name}:" in src, (
            f"_design-tokens.css must define {var_name} — locked in the "
            f"Issue 99 design system."
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


def test_review_page_exposes_why_this_clip_panel():
    """Issue 94 — clip transparency. review.html must surface the
    Claude-authored reasoning + cited principle + score + timing
    structure for every clip via a 'Why this clip?' expander. Each
    clip in the queue already has principle/reasoning fields in the
    /clips response (ClipOut); this pin guards the UI consumption."""
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "review.html").read_text()

    assert 'id="why-clip"' in src and 'Why this clip?' in src, (
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
    assert 'const PANELS = 5;' in src, (
        "Panel count constant must stay at 5 — drives the keyboard-nav "
        "cap and the dots indicator."
    )
    # The required honesty disclaimer (CLAUDE.md North Star constraint).
    # Match on the substring that's invariant across line breaks.
    assert "promise virality" in src, (
        "walkthrough.html must include the honesty disclaimer "
        "(CLAUDE.md no-virality rule)."
    )
    # The completion flag — auth.js reads this to skip the walkthrough
    # on subsequent visits.
    assert 'creatorclip:walkthrough_seen' in src, (
        "walkthrough.html must set the localStorage flag on completion "
        "so auth.js's first-run gate doesn't re-redirect."
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
    assert 'creatorclip:walkthrough_seen' in src, (
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


def test_onboarding_intake_is_mandatory():
    """Issue 100 — intake step on onboarding.html is no longer skippable.
    The 'Skip for now' button was removed (Issue 83's optional decision
    explicitly superseded). Pin both halves: button gone, Build DNA
    locked until identity exists."""
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "onboarding.html").read_text()

    assert 'skipIdentity' not in src, (
        "Issue 100 superseded Issue 83's optional intake — the "
        "'Skip for now' button + skipIdentity() function must be gone."
    )
    # Build DNA button starts disabled (gated on identity-exists)
    assert 'id="build-dna-btn"' in src
    # The disabled attribute must be present near build-dna-btn — search
    # the snippet to be precise about it being the initial state.
    btn_idx = src.find('id="build-dna-btn"')
    btn_snippet = src[btn_idx : btn_idx + 200]
    assert 'disabled' in btn_snippet, (
        "Build DNA button must start disabled — Issue 100 gates step 4 "
        "on step 3 completion. _enableDnaBuild flips the lock after "
        "identity is saved (or already on file)."
    )
    # The gating helpers must exist
    assert '_enableDnaBuild' in src and '_lockDnaBuild' in src and \
           '_checkIdentityExists' in src, (
        "Issue 100 gating helpers (_checkIdentityExists, _enableDnaBuild, "
        "_lockDnaBuild) must be defined."
    )


def test_all_templates_use_design_tokens():
    """Issue 99 Phase B (full rollout): every static template must link
    the shared `_design-tokens.css` and consume at least one `--color-*`
    semantic token. Pinning per-template avoids a future "let me restyle
    this one page" PR silently regressing back to inline hex values.

    pricing.html is also covered by `test_pricing_page_uses_design_tokens`
    below (which additionally asserts the broken-Wave-7 link is gone)."""
    import pathlib

    static_dir = pathlib.Path(__file__).parent.parent / "static"
    templates = [
        "index.html",
        "onboarding.html",
        "insights.html",
        "profile.html",
        "review.html",
        "pricing.html",
        "tos.html",
        "privacy.html",
        "early-access.html",
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


def test_every_template_has_legal_footer():
    """Wave 6 Fix B: every static template — authenticated, marketing, AND
    legal — must link to both /static/tos.html and /static/privacy.html in a
    footer. TOS + Privacy must be reachable from every page, which is a
    documented Google OAuth app-verification gate (Issue 29) and the
    canonical SaaS pattern (Stripe, Linear, Vercel, Notion). Pre-Wave-6 the
    Privacy and TOS pages had zero inbound links from anywhere.
    """
    import pathlib

    static_dir = pathlib.Path(__file__).parent.parent / "static"
    templates = [
        "index.html",
        "onboarding.html",
        "insights.html",
        "profile.html",
        "review.html",
        "pricing.html",
        "tos.html",
        "privacy.html",
        "early-access.html",
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
        "profile.html JS must call POST (create) + DELETE (revoke) on "
        "/creators/me/api-keys."
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
        "Revoke modal must warn that the action cannot be undone "
        "(GitHub canonical phrasing)."
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
            f"Issue 113: {name} must include id=\"nav-balance\" in the nav "
            f"for the minutes-remaining display."
        )
        assert '/static/walkthrough.html' in src and 'nav-help' in src, (
            f"Issue 113: {name} must include a .nav-help link to walkthrough.html."
        )


def test_auth_js_populates_nav_elements():
    """Issue 113: auth.js must populate nav-user and nav-balance elements
    after successful auth — no per-page duplication needed."""
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "auth.js").read_text()
    assert "nav-balance" in src, (
        "auth.js must populate the nav-balance element after auth."
    )
    assert "/billing/balance" in src, (
        "auth.js must fetch /billing/balance to display remaining minutes."
    )


def test_profile_dna_section_is_collapsible():
    """Issue 114: the Creator DNA section must be wrapped in a <details> element
    so it doesn't dominate the profile page by default."""
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "profile.html").read_text()
    assert '<details' in src and 'id="dna-section"' in src, (
        "Issue 114: profile.html DNA section must use a <details> element."
    )
    assert 'id="sync-chip"' in src, (
        "Issue 114: profile.html must include the sync-chip for DNA sync status."
    )
    assert "Synced with DNA" in src or "Not synced with DNA" in src, (
        "Issue 114: sync chip must show 'Synced with DNA' / 'Not synced with DNA' labels."
    )


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


def test_review_page_has_structured_feedback_panel():
    """Issue 118: review.html must include the multi-select structured feedback
    panel for approve and deny actions."""
    import pathlib

    src = (pathlib.Path(__file__).parent.parent / "static" / "review.html").read_text()
    assert 'id="feedback-panel"' in src, (
        "Issue 118: review.html must include the structured feedback panel."
    )
    assert "openFeedbackPanel" in src, (
        "Issue 118: Keep/Drop buttons must open the feedback panel."
    )
    assert "submitTaggedFeedback" in src, (
        "Issue 118: feedback must be submitted with tags via submitTaggedFeedback()."
    )
    assert "feedback_tags" in src, (
        "Issue 118: feedback payload must include feedback_tags field."
    )


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
    assert "applyStyle" in src, (
        "Issue 119: review.html must include applyStyle() function."
    )


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
    assert 'id="saved-panel"' in src, (
        "Issue 117: must include saved insights panel."
    )
    assert "saveInsight" in src, (
        "Issue 117: must have saveInsight() for bookmarking analyses."
    )
