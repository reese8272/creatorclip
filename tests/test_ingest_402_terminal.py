"""
Issue 352 Batch B (Issue-316 residual) — 402 must be TERMINAL in ingest_video.

When a creator's balance hits zero mid-pipeline, deduct_for_video raises
HTTPException(402). Retrying is deterministic waste: every retry re-runs the
full ingest (probe + audio extract + upload) and 402s again, burning the
task's retries and compute. ingest_video must fail cleanly (status=failed with
the ledger's actionable copy) WITHOUT calling self.retry; any other exception
must still go through the retry path.

DB-free unit tests — the async body and DB helpers are mocked.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from celery.exceptions import Retry
from fastapi import HTTPException

_402_DETAIL = "Insufficient minutes balance. Purchase a pack at /pricing to continue processing."


def test_ingest_402_is_terminal_and_sets_clean_status() -> None:
    """A 402 from the billing boundary must NOT burn retries — it re-raises
    immediately (terminal, on_failure fires) and records the actionable copy."""
    from models import IngestStatus
    from worker.tasks import ingest_video

    video_id = str(uuid.uuid4())
    set_status = AsyncMock()
    retry_mock = MagicMock(side_effect=Retry("retry requested"))

    with (
        patch("worker.tasks._creator_id_for_video", new=AsyncMock(return_value="c-1")),
        patch(
            "worker.tasks._ingest_async", new=AsyncMock(side_effect=HTTPException(402, _402_DETAIL))
        ),
        patch("worker.tasks._set_status", new=set_status),
        patch("worker.tasks.log_event"),
        patch.object(ingest_video, "retry", retry_mock),
        pytest.raises(HTTPException) as excinfo,
    ):
        ingest_video.run(video_id)

    assert excinfo.value.status_code == 402
    retry_mock.assert_not_called()
    set_status.assert_called_once_with(video_id, IngestStatus.failed, reason=_402_DETAIL)


def test_ingest_non_402_still_retries() -> None:
    """Any non-billing failure keeps the existing retry path (transient blips
    must not become terminal because of the 402 special case)."""
    from worker.tasks import ingest_video

    video_id = str(uuid.uuid4())
    retry_mock = MagicMock(side_effect=Retry("retry requested"))

    with (
        patch("worker.tasks._creator_id_for_video", new=AsyncMock(return_value="c-1")),
        patch("worker.tasks._ingest_async", new=AsyncMock(side_effect=ValueError("boom"))),
        patch("worker.tasks._set_status", new=AsyncMock()),
        patch("worker.tasks.log_event"),
        patch.object(ingest_video, "retry", retry_mock),
        pytest.raises(Retry),
    ):
        ingest_video.run(video_id)

    retry_mock.assert_called_once()
