"""
Shared slowapi Limiter keyed on creator_id extracted from the session JWT.
Falls back to remote IP for unauthenticated requests.

Issue 104: ``creator_key`` is the per-endpoint key_func that reads
``request.state.creator_id`` stamped by the auth dependencies
(``get_current_creator`` and ``get_current_creator_via_api_key``) instead of
re-decoding the JWT in the key_func.  This covers bearer-authenticated routes
(e.g. ``/clips/ingest``) which carry no session cookie and therefore bypassed
per-creator bucketing entirely under the old approach.

Issue 106: ``_creator_key`` now verifies ``exp`` with a 60s leeway and narrows
the exception to ``jwt.InvalidTokenError``. Previously ``verify_exp: False`` +
bare ``except Exception: pass`` meant an expired or exfiltrated session token
still keyed the per-creator rate-limit bucket — a quota-leak vector — and a
``JWT_SECRET_KEY`` misconfig was silently swallowed.

Issue 312 — bounded Redis socket timeout (SEV1):
  slowapi 0.1.9's ``_check_request_limit`` is a synchronous ``def`` that calls
  ``self.limiter.hit()`` with no ``await``.  ``SlowAPIMiddleware`` invokes it
  via the synchronous ``sync_check_limits`` → ``_check_limits`` path, so the
  Redis round-trip blocks the event-loop thread on every rate-limited request.

  The async-storage path (``limits.aio``, URI scheme ``async+redis://``) exists
  in ``limits`` 5.x but requires the caller to ``await`` the resulting
  coroutine.  slowapi 0.1.9 does NOT do this — see extension.py line 509:
  ``if not self.limiter.hit(lim.limit, *args, cost=cost):``.  Providing an
  async URI would make ``hit()`` return a coroutine that is truthily evaluated
  as True, silently disabling all limits.  The async path therefore requires a
  slowapi upgrade.

  INTERIM FIX (shipped here): keep sync ``RedisStorage``; add bounded
  ``socket_timeout`` (0.1 s) and ``socket_connect_timeout`` (0.25 s) via
  ``storage_options``.  These kwargs flow through the chain:
    ``Limiter(storage_options={...})``
    → ``storage_from_string(uri, **storage_options)``
    → ``RedisStorage.__init__(uri, **options)``
    → ``redis.from_url(uri, **options)``
    → connection pool ``connection_kwargs``
  Verified empirically: ``pool.connection_kwargs["socket_timeout"] == 0.1``.

  A Redis stall now times-out after 100 ms and raises ``RedisError`` which
  slowapi's in-process fallback (or ``swallow_errors``) absorbs, degrading
  ONE request instead of head-of-line-blocking the event loop.

  WHEN TO SHIP THE ASYNC PATH: upgrade slowapi to a version that ``await``s
  ``limiter.hit()`` (track upstream; not in 0.1.9).  At that point, switch the
  URI to ``async+redis://`` and use ``limits.aio.strategies`` — the ``Limiter``
  constructor and key_func interface are unchanged.
"""

import logging

import jwt
from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from config import settings

logger = logging.getLogger(__name__)

SESSION_COOKIE = "cc_session"

# 60s tolerates real NTP drift between hosts but rejects tokens past the
# 60-minute JWT_EXPIRY_MINUTES window. RFC 7519 §4.1.4 recommends "a few
# minutes" — for a security-relevant key decoder, 60s is the defensible
# choice over the longer windows you'd pick for a user-facing UX path.
# (Issue 106 — overrides /assess recommendation of 300s; see DECISIONS.)
_JWT_LEEWAY_S = 60

# Issue 312: bound the Redis socket timeout so a Redis stall degrades a single
# request instead of blocking the event-loop thread indefinitely.
#
# socket_timeout=0.1   — max time (s) to wait for a Redis *response*.
#                        100 ms is generous for a co-located Redis; adjust up
#                        if Redis is on a remote host (but keep < 500 ms).
# socket_connect_timeout=0.25 — max time (s) to *establish* the TCP connection.
#                        250 ms covers cold-start and short network blips.
#
# Both kwargs are passed directly to redis.from_url() via the limits library's
# storage_options chain (RedisStorage.__init__ → redis.from_url(**options)).
_REDIS_STORAGE_OPTIONS: dict[str, float] = {
    "socket_timeout": 0.1,
    "socket_connect_timeout": 0.25,
}


def _creator_key(request: Request) -> str:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        try:
            payload = jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=["HS256"],
                options={"verify_exp": True},
                leeway=_JWT_LEEWAY_S,
            )
            return str(payload["sub"])
        except jwt.InvalidTokenError as exc:
            # Log the exception CLASS only — PyJWT error messages can
            # include claim values (truncated subject, etc.) which should
            # not appear in plain logs. The log aggregator dedupes by
            # fingerprint, so per-line dedup isn't our concern.
            logger.warning("jwt_decode_failed exc=%s", type(exc).__name__)
    return get_remote_address(request)


def creator_key(request: Request) -> str:
    """Per-creator rate-limit key for authenticated routes (Issue 104).

    The auth dependencies (``get_current_creator`` and
    ``get_current_creator_via_api_key``) stamp ``request.state.creator_id``
    before returning so this key_func reads the already-resolved value —
    no re-decode of the JWT or bearer token is needed.  This is the slowapi
    canonical pattern: auth dependency stashes the resolved identity on
    ``request.state``; the key_func reads it.

    Falls back to ``get_remote_address`` on unauthenticated routes that
    accidentally inherit this key_func — no crash, just IP-based bucketing.
    """
    cid = getattr(request.state, "creator_id", None)
    if cid is not None:
        return str(cid)
    return get_remote_address(request)


limiter = Limiter(
    key_func=_creator_key,
    storage_uri=settings.REDIS_URL,
    storage_options=_REDIS_STORAGE_OPTIONS,  # type: ignore[arg-type]  # Dict[str,float] vs Dict[str,str] stub
)
