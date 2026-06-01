"""Issue 113 — UNIQUE(creator_id) on improvement_briefs + IntegrityError catch.

Default-lane tests (no Postgres required):
  - Structural: constraint in __table_args__
  - Structural: IntegrityError imported in router
  - Behavioral: IntegrityError path re-queries and returns existing row (session mocked)
"""

from unittest.mock import AsyncMock, MagicMock

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError


def test_improvement_briefs_has_unique_creator_id_constraint():
    """UNIQUE(creator_id) must be in ImprovementBrief.__table_args__.

    Without this the debounce comment at routers/improvement.py:109 is a lie:
    the SELECT FOR UPDATE SKIP LOCKED protects concurrent-UPDATE races but cannot
    prevent two concurrent first-ever inserts from both succeeding.
    """
    from models import ImprovementBrief

    constraint_names = {
        c.name
        for c in getattr(ImprovementBrief, "__table_args__", ())
        if isinstance(c, sa.UniqueConstraint)
    }
    assert "uq_improvement_briefs_creator_id" in constraint_names, (
        "ImprovementBrief must have UniqueConstraint('creator_id', "
        "name='uq_improvement_briefs_creator_id'). Without it, two concurrent "
        "first-ever POSTs can both insert separate rows and double-fire Anthropic."
    )


def test_improvement_router_imports_integrity_error():
    """IntegrityError must be importable from the improvement router module.

    The concurrent-first-insert catch (try: flush() / except IntegrityError)
    depends on this import being present. If removed, the except clause would
    raise NameError at runtime, not catch the DB constraint violation.
    """
    import routers.improvement as imp_router

    assert hasattr(imp_router, "IntegrityError"), (
        "routers/improvement.py must import IntegrityError from sqlalchemy.exc "
        "for the concurrent-insert race handler to work."
    )


def test_improvement_post_handles_concurrent_insert_race(client, mocker):
    """Behavioral: when flush() raises IntegrityError (concurrent first-insert race),
    the endpoint rolls back, re-queries, and returns the winning row's task_id as 202.
    """
    import uuid as _uuid

    from auth import SESSION_COOKIE, create_session_token
    from models import ImprovementBriefStatus

    creator_id = _uuid.uuid4()

    # Existing row the winner created.
    winning_row = MagicMock()
    winning_row.job_id = "winner-task-id"
    winning_row.status = ImprovementBriefStatus.pending

    # Stub the auth dependency to return a minimal creator.
    fake_creator = MagicMock()
    fake_creator.id = creator_id
    fake_creator.channel_id = "UC_test"

    # Patch the get_current_creator dependency.
    from auth import get_current_creator
    from main import app

    original = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_creator] = lambda: fake_creator

    # Build a mock session that covers the full router call path:
    #   scalar() call 1 → has_metrics check: non-None (creator has data)
    #   execute().scalar() → SKIP LOCKED returns None (no row, no lock)
    #   scalar() call 2 → re-query without lock: None (both racers miss)
    #   add() + flush() → IntegrityError (DB rejects the duplicate insert)
    #   rollback() → no-op
    #   scalar() call 3 → post-rollback re-query: winning_row
    mock_session = AsyncMock()
    mock_session.scalar.side_effect = [
        _uuid.uuid4(),  # has_metrics — any non-None means metrics exist
        None,  # re-query without lock — both racers find nothing
        winning_row,  # post-rollback re-query — finds the winner's row
    ]
    # SKIP LOCKED uses session.execute(...).scalar() — needs its own mock chain.
    execute_result = MagicMock()
    execute_result.scalar.return_value = None
    mock_session.execute = AsyncMock(return_value=execute_result)
    mock_session.flush.side_effect = IntegrityError("", {}, Exception())
    mock_session.rollback = AsyncMock()

    from db import get_session

    async def _fake_session():
        yield mock_session

    app.dependency_overrides[get_session] = _fake_session

    try:
        token = create_session_token(creator_id)
        resp = client.post(
            "/creators/me/improvement-brief",
            cookies={SESSION_COOKIE: token},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["task_id"] == "winner-task-id"
        assert body["status"] == "pending"
        mock_session.rollback.assert_called_once()
    finally:
        app.dependency_overrides = original
