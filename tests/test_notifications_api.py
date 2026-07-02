"""DB-free unit tests for Issue 245 — notification center, preferences, unsubscribe.

All DB access is mocked at the session boundary (the unit lane mocks the DB by
design — see CLAUDE.md Testing Rules). These cover the load-bearing edges:

  * PATCH /preferences cannot disable email_transactional (the field is absent
    from PreferencesPatch, so a crafted body is ignored).
  * GET / filters by creator.id; unauthenticated GET is 401; dismiss 404s a
    non-owned id.
  * GET /unsubscribe/{token} flips email_lifecycle off, is idempotent, 404s an
    unknown token with a generic body (no email/creator id), and 422s a
    malformed (non-uuid) token.
  * POST /unsubscribe/{token} — the RFC 8058 one-click endpoint mail receivers
    (Gmail/Yahoo) actually hit — same flip / idempotency / generic-404 contract.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from auth import get_current_creator
from db import get_session
from main import app
from models import Creator, Notification, NotificationPreference

# ── Helpers ───────────────────────────────────────────────────────────────────


def _creator() -> MagicMock:
    c = MagicMock(spec=Creator)
    c.id = uuid.uuid4()
    return c


def _session_override(session: AsyncMock):
    async def _gen():
        yield session

    return _gen


def _set_overrides(creator: MagicMock, session: AsyncMock) -> None:
    from tests._helpers import override_current_creator

    app.dependency_overrides[get_current_creator] = override_current_creator(creator)
    app.dependency_overrides[get_session] = _session_override(session)


# ── PATCH /preferences cannot disable transactional ───────────────────────────


def test_patch_preferences_cannot_disable_transactional(client) -> None:
    creator = _creator()
    prefs = MagicMock(spec=NotificationPreference)
    prefs.creator_id = creator.id
    prefs.email_transactional = True
    prefs.email_lifecycle = True
    prefs.inapp_enabled = True
    prefs.push_enabled = False

    session = AsyncMock()
    session.get = AsyncMock(return_value=prefs)
    _set_overrides(creator, session)

    # A crafted body that tries to turn transactional off — the field is not on
    # the request model, so it is silently ignored and transactional stays True.
    resp = client.patch(
        "/api/notifications/preferences",
        json={"email_transactional": False, "email_lifecycle": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["email_transactional"] is True
    assert prefs.email_transactional is True
    # The legitimate field DID apply.
    assert prefs.email_lifecycle is False


# ── GET / list ────────────────────────────────────────────────────────────────


def test_list_notifications_filters_by_creator(client) -> None:
    creator = _creator()

    row = MagicMock(spec=Notification)
    row.id = uuid.uuid4()
    row.kind = "clips_ready"
    row.title = "Clips ready"
    row.body = "Your clips are ready."
    row.link_url = "/app/review"
    row.seen_at = None
    row.created_at = MagicMock()
    row.created_at.isoformat.return_value = "2026-06-24T00:00:00+00:00"

    captured: dict = {}

    async def _scalars(stmt):
        captured["stmt"] = str(stmt)
        result = MagicMock()
        result.all.return_value = [row]
        return result

    session = AsyncMock()
    session.scalars = AsyncMock(side_effect=_scalars)
    _set_overrides(creator, session)

    resp = client.get("/api/notifications")
    assert resp.status_code == 200
    body = resp.json()
    assert body["unread_count"] == 1
    assert body["items"][0]["kind"] == "clips_ready"
    # The query filters on the notifications.creator_id column.
    assert "creator_id" in captured["stmt"]
    # Unseen row was stamped seen in the same txn.
    assert row.seen_at is not None
    session.commit.assert_awaited()


def test_list_notifications_requires_auth(client) -> None:
    # No override → real get_current_creator → 401 with no session cookie.
    resp = client.get("/api/notifications")
    assert resp.status_code == 401


# ── POST /{id}/dismiss ────────────────────────────────────────────────────────


def test_dismiss_notification_owned(client) -> None:
    creator = _creator()
    row = MagicMock(spec=Notification)
    row.id = uuid.uuid4()
    row.kind = "welcome"
    row.title = "Welcome"
    row.body = "Hi"
    row.link_url = None
    row.seen_at = None
    row.created_at = MagicMock()
    row.dismissed_at = None

    session = AsyncMock()
    session.scalar = AsyncMock(return_value=row)
    _set_overrides(creator, session)

    resp = client.post(f"/api/notifications/{row.id}/dismiss")
    assert resp.status_code == 200
    assert row.dismissed_at is not None
    session.commit.assert_awaited()


def test_dismiss_notification_not_owned_returns_404(client) -> None:
    creator = _creator()
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=None)  # not found / not owned
    _set_overrides(creator, session)

    resp = client.post(f"/api/notifications/{uuid.uuid4()}/dismiss")
    assert resp.status_code == 404


# ── GET /unsubscribe/{token} ──────────────────────────────────────────────────


def _admin_session_cm(session: AsyncMock):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm)


def test_unsubscribe_flips_lifecycle_off(client) -> None:
    token = uuid.uuid4()
    prefs = MagicMock(spec=NotificationPreference)
    prefs.creator_id = uuid.uuid4()
    prefs.email_lifecycle = True

    session = AsyncMock()
    session.scalar = AsyncMock(return_value=prefs)

    with patch("routers.notifications.AdminSessionLocal", _admin_session_cm(session)):
        resp = client.get(f"/unsubscribe/{token}")

    assert resp.status_code == 200
    assert prefs.email_lifecycle is False
    session.commit.assert_awaited()


def test_unsubscribe_idempotent_when_already_off(client) -> None:
    token = uuid.uuid4()
    prefs = MagicMock(spec=NotificationPreference)
    prefs.creator_id = uuid.uuid4()
    prefs.email_lifecycle = False  # already opted out

    session = AsyncMock()
    session.scalar = AsyncMock(return_value=prefs)

    with patch("routers.notifications.AdminSessionLocal", _admin_session_cm(session)):
        resp = client.get(f"/unsubscribe/{token}")

    assert resp.status_code == 200  # no-op success
    session.commit.assert_not_awaited()


def test_unsubscribe_unknown_token_generic_404(client) -> None:
    token = uuid.uuid4()
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=None)

    with patch("routers.notifications.AdminSessionLocal", _admin_session_cm(session)):
        resp = client.get(f"/unsubscribe/{token}")

    assert resp.status_code == 404
    # Generic body — must not reveal an email address or creator id.
    assert "@" not in resp.text
    assert str(token) not in resp.text


def test_unsubscribe_malformed_token_422(client) -> None:
    resp = client.get("/unsubscribe/not-a-uuid")
    assert resp.status_code == 422


# ── POST /unsubscribe/{token} (RFC 8058 one-click) ────────────────────────────


def test_one_click_post_unsubscribe_flips_lifecycle_off(client) -> None:
    token = uuid.uuid4()
    prefs = MagicMock(spec=NotificationPreference)
    prefs.creator_id = uuid.uuid4()
    prefs.email_lifecycle = True

    session = AsyncMock()
    session.scalar = AsyncMock(return_value=prefs)

    with patch("routers.notifications.AdminSessionLocal", _admin_session_cm(session)):
        # RFC 8058 receivers POST a form body of "List-Unsubscribe=One-Click".
        resp = client.post(
            f"/unsubscribe/{token}",
            data={"List-Unsubscribe": "One-Click"},
        )

    assert resp.status_code == 200
    assert prefs.email_lifecycle is False
    session.commit.assert_awaited()


def test_one_click_post_unsubscribe_idempotent(client) -> None:
    token = uuid.uuid4()
    prefs = MagicMock(spec=NotificationPreference)
    prefs.creator_id = uuid.uuid4()
    prefs.email_lifecycle = False  # already opted out

    session = AsyncMock()
    session.scalar = AsyncMock(return_value=prefs)

    with patch("routers.notifications.AdminSessionLocal", _admin_session_cm(session)):
        resp = client.post(f"/unsubscribe/{token}")

    assert resp.status_code == 200  # no-op success
    session.commit.assert_not_awaited()


def test_one_click_post_unsubscribe_unknown_token_generic_404(client) -> None:
    token = uuid.uuid4()
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=None)

    with patch("routers.notifications.AdminSessionLocal", _admin_session_cm(session)):
        resp = client.post(f"/unsubscribe/{token}")

    assert resp.status_code == 404
    # Generic body — must not reveal an email address or creator id.
    assert "@" not in resp.text
    assert str(token) not in resp.text
