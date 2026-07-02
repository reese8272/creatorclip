"""Ownership-scoped single-row fetch shared by routers (Issue 109e).

Standardizes the ``session.get(Model, id)`` + post-fetch ``creator_id`` check
into a single-shot ``SELECT ... WHERE id = :id AND creator_id = :creator_id``.
Semantics are identical to the old pattern: **404 for both missing and
foreign rows** — never 403, so an attacker cannot distinguish "does not
exist" from "not yours".

The explicit ``creator_id`` predicate is defense-in-depth: under the app DB
role the RLS ``tenant_isolation`` policy already hides foreign rows, but the
predicate keeps the guarantee under BYPASSRLS (admin/test factories) too.
"""

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Base


async def get_owned[ModelT: Base](
    session: AsyncSession,
    model: type[ModelT],
    row_id: uuid.UUID,
    creator_id: uuid.UUID,
    detail: str,
) -> ModelT:
    """Fetch one row by primary key scoped to ``creator_id``; 404 on miss.

    Raises the same ``HTTPException(404, detail)`` whether the row is absent
    or belongs to another creator.
    """
    row = (
        await session.execute(
            # ModelT is bound to Base, which doesn't declare id/creator_id; every
            # model passed here has both (the call sites are the contract).
            select(model).where(
                model.id == row_id,  # type: ignore[attr-defined]
                model.creator_id == creator_id,  # type: ignore[attr-defined]
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=detail)
    return row
