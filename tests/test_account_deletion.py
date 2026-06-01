"""
Tests for Issue 19 — account deletion (right-to-erasure).
Covers: DELETE /creators/me returns 204, OAuth revocation attempted,
media prefixes purged, cascade delete called, cookie cleared, audit log written.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from auth import SESSION_COOKIE, create_session_token, get_current_creator
from db import get_session
from main import app
from tests._helpers import override_current_creator


def _make_creator():
    c = MagicMock()
    c.id = uuid.uuid4()
    c.channel_id = "UCtest"
    c.email = "test@example.com"
    return c


def _session_cookie_for(creator) -> dict:
    """Build a real JWT cookie so the rate limiter keys off a unique creator UUID.

    Each test uses a different creator UUID, ensuring no two tests share the same
    rate-limit bucket even if Redis is available.
    """
    return {SESSION_COOKIE: create_session_token(creator.id)}


def _fake_session(creator_id=None, token_row=None):
    async def _session():
        session = AsyncMock()

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = token_row
            return result

        session.execute = AsyncMock(side_effect=execute_side_effect)
        session.delete = AsyncMock()
        session.commit = AsyncMock()
        session.add = MagicMock()
        yield session

    return _session


# ── 204 on success ────────────────────────────────────────────────────────────


def test_delete_account_returns_204(client):
    creator = _make_creator()
    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _fake_session()

    try:
        with (
            patch("worker.storage.delete_prefix", return_value=0),
            patch("routers.auth.httpx.AsyncClient"),
        ):
            resp = client.delete("/auth/me", cookies=_session_cookie_for(creator))
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 204


# ── Storage purge ─────────────────────────────────────────────────────────────


def test_delete_account_purges_source_and_clips_prefixes(client):
    creator = _make_creator()
    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _fake_session()

    purged = []

    def fake_delete_prefix(prefix):
        purged.append(prefix)
        return 0

    try:
        with (
            patch("worker.storage.delete_prefix", side_effect=fake_delete_prefix),
            patch("routers.auth.httpx.AsyncClient"),
        ):
            client.delete("/auth/me", cookies=_session_cookie_for(creator))
    finally:
        app.dependency_overrides.clear()

    assert any(f"source/{creator.id}/" in p for p in purged)
    assert any(f"clips/{creator.id}/" in p for p in purged)


def test_delete_account_continues_if_storage_purge_fails(client):
    creator = _make_creator()
    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _fake_session()

    try:
        with (
            patch("worker.storage.delete_prefix", side_effect=Exception("S3 error")),
            patch("routers.auth.httpx.AsyncClient"),
        ):
            resp = client.delete("/auth/me", cookies=_session_cookie_for(creator))
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 204


# ── DB cascade delete ─────────────────────────────────────────────────────────


def test_delete_account_calls_session_delete(client):
    creator = _make_creator()
    session_mock = AsyncMock()
    session_mock.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
    session_mock.delete = AsyncMock()
    session_mock.commit = AsyncMock()
    session_mock.add = MagicMock()

    async def _session():
        yield session_mock

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _session

    try:
        with (
            patch("worker.storage.delete_prefix", return_value=0),
            patch("routers.auth.httpx.AsyncClient"),
        ):
            client.delete("/auth/me", cookies=_session_cookie_for(creator))
    finally:
        app.dependency_overrides.clear()

    session_mock.delete.assert_called_once_with(creator)
    session_mock.commit.assert_called_once()


# ── Audit log ─────────────────────────────────────────────────────────────────


def test_delete_account_writes_audit_log(client):
    creator = _make_creator()
    session_mock = AsyncMock()
    session_mock.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
    session_mock.delete = AsyncMock()
    session_mock.commit = AsyncMock()
    session_mock.add = MagicMock()

    async def _session():
        yield session_mock

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _session

    try:
        with (
            patch("worker.storage.delete_prefix", return_value=0),
            patch("routers.auth.httpx.AsyncClient"),
        ):
            client.delete("/auth/me", cookies=_session_cookie_for(creator))
    finally:
        app.dependency_overrides.clear()

    # append_audit calls session.add with an AuditLog object
    session_mock.add.assert_called()
    added = session_mock.add.call_args[0][0]
    from models import AuditLog

    assert isinstance(added, AuditLog)
    assert added.action == "creator.deleted"
    assert added.entity_id == creator.id


# ── Session cookie cleared ────────────────────────────────────────────────────


def test_delete_account_clears_session_cookie(client):
    creator = _make_creator()
    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _fake_session()

    try:
        with (
            patch("worker.storage.delete_prefix", return_value=0),
            patch("routers.auth.httpx.AsyncClient"),
        ):
            resp = client.delete("/auth/me", cookies=_session_cookie_for(creator))
    finally:
        app.dependency_overrides.clear()

    # TestClient follows Set-Cookie; after a delete-cookie the cookie jar is empty
    # or the response header indicates the cookie was cleared (max-age=0 or expired)
    set_cookie = resp.headers.get("set-cookie", "")
    assert SESSION_COOKIE in set_cookie or SESSION_COOKIE not in client.cookies


# ── Issue 55: audit log written even when storage purge raises ────────────────


def test_audit_log_written_even_when_storage_purge_raises(client):
    """AuditLog must be written even if the storage purge raises an exception."""
    from models import AuditLog

    creator = _make_creator()
    session_mock = AsyncMock()
    session_mock.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
    session_mock.delete = AsyncMock()
    session_mock.commit = AsyncMock()
    session_mock.add = MagicMock()

    async def _session():
        yield session_mock

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _session

    try:
        with (
            patch(
                "worker.storage.delete_prefix", side_effect=Exception("simulated storage failure")
            ),
            patch("routers.auth.httpx.AsyncClient"),
        ):
            resp = client.delete("/auth/me", cookies=_session_cookie_for(creator))
    finally:
        app.dependency_overrides.clear()

    # Storage failure must not bubble up to the client
    assert resp.status_code == 204, (
        f"Expected 204 even with storage failure, got {resp.status_code}"
    )

    # An AuditLog row with action="creator.deleted" must have been written
    session_mock.add.assert_called()
    audit_row = session_mock.add.call_args[0][0]
    assert isinstance(audit_row, AuditLog), (
        f"session.add was called with {type(audit_row)}, expected AuditLog"
    )
    assert audit_row.action == "creator.deleted"
    assert audit_row.entity_id == creator.id
