"""
Set test env vars before any app module is imported.
These are fake credentials for unit tests only — never used against real external services.
Real credentials come from .env when running integration tests or against live services.
"""

import os
import time
import uuid

from cryptography.fernet import Fernet

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-real")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://creatorclip:dev_password@localhost:5432/creatorclip",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "test-client-id")
os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("OAUTH_REDIRECT_URI", "http://localhost:8000/auth/callback")
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-32-bytes-minimum-!")
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:8000")
os.environ.setdefault("LOG_DIR", "")  # disable file logging in tests (/app/logs is Docker-only)

import pytest
from fastapi.testclient import TestClient

from main import app


def pytest_configure(config: pytest.Config) -> None:
    """Fail fast, with one clear message, if a required backing service is down.

    Redis guard: the suite needs a live Redis — the slowapi rate limiter has no
    in-memory fallback by design (a fail-open limiter would be a prod cost/abuse
    risk). When Redis is missing this otherwise surfaces as dozens of opaque 500s
    from every limited/health route, which is what masked a mid-session Redis death
    once (see docs/OFF_COURSE_BUGS.md, 2026-05-29). This guard runs wherever pytest
    runs — GitHub Actions, Claude-on-web, and local — so the failure is always
    legible. Provision the service with `scripts/dev_session_setup.sh` (or
    `docker compose up -d redis`).

    Postgres guard (Issue 267): only checked when the integration marker is active
    (or DATABASE_URL is explicitly overridden from the default dev value), to avoid
    breaking the unit lane where Postgres is deliberately absent.
    """
    import socket
    from urllib.parse import urlparse

    # ── Redis fail-fast ────────────────────────────────────────────────────────
    redis_url = urlparse(os.environ["REDIS_URL"])
    redis_host = redis_url.hostname or "localhost"
    redis_port = redis_url.port or 6379
    try:
        with socket.create_connection((redis_host, redis_port), timeout=2):
            pass
    except OSError as exc:
        raise pytest.UsageError(
            f"Redis is not reachable at {redis_host}:{redis_port} ({exc}). "
            "The test suite requires a live Redis (the rate limiter has no "
            "in-memory fallback). Start it with `scripts/dev_session_setup.sh`, "
            "`docker compose up -d redis`, or "
            "`redis-server --daemonize yes --save '' --appendonly no`."
        ) from exc

    # ── Postgres fail-fast (Issue 267) ────────────────────────────────────────
    # Only probe Postgres when the integration marker is requested, so the unit
    # lane (which mocks/skips DB access) is not broken by a missing Postgres.
    _DEFAULT_DB = "postgresql+psycopg://creatorclip:dev_password@localhost:5432/creatorclip"
    db_url_raw = os.environ.get("DATABASE_URL", _DEFAULT_DB)
    integration_requested = False
    try:
        marker_expr = config.getoption("-m", default="")
        # Strip negations first: the DEFAULT unit lane runs with
        # `-m "not integration and not quarantine"`, whose text contains the
        # substring "integration" — a bare `"integration" in marker_expr` check
        # therefore fired the Postgres guard on EVERY unit run, breaking the unit
        # lane on any box without Postgres (masked everywhere Postgres is always
        # up: CI/Docker/prod-VM). Only treat integration as *requested* when it is
        # positively selected. (OCB 2026-06-24)
        positive_expr = marker_expr.replace("not integration", "")
        integration_requested = bool(marker_expr and "integration" in positive_expr)
    except (ValueError, AttributeError):
        pass
    db_overridden = db_url_raw != _DEFAULT_DB

    if integration_requested or db_overridden:
        db_url = urlparse(db_url_raw.replace("+psycopg", "").replace("+asyncpg", ""))
        pg_host = db_url.hostname or "localhost"
        pg_port = db_url.port or 5432
        try:
            with socket.create_connection((pg_host, pg_port), timeout=3):
                pass
        except OSError as exc:
            raise pytest.UsageError(
                f"Postgres is not reachable at {pg_host}:{pg_port} ({exc}). "
                "Integration tests require a live Postgres. Start it with "
                "`docker compose up -d postgres` or ensure DATABASE_URL points "
                "to an accessible instance."
            ) from exc


@pytest.fixture(scope="session")
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _isolate_app_state(client):
    """Function-scoped hygiene against cross-test state leaks in a single-process run:

    1. ``app.dependency_overrides`` — ~10 test modules set overrides without a
       finally-clear.
    2. The shared session-scoped TestClient's **cookie jar** — passing per-request
       ``cookies=`` to the shared ``client`` leaks the cookie onto its jar in httpx2
       (the StarletteDeprecationWarning), so an auth cookie set by an earlier test
       authenticates later requests.

    3. The slowapi rate-limiter's Redis buckets — the limiter has no in-memory fallback
       and its Redis state persists across modules (and even across pytest invocations
       against the same Redis), so accumulated request counts intermittently tripped a
       spurious 429 in a later test (e.g. ``test_data_export``/``test_issue_113`` — a
       long-standing flake whose victim moved run-to-run). ``limiter.reset()`` clears
       only the limiter-prefixed keys (RedisStorage.reset()), so each test starts with
       empty buckets while dedicated rate-limit tests still trip within their own test.

    All three intermittently poisoned later tests — e.g. ``test_clip_counts_requires_auth``
    saw a leaked auth cookie, so ``get_current_creator`` passed and the endpoint hit a
    real (absent) Postgres → 500 instead of the expected 401. Clearing all three before
    AND after every test makes execution order irrelevant. (OCB 2026-06-24)"""
    import contextlib

    from limiter import limiter

    import flags

    app.dependency_overrides.clear()
    client.cookies.clear()
    # 4. The feature-flag TTL cache (flags._cache, 30s) is process-global AND its
    #    misses open a DB session inside gated code paths, which shifts tests that
    #    script AdminSessionLocal with a fixed session sequence (test_publish.py's
    #    two-session iterator broke under CI's randomized order — PR #41,
    #    2026-07-02). Reset, then PRIME every known flag with its env default so
    #    unit tests never take the DB branch. test_flags.py's own autouse fixture
    #    re-clears the cache after this one, so its cold-cache tests still work.
    flags._reset_cache()
    _now = time.monotonic()
    for _key in flags.KNOWN_FLAGS:
        flags._cache[_key] = (_now, flags.env_default(_key))
    with contextlib.suppress(Exception):  # best-effort; never fail a test on limiter cleanup
        limiter.reset()
    yield
    app.dependency_overrides.clear()
    client.cookies.clear()
    flags._reset_cache()


@pytest.fixture(autouse=True)
def _clean_db_for_integration(request):
    """Integration-lane DB isolation: give every ``integration`` test a clean
    domain DB so order-dependent tests are deterministic instead of silently
    coupled to rows other tests leave behind.

    Why TRUNCATE and not a rolled-back transaction: the code under test manages
    its own sessions and commits (the Celery task helpers open their own
    ``AdminSessionLocal`` and ``commit()``), so a transaction-rollback fixture on
    the test's session can't isolate writes that already committed on a different
    connection. Tests like analytics-fairness (iterates *all* creators),
    per-creator-median, and outcome-polling read global table state, so leftover
    creators from earlier tests flipped their results (e.g. "Starting analytics
    refresh for 59 creator(s)" when the test seeded 5). A clean slate before each
    integration test makes execution order irrelevant — the same guarantee the
    ``_isolate_app_state`` fixture gives for app/limiter state.

    ``creators ... CASCADE`` clears every creator-FK'd domain table; ``audit_log``
    and ``event_log`` are not FK'd to creators (``entity_id`` is a bare UUID) so
    they're listed explicitly. Unit-lane tests (no ``integration`` marker; DB
    mocked/absent) are untouched.
    """
    if request.node.get_closest_marker("integration") is None:
        yield
        return
    import psycopg

    dsn = os.environ["DATABASE_URL"].replace("+psycopg", "").replace("+asyncpg", "")
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE creators, audit_log, event_logs RESTART IDENTITY CASCADE")
        # Clear any session-level advisory lock leaked by an earlier test. The Beat
        # task helpers (poll_clip_outcomes, refresh_youtube_analytics, …) take
        # ``pg_try_advisory_lock`` on their AdminSessionLocal connection and release
        # it in a ``finally``; if that release is skipped (e.g. the rollback on a
        # dead-event-loop connection raises), the lock survives on the pooled
        # backend and the NEXT test's task logs "advisory lock held — skipping" and
        # no-ops, so its assertions fail. Terminating the holding backend frees the
        # lock; both engines use pool_pre_ping so the dead connection is transparently
        # replaced on next checkout. (Tracked as a product concern in OFF_COURSE_BUGS.)
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_locks "
            "WHERE locktype = 'advisory' AND pid <> pg_backend_pid()"
        )
    yield


@pytest.fixture()
def creator_cookie() -> dict[str, str]:
    """Issue 267: per-test session cookie with a unique creator ID.

    Returns a cookie dict suitable for use in TestClient(cookies=...) or
    client.get(..., cookies=...) calls. Each invocation generates a fresh UUID
    so tests never share a slowapi rate-limit bucket (the rate limiter keys on
    creator_id, not IP, via the creator_key extractor — see Issue 104).

    Usage::

        def test_something(creator_cookie):
            with TestClient(app, cookies=creator_cookie) as c:
                ...

    For tests that also need dependency_overrides for get_current_creator, use
    override_current_creator from tests._helpers alongside this fixture.
    """
    from auth import SESSION_COOKIE, create_session_token

    creator_id = uuid.uuid4()
    return {SESSION_COOKIE: create_session_token(creator_id)}


# override_current_creator helper lives in tests/_helpers.py — importable from any test.
