# worker — assessed 2026-05-31

Slice: `worker/__init__.py` (empty), `worker/anthropic_stream.py`,
`worker/celery_app.py`, `worker/progress.py`, `worker/schedule.py`,
`worker/storage.py`, `worker/tasks.py`.

## Findings

- [SEV2] worker/tasks.py:318–391 (`_ingest_async`) — on retry after a SUCCESSFUL
  commit of `source_uri = audio_uri` (e.g. worker_lost mid-task after the final
  commit, `task_reject_on_worker_lost=True` redelivers), the next attempt sees
  `video.source_uri` already pointing at `audio/{video_id}.wav`. It then
  downloads the WAV, re-runs `extract_audio_wav` on it (legal — ffmpeg WAV→WAV
  is a no-op), uploads a fresh WAV under the same key (idempotent overwrite),
  and the deduction skips via `UNIQUE(video_id)`. Correctness is preserved,
  BUT the *original* `source/{creator.id}/{youtube_video_id}.mp4` object is
  now unreferenced and `_purge_stale_source_media_async` only purges URIs
  still in `Video.source_uri` — so the source mp4 is **permanently orphaned
  in R2**. At hundreds of creators × occasional retries this is an unbounded
  storage cost + a YouTube ToS exposure (source media is supposed to be
  purged within `SOURCE_MEDIA_RETENTION_HOURS`). | fix: before overwriting
  `video.source_uri` in the final commit block, capture
  `prior_source_uri = video.source_uri` at function entry; after the commit
  call `adelete_file(prior_source_uri)` if `prior_source_uri != audio_uri`
  and the prior URI is the mp4 (`startswith("source/")` or `.mp4`). Simpler:
  guard the whole function with "if source_uri already endswith('.wav') or
  is the audio key, return" so a retried ingest is an explicit no-op.

- [SEV2] worker/tasks.py:393–456 (`_transcribe_async`) — no idempotency guard.
  On `acks_late + reject_on_worker_lost` redelivery the transcription
  provider is called a second time (Deepgram / AssemblyAI charge per minute)
  and a second commit overwrites the `Transcript` row last-writer-wins. The
  `existing = await session.get(Transcript, …)` branch updates in place but
  does NOT short-circuit when the row is already populated. Same shape
  applies to `_signals_async` at worker/tasks.py:459–519 (librosa CPU is
  re-burned). | fix: at the top of `_transcribe_async`, after loading
  `video`, also load the existing `Transcript`; if it exists AND
  `video.ingest_status` is past `transcribing`, emit a `step` no-op and
  return. Mirror in `_signals_async` with a `Signals` existence check. The
  render task already has this guard (worker/tasks.py:562–570) — same pattern.

- [SEV2] worker/tasks.py:91–137 (`ingest_video` / `transcribe_video` /
  `build_signals` sync wrappers) — each catches every `Exception`, sets
  `ingest_status = failed`, then calls `self.retry(exc=exc)`. Celery's
  `retry` raises `Retry` — meaning `IngestStatus` cycles
  `failed → retrying → failed → done` per attempt. The UI / video-list
  endpoint reading `ingest_status` during a retry window briefly sees
  `failed` for a job that's still in flight. If max_retries is exhausted,
  `on_failure` fires the refund — good — but the row is left at `failed`
  even after refund, which the UI may reinterpret as "you can retry this
  manually" while the user is already charged-then-refunded. | fix: on the
  retry path use a transient `IngestStatus.retrying` (or do not flip status
  to `failed` until `on_failure` fires). Simpler: set status to `failed`
  only inside `RefundOnFailureTask.on_failure`, not on every intermediate
  exception. `_signals_async` already correctly sets `done` at success
  commit time.

- [SEV2] worker/tasks.py:140–147 (`generate_clips`) — uses the default base
  `Task` (not `RefundOnFailureTask`), so a terminal failure here leaves the
  user's minutes deducted with no refund even though the user got zero clips.
  The minutes were already deducted in `_ingest_async` (the value the user
  bought is "I gave you a video and got clips back"). | fix: change to
  `base=RefundOnFailureTask` so generate_clips' terminal failure refunds the
  same way ingest/transcribe/signals do. `refund_for_video` is keyed on
  `pack_id=refund:<video_id>` (billing/refund.py) so double-fire is safe.

- [SEV2] worker/tasks.py:1154–1297 (`_sync_channel_catalog_async`) — no
  per-creator idempotency or advisory lock. Two concurrent triggers for the
  same creator (the auth callback at routers/auth.py:120 firing concurrently
  with a UI "Refresh data" hitting routers/creators.py:170) both run
  `sync_video_catalog` (row-level idempotent on the upsert) AND both iterate
  the same `unmeasured` set, paying YouTube quota twice and racing on the
  per-video `sync_video_analytics` upsert. At hundreds of creators with
  retry storms this doubles quota burn against the daily 10k cap. | fix:
  take a `pg_advisory_xact_lock(hashtext("catalog-sync:" || creator_id))`
  at the top of the function (same pattern as `_build_dna_async` at
  worker/tasks.py:688) so concurrent catalog syncs for one creator
  serialize and the second sees the first's upserts.

- [SEV2] worker/tasks.py:266–307 (`_retrain_preference_async`) — the
  self-debounce check + `build_and_save` is not under an advisory lock.
  Concurrent retrains both pass the debounce check (both see the same
  `latest.updated_at`), both compute features (CPU + memory), and only one
  wins the version UNIQUE race. The other rolls back. The code comment
  claims Issue 71 "hardens" the version race, but the harden is at the DB
  constraint level — the wasted CPU still happens. `_build_dna_async` uses
  `pg_advisory_xact_lock(hashtext(creator_id))` for this exact reason.
  | fix: wrap the function body in the same advisory lock (different prefix,
  e.g. `"retrain:" + creator_id`) so concurrent retrain calls for one
  creator serialize and the second sees the freshly-saved `latest`, hits
  the debounce, and short-circuits.

- [SEV2] worker/tasks.py:816–909 (`_poll_clip_outcomes_async`) — single
  Redis Beat trigger, no advisory lock. If Beat double-fires (clock-skew
  restart, duplicate Beat scheduler running), both runs scan the same
  `by_creator` set, call `get_video_stats` against YouTube for every
  outcome twice, and race on `outcome.fetched_at = now`. Idempotency at the
  row level is OK (last-writer wins is the same value), but YouTube quota
  is doubled. | fix: acquire a global advisory lock at function entry
  (`SELECT pg_try_advisory_lock(hashtext('poll_clip_outcomes'))`); if False,
  log and return. Same fix recommended for `_refresh_youtube_analytics_async`
  (worker/tasks.py:1300), `_purge_stale_source_media_async`
  (worker/tasks.py:1007), `_purge_stale_youtube_analytics_async`
  (worker/tasks.py:1064) — none of them lock against duplicate Beat triggers.

- [SEV2] worker/celery_app.py:45–48 — soft (3000s) < hard (3300s) <
  visibility_timeout (3600s) is correctly ordered, but a WhisperX
  transcription of a 90-minute podcast on CPU regularly exceeds 50 minutes.
  When it does, `SoftTimeLimitExceeded` is raised, the sync wrapper at
  worker/tasks.py:114–119 catches `Exception` and calls `self.retry(...)`
  — which re-enqueues the same job, and the cycle repeats until
  max_retries=3, burning ~150 minutes of paid Deepgram-or-WhisperX work
  before the user sees "failed". The docstring acknowledges this
  ("long-form sources on CPU WhisperX may need a per-task override or the
  hosted backend") but it's unhardened. | fix: in `transcribe_video`'s
  `except`, special-case `SoftTimeLimitExceeded` to NOT retry — raise it
  through to `on_failure` so the refund fires immediately. Document the
  configured `TRANSCRIPTION_TIMEOUT_S=300` ceiling in `docs/DEPLOYMENT.md`
  and confirm `TRANSCRIPTION_TIMEOUT_S < soft_time_limit` so the inner
  `asyncio.wait_for` fires first when the backend hangs.

- [SEV2] worker/storage.py:46–49 (`_local_root`) — `LOCAL_MEDIA_DIR`
  defaults to `./media` (relative). When Celery runs from a working
  directory the developer didn't expect (systemd unit, docker WORKDIR
  change, k8s job manifest) the relative path resolves against CWD and
  source media lands somewhere unintended. CLAUDE.md §"Path and config
  safety" mandates "All paths absolute". | fix: in `_local_root`, resolve
  via `Path(settings.LOCAL_MEDIA_DIR).expanduser().resolve()`; update
  `.env.example` to show an absolute example
  (`LOCAL_MEDIA_DIR=/var/lib/creatorclip/media`); add a pydantic-settings
  validator that rejects a relative `LOCAL_MEDIA_DIR` when `ENV=production`.

- [SEV2] worker/tasks.py:64–85 (`RefundOnFailureTask.on_failure`) — Celery's
  `on_failure` is invoked from the worker's main thread, NOT from the loop
  thread the task body ran on. Calling `run_async(refund_for_video(...))`
  from inside `on_failure` dispatches the coroutine onto the singleton loop
  via `loop.run_until_complete`. With `worker_prefetch_multiplier=1` plus
  `acks_late` this is safe (one task at a time per process), but it's a
  fragile coupling that breaks the day someone raises prefetch.
  `(needs-runtime-confirmation)` — load-testing with prefetch>1 would
  surface the race. | fix: either pin a hard test that asserts prefetch=1
  in CI, or rewrite the refund to open its own sync session via a fresh
  sync SQLAlchemy connection so it doesn't depend on the singleton loop.

- [SEV2] worker/progress.py:90–97 (sync redis singleton) — the sync redis
  client is constructed without an explicit `socket_timeout`. A wedged
  Redis (network partition, AOF rewrite stall) causes `xadd` to block the
  worker thread indefinitely while a paid Anthropic stream is mid-flight.
  The try/except at worker/progress.py:108–117 catches the eventual
  exception but not a hang. | fix:
  `redis.from_url(settings.REDIS_URL, decode_responses=True,
  socket_timeout=2.0, socket_connect_timeout=2.0)`. Same for
  `_async_client()` at worker/progress.py:142–143.

- [cleanup] worker/__init__.py — file appears to be empty (read warned
  "shorter than the provided offset (1)"). Python treats `worker/` as a
  regular package because the file exists, but explicit-is-better-than-
  implicit. | fix: add the module docstring
  `"""Celery worker: tasks, schedule, progress streams, storage adapter."""`
  so future maintainers don't second-guess whether it's intentional.

- [cleanup] worker/tasks.py:64 (`on_failure` signature) — untyped (`exc`,
  `task_id`, `args`, `kwargs`, `einfo`). CLAUDE.md mandates type hints on
  every signature. | fix:
  `def on_failure(self, exc: BaseException, task_id: str,
  args: Sequence[Any], kwargs: dict[str, Any],
  einfo: ExceptionInfo) -> None:`.

- [cleanup] worker/tasks.py:858 — `by_creator: dict = defaultdict(list)`
  loses its value type. | fix:
  `by_creator: dict[uuid.UUID, list[ClipOutcome]] = defaultdict(list)`.

- [cleanup] worker/schedule.py:21 — imports `timedelta` from
  `celery.schedules` (which re-exports it for convenience) but every other
  file in this module uses `from datetime import timedelta`. DRY /
  consistency. | fix: import from `datetime` like the rest of the codebase.

- [cleanup] worker/anthropic_stream.py:44–45 — `messages: list` and
  `tools: list | None` are unparameterized. | fix: type them as
  `list[dict[str, Any]]` (matches the SDK's `MessageParam` shape closely
  enough for the gate).

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | 2 findings (orphan-mp4 retry on _ingest_async, sync-redis no timeout) |
| 2 Concurrency & scale | 5 findings (catalog-sync no lock, retrain no lock, poll+refresh+purge Beat no lock, SoftTimeLimitExceeded retry loop, refund on_failure thread-loop coupling) |
| 3 Security & compliance | ok (AdminSessionLocal use is intentional cross-tenant Beat scope; per-creator queries are filtered; no token in any logger.* call; the orphan-mp4 finding also has a compliance angle — flagged under lifecycle) |
| 4 Clip-quality | n/a (worker dispatches to clip_engine; no scoring math lives here) |
| 5 Anthropic SDK | ok (prompt caching surfaced via `cache` event; tokens emitted; structured output via `messages.stream`; web_search wired via `tools=` kwarg at worker/anthropic_stream.py:75) |
| 6 Cleanliness & typing | 5 cleanup findings |
| 7 Error handling / API | n/a (no routers in this module) |
| 8 Config & paths | 1 finding (LOCAL_MEDIA_DIR relative default) |

## Module verdict

**NEEDS-WORK** — no BLOCKERs; the pipeline is well-instrumented (advisory
locks, idempotency guards, progress streams, refund-on-failure base class)
and the recently-added at-least-once hardening (Issues 39/43/47/61/62/63/76)
is visible throughout. The remaining defects are the un-hardened cousins of
the patterns that DID get hardened: `_ingest_async` lacks the "already done?
return" short-circuit the render task has; `_sync_channel_catalog_async` /
`_retrain_preference_async` / the Beat tasks lack the advisory lock that
`_build_dna_async` has; the orphan-mp4 on retry breaks the ToS retention
promise the purge task is built to honor; `generate_clips` is the one
billable-pipeline task missing `RefundOnFailureTask`. Each is small, local,
and bounded — none ships a cross-tenant leak or money-loss path.
