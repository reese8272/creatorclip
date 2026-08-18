"""Issue 522 — every Redis client in the app must have a deadline.

This is the load-bearing half of the fail-closed spend guard, not hygiene.
``billing/spend_guard.py`` now refuses paid work when it cannot read the budget,
but that arm is only reachable if the read can *fail*. Against a
wedged-but-connected Redis a hang is not an exception: ``await r.ttl(...)``
simply never returns, the request hangs, and the guard never gets to decide
anything. A socket timeout is what converts "wedged" into an error.

Measured on the pinned redis 5.2.0: ``socket_timeout`` and
``socket_connect_timeout`` both default to **None** — block until the OS gives
up. (redis.io's production guide states a 10 s default; that is redis-py 6.x and
does not apply to this pin. Verify against the installed version, not the docs.)

``youtube/_redis.py`` was the last of the app's four clients without them —
Issue 312 fixed the limiter and left this one, which is exactly the shape of
drift a per-client sweep is meant to stop.
"""

from __future__ import annotations

import pytest

# Every module-level Redis client factory in the app, with the ceiling each is
# allowed. Scanned rather than trusted: a new client added without a timeout
# should be added here deliberately, not discovered during an outage.
_CLIENT_FACTORIES = [
    # (import path, factory attr, max socket_timeout seconds)
    ("youtube._redis", "get_redis_client", 5.0),
]


@pytest.mark.parametrize(("module_path", "factory", "ceiling"), _CLIENT_FACTORIES)
def test_client_sets_bounded_socket_timeouts(
    module_path: str, factory: str, ceiling: float
) -> None:
    import importlib

    module = importlib.import_module(module_path)
    client = getattr(module, factory)()
    kwargs = client.connection_pool.connection_kwargs

    socket_timeout = kwargs.get("socket_timeout")
    connect_timeout = kwargs.get("socket_connect_timeout")

    assert socket_timeout is not None, (
        f"{module_path}.{factory}() has no socket_timeout. redis-py 5.2.0 "
        "defaults it to None (block indefinitely), so a wedged Redis hangs the "
        "caller instead of raising — and the fail-closed spend guard can never "
        "be reached (Issue 522)."
    )
    assert 0 < socket_timeout <= ceiling, (
        f"socket_timeout {socket_timeout}s is outside (0, {ceiling}]. Too loose "
        "and a request-path dependency stalls; too tight and ordinary latency "
        "becomes spuriously refused paid work."
    )
    assert connect_timeout is not None, (
        f"{module_path}.{factory}() has no socket_connect_timeout (Issue 522)."
    )
    assert 0 < connect_timeout <= ceiling


def test_youtube_redis_matches_the_sibling_client_convention() -> None:
    """2.0 s, the same as worker/progress.py and worker/tasks.py.

    Deliberately NOT limiter.py's 0.1 s: the limiter degrades OPEN, so an
    aggressive timeout costs it nothing, while this client backs a fail-CLOSED
    money control. Pinning the constant means a careless edit cannot drift it
    back toward None without this failing.
    """
    from youtube._redis import _SOCKET_TIMEOUTS

    assert _SOCKET_TIMEOUTS == {"socket_timeout": 2.0, "socket_connect_timeout": 2.0}


# ── Loop rebinding (Issue 522) ────────────────────────────────────────────────


def test_client_rebinds_when_the_event_loop_changes() -> None:
    """A second `asyncio.run()` must get a client bound to ITS loop, not the dead one.

    redis.asyncio binds a connection to whichever event loop first uses it, so a
    module-level singleton reused from a later loop raises
    ``RuntimeError: Event loop is closed``. Production never hits this — one
    long-lived loop — but the test suite runs each ``asyncio.run()`` and each
    TestClient on its own, and worker tasks call this client directly.

    It was invisible while the spend guard failed OPEN: the RuntimeError was
    swallowed and the caller carried on. Failing CLOSED turned the same error
    into refused paid work, which is how six integration tests started reporting
    a task status of 'failed' where they expected 'ready'.

    ``worker/progress.py::_async_client`` already solved this; this module had
    not, which is the drift the test pins shut.
    """
    import asyncio

    import youtube._redis as youtube_redis

    youtube_redis._REDIS_CLIENT = None  # noqa: SLF001
    youtube_redis._REDIS_LOOP = None  # noqa: SLF001

    async def _grab():
        return youtube_redis.get_redis_client()

    first = asyncio.run(_grab())
    second = asyncio.run(_grab())

    assert first is not second, (
        "get_redis_client() returned the SAME client across two event loops. Its "
        "connections are bound to the first loop, which is now closed, so the "
        "next await raises RuntimeError: Event loop is closed — and with the "
        "spend guard failing closed that refuses paid work (Issue 522)."
    )


def test_client_is_reused_within_one_event_loop() -> None:
    """The rebinding must not degrade into a client-per-call.

    Without this, returning a fresh client every time would satisfy the test
    above while quietly abandoning the connection-pool singleton this module
    exists to provide.
    """
    import asyncio

    import youtube._redis as youtube_redis

    youtube_redis._REDIS_CLIENT = None  # noqa: SLF001
    youtube_redis._REDIS_LOOP = None  # noqa: SLF001

    async def _grab_twice():
        return youtube_redis.get_redis_client(), youtube_redis.get_redis_client()

    first, second = asyncio.run(_grab_twice())
    assert first is second, "the client must be a singleton within one loop"
