"""Unit tests for the async engine configuration (Issue 58).

No DB connection required — these introspect the engine object, so they run in the
default (non-integration) suite and guard the PgBouncer-compatibility settings that
production depends on but CI's direct-Postgres tests cannot exercise.
"""

import db


def test_prepared_statements_disabled_for_pgbouncer() -> None:
    # psycopg3 server-side prepared statements are incompatible with PgBouncer
    # transaction-pooling mode (docs/DEPLOYMENT.md). prepare_threshold=None disables them.
    assert db._CONNECT_ARGS == {"prepare_threshold": None}


def test_pool_ceiling_stays_under_pgbouncer_sidecar() -> None:
    # pool_size + max_overflow must stay <= the 25-conn PgBouncer sidecar.
    assert db._POOL_SIZE + db._MAX_OVERFLOW <= 25
    pool = db.engine.sync_engine.pool
    assert pool.size() == db._POOL_SIZE
    assert pool._max_overflow == db._MAX_OVERFLOW


def test_pool_recycle_set() -> None:
    # Connections are recycled so a Postgres/PgBouncer restart can't strand a stale handle.
    assert db.engine.sync_engine.pool._recycle == db._POOL_RECYCLE_S


def test_recreate_engine_reentry_guard_returns_early() -> None:
    # Concurrent prefork signals must not race through pool teardown (Issue 123).
    # The guard is a non-blocking lock (Issue 352) so check-and-set is atomic.
    original_engine = db.engine
    assert db._recreate_lock.acquire(blocking=False)
    try:
        db.recreate_engine()  # must be a no-op while another caller holds the lock
        assert db.engine is original_engine  # engine reference unchanged
    finally:
        db._recreate_lock.release()


def test_recreate_engine_never_exposes_disposed_engine() -> None:
    # Build-then-swap (Issue 352): after recreate_engine the module globals are
    # fresh objects and the sessionmaker is bound to the new engine, so no
    # reader can ever pick up a disposed engine via db.AsyncSessionLocal.
    pre_engine = db.engine
    pre_admin = db.admin_engine
    db.recreate_engine()
    try:
        assert db.engine is not pre_engine
        assert db.admin_engine is not pre_admin
        assert db.AsyncSessionLocal.kw["bind"] is db.engine
        assert db.AdminSessionLocal.kw["bind"] is db.admin_engine
    finally:
        db.recreate_engine()  # rebind again so no state leaks into later tests


# ── tenant_session (Issue 231) ─────────────────────────────────────────────────


async def test_tenant_session_stamps_creator_id_before_yield() -> None:
    """The helper must stamp session.info BEFORE any statement runs, so the
    after_begin listener emits the app.creator_id GUC on the first transaction."""
    import uuid
    from unittest.mock import AsyncMock, MagicMock, patch

    cid = uuid.uuid4()
    session = MagicMock()
    session.info = {}
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    with patch.object(db, "AsyncSessionLocal", MagicMock(return_value=session)):
        async with db.tenant_session(cid) as s:
            assert s is session
            assert s.info["creator_id"] == str(cid)


async def test_tenant_session_uses_app_factory_not_admin() -> None:
    """tenant_session must run on the RLS-gated app factory — never BYPASSRLS."""
    import uuid
    from unittest.mock import AsyncMock, MagicMock, patch

    session = MagicMock()
    session.info = {}
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    app_factory = MagicMock(return_value=session)
    admin_factory = MagicMock()

    with (
        patch.object(db, "AsyncSessionLocal", app_factory),
        patch.object(db, "AdminSessionLocal", admin_factory),
    ):
        async with db.tenant_session(str(uuid.uuid4())):
            pass

    app_factory.assert_called_once()
    admin_factory.assert_not_called()
