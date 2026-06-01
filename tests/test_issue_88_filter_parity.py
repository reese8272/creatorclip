"""
Issue 88 — DNA filter parity + observability.

The bugs being pinned:
  1. `dna.builder.rank_videos` required `Video.ingest_status==done` AND metrics.
     Catalog-synced videos are forever `pending`, so the build raised "0/0"
     even when the data-gate showed plenty of videos. Now: metrics-only.
  2. `youtube.analytics.check_data_gate` counted EVERY Video row regardless
     of metrics. Now: it joins VideoMetrics and matches rank_videos exactly.
  3. The gate's `ready` flag used `AND` while the builder accepts EITHER
     bucket above its min (surfaced by the targeted display-vs-filter audit).
     Now: `OR`.
  4. New `observability.log_event(event, **fields)` helper. Emits a structured
     JSON line so production debugging is `grep event=dna_build_started`.
  5. `build_patterns` emits a diagnostic `dna_build_insufficient_data` event
     on the insufficient-data raise — total/metered/per-kind counts inline.

All tests DB-free; mocked at the single boundary in each module.
"""

from __future__ import annotations

import json
import logging
import uuid
from unittest.mock import MagicMock, patch

import pytest

from models import IngestStatus, VideoKind

# ── rank_videos: drops the ingest_status filter (was the Issue 88 cause) ──────


@pytest.mark.asyncio
async def test_rank_videos_does_not_require_ingest_done():
    """A catalog-synced video has ingest_status=pending forever (no clip
    pipeline ever runs on it), but it has metrics — DNA must rank it."""
    from dna.builder import rank_videos

    captured_stmts: list = []

    async def _capture(stmt):
        captured_stmts.append(stmt)
        result = MagicMock()
        result.all.return_value = []
        return result

    fake_session = MagicMock()
    fake_session.execute = _capture

    await rank_videos(fake_session, uuid.uuid4())

    # Inspect the WHERE clause — must NOT contain ingest_status.
    where_sql = str(captured_stmts[0].whereclause).lower()
    assert "ingest_status" not in where_sql, (
        f"rank_videos must not filter by ingest_status (Issue 88) — got: {where_sql}"
    )
    assert "engagement_rate" in where_sql, "rank_videos must still require metrics for ranking"


# ── check_data_gate: same predicate as rank_videos (Issue 88) ─────────────────


@pytest.mark.asyncio
async def test_check_data_gate_requires_metrics():
    """Pre-Issue-88 the gate counted every Video row regardless of metrics —
    a creator could see "23 videos" while the build raised "0/0".  Now both
    paths share the predicate."""
    from youtube.analytics import check_data_gate

    captured_stmts: list = []

    async def _capture(stmt):
        captured_stmts.append(stmt)
        result = MagicMock()
        result.scalar_one.return_value = 0
        return result

    fake_session = MagicMock()
    fake_session.execute = _capture

    await check_data_gate(fake_session, uuid.uuid4())

    # Two SELECT COUNTs, both with the same metrics predicate.
    assert len(captured_stmts) == 2, "expected one COUNT per kind"
    for stmt in captured_stmts:
        sql = str(stmt.whereclause).lower()
        assert "engagement_rate" in sql, (
            f"check_data_gate must require metrics (Issue 88) — got: {sql}"
        )


@pytest.mark.asyncio
async def test_data_gate_ready_uses_or_not_and():
    """Issue 88 audit finding: pre-fix used AND, blocking creators who only
    had longs (or only shorts) above the min. Builder uses OR; gate must too."""
    from youtube.analytics import check_data_gate

    # Stub session.execute to return a long-only creator: 15 longs, 0 shorts.
    counts = iter([15, 0])

    async def _exec(_stmt):
        r = MagicMock()
        r.scalar_one.return_value = next(counts)
        return r

    fake_session = MagicMock()
    fake_session.execute = _exec

    out = await check_data_gate(fake_session, uuid.uuid4())
    assert out["long_form_ready"] is True
    assert out["shorts_ready"] is False
    assert out["ready"] is True, "OR semantics: long-only creator IS ready"


# ── log_event: structured JSON shape ──────────────────────────────────────────


def test_log_event_emits_structured_record(caplog):
    """`log_event(name, **fields)` must produce a record with event=<name>
    and the supplied fields as `extra=` keys (so JsonLogFormatter promotes
    them to top-level JSON keys in production)."""
    from observability import log_event

    with caplog.at_level(logging.INFO, logger="event"):
        log_event("dna_build_started", creator_id="abc-123", task_id="t-9")

    assert len(caplog.records) == 1
    rec = caplog.records[0]
    assert rec.message.startswith("event=dna_build_started")
    assert "creator_id=abc-123" in rec.message
    # The extras must land on the record so JsonLogFormatter promotes them.
    assert rec.event == "dna_build_started"
    assert rec.creator_id == "abc-123"
    assert rec.task_id == "t-9"


def test_log_event_json_format_promotes_fields():
    """JsonLogFormatter must promote log_event fields to top-level JSON keys
    so log aggregators can filter `event:"dna_build_started" creator_id:"X"`."""
    from observability import JsonLogFormatter

    rec = logging.LogRecord(
        name="event",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="event=foo creator_id=42",
        args=(),
        exc_info=None,
    )
    rec.event = "foo"
    rec.creator_id = "42"
    rec.request_id = "req-1"
    payload = json.loads(JsonLogFormatter().format(rec))
    assert payload["event"] == "foo"
    assert payload["creator_id"] == "42"
    assert payload["message"] == "event=foo creator_id=42"


# ── Diagnostic logging on insufficient-data raise (Issue 88) ──────────────────


@pytest.mark.asyncio
async def test_insufficient_data_raise_emits_diagnostic_event(caplog):
    """When the build fails the readiness check, an `event=
    dna_build_insufficient_data` line must include the row breakdown so the
    next "gate said 23 but build said 0" report is one log line away from
    the answer."""
    from dna.builder import build_patterns

    creator_id = uuid.uuid4()

    async def _exec(stmt):
        result = MagicMock()
        # rank_videos returns 0 rows
        result.all.return_value = []
        # diagnostic count() probes return their hardcoded numbers
        sql = str(stmt).lower()
        if "count(videos.id)" in sql and "video_metrics" in sql:
            result.scalar_one.return_value = 2  # 2 metered videos
        elif "count(videos.id)" in sql:
            result.scalar_one.return_value = 23  # 23 total videos
        else:
            result.scalar_one.return_value = 0
        return result

    fake_session = MagicMock()
    fake_session.execute = _exec

    with (
        caplog.at_level(logging.INFO, logger="event"),
        pytest.raises(ValueError, match="Insufficient data"),
    ):
        await build_patterns(fake_session, creator_id)

    diag = [r for r in caplog.records if getattr(r, "event", "") == "dna_build_insufficient_data"]
    assert diag, "diagnostic event must fire on insufficient-data raise"
    assert diag[0].total_videos_in_db == 23
    assert diag[0].metered_videos == 2
    assert diag[0].creator_id == str(creator_id)


# ── Keep the bug-class smoke alive: pending video with metrics ranks ──────────


def test_video_kind_enum_smoke():
    """Belt-and-suspenders: confirm the VideoKind enum values rank_videos
    relies on still exist (a rename would be a loud import error elsewhere,
    but this test pins the constants the assertions in this file use)."""
    assert VideoKind.long.value == "long"
    assert VideoKind.short.value == "short"
    # IngestStatus.pending must still exist — catalog-synced videos use it.
    assert IngestStatus.pending.value == "pending"


# ── sync_channel_catalog phase 2 chains metrics fetch (Issue 88) ──────────────


@pytest.mark.asyncio
async def test_sync_channel_catalog_chains_metrics_for_unmetered_videos():
    """After upserting Video rows, the catalog sync must call
    `sync_video_analytics` for each video that doesn't yet have an
    engagement_rate. Otherwise the user waits up to an hour for the Beat
    refresh to fill in metrics (the Issue 88 user-visible symptom)."""
    from unittest.mock import AsyncMock

    from worker.tasks import _sync_channel_catalog_async

    creator_id = uuid.uuid4()
    fake_creator = MagicMock(id=creator_id)
    fake_session = MagicMock()
    fake_session.get = AsyncMock(return_value=fake_creator)
    fake_session.commit = AsyncMock()

    # Two unmeasured videos returned by the longs phase-2 query (Issue 120: two
    # queries now — longs and shorts — so shorts returns empty to keep count at 2).
    unmeasured_videos = [MagicMock(id=uuid.uuid4()), MagicMock(id=uuid.uuid4())]
    longs_phase2 = MagicMock()
    longs_phase2.scalars = MagicMock(
        return_value=MagicMock(all=MagicMock(return_value=unmeasured_videos))
    )
    empty_phase2 = MagicMock()
    empty_phase2.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    # execute call order: advisory lock, longs unmeasured, shorts unmeasured, advisory unlock
    advisory_result = MagicMock()
    advisory_result.scalar_one = MagicMock(return_value=True)
    fake_session.execute = AsyncMock(
        side_effect=[advisory_result, longs_phase2, empty_phase2, MagicMock()]
    )

    fake_ctx = MagicMock()
    fake_ctx.__aenter__ = AsyncMock(return_value=fake_session)
    fake_ctx.__aexit__ = AsyncMock(return_value=None)

    sync_catalog_mock = AsyncMock()
    sync_analytics_mock = AsyncMock()
    with (
        patch("worker.tasks.db.AdminSessionLocal", return_value=fake_ctx),
        patch("youtube.oauth.get_valid_access_token", new=AsyncMock(return_value="tok")),
        patch("youtube.analytics.sync_video_catalog", new=sync_catalog_mock),
        patch("youtube.analytics.sync_video_analytics", new=sync_analytics_mock),
    ):
        await _sync_channel_catalog_async(str(creator_id))

    sync_catalog_mock.assert_awaited_once()
    # Phase 2: metrics fetched for both unmeasured videos
    assert sync_analytics_mock.await_count == 2, (
        "catalog sync must chain metrics for every unmeasured video — "
        "without this the user waits an hour for the Beat refresh (Issue 88)"
    )
