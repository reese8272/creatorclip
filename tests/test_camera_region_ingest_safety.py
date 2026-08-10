"""Issue 439 Stage 2 — safety contracts for video-level camera-region resolution.

Structural, in the same idiom as ``tests/test_poster_ingest_safety.py``: these
paths are ffmpeg/R2 I/O shells whose load-bearing property is not "what they
compute" but "what they must never do" — fail an ingest, discard a good value,
or pay egress forever. Asserting that against the source is cheap and is what
would actually have caught the class of bug this issue is about.
"""

import inspect

from worker import tasks


class TestIngest:
    def test_region_detection_never_fails_ingest(self) -> None:
        """ingest_video is a RefundOnFailureTask with max_retries=3 — a
        propagating detection error would retry the whole ingest and then REFUND
        the creator's minutes for a transcription that already succeeded."""
        src = inspect.getsource(tasks._ingest_async)
        assert "detect_video_camera_region" in src
        following = src[src.index("detect_video_camera_region") :]
        assert "except Exception" in following
        assert "camera_region_json = None" in following, (
            "a detection failure must degrade to None, never propagate"
        )

    def test_region_is_written_only_when_detection_succeeded(self) -> None:
        """A re-ingest whose detection declines must not NULL out a region an
        earlier run resolved — same guard as poster_uri and peaks_uri."""
        src = inspect.getsource(tasks._ingest_async)
        assert "if camera_region_json:" in src

    def test_detection_is_gated_on_the_feature_flag(self) -> None:
        src = inspect.getsource(tasks._ingest_async)
        assert "CAMERA_REGION_DETECT_ENABLED" in src


class TestBackfill:
    def test_candidate_query_skips_already_purged_sources(self) -> None:
        """Detection needs the source, which the retention purge nulls when it
        deletes the blob — those rows simply never match and keep falling back
        to per-clip detection at render time."""
        src = inspect.getsource(tasks._backfill_video_camera_regions_async)
        assert "Video.camera_region_jsonb.is_(None)" in src
        assert "Video.source_uri.is_not(None)" in src

    def test_uses_an_advisory_lock_and_admin_session(self) -> None:
        src = inspect.getsource(tasks._backfill_video_camera_regions_async)
        assert "_try_advisory_lock" in src, "multi-worker deploys must not double-download"
        assert "AdminSessionLocal" in src, "a cross-tenant sweep needs the admin session"

    def test_writes_go_through_a_tenant_session(self) -> None:
        """The cross-tenant READ is the whole exemption; every WRITE must reopen
        an RLS-enforced session (Issue 231)."""
        src = inspect.getsource(tasks._backfill_video_camera_regions_async)
        assert "db.tenant_session(str(creator_id))" in src

    def test_marks_declines_so_egress_is_not_paid_hourly_forever(self) -> None:
        """A source with no detectable chrome legitimately returns None forever —
        without a marker it would be re-downloaded every pass for all time. The
        key is version-scoped; see the test below for why."""
        src = inspect.getsource(tasks._backfill_video_camera_regions_async)
        assert "camera_region_backfill_failed:" in src

    def test_backfill_marker_is_scoped_to_the_detector_version(self) -> None:
        """Issue 443: the marker's 7-day TTL outlives the bug that set it, so
        after a detector fix the hourly sweep silently processed 0 videos — it
        cost a full cycle to notice. Keying by VIDEO_REGION_VERSION makes a fix
        invalidate the markers its own bug wrote."""
        src = inspect.getsource(tasks._backfill_video_camera_regions_async)
        assert "VIDEO_REGION_VERSION" in src

    def test_one_bad_source_does_not_end_the_batch(self) -> None:
        src = inspect.getsource(tasks._backfill_video_camera_regions_async)
        body = src[src.index("for video_id") :]
        assert "except Exception" in body

    def test_batch_is_smaller_than_the_poster_sweep(self) -> None:
        """This pass decodes up to 9 sixty-second windows per video (Issue 443's
        consensus) rather than one frame, so equal egress buys far more CPU."""
        assert tasks._CAMERA_REGION_BACKFILL_BATCH < tasks._POSTER_BACKFILL_BATCH
