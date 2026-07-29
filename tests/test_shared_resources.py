"""Tests for the shared_resources aclose registry (Issue 109b)."""

import pytest
from fastapi.testclient import TestClient

import shared_resources


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Snapshot/restore the process-wide registry so tests never leak entries."""
    saved = list(shared_resources._REGISTRY)
    yield
    shared_resources._REGISTRY = saved


async def test_close_all_runs_in_reverse_registration_order():
    order: list[str] = []

    def make(name):
        async def _close():
            order.append(name)

        return _close

    shared_resources.register_aclose("a", make("a"))
    shared_resources.register_aclose("b", make("b"))
    shared_resources.register_aclose("c", make("c"))

    await shared_resources.close_all()

    assert order[-3:] == ["c", "b", "a"]


async def test_close_all_isolates_per_resource_errors():
    closed: list[str] = []

    async def ok_a():
        closed.append("a")

    async def boom():
        raise RuntimeError("Event loop is closed")

    async def ok_c():
        closed.append("c")

    shared_resources.register_aclose("a", ok_a)
    shared_resources.register_aclose("boom", boom)
    shared_resources.register_aclose("c", ok_c)

    # Must not raise, and the failure must not block the remaining closes.
    await shared_resources.close_all()

    assert "a" in closed and "c" in closed


async def test_reregistering_a_name_replaces_and_moves_to_end():
    calls: list[str] = []

    def make(tag):
        async def _close():
            calls.append(tag)

        return _close

    shared_resources.register_aclose("x", make("stale"))
    shared_resources.register_aclose("y", make("y"))
    shared_resources.register_aclose("x", make("fresh"))

    await shared_resources.close_all()

    assert "stale" not in calls
    # x was re-registered last, so it closes before y (reverse order).
    assert calls.index("fresh") < calls.index("y")


def test_lifespan_shutdown_calls_close_all():
    """App shutdown closes registered resources via close_all (TestClient lifespan)."""
    import main as main_module

    closed: list[str] = []

    async def sentinel():
        closed.append("sentinel")

    with TestClient(main_module.app):
        # The health Redis client is lifespan-owned, NOT registry-managed —
        # registry replacement leaked earlier lifespans' clients to GC.
        assert not any(name == "health_redis" for name, _ in shared_resources._REGISTRY)
        shared_resources.register_aclose("test_sentinel", sentinel)
    assert closed == ["sentinel"]


def test_lifespan_owns_health_redis_close_and_restores_previous(monkeypatch):
    """Each lifespan acloses the health Redis client it created (on its own
    loop) and stack-restores the module global, so nested TestClient lifespans
    never leak a replaced client to GC ('Event loop is closed' at exit)."""
    import unittest.mock as mock

    import main as main_module

    fake_client = mock.MagicMock()
    fake_client.aclose = mock.AsyncMock()
    monkeypatch.setattr(main_module.aioredis, "from_url", lambda *a, **kw: fake_client)

    prev = main_module._health_redis
    with TestClient(main_module.app):
        assert main_module._health_redis is fake_client
    fake_client.aclose.assert_awaited_once()
    assert main_module._health_redis is prev
