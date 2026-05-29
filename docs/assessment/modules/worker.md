# worker — assessed 2026-05-29

Slice: `worker/__init__.py`, `worker/celery_app.py`, `worker/schedule.py`,
`worker/storage.py`, `worker/tasks.py`. Re-assessment after hardening Issues 58–75.
Cross-module claims were traced by reading the referenced code, not assumed.

## Findings

- [SEV2] worker/tasks.py:423–430 — `_build_dna_async` idempotency on `build_job_id`
  is a read-then-act check (`select(CreatorDna.id).where(build_job_id == job_id)`)
  with **no unique constraint** backing it (models.py:296 — `build_job_id` is
  nullable String, not unique). Sequential redelivery short-circuits correctly
  before the paid Anthropic/Voyage calls, but two *concurrent* redeliveries of the
  same task id both pass the check and both run `build_patterns` + `generate_brief`
  (Anthropic) + `embed_*` (Voyage) before colliding. The collision is then caught
  only structurally by `uq_dna_creator_version` (models.py:308) at commit, raising
  IntegrityError → generic `except Exception` in `build_dna` (tasks.py:145) →
  `self.retry`; the retry finds the now-committed draft and no-ops. Net: self-heals,
  but the losing build pays for one full LLM brief + embeddings before the conflict.
  | fix: add a UNIQUE index on `creator_dna.build_job_id` (partial, WHERE NOT NULL)
  and catch IntegrityError in `_build_dna_async` to convert the loser to a clean
  no-op, OR take a `pg_advisory_xact_lock(hashtext(job_id))` at the top of the build
  (same pattern already used in preference/train.py:88) so the second delivery
  blocks, re-reads, and short-circuits before any paid call.

- [SEV2] worker/tasks.py:547–556 — `_poll_clip_outcomes_async` calls
  `get_video_stats` per outcome inside a `try/except Exception: continue`. Unlike
  `_refresh_youtube_analytics_async` (tasks.py:698) it does **not** break on quota
  exhaustion. If the project quota is spent, every remaining call raises and is
  swallowed as a warning, so the task walks the whole candidate set firing doomed
  YouTube requests (against COMPLIANCE.md §4 "do not exceed quota / backoff on
  429/403"). Bounded by the 10-day `cutoff_created` cap (tasks.py:503) so not
  unbounded, but wasteful and noisy. | fix: catch `QuotaExhaustedError` explicitly
  and `break` out of the per-creator loop (mirror tasks.py:698–704), committing
  partial progress first; optionally gate the loop on `await remaining()`.

- [SEV2] worker/tasks.py:357–394 — `_render_clip_async` idempotency guard
  (line 363: skip if `render_status == done and render_uri`) protects the
  sequential redelivery-after-success case but **not concurrent** delivery: with
  `acks_late` + `reject_on_worker_lost`, two workers can both read `pending`, both
  set `running`, both encode, and both `upload_file` to the same key
  `clips/{clip_id}.mp4` (storage.py:45–53 overwrites). Result is a wasted double
  encode/upload, not corruption (identical inputs, last-writer-wins on identical
  bytes). | fix: `SELECT ... FOR UPDATE` (`with_for_update()`) on the Clip row in
  the opening session and re-check status under the lock before flipping to
  `running`, so the second worker observes `running`/`done` and bails.

- [SEV2] worker/tasks.py:222–259 — `_ingest_async` is not a clean no-op on
  redelivery after a successful commit. The first run overwrites `video.source_uri`
  with the derived audio URI (`audio/{video_id}.wav`, line 252). A redelivery then
  re-`probe_duration_s` + `extract_audio_wav` over the already-extracted WAV and
  re-uploads the same key. No corruption (billing is idempotent via UNIQUE(video_id)
  — billing/ledger.py:105; duration only set when unset — line 253), but it re-does
  ffmpeg work and re-downloads from R2. | fix: short-circuit when source_uri already
  points at the derived audio key (e.g. startswith `audio/` / suffix `.wav`) or gate
  on `ingest_status == done`, before opening `local_path`.

- [cleanup] .env.example:61 / worker/storage.py:40 — `LOCAL_MEDIA_DIR=./media` is a
  relative default and `_local_root()` resolves it with `Path(...)` against an
  unspecified worker cwd (CLAUDE.md "all paths absolute"). Dev-only (`STORAGE_BACKEND
  != r2`), so low risk. | fix: resolve to absolute in `_local_root()` via
  `Path(settings.LOCAL_MEDIA_DIR).resolve()`, or ship an absolute default.

## Verified-fixed (traced, no longer issues)

- celery_app.py:34–47 — `acks_late=True`, `task_reject_on_worker_lost=True`,
  `prefetch=1`; invariant soft(3000) < hard(3300) < visibility_timeout(3600) holds.
- tasks.py:341–350,381 — render cuts from `_render_start_for(clip)` =
  `setup_start_s` (falls back to `start_s` only when null), matching scoring/eval.
- clip_engine/ranking.py:100–109 — `generate_and_rank_clips` no-ops when clips exist
  and returns them in rank order; no delete+reinsert, so feedback/outcome cascades
  are preserved (Issue 61). `generate_clips` task relies on this for idempotency.
- tasks.py:503,511,546,562–564 — `poll_clip_outcomes` bounded by `final` marker +
  10-day created cap; `is_terminal_poll` captured before `fetched_at` overwrite.
- tasks.py:241–247,273–279,314–315,373–387 — temp media cleaned in `finally`;
  `local_path` (storage.py:97–101) unlinks its temp download in `finally`.
- tasks.py:276–279,315,448 — transcription/Voyage/LLM run via `asyncio.to_thread`
  (transcription additionally bounded by `asyncio.wait_for(TRANSCRIPTION_TIMEOUT_S)`);
  no blocking SDK call on the worker loop thread.
- celery_app.py:13–16 + observability.py:189–224 — request-id propagated
  API→Celery via `before_task_publish` (`x_request_id` header) and re-bound in
  `task_prerun` (Issue 75f).
- preference/train.py:88,208–211 — retrain serialized by `pg_advisory_xact_lock`;
  IntegrityError on version race caught and skipped; self-debounced on new feedback.
- Engine/loop lifecycle: per-worker singleton loop (celery_app.py:57–100), engine
  recreated at `worker_process_init`, disposed + shared HTTP client closed at
  shutdown. DB sessions all via `async with db.AsyncSessionLocal()`.
- No token/PII in any `logger.*` call (creator_id/video_id/clip_id UUIDs only);
  no `print()`/TODO/debug. Source-media purge gates on `ingest_done_at` per
  COMPLIANCE.md retention row (tasks.py:615–651).

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | 3 findings (concurrent-render double-encode, ingest re-do on redelivery, dna concurrent double-pay) |
| 2 Concurrency & scale | 1 finding (poll_clip_outcomes no quota break) |
| 3 Security & compliance | ok — tokens via get_valid_access_token, never logged; purge gated on ingest_done_at; per-creator scoping on every query |
| 4 Clip-quality | n/a (orchestration module; render-from-setup verified) |
| 5 Anthropic SDK | n/a (worker delegates LLM to dna/clip_engine; runs them off-loop) |
| 6 Cleanliness & typing | 1 cleanup (relative LOCAL_MEDIA_DIR default) |
| 7 Error handling / API | n/a (not a router) |
| 8 Config & paths | ok — all worker config in .env.example; 1 relative-path cleanup |

## Module verdict
NEEDS-WORK — no blockers; core at-least-once hardening (acks_late, timeout
invariant, render-from-setup, generate-clips no-op, poll bounding, off-loop LLM,
correlation-id) is verified in place, but four SEV2 idempotency/quota edges remain
(concurrent double-render/double-pay, ingest re-do, and missing quota break in
poll_clip_outcomes).
