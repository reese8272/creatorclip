"""
Tests for the React SPA serving layer (React adoption, docs/DECISIONS.md
2026-06-17). The SPA is served under /app/*: hashed assets via a StaticFiles
mount, every other /app path falling back to index.html so React Router owns
client-side routing. Legacy static/ pages must keep working.

The bundle (frontend/dist) is gitignored and only present after
`npm --prefix frontend run build`, so these tests skip when it is absent rather
than failing a fresh checkout / CI stage that has not built the frontend.
"""

import re

import pytest

from main import _SPA_INDEX

pytestmark = pytest.mark.skipif(
    not _SPA_INDEX.is_file(),
    reason="SPA bundle not built (run: npm --prefix frontend run build)",
)


def test_spa_route_returns_shell(client):
    resp = client.get("/app/profile")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert '<div id="root">' in resp.text
    # The shell must reference its hashed assets under the /app/ base.
    assert "/app/assets/" in resp.text


def test_spa_deep_route_falls_back_to_shell(client):
    # An arbitrary client-side path is owned by React Router, not the server.
    resp = client.get("/app/some/deep/route")
    assert resp.status_code == 200
    assert '<div id="root">' in resp.text


def test_spa_hashed_asset_resolves(client):
    shell = client.get("/app/profile").text
    match = re.search(r"/app/assets/index-[^\"']+\.js", shell)
    assert match, "no hashed JS asset reference in the SPA shell"
    asset = client.get(match.group(0))
    assert asset.status_code == 200
    assert "javascript" in asset.headers["content-type"]


def test_legacy_static_pages_retired(client):
    # The legacy vanilla UI was retired in Issue 226 — the /static/*.html pages
    # were removed (the SPA under /app/* is now the only UI). The /static mount
    # remains for CSS/JS assets, but the legacy HTML pages must be gone.
    resp = client.get("/static/login.html")
    assert resp.status_code == 404
