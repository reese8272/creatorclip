# worker — assessed 2026-05-31 (wave-3 re-assessment)

Slice: `worker/__init__.py`, `worker/celery_app.py`, `worker/schedule.py`,
`worker/storage.py`, `worker/tasks.py`, `worker/progress.py`,
`worker/anthropic_stream.py`. Wave-3 re-assessment against baseline
`84a7e9f`. Three Wave-3 SEV2s from the prior assessment are now closed
(Fixes D / E / F); the eight carried-forward SEV2s remain unchanged.
Two cleanups are still open.

## Findings

### Wave-3 deltas — verified

- ✅ **Fix A — `worker/anthropic_stream.py:69–76` dict-based
  `stream_kwargs` construction.** The `tools` kwarg is now built into a
  local `stream_kwargs: dict[str, Any]` and only inserted when
  `tools is not None`. Confirmed: `dna/brief.py:136–143` calls
  `stream_and_emit(...)` WITHOUT a `tools=` argument (uses the default
  `None`), so the kwarg is omitted from `client.messages.stream(...)` —
  matches the pre-existing `.create()` call shape on that path.
  `improvement/brief.py:127–135` passes `tools=tools` (the web_search
  block), so the kwarg is forwarded. Semantics preserved end-to-end;
  no SDK shape regression on the no-tools path. The pre-existing
  `for event in stream:` try/except is unchanged → forward-failure
  handling intact.

- ✅ **Fix E — terminal `done` moved from `_signals_async` →
  `_generate_clips_async`.** `worker/tasks.py:495` now emits
  `aemit(video_id, "step", label="ingest_complete", stage="signals")` —
  `"step"` is NOT in `TERMINAL_EVENT_TYPES` (`worker/progress.py:59`),
  so the stream stays open and no `EXPIRE` is set
  (`worker/progress.py:155–156`). `_generate_clips_async`
  (`worker/tasks.py:897–989`) now owns the terminal lifecycle:
  - Start emit at line 915 (`label="generate_clips_start"`).
  - Score-and-rank progress at line 959 (`label="score_and_rank"`).
  - Terminal `done` at line 974–980 with `clip_count=len(clips)`.
  - Idempotent short-circuit at line 933–945 ALSO emits `done` (with
    `message="Clips already generated."`) so a redelivered task closes
    the SSE consumer rather than orphaning it.
  - Exception handler at line 981–989 emits `error` with safe
    static message + `exc_type=type(exc).__name__` (no PII).
  - Issue-46 idempotency invariant intact: the
    `select(Clip.id).where(... render_status == done).limit(1)` short-
    circuit still guards against duplicate candidate generation under
    at-least-once redelivery. The new emits sit OUTSIDE the guard
    branches, so neither path mutates state twice.

- ✅ **Fix F — per-video skip emits in `_sync_channel_catalog_async`.**
  `worker/tasks.py:1162–1169` emits
  `step:sync_metrics_skipped` carrying `i`, `total`, and
  `reason=type(exc).__name__`. Verified:
  - **Class-name only**: `type(exc).__name__` is used (not `str(exc)` /
    `repr(exc)` / `exc.args`), preserving the no-PII / no-internal-
    detail invariant the worker module enforces. Compare to the
    successful `sync_metrics` emit at line 1136–1142 which also carries
    only counts.
  - **i/N math contiguous**: for a 3-video batch with video 2 failing,
    the loop emits at line 1136 (`sync_metrics i=1, total=3`), then on
    the failure path emits at line 1162 (`sync_metrics_skipped i=2,
    total=3`), then the next success emits at line 1136 (`sync_metrics
    i=3, total=3`). `enumerate(unmeasured, 1)` (line 1132) drives `i`,
    and the failure branch does NOT decrement — so the contract holds.
  - The `YouTubeAuthError` re-raise at line 1143 still bypasses the
    skip-emit and surfaces to the outer except (line 1182) which fires
    a terminal `error` — auth death stays terminal, not a per-video
    skip. Correct.

- ✅ **Wave-3 SEV2 `routers/auth.py:117–119` (Fix D) — CLOSED.**
  Re-read `routers/auth.py:117–137` against the prior gap:
  ```python
  task = sync_channel_catalog.delay(str(creator.id))
  try:
      await progress.aset_owner(task.id, str(creator.id))
  except _redis_pkg.RedisError as exc:
      ...warning log...
  ```
  Owner is now stamped immediately after `.delay(...)`, mirroring the
  manual `/me/catalog-sync` route in `routers/creators.py:167`. The
  RedisError-only catch is the right shape (fail-open on Redis brown-out
  without swallowing programming errors). Strictly out-of-slice fix,
  but the worker-side emit consumer is now reachable end-to-end.

- ✅ **Wave-3 SEV2 `worker/tasks.py:_signals_async:490` — CLOSED.**
  `done` no longer fires in `_signals_async`; it now fires from
  `_generate_clips_async` after `generate_and_rank_clips` returns. The
  UI sees a continuous step stream from ingest_start through
  generate_clips_start through the terminal done — no premature
  close, no silent gap.

- ✅ **Wave-3 SEV2 `worker/tasks.py:_sync_channel_catalog_async` per-video
  silent skip — CLOSED.** The per-video failure path now emits
  `sync_metrics_skipped` with `reason=<ExceptionClassName>`. UX
  rendering can now show a "skipped" tick; the contiguous-i invariant
  the consumer relies on is preserved.

### Carried forward — still present in wave-3

- [SEV2] `worker/progress.py:134–146` — `_async_client()` rebuilds the
  singleton on loop-mismatch but still does not close the old client
  first. Same as wave-2; no Wave-3 change touched this code path.
  Production impact: low (one loop per worker process). Test impact:
  abandoned client warnings + potential socket retention until process
  exit. `(needs-runtime-confirmation via lsof)`. | fix: in the rebuild
  branch, best-effort close the old reference before rebinding (or
  expose `await arebind()` for the test fixture to call explicitly) —
  see prior wave-2 patch.

- [SEV2] `worker/progress.py:149–164` — `aemit` exception handler still
  nulls out `_AIO` / `_AIO_LOOP` globals on **any** exception, including
  transient XADD errors that have nothing to do with the connection.
  Wave-3 ADDS new emit sites: `_generate_clips_async` (3 new emits) +
  per-video skip emits in `_sync_channel_catalog_async`. The
  singleton-churn surface continues to widen. | fix: only reset on
  `redis.exceptions.ConnectionError` / `redis.exceptions.TimeoutError`;
  leave the singleton intact for transient XADD failures.

- [SEV2] `worker/progress.py:190–208` — `aread_since` uses `XREAD` with
  `block=5000` and the default redis-py async pool of 50 connections
  per client. Wave-3 increases pressure on the shared client: the
  upload→ingest→transcribe→signals→generate_clips chain now keeps the
  stream open through generate_clips (no early close), so subscribers
  hold their XREAD slots strictly longer. The 100-concurrent-SSE-
  consumer math gets tighter. | fix: bound the async client's pool
  explicitly at construction (`max_connections=200`) and split
  reader/writer clients so a wedged reader pool doesn't starve emits.
  Document alongside PgBouncer math in `docs/DEPLOYMENT.md`.
  `(needs-runtime-confirmation via load test)`.

- [SEV2] `worker/progress.py:74–85` — `_serialize` does `json.dumps(fields,
  default=str)` with no schema or allowlist. Wave-3 adds two more
  emit kwargs (`clip_count` on the `generate_clips` done emit,
  `reason=<ExceptionClassName>` on `sync_metrics_skipped`). Both new
  values are structurally safe (an int and a class-name string), but
  the trust surface widens by another two call sites. | fix: per-
  event-type `EventPayload(BaseModel)` with `extra="forbid"` and a
  size-cap on stringified field values. Cheapest interim: a token-
  shape regex on every `default=str` fallback (`^sk-ant-`, `^ya29\.`,
  long base64) that logs+drops rather than emits.

- [SEV2] `worker/tasks.py:781–786` — `_emit("error", message=str(exc))`
  for `ValueError` raw-passes the exception message to the SSE stream.
  Today's raisers in `_build_dna_async`'s ValueError path are
  hand-written data-gate strings (`f"Creator {creator_id} not found"`,
  etc.), but a downstream library `ValueError` with a token or DB
  error in the message would leak. The Wave-3 upload-chain emits all
  use a static `message=` + `exc_type=type(exc).__name__` (good — no
  leak); this SEV2 is now isolated to the DNA-brief flow but the
  trust-boundary gap remains. | fix: allowlist of known-safe data-gate
  messages OR a small classifier (`"creator/video/dna not found"`,
  `"X is empty"`); fall back to a generic `"validation failed"`.

- [SEV2] `worker/anthropic_stream.py:78–86` — Anthropic stream context
  manager runs inside `asyncio.to_thread`. If the network drops
  mid-stream, the SDK raises inside the `for event in stream:` loop,
  escapes the `with` block, propagates up. No terminal `error` emit at
  this layer — the caller in `dna/brief.py` (or `improvement/brief.py`,
  now both flow through this code path) catches and emits the generic
  error, but mid-stream tokens already delivered to the UI are
  followed by an undefined gap before the caller's terminal emit
  fires. Wave-3 did NOT change this path. | fix: wrap the streaming
  call site (`stream_and_emit` itself or each caller) in:
  ```python
  try:
      ...
  except Exception:
      sync_emit(task_id, "error", message="stream interrupted")
      raise
  ```
  Lower latency than the multi-layer async unwind. The caller's emit
  still fires as backup.

- [SEV2] `worker/anthropic_stream.py:93–99` — `usage_dict` casts each
  field via `getattr(usage, ..., 0)`. `0`-as-default silently hides a
  schema change in a future SDK bump where a field is renamed or moved
  into a sub-object. `cache_read=0` reported to logs would under-report
  savings without raising. Same as wave-2. | fix: distinguish "absent"
  from "zero" — return `None` when the attribute is missing; log a
  warning the first time a previously-present field returns None.

- [SEV2] `worker/tasks.py:527–621` — `_render_clip_async` idempotency
  guard at line 547 (`render_status == done and render_uri`) protects
  *sequential* redelivery-after-success but **not concurrent**
  delivery. With `acks_late` + `reject_on_worker_lost`, two workers
  can both read `pending`, both flip to `running`, both encode +
  upload to `clips/{clip_id}.mp4` (storage overwrites; last writer
  wins on bytes that are deterministically identical, so no
  corruption — just wasted ffmpeg + R2 round-trips). Wave-3 did NOT
  change this path, but Wave-3's terminal-done shift means a
  redelivery-after-success now emits `done` on the **video_id**
  stream from generate_clips AND on the **clip_id** stream from
  render — those are different stream keys, so no event-double-fire,
  but the underlying double-encode race is unchanged. | fix: use
  `select(Clip).where(...).with_for_update()` on the Clip row in the
  opening session and re-check `render_status` under the lock before
  flipping to `running`.

- [SEV2] `worker/tasks.py:303–376` — `_ingest_async` is not a clean
  no-op on redelivery after a successful commit. First run overwrites
  `video.source_uri` with the derived audio URI
  (`audio/{video_id}.wav`). A redelivery re-runs `probe_duration_s` +
  `extract_audio_wav` over the already-extracted WAV and re-uploads
  the same key. No corruption (`deduct_for_video` no-ops via
  `UNIQUE(video_id)`; duration only set when unset), but wastes
  ffmpeg + R2 round-trips. Wave-3 unchanged. | fix: short-circuit when
  `source_uri` already points at the derived audio key
  (`source_uri.startswith("s3://" + bucket + "/audio/")` or
  `Path(source_uri).suffix == ".wav"`), or gate on
  `ingest_status == done` before opening `alocal_path`.

- [SEV2] `worker/tasks.py:801–894` — `_poll_clip_outcomes_async` does
  not break on YouTube quota exhaustion. Per-outcome
  `get_video_stats` failures are swallowed by the broad `except
  Exception: continue` at line 868–875, so a quota-out creator walks
  the whole candidate set firing doomed YouTube calls. Bounded by
  the 10-day `cutoff_created` cap (line 822), but wasteful and
  noisy. Wave-3 unchanged. | fix: catch `QuotaExhaustedError`
  explicitly inside the inner loop and `break` (mirror the
  analytics-refresh pattern at `worker/tasks.py:1244–1250`),
  committing partial progress first.

### Cleanups (carried forward)

- [cleanup] `worker/storage.py:46–49` + `.env.example` —
  `LOCAL_MEDIA_DIR=./media` is a relative default and `_local_root()`
  resolves it with bare `Path(...)` against the worker cwd
  (CLAUDE.md "all paths absolute"). Dev-only
  (`STORAGE_BACKEND != r2`), low risk. | fix:
  `Path(settings.LOCAL_MEDIA_DIR).resolve()` in `_local_root()`, or
  ship an absolute default.

- [cleanup] `worker/progress.py:74–85` — `_serialize` returns
  `{type, ts, request_id, data:<json>}`. The wire shape is not
  documented in the module docstring; SSE consumers + `routers/tasks.py`
  decoder rely on it implicitly. Wave-3 again widens the consumer
  contract (the generate_clips done shape, the sync_metrics_skipped
  shape). | fix: document the wire shape in the module docstring and
  link `routers/tasks.py` as canonical decoder.

- [cleanup] `worker/progress.py:170–184` — `aset_owner` / `aget_owner`
  have no try/except wrapper, unlike `aemit`. This is *correct* (auth
  must be load-bearing) but the asymmetry is undocumented; a future
  contributor "fixing consistency" by wrapping these in try/except
  would silently break SSE auth. The Wave-3 Fix D in `routers/auth.py`
  added a router-side `RedisError` catch around `aset_owner` for the
  initial-onboarding case — that's the right shape (each caller
  decides its own fail-open posture), but reinforces the case for an
  in-module comment. | fix: add a one-line comment on each: "MUST
  raise — the ownership key is the SSE authorization invariant.
  Caller decides fail-open posture."

- [cleanup] `worker/progress.py:239–244` — `arelease_slot` calls `DECR`
  with no clamp at zero; the counter goes negative on
  disconnect-after-TTL. Acknowledged in the docstring. Worth a
  one-Lua-roundtrip clamp:
  ```lua
  local v = redis.call('DECR', KEYS[1]); if v < 0 then redis.call('SET', KEYS[1], 0) end
  ```

### Cross-cutting (verified, wave-3)

- ✅ **No blocking calls in any new emit path.** `_generate_clips_async`
  uses `await aemit(...)` on the worker's singleton event loop;
  `generate_and_rank_clips` is awaited; nothing on the event loop
  thread blocks. `sync_emit(...)` is still reserved exclusively for
  the Anthropic streaming callback running inside `asyncio.to_thread`.
- ✅ **No PII / token leakage in Wave-3 emit payloads.** New fields:
  `clip_count` (int from `len(clips)`), `reason=type(exc).__name__`
  (class-name string). Both structurally safe. Static `message=` on
  the error path uses the same generic phrasing pattern as the rest
  of the upload chain.
- ✅ **Idempotency invariants preserved.** `_generate_clips_async`
  short-circuit (line 933–945) retained; emits `done` from the
  short-circuit branch so a redelivered task closes its SSE
  consumer rather than leaving it pending. `_signals_async` no longer
  emits `done` — eliminates the prior wave-2 "double terminal under
  build_signals redelivery" wart (a re-execution now emits
  `ingest_complete` step twice but no duplicate terminal).
- ✅ **Stream key scoping unchanged.** `video_id` for the upload chain
  (ingest → transcribe → signals → generate_clips), `clip_id` for
  render, `task_id` (Celery task id) for catalog sync + DNA build +
  improvement brief. Per-creator isolation enforced via
  `task:{task_id}:owner` lookup at the SSE endpoint.
- ✅ **Per-creator scoping for every Wave-3 query.** `_generate_clips_async`
  reads `Video`, `Signals`, `Transcript` keyed on `video_uuid` (PK),
  `Clip` filtered by `video_id == video_uuid`; `creator_id` propagates
  via `video.creator_id`. `_sync_channel_catalog_async` per-video
  loop carries `Video.creator_id == creator.id` on the unmeasured
  query (line 1118). No cross-tenant leak introduced.
- ✅ **Honesty/no-virality strings.** Wave-3 emit messages: "Clip
  generation failed; retrying.", `f"Generated {len(clips)} clip(s)."`,
  "Clips already generated.", "YouTube auth unavailable; reconnect.",
  "Catalog sync failed; retrying.", "Synced {N} new video(s).". None
  promise virality or guarantee performance.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | 4 findings (concurrent-render double-encode, ingest re-do, async-client rebuild-without-aclose, aemit churn on transient failures). All carried from wave-2. |
| 2 Concurrency & scale | 2 findings (poll_clip_outcomes no quota break, blocking XREAD pool sizing — pressure further widened by Wave-3's later terminal-done). |
| 3 Security & compliance | 2 findings (raw ValueError leak into SSE event; unschema'd emit payload). Wave-3 owner-stamp gap in auth.py CLOSED. Per-creator scoping verified end-to-end. |
| 4 Clip-quality | n/a (orchestration module; ranking is in clip_engine). |
| 5 Anthropic SDK | 2 findings (no terminal emit on stream interrupt; silent-zero usage defaults). Wave-3 Fix A verified — no new SDK-shape regression. |
| 6 Cleanliness & typing | 4 cleanups (relative LOCAL_MEDIA_DIR default; wire-shape contract not in docstring; aset_owner/aget_owner load-bearing asymmetry undocumented; arelease_slot zero-clamp). |
| 7 Error handling / API | ok — Wave-3 closed the per-video silent-skip finding from wave-2; all worker error paths now emit either a terminal `error` or a contiguous skip step. |
| 8 Config & paths | ok — no new config introduced in wave-3; carry-over relative-path cleanup. |

## Module verdict

NEEDS-WORK — no BLOCKERs. Wave-3 closes three SEV2s cleanly (Fix D
owner-stamp in routers/auth.py; Fix E terminal-done lifecycle moved
to _generate_clips_async; Fix F per-video skip emits with class-name-
only reason). Each fix is structurally correct: idempotency invariants
preserved, no PII leakage, no blocking calls, no cross-tenant leakage.
Eight carried-forward SEV2s and four cleanups remain unchanged. The
highest-leverage remaining work is the `worker/progress.py` pool
sizing + aemit-churn pair (SEV2 ×2) — both compound under Wave-3's
wider emit-call-site surface and will bite first under the 200-creator
scale target. The render double-encode race and ingest re-do are
real but bounded (wasted compute, not corruption). The Anthropic
stream-interrupt gap now affects both DNA + improvement-brief flows
post-Wave-3 and remains the highest-value SEV2 in the SDK category.
