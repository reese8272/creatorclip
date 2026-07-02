"""
Issue 335 — YouTube edge suite.

Coverage targets:
  1. oauth: expires_at NULL guard; error string not leaked; Lua-0 mid-flight; preserve
     stored refresh_token when Google omits it in the re-auth response.
  2. analytics: empty rows for all four report functions; all-zero activity totals;
     unparseable day strings skipped.
  3. data_api: malformed ISO-8601 duration logs WARNING; classify_video_kind(0.0)→short;
     clamp_ingest_field with astral/emoji; check_captions_available absent→False.
  4. ingest: probe_duration_s non-zero rc logs stderr; extract_audio_wav stderr in
     RuntimeError message; download_via_ytdlp enabled-but-file-not-found.
  5. DB-sync integration edges (marked `integration`): sync_video_catalog empty
     playlist; sync_video_analytics no-metrics path; check_data_gate zero videos.
"""

from __future__ import annotations

import logging
import sys
import types
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# ── Shared helpers ─────────────────────────────────────────────────────────────


def _make_lock_redis(*, eval_return: int = 1) -> AsyncMock:
    """Fake Redis that always acquires the refresh lock and returns `eval_return`
    from the Lua compare-and-delete (1 = deleted our lock; 0 = already expired)."""
    mock = AsyncMock()
    mock.set = AsyncMock(return_value=True)
    mock.eval = AsyncMock(return_value=eval_return)
    return mock


def _admin_session_mock() -> MagicMock:
    inner = AsyncMock()
    inner.execute = AsyncMock(return_value=MagicMock())
    inner.commit = AsyncMock()
    inner.rollback = AsyncMock()
    factory = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=inner)
    cm.__aexit__ = AsyncMock(return_value=None)
    factory.return_value = cm
    factory.inner = inner
    return factory


# ──────────────────────────────────────────────────────────────────────────────
# 1. OAUTH EDGES
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_valid_access_token_null_expires_at_triggers_refresh():
    """expires_at = None on a YoutubeToken row must not raise TypeError.

    The column is nullable=False in production, but a defensive None-guard
    prevents an AttributeError/TypeError on a row that has somehow arrived
    with a null value, routing it safely into the refresh branch instead of
    crashing the request.
    """
    from crypto import encrypt
    from youtube.oauth import get_valid_access_token

    creator_id = uuid.uuid4()
    row = MagicMock()
    row.refresh_token_encrypted = encrypt("refresh-tok")
    row.access_token_encrypted = encrypt("old-access-tok")
    row.expires_at = None  # ← the defensive case
    row.scope = "openid"

    session_mock = AsyncMock()
    session_mock.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: row))
    session_mock.commit = AsyncMock()
    session_mock.refresh = AsyncMock()

    new_payload = {
        "access_token": "fresh-tok",
        "expires_in": 3600,
        "scope": "openid",
    }
    admin = _admin_session_mock()

    with (
        patch("db.AdminSessionLocal", admin),
        patch("youtube.oauth.get_redis_client", return_value=_make_lock_redis()),
        patch("youtube.oauth.refresh_access_token", AsyncMock(return_value=new_payload)),
        patch("youtube.oauth.store_or_update_tokens", AsyncMock()),
    ):
        token = await get_valid_access_token(creator_id, session_mock)

    assert token == "fresh-tok"


@pytest.mark.asyncio
async def test_get_valid_access_token_error_string_not_leaked_to_client():
    """The 401 raised on refresh failure must contain only a generic message.

    Google's error body (e.g. "invalid_client: …") must never appear in the
    exception detail surfaced to the end user.
    """
    from fastapi import HTTPException

    from crypto import encrypt
    from youtube.oauth import get_valid_access_token

    creator_id = uuid.uuid4()
    row = MagicMock()
    row.refresh_token_encrypted = encrypt("refresh-tok")
    row.access_token_encrypted = encrypt("old-access-tok")
    row.expires_at = datetime.now(UTC) - timedelta(minutes=2)
    row.scope = "openid"

    session_mock = AsyncMock()
    session_mock.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: row))

    import httpx

    class _Resp:
        status_code = 400
        request = httpx.Request("POST", "https://oauth2.googleapis.com/token")

        def json(self) -> dict:
            return {"error": "invalid_client", "error_description": "super-secret-internal-detail"}

    async def _fail(_refresh_token: str) -> dict:
        raise httpx.HTTPStatusError("400", request=_Resp.request, response=_Resp())

    admin = _admin_session_mock()
    with (
        patch("db.AdminSessionLocal", admin),
        patch("youtube.oauth.get_redis_client", return_value=_make_lock_redis()),
        patch("youtube.oauth.refresh_access_token", side_effect=_fail),
        pytest.raises(HTTPException) as exc_info,
    ):
        await get_valid_access_token(creator_id, session_mock)

    detail = str(exc_info.value.detail)
    assert exc_info.value.status_code == 401
    assert "invalid_client" not in detail
    assert "super-secret-internal-detail" not in detail


@pytest.mark.asyncio
async def test_lock_ttl_expires_lua_returns_zero_no_corruption():
    """Lua compare-and-delete returning 0 (lock expired mid-flight) must not
    cause an exception or corrupt state — the refresh still completes successfully.

    eval() returning 0 means the TTL lapsed and another worker may have taken the
    key, but since our refresh already committed the code must just return normally.
    """
    from crypto import encrypt
    from youtube.oauth import get_valid_access_token

    creator_id = uuid.uuid4()
    row = MagicMock()
    row.refresh_token_encrypted = encrypt("refresh-tok")
    row.access_token_encrypted = encrypt("old-access-tok")
    row.expires_at = datetime.now(UTC) - timedelta(minutes=2)
    row.scope = "openid"

    session_mock = AsyncMock()
    session_mock.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: row))
    session_mock.refresh = AsyncMock()

    new_payload = {"access_token": "new-tok", "expires_in": 3600, "scope": "openid"}
    admin = _admin_session_mock()

    # eval returns 0 → lock already gone (TTL expired during our refresh)
    redis = _make_lock_redis(eval_return=0)

    with (
        patch("db.AdminSessionLocal", admin),
        patch("youtube.oauth.get_redis_client", return_value=redis),
        patch("youtube.oauth.refresh_access_token", AsyncMock(return_value=new_payload)),
        patch("youtube.oauth.store_or_update_tokens", AsyncMock()),
    ):
        token = await get_valid_access_token(creator_id, session_mock)

    assert token == "new-tok"
    # Lua script was called exactly once in the finally block
    redis.eval.assert_awaited_once()


@pytest.mark.asyncio
async def test_store_or_update_tokens_preserves_stored_refresh_when_omitted():
    """When Google's re-auth response omits refresh_token, the existing DB row's
    refresh_token_encrypted must NOT be overwritten.

    This is the token-rotation edge: Google only re-issues a refresh_token on the
    first authorization; incremental / subsequent authorizations return only an
    access_token. The stored refresh_token must survive.
    """
    from crypto import decrypt, encrypt
    from youtube.oauth import store_or_update_tokens

    creator_id = uuid.uuid4()
    stored_refresh = "stored-refresh-token"

    existing_row = MagicMock()
    existing_row.access_token_encrypted = encrypt("old-access")
    existing_row.refresh_token_encrypted = encrypt(stored_refresh)
    existing_row.scope = "openid"
    existing_row.expires_at = datetime.now(UTC)
    existing_row.updated_at = None

    session_mock = AsyncMock()
    session_mock.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=lambda: existing_row)
    )

    await store_or_update_tokens(
        session_mock,
        creator_id,
        access_token="new-access",
        refresh_token=None,  # ← omitted by Google
        scope="openid",
        expires_in=3600,
    )

    # The stored refresh must not have been touched.
    assert decrypt(existing_row.refresh_token_encrypted) == stored_refresh


# ──────────────────────────────────────────────────────────────────────────────
# 2. ANALYTICS EMPTY-ROW COVERAGE
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_retention_curve_empty_rows_returns_empty_list():
    """Empty Analytics report for retention curve → []."""
    from youtube.analytics import fetch_retention_curve

    empty = {"columnHeaders": [], "rows": []}
    with patch("youtube.analytics._fetch_report", AsyncMock(return_value=empty)):
        result = await fetch_retention_curve("tok", "vid", "UC_test", 600.0)
    assert result == []


@pytest.mark.asyncio
async def test_fetch_demographics_empty_rows_returns_empty_payload():
    """Empty Analytics demographics response → {"rows": []}."""
    from youtube.analytics import fetch_demographics

    empty = {"columnHeaders": [], "rows": []}
    with patch("youtube.analytics._fetch_report", AsyncMock(return_value=empty)):
        result = await fetch_demographics("tok", "UC_test")
    assert result == {"rows": []}


@pytest.mark.asyncio
async def test_fetch_demographics_null_rows_returns_empty_payload():
    """Analytics demographics with null rows key → {"rows": []}."""
    from youtube.analytics import fetch_demographics

    null_rows = {"columnHeaders": [], "rows": None}
    with patch("youtube.analytics._fetch_report", AsyncMock(return_value=null_rows)):
        result = await fetch_demographics("tok", "UC_test")
    assert result == {"rows": []}


@pytest.mark.asyncio
async def test_fetch_audience_activity_all_zero_views_does_not_divide_by_zero():
    """All-zero view totals must produce activity_index=0.0, not ZeroDivisionError.

    The `max_views or 1.0` guard replaces a zero divisor with 1.0, so the
    resulting activity_index for every bucket is 0 / 1.0 = 0.0.
    """
    from youtube.analytics import fetch_audience_activity

    all_zero = {
        "columnHeaders": [{"name": "day"}, {"name": "views"}],
        "rows": [
            ["2026-01-05", 0],  # Monday
            ["2026-01-06", 0],  # Tuesday
        ],
    }
    with patch("youtube.analytics._fetch_report", AsyncMock(return_value=all_zero)):
        rows = await fetch_audience_activity("tok", "UC_test")

    assert len(rows) == 2
    assert all(r["activity_index"] == 0.0 for r in rows)


@pytest.mark.asyncio
async def test_fetch_audience_activity_unparseable_day_strings_are_skipped():
    """Rows with dates that can't be parsed by %Y-%m-%d are silently skipped."""
    from youtube.analytics import fetch_audience_activity

    mixed = {
        "columnHeaders": [{"name": "day"}, {"name": "views"}],
        "rows": [
            ["2026-01-05", 500],  # valid — Monday
            ["not-a-date", 9999],  # invalid — must be skipped
            ["2026-01-06", 300],  # valid — Tuesday
        ],
    }
    with patch("youtube.analytics._fetch_report", AsyncMock(return_value=mixed)):
        rows = await fetch_audience_activity("tok", "UC_test")

    # Only 2 valid rows; the malformed one is dropped
    assert len(rows) == 2
    indices = [r["activity_index"] for r in rows]
    # Monday had 500 views = max → index 1.0; Tuesday 300 → 0.6
    assert any(pytest.approx(1.0) == v for v in indices)
    assert any(pytest.approx(0.6) == v for v in indices)


@pytest.mark.asyncio
async def test_fetch_audience_activity_empty_rows_returns_empty_list():
    """Empty audience-activity report → []."""
    from youtube.analytics import fetch_audience_activity

    empty = {"columnHeaders": [], "rows": []}
    with patch("youtube.analytics._fetch_report", AsyncMock(return_value=empty)):
        result = await fetch_audience_activity("tok", "UC_test")
    assert result == []


# ──────────────────────────────────────────────────────────────────────────────
# 3. DATA_API EDGES
# ──────────────────────────────────────────────────────────────────────────────


def test_parse_duration_malformed_returns_zero_and_logs_warning(caplog):
    """Malformed ISO-8601 string → 0.0 AND a WARNING log for observability.

    The silent-zero default is preserved (callers tolerate 0.0 as a sentinel),
    but the WARNING allows operators to spot bad API payloads in production.
    """
    from youtube.data_api import parse_duration_seconds

    with caplog.at_level(logging.WARNING, logger="youtube.data_api"):
        result = parse_duration_seconds("P-INVALID-GARBAGE")

    assert result == 0.0
    assert any("unrecognized" in rec.message.lower() for rec in caplog.records), (
        "Expected a WARNING mentioning 'unrecognized' for malformed duration"
    )


def test_parse_duration_empty_string_returns_zero_and_logs_warning(caplog):
    """Empty string is also malformed → 0.0 + WARNING."""
    from youtube.data_api import parse_duration_seconds

    with caplog.at_level(logging.WARNING, logger="youtube.data_api"):
        result = parse_duration_seconds("")

    assert result == 0.0
    assert caplog.records  # at least one WARNING


def test_classify_video_kind_zero_duration_is_short():
    """A 0.0-second duration (sentinel / parse-failure fallback) classifies as short.

    0.0 <= SHORTS_MAX_DURATION_S (180) so the condition is satisfied. This
    boundary is load-bearing: a failed parse must not accidentally
    classify a video as long-form.
    """
    from models import VideoKind
    from youtube.data_api import classify_video_kind

    assert classify_video_kind(0.0) == VideoKind.short


def test_clamp_ingest_field_emoji_preserved_as_full_codepoint():
    """clamp_ingest_field with an emoji/astral char must not produce mojibake.

    Python's `str` slices by Unicode code point, so emoji (U+1F600+) are never
    split mid-sequence regardless of their UTF-8 byte representation. The test
    asserts that a short emoji title is returned intact.
    """
    from youtube.data_api import clamp_ingest_field

    # 8 chars — well under any sane max_chars; must come back unchanged.
    title = "Hello 🎬🎥"
    result = clamp_ingest_field(title, 200)
    assert result == title


def test_clamp_ingest_field_emoji_at_boundary_does_not_split_codepoint():
    """When truncation falls right on an emoji boundary, the emoji is removed whole."""
    from youtube.data_api import clamp_ingest_field

    # Build a string where the emoji starts at index max_chars-1
    # (so a byte-based slice would cut inside it but a codepoint slice drops it).
    base = "A" * 9  # 9 chars
    emoji = "🎬"  # 1 codepoint, 4 bytes
    value = base + emoji  # 10 chars total
    result = clamp_ingest_field(value, 9)
    # rsplit at word boundary — no space, so falls back to [:9] = "AAAAAAAAA"
    assert "🎬"[:1] not in result or result == base  # emoji not partially present
    assert "\ud83c" not in result  # no surrogate half


def test_check_captions_available_empty_items_returns_false():
    """check_captions_available returns False when the API returns no caption tracks."""
    import asyncio

    from youtube.data_api import check_captions_available

    async def _run():
        with patch("youtube.data_api._get_json", AsyncMock(return_value={"items": []})):
            return await check_captions_available("tok", "vid123")

    assert asyncio.get_event_loop().run_until_complete(_run()) is False


def test_check_captions_available_missing_key_returns_false():
    """check_captions_available returns False when the API response has no 'items' key."""
    import asyncio

    from youtube.data_api import check_captions_available

    async def _run():
        with patch("youtube.data_api._get_json", AsyncMock(return_value={})):
            return await check_captions_available("tok", "vid123")

    assert asyncio.get_event_loop().run_until_complete(_run()) is False


# ──────────────────────────────────────────────────────────────────────────────
# 4. INGEST EDGES
# ──────────────────────────────────────────────────────────────────────────────


def test_probe_duration_s_non_zero_returncode_returns_none_and_logs_stderr(caplog):
    """probe_duration_s: when ffprobe exits non-zero (missing stream, corrupt file)
    the function returns None AND logs the stderr[:500] tail at WARNING level.
    """
    from youtube.ingest import probe_duration_s

    with (
        patch(
            "subprocess.run",
            return_value=MagicMock(
                returncode=1,
                stdout="",
                stderr="Invalid data found when processing input",
            ),
        ),
        caplog.at_level(logging.WARNING, logger="youtube.ingest"),
    ):
        result = probe_duration_s("/tmp/bad.mp4")

    assert result is None
    assert any("Invalid data" in rec.message for rec in caplog.records), (
        "Expected stderr content in WARNING log"
    )


def test_probe_duration_s_empty_stdout_returns_none():
    """probe_duration_s: returncode=0 but empty stdout (no duration found) → None."""
    from youtube.ingest import probe_duration_s

    with patch(
        "subprocess.run",
        return_value=MagicMock(returncode=0, stdout="  \n  ", stderr=""),
    ):
        result = probe_duration_s("/tmp/noduration.mp4")

    assert result is None


def test_extract_audio_wav_stderr_tail_in_runtime_error():
    """extract_audio_wav: when ffmpeg fails, the RuntimeError message contains
    stderr[:500] so operators see the exact ffmpeg error without digging logs.
    """
    from youtube.ingest import extract_audio_wav

    with (
        patch(
            "subprocess.run",
            return_value=MagicMock(
                returncode=1,
                stderr="No audio stream found: stream #0:0 is video only",
            ),
        ),
        pytest.raises(RuntimeError) as exc_info,
    ):
        extract_audio_wav("/tmp/in.mp4", "/tmp/out.wav")

    assert "No audio stream found" in str(exc_info.value)


def test_download_via_ytdlp_enabled_file_not_found(monkeypatch, tmp_path):
    """download_via_ytdlp: YTDLP_ENABLED=True, yt-dlp 'succeeds' but the
    expected WAV output file is not created → clear FileNotFoundError.
    """
    monkeypatch.setattr("config.settings.YTDLP_ENABLED", True)

    class _FakeYDL:
        def __init__(self, opts: dict) -> None:
            pass

        def __enter__(self) -> _FakeYDL:
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def download(self, urls: list[str]) -> None:
            # "Succeeds" but does NOT create the WAV file.
            pass

    fake_yt_dlp = types.ModuleType("yt_dlp")
    fake_yt_dlp.YoutubeDL = _FakeYDL  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "yt_dlp", fake_yt_dlp)

    from youtube.ingest import download_via_ytdlp

    with pytest.raises(FileNotFoundError, match="not found"):
        download_via_ytdlp("dQw4w9WgXcQ", tmp_path)


# ──────────────────────────────────────────────────────────────────────────────
# 5. DB-SYNC INTEGRATION EDGES
# ──────────────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def _db_session():
    """Async DB session fixture following the established integration-test pattern.

    Creates its own engine + sessionmaker so the test's async lifecycle is
    self-contained and does not interfere with the session-scoped TestClient
    or the module-level db.engine pool.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from config import settings

    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sync_video_catalog_empty_playlist_is_no_op(_db_session):
    """sync_video_catalog with an empty uploads playlist must not add any Video rows.

    The early-return `if not playlist_items: return` guard is confirmed against
    a real Postgres session — no Video row is inserted.
    """
    from sqlalchemy import select

    from models import Creator, OnboardingState, Video
    from youtube.analytics import sync_video_catalog

    session = _db_session
    creator = Creator(
        google_sub=f"edge335_{uuid.uuid4().hex[:8]}",
        channel_id="UC_edge335",
        onboarding_state=OnboardingState.active,
    )
    session.add(creator)
    await session.commit()

    with patch("youtube.analytics.list_channel_videos", AsyncMock(return_value=[])):
        await sync_video_catalog(session, creator, "tok")
    await session.commit()

    result = await session.execute(select(Video).where(Video.creator_id == creator.id))
    videos = result.scalars().all()
    assert videos == [], f"Expected no Video rows, got {len(videos)}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sync_video_analytics_no_metrics_does_not_create_row(_db_session):
    """sync_video_analytics: when fetch_video_metrics returns None (no YouTube
    Analytics data for this video), no VideoMetrics row must be created.

    Also mocks fetch_retention_curve (called when duration_s > 0) so the test
    stays in the unit boundary for HTTP — we are testing DB behaviour only.
    """
    from sqlalchemy import select

    from models import (
        Creator,
        IngestStatus,
        OnboardingState,
        Video,
        VideoKind,
        VideoMetrics,
        VideoOrigin,
    )
    from youtube.analytics import sync_video_analytics

    session = _db_session
    creator = Creator(
        google_sub=f"edge335b_{uuid.uuid4().hex[:8]}",
        channel_id="UC_edge335b",
        onboarding_state=OnboardingState.active,
    )
    session.add(creator)
    await session.flush()

    video = Video(
        creator_id=creator.id,
        youtube_video_id=f"yt{uuid.uuid4().hex[:8]}",
        kind=VideoKind.long,
        duration_s=600.0,
        origin=VideoOrigin.catalog,
        ingest_status=IngestStatus.pending,
    )
    session.add(video)
    await session.commit()

    # Mock BOTH analytics fetchers — this test is about DB state, not HTTP.
    # fetch_retention_curve is called unconditionally when duration_s > 0, so it
    # must also be patched to avoid a real 401 against the YouTube Analytics API.
    with (
        patch("youtube.analytics.fetch_video_metrics", AsyncMock(return_value=None)),
        patch("youtube.analytics.fetch_retention_curve", AsyncMock(return_value=[])),
    ):
        await sync_video_analytics(session, video, creator, "tok")
    await session.commit()

    metrics_result = await session.execute(
        select(VideoMetrics).where(VideoMetrics.video_id == video.id)
    )
    assert metrics_result.scalar_one_or_none() is None, (
        "No VideoMetrics row should be created when fetch_video_metrics returns None"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_check_data_gate_zero_videos_returns_all_zeros(_db_session):
    """check_data_gate with a creator who has no videos → both counts zero,
    both ready-flags False, overall ready=False.
    """
    from models import Creator, OnboardingState
    from youtube.analytics import check_data_gate

    session = _db_session
    creator = Creator(
        google_sub=f"edge335c_{uuid.uuid4().hex[:8]}",
        onboarding_state=OnboardingState.connected,
    )
    session.add(creator)
    await session.commit()

    gate = await check_data_gate(session, creator.id)

    assert gate["long_form_videos"] == 0
    assert gate["shorts"] == 0
    assert gate["long_form_ready"] is False
    assert gate["shorts_ready"] is False
    assert gate["ready"] is False
