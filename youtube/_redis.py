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


def get_redis_client() -> redis.Redis:
    """Return the module-level Redis singleton, creating it on first call.

    redis-py 4.2+ manages a connection pool internally per client instance.
    Reusing one client across the process is the recommended production pattern.
    """
    global _REDIS_CLIENT
    if _REDIS_CLIENT is None:
        _REDIS_CLIENT = redis.from_url(settings.REDIS_URL, decode_responses=True)
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
