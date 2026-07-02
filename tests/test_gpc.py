"""Global Privacy Control (Issue 302 — w3c.github.io/gpc).

Three pins: the Sec-GPC header is surfaced on request.state (detection only),
the /.well-known/gpc.json declaration serves both spec fields, and the Privacy
Policy's CCPA section carries the GPC recognition clause (same clause-test
pattern as test_static.py::test_privacy_page_has_limited_use_disclosure).
"""

from fastapi import Request

from main import app


def test_sec_gpc_header_sets_request_state(client):
    """`Sec-GPC: 1` → request.state.gpc is True; absent header → False."""

    @app.get("/_test/gpc-state", include_in_schema=False)
    async def _gpc_state(request: Request) -> dict:
        return {"gpc": request.state.gpc}

    try:
        with_signal = client.get("/_test/gpc-state", headers={"Sec-GPC": "1"})
        assert with_signal.status_code == 200
        assert with_signal.json()["gpc"] is True

        without_signal = client.get("/_test/gpc-state")
        assert without_signal.json()["gpc"] is False
    finally:
        app.router.routes = [
            r for r in app.router.routes if getattr(r, "path", None) != "/_test/gpc-state"
        ]


def test_well_known_gpc_json_served_with_spec_fields(client):
    """GET /.well-known/gpc.json returns the W3C GPC support resource:
    `gpc` (boolean, true) + `lastUpdate` (RFC3339 date), as application/json."""
    resp = client.get("/.well-known/gpc.json")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert body["gpc"] is True
    # RFC3339 full-date shape (YYYY-MM-DD).
    assert isinstance(body["lastUpdate"], str)
    year, month, day = body["lastUpdate"].split("-")
    assert len(year) == 4 and len(month) == 2 and len(day) == 2


def test_privacy_page_has_gpc_clause(client):
    """The CCPA section must state that GPC is recognised and that, because we
    do not sell or share personal information, a GPC opt-out is satisfied by
    default. Pinned so the clause can't silently regress (Issue 302)."""
    resp = client.get("/static/privacy.html")
    assert resp.status_code == 200
    text = resp.content.decode()
    assert "Global Privacy Control" in text
    assert "GPC" in text
    assert "satisfied by default" in text
    # The load-bearing predicate for the default-satisfied stance.
    assert "do not sell or share" in text.lower() or "does not sell or share" in text.lower()
