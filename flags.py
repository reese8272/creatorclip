"""Runtime feature flags / kill switches (Issue 284).

Two-tier resolution, cheapest-wins, per flag key:

    1. ``feature_flags`` DB row (flip via ``scripts/flags.py`` — NO deploy),
       read through a ~30 s in-process TTL cache so the hot request path adds
       at most one tiny query per key per TTL window per process.
    2. Env default from config (``FLAG_<KEY>_ENABLED``).
    3. Hard default ``True`` — an unknown key means subsystem ON.

FAIL-OPEN: any DB error falls back to the env default (and caches it for one
TTL window so a down DB is not hammered). The flag system being down must
never take the app down — a kill switch is an emergency brake, not a new
single point of failure. The fallback warning is logged once per key, not per
request.

Flips go through :func:`set_flag`, which upserts the row, invalidates the
cache, and emits a ``flag_flipped`` event on both telemetry rails
(observability.log_event line + event_log DB row) so every flip is audited
with actor + reason.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from config import settings
from models import FeatureFlag
from observability import log_event

logger = logging.getLogger(__name__)

# Seconds a resolved flag value is served from process memory before the DB is
# re-read. Worst-case propagation of a flip is one TTL window per process.
FLAG_TTL_S = 30.0

# Known kill switches → their env-default attribute on Settings.
KNOWN_FLAGS: dict[str, str] = {
    "llm_generation": "FLAG_LLM_GENERATION_ENABLED",
    "youtube_publish": "FLAG_YOUTUBE_PUBLISH_ENABLED",
    "render_intake": "FLAG_RENDER_INTAKE_ENABLED",
    "signup": "FLAG_SIGNUP_ENABLED",
}

# Honest, stack-trace-free operator-pause messages surfaced to the client.
_BLOCK_MESSAGES: dict[str, str] = {
    "llm_generation": (
        "AI generation is temporarily paused by the operators. Please try again later."
    ),
    "youtube_publish": "YouTube publishing is temporarily paused by the operators.",
    "render_intake": (
        "Clip rendering is temporarily paused by the operators. Please try again later."
    ),
    "signup": "The beta is at capacity — new signups are temporarily paused.",
}

_cache: dict[str, tuple[float, bool]] = {}
_fallback_warned: set[str] = set()


def _reset_cache() -> None:
    """Clear the TTL cache + warn-once state (tests and set_flag)."""
    _cache.clear()
    _fallback_warned.clear()


def env_default(key: str) -> bool:
    """Env-tier default for ``key``; unknown keys are hard-default ON."""
    attr = KNOWN_FLAGS.get(key)
    if attr is None:
        return True
    return bool(getattr(settings, attr, True))


def block_message(key: str) -> str:
    return _BLOCK_MESSAGES.get(key, "This feature is temporarily paused by the operators.")


async def flag_enabled(
    key: str,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> bool:
    """Resolve one flag: DB row (TTL-cached) → env default → True.

    ``session_factory`` defaults to the app-role sessionmaker; worker tasks
    pass ``db.AdminSessionLocal`` (their loop-bound engine). Never raises —
    any DB failure fails OPEN to the env default.
    """
    now = time.monotonic()
    cached = _cache.get(key)
    if cached is not None and now - cached[0] < FLAG_TTL_S:
        return cached[1]

    try:
        if session_factory is None:
            import db

            session_factory = db.AsyncSessionLocal
        async with session_factory() as session:
            row = await session.get(FeatureFlag, key)
        value = bool(row.enabled) if row is not None else env_default(key)
        _fallback_warned.discard(key)
    except Exception:  # noqa: BLE001 — fail-open by design; the flag system must never 500 the app
        value = env_default(key)
        if key not in _fallback_warned:
            _fallback_warned.add(key)
            logger.warning(
                "feature-flag lookup failed for %s — failing open to env default %s",
                key,
                value,
                exc_info=True,
            )
    _cache[key] = (now, value)
    return value


async def set_flag(
    key: str,
    enabled: bool,
    updated_by: str,
    reason: str | None,
    session: AsyncSession,
) -> FeatureFlag:
    """Upsert one flag row and emit an audited ``flag_flipped`` event.

    Commits the session (a flip must be durable the moment the caller returns)
    and invalidates the local TTL cache; other processes converge within one
    TTL window.
    """
    row = await session.get(FeatureFlag, key)
    if row is None:
        row = FeatureFlag(key=key, enabled=enabled, updated_by=updated_by, reason=reason)
        session.add(row)
    else:
        row.enabled = enabled
        row.updated_by = updated_by
        row.reason = reason
        row.updated_at = datetime.now(UTC)
    await session.commit()
    _cache.pop(key, None)

    log_event("flag_flipped", flag=key, enabled=enabled, updated_by=updated_by, reason=reason)
    # Durable audit row on the event_log rail (best-effort by contract).
    from event_log import record_event

    await record_event(
        source="backend",
        event="flag_flipped",
        level="warning",
        extra={"flag": key, "enabled": enabled, "updated_by": updated_by, "reason": reason},
    )
    return row


def require_flag(key: str) -> Callable[[], Awaitable[None]]:
    """FastAPI dependency factory: 503 with a stable error code when ``key`` is off.

    Usage: ``@router.post(..., dependencies=[Depends(require_flag("llm_generation"))])``.
    The 503 detail carries ``code`` (stable, machine-readable) + an honest
    human message — never a stack trace or DB error.
    """

    async def _gate() -> None:
        if not await flag_enabled(key):
            raise HTTPException(
                status_code=503,
                detail={"code": f"{key}_disabled", "message": block_message(key)},
            )

    return _gate
