"""Waveform peaks must never fail the work that produced the media (Issue 392).

Same posture — and the same reasoning — as `test_poster_ingest_safety.py`.
`ingest_video` is a `RefundOnFailureTask` with `max_retries=3`, so a propagating
peaks error would retry the ENTIRE ingest three times and then REFUND the
creator's minutes for a transcription that actually succeeded. A missing waveform
is a labelled flat track in the UI; a spurious refund is money.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, patch

from worker import tasks

_PEAKS = {
    "version": 1,
    "sample_rate": 16000,
    "samples_per_pixel": 512,
    "bits": 8,
    "length": 1,
    "data": [-1, 1],
}


class TestComputeAndUploadPeaks:
    def test_returns_none_when_the_audio_yields_no_peaks(self, tmp_path: Path) -> None:
        with patch("ingestion.peaks.compute_peaks", return_value=None):
            result = asyncio.run(
                tasks._compute_and_upload_peaks(tmp_path / "a.wav", "peaks/c/v.json")
            )
        assert result is None

    def test_uploads_and_returns_the_uri_on_success(self, tmp_path: Path) -> None:
        with (
            patch("ingestion.peaks.compute_peaks", return_value=_PEAKS),
            patch("ingestion.peaks.write_peaks_json", return_value=True),
            patch(
                "worker.storage.aupload_file",
                AsyncMock(return_value="s3://bucket/peaks/c/v.json"),
            ),
        ):
            result = asyncio.run(
                tasks._compute_and_upload_peaks(tmp_path / "a.wav", "peaks/c/v.json")
            )
        assert result == "s3://bucket/peaks/c/v.json"

    def test_always_removes_the_temp_file(self, tmp_path: Path) -> None:
        captured: list[Path] = []

        def spy(peaks, out_path):  # noqa: ANN001, ANN202
            captured.append(Path(out_path))
            return False

        with (
            patch("ingestion.peaks.compute_peaks", return_value=_PEAKS),
            patch("ingestion.peaks.write_peaks_json", side_effect=spy),
        ):
            asyncio.run(tasks._compute_and_upload_peaks(tmp_path / "a.wav", "peaks/c/v.json"))

        assert captured, "serialisation should have been attempted"
        assert not captured[0].exists(), "the temp peaks file must be cleaned up even on failure"

    def test_removes_the_temp_file_even_when_upload_raises(self, tmp_path: Path) -> None:
        captured: list[Path] = []

        def spy(peaks, out_path):  # noqa: ANN001, ANN202
            captured.append(Path(out_path))
            return True

        with (
            patch("ingestion.peaks.compute_peaks", return_value=_PEAKS),
            patch("ingestion.peaks.write_peaks_json", side_effect=spy),
            patch("worker.storage.aupload_file", AsyncMock(side_effect=RuntimeError("R2 down"))),
            contextlib.suppress(RuntimeError),
        ):
            asyncio.run(tasks._compute_and_upload_peaks(tmp_path / "a.wav", "peaks/c/v.json"))

        assert captured and not captured[0].exists()


class TestIngestNeverFailsOnPeaks:
    """Source-inspection guards.

    These read `ingest_video`'s own source rather than driving the task, because
    the property being protected is structural — "this call is wrapped and its
    failure is swallowed" — and an integration-style test of a refund path needs
    a live DB, Redis and a billing ledger to prove one `try`.
    """

    def test_peaks_extraction_is_wrapped_and_swallowed(self) -> None:
        src = inspect.getsource(tasks._ingest_async)
        assert "_compute_and_upload_peaks(" in src, "ingest should compute peaks"
        call_at = src.index("_compute_and_upload_peaks(")
        before = src[:call_at]
        # The nearest preceding `try:` must be closer than the nearest preceding
        # `finally:` — i.e. the call really is inside a guarded block.
        assert before.rindex("try:") > before.rindex("async with alocal_path")
        after = src[call_at:]
        assert "except Exception as exc:" in after
        assert "peaks_uri = None" in after

    def test_peaks_writeback_never_nulls_an_existing_value(self) -> None:
        src = inspect.getsource(tasks._ingest_async)
        # Guarded assignment: a failed re-run leaves an earlier run's waveform.
        assert "if peaks_uri:" in src
        assert "video.peaks_uri = peaks_uri" in src

    def test_peaks_are_computed_from_the_wav_not_the_source_video(self) -> None:
        """Reading the source again would cost a second full R2 download.

        It would also be impossible after the 72h audio purge, since the WAV is
        the only decoded audio that ever exists locally.
        """
        src = inspect.getsource(tasks._ingest_async)
        call_at = src.index("_compute_and_upload_peaks(")
        assert "wav_path" in src[call_at : call_at + 200]


class TestPeaksBackfill:
    def test_backfill_only_targets_videos_whose_audio_still_exists(self) -> None:
        """The hard ceiling the poster sweep does not have.

        Peaks come from `audio_uri`, which `purge_stale_source_media` deletes at
        SOURCE_MEDIA_RETENTION_HOURS. Without this predicate the sweep would
        re-attempt every purged video forever.
        """
        src = inspect.getsource(tasks._backfill_video_peaks_async)
        assert "Video.peaks_uri.is_(None)" in src
        assert "Video.audio_uri.is_not(None)" in src

    def test_backfill_takes_the_advisory_lock_and_marks_failures(self) -> None:
        src = inspect.getsource(tasks._backfill_video_peaks_async)
        assert '_try_advisory_lock(session, "backfill_video_peaks")' in src
        assert "_PEAKS_FAIL_TTL_S" in src

    def test_backfill_writes_through_a_tenant_session(self) -> None:
        src = inspect.getsource(tasks._backfill_video_peaks_async)
        assert "db.tenant_session(str(creator_id))" in src
        assert "video.peaks_uri is None" in src
