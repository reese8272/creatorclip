# worker — assessed 2026-05-31 (wave-2 re-assessment)

Slice: `worker/__init__.py`, `worker/celery_app.py`, `worker/schedule.py`,
`worker/storage.py`, `worker/tasks.py`, `worker/progress.py`,
`worker/anthropic_stream.py`. Wave-2 re-assessment after Issue 92 wired
`aemit(...)` into 6 worker tasks. Baseline commit `f5d44df`; carried-forward
findings re-read against current source.

## Findings

### Wave-2 deltas (Issue 92 emit wiring) — verified

- ✅ **Emit additions do not break idempotency.** Traced each new emit site:
  - `_ingest_async` (tasks.py:303–375), `_transcribe_async` (tasks.py:378–441),
    `_signals_async` (tasks.py:444–499) emit `step` events at stage
    boundaries; terminal `done` fires only from `_signals_async` (the last
    chain stage). The pre-existing idempotency guards (status-flip on
    redelivery, UNIQUE constraints) are untouched.
  - `_render_clip_async` (tasks.py:522–616) keeps the existing
    `render_status == done` short-circuit (line 542) AND emits `done` with
    `message="Clip already rendered."` (lines 544–549) BEFORE returning, so a
    redelivered task still terminates the SSE stream cleanly.
  - `_sync_channel_catalog_async(creator_id, task_id=None)` — the inner
    `_emit()` (tasks.py:1035–1037) is the canonical None-guard pattern.
  - `_generate_improvement_brief_async(job_id, creator_id)` keeps the
    job-id/status idempotency check (tasks.py:1276–1287) and emits `done`
    in the redelivery short-circuit.
- ✅ **No blocking calls introduced.** Every emit is `await aemit(...)` on the
  worker's singleton event loop. `sync_emit(...)` is reserved for the
  Anthropic streaming callback running inside `asyncio.to_thread`
  (`anthropic_stream.py`). Confirmed by grep — `sync_emit` callers are
  exclusively in `anthropic_stream._forward_event`.
- ✅ **No PII / token leakage in new emit payloads.** Every Issue-92 emit
  passes only: `stage`, `label`, `i`, `total`, `duration_s`, `clip_duration_s`,
  `segment_count`, `fetched`, `backend`, `version`, `brief_chars`,
  `exc_type=type(exc).__name__`, and human-safe static messages. No
  exception args, no creator ids, no URIs surface in payload dicts.
  `exc_type` is the class name only (not args / stack trace).
- ✅ **Redis hiccup cannot abort actual work.** `aemit` swallows every
  exception (progress.py:157–164); the work continues. The outer try/except
  in each task re-raises ONLY the underlying work exception — emits inside
  the except blocks (`aemit(... "error", ...)`) are themselves
  swallow-and-log inside aemit, so an emit failure during error handling
  cannot mask the original. Verified by re-reading tasks.py:365–375,
  433–441, 491–499, 608–616, 781–793, 1123–1133.

### Wave-2 gap — newly introduced

- [SEV2] `routers/auth.py:117–119` — initial-onboarding catalog sync
  enqueues `sync_channel_catalog.delay(str(creator.id))` WITHOUT calling
  `progress.aset_owner(task.id, str(creator.id))`. The worker
  `_sync_channel_catalog_async` (tasks.py:1004–1133) now emits to
  `task:{task.id}:events`. The SSE endpoint at `/tasks/{task_id}/events`
  (routers/tasks.py:131–138) refuses any subscription whose ownership key
  is absent → 404 for the user. Net effect: the post-OAuth-callback
  catalog sync emits a full progress stream that no client can subscribe
  to; events live in Redis until trimmed by `_MAXLEN`. Compare to the
  manual /me/catalog-sync route (creators.py:164–167) which DOES stamp
  ownership. Strictly outside this module's slice (the gap is in the
  router), but the worker-side emits are the user-visible end of the
  contract, so it's flagged here. | fix: add `await
  progress.aset_owner(task.id, str(creator.id))` immediately after the
  `.delay(...)` in `routers/auth.py:119`, mirroring `creators.py:167`.
  Alternative: skip Issue-92 emits for the auth-callback path by
  enqueueing with an explicit `task_id=None` override — but the simpler
  fix is to stamp the owner.

- [SEV2] `worker/tasks.py:136` + `_signals_async:490` — `_signals_async`
  emits the terminal `"done"` event AND THEN `build_signals` (sync wrapper
  at line 130–137) enqueues `generate_clips.delay(video_id)` AFTER the
  emit. The UI sees "Ingest complete" while clips are still pending
  generation; `generate_clips` itself has NO emits in this wave, so the
  client transitions from terminal-done back to silence until clips
  appear via polling. Under at-least-once redelivery of `build_signals`,
  the terminal `done` may fire repeatedly on the same stream key
  (`video_id`) — SSE consumers must be idempotent on `done`. Honest about
  uncertainty: this is a UX wart, not a correctness defect — but the
  ordering between "terminal done" and "downstream generate_clips chain"
  is a contract gap. | fix (small): emit `step
  label=clip_generation_queued` instead of `done` from `_signals_async`,
  and let `_generate_clips_async` own its own `done` after candidate
  ranking completes (requires extending Issue 92 to generate_clips).
  Alternative: rename the event to `done_ingest` so the wider chain can
  emit a distinct `done` later without colliding on TERMINAL_EVENT_TYPES.

- [SEV2] `worker/tasks.py:1098–1110` — inside `_sync_channel_catalog_async`,
  per-video metric failures are caught and logged (good) but **no error
  emit is sent for the skipped video**. A user watching the SSE stream
  sees `step` events for successful videos but the failed video silently
  disappears from the sequence — `i` jumps from N→N+2 if video N+1 failed.
  Honest about severity: cosmetic — the final `done` reports the
  correct `fetched=N` count, so the user gets accurate totals. | fix:
  emit `await _emit("step", label="sync_metrics_skipped", stage=...,
  i=i, total=total, exc_type=type(exc).__name__)` inside the except
  branch so the client UI can render a "skipped" tick.

### Carried forward — still present in wave-2

- [SEV2] `worker/progress.py:134–146` — `_async_client()` rebuilds the
  singleton on loop-mismatch but **still does not close the old client
  first**. In prod (one loop per worker process) this never triggers. In
  pytest (function-scoped loops) a fresh `aredis.Redis` is constructed
  every test; the previous one is abandoned with its connection pool
  half-bound to a dead loop. Each abandoned client logs `Event loop is
  closed` at GC and may hold sockets until process exit.
  `(needs-runtime-confirmation via lsof)`. | fix: in the rebuild branch,
  best-effort close the old reference before rebinding:
  ```python
  if _AIO is not None and _AIO_LOOP is not current:
      old, old_loop = _AIO, _AIO_LOOP
      _AIO = None
      _AIO_LOOP = None
      if old_loop is not None and not old_loop.is_closed():
          old_loop.call_soon_threadsafe(lambda: asyncio.ensure_future(old.aclose()))
  ```
  or expose `await arebind()` the test fixture calls explicitly.

- [SEV2] `worker/progress.py:149–164` — `aemit` exception handler still
  nulls out `_AIO` / `_AIO_LOOP` globals on **any** exception, including
  transient XADD errors that have nothing to do with the connection.
  Under concurrent emits during a Redis brown-out, every concurrent emit
  rebuilds the pool, worsening the brown-out (pathological churn).
  Pool-rebuild abandons in-flight `aread_since` and forces SSE consumers
  to reconnect — visible UX flicker. **Wave-2 amplifies the surface**: 6
  more emit sites now share the same singleton, so a one-shot brown-out
  during a render cascades to every other in-flight task's emit. | fix:
  only reset on connection-class errors:
  ```python
  except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as exc:
      global _AIO, _AIO_LOOP
      _AIO = None
      _AIO_LOOP = None
      logger.warning(...)
  except Exception as exc:
      logger.warning(...)  # leave singleton intact for transient XADD failures
  ```

- [SEV2] `worker/progress.py:190–208` — `aread_since` uses `XREAD` with
  `block=5000` (default). Each blocked XREAD holds **one Redis connection
  for the full block duration**. redis-py's async pool default is
  `max_connections=50` per client. With ~100 concurrent SSE consumers
  (well inside the stated 200-creator scale), the pool exhausts. **Wave-2
  worsens the math**: now `_ingest_async`, `_transcribe_async`,
  `_signals_async`, `_render_clip_async`, `_sync_channel_catalog_async`,
  and `_generate_improvement_brief_async` all emit through the SAME
  client, so reader+writer share the 50-slot pool. | fix: bound the
  async client's pool explicitly at construction:
  ```python
  _AIO = aredis.from_url(
      settings.REDIS_URL,
      decode_responses=True,
      max_connections=settings.REDIS_MAX_CONNECTIONS,  # default 200
  )
  ```
  Use separate clients for SSE reads vs worker writes so a wedged reader
  pool doesn't starve emits. Document in `docs/DEPLOYMENT.md` alongside
  PgBouncer math. `(needs-runtime-confirmation via load test)`.

- [SEV2] `worker/progress.py:74–85` — `_serialize` does `json.dumps(fields,
  default=str)`. Every value becomes a string in the event payload.
  Wave-2 added 6 more emit call sites in tasks.py, so the structural
  trust surface is wider. Today's callers pass only counts / version
  ints / class names / static messages — safe — but **no schema or
  allowlist prevents a future caller from passing an exception arg, an
  OAuth token slice, or a transcript snippet through `default=str`**.
  The guards (`message=str(exc)` at tasks.py:785 for `ValueError`;
  generic strings elsewhere) rely on every author remembering the rule.
  | fix: add a per-event-type `EventPayload(BaseModel)` with strict
  typing and reject unknown fields at the emit boundary. Cheapest
  interim: an emit-level guard that logs+drops any field whose
  stringified length exceeds N or matches a token-shape regex
  (`^sk-ant-`, `^ya29\.`, `^[A-Za-z0-9+/]{100,}={0,2}$`).

- [SEV2] `worker/tasks.py:781–786` — `_emit("error", message=str(exc))`
  for `ValueError` raw-passes the exception message to the SSE stream.
  Today's raisers in this code path are safe data-gate strings (e.g.
  `f"Creator {creator_id} not found"`), but a downstream library
  throwing a `ValueError` with a token or DB error in the message would
  leak it to an authenticated subscriber. The SEV2 is the unverified
  trust boundary. **Wave-2 added a related instance**: `_ingest_async`
  (tasks.py:322–324), `_transcribe_async` (tasks.py:390–391), and
  `_signals_async` (tasks.py:458–459) raise `ValueError(f"... {video_id}
  ...")` with the video_id interpolated. These bubble out through the
  except branches which use a static `message="Ingest failed; retrying."`
  + `exc_type=type(exc).__name__` (good — no leak) — so for the upload
  chain the wider exposure is contained. The dna-brief code path
  remains the load-bearing gap. | fix: allowlist of known-safe data-gate
  messages OR sanitize via a small classifier (`"creator/video/dna not
  found"`, `"X is empty"`) and fall back to a generic `"validation
  failed"` for anything else.

- [SEV2] `worker/anthropic_stream.py:57–75` — Anthropic stream context
  manager runs inside `asyncio.to_thread` (good). If the network drops
  mid-stream, the SDK raises inside the `for event in stream:` loop,
  escapes the `with` block, propagates up. **No terminal `error` emit at
  this layer** — caller in `_build_dna_async` catches and emits a generic
  error (good), but mid-stream tokens already delivered to the UI are
  followed by an undefined gap before the caller's terminal emit fires.
  On a slow brown-out the SSE consumer's keepalive logic must hold for
  >100ms — usually fine, but if the broker also flakes the user sees a
  frozen stream with no terminal event. **Wave-2 also runs this path
  through the improvement-brief streaming** (improvement/brief.py:115–142
  via `task_id=job_id` from tasks.py:1344–1350), so the same gap now
  applies to two flows. | fix: wrap the streaming call site (here or
  in `dna/brief.py`/`improvement/brief.py`) in:
  ```python
  try:
      ...
  except Exception:
      sync_emit(task_id, "error", message="stream interrupted")
      raise
  ```
  Lower latency than the multi-layer async unwind. The caller's emit
  still fires as backup.

- [SEV2] `worker/anthropic_stream.py:72–84` — `usage_dict` casts each
  field via `getattr(usage, ..., 0)`. The Anthropic SDK returns ints
  today, so this is safe, but `0`-as-default **silently hides** a schema
  change in a future SDK bump where the field is renamed or moved into
  a sub-object (relevant: `/claude-api` brief explicitly calls out the
  post-0.40 SDK bump in Issue 84). `cache_read=0` reported to logs would
  under-report savings without raising. | fix: distinguish "absent" from
  "zero" — return `None` when the attribute is missing; log a warning
  the first time a previously-present field returns None.

- [SEV2] `worker/tasks.py:522–616` — `_render_clip_async` idempotency
  guard (line 542) protects the *sequential* redelivery-after-success
  case but **not concurrent** delivery: with `acks_late` +
  `reject_on_worker_lost`, two workers can both read `pending`, both
  flip to `running`, both encode + upload to `clips/{clip_id}.mp4`
  (storage.py overwrites). Result: wasted double encode/upload (identical
  bytes, last-writer-wins). Wave-2 makes this MORE expensive — both
  workers now also emit `done` on the same stream, so a subscriber sees
  the terminal event twice. | fix: use
  `select(Clip).where(...).with_for_update()` on the Clip row in the
  opening session and re-check `render_status` under the lock before
  flipping to `running`.

- [SEV2] `worker/tasks.py:303–375` — `_ingest_async` is not a clean no-op
  on redelivery after a successful commit. First run overwrites
  `video.source_uri` with the derived audio URI
  (`audio/{video_id}.wav`). A redelivery re-`probe_duration_s` +
  `extract_audio_wav` over the already-extracted WAV and re-uploads the
  same key. No corruption (`deduct_for_video` no-ops via
  `UNIQUE(video_id)`; duration only set when unset), but wastes ffmpeg +
  R2 round-trips. Wave-2: also emits a duplicate full sequence of step
  events on redelivery. | fix: short-circuit when `source_uri` already
  points at the derived audio key (`source_uri.startswith("s3://" +
  bucket + "/audio/")` or `Path(source_uri).suffix == ".wav"`), or gate
  on `ingest_status == done` before opening `alocal_path`.

- [SEV2] `worker/tasks.py:796–889` — `_poll_clip_outcomes_async` does
  **not** break on YouTube quota exhaustion. Per-outcome
  `get_video_stats` failures are swallowed by `except Exception:
  continue` (line 863–870), so a quota-out creator walks the whole
  candidate set firing doomed YouTube calls. Bounded by the 10-day
  `cutoff_created` cap, but wasteful and noisy. | fix: catch
  `QuotaExhaustedError` explicitly inside the inner loop and `break`
  (mirror analytics-refresh at tasks.py:1185–1191), committing partial
  progress first.

- [cleanup] `worker/storage.py:46–49` + `.env.example` —
  `LOCAL_MEDIA_DIR=./media` is a relative default and `_local_root()`
  resolves it with bare `Path(...)` against the worker cwd
  (CLAUDE.md "all paths absolute"). Dev-only (`STORAGE_BACKEND != r2`),
  low risk. | fix: `Path(settings.LOCAL_MEDIA_DIR).resolve()` in
  `_local_root()`, or ship an absolute default.

- [cleanup] `worker/progress.py:74–85` — `_serialize` returns
  `{type, ts, request_id, data:<json>}`. The wire shape is not
  documented in the module docstring; SSE consumers + `routers/tasks.py`
  decoder rely on it implicitly. | fix: document the wire shape in
  the module docstring and link `routers/tasks.py` as canonical
  decoder.

- [cleanup] `worker/progress.py:170–184` — `aset_owner` / `aget_owner`
  have no try/except wrapper, unlike `aemit`. This is *correct* (auth
  must be load-bearing) but the asymmetry is undocumented; a future
  contributor "fixing consistency" by wrapping these in try/except
  would silently break SSE auth. | fix: add an explicit one-line
  comment on each: "MUST raise — the ownership key is the SSE
  authorization invariant."

- [cleanup] `worker/progress.py:239–244` — `arelease_slot` calls `DECR`
  with no clamp at zero; the counter goes negative on
  disconnect-after-TTL. Acknowledged in the docstring. Worth a
  one-Lua-roundtrip clamp:
  ```lua
  local v = redis.call('DECR', KEYS[1]); if v < 0 then redis.call('SET', KEYS[1], 0) end
  ```

### Cross-cutting (verified)

- ✅ `sync_emit` (progress.py:100–117) bare-except + warn — Anthropic
  streaming callback (`stream_and_emit._forward_event`) cannot abort
  iteration on a Redis blip; the per-event `try/except` in
  anthropic_stream.py:64–69 is the second layer.
- ✅ Issue 86 brief promise: "cache stats BEFORE first token" —
  anthropic_stream.py:94–107 fires `sync_emit(task_id, "cache", ...)` on
  `message_start`, which the SDK guarantees as the first event before
  any `content_block_*`. Now used by both DNA brief AND improvement
  brief (Wave-2 reuse via `task_id` kwarg).
- ✅ Per-creator SSE scoping: `task_id` in the Redis stream key is gated
  by `task:{task_id}:owner` lookup. Verified call sites in
  routers/creators.py:167, routers/creators.py:195, routers/clips.py:156,
  routers/videos.py:265, routers/improvement.py:98. **Gap**:
  routers/auth.py:117–119 (the initial onboarding catalog sync) does
  NOT stamp ownership — flagged above as a wave-2 SEV2.
- ✅ `_emit(...)` short-circuits when `progress_enabled` is False
  / `task_id is None` — verified in `_build_dna_async` (tasks.py:654–658)
  and `_sync_channel_catalog_async` (tasks.py:1035–1037).
- ✅ `request_id_ctx.get()` in `_serialize` has a default `"-"` set at
  observability.py — never raises in a worker context.
- ✅ No PII / token in any `logger.*` call across the slice — only
  task_id, creator_id, version ints, exception class names. Emit-failure
  logs use `%s` not `%r`.
- ✅ `aclose()` (progress.py:250–282) correctly handles the dead-loop
  case — drops references and returns rather than calling `aclose` on
  the dead loop.
- ✅ `RefundOnFailureTask` (tasks.py:56–85) refund path bounded —
  refund helper failures caught and only logged.
- ✅ Idempotency on `_build_dna_async`: `pg_advisory_xact_lock` +
  build_job_id re-check UNDER the lock + partial UNIQUE backstop +
  IntegrityError handler around the commit. Closes prior double-pay
  SEV2. Wave-2 `_emit("done", reason="idempotent_skip")` correctly
  terminates the SSE stream for redeliveries.
- ✅ Per-creator scoping for `_poll_clip_outcomes_async`,
  `_refresh_youtube_analytics_async`, `_sync_channel_catalog_async`,
  `_generate_improvement_brief_async` — every query carries
  `Video.creator_id == ...` or `creator.id` filters; no cross-tenant
  leak in this slice.
- ✅ Wave-2 emit additions do not introduce blocking calls or
  cross-task leakage. Stream keys are correctly scoped per task:
  `video_id` for the upload chain, `clip_id` for render, `task_id`
  (Celery task id) for catalog sync + DNA build + improvement brief.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | 4 findings (concurrent-render double-encode, ingest re-do, async-client rebuild-without-aclose in tests, aemit churn on transient failures). |
| 2 Concurrency & scale | 2 findings (poll_clip_outcomes no quota break, blocking XREAD pool sizing — exacerbated by wave-2 emit surface). |
| 3 Security & compliance | 3 findings (auth.py initial catalog sync missing aset_owner — wave-2 regression; raw ValueError leak into SSE event; unschema'd emit payload). Per-creator scoping verified end-to-end. |
| 4 Clip-quality | n/a (orchestration module) |
| 5 Anthropic SDK | 2 findings (no terminal emit on stream interrupt — now affects both DNA + improvement brief; silent-zero usage defaults hide schema drift). |
| 6 Cleanliness & typing | 3 cleanups (relative LOCAL_MEDIA_DIR default; wire-shape contract not in docstring; aset_owner/aget_owner load-bearing asymmetry; arelease_slot zero-clamp). |
| 7 Error handling / API | 1 finding (catalog sync per-video skip emits silently — UX, not safety). |
| 8 Config & paths | ok — no new config introduced in wave-2; carry-over relative-path cleanup. |

## Module verdict

NEEDS-WORK — no BLOCKERs; **wave-2 emit wiring is structurally correct**
(no blocking, no PII leakage, no idempotency regression, work cannot be
aborted by a Redis hiccup) but introduces three new SEV2s (auth.py
catalog-sync missing owner stamp; chain-terminal `done` fires before
generate_clips runs; per-video metric failures emit no skip event) and
amplifies the pre-existing aemit-churn + blocking-XREAD pool-sizing
SEV2s by sharing the same Redis singleton across six more call sites.
Eight carried-forward SEV2s remain. All fixes are mechanical and bounded.
The auth.py owner-stamp gap is the single highest-leverage fix —
one-line change in routers/auth.py mirroring the working pattern at
creators.py:167.
