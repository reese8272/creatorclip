"""Guard the Render-injected Postgres DSN scheme normalization (Render beta).

The async engine (db.py) requires `postgresql+psycopg://`. Render's managed
Postgres `connectionString` injects a bare `postgresql://`, so config must
upgrade the scheme at load — otherwise the app crashes at startup selecting the
sync psycopg2 driver. This is the single highest-likelihood Render break, so it
gets a focused test.
"""

import pytest

from config import _normalize_async_pg_dsn


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Render injects a bare libpq DSN — must be upgraded to the async driver.
        (
            "postgresql://user:pw@host:5432/db",
            "postgresql+psycopg://user:pw@host:5432/db",
        ),
        # Legacy `postgres://` scheme (some providers still emit it).
        (
            "postgres://user:pw@host:5432/db",
            "postgresql+psycopg://user:pw@host:5432/db",
        ),
        # Already-qualified async DSN — passed through untouched (idempotent).
        (
            "postgresql+psycopg://user:pw@host:5432/db",
            "postgresql+psycopg://user:pw@host:5432/db",
        ),
        # A different async driver is left alone, not clobbered to psycopg.
        (
            "postgresql+asyncpg://user:pw@host:5432/db",
            "postgresql+asyncpg://user:pw@host:5432/db",
        ),
    ],
)
def test_normalize_async_pg_dsn(raw: str, expected: str) -> None:
    assert _normalize_async_pg_dsn(raw) == expected


def test_normalize_passthrough_for_empty_and_none() -> None:
    # Optional DSN fields (DATABASE_MIGRATION_URL, LOGS_DATABASE_URL) may be None
    # or empty — normalization must not synthesize a scheme for them.
    assert _normalize_async_pg_dsn(None) is None
    assert _normalize_async_pg_dsn("") == ""
