import uuid
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db import get_session
from models import Creator

SESSION_COOKIE = "cc_session"
_ALGORITHM = "HS256"


def create_session_token(creator_id: uuid.UUID) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(creator_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.JWT_EXPIRY_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=_ALGORITHM)


def decode_session_token(token: str) -> dict:
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[_ALGORITHM])


async def get_current_creator(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Creator:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_session_token(token)
        creator_id = uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid or expired session") from None
    result = await session.execute(select(Creator).where(Creator.id == creator_id))
    creator = result.scalar_one_or_none()
    if creator is None:
        raise HTTPException(status_code=401, detail="Creator not found")
    return creator
