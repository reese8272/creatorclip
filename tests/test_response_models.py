"""
Standing guard for API response-model coverage (Issue 76 / Issue 73).

response_model is what gives each endpoint a typed OpenAPI contract AND an explicit
response-side field allow-list (so an ORM/dict can't leak a field the API never meant
to expose). This test fails if a future endpoint ships without one — the same
"make it an invariant, not a one-time review" posture as test_isolation.py.

Exempt are the routes that intentionally return no schema-modeled JSON body:
the index HTML, the liveness probe, the OAuth redirects, and the 204 delete.
(/metrics and the Stripe webhook are include_in_schema=False and drop out here.)
"""

from fastapi.routing import APIRoute

from main import app

_EXEMPT_PATHS = {
    "/",  # serves index.html (HTMLResponse, not JSON)
    "/health",  # liveness probe — intentionally a plain status dict
    "/auth/login",  # 302 RedirectResponse to Google
    "/auth/callback",  # 302 RedirectResponse after OAuth
}


def test_every_documented_json_route_declares_response_model() -> None:
    missing: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.include_in_schema:
            continue
        if route.path in _EXEMPT_PATHS:
            continue
        if route.status_code == 204:  # no response body (e.g. account deletion)
            continue
        if route.response_model is None:
            methods = ",".join(sorted((route.methods or set()) - {"HEAD", "OPTIONS"}))
            missing.append(f"{methods} {route.path}")
    assert not missing, f"endpoints missing response_model: {sorted(set(missing))}"
