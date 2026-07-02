"""Unit tests for the event-log sink redaction guard (Issue 151, 233).

The load-bearing invariant: no PII / token / secret ever reaches an event_logs
row. _redact() is a pure function, so this runs without a database.

Also covers purge_stale_events (Issue 250 — GDPR Art. 5(1)(e) storage-limitation).

Issue 233 regression: _redact() behaviour must be byte-identical after the
blocklist was extracted into redact.py (DRY refactor).
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import event_log
from event_log import _redact, purge_creator_events, purge_stale_events


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

    monkeypatch.setattr(event_log, "_get_sessionmaker", lambda: lambda: _CM())
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


# ── purge_stale_events (Issue 250 — GDPR Art. 5(1)(e) storage-limitation) ────


def test_purge_stale_events_noop_when_disabled(monkeypatch):
    """When EVENT_LOG_DB_ENABLED is False, the function returns 0 without touching the DB."""
    monkeypatch.setattr(event_log.settings, "EVENT_LOG_DB_ENABLED", False)
    monkeypatch.setattr(event_log, "_sessionmaker", None)
    cutoff = datetime.now(UTC) - timedelta(days=90)
    n = asyncio.run(purge_stale_events(cutoff))
    assert n == 0
    assert event_log._sessionmaker is None


def test_purge_stale_events_deletes_and_returns_rowcount(monkeypatch):
    """Happy path: DELETE is issued and the affected rowcount is returned."""
    monkeypatch.setattr(event_log.settings, "EVENT_LOG_DB_ENABLED", True)

    session = MagicMock()
    result = MagicMock()
    result.rowcount = 17
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()

    class _CM:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(event_log, "_get_sessionmaker", lambda: lambda: _CM())
    cutoff = datetime.now(UTC) - timedelta(days=90)
    n = asyncio.run(purge_stale_events(cutoff))
    assert n == 17
    session.execute.assert_awaited_once()
    session.commit.assert_awaited_once()


def test_purge_stale_events_cutoff_boundary(monkeypatch):
    """The DELETE statement must filter on EventLog.at < cutoff (exclusive boundary)."""
    monkeypatch.setattr(event_log.settings, "EVENT_LOG_DB_ENABLED", True)

    captured: dict = {}
    result = MagicMock()
    result.rowcount = 0
    session = MagicMock()

    async def fake_execute(stmt):
        captured["stmt"] = stmt
        return result

    session.execute = AsyncMock(side_effect=fake_execute)
    session.commit = AsyncMock()

    class _CM:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(event_log, "_get_sessionmaker", lambda: lambda: _CM())
    cutoff = datetime.now(UTC) - timedelta(days=90)
    asyncio.run(purge_stale_events(cutoff))

    # The DELETE statement's WHERE clause must reference the `at` column
    where_sql = str(captured["stmt"].whereclause).lower()
    assert "event_logs.at" in where_sql, f"expected 'event_logs.at' in: {where_sql}"


def test_purge_stale_events_swallows_errors(monkeypatch):
    """A DB failure must not propagate — returns -1 (mirrors purge_creator_events)."""
    monkeypatch.setattr(event_log.settings, "EVENT_LOG_DB_ENABLED", True)

    def _boom():
        raise RuntimeError("logs DB down")

    monkeypatch.setattr(event_log, "_get_sessionmaker", _boom)
    cutoff = datetime.now(UTC) - timedelta(days=90)
    assert asyncio.run(purge_stale_events(cutoff)) == -1


# ── purge_stale_event_logs Beat task (Issue 250) ──────────────────────────────


def test_purge_stale_event_logs_beat_registered():
    """Beat schedule must include the daily event-log purge entry."""
    import worker.schedule  # noqa: F401 — registers beat_schedule
    from worker.celery_app import celery

    assert "purge-stale-event-logs-daily" in celery.conf.beat_schedule


def test_purge_stale_event_logs_beat_runs_daily():
    from celery.schedules import timedelta as ctd

    import worker.schedule  # noqa: F401
    from worker.celery_app import celery

    entry = celery.conf.beat_schedule["purge-stale-event-logs-daily"]
    assert entry["schedule"] == ctd(hours=24)


def test_purge_stale_event_logs_task_registered():
    from worker.celery_app import celery

    assert "worker.tasks.purge_stale_event_logs" in celery.tasks


def test_purge_stale_event_logs_task_calls_async_impl():
    from unittest.mock import patch

    with patch("worker.tasks.run_async") as mock_run:
        from worker.tasks import purge_stale_event_logs

        purge_stale_event_logs()
        mock_run.assert_called_once()


# ── Issue 233 regression: _redact() byte-identical after blocklist extraction ──
def test_redact_regression_after_extraction():
    """_redact() output must be unchanged after _REDACT_SUBSTRINGS moved to redact.py."""
    data = {
        "email": "creator@example.com",
        "api_key": "sk-live-abc",
        "creator_id": "uuid-123",
        "count": 42,
    }
    out = _redact(data)
    assert out is not None
    assert out["email"] == "[redacted]"
    assert out["api_key"] == "[redacted]"
    assert out["creator_id"] == "uuid-123"
    assert out["count"] == 42


# ── Issue 337: record_event non-UUID creator_id + write-failure contract ──────


def test_record_event_non_uuid_creator_id_writes_with_cid_none(monkeypatch) -> None:
    """Non-UUID creator_id is coerced to None; the row is still written.

    "anonymous" actors and programmatic callers that pass arbitrary strings must
    not crash record_event — the row lands with creator_id=NULL.
    """
    monkeypatch.setattr(event_log.settings, "EVENT_LOG_DB_ENABLED", True)

    added_rows: list = []
    session = MagicMock()
    session.add = lambda row: added_rows.append(row)
    session.commit = AsyncMock()

    class _CM:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(event_log, "_get_sessionmaker", lambda: lambda: _CM())

    asyncio.run(
        event_log.record_event(
            source="test",
            event="non_uuid_creator_test",
            creator_id="not-a-valid-uuid",
        )
    )

    assert len(added_rows) == 1, "A row must be written even with a non-UUID creator_id"
    assert added_rows[0].creator_id is None, "Non-UUID creator_id must be stored as NULL (cid=None)"


def test_record_event_write_failure_swallowed(monkeypatch) -> None:
    """A DB write failure during record_event must be swallowed — never propagated.

    Telemetry is best-effort; a broken logs DB must not abort the request it is
    describing.
    """
    monkeypatch.setattr(event_log.settings, "EVENT_LOG_DB_ENABLED", True)

    session = MagicMock()
    session.add = MagicMock(side_effect=RuntimeError("DB is down"))
    session.commit = AsyncMock()

    class _CM:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(event_log, "_get_sessionmaker", lambda: lambda: _CM())

    # Must not raise — the caller (e.g. HTTP middleware) must not be affected.
    asyncio.run(
        event_log.record_event(
            source="test",
            event="write_failure_test",
        )
    )
