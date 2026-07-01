import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db import get_session
from models import Creator

# ── CSRF Fetch-Metadata defence (Issue 230) ───────────────────────────────────
# Sec-Fetch-Site check on state-changing routes. Browser-sent header values:
#   cross-site: definitely from a different origin — reject.
#   same-origin / same-site: from our own origin — allow.
#   none: direct navigation (typed URL, bookmark) — allow.
#   absent: non-browser client (curl, SDK) — allow (API-key callers lack header).
#
# Additionally, requests authenticated via Authorization: Bearer (API-key path)
# are exempted — CSRF is only a risk for cookie-based session auth.
#
# Source: https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html

_CSRF_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def check_not_cross_site(request: Request) -> None:
    """FastAPI dependency: reject cross-site requests on mutating cookie-authed routes.

    Safe to add as a global dependency — it no-ops on:
    - GET / HEAD / OPTIONS (not state-changing)
    - Requests with an Authorization: Bearer header (API-key path, not cookie auth)
    - Absent Sec-Fetch-Site header (non-browser clients such as curl or SDKs)

    When CSRF_FETCH_METADATA_ENABLED is False (default in tests — TestClient does
    not send Sec-Fetch-* headers), the check is skipped entirely.
    """
    if not settings.CSRF_FETCH_METADATA_ENABLED:
        return
    if request.method not in _CSRF_MUTATING_METHODS:
        return
    # API-key authenticated paths send Authorization: Bearer — not cookie auth.
    # CSRF is only a risk when session cookies are the credential.
    if request.headers.get("authorization", "").startswith("Bearer "):
        return
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is None:
        # Non-browser client (curl, httpx without explicit header) — allow.
        return
    if fetch_site == "cross-site":
        raise HTTPException(
            status_code=403,
            detail="Cross-site request blocked (Fetch-Metadata policy, Issue 230).",
        )


SESSION_COOKIE = "cc_session"
_ALGORITHM = "HS256"
# 60-second leeway absorbs NTP clock drift between issuer and verifier for
# both exp (tolerates a token expired < 60 s ago) and iat (tolerates a token
# issued < 60 s in the future from a clock-skewed peer). RFC 7519 §4.1.6/7.
_JWT_LEEWAY = timedelta(seconds=60)


def create_session_token(creator_id: uuid.UUID) -> str:
    # WHY stateless + non-revocable (Issue 232):
    # This HS256 token is intentionally stateless. Logout deletes the browser
    # cookie (routers/auth.py) but does NOT invalidate the token server-side —
    # a stolen cookie remains valid until the `exp` claim fires.
    #
    # The accepted exposure window is JWT_EXPIRY_MINUTES (default 60 minutes).
    # This tradeoff was made deliberately:
    #   - Adding a Redis jti deny-list for revocation would make every auth
    #     check hard-depend on Redis; if Redis goes down, all sessions are
    #     invalid (the Issue-76 class of failure). The 60-min window is
    #     acceptable for a B2C SaaS with no admin-privilege escalation path.
    #   - For higher-assurance revocation, issue a shorter-lived token
    #     (reduce JWT_EXPIRY_MINUTES) rather than adding the Redis dependency.
    #
    # See docs/COMPLIANCE.md → Auth section for the documented exposure window.
    # Source: https://cheatsheetseries.owasp.org/cheatsheets/JSON_Web_Token_for_Java_Cheat_Sheet.html
    now = datetime.now(UTC)
    payload = {
        "sub": str(creator_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.JWT_EXPIRY_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=_ALGORITHM)


def decode_session_token(token: str) -> dict[str, Any]:
    # `require: ["exp"]` rejects tokens that omit the expiry claim — a hand-crafted
    # token without exp would otherwise decode as non-expiring (Issue 340a).
    # `leeway` grants _JWT_LEEWAY of clock-skew tolerance on both exp and iat.
    return jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[_ALGORITHM],
        options={"require": ["exp"]},
        leeway=_JWT_LEEWAY,
    )


def creator_id_from_cookie(request: Request) -> uuid.UUID | None:
    """Best-effort: resolve the creator UUID from the session cookie's signed JWT
    ``sub``, with NO database lookup.

    For attribution-only paths (telemetry — ``record_activity`` and the
    http_request middleware, Issue 151) that run outside the dependency graph and
    therefore can't take a ``Depends(get_session)``-backed
    ``get_current_creator``. The JWT signature is the trust boundary (a forged
    cookie fails to decode); we only need the id, not a verified Creator row.
    Returns ``None`` for no/invalid cookie. (Issue 152 follow-up fix.)
    """
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        return uuid.UUID(decode_session_token(token)["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        return None


async def get_current_creator(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Creator:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_session_token(token)
        creator_id = uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid or expired session") from None
    # Bootstrap the Creator lookup before SET LOCAL is meaningful: the `creators`
    # table is exempt from RLS (Issue 56), so the query runs cleanly even with
    # no app.creator_id GUC set yet.
    result = await session.execute(select(Creator).where(Creator.id == creator_id))
    creator = result.scalar_one_or_none()
    if creator is None:
        raise HTTPException(status_code=401, detail="Creator not found")
    # Attribute the rest of this request's queries to the resolved creator.
    # The after_begin listener on AsyncSessionLocal will emit
    # `SET LOCAL app.creator_id = :cid` on every subsequent transaction,
    # gating RLS policies on tenant-owned tables (Issue 79).
    session.info["creator_id"] = creator.id
    # Issue 344: the Creator lookup above already auto-began this request's
    # transaction, so `after_begin` fired before `session.info["creator_id"]`
    # was set and emitted no GUC. The endpoint's writes commit in that SAME
    # transaction (no intervening commit/rollback), so without this the INSERT
    # would hit the RLS WITH CHECK with `app.creator_id` unset and 500. Set the
    # GUC on the live transaction now; `is_local=true` clears it at commit,
    # matching the listener's semantics for any later transactions this request.
    await session.execute(
        text("SELECT set_config('app.creator_id', :cid, true)"),
        {"cid": str(creator.id)},
    )
    # Stash on request.state so creator_key() in limiter.py can read the
    # already-resolved identity without re-decoding the JWT. (Issue 104)
    request.state.creator_id = creator.id
    return creator
