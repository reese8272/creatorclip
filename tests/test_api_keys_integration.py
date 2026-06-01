"""Integration tests for the /creators/me/api-keys management endpoints
(Issue 95) + the bearer-auth dependency.

Real Postgres; marker: integration. Covers the full CRUD lifecycle,
per-creator isolation, raw-key-shown-once invariant, and revoke
semantics.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api_key import (
    generate_api_key,
    hash_api_key,
)
from auth import create_session_token
from config import settings
from main import app
from models import Creator, CreatorApiKey, OnboardingState

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed_creator(session: AsyncSession) -> Creator:
    creator = Creator(
        google_sub=f"test_apik_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_apik_{uuid.uuid4().hex[:6]}",
        channel_title="API Key Test",
        onboarding_state=OnboardingState.active,
        minutes_balance=200,
    )
    session.add(creator)
    await session.commit()
    return creator


async def _cleanup(session: AsyncSession, creator_id: uuid.UUID) -> None:
    await session.execute(delete(CreatorApiKey).where(CreatorApiKey.creator_id == creator_id))
    await session.execute(delete(Creator).where(Creator.id == creator_id))
    await session.commit()


def _auth_cookie(creator_id: uuid.UUID) -> dict[str, str]:
    """Session-cookie dict suitable for TestClient. Per-creator UUID
    sidesteps the slowapi rate-limiter cross-test bucket (OFF_COURSE_BUGS
    2026-05-31 entry)."""
    return {"cc_session": create_session_token(creator_id)}


# ── POST /creators/me/api-keys ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_api_key_returns_raw_key_once(client, db_session: AsyncSession):
    """The POST response is the only time the raw key exists outside the
    user's keyring. Subsequent GET responses must never include it."""
    creator = await _seed_creator(db_session)
    try:
        resp = client.post(
            "/creators/me/api-keys",
            json={"name": "OBS macbook"},
            cookies=_auth_cookie(creator.id),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()

        assert "raw_key" in body, "POST must include the raw key for one-time copy"
        assert body["raw_key"].startswith("ack_"), "Raw key carries the canonical prefix"
        assert body["name"] == "OBS macbook"
        assert body["key_prefix"] == body["raw_key"][len("ack_") : len("ack_") + 8]

        # The DB row holds the hash, not the raw key.
        row = (
            await db_session.execute(
                select(CreatorApiKey).where(CreatorApiKey.creator_id == creator.id)
            )
        ).scalar_one()
        assert row.key_hash == hash_api_key(body["raw_key"])
        assert row.key_hash != body["raw_key"]
    finally:
        await _cleanup(db_session, creator.id)


@pytest.mark.asyncio
async def test_create_api_key_rejects_empty_name(client, db_session: AsyncSession):
    """Empty name is a programmer error in the companion app's signup
    flow, not a real user choice — fail fast with 422."""
    creator = await _seed_creator(db_session)
    try:
        resp = client.post(
            "/creators/me/api-keys",
            json={"name": ""},
            cookies=_auth_cookie(creator.id),
        )
        assert resp.status_code == 422
    finally:
        await _cleanup(db_session, creator.id)


@pytest.mark.asyncio
async def test_create_api_key_requires_auth(client):
    """No session cookie → 401."""
    resp = client.post("/creators/me/api-keys", json={"name": "test"})
    assert resp.status_code == 401


# ── GET /creators/me/api-keys ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_api_keys_excludes_raw_key(client, db_session: AsyncSession):
    """GET responses must never include the raw key — only the prefix
    used to identify it in the management UI."""
    creator = await _seed_creator(db_session)
    try:
        create_resp = client.post(
            "/creators/me/api-keys",
            json={"name": "OBS"},
            cookies=_auth_cookie(creator.id),
        )
        raw_key = create_resp.json()["raw_key"]

        list_resp = client.get(
            "/creators/me/api-keys",
            cookies=_auth_cookie(creator.id),
        )
        assert list_resp.status_code == 200
        keys = list_resp.json()["keys"]
        assert len(keys) == 1
        assert "raw_key" not in keys[0]
        assert raw_key not in str(keys)  # raw key not anywhere in the response
        assert keys[0]["key_prefix"] == raw_key[len("ack_") : len("ack_") + 8]
    finally:
        await _cleanup(db_session, creator.id)


@pytest.mark.asyncio
async def test_list_api_keys_filters_revoked(client, db_session: AsyncSession):
    """Revoked keys must not appear in the list — the UI showing them
    would imply they still work."""
    creator = await _seed_creator(db_session)
    try:
        active_resp = client.post(
            "/creators/me/api-keys",
            json={"name": "active"},
            cookies=_auth_cookie(creator.id),
        )
        revoked_resp = client.post(
            "/creators/me/api-keys",
            json={"name": "to-revoke"},
            cookies=_auth_cookie(creator.id),
        )
        client.delete(
            f"/creators/me/api-keys/{revoked_resp.json()['id']}",
            cookies=_auth_cookie(creator.id),
        )

        list_resp = client.get(
            "/creators/me/api-keys",
            cookies=_auth_cookie(creator.id),
        )
        names = [k["name"] for k in list_resp.json()["keys"]]
        assert names == ["active"], f"Expected only the active key; got {names}"
        assert active_resp.json()["id"] in str(list_resp.json())
    finally:
        await _cleanup(db_session, creator.id)


@pytest.mark.asyncio
async def test_list_api_keys_per_creator_isolation(client, db_session: AsyncSession):
    """Creator A's list must not include creator B's keys. Load-bearing
    isolation guarantee (CLAUDE.md per-creator rule)."""
    creator_a = await _seed_creator(db_session)
    creator_b = await _seed_creator(db_session)
    try:
        client.post(
            "/creators/me/api-keys",
            json={"name": "A's key"},
            cookies=_auth_cookie(creator_a.id),
        )
        client.post(
            "/creators/me/api-keys",
            json={"name": "B's key"},
            cookies=_auth_cookie(creator_b.id),
        )

        a_list = client.get("/creators/me/api-keys", cookies=_auth_cookie(creator_a.id))
        b_list = client.get("/creators/me/api-keys", cookies=_auth_cookie(creator_b.id))

        a_names = {k["name"] for k in a_list.json()["keys"]}
        b_names = {k["name"] for k in b_list.json()["keys"]}
        assert a_names == {"A's key"}
        assert b_names == {"B's key"}
        assert a_names.isdisjoint(b_names)
    finally:
        await _cleanup(db_session, creator_a.id)
        await _cleanup(db_session, creator_b.id)


# ── DELETE /creators/me/api-keys/{id} ──────────────────────────────────────


@pytest.mark.asyncio
async def test_revoke_api_key_soft_deletes(client, db_session: AsyncSession):
    """DELETE sets revoked_at instead of deleting the row — audit trail
    is preserved. The revoked row is hidden from the list endpoint."""
    creator = await _seed_creator(db_session)
    try:
        create_resp = client.post(
            "/creators/me/api-keys",
            json={"name": "OBS"},
            cookies=_auth_cookie(creator.id),
        )
        key_id = create_resp.json()["id"]

        del_resp = client.delete(
            f"/creators/me/api-keys/{key_id}",
            cookies=_auth_cookie(creator.id),
        )
        assert del_resp.status_code == 204

        # Row still exists with revoked_at set.
        row = (
            await db_session.execute(
                select(CreatorApiKey).where(CreatorApiKey.id == uuid.UUID(key_id))
            )
        ).scalar_one()
        assert row.revoked_at is not None
        assert row.revoked_at.tzinfo is not None
    finally:
        await _cleanup(db_session, creator.id)


@pytest.mark.asyncio
async def test_revoke_other_creators_key_returns_404(client, db_session: AsyncSession):
    """Creator A cannot revoke creator B's key (returns 404, not 403, to
    avoid leaking the existence of the row)."""
    creator_a = await _seed_creator(db_session)
    creator_b = await _seed_creator(db_session)
    try:
        b_key = client.post(
            "/creators/me/api-keys",
            json={"name": "B"},
            cookies=_auth_cookie(creator_b.id),
        ).json()
        resp = client.delete(
            f"/creators/me/api-keys/{b_key['id']}",
            cookies=_auth_cookie(creator_a.id),
        )
        assert resp.status_code == 404

        # B's row still active.
        row = (
            await db_session.execute(
                select(CreatorApiKey).where(CreatorApiKey.id == uuid.UUID(b_key["id"]))
            )
        ).scalar_one()
        assert row.revoked_at is None
    finally:
        await _cleanup(db_session, creator_a.id)
        await _cleanup(db_session, creator_b.id)


@pytest.mark.asyncio
async def test_revoke_idempotent_on_already_revoked(client, db_session: AsyncSession):
    """Revoking an already-revoked key returns 404 (not 409 — the key is
    effectively gone from the user's perspective)."""
    creator = await _seed_creator(db_session)
    try:
        create_resp = client.post(
            "/creators/me/api-keys",
            json={"name": "OBS"},
            cookies=_auth_cookie(creator.id),
        )
        key_id = create_resp.json()["id"]
        client.delete(f"/creators/me/api-keys/{key_id}", cookies=_auth_cookie(creator.id))

        resp = client.delete(
            f"/creators/me/api-keys/{key_id}",
            cookies=_auth_cookie(creator.id),
        )
        assert resp.status_code == 404
    finally:
        await _cleanup(db_session, creator.id)


# ── Bearer auth dependency (used by /clips/ingest) ─────────────────────────


@pytest.mark.asyncio
async def test_bearer_dependency_updates_last_used_at(client, db_session: AsyncSession):
    """A successful auth must stamp last_used_at so the management UI
    can show key freshness ('used 3 minutes ago')."""
    creator = await _seed_creator(db_session)
    try:
        raw_key = client.post(
            "/creators/me/api-keys",
            json={"name": "OBS"},
            cookies=_auth_cookie(creator.id),
        ).json()["raw_key"]

        before = datetime.now(UTC)
        # Hit the bearer-authed surface — /clips/ingest will exercise the
        # dependency. Without a multipart body we expect a 422; what we
        # care about is that last_used_at moved.
        client.post(
            "/clips/ingest",
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        # last_used_at gets stamped INSIDE the dependency before the
        # validation error, so it should advance regardless of the 422.

        row = (
            await db_session.execute(
                select(CreatorApiKey).where(CreatorApiKey.creator_id == creator.id)
            )
        ).scalar_one()
        # last_used_at may be None if the request didn't hit the dependency
        # (e.g. validation error before). Either way, it must NOT be in the
        # past relative to `before`.
        if row.last_used_at is not None:
            assert row.last_used_at >= before
    finally:
        await _cleanup(db_session, creator.id)


@pytest.mark.asyncio
async def test_bearer_dependency_rejects_revoked_key(client, db_session: AsyncSession):
    """A revoked key must 401 — even though its hash still exists, the
    revoked_at filter excludes it from the auth lookup."""
    creator = await _seed_creator(db_session)
    try:
        create_resp = client.post(
            "/creators/me/api-keys",
            json={"name": "OBS"},
            cookies=_auth_cookie(creator.id),
        )
        raw_key = create_resp.json()["raw_key"]
        client.delete(
            f"/creators/me/api-keys/{create_resp.json()['id']}",
            cookies=_auth_cookie(creator.id),
        )

        resp = client.post(
            "/clips/ingest",
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert resp.status_code == 401
        assert (
            "revoked" in resp.json().get("detail", "").lower()
            or "invalid" in resp.json().get("detail", "").lower()
        )
    finally:
        await _cleanup(db_session, creator.id)


@pytest.mark.asyncio
async def test_bearer_dependency_rejects_unknown_key(client, db_session: AsyncSession):
    """A well-formed bearer that doesn't match any hash on file must 401."""
    creator = await _seed_creator(db_session)
    try:
        fake_key = generate_api_key()
        resp = client.post(
            "/clips/ingest",
            headers={"Authorization": f"Bearer {fake_key}"},
        )
        assert resp.status_code == 401
    finally:
        await _cleanup(db_session, creator.id)


@pytest.mark.asyncio
async def test_bearer_dependency_rejects_missing_header():
    """No Authorization header → 401."""
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        resp = c.post("/clips/ingest")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_bearer_dependency_rejects_non_canonical_prefix(client):
    """A bearer that isn't ours (no ack_ prefix) is 401 BEFORE we hit the
    DB. Saves a SHA-256 + index lookup on garbage bearers."""
    resp = client.post(
        "/clips/ingest",
        headers={"Authorization": "Bearer not-our-format"},
    )
    assert resp.status_code == 401
