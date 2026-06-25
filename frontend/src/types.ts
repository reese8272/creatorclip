// Shapes returned by the existing FastAPI endpoints the profile page consumes.
// Kept deliberately narrow — only the fields the UI reads (Pareto: model what we
// use, not the whole payload).

export type AnalysisMode = 'auto' | 'selective' | 'manual'

// Server-resolved next-step guidance (SetupStepOut, DECISIONS 2026-06-08).
// The dashboard renders the DNA CTA banner directly from this instead of
// re-inferring onboarding progress across endpoints.
export interface SetupStep {
  step: 'sync_catalog' | 'build_dna' | 'confirm_dna' | 'link_first_video' | 'complete'
  label: string
  next_action_type: 'navigate' | 'open_form' | 'wait'
  next_action_url: string | null
  progress_index: number
  progress_total: number
}

export interface CurrentUser {
  channel_title?: string | null
  email?: string | null
  analysis_mode?: AnalysisMode
  onboarding_state?: string
  can_publish?: boolean
  setup?: SetupStep | null
}

export interface Balance {
  minutes_balance: number
  low_balance: boolean
  // Trial fields (Issue 126) — present once first OAuth login stamps a trial.
  trial_active?: boolean
  trial_days_remaining?: number | null
}

// ── Dashboard (Issue 85c) ────────────────────────────────────────────────────

// One row of GET /videos (VideoListItemOut). `clippable` is the Issue-139
// derived flag: true only when stored media exists, so a linked video with no
// source gets the "upload to clip" affordance instead of a queue/generate CTA.
export type IngestStatus = 'pending' | 'running' | 'done' | 'failed'

export interface Video {
  id: string
  // Nullable since Issue 317 made the column nullable (standalone uploads have
  // no associated YouTube id); mirrors backend VideoListItemOut.youtube_video_id.
  youtube_video_id: string | null
  title: string | null
  kind: string
  ingest_status: IngestStatus
  duration_s: number | null
  created_at: string
  origin: string
  clippable: boolean
}

export interface VideoListResponse {
  videos: Video[]
  state: 'empty_initial' | 'empty_filtered' | 'populated'
  message?: string | null
}

// GET /videos/catalog — paginated synced-channel rows for the ChannelBrowser
// (Issue 310). These are origin=catalog videos hidden from GET /videos; each
// carries clippable=false (no stored source). Promoting one via POST /videos/link
// adopts it into the clip pipeline.
export interface CatalogListResponse {
  videos: Video[]
  total: number
  limit: number
  offset: number
}

// Subset of ClipOut the dashboard reads to count rendered clips per video.
export interface ClipListItem {
  id: string
  render_status: string
}

export interface ClipListResponse {
  clips: ClipListItem[]
}

// GET /videos/clips/counts — batched clip count response (Issue 213, replaces N+1).
export interface VideoClipCount {
  video_id: string
  total: number
  rendered: number
}

export interface ClipCountsResponse {
  counts: VideoClipCount[]
}

// GET /creators/me/insights/analytics?period=… summary.
export interface Analytics {
  videos_in_period: number
  total_views: number
  total_watch_time_h: number
  avg_view_duration_s: number | null
  avg_engagement_rate: number | null
  metrics_available: boolean
}

export type AnalyticsPeriod = '7d' | '28d' | '90d' | 'all'

// ── Onboarding (Issue 85d) ───────────────────────────────────────────────────

// 202 envelope shared by POST /creators/me/catalog/sync and /dna/build.
// stream_url is null on a Redis blip (no live progress channel that time).
export interface TaskQueued {
  task_id: string
  status: string
  stream_url: string | null
}

// GET /creators/me/data-gate — readiness of the creator's catalog for DNA build.
export interface DataGate {
  long_form_videos: number
  shorts: number
  long_form_ready: boolean
  shorts_ready: boolean
  ready: boolean
}

// ── Insights (Issue 85e) ─────────────────────────────────────────────────────

export interface ChannelTotals {
  videos_analyzed: number
  shorts: number
  longs: number
  ingested_done: number
  total_minutes_processed: number
}

export interface DnaStats {
  version: number | null
  status: string | null
  optimal_clip_len_s: number | null
  best_source_region: string | null
  optimal_upload_gap_h: number | null
}

// performance_score_components mirrors _compute_virality_score in routers/insights.py:
// { retention: number|null, engagement: number|null, views: number|null }
// Each sub-score is 0–100 where 50 = channel average.
export interface PerformanceComponents {
  retention: number | null
  engagement: number | null
  views: number | null
}

export interface Performer {
  video_id: string
  youtube_video_id: string
  title: string | null
  kind: string
  performance_score: number | null
  performance_score_components?: PerformanceComponents | null
}

export interface InsightsResponse {
  totals: ChannelTotals
  dna: DnaStats
  top_performers: Performer[]
  bottom_performers: Performer[]
}

export interface UploadWindow {
  day_name: string
  label: string
  activity_index: number
}

export interface UploadIntel {
  data_available: boolean
  best_windows: UploadWindow[]
  optimal_gap_hours: number | null
}

export interface ImprovementBrief {
  status: 'pending' | 'ready' | 'failed'
  brief: string | null
  error: string | null
}

export interface PerformerInsight {
  id: string
  content: string
}

export interface SavedInsight {
  id: string
  title: string | null
  content: string
  dna_version: number | null
  created_at: string
}

export interface SavedInsightsResponse {
  insights: SavedInsight[]
}

// ── Analysis (Issue 85e) ─────────────────────────────────────────────────────

// POST /creators/me/video-analysis — returns context synchronously, then streams
// the narrative over stream_url. analytics_available has a has_metrics alias.
export interface AnalysisStart {
  video_title: string | null
  analytics_available?: boolean
  has_metrics?: boolean
  stream_url: string | null
  task_id: string | null
}

// Done-payload shapes for the four per-video features.
export interface TitleSuggestion {
  title: string
  ctr_signal: 'up' | 'neutral' | 'down' | string
  rationale: string
  search_grounded?: boolean
}

export interface ThumbnailConcept {
  composition: string
  dominant_emotion?: string | null
  text_overlay?: string | null
  color_direction?: string | null
  predicted_ctr_rationale?: string | null
  based_on_pattern?: string | null
}

export interface HookReport {
  retention_drop_at_s: number | null
  retention_at_drop: number | null
  transcript_at_drop: string | null
  diagnosis: string | null
  rewrite_suggestion: string | null
  honesty_disclaimer: string | null
}

export interface Chapter {
  timestamp_formatted: string
  title: string
}

// ── Review / Editor (Issue 85f) ──────────────────────────────────────────────

// Full clip shape (ClipOut) — the review page reads far more than the dashboard's
// ClipListItem (which only needs id + render_status for counting).
export interface ReviewClip {
  id: string
  video_id: string
  setup_start_s: number | null
  start_s: number
  end_s: number
  peak_s: number | null
  score: number | null
  rank: number | null
  principle: string
  reasoning: string
  render_status: string
  render_uri: string | null
  cleaned_render_uri: string | null
}

// Issue 216 — honest personalization-status surface.
// Placed on the list envelope, not per-clip, to avoid O(N) scorer reads per request.
export interface PersonalizationStatus {
  active: boolean
  labels: number
  threshold: number
  weight: number
}

export interface ReviewClipListResponse {
  clips: ReviewClip[]
  personalization?: PersonalizationStatus | null
  // Issue 217 — "why not clipped" transparency surface.
  // skip_reason is a stable code string (e.g. "no_signal_above_threshold");
  // skip_reason_label is the human-readable, principle-grounded explanation.
  // Both are null when clips exist or when ingest is not yet done.
  skip_reason?: string | null
  skip_reason_label?: string | null
}

export type FeedbackAction = 'upvote' | 'downvote' | 'skip' | 'trim'

export interface FeedbackPayload {
  action: FeedbackAction
  trim_start_s?: number
  trim_end_s?: number
  feedback_tags?: string[]
  feedback_note?: string | null
}

export interface CleanPreviewCut {
  start_s: number
  end_s: number
  reason: string // "filler" | "silence"
  word: string | null
}

export interface CleanPreview {
  clip_id: string
  clip_duration_s: number
  cuts: CleanPreviewCut[]
  percent_removed: number
  warning: string | null
}

export interface TranscriptWord {
  word: string
  start_s: number
  end_s: number
  index: number
}

export interface ClipTranscript {
  clip_id: string
  clip_duration_s: number
  words: TranscriptWord[]
}

// One queued cut in the transcript editor (clip-relative seconds + word span).
export interface EditorCut {
  start_s: number
  end_s: number
  indices: [number, number]
}

export interface DnaProfile {
  version: number
  status: string
  created_at: string
  brief_text: string | null
  optimal_clip_len_s: number | null
  best_source_region: string | null
  optimal_upload_gap_h: number | null
}

export interface DnaResponse {
  profile: DnaProfile | null
  message?: string
}

export interface NicheOption {
  id: string
  label: string
}

export interface Identity {
  version: number
  created_at: string
  niches: string[]
  audience_summary: string
  mission?: string | null
  content_pillars?: string[]
  tone_tags?: string[]
  hard_nos?: string[]
}

export interface IdentityResponse {
  identity: Identity | null
  conflict?: string | null
}

export interface IdentityPayload {
  niches: string[]
  audience_summary: string
  mission: string | null
  content_pillars: string[]
  tone_tags: string[]
  hard_nos: string[]
}

export interface ApiKey {
  id: string
  name: string
  key_prefix: string
  last_used_at: string | null
  created_at: string | null
}

// ── Brand Kit (Issue 186) ────────────────────────────────────────────────────

// Shape returned by GET /creators/me/brand-kit and PUT /creators/me/brand-kit.
// Mirrors BrandKitOut on the server side.
export interface BrandKit {
  subtitle: string | null
  background: string | null
  captions_enabled: boolean
  zoom_on_peak: boolean
  denoise: boolean
  aspect: string | null
}
