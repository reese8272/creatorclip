"""Shared test helpers (importable from any test file).

Kept out of conftest.py because pytest's conftest is for fixtures + plugin
hooks, not directly-imported helper functions."""

from fastapi import Request


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
