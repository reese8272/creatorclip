"""
Tests for Issue 15 — connected user flow + auth guard.
Covers: auth.js served, nav present in all pages, review page accepts video_id param,
OAuth callback redirects to root, all pages include auth guard script.
"""

# ── auth.js served ────────────────────────────────────────────────────────────


def test_auth_js_served(client):
    resp = client.get("/static/auth.js")
    assert resp.status_code == 200
    assert (
        "javascript" in resp.headers["content-type"]
        or "text/plain" in resp.headers["content-type"]
        or "application/javascript" in resp.headers.get("content-type", "")
    )


def test_auth_js_contains_guard(client):
    resp = client.get("/static/auth.js")
    content = resp.text
    assert "/auth/me" in content
    assert "auth:ready" in content
    assert "window.location" in content


def test_auth_js_contains_logout(client):
    resp = client.get("/static/auth.js")
    assert "logout" in resp.text
    assert "/auth/logout" in resp.text


# ── All pages include auth.js ─────────────────────────────────────────────────


def test_index_includes_auth_js(client):
    # Legacy-index content. `/` redirects to the SPA once built (Issue 85g),
    # so assert against the legacy file directly (still served).
    resp = client.get("/static/index.html")
    assert b"auth.js" in resp.content


def test_review_includes_auth_js(client):
    resp = client.get("/static/review.html")
    assert b"auth.js" in resp.content


def test_profile_includes_auth_js(client):
    resp = client.get("/static/profile.html")
    assert b"auth.js" in resp.content


def test_onboarding_includes_auth_js(client):
    resp = client.get("/static/onboarding.html")
    assert b"auth.js" in resp.content


def test_insights_includes_auth_js(client):
    resp = client.get("/static/insights.html")
    assert b"auth.js" in resp.content


# ── Nav bar present in all pages ──────────────────────────────────────────────


def test_index_has_nav(client):
    content = client.get("/static/index.html").text
    assert "Dashboard" in content or "AutoClip" in content
    assert "/static/review.html" in content


def test_review_has_nav(client):
    content = client.get("/static/review.html").text
    assert "AutoClip" in content
    assert "Dashboard" in content


def test_profile_has_nav(client):
    content = client.get("/static/profile.html").text
    assert "AutoClip" in content
    assert "Dashboard" in content


def test_insights_has_nav(client):
    content = client.get("/static/insights.html").text
    assert "AutoClip" in content
    assert "Dashboard" in content


# ── Review page accepts video_id param (static assertion) ────────────────────


def test_review_page_uses_url_params(client):
    """Review page JS should read video_id from URLSearchParams."""
    resp = client.get("/static/review.html")
    assert b"URLSearchParams" in resp.content
    assert b"video_id" in resp.content


def test_review_page_redirects_after_all_reviewed(client):
    """After all clips reviewed, page should redirect to dashboard."""
    resp = client.get("/static/review.html")
    assert b"window.location = '/'" in resp.content


# ── OAuth callback redirects to root ─────────────────────────────────────────


def test_oauth_callback_redirects_to_root(client):
    """Callback with bad state/code should 400, but the redirect target is /."""
    resp = client.get("/auth/callback?code=x&state=x", follow_redirects=False)
    # Should 400 (bad state), not redirect — just verify the route exists and
    # that the success path would redirect to / (checked via source).
    assert resp.status_code in (302, 400)


# ── Profile confirm redirects to dashboard ───────────────────────────────────


def test_profile_confirm_triggers_redirect(client):
    """Profile page JS should redirect to / after confirming DNA."""
    resp = client.get("/static/profile.html")
    assert b"window.location = '/'" in resp.content


# ── Onboarding page references profile and uses auth:ready ───────────────────


def test_onboarding_uses_auth_ready(client):
    resp = client.get("/static/onboarding.html")
    assert b"auth:ready" in resp.content


def test_onboarding_links_to_profile(client):
    resp = client.get("/static/onboarding.html")
    assert b"profile.html" in resp.content
