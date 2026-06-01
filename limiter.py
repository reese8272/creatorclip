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
)
