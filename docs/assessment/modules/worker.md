# worker — assessed 2026-05-31

Slice: `worker/__init__.py`, `worker/celery_app.py`, `worker/schedule.py`,
`worker/storage.py`, `worker/tasks.py`, `worker/progress.py`,
`worker/anthropic_stream.py`. Re-assessment after Wave-1 Hotfix A landed on
`worker/progress.py` (uncommitted in working tree; baseline commit 74431e7).
Cross-module claims (routers/tasks.py, dna/brief.py, observability.py,
routers/creators.py) traced by reading, not assumed.

## Findings

### Resolved this wave

- ✅ **Hotfix A verified — SEV1 closed.** `worker/progress.py:222–236`
  `aacquire_slot` now calls `await client.expire(key, _STREAM_TTL_SECONDS)`
  unconditionally on every INCR (line 232), outside the `if count >
  max_concurrent` branch. The previously-flagged TTL-cap-bypass drift (a
  creator who steady-state-held N≥1 streams past 3600s would see the counter
  expire under them and the next INCR reset to 1, silently bypassing the cap)
  can no longer occur: every successful acquire refreshes the sliding window.
  The comment at lines 225–231 documents the prior failure mode for the next
  contributor. The two-op race window remains (~ms; flagged as acceptable in
  the docstring at lines 217–220 — bounded "exceed-by-1-or-2" not "unbounded
  bypass").

### Carried forward — still present in this wave

- [SEV2] `worker/progress.py:134–146` — `_async_client()` rebuilds the
  singleton on loop-mismatch but **still does not close the old client first**.
  In prod (one loop per worker process) this never triggers. In pytest
  (function-scoped loops) a fresh `aredis.Redis` is constructed every test,
  the previous one is abandoned with its connection pool half-bound to a dead
  loop. Each abandoned client logs `Event loop is closed` at GC and may hold
  sockets until process exit. Tests pass, but the noise drowns real warnings
  and the socket leak is `(needs-runtime-confirmation via lsof)`. | fix: in
  the rebuild branch, best-effort close the old reference before rebinding:
  ```python
  if _AIO is not None and _AIO_LOOP is not current:
      old, old_loop = _AIO, _AIO_LOOP
      _AIO = None
      _AIO_LOOP = None
      if old_loop is not None and not old_loop.is_closed():
          old_loop.call_soon_threadsafe(lambda: asyncio.ensure_future(old.aclose()))
  ```
  or expose an `await arebind()` helper the test fixture calls explicitly.

- [SEV2] `worker/progress.py:149–164` — `aemit` exception handler **still**
  nulls out `_AIO` / `_AIO_LOOP` globals on *any* exception, including
  transient XADD errors that have nothing to do with the connection. Under
  concurrent emits during a Redis brown-out, every concurrent emit rebuilds
  the pool, worsening the brown-out (pathological churn). Pool-rebuild also
  abandons in-flight reads on `aread_since` and forces the SSE consumers to
  re-establish — visible UX flicker. | fix: only reset on connection-class
  errors:
  ```python
  except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as exc:
      global _AIO, _AIO_LOOP
      _AIO = None
      _AIO_LOOP = None
      logger.warning(...)
  except Exception as exc:
      logger.warning(...)  # leave singleton intact for transient XADD failures
  ```
  Pin with an `asyncio.Lock()` if multi-loop concurrency ever lands.

- [SEV2] `worker/progress.py:190–208` — `aread_since` uses `XREAD` with
  `block=5000` (default). Each blocked XREAD holds **one Redis connection for
  the full block duration**. redis-py's async pool default is
  `max_connections=50` per client. With ~100 concurrent SSE consumers (well
  inside the stated 200-creator scale), the pool exhausts and new emits/reads
  queue or fail. The progress stream then becomes a hard cap on web
  concurrency. | fix: bound the async client's pool explicitly at
  construction:
  ```python
  _AIO = aredis.from_url(
      settings.REDIS_URL,
      decode_responses=True,
      max_connections=settings.REDIS_MAX_CONNECTIONS,  # default 200
  )
  ```
  Add a separate redis client for SSE reads vs writes so a wedged reader pool
  doesn't starve worker emits. Document the math in `docs/DEPLOYMENT.md`
  alongside the PgBouncer math. `(needs-runtime-confirmation via load test)`.

- [SEV2] `worker/progress.py:74–85` — `_serialize` does `json.dumps(fields,
  default=str)`. Every value passed to `aemit`/`sync_emit` becomes a string in
  the event payload. Callers in `_build_dna_async` (tasks.py:572–588, 660)
  pass only counts and version ints — safe today — but there is **no schema
  or allowlist** preventing a future caller from passing an exception arg, an
  OAuth token slice, or a transcript snippet through `default=str`. The two
  existing token-leak guards (`message="DNA build failed; retrying"` at
  tasks.py:671; `message=str(exc)` at tasks.py:665 for `ValueError`) rely on
  every author remembering the rule. | fix: add a per-event-type
  `EventPayload(BaseModel)` with strict typing and reject unknown fields at
  the emit boundary. Cheapest interim: an emit-level guard that logs+drops
  any field whose stringified length exceeds N or matches a token-shape regex
  (`^sk-ant-`, `^ya29\.`, `^[A-Za-z0-9+/]{100,}={0,2}$`).

- [SEV2] `worker/tasks.py:661–666` — `_emit("error", message=str(exc))` for
  `ValueError` raw-passes the exception message to the SSE stream. Today's
  raisers in this code path are safe data-gate messages (e.g.
  `f"Creator {creator_id} not found"`, `"Video {video_id} has no source_uri"`),
  but a downstream library throwing a `ValueError` with a token or DB error
  in the message would leak it to an authenticated subscriber. The SEV2 is
  the unverified trust boundary. | fix: maintain an allowlist of
  known-safe data-gate messages OR sanitize via a small classifier
  (`"creator/video/dna not found"`, `"X is empty"`) and fall back to a
  generic `"validation failed"` for anything else.

- [SEV2] `worker/tasks.py:415–496` — `_render_clip_async` idempotency guard
  (line 448: skip if `render_status == done and render_uri`) protects the
  *sequential* redelivery-after-success case but **not concurrent** delivery:
  with `acks_late` + `reject_on_worker_lost`, two workers can both read
  `pending`, both flip to `running`, both encode + upload to the same key
  `clips/{clip_id}.mp4` (storage.py:52–60 overwrites). Result: wasted double
  encode/upload, not corruption (identical bytes, last-writer-wins). | fix:
  use `select(Clip).where(...).with_for_update()` on the Clip row in the
  opening session and re-check `render_status` under the lock before flipping
  to `running`, so the second worker observes `running`/`done` and bails.

- [SEV2] `worker/tasks.py:300–339` — `_ingest_async` is not a clean no-op on
  redelivery after a successful commit. First run overwrites
  `video.source_uri` with the derived audio URI (`audio/{video_id}.wav`,
  line 332). A redelivery then re-`probe_duration_s` + `extract_audio_wav`
  over the already-extracted WAV and re-uploads the same key. No corruption
  (`deduct_for_video` no-ops on conflict via `UNIQUE(video_id)`; duration
  only set when unset — line 333), but wastes ffmpeg + R2 round-trips. |
  fix: short-circuit when `source_uri` already points at the derived audio
  key (`source_uri.startswith("s3://" + bucket + "/audio/")` or
  `Path(source_uri).suffix == ".wav"`) or gate on `ingest_status == done`
  before opening `alocal_path`.

- [SEV2] `worker/tasks.py:737–769` — `_poll_clip_outcomes_async` does **not**
  break on YouTube quota exhaustion. Per-outcome `get_video_stats` failures
  are swallowed by `except Exception: continue` (line 743–750), so a
  quota-out creator walks the whole candidate set firing doomed YouTube
  calls. Bounded by the 10-day `cutoff_created` cap (line 697) and committed
  per-creator, but wasteful and noisy. | fix: catch `QuotaExhaustedError`
  explicitly inside the inner loop and `break` out (mirror the
  analytics-refresh handler at tasks.py:1019–1025), committing partial
  progress first; optionally gate the inner loop on `await remaining()`.

- [SEV2] `worker/anthropic_stream.py:57–75` — the Anthropic stream context
  manager runs synchronously inside `asyncio.to_thread` (good). If the
  network drops mid-stream, the SDK raises *inside* the `for event in
  stream:` loop, escapes the `with` block, and propagates up to the calling
  `asyncio.to_thread(generate_brief, ...)`. There is **no terminal `error`
  emit at this layer** — the caller in `_build_dna_async` catches the
  exception and emits a generic error (good), but mid-stream tokens already
  delivered to the UI are now followed by an undefined gap before the
  caller's terminal emit fires. On a slow brown-out the SSE consumer's
  keepalive logic must hold for >100ms — usually fine, but if the broker
  also flakes the user sees a frozen stream with no terminal event. | fix:
  wrap the streaming call site (here or `dna/brief.py`) in
  `try/except: sync_emit(task_id, "error", message="stream interrupted"); raise`
  so the terminal event lands on the same Redis call path as the deltas
  (lower latency than the multi-layer async unwind). Low-risk; the caller's
  emit still fires as backup.

- [SEV2] `worker/anthropic_stream.py:72–84` — `usage_dict` casts every field
  to a Python int via `getattr(usage, ..., 0)`. The Anthropic SDK returns
  those as ints, so this is safe today, but `0` as default *silently hides*
  a schema change in a future SDK bump where the field is renamed or moved
  to a sub-object (relevant: `/claude-api` brief explicitly calls out the
  post-0.40 SDK bump in Issue 84). The `cache_read=0` reported to logs would
  then under-report savings without raising. | fix: distinguish "absent" from
  "zero" — return `None` when the attribute is missing; log a warning the
  first time a previously-present field returns None so the SDK-bump
  regression is loud, not silent.

- [cleanup] `.env.example` / `worker/storage.py:46–49` —
  `LOCAL_MEDIA_DIR=./media` is a relative default and `_local_root()`
  resolves it with bare `Path(...)` against the worker cwd
  (CLAUDE.md "all paths absolute"). Dev-only (`STORAGE_BACKEND != r2`), so
  low risk. | fix: `Path(settings.LOCAL_MEDIA_DIR).resolve()` in
  `_local_root()`, or ship an absolute default.

- [cleanup] `worker/progress.py:74–85` — `_serialize` returns a dict keyed by
  `type`, `ts`, `request_id`, `data`. Nesting all event-specific payload
  inside one JSON-string `data` field is the right call, but the wire shape
  is not documented in the module docstring; SSE consumers and
  `routers/tasks.py` decoder rely on the contract implicitly. | fix: extend
  the module docstring with the wire shape `{type, ts, request_id,
  data:<json-encoded fields>}` and link `routers/tasks.py` as the canonical
  decoder.

- [cleanup] `worker/progress.py:170–184` — `aset_owner` / `aget_owner` have
  **no try/except wrapper**, unlike `aemit`. This is *correct* (auth must be
  load-bearing) but the asymmetry is undocumented; a future contributor
  "fixing consistency" by wrapping these in try/except would silently break
  SSE auth. | fix: add an explicit one-line comment on each: "MUST raise —
  the ownership key is the SSE authorization invariant; a swallow here would
  let a leaked task_id read another creator's stream after a Redis blip."

- [cleanup] `worker/progress.py:239–244` — `arelease_slot` calls `DECR` with
  no clamp at zero; the counter goes negative on disconnect-after-TTL and
  stays negative until the next missing-key INCR. The docstring at line 243
  acknowledges it, but with Hotfix A the EXPIRE-on-every-INCR now refreshes
  the TTL while in use, so the negative-state window is shorter — still
  worth a one-Lua-roundtrip clamp:
  ```lua
  local v = redis.call('DECR', KEYS[1]); if v < 0 then redis.call('SET', KEYS[1], 0) end
  ```
  Severity downgraded from SEV2 → cleanup post-Hotfix A: with the TTL now
  refreshed on every acquire, the only path into negatives is a release
  after a connection idle past 3600s, which the dedicated keepalive in the
  SSE loop should prevent in practice.

### Cross-cutting (verified)

- ✅ `sync_emit` (progress.py:100–117) is correctly bare-`except Exception`
  and only warns — verified the call path from the Anthropic streaming
  callback (`stream_and_emit._forward_event`) cannot abort iteration on a
  Redis blip; the per-event `try/except` in anthropic_stream.py:64–69 is the
  second layer, both guard correctly.
- ✅ Issue 86 brief promise: "cache stats BEFORE first token" — traced:
  anthropic_stream.py:94–107 fires `sync_emit(task_id, "cache", ...)` on the
  `message_start` event, which the Anthropic SDK guarantees as the first
  event before any `content_block_*`. Promise honored.
- ✅ Per-creator scoping for SSE: `task_id` in the Redis stream key is gated
  by the `task:{task_id}:owner` lookup; only authenticated read path
  enforces ownership, so a leaked task_id cannot read another creator's
  events.
- ✅ `_emit(...)` short-circuits when `progress_enabled` is False
  (`_build_dna_async` direct-invocation in unit tests). (tasks.py:534–538)
- ✅ `request_id_ctx.get()` in `_serialize` has a default `"-"` set at
  observability.py, so it never raises in a worker context even if
  `install_celery_observability()` wasn't called.
- ✅ No PII / token in any `logger.*` call across the slice — only task_id,
  creator_id, version ints, and exception class names. Emit-failure logs
  use `%s` not `%r`.
- ✅ `aclose()` (progress.py:250–282) correctly handles the dead-loop case
  — drops references and returns rather than calling `aclose` on the dead
  loop.
- ✅ `RefundOnFailureTask` (tasks.py:56–85) refund path is bounded — refund
  helper failures are caught and only logged; the task's terminal failure
  is allowed to stand. No risk of refund-loop swallowing the original
  failure.
- ✅ Idempotency on `_build_dna_async` (tasks.py:540–660) confirmed:
  `pg_advisory_xact_lock(hashtext(creator_id))` + re-check of `build_job_id`
  UNDER the lock + partial UNIQUE on `creator_dna.build_job_id` (migration
  0008) + IntegrityError handler around the commit. Closes the prior
  double-pay SEV2.
- ✅ Per-creator scoping for `_poll_clip_outcomes_async`,
  `_refresh_youtube_analytics_async`, `_sync_channel_catalog_async`,
  `_generate_improvement_brief_async` — every query carries
  `Video.creator_id == ...` or `creator.id` filters; no cross-tenant leak
  surface in this slice.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | 4 findings (concurrent-render double-encode, ingest re-do, async-client rebuild-without-aclose in tests, aemit churn on transient failures) |
| 2 Concurrency & scale | 2 findings (poll_clip_outcomes no quota break, blocking XREAD pool sizing). Hotfix A closed the TTL-cap-bypass SEV1. |
| 3 Security & compliance | 2 findings (raw ValueError leak into SSE event, unschema'd emit payload — both structural, both NOT exploitable today); ownership + per-creator scoping verified end-to-end. |
| 4 Clip-quality | n/a (orchestration module) |
| 5 Anthropic SDK | 2 findings (no terminal emit on stream interrupt; silent-zero usage defaults hide schema drift); cache-before-first-token promise verified. |
| 6 Cleanliness & typing | 3 cleanups (relative LOCAL_MEDIA_DIR default; aset_owner/aget_owner load-bearing asymmetry undocumented; wire-shape contract not in docstring; arelease_slot zero-clamp). |
| 7 Error handling / API | n/a (worker is not a router; routers/tasks.py owned by routers module) |
| 8 Config & paths | ok — no new config introduced; 1 carry-over relative-path cleanup. |

## Module verdict

NEEDS-WORK — no BLOCKERs; **Hotfix A verified correct** (SEV1 TTL-cap-bypass
closed at progress.py:232). Nine SEV2s remain: three on the Wave-1 Issue 86
surface (async-client rebuild without aclose in tests, aemit churn on any
Redis exception, blocking-XREAD pool sizing under SSE concurrency), two
structural-trust on emit boundary (raw-ValueError leak path, unschema'd emit
payloads), two on stream-error UX (no terminal emit on mid-stream interrupt,
silent-zero usage defaults hide SDK drift), and four carried-forward from
the prior wave (concurrent double-render, ingest re-do on redelivery,
poll_clip_outcomes quota break, abandoned aredis clients across pytest
loops). All eight SEV2 fixes are mechanical and bounded.
