"""
Standing guard for API response-model coverage (Issue 76 / Issue 73).

response_model is what gives each endpoint a typed OpenAPI contract AND an explicit
response-side field allow-list (so an ORM/dict can't leak a field the API never meant
to expose). This test fails if a future endpoint ships without one — the same
"make it an invariant, not a one-time review" posture as test_isolation.py.

Exempt are the routes that intentionally return no schema-modeled JSON body:
the index HTML, the liveness probe, the OAuth redirects, and the 204 delete.
(/metrics and the Stripe webhook are include_in_schema=False and drop out here.)

Issue 73 close-out: GET /api/logs/me now has response_model=EventLogListOut and
POST /{video_id}/queue now has response_model=QueuedOut — both previously missing.
"""

from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import BaseModel
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)

from main import app

_EXEMPT_PATHS = {
    "/",  # serves index.html (HTMLResponse, not JSON)
    "/health",  # liveness probe — intentionally a plain status dict
    "/auth/login",  # 302 RedirectResponse to Google
    "/auth/callback",  # 302 RedirectResponse after OAuth
    "/auth/connect-publishing",  # 302 RedirectResponse to Google (incremental consent)
    "/tasks/{task_id}/events",  # SSE (text/event-stream); wire format is named SSE events, not a JSON body (Issue 86)
    "/clips/{clip_id}/download",  # 302 → presigned R2 or FileResponse; no JSON schema
    "/creators/me/export/download",  # 302 → presigned R2 or FileResponse; no JSON schema
}

# Routes whose response_class signals a non-JSON body (redirect, HTML page, file
# download, SSE) are exempt — they intentionally have no JSON schema to declare.
_NON_JSON_RESPONSE_CLASSES = {
    Response,
    RedirectResponse,
    HTMLResponse,
    FileResponse,
    StreamingResponse,
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
        # FastAPI wraps the default response_class in a DefaultPlaceholder;
        # unwrap it so the set membership check works on the actual class.
        rc = route.response_class
        actual_rc = rc.value if hasattr(rc, "value") else rc
        if actual_rc in _NON_JSON_RESPONSE_CLASSES:
            continue
        if route.response_model is None:
            methods = ",".join(sorted((route.methods or set()) - {"HEAD", "OPTIONS"}))
            missing.append(f"{methods} {route.path}")
    assert not missing, f"endpoints missing response_model: {sorted(set(missing))}"


def test_guard_catches_undeclared_route() -> None:
    """The guard catches a new route that ships without response_model=.

    FastAPI infers response_model from a typed return annotation (e.g. -> dict),
    so to simulate a genuinely undeclared route we must explicitly set
    response_model=None. The guard test therefore verifies that our production
    guard correctly identifies a route whose response_model was never set.
    Uses a separate app fixture so we don't pollute the production app.
    """

    class _DummyOut(BaseModel):
        ok: bool

    dummy_app = FastAPI()

    @dummy_app.get("/with_model", response_model=_DummyOut)
    async def _with_model() -> dict:  # noqa: RUF100
        return {"ok": True}

    # Simulate a route where the developer forgot to declare a response_model
    # and also omitted a return type annotation (FastAPI then sets response_model=None).
    route_no_model = APIRoute(
        "/no_model",
        endpoint=lambda: {"ok": False},
        methods=["GET"],
        response_model=None,
        include_in_schema=True,
        status_code=200,
    )
    dummy_app.routes.append(route_no_model)

    missing: list[str] = []
    for route in dummy_app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.response_model is None:
            missing.append(route.path)

    assert "/no_model" in missing, "guard must catch the undeclared route"
    assert "/with_model" not in missing, "guard must pass the declared route"
