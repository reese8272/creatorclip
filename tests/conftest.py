"""
Set test env vars before any app module is imported.
These are fake credentials for unit tests only — never used against real external services.
Real credentials come from .env when running integration tests or against live services.
"""

import os

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

    The suite needs a live Redis — the slowapi rate limiter has no in-memory
    fallback by design (a fail-open limiter would be a prod cost/abuse risk). When
    Redis is missing this otherwise surfaces as dozens of opaque 500s from every
    limited/health route, which is what masked a mid-session Redis death once (see
    docs/OFF_COURSE_BUGS.md, 2026-05-29). This guard runs wherever pytest runs —
    GitHub Actions, Claude-on-web, and local — so the failure is always legible.
    Provision the service with `scripts/dev_session_setup.sh` (or `docker compose
    up -d redis`).
    """
    import socket
    from urllib.parse import urlparse

    url = urlparse(os.environ["REDIS_URL"])
    host, port = url.hostname or "localhost", url.port or 6379
    try:
        with socket.create_connection((host, port), timeout=2):
            return
    except OSError as exc:
        raise pytest.UsageError(
            f"Redis is not reachable at {host}:{port} ({exc}). The test suite "
            "requires a live Redis (the rate limiter has no in-memory fallback). "
            "Start it with `scripts/dev_session_setup.sh`, `docker compose up -d "
            "redis`, or `redis-server --daemonize yes --save '' --appendonly no`."
        ) from exc


@pytest.fixture(scope="session")
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


# override_current_creator helper lives in tests/_helpers.py — importable from any test.
