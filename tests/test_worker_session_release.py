"""Ready-pass W2 — worker tasks must not hold tenant sessions across LLM calls.

The Issue 82b trio, applied to the three worker sites that still held an open
``db.tenant_session`` (a pooled connection) across a 30–120 s Anthropic call:

1. ``_generate_improvement_brief_async`` — read phase closes BEFORE the LLM;
   ready/failed writes run on DISTINCT, freshly acquired tenant sessions with
   ``session.info["creator_id"]`` stamped before their first query (the RLS
   HARD criterion — the after_begin GUC listener keys off that stamp).
2. ``_generate_video_analysis_async`` — session closes before the LLM; no
   write phase (ephemeral result; the ledger self-sessions).
3. ``_chat_respond_async`` — history read closes before the agentic turn; the
   turn receives a tenant session FACTORY (short-lived session per tool call);
   the assistant row is written on a fresh stamped session.

``_build_dna_async`` is EXEMPT by design: its session + pg_advisory_xact_lock
held across the LLM serializes concurrent builds (Issue 76 double-spend race).

Unit lane: the REAL ``db.tenant_session`` runs against a fake
``db.AsyncSessionLocal`` — the creator_id stamp is the genuine article.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models import (
    ChatConversation,
    ChatRole,
    Creator,
    ImprovementBrief,
    ImprovementBriefStatus,
)

_UNSET = object()


class _FakeSession:
    """Session faked at the boundary; records lifecycle + the RLS stamp.

    ``info_at_first_query`` captures ``info['creator_id']`` at the FIRST
    query — a missing stamp there means the after_begin listener would emit
    no GUC and per-creator isolation silently breaks (Issue 82b HARD
    criterion).
    """

    def __init__(self, responses: dict) -> None:
        self._responses = responses
        self.info: dict = {}
        self.closed = False
        self.committed = False
        self.added: list = []
        self.info_at_first_query: object = _UNSET

    def _record(self) -> None:
        if self.info_at_first_query is _UNSET:
            self.info_at_first_query = self.info.get("creator_id")

    async def get(self, model, pk):
        self._record()
        return self._responses.get(model)

    async def execute(self, stmt, *args, **kwargs):
        self._record()
        entity = stmt.column_descriptions[0]["type"]
        result = MagicMock()
        result.scalar_one_or_none.return_value = self._responses.get(entity)
        result.scalars.return_value = iter(self._responses.get("scalars_list", []))
        return result

    def add(self, obj) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        pass

    async def close(self) -> None:
        self.closed = True

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        await self.close()
        return False


def _session_factory(responses: dict, sessions: list) -> MagicMock:
    def _new() -> _FakeSession:
        s = _FakeSession(responses)
        sessions.append(s)
        return s

    return MagicMock(side_effect=_new)


# ── 1. Improvement brief ──────────────────────────────────────────────────────


def _brief_fixtures(creator_id: uuid.UUID) -> tuple[MagicMock, MagicMock, dict]:
    row = MagicMock()
    row.job_id = "some-older-job"
    row.status = ImprovementBriefStatus.pending
    creator = MagicMock()
    creator.id = creator_id
    creator.channel_title = "Chan"
    return row, creator, {ImprovementBrief: row, Creator: creator, "scalars_list": []}


async def test_improvement_brief_read_session_closed_before_llm() -> None:
    creator_id = uuid.uuid4()
    row, _creator, responses = _brief_fixtures(creator_id)
    sessions: list[_FakeSession] = []
    state: dict = {}

    async def _fake_build(**kwargs):
        state["n_sessions"] = len(sessions)
        state["closed"] = [s.closed for s in sessions]
        return "brief text", {}

    with (
        patch("db.AsyncSessionLocal", new=_session_factory(responses, sessions)),
        patch("improvement.brief.generate_improvement_brief", new=_fake_build),
        patch("billing.spend_guard.ensure_within_budget", new=AsyncMock()),
        patch("dna.profile.get_active", new=AsyncMock(return_value=None)),
        patch("billing.ledger.record_llm_usage", new=AsyncMock()),
        patch("worker.progress.aemit", new=AsyncMock()),
    ):
        from worker.tasks import _generate_improvement_brief_async

        await _generate_improvement_brief_async("job-1", str(creator_id))

    # The read session (the only one so far) was CLOSED before the LLM fired.
    assert state["n_sessions"] == 1
    assert state["closed"] == [True]
    # The ready write ran on a DISTINCT, freshly acquired tenant session,
    # stamped with creator_id BEFORE its first query (RLS HARD criterion).
    assert len(sessions) == 2
    write = sessions[1]
    assert write is not sessions[0]
    assert write.info_at_first_query == str(creator_id)
    assert write.committed
    assert row.status == ImprovementBriefStatus.ready
    assert row.brief_text == "brief text"


async def test_improvement_brief_llm_failure_marks_failed_on_fresh_session() -> None:
    creator_id = uuid.uuid4()
    row, _creator, responses = _brief_fixtures(creator_id)
    sessions: list[_FakeSession] = []

    with (
        patch("db.AsyncSessionLocal", new=_session_factory(responses, sessions)),
        patch(
            "improvement.brief.generate_improvement_brief",
            new=AsyncMock(side_effect=RuntimeError("anthropic 401 secret-token")),
        ),
        patch("billing.spend_guard.ensure_within_budget", new=AsyncMock()),
        patch("dna.profile.get_active", new=AsyncMock(return_value=None)),
        patch("worker.progress.aemit", new=AsyncMock()),
    ):
        from worker.tasks import _generate_improvement_brief_async

        with pytest.raises(RuntimeError):
            await _generate_improvement_brief_async("job-2", str(creator_id))

    # Failed-mark ran on a FRESH stamped session (the read one was closed).
    assert len(sessions) == 2
    fail = sessions[1]
    assert fail.info_at_first_query == str(creator_id)
    assert fail.committed
    assert row.status == ImprovementBriefStatus.failed
    # SAFE message only — never the raised exception text.
    assert "secret" not in row.error and "401" not in row.error


async def test_improvement_brief_redelivery_skips_llm_without_second_session() -> None:
    """Idempotency preserved by the split: a ready row for this job short-
    circuits before the paid LLM call — and before any write session opens."""
    creator_id = uuid.uuid4()
    row, _creator, responses = _brief_fixtures(creator_id)
    row.job_id = "job-3"
    row.status = ImprovementBriefStatus.ready
    sessions: list[_FakeSession] = []
    build = AsyncMock()

    with (
        patch("db.AsyncSessionLocal", new=_session_factory(responses, sessions)),
        patch("improvement.brief.generate_improvement_brief", new=build),
        patch("worker.progress.aemit", new=AsyncMock()),
    ):
        from worker.tasks import _generate_improvement_brief_async

        await _generate_improvement_brief_async("job-3", str(creator_id))

    build.assert_not_awaited()
    assert len(sessions) == 1


# ── 2. Video analysis ─────────────────────────────────────────────────────────


async def test_video_analysis_session_closed_before_llm() -> None:
    creator_id = uuid.uuid4()
    creator = MagicMock()
    creator.channel_title = "Chan"
    sessions: list[_FakeSession] = []
    state: dict = {}

    async def _fake_build(**kwargs):
        state["n_sessions"] = len(sessions)
        state["closed"] = [s.closed for s in sessions]
        return "analysis text", {}

    with (
        patch(
            "db.AsyncSessionLocal",
            new=_session_factory({Creator: creator, "scalars_list": []}, sessions),
        ),
        patch("analysis.brief.generate_video_analysis", new=_fake_build),
        patch("worker.tasks._spend_guard_blocked", new=AsyncMock(return_value=False)),
        patch("dna.profile.get_active", new=AsyncMock(return_value=None)),
        patch("billing.ledger.record_llm_usage", new=AsyncMock()),
        patch("worker.progress.aemit", new=AsyncMock()),
    ):
        from worker.tasks import _generate_video_analysis_async

        await _generate_video_analysis_async("job-4", str(creator_id), "yt123", "why did it pop?")

    # Session closed before the LLM; ephemeral result — no write session.
    assert state["n_sessions"] == 1
    assert state["closed"] == [True]
    assert len(sessions) == 1


# ── 3. Chat respond ───────────────────────────────────────────────────────────


async def test_chat_respond_splits_sessions_around_agentic_turn() -> None:
    cid = uuid.uuid4()
    conv_id = uuid.uuid4()
    conv = MagicMock()
    conv.creator_id = cid
    creator = MagicMock()
    creator.channel_title = "Chan"
    user_msg = MagicMock()
    user_msg.role = ChatRole.user
    user_msg.content = "hi"

    sessions: list[_FakeSession] = []
    responses = {
        ChatConversation: conv,
        Creator: creator,
        "scalars_list": [user_msg],
    }
    state: dict = {}

    async def _fake_turn(job_id, creator_id, channel_title, history, session_factory):
        state["closed_at_turn"] = [s.closed for s in sessions]
        state["n_sessions_at_turn"] = len(sessions)
        # The factory must hand out fresh tenant sessions stamped for RLS.
        async with session_factory() as s:
            state["factory_session_stamp"] = s.info.get("creator_id")
        return "reply text", {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read": 3,
            "cache_creation": 0,
            "truncated": 0,
        }

    with (
        patch("db.AsyncSessionLocal", new=_session_factory(responses, sessions)),
        patch("chat.runner.run_chat_turn", new=_fake_turn),
        patch("worker.tasks._spend_guard_blocked", new=AsyncMock(return_value=False)),
        patch("worker.progress.aemit", new=AsyncMock()),
    ):
        from worker.tasks import _chat_respond_async

        await _chat_respond_async("job-5", str(cid), str(conv_id))

    # The history-read session was CLOSED before the agentic turn began.
    assert state["n_sessions_at_turn"] == 1
    assert state["closed_at_turn"] == [True]
    # Factory sessions carry the RLS stamp.
    assert state["factory_session_stamp"] == str(cid)
    # Assistant reply written on a fresh stamped session: read + factory + write.
    assert len(sessions) == 3
    write = sessions[-1]
    assert write.info_at_first_query == str(cid)
    assert write.committed
    assert len(write.added) == 1
    assert write.added[0].content == "reply text"
    assert write.added[0].role is ChatRole.assistant
