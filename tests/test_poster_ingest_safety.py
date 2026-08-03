"""A poster frame must never fail the work that produced the media (Issue 387).

THE HIGHEST-VALUE TEST IN THIS ISSUE. `ingest_video` is a `RefundOnFailureTask`
with `max_retries=3`. If poster extraction were allowed to propagate, one
undecodable frame would retry the ENTIRE ingest three times and then REFUND the
creator's minutes for a transcription that actually succeeded. A missing poster
is a placeholder in the UI; a spurious refund is money.

The same posture applies to `_encode_and_upload_clip`, which retries too — there
a propagating poster error would cost a full re-encode.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from worker import tasks


class TestExtractAndUploadPoster:
    def test_returns_none_when_no_frame_can_be_produced(self, tmp_path: Path) -> None:
        with patch("clip_engine.render.extract_poster_frame", return_value=False):
            result = asyncio.run(
                tasks._extract_and_upload_poster(tmp_path / "src.mp4", "posters/c/v.jpg")
            )
        assert result is None

    def test_uploads_and_returns_the_uri_on_success(self, tmp_path: Path) -> None:
        with (
            patch("clip_engine.render.extract_poster_frame", return_value=True),
            patch(
                "worker.storage.aupload_file",
                AsyncMock(return_value="s3://bucket/posters/c/v.jpg"),
            ),
        ):
            result = asyncio.run(
                tasks._extract_and_upload_poster(tmp_path / "src.mp4", "posters/c/v.jpg")
            )
        assert result == "s3://bucket/posters/c/v.jpg"

    def test_always_removes_the_temp_file(self, tmp_path: Path) -> None:
        captured: list[Path] = []

        def spy(src, out_path, **kwargs):  # noqa: ANN001, ANN202
            captured.append(out_path)
            return False

        with patch("clip_engine.render.extract_poster_frame", side_effect=spy):
            asyncio.run(tasks._extract_and_upload_poster(tmp_path / "src.mp4", "posters/c/v.jpg"))

        assert captured, "extraction should have been attempted"
        assert not captured[0].exists(), "the temp poster must be cleaned up even on failure"

    def test_removes_the_temp_file_even_when_upload_raises(self, tmp_path: Path) -> None:
        captured: list[Path] = []

        def spy(src, out_path, **kwargs):  # noqa: ANN001, ANN202
            captured.append(out_path)
            out_path.write_bytes(b"jpeg")
            return True

        with (
            patch("clip_engine.render.extract_poster_frame", side_effect=spy),
            patch("worker.storage.aupload_file", AsyncMock(side_effect=RuntimeError("R2 down"))),
            pytest.raises(RuntimeError),
        ):
            asyncio.run(tasks._extract_and_upload_poster(tmp_path / "src.mp4", "posters/c/v.jpg"))

        assert not captured[0].exists()


class TestPosterFailureIsContained:
    """The callers must swallow, not propagate.

    Asserted against the SOURCE of each caller rather than by driving a full
    ingest: `_ingest_async` needs a live DB, R2 and Celery context, and a test
    that mocked all of them would be asserting on the mocks. What must never
    regress is structural — that the poster call sits inside a try/except which
    keeps going — so that is what is checked.
    """

    def test_ingest_wraps_poster_extraction(self) -> None:
        src = inspect.getsource(tasks._ingest_async)
        assert "_extract_and_upload_poster" in src, "ingest should extract a poster"

        idx = src.index("_extract_and_upload_poster")
        # The call sits inside a try whose handler assigns None and continues.
        preceding = src[:idx]
        assert preceding.rstrip().endswith(
            (
                "poster_uri = await",
                "poster_uri = await tasks._extract_and_upload_poster",
            )
        ) or "try:" in preceding[-400:], "the poster call must be inside a try block"
        following = src[idx:]
        assert "except Exception" in following
        assert "poster_uri = None" in following, (
            "a poster failure must degrade to None, never propagate — ingest_video is a "
            "RefundOnFailureTask and would refund minutes for successful work"
        )

    def test_clip_render_wraps_poster_extraction(self) -> None:
        src = inspect.getsource(tasks._encode_and_upload_clip)
        assert "_extract_and_upload_poster" in src
        following = src[src.index("_extract_and_upload_poster") :]
        assert "except Exception" in following
        assert "poster_uri = None" in following

    def test_poster_is_written_only_when_extraction_succeeded(self) -> None:
        for fn in (tasks._ingest_async, tasks._encode_and_upload_clip):
            src = inspect.getsource(fn)
            assert "if poster_uri:" in src, (
                f"{fn.__name__} must not write a null poster_uri over an existing one"
            )


class TestBackfill:
    def test_candidate_query_skips_already_purged_sources(self) -> None:
        """Purged videos are skipped STRUCTURALLY, not by a conditional: the
        retention purge nulls `source_uri` when it deletes the blob, so those
        rows simply never match. They keep poster_uri NULL forever, which is the
        acceptance criterion's graceful placeholder."""
        src = inspect.getsource(tasks._backfill_video_posters_async)
        assert "Video.poster_uri.is_(None)" in src
        assert "Video.source_uri.is_not(None)" in src

    def test_uses_an_advisory_lock_and_admin_session(self) -> None:
        src = inspect.getsource(tasks._backfill_video_posters_async)
        assert "_try_advisory_lock" in src, "multi-worker deploys must not double-download"
        assert "AdminSessionLocal" in src, "a cross-tenant sweep needs the admin session"

    def test_marks_permanent_failures_so_egress_is_not_paid_hourly_forever(self) -> None:
        src = inspect.getsource(tasks._backfill_video_posters_async)
        assert "poster_backfill_failed:" in src
        assert "_POSTER_FAIL_TTL_S" in src

    def test_processes_newest_first(self) -> None:
        # If the drain is interrupted, finish the rows a creator is most likely
        # to be looking at.
        src = inspect.getsource(tasks._backfill_video_posters_async)
        assert "Video.created_at.desc()" in src

    def test_one_bad_source_does_not_end_the_batch(self) -> None:
        src = inspect.getsource(tasks._backfill_video_posters_async)
        body = src[src.index("for video_id") :]
        assert "except Exception" in body
        assert "continue" in body
