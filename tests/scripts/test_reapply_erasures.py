"""Unit tests for scripts/reapply_erasures.py (Issue 254).

Core-loop contract with a mocked session: an audit-logged creator id with NO
surviving row is skipped (idempotent no-op), one WITH a surviving row gets the
erase cascade re-run on a session stamped with its creator_id (per-creator
RLS isolation)."""

import uuid
from unittest.mock import AsyncMock, MagicMock

from scripts.reapply_erasures import reapply


def _session_cm(execute_results: list) -> MagicMock:
    """A mock async-context-manager session whose execute() yields results in order."""
    session = MagicMock()
    session.info = {}
    session.execute = AsyncMock(side_effect=execute_results)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    cm._session = session
    return cm


def _ids_result(ids: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = ids
    return result


def _creator_result(creator) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = creator
    return result


async def test_reapply_skips_absent_and_erases_resurrected() -> None:
    erased_id = uuid.uuid4()  # already gone — must be skipped
    resurrected_id = uuid.uuid4()  # restored dump brought it back — must be erased
    survivor = MagicMock()
    survivor.id = resurrected_id

    cms = [
        _session_cm([_ids_result([erased_id, resurrected_id])]),  # audit-trail read
        _session_cm([_creator_result(None)]),  # erased_id: no surviving row
        _session_cm([_creator_result(survivor)]),  # resurrected_id: surviving row
    ]
    factory = MagicMock(side_effect=cms)
    erase = AsyncMock()

    reapplied, skipped = await reapply(factory, erase)

    assert (reapplied, skipped) == (1, 1)
    erase.assert_awaited_once_with(cms[2]._session, survivor)
    # Per-creator RLS stamp set before the erase ran on that session.
    assert cms[2]._session.info["creator_id"] == resurrected_id


async def test_reapply_is_noop_when_nothing_resurrected() -> None:
    """Re-running after a successful replay (or on a fresh DB) erases nothing."""
    factory = MagicMock(side_effect=[_session_cm([_ids_result([])])])
    erase = AsyncMock()

    reapplied, skipped = await reapply(factory, erase)

    assert (reapplied, skipped) == (0, 0)
    erase.assert_not_awaited()
