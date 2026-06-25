"""Security baseline regression guards (Issues 107, 229, 230).

Enforces two invariants that prevent silent drift in the pip-audit ignore lists:

1. Every ID in run_layer0.py::PIP_AUDIT_IGNORES has a non-empty comment in the
   source explaining WHY it is accepted-risk.

2. The ignore list in pyproject.toml [tool.pip-audit] and the Python-side
   PIP_AUDIT_IGNORES set are identical — so `pip-audit` invoked directly (e.g. by
   a developer) applies the same ignores as the CI harness.

These tests catch the most dangerous form of ignore-list rot: an ID that is added
silently with no rationale, or an ID that exists on one side but not the other.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_RUN_LAYER0 = (
    _REPO_ROOT / ".claude" / "skills" / "production-assessment" / "scripts" / "run_layer0.py"
)
_PYPROJECT = _REPO_ROOT / "pyproject.toml"


def _load_pip_audit_ignores_from_script() -> set[str]:
    """Parse PIP_AUDIT_IGNORES out of run_layer0.py using the AST (not import).

    Importing the script would execute module-level side effects and require the
    full production environment. AST parsing is hermetic and fast.
    """
    tree = ast.parse(_RUN_LAYER0.read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "PIP_AUDIT_IGNORES"
            and isinstance(node.value, ast.Set)
        ):
            return {
                elt.value
                for elt in node.value.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            }
    raise AssertionError(
        f"PIP_AUDIT_IGNORES set not found in {_RUN_LAYER0}. "
        "If the variable was renamed, update this test."
    )


def _load_pip_audit_ignores_from_toml() -> list[str]:
    """Read [tool.pip-audit].ignore-vulns from pyproject.toml."""
    with _PYPROJECT.open("rb") as fh:
        config = tomllib.load(fh)
    return config.get("tool", {}).get("pip-audit", {}).get("ignore-vulns", [])


def test_pip_audit_ignore_lists_are_in_sync() -> None:
    """PIP_AUDIT_IGNORES in run_layer0.py must equal [tool.pip-audit].ignore-vulns
    in pyproject.toml.

    The harness applies ignores via CLI flags (from the Python set); the TOML stanza
    applies ignores when pip-audit is invoked directly. They must stay identical so
    developers and CI see the same result.
    """
    script_ids = _load_pip_audit_ignores_from_script()
    toml_ids = set(_load_pip_audit_ignores_from_toml())

    only_in_script = script_ids - toml_ids
    only_in_toml = toml_ids - script_ids

    assert not only_in_script, (
        f"IDs in run_layer0.py::PIP_AUDIT_IGNORES but NOT in "
        f"pyproject.toml [tool.pip-audit].ignore-vulns: {sorted(only_in_script)}. "
        f"Add them to pyproject.toml with a reason comment, or remove them from both."
    )
    assert not only_in_toml, (
        f"IDs in pyproject.toml [tool.pip-audit].ignore-vulns but NOT in "
        f"run_layer0.py::PIP_AUDIT_IGNORES: {sorted(only_in_toml)}. "
        f"Add them to the Python set, or remove them from both."
    )


def test_run_layer0_pip_audit_ignores_have_comments() -> None:
    """Every ID in PIP_AUDIT_IGNORES must have a non-empty comment nearby in the
    source explaining WHY it is accepted-risk.

    An uncommented ignore entry is a maintenance hazard: future developers cannot
    judge whether the rationale still applies.

    Convention: the comment must appear on the same line as the ID string OR on
    the line immediately preceding it (i.e. within 2 raw lines of the string literal).
    """
    source = _RUN_LAYER0.read_text()
    ignore_ids = _load_pip_audit_ignores_from_script()

    for vuln_id in ignore_ids:
        # Find the character offset of the ID in the source.
        idx = source.find(f'"{vuln_id}"')
        assert idx != -1, (
            f'"{vuln_id}" not found as a string literal in {_RUN_LAYER0}. '
            "This test may need to be updated if quoting style changed."
        )
        # Grab the 2 lines preceding + the line containing the ID to check for a comment.
        preceding_text = source[:idx]
        lines_before = preceding_text.splitlines()[-2:] if preceding_text.splitlines() else []
        line_with_id = source[idx:].split("\n", 1)[0]
        context = "\n".join(lines_before + [line_with_id])
        assert re.search(r"#\s*\S", context), (
            f'"{vuln_id}" in PIP_AUDIT_IGNORES has no comment explaining why it is '
            f"accepted-risk. Add a # comment on the same or preceding line. "
            f"Context seen:\n{context}"
        )


def test_pyproject_pip_audit_ignores_have_comments() -> None:
    """Every ID in pyproject.toml [tool.pip-audit].ignore-vulns must have a
    non-empty # comment in the raw TOML source.

    TOML parsers discard comments, so this test reads the raw text and checks
    that each ID string literal has a comment on the same or immediately
    preceding line.
    """
    toml_text = _PYPROJECT.read_text()
    toml_ids = _load_pip_audit_ignores_from_toml()

    for vuln_id in toml_ids:
        idx = toml_text.find(f'"{vuln_id}"')
        assert idx != -1, (
            f'"{vuln_id}" not found as a string literal in {_PYPROJECT}. '
            "This test may need updating if quoting style changed."
        )
        preceding_text = toml_text[:idx]
        lines_before = preceding_text.splitlines()[-2:] if preceding_text.splitlines() else []
        line_with_id = toml_text[idx:].split("\n", 1)[0]
        context = "\n".join(lines_before + [line_with_id])
        assert re.search(r"#\s*\S", context), (
            f'"{vuln_id}" in pyproject.toml [tool.pip-audit].ignore-vulns has no '
            f"# comment explaining why it is accepted-risk. "
            f"Context seen:\n{context}"
        )


# ── Issue 230: CSRF Fetch-Metadata defence ───────────────────────────────────

import uuid as _uuid  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from auth import get_current_creator  # noqa: E402
from db import get_session  # noqa: E402
from main import app  # noqa: E402


def _csrf_client(monkeypatch) -> TestClient:
    """TestClient with CSRF_FETCH_METADATA_ENABLED=True and a mocked creator."""
    monkeypatch.setattr("config.settings.CSRF_FETCH_METADATA_ENABLED", True)

    creator = MagicMock()
    creator.id = _uuid.uuid4()

    async def _fake_session():
        session = AsyncMock()
        result = MagicMock()
        result.scalars.return_value = []
        session.execute = AsyncMock(return_value=result)
        yield session

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_current_creator, None)
        app.dependency_overrides.pop(get_session, None)


def test_csrf_cross_site_post_returns_403(monkeypatch):
    """Issue 230: POST with Sec-Fetch-Site: cross-site must return 403.
    This is the primary CSRF rejection case — a cross-site form/fetch attack.
    """
    for c in _csrf_client(monkeypatch):
        resp = c.post(
            "/videos/link",
            json={"youtube_video_id": "abc12345678"},
            headers={"sec-fetch-site": "cross-site"},
        )
    assert resp.status_code == 403, (
        f"Issue 230: cross-site POST must be rejected with 403. Got {resp.status_code}."
    )


def test_csrf_same_origin_post_passes(monkeypatch):
    """Issue 230: POST with Sec-Fetch-Site: same-origin must not be blocked.
    SPA API calls from the same origin are the normal operating path.
    """
    for c in _csrf_client(monkeypatch):
        resp = c.post(
            "/videos/link",
            json={"youtube_video_id": "abc12345678"},
            headers={"sec-fetch-site": "same-origin"},
        )
    # 4xx/5xx is fine for auth/validation reasons — the CSRF check must not be the cause.
    assert resp.status_code != 403, (
        f"Issue 230: same-origin POST must NOT be CSRF-blocked. Got {resp.status_code}."
    )


def test_csrf_absent_header_passes(monkeypatch):
    """Issue 230: POST with no Sec-Fetch-Site header must not be blocked.
    Non-browser API clients (curl, SDK) do not send this header; blocking them
    would break the API-key path.
    """
    for c in _csrf_client(monkeypatch):
        resp = c.post(
            "/videos/link",
            json={"youtube_video_id": "abc12345678"},
        )
    assert resp.status_code != 403, (
        f"Issue 230: absent Sec-Fetch-Site must NOT be CSRF-blocked. Got {resp.status_code}."
    )


def test_csrf_bearer_auth_passes(monkeypatch):
    """Issue 230: POST with Authorization: Bearer must not be CSRF-blocked.
    API-key callers authenticate via Bearer, not session cookies — CSRF is
    only a risk for cookie-authed routes.
    """
    for c in _csrf_client(monkeypatch):
        resp = c.post(
            "/videos/link",
            json={"youtube_video_id": "abc12345678"},
            headers={"sec-fetch-site": "cross-site", "authorization": "Bearer test-key"},
        )
    assert resp.status_code != 403, (
        f"Issue 230: Authorization: Bearer cross-site POST must NOT be CSRF-blocked. "
        f"Got {resp.status_code}."
    )


def test_csrf_get_routes_not_blocked(monkeypatch):
    """Issue 230: GET requests must never be blocked by the CSRF check.
    Safe methods (GET, HEAD, OPTIONS) are never state-changing.
    """
    for c in _csrf_client(monkeypatch):
        resp = c.get(
            "/videos",
            headers={"sec-fetch-site": "cross-site"},
        )
    assert resp.status_code != 403, (
        f"Issue 230: GET with cross-site header must NOT be CSRF-blocked. Got {resp.status_code}."
    )


def test_csrf_disabled_in_dev(monkeypatch):
    """Issue 230: CSRF_FETCH_METADATA_ENABLED=False (default dev/test value) means
    cross-site POST must pass through. TestClient does not send Sec-Fetch-* headers,
    so tests that don't explicitly enable the feature must not be affected.
    """
    # Explicitly leave CSRF_FETCH_METADATA_ENABLED=False (default).
    creator = MagicMock()
    creator.id = _uuid.uuid4()

    async def _fake_session():
        session = AsyncMock()
        result = MagicMock()
        result.scalars.return_value = []
        session.execute = AsyncMock(return_value=result)
        yield session

    app.dependency_overrides[get_current_creator] = lambda: creator
    app.dependency_overrides[get_session] = _fake_session
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.post(
                "/videos/link",
                json={"youtube_video_id": "abc12345678"},
                headers={"sec-fetch-site": "cross-site"},
            )
    finally:
        app.dependency_overrides.pop(get_current_creator, None)
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code != 403, (
        f"Issue 230: with CSRF_FETCH_METADATA_ENABLED=False, cross-site POST must "
        f"NOT be blocked. Got {resp.status_code}."
    )


# ── Issue 228: every LLM/render route is quota + rate-limit gated ─────────────
#
# Cost-safety structural guard. A new handler in any LLM/render router that
# enqueues billed work MUST carry BOTH a @limiter.limit decorator (the daily/
# burst ceiling) AND a check_positive_balance / check_balance* call (the floor).
# Walking the AST catches a gate-less route at commit time instead of in prod.

_LLM_RENDER_ROUTERS = (
    "clips",
    "titles",
    "thumbnails",
    "insights",
    "improvement",
    "analysis",
)

# Read-only / cheap routes in these modules that intentionally carry no balance
# floor (plain polls/reads at 60–120/minute) — excluded from the floor sweep but
# still required to be rate-limited.
_FLOOR_EXEMPT_HANDLERS = frozenset(
    {
        "get_clip_counts",
        "list_clips",
        "clean_preview",
        "clean_confirm",
        "clip_transcript",
        "get_clip",
        "download_clip",
        "get_insights",
        "get_analytics_summary",
        "save_insight",
        "list_insights",
        "delete_insight",
        "get_improvement_brief",
        "get_video_analysis",
        "get_hook_analysis",
        "get_chapters",
    }
)


def _decorator_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Return dotted-attribute names for each decorator on a function node."""
    names: list[str] = []
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        parts: list[str] = []
        while isinstance(target, ast.Attribute):
            parts.append(target.attr)
            target = target.value
        if isinstance(target, ast.Name):
            parts.append(target.id)
        names.append(".".join(reversed(parts)))
    return names


def _is_write_route(dec_names: list[str]) -> bool:
    """True if the handler is a state-changing/enqueueing route (post/put)."""
    return any(n.endswith(".post") or n.endswith(".put") for n in dec_names)


def _has_limiter_decorator(dec_names: list[str]) -> bool:
    return any(n.endswith("limiter.limit") or n == "limiter.limit" for n in dec_names)


def _calls_balance_gate(node: ast.AST) -> bool:
    """True if the function body calls check_positive_balance / check_balance*."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name == "check_positive_balance" or name.startswith("check_balance"):
                return True
    return False


def _gated_handlers(module_src: str) -> list[tuple[str, bool, bool]]:
    """For each write-route handler in a router module return
    (name, has_limiter, has_balance_gate)."""
    tree = ast.parse(module_src)
    out: list[tuple[str, bool, bool]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        dec_names = _decorator_names(node)
        # get_thumbnail_patterns is a GET that runs a billed multimodal LLM call,
        # so it is in-scope for the gate despite not being a write route.
        in_scope = _is_write_route(dec_names) or node.name == "get_thumbnail_patterns"
        if not in_scope:
            continue
        out.append((node.name, _has_limiter_decorator(dec_names), _calls_balance_gate(node)))
    return out


def test_every_llm_render_route_is_quota_and_rate_gated() -> None:
    """Issue 228: each LLM/render write route carries a @limiter.limit AND a
    check_positive_balance/check_balance* call."""
    routers_dir = _REPO_ROOT / "routers"
    checked = 0
    for mod in _LLM_RENDER_ROUTERS:
        src = (routers_dir / f"{mod}.py").read_text()
        for name, has_limiter, has_floor in _gated_handlers(src):
            assert has_limiter, (
                f"routers/{mod}.py::{name} is a billed route with no @limiter.limit "
                f"rate/quota decorator (Issue 228)."
            )
            if name in _FLOOR_EXEMPT_HANDLERS:
                continue
            assert has_floor, (
                f"routers/{mod}.py::{name} enqueues billed work but never calls "
                f"check_positive_balance/check_balance* — add the balance floor "
                f"(Issue 228)."
            )
            checked += 1
    # Guard against the sweep silently matching nothing (e.g. a refactor that
    # renamed decorators) — we must have asserted the floor on real handlers.
    assert checked >= 10, f"expected >=10 gated handlers, swept {checked}"


def test_ast_sweep_flags_a_gateless_route() -> None:
    """A synthetic LLM route with a limiter but NO balance gate must fail the
    floor assertion — proving the sweep is not a no-op."""
    synthetic = (
        "import limiter\n"
        "from billing.ledger import check_positive_balance\n"
        "router = object()\n"
        "@router.post('/leak')\n"
        "@limiter.limit('10/hour')\n"
        "async def leaky_route(request, creator, session):\n"
        "    return {'ok': True}\n"
    )
    handlers = _gated_handlers(synthetic)
    assert handlers, "synthetic route should be detected as a write route"
    name, has_limiter, has_floor = handlers[0]
    assert name == "leaky_route"
    assert has_limiter is True
    assert has_floor is False, "gate-less route must be reported as ungated"
