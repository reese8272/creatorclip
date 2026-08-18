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

  A Redis stall now times-out after 100 ms and raises ``RedisError`` instead of
  head-of-line-blocking the event loop.

  CORRECTED 2026-08-18 (Issue 522): this block used to end "...which slowapi's
  in-process fallback (or ``swallow_errors``) absorbs, degrading ONE request."
  That was false for three months. Neither kwarg was ever passed, both default
  to ``False``, and the ``RedisError`` propagated — so the bounded timeout
  converted an event-loop stall into a fast 500 on every rate-limited route
  rather than into a degraded one. The absorbing half is only true as of the
  ``in_memory_fallback_enabled=True`` below.

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
    # Issue 522 — THE line that keeps a Redis blip from being a sign-in outage.
    # slowapi's own reference: in_memory_fallback_enabled "simply falls back to in
    # memory storage when the main storage is down and inherits the original
    # limits" (https://slowapi.readthedocs.io/en/latest/api/). Without it a
    # RedisError propagates out of _check_request_limit and EVERY rate-limited
    # route 500s with its body never running — including GET /auth/me (120/min),
    # which AuthGate calls on every page load. Nobody could sign in, and /health
    # reported 200 throughout.
    #
    # NOT swallow_errors: that discards the limit entirely, turning an outage into
    # an unlimited-traffic window. This keeps the limits and only moves where they
    # are counted.
    #
    # Accepted cost, stated in DECISIONS 2026-08-17: the fallback bucket is
    # per-process, and prod runs `uvicorn --workers 2`
    # (docker-compose.prod.yml:14), so effective limits are 2x while Redis is
    # down. Two workers' worth of over-admission beats a total outage.
    in_memory_fallback_enabled=True,
)


# Issue 228 — per-creator DAILY job ceiling (cost safety).
#
# The existing per-endpoint hourly @limiter.limit values (e.g. "10/hour",
# "20/hour") are the SHORT-WINDOW burst guard. They bound how fast a creator can
# fire jobs, but NOT how much they can spend over a full day — 20/hour render =
# 480/day of unbounded ffmpeg + R2, and the LLM routers had no usage ceiling at
# all. This module exposes two reusable daily-cap limit strings that routers
# STACK as a second @limiter.limit decorator beneath the hourly one. slowapi
# stores both in limiter._route_limits[qualname] and the MOST-RESTRICTIVE binds
# per request, so the daily ceiling layers on cleanly without a bespoke Redis
# counter — the same pattern routers/chat.py uses for CHAT_DAILY_MESSAGE_LIMIT.
#
# Best-effort caveat (Issue 312, corrected by Issue 522): the cap is Redis-backed.
# During a Redis outage the count moves to the per-process in-memory fallback, so
# the daily ceiling is enforced per worker rather than globally — at
# `--workers 2` that is a 2x effective ceiling until Redis returns, NOT the
# "degrades to fail-open" this comment previously claimed (it did not degrade at
# all; it 500'd). Accepted and consistent with every other limit in this module
# (see docs/DECISIONS.md, Issues 228 and 522).


def daily_limit(cap: int) -> str:
    """Return a slowapi 'N/day' limit string for a per-creator daily ceiling.

    ``cap`` is the maximum number of jobs a single creator may run per day;
    slowapi parses the returned string via ``limits.parse``.
    """
    return f"{cap}/day"


# LLM jobs (titles/thumbnails/insights/improvement/analysis/generate_clips) —
# bounds worst-case Anthropic/Deepgram spend per creator/day.
LLM_DAILY_LIMIT: str = daily_limit(settings.LLM_DAILY_JOB_LIMIT)

# Render jobs (render_clip/clean_clip/submit_cuts/ingest_clip) — bounds ffmpeg
# CPU + Cloudflare R2 egress per creator/day.
RENDER_DAILY_LIMIT: str = daily_limit(settings.RENDER_DAILY_JOB_LIMIT)

# Brief-generating endpoints (titles/thumbnails/insights analysis/improvement
# brief) — independently tunable cap for the most expensive single-request
# inference paths. Stacked on top of the existing hourly burst limit and the
# shared LLM_DAILY_LIMIT; the tightest limit wins at runtime.
BRIEF_DAILY_LIMIT: str = daily_limit(settings.BRIEF_DAILY_LIMIT_PER_CREATOR)
