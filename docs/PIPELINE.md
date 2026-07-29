# PIPELINE.md — The Canonical Video → Finished Clip Flow

> The end-to-end map of what happens to a creator's video from upload to a published,
> outcome-tracked Short. Every stage names its files and key functions so an incident or a
> new session can navigate straight to the code. Companion docs: `docs/SOT.md` (stack +
> schema), `docs/CLIPPING_PRINCIPLES.md` (scoring principles), `docs/COMPLIANCE.md`
> (retention rules). Maintained since 2026-07-29 (ready-pass W3); update whenever a stage's
> contract changes.

---

## Stage map

```
upload/link ─▶ ingest ─▶ transcribe ─▶ signals ─▶ generate clips ─▶ auto-render
                                                        │                │
                                                        ▼                ▼
                        retrain ◀─ feedback ◀─ review / edit ◀─── rendered clip
                           │                        │
                           ▼                        ├─▶ trim re-render / clean pass
                    better ranking                  ├─▶ apply AI title/description
                                                    └─▶ schedule ─▶ publish ─▶ outcomes ─▶ retrain
```

Task chain: `start_pipeline` (Celery chain `worker/tasks.py:~223`) = `ingest_video` →
`transcribe_video` → `build_signals` → `generate_clips` → (auto) `render_video_clips`.
Every stage emits SSE progress to `task:{video_id}:events` (see **Streaming**, below).

---

## 1. Ingest

| | |
|---|---|
| Endpoints | `POST /videos/upload`, `POST /videos/link` — `routers/videos.py:333-521` |
| Task | `ingest_video` / `_ingest_async` — `worker/tasks.py:334-381` |
| Storage | R2 (prod) / local disk (dev) via `worker/storage.py`; key `source/{creator_id}/{token}{suffix}` |

Multipart stream → temp file → ffprobe duration probe → storage upload → `Video` row
(`ingest_status=pending`, `origin=upload|link`) → chain enqueue → `202 {video_id, stream_url}`.
Minutes are debited at upload (idempotent via `MinuteDeduction` UNIQUE); failed videos can be
retried through `POST /videos/{id}/queue` (also accepts `failed` → resets + re-runs).

**Media lifecycle (load-bearing):** `videos.source_uri` keeps the ORIGINAL video;
`videos.audio_uri` (migration 0039) holds the extracted WAV. Never overwrite `source_uri` —
the 2026-06-30 SEV1 (0/18 clips ever rendered) came from ingest replacing it with audio.
`purge_stale_source_media` (Beat, `worker/tasks.py:~819`) deletes both at
`SOURCE_MEDIA_RETENTION_HOURS` (72h, `config.py:243`) after `ingest_done_at`, per
COMPLIANCE.md. After the purge, render-from-source endpoints 409 with
`{"code": "source_expired"}` (see **Error taxonomy**).

## 2. Transcription

| | |
|---|---|
| Task | `transcribe_video` / `_transcribe_async` — `worker/tasks.py:384-426` |
| Backends | `ingestion/transcribe.py` — Deepgram nova-3 default (`TRANSCRIPTION_BACKEND`); WhisperX / AssemblyAI selectable |

ffmpeg WAV extract → `audio_uri` → Deepgram word-level transcript →
`Transcript.segments_jsonb`. Terminal timeouts set `ingest_status=failed` + reason.

## 3. Signals

| | |
|---|---|
| Task | `build_signals` / `_signals_async` — `worker/tasks.py:429-468` |
| Module | `ingestion/signals.py` → `Signals.timeline_jsonb` (binned energy/silence/tempo features) |

On success sets `ingest_status=done` and enqueues `generate_clips`. That enqueue is guarded
(ready-pass W1): a broker failure logs `generate_clips_enqueue_failed` and flips the video to
`failed` + reason instead of stranding a "done" video with zero clips.

## 4. Clip generation & scoring

| | |
|---|---|
| Endpoint | `POST /videos/{id}/clips/generate` — `routers/clips.py:222-293` (kill switch `llm_generation`, spend guard, idempotency) |
| Task | `generate_clips` — `worker/tasks.py:471-491` |
| Engine | `clip_engine/candidates.py` (windows + skip reasons) → `clip_engine/scoring.py` (LLM vs creator DNA; prompt-cached brief) → `clip_engine/ranking.py` (preference-model rerank + persist) |

The engine clips the **setup, not the aftermath** (backward look from peak, 60–90s window)
and every score cites a named principle from `docs/CLIPPING_PRINCIPLES.md`. Ranking blends
DNA fit with the recency-decayed preference model once the creator crosses the label
threshold (`personalization_status`, Issue 216); below it, honest "still learning" framing.
No clips → `skip_reason` (`source_unavailable | low_energy | high_silence | diverse_peaks`).
Concurrency backstop: deferred UNIQUE `(video_id, rank)` (migration 0046).
Auto-render enqueues `render_video_clips` for the top `CLIPS_PER_VIDEO_DEFAULT`;
`auto_render_enqueued` fires only when the enqueue actually succeeded (ready-pass W1).

## 5. Render

| | |
|---|---|
| Endpoint | `POST /clips/{id}/render` — `routers/clips.py:432-572`: source-expired 409 pre-check (Issue 362), style persist + re-render reset (Issue 353), kill switch `render_intake`, 202 + stream |
| Tasks | `render_clip` (permanent-vs-transient classification, Issue 336), `render_video_clips` (batch, one source download) — `worker/tasks.py:494-591`, `_render_clip_async` ~:1752 |
| Engine | `clip_engine/render.py` (ffmpeg), `clip_engine/captions.py` (drawtext presets), `clip_engine/reframe.py` (9:16 + zoom-on-peak) |

Style comes from the request merged with brand-kit defaults (`subtitle`, `background`,
`captions_enabled`, `zoom_on_peak`, `denoise`, `aspect`). Failure classes: permanent
(`ValueError`/`FileNotFoundError`, incl. `SourceExpiredError` with an actionable SSE message
— ready-pass W1) never retry; transient retries ≤3; SoftTimeLimit is terminal. Stuck-render
recovery: Redis start-marker + `sweep_stale_renders` conditional UPDATE (Issues 359/361).
Output: `render_uri` + `render_status=done`; served to the SPA via
`GET /clips/{id}/download?disposition=inline` (presigned; Issue 182).

## 6. Review & editing (SPA `/app/review/:videoId`, `/app/editor`)

| | |
|---|---|
| List | `GET /videos/{id}/clips` — ranked clips + personalization band + impressions logging; SPA polls 4s while renders run |
| Feedback | `POST /clips/{id}/feedback` — `routers/review.py` (keep/drop/skip/trim/format + tags; **clip-relative** trim validation) |
| Trim re-render | `POST /clips/{id}/trim-render` — `routers/review.py` (ready-pass W1): clip-relative window → cut list → existing `edit_clip` task → `cleaned_render_uri` → confirm swap |
| Clean pass | `GET /clips/{id}/clean-preview`, `POST /clips/{id}/clean`, `POST /clips/{id}/clean/confirm` — filler/silence cuts (Issue 134) |
| Transcript cuts | `GET /clips/{id}/transcript`, `POST /clips/{id}/cuts` — word-level editor (Issue 135); validation: no overlap, ≥5s kept, ≤85% removed (`clip_engine/edits.py`) |
| AI metadata | `POST /clips/{id}/title-suggestions`, `POST /clips/{id}/caption-hooks`, `POST /clips/{id}/explanation` (Issues 322/323/325) + **`PATCH /clips/{id}` applies** `applied_title`/`applied_description` (migration 0047, ready-pass W1) |
| Key components | `frontend/src/pages/Review.tsx`, `Editor.tsx`; `components/review/` — `ClipPlayer` (source-expired card), `YourCall` (feedback + trim + applied-title field), `WhyThisClip` (suggestion cards + Apply), `CleanedPreviewConfirm` (shared confirm swap), `PublishPanel` |

Editing timebase rule: all editor endpoints are **clip-relative seconds** with origin
`setup_start_s ?? start_s`. Edits re-encode from `render_uri` (not the source), so they keep
working after the 72h purge; results land in `cleaned_render_uri` until the creator confirms.

## 7. Publish & outcomes

| | |
|---|---|
| Endpoints | `routers/publications.py` — `POST /clips/{id}/publications` (schedule, tz-aware future datetime, suggested audience windows), `/confirm`, `/cancel`, list |
| SPA | `components/review/PublishPanel.tsx` + schedule dialog (ready-pass W1) |
| Sweep | `sweep_scheduled_publications` (Beat, 5m) → `publish_to_youtube` — `worker/tasks.py:605-799` |
| Upload | `youtube/publish.py::upload_video` (resumable; quota via `consume_insert`, non-refundable) |
| Outcomes | `ClipOutcome` rows → `poll_clip_outcomes` (48h/7d) → `performed_well` vs comparable-Shorts median (Issue 201) → 3× preference weight |

Uploads land **private** (creator publishes in YouTube Studio — honesty constraint; surfaced
as `privacy_note`). Title = `applied_title or video.title` (100-char cap per the official
YouTube limit), description = `applied_description or "#Shorts"`. Idempotent on `task_id`.

## 8. Personalization loop

`ClipFeedback` → `retrain_preference` (`worker/tasks.py:~1036`; advisory-lock guarded,
self-debounced) → recency-decayed LightGBM/logistic reranker (`preference/`) → next
generation reranks; outcome signals weight recent wins. Cold-start honesty via the
threshold band.

## Recap (long-video summaries)

`POST /videos/{id}/summaries` (`routers/clips.py:~1500-1814`, source-expired 409 guard) →
`render_summary` → `GET /summaries/{id}/download`; SPA `/app/video/:id/recap` with the same
source-expired card. Worker pre-checks the source (`_load_summary_render_plan`) and emits the
actionable SSE message in the enqueue-to-run race window.

---

## Streaming (SSE)

`GET /tasks/{key}/events` (`routers/tasks.py`): key is the **video_id** for pipeline stages
and the **clip_id** for clip edits/renders (sibling convention — clients must use the
`stream_url` returned by the enqueuing endpoint, never construct their own). Ownership keys
in Redis (`progress.aset_owner`) are fail-open on Redis blips: `stream_url=null` degrades the
UI to polling.

## Error taxonomy (structured 4xx `detail`)

Structured details are `{"code": ..., "message": ...}`; the SPA's `ApiError` exposes
`.code` and keeps `.message` string-compatible (plain-string details still occur on older
routes).

| code | Where | Meaning / UI state |
|---|---|---|
| `source_expired` | render 409, recap 409 | 72h retention purge — "re-upload to render" card |
| `pending_clean_or_edit` | clean/cuts/trim-render 409 | a cleaned artifact awaits confirm — confirm or keep original first |
| `trim_noop` | trim-render 422 | trim covers the whole clip — nothing to remove |
| `out_of_bounds` | trim-render 422 | trim end past the rendered clip's duration (message includes it) |
| `kept_too_short` / `removed_too_much` / `invalid_segment` | cuts/trim-render 422 | `clip_engine/edits.py` validation, limits embedded in the message |

## Cross-cutting guarantees

- **Tenant isolation**: every query creator-scoped; RLS backstop active in prod (role split,
  Issue 343; policies incl. 0038/0045).
- **Money paths**: minutes debited at upload; renders/edits charge no extra minutes; LLM
  routes are rate-limited + spend-guarded (`billing/spend_guard.py`) with cached-token-aware
  cost ledger (`billing/ledger.py`).
- **Kill switches**: `llm_generation`, `render_intake`, `youtube_publish` (`flags.py`) — any
  risky subsystem can be paused without a deploy; blocked work surfaces as clean failures.
- **No virality promises** anywhere — structural test `tests/test_compliance_no_virality.py`.
