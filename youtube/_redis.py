"""
Shared Redis client singleton for the youtube package.

Both youtube/quota.py and youtube/oauth.py need a Redis connection.
Factoring the singleton here avoids duplication and ensures both modules
share the same underlying connection pool (redis-py 4.2+ provides a pool
per Redis instance by default).

Import pattern:
    from youtube._redis import get_redis_client
"""

import logging

import redis.asyncio as redis

from config import settings
from shared_resources import register_aclose

logger = logging.getLogger(__name__)

_REDIS_CLIENT: redis.Redis | None = None

# Issue 522 — bounded socket timeouts. redis-py 5.2.0 defaults BOTH of these to
# None, i.e. block until the OS gives up (TCP keepalive, ~2 h). Verified against
# the pinned version: redis.io's production guide states a 10 s default, but that
# is redis-py 6.x behaviour and does not apply here.
#
# This is load-bearing, not hygiene. billing/spend_guard.py now fails CLOSED on a
# Redis error, and against a wedged-but-connected Redis a hang is not an
# exception — `await r.ttl(...)` would simply never return, so the fail-closed
# arm could never be reached and the request would hang instead of refusing.
# A deadline is what turns "wedged" into an error the guard can act on.
#
# 2.0 s matches the three sibling clients (worker/progress.py sync + async,
# worker/tasks.py) rather than limiter.py's 0.1 s. Different consequence class:
# the limiter degrades OPEN, so an aggressive timeout there costs nothing, while
# this client backs a fail-CLOSED money control where an over-tight timeout turns
# ordinary latency into spuriously refused paid work. This module is also shared
# by the API process and the Celery worker, so the value must suit both.
_SOCKET_TIMEOUTS: dict[str, float] = {
    "socket_timeout": 2.0,
    "socket_connect_timeout": 2.0,
}


def get_redis_client() -> redis.Redis:
    """Return the module-level Redis singleton, creating it on first call.

    redis-py 4.2+ manages a connection pool internally per client instance.
    Reusing one client across the process is the recommended production pattern.
    """
    global _REDIS_CLIENT
    if _REDIS_CLIENT is None:
        _REDIS_CLIENT = redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            **_SOCKET_TIMEOUTS,
        )
    return _REDIS_CLIENT


async def aclose() -> None:
    """Close the shared client and reset the singleton so a later call recreates it.

    This client is reached from both the API process (spend_guard, oauth,
    data_api) and the Celery worker process (youtube.quota) — the API side
    is wired into shutdown via ``shared_resources.close_all()``
    (Issue 109b), and the worker side calls this directly from
    ``worker_process_shutdown`` (worker/celery_app.py, Issue 367).
    """
    global _REDIS_CLIENT
    if _REDIS_CLIENT is not None:
        try:
            await _REDIS_CLIENT.aclose()
        except Exception as exc:  # noqa: BLE001 — shutdown must never raise
            logger.warning("youtube_redis aclose failed: %s", exc)
    _REDIS_CLIENT = None


# App shutdown closes this via shared_resources.close_all() (Issue 109b); the
# worker process also closes it explicitly (Issue 367, see aclose() docstring).
register_aclose("youtube_redis", aclose)
