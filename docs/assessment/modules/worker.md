# worker — assessed 2026-05-30

Slice: `worker/__init__.py`, `worker/celery_app.py`, `worker/schedule.py`,
`worker/storage.py`, `worker/tasks.py`, **`worker/progress.py` (NEW)**,
**`worker/anthropic_stream.py` (NEW)**. Re-assessment after Issue 86 (live SSE
progress) landed. Cross-module claims (routers/tasks.py, dna/brief.py,
observability.py, routers/creators.py) traced by reading, not assumed.

## Findings

### Carried forward from 2026-05-29 (still present)

- [SEV2] worker/tasks.py:547–556 — `_poll_clip_outcomes_async` does **not**
  break on YouTube quota exhaustion. Per-outcome `get_video_stats` failures
  are swallowed by `except Exception: continue` (line 721–728), so a quota-out
  creator walks the whole candidate set firing doomed YouTube calls. Bounded
  by the 10-day `cutoff_created` cap (line 675) and committed per-creator, but
  wasteful and noisy and contrary to COMPLIANCE §4. | fix: catch
  `QuotaExhaustedError` explicitly inside the inner loop and `break` out (mirror
  the analytics-refresh handler at tasks.py:906–912), committing partial
  progress first; optionally gate the inner loop on `await remaining()`.

- [SEV2] worker/tasks.py:416–474 — `_render_clip_async` idempotency guard
  (line 426: skip if `render_status == done and render_uri`) protects the
  *sequential* redelivery-after-success case but **not concurrent** delivery:
  with `acks_late` + `reject_on_worker_lost`, two workers can both read
  `pending`, both flip to `running`, both encode + `upload_file` to the same
  key `clips/{clip_id}.mp4` (storage.py:52–60 overwrites). Result is a wasted
  double encode/upload, not corruption (identical bytes, last-writer-wins). |
  fix: `select(Clip).where(...).with_for_update()` on the Clip row in the
  opening session and re-check `render_status` under the lock before flipping
  to `running`, so the second worker observes `running`/`done` and bails.

- [SEV2] worker/tasks.py:278–317 — `_ingest_async` is not a clean no-op on
  redelivery after a successful commit. First run overwrites
  `video.source_uri` with the derived audio URI (`audio/{video_id}.wav`,
  line 310). A redelivery then re-`probe_duration_s` + `extract_audio_wav`
  over the already-extracted WAV and re-uploads the same key. No corruption
  (billing is idempotent via UNIQUE(video_id), `deduct_for_video` no-ops on
  conflict; duration only set when unset — line 311), but it re-does ffmpeg
  work and re-downloads from R2. | fix: short-circuit when source_uri already
  points at the derived audio key (e.g. `source_uri.startswith("audio/")` or
  suffix `.wav`) or gate on `ingest_status == done` before opening
  `alocal_path`.

- [cleanup] .env.example / worker/storage.py:46–49 — `LOCAL_MEDIA_DIR=./media`
  is a relative default and `_local_root()` resolves it with bare `Path(...)`
  against the worker cwd (CLAUDE.md "all paths absolute"). Dev-only
  (`STORAGE_BACKEND != r2`), so low risk. | fix: `Path(settings.LOCAL_MEDIA_DIR).resolve()`
  in `_local_root()`, or ship an absolute default.

### Resolved since 2026-05-29

- ✅ `_build_dna_async` double-pay race — Issue 76 added
  `pg_advisory_xact_lock(hashtext(creator_id))` (tasks.py:526–529) + re-check
  of `build_job_id` UNDER the lock (tasks.py:533–543) + the partial UNIQUE on
  `creator_dna.build_job_id` (migration 0008, cited at tasks.py:493) as
  structural backstop, plus an IntegrityError handler around the commit
  (tasks.py:621–630) that converts the loser to a clean no-op. The prior
  SEV2 (double Anthropic brief + Voyage embed on concurrent redelivery) is
  closed.

### New findings — Issue 86 (worker/progress.py + worker/anthropic_stream.py + tasks.py emit wiring)

- [SEV1] worker/progress.py:214–232 — `aacquire_slot` EXPIRE is set **only**
  on the INCR→1 transition. If a creator holds N>0 SSE streams continuously
  past `_STREAM_TTL_SECONDS=3600s`, the key TTL elapses → counter resets to
  0 → live `arelease_slot` calls DECR into negatives. Once negative, the cap
  is **silently bypassed** until the next TTL refresh fires — an authed
  creator can hold-open many more than 3 streams in a steady-state attack.
  Also: there is no TTL refresh on subsequent INCRs, so under continuous load
  the counter inevitably races the TTL. | fix: `EXPIRE` on every INCR (not
  just count==1) so the key TTL is refreshed while in use, OR drop the
  TTL-based recovery and use a sorted-set with per-stream entries that expire
  individually (`ZADD task:{cid}:streams <expire_ts> <stream_uuid>` +
  `ZREMRANGEBYSCORE` on each acquire). The sorted-set version is the
  industry-standard concurrency-limit pattern.

- [SEV2] worker/progress.py:235–240 — `arelease_slot` calls `DECR` with no
  guard against the counter being absent (TTL expired) or already 0; the
  counter goes negative and stays negative until the next missing-key INCR
  resets it. The comment acknowledges this but the fix is one extra Lua
  round-trip: `if redis.call('DECR', KEYS[1]) < 0 then redis.call('SET',
  KEYS[1], 0) end` (or the cheaper `MAX(0, current-1)` pattern). Without it,
  the cap is unreliable in steady-state. | fix: clamp at 0 with a small Lua
  script or `redis.call("eval", ...)`; alternately collapse with the SEV1
  fix above into a sorted-set scheme that has no negative state.

- [SEV2] worker/progress.py:130–164 — `_async_client()` rebuilds the
  singleton on loop-mismatch but **does not close the old client first**. In
  prod (one loop per worker) this never triggers. In pytest (function-scoped
  loops) a fresh aredis.Redis is constructed every test, the previous one is
  abandoned with its connection pool half-bound to a dead loop. Each
  abandoned client logs `Event loop is closed` at GC and may hold sockets
  until process exit. Tests pass but the noise can drown real warnings. |
  fix: in the rebuild branch, best-effort close the old reference before
  rebinding:
  ```python
  if _AIO is not None and _AIO_LOOP is not current:
      old = _AIO
      try: await old.aclose()
      except Exception: pass
  ```
  Since `_async_client()` is sync, the cleaner pattern is to expose an
  `await arebind()` helper that the test fixture (or `aclose()`) calls, or
  schedule `old.aclose()` onto the old loop if it's still running. Mark
  `(needs-runtime-confirmation)` on the socket-leak severity — a runtime
  `lsof` would settle it.

- [SEV2] worker/progress.py:149–164 — `aemit` swallows ALL exceptions
  (correct: observability must not be load-bearing) AND on exception nulls
  out `_AIO` / `_AIO_LOOP` globals (line 161–163) without locking. Under
  concurrent emits, thread A's reset between thread B's `_async_client()`
  call and thread B's `await client.xadd(...)` is benign because B holds a
  local reference — but thread B's failure path then **races** to reset the
  *new* client thread A just built, causing one extra rebuild per concurrent
  failure. Effect: pathological churn under a Redis brown-out (every concurrent
  emit rebuilds the pool), worsening the very condition the swallow was
  protecting against. | fix: only reset on the specific error classes that
  warrant it (`ConnectionError`, `TimeoutError`, `redis.exceptions.RedisError`
  with a connection cause); leave the singleton intact on transient XADD
  failures or assertion errors so the next call reuses the pool. Pin with a
  `_RESET_LOCK = asyncio.Lock()` if multiple loops are ever a concern.

- [SEV2] worker/progress.py:188–208 — `aread_since` uses `XREAD` with
  `block=block_ms` (5000 by default). Each blocked XREAD holds **one Redis
  connection for the full block duration**. redis-py's async pool default is
  `max_connections=50` per client. With ~100 concurrent SSE consumers (well
  inside Issue 86's stated 200-creator scale), the pool exhausts and new
  emits/reads queue or fail. The progress stream is now a hard cap on web
  concurrency. | fix: bound the async client's pool explicitly at construction
  (`aredis.from_url(..., max_connections=N)`) with `N >=
  MAX_CONCURRENT_SSE_PER_CREATOR * expected_concurrent_creators + headroom`;
  document the math in DEPLOYMENT.md alongside the PgBouncer math. Use a
  separate redis client for SSE reads vs writes so a wedged reader pool
  doesn't starve worker emits. `(needs-runtime-confirmation via load test)`.

- [SEV2] worker/progress.py:74–85 — `_serialize` does `json.dumps(fields,
  default=str)`. Every value passed to `aemit`/`sync_emit` becomes a string
  in the event payload. Callers in `_build_dna_async` (tasks.py:559–566,
  638) pass only counts and version ints — safe today — but there is **no
  schema or allowlist** preventing a future caller from passing an exception
  arg, an OAuth token slice, or a transcript snippet through `default=str`.
  The two existing token-leak guards (`message="DNA build failed; retrying"`
  at tasks.py:649; `message=str(exc)` at tasks.py:643 for ValueError) rely
  on every author remembering the rule. | fix: add a fixed `ALLOWED_FIELDS`
  set per event type, or a `EventPayload(BaseModel)` per event type with
  strict typing; reject unknown fields at the emit boundary. Cheapest: an
  emit-level guard that logs+drops any field whose stringified length exceeds
  N or matches a token-shape regex. The SEV2 here is "structural risk for
  the next contributor" not "leak today" — current call sites are clean.

- [SEV2] worker/tasks.py:643 — `_emit("error", message=str(exc))` for
  `ValueError` raw-passes the exception message to the SSE stream. Today's
  raisers in this code path are safe data-gate messages (e.g.
  `f"Creator {creator_id} not found"`, `"Video {video_id} has no source_uri"`),
  but a downstream library throwing a ValueError with a token or DB error in
  the message would leak it to an authenticated subscriber. The SEV2 is the
  unverified trust boundary — sanitize, don't trust. | fix: maintain an
  allowlist of known-safe data-gate messages OR sanitize via a small
  classifier (`"creator/video/dna not found"`, `"X is empty"`, etc.) and
  fall back to a generic `"validation failed"` for anything else. Same
  applies to any future `ValueError` raised inside the build.

- [SEV2] worker/anthropic_stream.py:57–75 — the Anthropic stream context
  manager runs synchronously inside `asyncio.to_thread` (good). If the
  network drops mid-stream the SDK raises *inside* the `for event in stream:`
  loop, escapes the `with` block, and propagates up to the calling
  `asyncio.to_thread(generate_brief, ...)`. There is **no terminal `error`
  emit** at this layer — the caller in `_build_dna_async` catches the
  exception and emits a generic error (good), but mid-stream tokens already
  delivered to the UI are now followed by an undefined gap before the
  caller's terminal emit fires (it must finish unwinding the to_thread, the
  outer try/except, then `aemit`). On a slow brown-out the SSE consumer's
  keepalive logic must hold for >100ms — usually fine, but if the broker
  also flakes the user sees a frozen stream with no terminal event. |
  fix: wrap the streaming call site (dna/brief.py:136 or here) in
  `try/except: sync_emit(task_id, "error", message="stream interrupted"); raise`
  so the terminal event lands on the same Redis call path as the deltas
  (lower latency than the multi-layer async unwind). Low-risk; the caller's
  emit still fires as backup.

- [SEV2] worker/anthropic_stream.py:72–84 — `text_blocks[-1].text` mirrors
  dna/brief.py's pattern (good) but `usage_dict` casts every field to a
  Python int via `getattr(usage, ..., 0)`. The Anthropic SDK returns those
  as ints, so this is safe today, but `0` as a default *silently hides* a
  schema change in a future SDK bump where the field is renamed or moved to
  a sub-object (relevant because /claude-api in the brief explicitly calls
  out the post-0.40 SDK bump). The "cache_read=0" reported to logs would
  then under-report savings without raising. | fix: distinguish "absent" from
  "zero" — return `None` when the attribute is missing, and a callable like
  `_required_int(usage, "input_tokens")` for fields that MUST be present.
  Log a warning the first time a previously-present field returns None so
  the SDK-bump regression is loud, not silent.

- [cleanup] worker/progress.py:74–85 — `_serialize` returns dict keyed by
  `type`, `ts`, `request_id`, `data`. Redis Stream entries are flat
  string→string; nesting all event-specific payload inside one JSON-string
  `data` field is the right call (XADD field churn would be worse) but is
  worth a one-line comment in the docstring documenting the shape so SSE
  consumers and the routers/tasks.py parser stay in sync. Not a defect; cite
  the contract explicitly. | fix: extend the module docstring with the wire
  shape `{type, ts, request_id, data:<json>}` and link routers/tasks.py:105
  as the canonical decoder.

- [cleanup] worker/progress.py:170–184 — `aset_owner` / `aget_owner` have
  **no try/except wrapper**, unlike `aemit`. If Redis is down at task
  enqueue, `aset_owner` raises and the `build_dna` API endpoint 500s. This
  is *correct* (auth must be load-bearing — better to refuse the build than
  silently create an orphan task no one can attach to), but the asymmetry
  vs `aemit`'s swallow is not documented; a future contributor "fixing
  consistency" by wrapping these in try/except would silently break SSE
  auth. | fix: add an explicit one-line comment on each: "MUST raise — the
  ownership key is the SSE authorization invariant; a swallow here would
  let a leaked task_id read another creator's stream after a Redis blip."

### Cross-cutting (observability ≠ load-bearing)

- ✅ `sync_emit` (progress.py:100–117) is correctly bare-`except Exception`
  and only warns — verified the call path from the Anthropic streaming
  callback (`stream_and_emit._forward_event`) cannot abort iteration on a
  Redis blip; the per-event `try/except` in anthropic_stream.py:64–69 is the
  second layer, both guard correctly.
- ✅ Issue 86 brief promise: "cache stats BEFORE first token" — traced:
  anthropic_stream.py:94–107 fires `sync_emit(task_id, "cache", ...)` on
  the `message_start` event, which the Anthropic SDK guarantees as the
  first event before any `content_block_*`. Promise honored.
- ✅ Per-creator scoping: `task_id` in the Redis stream key is gated by
  the `task:{task_id}:owner` lookup in `routers/tasks.py:131–138` — owner
  None → 404, mismatch → 403. The stream key itself is not creator-scoped
  (it's task-scoped) but the only authenticated read path enforces ownership,
  so a leaked task_id cannot read another creator's events. Verified.
- ✅ `_emit(...)` correctly short-circuits when `progress_enabled` is False
  (`_build_dna_async` direct-invocation in unit tests), so existing mocks
  of `_build_dna_async` continue to pass without Redis. (tasks.py:512–516)
- ✅ `request_id_ctx.get()` in `_serialize` (progress.py:83) has a default
  `"-"` set at observability.py:37, so it never raises in a worker context
  even if `install_celery_observability()` wasn't called. Defensive default
  confirmed.
- ✅ No PII / token in any new `logger.*` call across progress.py,
  anthropic_stream.py, or the new tasks.py emit sites — only task_id (a
  UUID), creator_id (UUID via build flow), version ints, and SDK-provided
  exception class names. No raw exception messages logged for emit failures
  (formatted via `%s` not `%r`, which is the safer choice).
- ✅ `aclose()` (progress.py:246–278) correctly handles the dead-loop case
  — drops references and returns rather than calling `aclose` on the dead
  loop. Verified.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | 4 findings (concurrent-render double-encode, ingest re-do, async-client churn on Redis failure, abandoned aredis clients across pytest loops) |
| 2 Concurrency & scale | 3 findings (poll_clip_outcomes no quota break, aacquire_slot TTL drift → cap bypass, blocking XREAD pool sizing under SSE concurrency) |
| 3 Security & compliance | 2 findings (raw ValueError leak into SSE event, unschema'd emit payload — both structural, both NOT exploitable today); ownership + per-creator scoping verified end-to-end |
| 4 Clip-quality | n/a (orchestration module) |
| 5 Anthropic SDK | 2 findings (no terminal emit on stream interrupt; silent-zero usage defaults hide schema drift); cache-before-first-token promise verified |
| 6 Cleanliness & typing | 2 cleanups (relative LOCAL_MEDIA_DIR default; undocumented aset_owner/aget_owner load-bearing asymmetry, wire-shape contract not in docstring) |
| 7 Error handling / API | n/a (worker is not a router; routers/tasks.py owned by routers module) |
| 8 Config & paths | ok — no new config introduced by Issue 86 beyond reusing REDIS_URL; 1 carry-over relative-path cleanup |

## Module verdict

NEEDS-WORK — no BLOCKERs; Issue 86 hardware (loop-aware singleton, cache-stats
before first token, idempotent skip when task_id=None, ownership-key auth
enforced at the SSE endpoint, defensive sync_emit swallow) is verified
correctly in place. The new SEV1 — `aacquire_slot` TTL drift silently
bypassing the per-creator concurrent-SSE cap — and four SEV2s on the Issue 86
surface (async-client churn under failure, blocking-XREAD pool sizing,
raw-ValueError leak path, no terminal emit on mid-stream interrupt) join the
four carried-forward worker SEV2s (concurrent double-render, ingest re-do,
poll_clip_outcomes quota break, abandoned aredis clients in tests). The
prior DNA-double-pay SEV2 is resolved by the Issue 76 advisory lock + partial
UNIQUE.
