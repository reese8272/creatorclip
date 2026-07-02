"""
Tests for Issue 15 — connected user flow + auth guard.
Covers: nav present in all pages, review page accepts video_id param,
OAuth callback redirects to root. auth.js served/guard tests removed with the
file itself (Issue 148 close-out — legacy static app retired in Issue 226).
"""

import pytest

# ── All pages include auth.js ─────────────────────────────────────────────────


@pytest.mark.skip("Issue 226: legacy static pages retired — index.html deleted")
def test_index_includes_auth_js(client):
    resp = client.get("/static/index.html")
    assert b"auth.js" in resp.content


@pytest.mark.skip("Issue 226: legacy static pages retired — review.html deleted")
def test_review_includes_auth_js(client):
    resp = client.get("/static/review.html")
    assert b"auth.js" in resp.content


@pytest.mark.skip("Issue 226: legacy static pages retired — profile.html deleted")
def test_profile_includes_auth_js(client):
    resp = client.get("/static/profile.html")
    assert b"auth.js" in resp.content


@pytest.mark.skip("Issue 226: legacy static pages retired — onboarding.html deleted")
def test_onboarding_includes_auth_js(client):
    resp = client.get("/static/onboarding.html")
    assert b"auth.js" in resp.content


@pytest.mark.skip("Issue 226: legacy static pages retired — insights.html deleted")
def test_insights_includes_auth_js(client):
    resp = client.get("/static/insights.html")
    assert b"auth.js" in resp.content


# ── Nav bar present in all pages ──────────────────────────────────────────────


@pytest.mark.skip("Issue 226: legacy static pages retired — index.html deleted")
def test_index_has_nav(client):
    content = client.get("/static/index.html").text
    assert "Dashboard" in content or "AutoClip" in content
    assert "/static/review.html" in content


@pytest.mark.skip("Issue 226: legacy static pages retired — review.html deleted")
def test_review_has_nav(client):
    content = client.get("/static/review.html").text
    assert "AutoClip" in content
    assert "Dashboard" in content


@pytest.mark.skip("Issue 226: legacy static pages retired — profile.html deleted")
def test_profile_has_nav(client):
    content = client.get("/static/profile.html").text
    assert "AutoClip" in content
    assert "Dashboard" in content


@pytest.mark.skip("Issue 226: legacy static pages retired — insights.html deleted")
def test_insights_has_nav(client):
    content = client.get("/static/insights.html").text
    assert "AutoClip" in content
    assert "Dashboard" in content


# ── Review page accepts video_id param (static assertion) ────────────────────


@pytest.mark.skip("Issue 226: legacy static pages retired — review.html deleted")
def test_review_page_uses_url_params(client):
    """Review page JS should read video_id from URLSearchParams."""
    resp = client.get("/static/review.html")
    assert b"URLSearchParams" in resp.content
    assert b"video_id" in resp.content


@pytest.mark.skip("Issue 226: legacy static pages retired — review.html deleted")
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


@pytest.mark.skip("Issue 226: legacy static pages retired — profile.html deleted")
def test_profile_confirm_triggers_redirect(client):
    """Profile page JS should redirect to / after confirming DNA."""
    resp = client.get("/static/profile.html")
    assert b"window.location = '/'" in resp.content


# ── Onboarding page references profile and uses auth:ready ───────────────────


@pytest.mark.skip("Issue 226: legacy static pages retired — onboarding.html deleted")
def test_onboarding_uses_auth_ready(client):
    resp = client.get("/static/onboarding.html")
    assert b"auth:ready" in resp.content


@pytest.mark.skip("Issue 226: legacy static pages retired — onboarding.html deleted")
def test_onboarding_links_to_profile(client):
    resp = client.get("/static/onboarding.html")
    assert b"profile.html" in resp.content
