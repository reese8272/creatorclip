"""Unit tests for the event-log sink redaction guard (Issue 151).

The load-bearing invariant: no PII / token / secret ever reaches an event_logs
row. _redact() is a pure function, so this runs without a database.
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import event_log
from event_log import _redact, purge_creator_events


def test_redact_masks_sensitive_keys():
    out = _redact(
        {
            "email": "creator@example.com",
            "access_token": "ya29.secret",
            "refresh_token": "1//refresh",
            "Authorization": "Bearer abc",
            "password": "hunter2",
            "api_key": "ack_live_123",
            "session_jwt": "eyJ...",
            "note": "kept",
            "count": 7,
        }
    )
    # Everything that looks like a secret is masked...
    for k in (
        "email",
        "access_token",
        "refresh_token",
        "Authorization",
        "password",
        "api_key",
        "session_jwt",
    ):
        assert out[k] == "[redacted]", f"{k} must be redacted"
    # ...and benign fields survive untouched.
    assert out["note"] == "kept"
    assert out["count"] == 7


def test_redact_truncates_long_strings_and_caps_keys():
    long = "x" * 5000
    out = _redact({"note": long, **{f"k{i}": i for i in range(50)}})
    assert len(out["note"]) <= 500
    assert len(out) <= 20  # key-count cap prevents log bloat


def test_redact_none_and_empty():
    assert _redact(None) is None
    assert _redact({}) is None


def test_record_event_noop_when_disabled(monkeypatch):
    """With EVENT_LOG_DB_ENABLED off, record_event returns without ever opening
    a pool (events still flow to app.log via the caller's log_event)."""
    import asyncio

    monkeypatch.setattr(event_log.settings, "EVENT_LOG_DB_ENABLED", False)
    # Reset any sessionmaker a prior test may have built.
    monkeypatch.setattr(event_log, "_sessionmaker", None)
    asyncio.run(event_log.record_event(source="ui", event="click", target="x"))
    assert event_log._sessionmaker is None  # never touched the DB


# ── purge_creator_events (Issue 248 — erasure completeness) ────────────────────


def test_purge_creator_events_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(event_log.settings, "EVENT_LOG_DB_ENABLED", False)
    monkeypatch.setattr(event_log, "_sessionmaker", None)
    n = asyncio.run(purge_creator_events(uuid.uuid4()))
    assert n == 0
    assert event_log._sessionmaker is None  # never opened the logs pool


def test_purge_creator_events_deletes_and_returns_rowcount(monkeypatch):
    monkeypatch.setattr(event_log.settings, "EVENT_LOG_DB_ENABLED", True)

    session = MagicMock()
    result = MagicMock()
    result.rowcount = 3
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()

    class _CM:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(event_log, "_get_sessionmaker", lambda: (lambda: _CM()))
    n = asyncio.run(purge_creator_events(str(uuid.uuid4())))
    assert n == 3
    session.execute.assert_awaited_once()  # a DELETE was issued


def test_purge_creator_events_swallows_errors(monkeypatch):
    """A failure on the logs engine must not abort the deletion path → returns -1."""
    monkeypatch.setattr(event_log.settings, "EVENT_LOG_DB_ENABLED", True)

    def _boom():
        raise RuntimeError("logs DB down")

    monkeypatch.setattr(event_log, "_get_sessionmaker", _boom)
    assert asyncio.run(purge_creator_events(uuid.uuid4())) == -1
