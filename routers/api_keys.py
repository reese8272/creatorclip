"""API key management endpoints (Issue 95).

The OBS companion app (separate repo) uploads clips with an API key in
the Authorization header. These endpoints let a creator create / list /
revoke their keys from profile.html.

Auth surface: session-cookie ``get_current_creator`` (the management UI
is in the browser; the bearer-auth surface is ``/clips/ingest`` only).
"""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_key import display_prefix, generate_api_key, hash_api_key
from auth import get_current_creator
from db import get_session
from limiter import limiter
from models import Creator, CreatorApiKey

router = APIRouter(prefix="/creators/me/api-keys", tags=["api-keys"])
logger = logging.getLogger(__name__)


# ── Response models ─────────────────────────────────────────────────────────


class ApiKeyOut(BaseModel):
    id: str
    name: str
    key_prefix: str
    created_at: str
    last_used_at: str | None


class ApiKeyListOut(BaseModel):
    keys: list[ApiKeyOut]


class ApiKeyCreatedOut(BaseModel):
    """Returned ONLY at creation. The raw key is gone from our server the
    moment we write the hash, so this is the only chance the user has to
    copy it."""

    id: str
    name: str
    key_prefix: str
    raw_key: str = Field(
        ..., description="The full API key. Shown once at creation; never retrievable."
    )
    created_at: str


class ApiKeyCreateIn(BaseModel):
    name: str = Field(
        ..., min_length=1, max_length=64, description="Human label, e.g. 'OBS macbook'"
    )


# ── Helpers ─────────────────────────────────────────────────────────────────


def _to_out(row: CreatorApiKey) -> dict:
    return {
        "id": str(row.id),
        "name": row.name,
        "key_prefix": row.key_prefix,
        "created_at": row.created_at.isoformat(),
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
    }


# ── Endpoints ───────────────────────────────────────────────────────────────


@router.get("", response_model=ApiKeyListOut)
@limiter.limit("60/minute")
async def list_api_keys(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """List the creator's active API keys, newest first.

    Revoked keys are filtered out at the query layer — the management
    UI never needs to render them and showing them would imply they
    still work.
    """
    result = await session.execute(
        select(CreatorApiKey)
        .where(
            CreatorApiKey.creator_id == creator.id,
            CreatorApiKey.revoked_at.is_(None),
        )
        .order_by(CreatorApiKey.created_at.desc())
    )
    return {"keys": [_to_out(row) for row in result.scalars().all()]}


@router.post("", response_model=ApiKeyCreatedOut, status_code=201)
@limiter.limit("10/hour")
async def create_api_key(
    request: Request,
    body: ApiKeyCreateIn,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Create a new API key and return the raw key ONCE.

    After this response, the raw key is unrecoverable — we store only
    the SHA-256 hash. The companion app must copy the key immediately
    to its OS keyring; if lost, the user creates a new one and revokes
    the old.
    """
    raw_key = generate_api_key()
    row = CreatorApiKey(
        creator_id=creator.id,
        name=body.name,
        key_hash=hash_api_key(raw_key),
        key_prefix=display_prefix(raw_key),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)

    from observability import log_event

    log_event(
        "api_key_created",
        creator_id=str(creator.id),
        api_key_id=str(row.id),
        api_key_name=row.name,
    )

    return {
        "id": str(row.id),
        "name": row.name,
        "key_prefix": row.key_prefix,
        "raw_key": raw_key,
        "created_at": row.created_at.isoformat(),
    }


@router.delete("/{key_id}", status_code=204, response_class=Response)
@limiter.limit("60/minute")
async def revoke_api_key(
    request: Request,
    key_id: uuid.UUID,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Revoke an API key (soft delete — sets revoked_at).

    Per-creator isolation: returns 404 if the key belongs to another
    creator or doesn't exist. Idempotent on already-revoked keys
    (treated as 404 to avoid leaking state).
    """
    row = await session.get(CreatorApiKey, key_id)
    if row is None or row.creator_id != creator.id or row.revoked_at is not None:
        raise HTTPException(status_code=404, detail="API key not found")
    row.revoked_at = datetime.now(UTC)
    await session.commit()

    from observability import log_event

    log_event(
        "api_key_revoked",
        creator_id=str(creator.id),
        api_key_id=str(row.id),
        api_key_name=row.name,
    )
    return Response(status_code=204)
