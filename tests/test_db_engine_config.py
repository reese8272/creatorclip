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
    original_flag = db._recreate_in_progress
    original_engine = db.engine
    db._recreate_in_progress = True
    try:
        db.recreate_engine()  # must be a no-op when already in progress
        assert db.engine is original_engine  # engine reference unchanged
    finally:
        db._recreate_in_progress = original_flag
