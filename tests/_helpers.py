"""Shared test helpers (importable from any test file).

Kept out of conftest.py because pytest's conftest is for fixtures + plugin
hooks, not directly-imported helper functions."""

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
