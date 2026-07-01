"""
Integration tests for Issue 79 — Postgres Row-Level Security tenant isolation.

The test strategy assumes the surrounding test infra (docker-compose dev /
integration.yml CI) connects as a SUPERUSER. Within each test we issue
``SET LOCAL ROLE creatorclip_app`` so policies are evaluated under the
non-BYPASSRLS app role, then assert that an unfiltered ``SELECT *`` of every
tenant-owned table returns zero rows belonging to Creator B while Creator A
is in scope via ``set_config('app.creator_id', :cid, true)`` (the parameterized
equivalent of ``SET LOCAL`` — utility ``SET`` doesn't accept bind params).

This is the structural property RLS is purchased to provide: the application
can forget the ``WHERE creator_id = :id`` predicate and the database still
refuses to leak cross-tenant rows.

Setup / teardown runs as the SUPERUSER (no SET ROLE), so the fixtures can
seed both creators without RLS interfering with the seeding writes.
"""

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from models import (
    AudienceActivity,
    Clip,
    ClipFeedback,
    ClipFormat,
    Creator,
    CreatorDna,
    CreatorInsight,
    Demographics,
    DnaEmbedding,
    DnaEmbeddingKind,
    DnaStatus,
    FeedbackAction,
    ImprovementBrief,
    ImprovementBriefStatus,
    IngestStatus,
    InsightType,
    MinuteDeduction,
    MinutePack,
    OnboardingState,
    PreferenceModel,
    RenderStatus,
    Usage,
    Video,
    VideoKind,
    YoutubeToken,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def admin_engine():
    """SUPERUSER engine used for fixture setup / teardown."""
    eng = create_async_engine(settings.database_migration_url, pool_pre_ping=True)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db_session(admin_engine):
    factory = async_sessionmaker(admin_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


async def _seed_creator(session: AsyncSession, *, label: str) -> Creator:
    creator = Creator(
        google_sub=f"test_rls_{label}_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_rls_{label}_{uuid.uuid4().hex[:6]}",
        channel_title=f"RLS Test {label}",
        onboarding_state=OnboardingState.active,
        minutes_balance=100,
    )
    session.add(creator)
    await session.commit()
    return creator


async def _seed_all_tenant_rows(session: AsyncSession, creator_id: uuid.UUID) -> None:
    """Seed one row in every tenant-owned table for the given creator."""
    now = datetime.now(UTC)
    # Parent rows first so FKs resolve.
    video = Video(
        creator_id=creator_id,
        youtube_video_id=f"yt_{uuid.uuid4().hex[:8]}",
        title="RLS fixture",
        kind=VideoKind.long,
        duration_s=120.0,
        ingest_status=IngestStatus.done,
    )
    session.add(video)
    await session.flush()
    clip = Clip(
        video_id=video.id,
        creator_id=creator_id,
        start_s=10.0,
        end_s=50.0,
        peak_s=30.0,
        format=ClipFormat.short,
        render_status=RenderStatus.done,
    )
    session.add(clip)
    await session.flush()

    session.add(
        YoutubeToken(
            creator_id=creator_id,
            access_token_encrypted="x",
            refresh_token_encrypted="x",
            expires_at=now,
            scope="",
        )
    )
    session.add(
        AudienceActivity(
            creator_id=creator_id, day_of_week=1, hour=12, activity_index=0.5, fetched_at=now
        )
    )
    session.add(Demographics(creator_id=creator_id, payload_jsonb={}, fetched_at=now))
    session.add(
        CreatorDna(
            creator_id=creator_id,
            version=1,
            brief_text="x",
            patterns_jsonb={},
            status=DnaStatus.draft,
        )
    )
    session.add(
        DnaEmbedding(
            creator_id=creator_id,
            kind=DnaEmbeddingKind.pattern,
            embedding=[0.0] * 1024,
            ref_jsonb={},
        )
    )
    session.add(
        ClipFeedback(
            clip_id=clip.id,
            creator_id=creator_id,
            action=FeedbackAction.upvote,
        )
    )
    session.add(
        PreferenceModel(
            creator_id=creator_id,
            version=1,
            weights_blob=b"x",
            updated_at=now,
        )
    )
    session.add(
        MinutePack(
            creator_id=creator_id,
            pack_id="trial",
            minutes_granted=60,
            price_cents=0,
            reason="trial",
        )
    )
    session.add(
        MinuteDeduction(
            video_id=video.id,
            creator_id=creator_id,
            minutes_deducted=2,
            duration_s=120.0,
        )
    )
    session.add(Usage(creator_id=creator_id, period=now.strftime("%Y-%m")))
    # The two tables migration 0010 missed; their RLS landed in 0038.
    session.add(
        ImprovementBrief(
            creator_id=creator_id,
            status=ImprovementBriefStatus.ready,
            brief_text="x",
        )
    )
    session.add(
        CreatorInsight(
            creator_id=creator_id,
            insight_type=InsightType.recommendation,
            content="x",
        )
    )
    await session.commit()


async def _cleanup(session: AsyncSession, creator_ids: list[uuid.UUID]) -> None:
    """Clean up every tenant table for the given creators. Order matters for
    FK constraints (clips before videos, etc.). MinutePack and Usage don't
    cascade from creator delete in the model; clear explicitly."""
    for table_model in (
        ClipFeedback,
        Clip,
        DnaEmbedding,
        CreatorDna,
        AudienceActivity,
        Demographics,
        PreferenceModel,
        Usage,
        MinutePack,
        MinuteDeduction,
        Video,
        YoutubeToken,
    ):
        await session.execute(delete(table_model).where(table_model.creator_id.in_(creator_ids)))
    await session.execute(delete(Creator).where(Creator.id.in_(creator_ids)))
    await session.commit()


# The tenant-owned tables with direct creator_id columns. The first 12 match
# migration 0010's _TENANT_TABLES; improvement_briefs + creator_insights were the
# two stragglers that 0010 missed (added their RLS policy in migration 0038 after the
# Issue 340b sweep found them unprotected — see docs/OFF_COURSE_BUGS.md 2026-06-30).
_TENANT_TABLES = (
    "audience_activity",
    "clip_feedback",
    "clips",
    "creator_dna",
    "creator_insights",
    "demographics",
    "dna_embeddings",
    "improvement_briefs",
    "minute_deductions",
    "minute_packs",
    "preference_models",
    "usage",
    "videos",
    "youtube_tokens",
)


@pytest.mark.asyncio
async def test_rls_blocks_cross_tenant_unfiltered_select(admin_engine, db_session):
    """For every tenant-owned table, an unfiltered ``SELECT *`` issued under
    the ``creatorclip_app`` role with Creator A's GUC set returns zero rows
    belonging to Creator B.

    This is the property RLS is purchased to provide: even when the
    application forgets ``WHERE creator_id = :id``, the database refuses to
    return cross-tenant rows.
    """
    creator_a = await _seed_creator(db_session, label="A")
    creator_b = await _seed_creator(db_session, label="B")

    try:
        await _seed_all_tenant_rows(db_session, creator_a.id)
        await _seed_all_tenant_rows(db_session, creator_b.id)

        # Now run the RLS visibility test inside a single transaction on a
        # fresh connection. SET LOCAL ROLE makes the role switch transaction-
        # scoped so the surrounding fixture teardown still runs as SUPERUSER.
        factory = async_sessionmaker(admin_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            await s.execute(text("SET LOCAL ROLE creatorclip_app"))
            # SET LOCAL doesn't accept bind parameters — use the set_config()
            # function form, matching db.py's after_begin listener.
            await s.execute(
                text("SELECT set_config('app.creator_id', :cid, true)"),
                {"cid": str(creator_a.id)},
            )

            for table in _TENANT_TABLES:
                rows = (await s.execute(text(f"SELECT creator_id FROM {table}"))).all()
                row_creator_ids = {r[0] for r in rows}
                assert creator_b.id not in row_creator_ids, (
                    f"RLS leak on {table}: row owned by creator B visible to creator A"
                )
                # Creator A's row may or may not appear depending on FK chain,
                # but it must never be that B is visible. The minimum guarantee
                # tested here is non-leakage, which is what RLS provides.
    finally:
        await _cleanup(db_session, [creator_a.id, creator_b.id])


@pytest.mark.asyncio
async def test_get_current_creator_sets_guc_for_same_transaction_write(admin_engine, db_session):
    """Regression for Issue 344 (prod upload 500s after the RLS role split).

    The real ``auth.get_current_creator`` resolves the creator with a SELECT that
    auto-begins the request transaction — ``after_begin`` fires before
    ``session.info['creator_id']`` is set, so it emits no GUC. The endpoint's
    writes commit in that SAME transaction, so the dependency must set
    ``app.creator_id`` on the live transaction itself; otherwise the INSERT hits
    the RLS ``WITH CHECK`` with the GUC unset and 500s.

    The prior RLS tests set the GUC by hand (masking this gap). This one drives
    the real dependency end-to-end under the ``creatorclip_app`` role and asserts
    an INSERT into a tenant table then succeeds in the same transaction.
    """
    from starlette.requests import Request

    from auth import SESSION_COOKIE, create_session_token, get_current_creator

    creator = await _seed_creator(db_session, label="guc")

    try:
        factory = async_sessionmaker(admin_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            # Drop to the non-BYPASSRLS app role for the rest of this transaction,
            # exactly as the prod app connects. This also auto-begins T1.
            await s.execute(text("SET LOCAL ROLE creatorclip_app"))

            token = create_session_token(creator.id)
            scope = {
                "type": "http",
                "headers": [(b"cookie", f"{SESSION_COOKIE}={token}".encode())],
                "state": {},
            }
            request = Request(scope)

            # Real dependency: bootstrap SELECT + GUC injection on the live txn.
            resolved = await get_current_creator(request=request, session=s)
            assert resolved.id == creator.id

            # The write that 500'd in prod: an INSERT into a tenant-owned table
            # committed in the SAME transaction the auth SELECT began. Passes
            # only because the GUC is now set on this transaction.
            s.add(
                Video(
                    creator_id=creator.id,
                    kind=VideoKind.long,
                    duration_s=300.0,
                    source_uri="s3://test/source/guc.mp4",
                    ingest_status=IngestStatus.pending,
                )
            )
            await s.flush()
    finally:
        await _cleanup(db_session, [creator.id])


@pytest.mark.asyncio
async def test_rls_creators_table_remains_visible_for_auth_bootstrap(admin_engine, db_session):
    """The ``creators`` table is exempt from RLS (Issue 56) so the FastAPI auth
    dependency can resolve ``current_creator`` from the JWT before
    ``app.creator_id`` has been set. Under the ``creatorclip_app`` role with no
    GUC set, a lookup by id must still return the row."""
    creator = await _seed_creator(db_session, label="auth")

    try:
        factory = async_sessionmaker(admin_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            await s.execute(text("SET LOCAL ROLE creatorclip_app"))
            # No set_config('app.creator_id', ...) — simulating bootstrap-auth.
            result = await s.execute(select(Creator).where(Creator.id == creator.id))
            row = result.scalar_one_or_none()
        assert row is not None, "creators table must NOT be gated by RLS"
        assert row.id == creator.id
    finally:
        await _cleanup(db_session, [creator.id])


@pytest.mark.asyncio
async def test_rls_deny_by_default_unset_context(admin_engine, db_session):
    """Issue 340b — deny-by-default proof: with ``app.creator_id`` UNSET under the
    ``creatorclip_app`` role, every RLS-gated tenant table returns zero rows.

    ``current_setting('app.creator_id', true)`` returns NULL when the GUC has
    never been set. The RLS predicate
    ``creator_id = current_setting('app.creator_id', true)::uuid``
    evaluates to ``creator_id = NULL`` which is always false/NULL in SQL — so
    no rows pass the policy, regardless of what data exists in the table.

    This is the structural guarantee that the application CANNOT accidentally
    leak cross-tenant data if it forgets to set the GUC before querying.
    """
    # Seed ONE creator with data in all tenant tables so we have rows to *not* see.
    creator = await _seed_creator(db_session, label="deny_default")
    try:
        await _seed_all_tenant_rows(db_session, creator.id)

        factory = async_sessionmaker(admin_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            await s.execute(text("SET LOCAL ROLE creatorclip_app"))
            # Deliberately do NOT set app.creator_id — simulating missing GUC context.
            # Verify current_setting returns NULL (the ''missing_ok'' form).
            guc_val = (
                await s.execute(text("SELECT current_setting('app.creator_id', true)"))
            ).scalar()
            assert guc_val is None or guc_val == "", (
                "app.creator_id should be unset at this point in the test"
            )

            for table in _TENANT_TABLES:
                rows = (await s.execute(text(f"SELECT creator_id FROM {table}"))).all()  # noqa: S608
                assert len(rows) == 0, (
                    f"RLS deny-by-default FAILED on {table}: "
                    f"{len(rows)} rows visible with no app.creator_id GUC set. "
                    "This means the tenant_isolation policy is not enforced when the "
                    "GUC is unset — a data-leakage risk."
                )
    finally:
        await _cleanup(db_session, [creator.id])


@pytest.mark.asyncio
async def test_oauth_callback_tenant_write_requires_guc(admin_engine, db_session):
    """Regression for the 2026-06-30 prod sign-in outage (oauth_failed).

    The OAuth ``/callback`` runs PRE-auth: it creates the creator (``creators`` is
    RLS-exempt) and then, in the SAME transaction, writes RLS-FORCED tenant tables
    (``youtube_tokens`` via store_or_update_tokens, ``minute_packs`` via
    grant_minutes). There is no ``get_current_creator`` in this path, so db.py's
    ``after_begin`` listener never sets ``app.creator_id``. After the Issue 343 role
    split (app connects as non-BYPASSRLS ``creatorclip_app``) those writes hit the
    ``tenant_isolation`` WITH CHECK with the GUC unset → SQLSTATE 42501 →
    ProgrammingError → swallowed as ``oauth_failed``, breaking every sign-in.

    The fix: ``_exchange_and_persist`` emits ``set_config('app.creator_id', creator.id)``
    after the flush. This test proves the GUC is load-bearing — the write FAILS without
    it and SUCCEEDS with it — under the real role.
    """
    from sqlalchemy.exc import DBAPIError

    creator = await _seed_creator(db_session, label="oauth_cb")

    def _token() -> YoutubeToken:
        return YoutubeToken(
            creator_id=creator.id,
            access_token_encrypted="enc-at",
            refresh_token_encrypted="enc-rt",
            scope="https://www.googleapis.com/auth/youtube.readonly",
            expires_at=datetime.now(UTC),
        )

    factory = async_sessionmaker(admin_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        # (a) WITHOUT the GUC: the pre-fix behaviour — RLS rejects the insert.
        async with factory() as s:
            await s.execute(text("SET LOCAL ROLE creatorclip_app"))
            s.add(_token())
            with pytest.raises(DBAPIError) as exc_info:
                await s.flush()
            assert "row-level security" in str(exc_info.value).lower(), (
                f"expected an RLS violation, got: {exc_info.value}"
            )

        # (b) WITH the GUC set to the new creator's id (what the fix does): succeeds.
        async with factory() as s:
            await s.execute(text("SET LOCAL ROLE creatorclip_app"))
            await s.execute(
                text("SELECT set_config('app.creator_id', :cid, true)"),
                {"cid": str(creator.id)},
            )
            s.add(_token())
            await s.flush()  # must not raise
    finally:
        await _cleanup(db_session, [creator.id])
