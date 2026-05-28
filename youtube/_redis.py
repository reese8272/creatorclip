"""
Shared Redis client singleton for the youtube package.

Both youtube/quota.py and youtube/oauth.py need a Redis connection.
Factoring the singleton here avoids duplication and ensures both modules
share the same underlying connection pool (redis-py 4.2+ provides a pool
per Redis instance by default).

Import pattern:
    from youtube._redis import get_redis_client
"""

import redis.asyncio as redis

from config import settings

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
