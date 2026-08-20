"""Shared test helpers (importable from any test file).

Kept out of conftest.py because pytest's conftest is for fixtures + plugin
hooks, not directly-imported helper functions."""

from typing import NamedTuple
from unittest.mock import AsyncMock, MagicMock

from fastapi import Request


def owned_result(row):
    """Mock execute-result for the get_owned ownership-scoped select (Issue 109e).

    Routers fetch owned rows via
    ``(await session.execute(select(M).where(M.id==..., M.creator_id==...))).scalar_one_or_none()``.
    Pass ``row=None`` to simulate missing/foreign (→ 404)."""
    res = MagicMock()
    res.scalar_one_or_none.return_value = row
    return res


def where_criteria(stmt):
    """Extract {column_name: bound_value} from a statement's simple equality
    whereclause — enough to emulate the DB applying get_owned's predicate."""
    crits = {}
    wc = getattr(stmt, "whereclause", None)
    if wc is None:
        return crits
    for clause in getattr(wc, "clauses", [wc]):
        try:
            crits[clause.left.name] = clause.right.value
        except AttributeError:
            continue
    return crits


def owned_lookup_result(stmt, *candidates):
    """Emulate the get_owned ownership select against candidate rows: the
    execute-result's scalar_one_or_none() is the first row matching EVERY
    equality criterion in the whereclause (id AND creator_id), else None —
    exactly what the DB would return. Foreign rows therefore miss → 404."""
    crits = where_criteria(stmt)
    for row in candidates:
        if row is not None and all(getattr(row, k) == v for k, v in crits.items()):
            return owned_result(row)
    return owned_result(None)


def stub_get_owned(session, row):
    """Point ``session.execute`` at the get_owned select: first call returns
    ``owned_result(row)``; later calls return a fresh MagicMock (same default
    behavior a bare AsyncMock session had before Issue 109e). Use an explicit
    side_effect list instead when the endpoint's other execute calls matter."""

    async def _execute(*args, **kwargs):
        if not _execute.first_done:
            _execute.first_done = True
            return owned_result(row)
        return MagicMock()

    _execute.first_done = False
    session.execute = AsyncMock(side_effect=_execute)
    return session


def override_current_creator(creator):
    """Test helper: dependency override for `get_current_creator` that ALSO
    stashes `creator.id` on `request.state`, mirroring what the real auth
    dependency does post-Issue-104. Without this, the slowapi `creator_key`
    (Issue 104) falls back to `get_remote_address` → ``"testclient"`` is
    shared across every test in a run → `/auth/me`, `/clips/*`,
    `/api-keys/*` etc. all burn through their per-hour rate limits within
    the first few tests.

    Usage::

        from tests._helpers import override_current_creator
        app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    """

    async def _override(request: Request):
        request.state.creator_id = creator.id
        return creator

    return _override


# ── Live route-table discovery (Issue 506) ───────────────────────────────────
#
# Three sibling gates used to freeze their scope as a list literal — 9 of 17
# LLM routes in test_creator_quota, 10 of 17 in test_flags, and an AST sweep in
# test_security_baselines that excluded routers/chat.py and routers/creators.py
# entirely, leaving three routes with no structural coverage from any of them.
# The population is now derived here, once, and shared. The house rule is
# "scan, don't list"; the model is tests/test_usage_coverage.py.


class RouteFacts(NamedTuple):
    """What the gates need to know about one live route."""

    path: str
    method: str
    qualname: str  # "routers.clips.generate_clips" — the slowapi limit key
    module: str  # "clips" — the routers/<module>.py it lives in
    dependencies: frozenset[str]  # dependency callable __name__s


def collect_route_contexts(router, out: dict | None = None) -> dict:
    """Map (path, method) -> route context for EVERY effectively-served route.

    FastAPI >= 0.13x defers ``include_router`` into ``_IncludedRouter`` objects,
    so ``app.routes`` holds almost no ``APIRoute`` instances and a naive walk
    finds NOTHING and passes vacuously — that is how the equivalent guard in
    tests/test_response_models.py went vacuous (docs/OFF_COURSE_BUGS.md,
    2026-08-04). This handles both shapes. Callers must still assert they found
    something, because "found nothing" is this walker's failure mode too.
    """
    from fastapi.routing import APIRoute

    if out is None:
        out = {}
    for r in getattr(router, "routes", []) or []:
        if isinstance(r, APIRoute):
            for method in r.methods or set():
                out[(r.path, method)] = r
        elif type(r).__name__ == "_IncludedRouter":
            # Effective contexts carry the PREFIXED path plus the merged
            # dependant — i.e. what actually serves the request.
            ctxs = r.effective_route_contexts
            ctxs = ctxs() if callable(ctxs) else ctxs
            for ctx in ctxs:
                for method in ctx.methods or set():
                    out[(ctx.path, method)] = ctx
            collect_route_contexts(getattr(r, "original_router", None), out)
        else:
            collect_route_contexts(r, out)
    return out


def discover_routes() -> list[RouteFacts]:
    """Every live route, with its gate dependencies resolved. Imports the app."""
    from main import app

    facts: list[RouteFacts] = []
    for (path, method), ctx in collect_route_contexts(app.router).items():
        endpoint = getattr(ctx, "endpoint", None)
        module = getattr(endpoint, "__module__", "") if endpoint else ""
        name = getattr(endpoint, "__name__", "") if endpoint else ""
        deps = {getattr(d.call, "__name__", "") for d in getattr(ctx.dependant, "dependencies", [])}
        facts.append(
            RouteFacts(
                path=path,
                method=method,
                qualname=f"{module}.{name}" if module and name else "",
                module=module.rsplit(".", 1)[-1] if module else "",
                dependencies=frozenset(deps),
            )
        )
    return sorted(facts)


# The kill switch and the spend gate. `require_flag` names its closure
# `require_flag_<key>` (flags.py), so this asserts the LLM switch specifically
# rather than merely *some* flag.
LLM_FLAG_DEP = "require_flag_llm_generation"
BUDGET_DEP = "require_budget"


def kill_switches(route: RouteFacts) -> set[str]:
    """The `require_flag_*` dependencies on a route (flags.py names them so)."""
    return {d for d in route.dependencies if d.startswith("require_flag_")}


def discover_billed_routes() -> list[RouteFacts]:
    """Routes carrying EITHER a spend gate or a kill switch.

    Deliberately not "routes carrying the LLM flag": a test that asserts
    flag-AND-budget over a population defined by the flag is half circular, and
    the half it loses is the one that matters — a route with one gate and not
    the other. Taking the union makes that asymmetry itself the failure.

    Note the pairing rule is NOT "every billed route has the llm_generation
    flag": the render routes are correctly gated by `render_intake` instead.
    The invariant is spend-gate <-> SOME kill switch, plus llm_generation ->
    spend gate. Asserting the LLM flag over this whole population would fail
    five correctly-gated render routes.
    """
    return [r for r in discover_routes() if BUDGET_DEP in r.dependencies or kill_switches(r)]
