"""
Integration tests for scripts/rotate_token_key.py — Issue 54.

Tests call _rotate() directly against a real Postgres instance.
Requires live Postgres with Alembic schema applied (excluded from default CI).
"""

import importlib.util
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from models import Creator, OnboardingState, YoutubeToken

_spec = importlib.util.spec_from_file_location(
    "rotate_token_key",
    Path(__file__).parent.parent / "scripts" / "rotate_token_key.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_rotate = _mod._rotate


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


async def _seed_token(
    session: AsyncSession, fernet: Fernet, *, access: str, refresh: str
) -> uuid.UUID:
    """Create a Creator + YoutubeToken row encrypted with the given Fernet key."""
    creator = Creator(
        google_sub=f"rot_test_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_rot_{uuid.uuid4().hex[:6]}",
        channel_title="Rotate Test Channel",
        onboarding_state=OnboardingState.active,
    )
    session.add(creator)
    await session.flush()

    token = YoutubeToken(
        creator_id=creator.id,
        access_token_encrypted=fernet.encrypt(access.encode()).decode(),
        refresh_token_encrypted=fernet.encrypt(refresh.encode()).decode(),
        scope="https://www.googleapis.com/auth/youtube.readonly",
        expires_at=datetime.now(UTC),
    )
    session.add(token)
    await session.flush()
    await session.commit()
    return creator.id


async def _cleanup(session: AsyncSession, creator_ids: list[uuid.UUID]) -> None:
    for cid in creator_ids:
        await session.execute(delete(Creator).where(Creator.id == cid))
    await session.commit()


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_rotation_re_encrypts_every_row(db_session: AsyncSession):
    """All rows are re-encrypted with the new key; old key can no longer decrypt."""
    key_a = Fernet.generate_key().decode()
    key_b = Fernet.generate_key().decode()
    fernet_a = Fernet(key_a)
    fernet_b = Fernet(key_b)

    plaintexts = [
        ("access-token-1", "refresh-token-1"),
        ("access-token-2", "refresh-token-2"),
        ("access-token-3", "refresh-token-3"),
    ]
    creator_ids = []
    for access, refresh in plaintexts:
        cid = await _seed_token(db_session, fernet_a, access=access, refresh=refresh)
        creator_ids.append(cid)

    try:
        result = await _rotate(key_a, key_b)
        assert result == 0, f"_rotate returned {result} errors, expected 0"

        rows = list(
            (
                await db_session.execute(
                    select(YoutubeToken).where(YoutubeToken.creator_id.in_(creator_ids))
                )
            ).scalars()
        )
        assert len(rows) == 3

        for row in rows:
            # New key must decrypt successfully and yield one of the original plaintexts.
            decrypted_access = fernet_b.decrypt(row.access_token_encrypted.encode()).decode()
            decrypted_refresh = fernet_b.decrypt(row.refresh_token_encrypted.encode()).decode()
            assert decrypted_access in [p[0] for p in plaintexts]
            assert decrypted_refresh in [p[1] for p in plaintexts]

            # Old key must no longer work.
            with pytest.raises(InvalidToken):
                fernet_a.decrypt(row.access_token_encrypted.encode())
    finally:
        await _cleanup(db_session, creator_ids)


@pytest.mark.integration
async def test_rotation_rolls_back_on_corrupt_row(db_session: AsyncSession):
    """If any row fails to decrypt, all rows remain encrypted under the original key."""
    key_a = Fernet.generate_key().decode()
    key_b = Fernet.generate_key().decode()
    fernet_a = Fernet(key_a)

    creator_ids = []
    good_access = ["good-access-1", "good-access-2"]
    for i, access in enumerate(good_access, 1):
        cid = await _seed_token(db_session, fernet_a, access=access, refresh=f"good-refresh-{i}")
        creator_ids.append(cid)

    # Seed a third row with a corrupt ciphertext.
    corrupt_creator = Creator(
        google_sub=f"rot_corrupt_{uuid.uuid4().hex[:8]}",
        channel_id=f"UC_corrupt_{uuid.uuid4().hex[:6]}",
        channel_title="Corrupt Test Channel",
        onboarding_state=OnboardingState.active,
    )
    db_session.add(corrupt_creator)
    await db_session.flush()
    corrupt_token = YoutubeToken(
        creator_id=corrupt_creator.id,
        access_token_encrypted="not-valid-fernet-ciphertext",
        refresh_token_encrypted=fernet_a.encrypt(b"some-refresh").decode(),
        scope="https://www.googleapis.com/auth/youtube.readonly",
        expires_at=datetime.now(UTC),
    )
    db_session.add(corrupt_token)
    await db_session.flush()
    await db_session.commit()
    creator_ids.append(corrupt_creator.id)

    try:
        result = await _rotate(key_a, key_b)
        # Script returns number of errors (1 corrupt row).
        assert result > 0, "Expected non-zero error count from _rotate with a corrupt row"

        # Re-query all good rows — they must still decrypt with key_a (rollback happened).
        rows = list(
            (
                await db_session.execute(
                    select(YoutubeToken).where(YoutubeToken.creator_id.in_(creator_ids[:-1]))
                )
            ).scalars()
        )
        assert len(rows) == 2
        for row in rows:
            # Rollback: old key still works.
            fernet_a.decrypt(row.access_token_encrypted.encode())

        # Corrupt row is unchanged.
        corrupt_row = await db_session.scalar(
            select(YoutubeToken).where(YoutubeToken.creator_id == corrupt_creator.id)
        )
        assert corrupt_row.access_token_encrypted == "not-valid-fernet-ciphertext"
    finally:
        await _cleanup(db_session, creator_ids)


@pytest.mark.integration
async def test_rotation_logs_no_plaintext_tokens(db_session: AsyncSession, caplog):
    """Rotation must not emit any plaintext token value to the log."""
    import logging

    key_a = Fernet.generate_key().decode()
    key_b = Fernet.generate_key().decode()
    fernet_a = Fernet(key_a)

    access_plain = "plaintext-access-XYZ"
    refresh_plain = "plaintext-refresh-ABC"
    creator_id = await _seed_token(db_session, fernet_a, access=access_plain, refresh=refresh_plain)

    try:
        with caplog.at_level(logging.DEBUG):
            await _rotate(key_a, key_b)

        assert access_plain not in caplog.text, "access token plaintext leaked into logs"
        assert refresh_plain not in caplog.text, "refresh token plaintext leaked into logs"
    finally:
        await _cleanup(db_session, [creator_id])
