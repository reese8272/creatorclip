"""Unit tests for the ingest failure_reason humanizer.

Root-caused from a prod upload that flipped to FAILED with no user-visible cause
(the reason lived only in worker logs). The worker now maps the exception to a
short, creator-safe reason — these tests pin that it's helpful AND never leaks a
raw exception message (which could carry a presigned URL, path, or token).
"""

from worker.tasks import _humanize_failure


def test_missing_source_maps_to_reupload_guidance():
    msg = _humanize_failure(FileNotFoundError("/tmp/x.mp4"), "ingest")
    assert "re-upload" in msg.lower()


def test_stage_specific_reasons():
    assert "transcription" in _humanize_failure(RuntimeError("boom"), "transcribe").lower()
    assert "analyse" in _humanize_failure(RuntimeError("boom"), "signals").lower()
    assert "video file" in _humanize_failure(RuntimeError("boom"), "ingest").lower()


def test_reason_never_leaks_the_raw_exception_message():
    secret_url = "https://acct.r2.cloudflarestorage.com/k?X-Amz-Signature=deadbeef"
    for stage in ("ingest", "transcribe", "signals", "clips"):
        msg = _humanize_failure(RuntimeError(secret_url), stage)
        assert "X-Amz-Signature" not in msg
        assert "cloudflarestorage" not in msg
