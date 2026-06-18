"""Pro chatbot endpoints — streaming assistant scoped to the creator (Issue 152).

Access gate (DECISIONS 2026-06-17): there is no subscription tier, so "Pro" =
an *active* creator — one with a positive minute balance OR an unexpired free
trial. Non-active creators get a 402 with an upgrade affordance. Margin is then
protected by a per-creator daily message quota (``CHAT_DAILY_MESSAGE_LIMIT``)
plus the runner's tool-loop / token caps.

Each user message enqueues a Celery task that streams the reply over the
existing ``/tasks/{id}/events`` SSE channel (Issue 86), reusing the React
``taskStream`` consumer. Conversations + messages are persisted per-creator;
every read is filtered by the owning creator (RLS + app-layer, defense in depth).
"""

from __future__ import annotations

import asyncio
import logging
import uuid as _uuid_mod
from datetime import UTC, datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from config import settings
from db import get_session
from limiter import creator_key, limiter
from models import ChatConversation, ChatMessage, ChatRole, Creator

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)

_DAILY_LIMIT = f"{settings.CHAT_DAILY_MESSAGE_LIMIT}/day"
_TITLE_MAX = 60


class ChatMessageIn(BaseModel):
    conversation_id: str | None = Field(
        None, description="Existing conversation to continue; omit to start a new one."
    )
    message: str = Field(..., min_length=1, max_length=4000)


class ChatQueuedOut(BaseModel):
    task_id: str
    stream_url: str | None = None
    conversation_id: str
    message_id: str


def _require_chat_access(creator: Creator) -> None:
    """Gate the chatbot on active-creator status (positive balance OR live trial).

    Raises 402 with upgrade copy otherwise — no new billing infra (DECISIONS).
    """
    if creator.minutes_balance and creator.minutes_balance > 0:
        return
    ends = creator.trial_ends_at
    if ends is not None:
        if ends.tzinfo is None:
            ends = ends.replace(tzinfo=UTC)
        if ends > datetime.now(UTC):
            return
    raise HTTPException(
        status_code=402,
        detail="The assistant is available on an active plan. Add minutes at /pricing to chat.",
    )


def _parse_uuid(raw: str, what: str) -> _uuid_mod.UUID:
    try:
        return _uuid_mod.UUID(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid {what}.") from exc


async def _owned_conversation(
    conversation_id: _uuid_mod.UUID, creator: Creator, session: AsyncSession
) -> ChatConversation:
    conv = await session.scalar(
        select(ChatConversation).where(
            ChatConversation.id == conversation_id,
            ChatConversation.creator_id == creator.id,
        )
    )
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return conv


async def _enqueue_reply(
    creator: Creator, conversation_id: _uuid_mod.UUID
) -> tuple[str, str | None]:
    """Dispatch the chat_respond task and register SSE ownership."""
    import redis as _redis_pkg

    from worker import progress
    from worker.tasks import chat_respond

    task = await asyncio.to_thread(chat_respond.delay, str(creator.id), str(conversation_id))
    stream_url: str | None = f"/tasks/{task.id}/events"
    try:
        await progress.aset_owner(task.id, str(creator.id))
    except _redis_pkg.RedisError as exc:
        logger.warning("chat aset_owner failed task=%s err=%s", task.id, exc)
        stream_url = None
    return task.id, stream_url


@router.post(
    "/messages",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ChatQueuedOut,
)
@limiter.limit(_DAILY_LIMIT, key_func=creator_key)
async def post_message(
    request: Request,
    body: ChatMessageIn = Body(...),
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Send a user message; stream the assistant reply over SSE."""
    _require_chat_access(creator)

    if body.conversation_id:
        conv = await _owned_conversation(
            _parse_uuid(body.conversation_id, "conversation_id"), creator, session
        )
    else:
        conv = ChatConversation(
            creator_id=creator.id,
            title=body.message[:_TITLE_MAX],
        )
        session.add(conv)
        await session.flush()  # assigns conv.id

    user_msg = ChatMessage(conversation_id=conv.id, role=ChatRole.user, content=body.message)
    session.add(user_msg)
    conv.updated_at = datetime.now(UTC)
    await session.commit()

    task_id, stream_url = await _enqueue_reply(creator, conv.id)
    logger.info("chat message queued creator=%s conv=%s task=%s", creator.id, conv.id, task_id)
    return {
        "task_id": task_id,
        "stream_url": stream_url,
        "conversation_id": str(conv.id),
        "message_id": str(user_msg.id),
    }


@router.post(
    "/conversations/{conversation_id}/regenerate",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ChatQueuedOut,
)
@limiter.limit(_DAILY_LIMIT, key_func=creator_key)
async def regenerate(
    request: Request,
    conversation_id: str,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Re-run the last user turn (drops the previous assistant reply first)."""
    _require_chat_access(creator)
    conv = await _owned_conversation(
        _parse_uuid(conversation_id, "conversation_id"), creator, session
    )

    last = await session.scalar(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conv.id)
        .order_by(ChatMessage.created_at.desc())
        .limit(1)
    )
    if last is None:
        raise HTTPException(status_code=400, detail="Nothing to regenerate.")
    if last.role is ChatRole.assistant:
        await session.delete(last)
    conv.updated_at = datetime.now(UTC)
    await session.commit()

    task_id, stream_url = await _enqueue_reply(creator, conv.id)
    return {
        "task_id": task_id,
        "stream_url": stream_url,
        "conversation_id": str(conv.id),
        "message_id": "",
    }


@router.get("/conversations")
async def list_conversations(
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    rows = (
        (
            await session.execute(
                select(ChatConversation)
                .where(ChatConversation.creator_id == creator.id)
                .order_by(ChatConversation.updated_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return {
        "conversations": [
            {
                "id": str(c.id),
                "title": c.title,
                "updated_at": c.updated_at.isoformat(),
            }
            for c in rows
        ]
    }


@router.get("/conversations/{conversation_id}/messages")
async def get_messages(
    conversation_id: str,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    conv = await _owned_conversation(
        _parse_uuid(conversation_id, "conversation_id"), creator, session
    )
    rows = (
        (
            await session.execute(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == conv.id)
                .order_by(ChatMessage.created_at)
            )
        )
        .scalars()
        .all()
    )
    return {
        "conversation_id": str(conv.id),
        "title": conv.title,
        "messages": [
            {
                "id": str(m.id),
                "role": m.role.value,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
            }
            for m in rows
        ],
    }


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> None:
    conv = await _owned_conversation(
        _parse_uuid(conversation_id, "conversation_id"), creator, session
    )
    await session.delete(conv)  # cascades to messages
    await session.commit()
