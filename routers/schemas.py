"""Response models for the API surface (Issue 75 — response_model coverage).

Pydantic `*Out` models so every JSON endpoint declares its response shape: it
documents the contract in OpenAPI, validates what we return, and filters the
payload to exactly these fields (no accidental over-exposure). Each model mirrors
the dict its handler already returns — see the handler for the source of truth.
"""

from pydantic import BaseModel


class StatusOut(BaseModel):
    status: str


class TaskQueuedOut(BaseModel):
    task_id: str
    status: str


class AuthMeOut(BaseModel):
    id: str
    channel_id: str | None = None
    channel_title: str | None = None
    email: str | None = None
    onboarding_state: str


class CreatorMeOut(AuthMeOut):
    created_at: str


class DataGateOut(BaseModel):
    long_form_videos: int
    shorts: int
    long_form_ready: bool
    shorts_ready: bool
    ready: bool


class DnaProfileOut(BaseModel):
    id: str
    version: int
    status: str
    brief_text: str | None = None
    optimal_clip_len_s: float | None = None
    best_source_region: str | None = None
    optimal_upload_gap_h: float | None = None
    created_at: str


class DnaOut(BaseModel):
    profile: DnaProfileOut | None = None
    message: str | None = None


class DnaConfirmOut(BaseModel):
    id: str
    version: int
    status: str


class BriefStatusOut(BaseModel):
    status: str
    brief: str | None = None
    error: str | None = None
    updated_at: str | None = None


class FeedbackOut(BaseModel):
    id: str
    action: str


class UploadWindowOut(BaseModel):
    day_of_week: int
    day_name: str
    hour: int
    activity_index: float
    label: str


class UploadIntelOut(BaseModel):
    best_windows: list[UploadWindowOut]
    optimal_gap_hours: float | None = None
    data_available: bool


class ClipOut(BaseModel):
    id: str
    video_id: str
    setup_start_s: float | None = None
    start_s: float
    end_s: float
    peak_s: float | None = None
    score: float | None = None
    rank: int | None = None
    principle: str = ""
    reasoning: str = ""
    render_status: str
    render_uri: str | None = None


class ClipsOut(BaseModel):
    clips: list[ClipOut]


class VideoOut(BaseModel):
    id: str
    youtube_video_id: str
    title: str | None = None
    kind: str
    ingest_status: str
    duration_s: float | None = None
    created_at: str


class VideoLinkOut(BaseModel):
    video_id: str
    status: str


class VideoStatusOut(BaseModel):
    video_id: str
    youtube_video_id: str
    ingest_status: str
    source_uri: str | None = None
    captions_available: bool
