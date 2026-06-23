"""Dedicated event-log sink (Issue 151).

Persists UI + backend telemetry to the `event_logs` table through its own
SQLAlchemy engine (bound to `settings.logs_database_url`, which defaults to the
primary DB but can point at a separate logical/physical DB so high-volume
telemetry never contends with the OLTP path).

Two hard rules:
  1. **Best-effort** — a logging failure must NEVER break the request it is
     describing. Every write is wrapped; failures are logged and swallowed.
  2. **No PII / no secrets** — `_redact()` scrubs any `extra` key whose name
     looks like an email, token, password, cookie, or secret before it touches
     the row. The creator is identified by id only, never email.

The engine is created lazily on first use so importing this module (e.g. in a
Celery worker) does not open a pool on the wrong event loop.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config import settings
from models import EventLog
from redact import _REDACTED, is_sensitive

logger = logging.getLogger(__name__)

_MAX_KEYS = 20
_MAX_STR_LEN = 500

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _is_sensitive(key: str) -> bool:
    return is_sensitive(key)


def _redact(extra: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a copy of `extra` with sensitive keys masked, key-count capped, and
    long string values truncated. Pure function — unit-tested without a DB."""
    if not extra:
        return None
    out: dict[str, Any] = {}
    for key, value in list(extra.items())[:_MAX_KEYS]:
        if _is_sensitive(key):
            out[key] = _REDACTED
        elif isinstance(value, str):
            out[key] = value[:_MAX_STR_LEN]
        else:
            out[key] = value
    return out


def _get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _engine, _sessionmaker
    if _sessionmaker is None:
        _engine = create_async_engine(
            settings.logs_database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            pool_recycle=1800,
            connect_args={"prepare_threshold": None},
        )
        _sessionmaker = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)
    return _sessionmaker


async def record_event(
    *,
    source: str,
    event: str,
    creator_id: uuid.UUID | str | None = None,
    level: str = "info",
    request_id: str | None = None,
    page: str | None = None,
    target: str | None = None,
    status_code: int | None = None,
    duration_ms: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Append one telemetry row. Best-effort: never raises into the caller."""
    if not settings.EVENT_LOG_DB_ENABLED:
        return

    cid: uuid.UUID | None
    if isinstance(creator_id, str):
        try:
            cid = uuid.UUID(creator_id)
        except ValueError:
            cid = None  # "anonymous" and other non-UUID actors are not creators
    else:
        cid = creator_id

    try:
        sm = _get_sessionmaker()
        async with sm() as session:
            session.add(
                EventLog(
                    source=source,
                    event=event,
                    level=level,
                    creator_id=cid,
                    request_id=request_id,
                    page=page[:128] if page else None,
                    target=target[:256] if target else None,
                    status_code=status_code,
                    duration_ms=duration_ms,
                    extra=_redact(extra),
                )
            )
            await session.commit()
    except Exception:  # noqa: BLE001 — telemetry must never break the request path
        logger.warning("event_log.record_event failed (swallowed)", exc_info=True)


async def purge_creator_events(creator_id: uuid.UUID | str) -> int:
    """Delete all telemetry rows for a creator (Issue 248 — right to erasure).

    ``event_logs`` lives on a separate engine with no FK to ``creators``, so the
    DB cascade on account deletion can't reach it — deletion must purge it
    explicitly. Best-effort: a failure here is logged and returns ``-1`` rather
    than aborting the account deletion (mirrors the R2-purge posture). Returns the
    number of rows deleted, or 0 when telemetry is disabled.
    """
    if not settings.EVENT_LOG_DB_ENABLED:
        return 0
    cid = uuid.UUID(creator_id) if isinstance(creator_id, str) else creator_id
    try:
        sm = _get_sessionmaker()
        async with sm() as session:
            result = await session.execute(delete(EventLog).where(EventLog.creator_id == cid))
            await session.commit()
            return result.rowcount or 0
    except Exception:  # noqa: BLE001 — erasure best-effort; never abort deletion
        logger.warning("event_log.purge_creator_events failed (swallowed)", exc_info=True)
        return -1


async def purge_stale_events(cutoff: datetime) -> int:
    """Delete all telemetry rows older than ``cutoff`` (Issue 250 — GDPR Art. 5(1)(e)).

    Enforces the rolling retention window defined by ``EVENT_LOG_RETENTION_DAYS``
    (default 90 days). Only rows with ``at < cutoff`` are removed, so the cutoff
    boundary is exclusive — rows exactly at the cutoff instant are kept.

    Best-effort: a failure is logged and returns ``-1`` rather than propagating.
    Mirrors ``purge_creator_events`` exactly in error posture and engine usage.
    Returns the number of rows deleted, or 0 when telemetry is disabled.
    """
    if not settings.EVENT_LOG_DB_ENABLED:
        return 0
    try:
        sm = _get_sessionmaker()
        async with sm() as session:
            result = await session.execute(delete(EventLog).where(EventLog.at < cutoff))
            await session.commit()
            return result.rowcount or 0
    except Exception:  # noqa: BLE001 — best-effort; never propagate
        logger.warning("event_log.purge_stale_events failed (swallowed)", exc_info=True)
        return -1


async def dispose() -> None:
    """Dispose the logs engine pool. Call on app shutdown."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None
