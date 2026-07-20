"""Issue 361 (races) — persist_ranked_clips loser path.

Unit lane (session mocked): when a concurrent execution commits the clip set
first, the loser's commit raises IntegrityError against uq_clips_video_rank
(deferred → surfaces at COMMIT); persist_ranked_clips must roll back and return
the winner's set instead of double-inserting. The winner-path idempotency guard
is covered by tests/test_generate_clips_idempotency_integration.py.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.exc import IntegrityError

from clip_engine.ranking import persist_ranked_clips


def _ranked(n: int = 2) -> list[dict]:
    return [
        {
            "setup_start_s": 1.0,
            "start_s": 2.0,
            "end_s": 30.0 + i,
            "peak_s": 20.0,
            "score": 0.9 - i * 0.1,
            "rank": i + 1,
        }
        for i in range(n)
    ]


def _select_result(clips: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = clips
    return result


async def test_persist_integrity_error_returns_winner_set() -> None:
    video_id, creator_id = uuid.uuid4(), uuid.uuid4()
    winner_clips = [MagicMock(rank=1), MagicMock(rank=2)]

    session = AsyncMock()
    session.add = MagicMock()
    # First execute: the pre-insert guard sees no clips; second: the
    # post-rollback re-select returns the winner's committed set.
    session.execute = AsyncMock(side_effect=[_select_result([]), _select_result(winner_clips)])
    session.commit = AsyncMock(
        side_effect=IntegrityError("stmt", {}, Exception("uq_clips_video_rank"))
    )

    result = await persist_ranked_clips(session, video_id, creator_id, _ranked())

    assert result == winner_clips
    session.rollback.assert_awaited_once()
    # Loser never refreshes/reranks its own (rolled-back) inserts.
    session.refresh.assert_not_awaited()
