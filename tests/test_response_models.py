"""Guard the response_model contracts (Issue 75).

Each handler returns a hand-built dict; the matching `*Out` model must accept that
exact shape or FastAPI raises a 500 at runtime. Several of these endpoints are only
exercised by integration tests (real Postgres), so these DB-free checks validate the
model ⇄ handler-dict shape in the default run — drift here fails fast.
"""

from routers.schemas import (
    ClipOut,
    DataGateOut,
    DnaOut,
    UploadIntelOut,
    VideoOut,
    VideoStatusOut,
)


def test_data_gate_out_accepts_handler_shape():
    # mirrors youtube.analytics.check_data_gate
    DataGateOut(
        long_form_videos=12,
        shorts=6,
        long_form_ready=True,
        shorts_ready=True,
        ready=True,
    )


def test_dna_out_accepts_both_handler_branches():
    # branch 1: no profile yet
    DnaOut(profile=None, message="No DNA profile yet.")
    # branch 2: full profile (mirrors routers.creators.get_dna)
    out = DnaOut(
        profile={
            "id": "x",
            "version": 1,
            "status": "confirmed",
            "brief_text": "b",
            "optimal_clip_len_s": 35.0,
            "best_source_region": "opening",
            "optimal_upload_gap_h": 48.0,
            "created_at": "2026-05-29T00:00:00+00:00",
        }
    )
    assert out.profile is not None and out.profile.version == 1


def test_upload_intel_out_accepts_handler_shape():
    UploadIntelOut(
        best_windows=[
            {"day_of_week": 2, "day_name": "Tue", "hour": 18, "activity_index": 0.7, "label": "x"}
        ],
        optimal_gap_hours=48.0,
        data_available=True,
    )
    # empty windows + no gap is valid too (no data yet)
    UploadIntelOut(best_windows=[], optimal_gap_hours=None, data_available=False)


def test_video_out_accepts_handler_shape():
    VideoOut(
        id="x",
        youtube_video_id="abc12345678",
        title=None,
        kind="long",
        ingest_status="done",
        duration_s=600.0,
        created_at="2026-05-29T00:00:00+00:00",
    )


def test_video_status_out_accepts_handler_shape():
    VideoStatusOut(
        video_id="x",
        youtube_video_id="abc12345678",
        ingest_status="pending",
        source_uri=None,
        captions_available=False,
    )


def test_clip_out_accepts_handler_shape():
    # mirrors routers.clips._clip_response (principle/reasoning default to "")
    ClipOut(
        id="x",
        video_id="v",
        setup_start_s=10.0,
        start_s=10.0,
        end_s=40.0,
        peak_s=25.0,
        score=0.9,
        rank=1,
        principle="",
        reasoning="",
        render_status="pending",
        render_uri=None,
    )
