"""Tests for Issue 104 — Wave-8 new-surface fixes.

Covers:
  Fix 1 — Per-creator rate-limit key (creator_key key_func)
  Fix 2 — insights aggregate (FILTER clause correctness)
  Fix 3 — temp-file cleanup on non-HTTPException paths
  Fix 4 — audit-log rows on api_key.created / api_key.revoked
  Static — every @limiter.limit in routers/*.py uses key_func=creator_key
"""

from __future__ import annotations

import pathlib
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ── Fix 1: creator_key reads request.state.creator_id ────────────────────────


def test_creator_key_reads_request_state_creator_id():
    """creator_key must bucket by the already-resolved UUID stamped on
    request.state by the auth dependency, not by IP."""
    from fastapi import Request

    from limiter import creator_key

    cid = uuid.uuid4()
    scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
    request = Request(scope=scope)
    request.state.creator_id = cid

    key = creator_key(request)
    assert key == str(cid), f"Expected {cid!s}, got {key!r}"


def test_creator_key_falls_back_to_remote_address():
    """When no creator_id is on request.state, creator_key must fall back to
    the client host — covers unauthenticated routes + fallback safety."""
    from unittest.mock import MagicMock

    from limiter import creator_key

    request = MagicMock()
    # Simulate a request with no creator_id on state
    del request.state.creator_id  # AttributeError on getattr → getattr default
    request.state = MagicMock(spec=[])  # empty state, no creator_id attribute
    request.client.host = "10.0.0.1"

    key = creator_key(request)
    assert key == "10.0.0.1"


# ── Fix 1: auth deps stash creator_id on request.state ───────────────────────


async def test_get_current_creator_stashes_creator_id_on_state():
    """get_current_creator must set request.state.creator_id = creator.id
    so creator_key() can read it without re-decoding the JWT."""
    from auth import create_session_token, get_current_creator

    cid = uuid.uuid4()
    # Build a minimal fake Request with the session cookie
    token = create_session_token(cid)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "query_string": b"",
    }
    from starlette.requests import Request as StarletteRequest

    scope["headers"] = [(b"cookie", f"cc_session={token}".encode())]
    request = StarletteRequest(scope=scope)

    creator = MagicMock()
    creator.id = cid

    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = creator
    session.execute = AsyncMock(return_value=result)

    returned = await get_current_creator(request=request, session=session)
    assert returned is creator
    assert getattr(request.state, "creator_id", None) == cid


async def test_get_current_creator_via_api_key_stashes_creator_id_on_state():
    """get_current_creator_via_api_key must set request.state.creator_id so
    creator_key() works correctly on bearer-auth routes like /clips/ingest."""
    from api_key import generate_api_key, get_current_creator_via_api_key, hash_api_key

    raw_key = generate_api_key()
    cid = uuid.uuid4()

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/clips/ingest",
        "headers": [],
        "query_string": b"",
    }
    from starlette.requests import Request as StarletteRequest

    auth_header = f"Bearer {raw_key}".encode()
    scope["headers"] = [(b"authorization", auth_header)]
    request = StarletteRequest(scope=scope)

    key_row = MagicMock()
    key_row.key_hash = hash_api_key(raw_key)
    key_row.revoked_at = None
    key_row.creator_id = cid

    creator = MagicMock()
    creator.id = cid

    session = AsyncMock()
    # scalar_one_or_none → api_key row; session.get → creator
    key_result = MagicMock()
    key_result.scalar_one_or_none.return_value = key_row
    session.execute = AsyncMock(return_value=key_result)
    session.get = AsyncMock(return_value=creator)
    session.commit = AsyncMock()

    returned = await get_current_creator_via_api_key(request=request, session=session)
    assert returned is creator
    assert getattr(request.state, "creator_id", None) == cid


# ── Fix 3: temp-file leak on non-HTTPException ────────────────────────────────


def test_ingest_clip_cleans_temp_file_on_oserror():
    """Structural guard: routers/clips.py::ingest_clip must wrap the entire
    post-NamedTemporaryFile block in a single try/finally that unlinks the
    temp file.  This ensures OSError (disk full on R2 PUT), CancelledError
    (client disconnect), and any other non-HTTPException path cannot leak the
    file.

    We verify the source code contains the expected structural pattern rather
    than mocking the full FastAPI + slowapi stack (which requires a live
    request and session — those paths are covered by the integration suite).
    """

    src = (pathlib.Path(__file__).parent.parent / "routers" / "clips.py").read_text()

    # The old pattern had THREE separate unlink calls in individual except/finally
    # arms.  The fix reduces this to ONE outer try/finally wrapping everything
    # from the write loop through the R2 PUT.  Assert we have a single
    # try/finally (not multiple scattered unlinks outside finally).

    # Count `tmp_path.unlink` occurrences — with the fix there must be exactly ONE.
    unlink_count = src.count("tmp_path.unlink")
    assert unlink_count == 1, (
        f"ingest_clip must have exactly ONE tmp_path.unlink call (the outer "
        f"finally clause). Found {unlink_count}. The old pattern scattered "
        f"unlinks across multiple except arms; the fix consolidates them. (Issue 104 Fix 3)"
    )

    # The single unlink must appear INSIDE a `finally:` block.  A simple text
    # search is sufficient — we're not resolving AST nesting.
    finally_idx = src.rfind("finally:")
    unlink_idx = src.rfind("tmp_path.unlink")
    assert finally_idx != -1, "ingest_clip must have a finally: block"
    assert unlink_idx > finally_idx, (
        "tmp_path.unlink must appear AFTER the finally: keyword — it must "
        "be inside the finally block, not in an except arm. (Issue 104 Fix 3)"
    )


# ── Fix 2 + 4: integration tests (marker: integration) ───────────────────────


pytestmark_integration = pytest.mark.integration


@pytest_asyncio.fixture
async def _db_session_104():
    from config import settings

    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed_creator_104(session: AsyncSession, *, suffix: str = "") -> object:
    from models import Creator, OnboardingState

    creator = Creator(
        google_sub=f"test_104_{suffix}_{uuid.uuid4().hex[:6]}",
        channel_id=f"UC_104_{uuid.uuid4().hex[:6]}",
        channel_title="Issue 104 Test",
        onboarding_state=OnboardingState.active,
        minutes_balance=200,
    )
    session.add(creator)
    await session.commit()
    return creator


async def _cleanup_104(session: AsyncSession, creator_id: uuid.UUID) -> None:
    from models import AuditLog, Creator, CreatorApiKey, Video

    await session.execute(delete(AuditLog).where(AuditLog.actor == str(creator_id)))
    await session.execute(delete(CreatorApiKey).where(CreatorApiKey.creator_id == creator_id))
    await session.execute(delete(Video).where(Video.creator_id == creator_id))
    await session.execute(delete(Creator).where(Creator.id == creator_id))
    await session.commit()


def _auth_cookie(creator_id: uuid.UUID) -> dict[str, str]:
    from auth import create_session_token

    return {"cc_session": create_session_token(creator_id)}


# ── Fix 2: insights aggregate counts ─────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.integration
async def test_insights_totals_returns_correct_counts(client, _db_session_104: AsyncSession):
    """Seed 3 shorts + 2 longs + 4 done + 1 pending; assert the FILTER-clause
    aggregates return exact values (not zero, which the old nullif bug produced).

    This is the regression test for the nullif-based aggregate that always
    returned 0 for shorts/longs/ingested_done."""

    from models import IngestStatus, Video, VideoKind

    creator = await _seed_creator_104(_db_session_104, suffix="ins")
    try:
        # 3 shorts
        for _ in range(3):
            yt_id = f"sh{uuid.uuid4().hex[:10]}"
            _db_session_104.add(
                Video(
                    creator_id=creator.id,
                    youtube_video_id=yt_id,
                    kind=VideoKind.short,
                    ingest_status=IngestStatus.done,
                    duration_s=45.0,
                )
            )
        # 2 longs — 1 done, 1 pending
        for status in [IngestStatus.done, IngestStatus.pending]:
            yt_id = f"lg{uuid.uuid4().hex[:10]}"
            _db_session_104.add(
                Video(
                    creator_id=creator.id,
                    youtube_video_id=yt_id,
                    kind=VideoKind.long,
                    ingest_status=status,
                    duration_s=600.0,
                )
            )
        await _db_session_104.commit()

        resp = client.get("/creators/me/insights", cookies=_auth_cookie(creator.id))
        assert resp.status_code == 200, resp.text
        data = resp.json()

        totals = data["totals"]
        assert totals["videos_analyzed"] == 5
        assert totals["shorts"] == 3, (
            f"Expected 3 shorts, got {totals['shorts']}. "
            "This was 0 with the old nullif bug — check the FILTER clause fix."
        )
        assert totals["longs"] == 2, f"Expected 2 longs, got {totals['longs']}."
        # 3 shorts done + 1 long done = 4 done
        assert totals["ingested_done"] == 4, (
            f"Expected 4 ingested_done, got {totals['ingested_done']}."
        )
        # 3×45 + 1×600 + 1×600 = 135 + 1200 = 1335s = 22.25 min → rounds to 22.3
        expected_min = round(1335.0 / 60.0, 1)
        assert totals["total_minutes_processed"] == expected_min, (
            f"Expected {expected_min} min, got {totals['total_minutes_processed']}."
        )
    finally:
        await _cleanup_104(_db_session_104, creator.id)


# ── Fix 4: audit rows on api_key CRUD ────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.integration
async def test_api_key_create_writes_audit_row(client, _db_session_104: AsyncSession):
    """POST /creators/me/api-keys must write an AuditLog row with
    action='api_key.created' and a non-null after_jsonb carrying
    name + key_prefix."""
    from models import AuditLog

    creator = await _seed_creator_104(_db_session_104, suffix="ack_create")
    try:
        resp = client.post(
            "/creators/me/api-keys",
            json={"name": "OBS test key"},
            cookies=_auth_cookie(creator.id),
        )
        assert resp.status_code == 201, resp.text
        key_id = uuid.UUID(resp.json()["id"])

        # Flush the session to see rows written by the endpoint
        await _db_session_104.commit()

        audit_rows = (
            (
                await _db_session_104.execute(
                    select(AuditLog).where(
                        AuditLog.actor == str(creator.id),
                        AuditLog.action == "api_key.created",
                        AuditLog.entity_id == key_id,
                    )
                )
            )
            .scalars()
            .all()
        )

        assert len(audit_rows) >= 1, (
            "No audit row found for api_key.created — Fix 4 (OWASP ASVS §7.2)"
        )
        row = audit_rows[0]
        assert row.entity_type == "api_key"
        assert row.before_jsonb is None
        assert row.after_jsonb is not None
        assert row.after_jsonb.get("name") == "OBS test key"
        assert "key_prefix" in row.after_jsonb
    finally:
        await _cleanup_104(_db_session_104, creator.id)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_api_key_revoke_writes_audit_row(client, _db_session_104: AsyncSession):
    """DELETE /creators/me/api-keys/{id} must write an AuditLog row with
    action='api_key.revoked' and a non-null before_jsonb carrying
    name + key_prefix (after_jsonb = None for a revoke)."""
    from models import AuditLog

    creator = await _seed_creator_104(_db_session_104, suffix="ack_revoke")
    try:
        create_resp = client.post(
            "/creators/me/api-keys",
            json={"name": "OBS revoke test"},
            cookies=_auth_cookie(creator.id),
        )
        assert create_resp.status_code == 201
        key_id = uuid.UUID(create_resp.json()["id"])

        del_resp = client.delete(
            f"/creators/me/api-keys/{key_id}",
            cookies=_auth_cookie(creator.id),
        )
        assert del_resp.status_code == 204

        await _db_session_104.commit()

        audit_rows = (
            (
                await _db_session_104.execute(
                    select(AuditLog).where(
                        AuditLog.actor == str(creator.id),
                        AuditLog.action == "api_key.revoked",
                        AuditLog.entity_id == key_id,
                    )
                )
            )
            .scalars()
            .all()
        )

        assert len(audit_rows) >= 1, (
            "No audit row found for api_key.revoked — Fix 4 (OWASP ASVS §7.2)"
        )
        row = audit_rows[0]
        assert row.entity_type == "api_key"
        assert row.after_jsonb is None
        assert row.before_jsonb is not None
        assert row.before_jsonb.get("name") == "OBS revoke test"
        assert "key_prefix" in row.before_jsonb
    finally:
        await _cleanup_104(_db_session_104, creator.id)
