# worker — assessed 2026-05-31 (wave-4 re-assessment)

Slice: `worker/__init__.py`, `worker/celery_app.py`, `worker/schedule.py`,
`worker/storage.py`, `worker/tasks.py`, `worker/progress.py`,
`worker/anthropic_stream.py`. Wave-4 re-assessment against baseline
`67fddc9`. Wave-4 introduces Fix 3 (Issue 75b YouTube ToS compliance):
a per-day analytics-staleness purge. The eight carried-forward SEV2s
from Wave-3 remain present and unchanged; four cleanups remain open.
No Wave-4 finding rises above cleanup — the new code is structurally
sound and the only delta is the additional emit-/scheduling surface
that the existing SEV2s now extend over.

## Findings

### Wave-4 delta — verified

- ✅ **Fix 3 / Issue 75b — `worker/tasks.py:1064–1151`
  `_purge_stale_youtube_analytics_async`.** The new analytics-staleness
  purge is structurally correct end-to-end:
  - **Cutoff math** (line 1097): `datetime.now(UTC) - timedelta(days=
    settings.YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS)`. `config.py:95`
    defines the default at 30 — matches the YouTube API Services
    Developer Policies §III.E.4.b 30-day re-verification window.
    Configurable via env so the limit can tighten without a deploy.
  - **VideoMetrics filtered by `fetched_at < cutoff`** (line 1105) —
    `models.py:204` confirms `VideoMetrics.fetched_at` is a non-null
    `DateTime(timezone=True)`. Stale `video_id`s are collected via a
    single SELECT (`scalars().all()`).
  - **RetentionCurve cascade**: `models.py:209–225` confirms
    `RetentionCurve` has no `fetched_at` column of its own (it has only
    `timestamp_s` + `audience_watch_ratio`). The implementation deletes
    `RetentionCurve` rows whose `video_id` is in the stale VideoMetrics
    set (line 1118–1121) — correct, because `youtube/analytics.py::
    sync_video_analytics` writes both in lock-step, so freshness is a
    parent property. The order (curves first, then metrics) matters
    only for FK integrity if there were a curves→metrics FK; there
    isn't (both FK to `videos.id`), so order is cosmetic — still good.
    The Video row itself is NOT deleted (correctly: the Video record
    is the creator's data, not YouTube API data).
  - **AudienceActivity + Demographics by own `fetched_at`**
    (lines 1130–1137). `models.py:237` (`AudienceActivity.fetched_at`)
    and `models.py:247` (`Demographics.fetched_at`) confirmed — each
    has its own column written by `youtube/analytics.py::
    sync_audience_data`. Direct `delete().where(...fetched_at <
    cutoff)` is the right shape; uses parameterized SQLAlchemy core
    delete (no f-string SQL).
  - **Single short transaction** (line 1099 → 1139): all four deletes
    fire under one `async with db.AdminSessionLocal()` block and one
    `await session.commit()`. DB connection is acquired once and
    released at the end of the context — no per-table connection
    churn, no long-held connection during external IO.
  - **Idempotent**: re-running with nothing stale yields the same
    SELECT (empty `stale_video_ids` → no deletes), the same two
    `DELETE WHERE ... fetched_at < cutoff` against empty result sets
    (0 rowcount), and an unconditional `commit()` which is a no-op
    when no rows were modified. The conditional `logger.info` at line
    1141 (`if total: ...`) means no log line on the no-op path — small
    nicety. Safe to call concurrently: each invocation operates on the
    rows whose `fetched_at < cutoff` AT THAT INSTANT, and `DELETE` is
    monotonic.
  - **Logging is PII/token-clean**: only `cutoff.isoformat()` (a
    timezone-aware UTC timestamp) plus four integer rowcounts. No
    creator ids, no video ids, no exception text. Note the cutoff is
    log-only — no rate or counter exposes it via the wire emit path,
    which is fine because this Beat task does NOT publish progress
    events (no SSE subscriber for a Beat sweep).
  - **Beat registration** (`worker/schedule.py:38–41`): the entry
    `"purge-stale-youtube-analytics-daily"` is correctly added to the
    `celery.conf.beat_schedule` dict at 24h cadence. Task name
    `"worker.tasks.purge_stale_youtube_analytics"` matches the
    Celery task `name=` at `worker/tasks.py:170` exactly — Beat
    discovery will resolve. Module docstring at lines 13–18 cites the
    intended 6h offset from `refresh-youtube-analytics-daily` so the
    purge sees the freshest possible `fetched_at` values; the
    `timedelta(hours=24)` schedule alone does NOT enforce the offset
    (Beat staggers schedules from the first-run anchor, which depends
    on Beat process start time), but the practical impact is null —
    on day 1 the refresh runs first because it's registered above the
    purge entry; thereafter both fire on their own anchors. **Minor
    documentation/behavior drift, not a defect** — flagged below as
    a cleanup.
  - **Celery task wrapper** (`worker/tasks.py:170–182`): synchronous
    `purge_stale_youtube_analytics` calls `run_async(_purge_…())`,
    matching the pattern used for `purge_stale_source_media` (line
    161–167) and the rest of the Beat-driven async-impl tasks. No
    retries are configured (no `bind`, no `max_retries`) — correct for
    a purge sweep: if the sweep fails, the next 24h tick retries.
  - **No PII / token leak in any log line**: `logger.info` at lines
    1143–1151 takes only ints + the cutoff timestamp. The async impl
    has no `logger.warning` / `logger.error` calls at all — any
    exception in the SELECT / DELETE / commit propagates up to the
    Celery task wrapper (line 170–182) where it's swallowed by the
    Celery worker process default. **Minor visibility gap** — flagged
    as a cleanup below.

### Carried forward — still present in wave-4

- [SEV2] `worker/progress.py:134–146` — `_async_client()` rebuilds the
  singleton on loop-mismatch but still does not `aclose` the old client
  first. Same as wave-3; Wave-4 did not touch this code path.
  Production impact: low (one loop per worker process). Test impact:
  abandoned client warnings + potential socket retention until process
  exit. `(needs-runtime-confirmation via lsof)`. | fix: in the rebuild
  branch, best-effort close the old reference before rebinding (or
  expose `await arebind()` for the test fixture to call explicitly).

- [SEV2] `worker/progress.py:149–164` — `aemit` exception handler still
  nulls out `_AIO` / `_AIO_LOOP` globals on **any** exception,
  including transient XADD errors that have nothing to do with the
  connection. Wave-4 adds no new aemit sites (the new purge task is
  log-only), so the singleton-churn surface is unchanged from Wave-3.
  | fix: only reset on `redis.exceptions.ConnectionError` /
  `redis.exceptions.TimeoutError`; leave the singleton intact for
  transient XADD failures.

- [SEV2] `worker/progress.py:190–208` — `aread_since` uses `XREAD` with
  `block=5000` and the default redis-py async pool of 50 connections
  per client. Wave-4 does not add new readers, but the cumulative
  load from prior waves is unchanged — the upload-chain stream stays
  open through generate_clips, so subscribers hold their XREAD slots
  longer than they did pre-Wave-3. | fix: bound the async client's
  pool explicitly at construction (`max_connections=200`) and split
  reader/writer clients so a wedged reader pool doesn't starve emits.
  `(needs-runtime-confirmation via load test)`.

- [SEV2] `worker/progress.py:74–85` — `_serialize` does
  `json.dumps(fields, default=str)` with no schema or allowlist.
  Wave-4 adds no new emit kwargs (the new purge has no SSE consumer),
  but the underlying trust gap is unchanged. | fix: per-event-type
  `EventPayload(BaseModel)` with `extra="forbid"` and a size cap;
  cheapest interim is a token-shape regex (`^sk-ant-`, `^ya29\.`, long
  base64) on every `default=str` fallback that logs+drops rather than
  emits.

- [SEV2] `worker/tasks.py:805` — `_emit("error", message=str(exc))` for
  `ValueError` in `_build_dna_async` raw-passes the exception message
  to the SSE stream. Today's raisers are hand-written data-gate
  strings, but a downstream library `ValueError` whose message carries
  a token or DB error would leak. (Line number shifted: previously
  reported `781–786` in wave-3, now line 805 after the wave-4 task
  additions.) | fix: allowlist of known-safe data-gate messages OR a
  small classifier (`"creator/video/dna not found"`, `"X is empty"`);
  fall back to a generic `"validation failed"`.

- [SEV2] `worker/anthropic_stream.py:78–86` — Anthropic stream context
  manager runs inside `asyncio.to_thread`. If the network drops
  mid-stream, the SDK raises inside the `for event in stream:` loop;
  no terminal `error` emit fires at this layer. The caller in
  `dna/brief.py` / `improvement/brief.py` catches and emits the
  generic error, but tokens already delivered to the UI are followed
  by an undefined gap before the caller's terminal emit fires. Wave-4
  unchanged. | fix: wrap the streaming call site (`stream_and_emit`
  itself or each caller) in
  `try: ... except Exception: sync_emit(task_id, "error",
  message="stream interrupted"); raise`. Lower latency than the
  multi-layer async unwind.

- [SEV2] `worker/anthropic_stream.py:93–99` — `usage_dict` casts each
  field via `getattr(usage, ..., 0)`. `0`-as-default silently hides a
  schema change in a future SDK bump where a field is renamed or
  moved into a sub-object. `cache_read=0` reported to logs would
  under-report savings without raising. Same as wave-3. | fix:
  distinguish "absent" from "zero" — return `None` when the attribute
  is missing; log a warning the first time a previously-present field
  returns None.

- [SEV2] `worker/tasks.py:542–636` — `_render_clip_async` idempotency
  guard at line 562 (`render_status == done and render_uri`) protects
  *sequential* redelivery-after-success but **not concurrent**
  delivery. With `acks_late` + `reject_on_worker_lost`, two workers
  can both read `pending`, both flip to `running`, both encode +
  upload to `clips/{clip_id}.mp4` (storage overwrites; bytes are
  deterministically identical, so no corruption — just wasted ffmpeg
  + R2 round-trips). Wave-4 unchanged. | fix: use
  `select(Clip).where(...).with_for_update()` on the Clip row in the
  opening session and re-check `render_status` under the lock before
  flipping to `running`.

- [SEV2] `worker/tasks.py:318–390` — `_ingest_async` is not a clean
  no-op on redelivery after a successful commit. First run overwrites
  `video.source_uri` with the derived audio URI
  (`audio/{video_id}.wav`). A redelivery re-runs `probe_duration_s` +
  `extract_audio_wav` over the already-extracted WAV and re-uploads
  the same key. No corruption (`deduct_for_video` no-ops via
  `UNIQUE(video_id)`; duration only set when unset), but wastes
  ffmpeg + R2 round-trips. Wave-4 unchanged. | fix: short-circuit when
  `source_uri` already points at the derived audio key
  (`Path(source_uri).suffix == ".wav"`), or gate on
  `ingest_status == done` before opening `alocal_path`.

- [SEV2] `worker/tasks.py:816–909` — `_poll_clip_outcomes_async` does
  not break on YouTube quota exhaustion. Per-outcome `get_video_stats`
  failures are swallowed by the broad `except Exception: continue` at
  line 883–890, so a quota-out creator walks the whole candidate set
  firing doomed YouTube calls. Bounded by the 10-day `cutoff_created`
  cap (line 837), but wasteful and noisy. Wave-4 unchanged. | fix:
  catch `QuotaExhaustedError` explicitly inside the inner loop and
  `break` (mirror the analytics-refresh pattern at line 1349–1355),
  committing partial progress first.

### Cleanups

- [cleanup] `worker/schedule.py:13–18` vs `worker/schedule.py:34–41` —
  the module docstring asserts the analytics purge runs "6 hours
  offset from refresh_youtube_analytics so the purge sees the
  FRESHEST possible fetched_at values". The Celery Beat config alone
  (`timedelta(hours=24)` on both entries) does NOT enforce a 6-hour
  offset — Beat anchors each entry from its first-fire wall-clock
  time, so the actual offset depends on registration order at Beat
  startup. The functional consequence is null (the purge can over- or
  under-shoot the refresh by 1 tick in either direction, but
  fetched_at advances monotonically so the cutoff math is unaffected),
  but the docstring overstates the guarantee. | fix: either (a)
  replace `timedelta(hours=24)` with an explicit `crontab(hour=N,
  minute=0)` schedule for both entries to make the 6-hour gap
  load-bearing, or (b) soften the docstring to "runs daily; ordering
  vs refresh is best-effort, the purge cutoff math is independent
  of run order".

- [cleanup] `worker/tasks.py:170–182` `purge_stale_youtube_analytics`
  task wrapper — has no try/except around `run_async(...)`. Any
  exception from the async impl (transient DB blip, deadlock with
  concurrent FK cascade, etc.) propagates to Celery's default error
  handler, which logs but provides no human-readable hint that the
  YouTube-ToS purge sweep failed. Compare to `purge_stale_source_media`
  (line 161–167), which has the same shape — so this is a pre-existing
  pattern, not a new gap, but the Wave-4 task brings it into focus
  since this sweep is the OAuth-verification-required one. | fix:
  add a single `try: run_async(...); except Exception as exc:
  logger.exception("purge_stale_youtube_analytics failed: %s", exc);
  raise`. Mirror in `purge_stale_source_media`. The `raise` preserves
  Celery's existing error-tracking behavior.

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
  decoder rely on it implicitly. | fix: document the wire shape in
  the module docstring and link `routers/tasks.py` as canonical
  decoder.

- [cleanup] `worker/progress.py:170–184` — `aset_owner` / `aget_owner`
  have no try/except wrapper, unlike `aemit`. This is *correct* (auth
  must be load-bearing) but the asymmetry is undocumented; a future
  contributor "fixing consistency" by wrapping these in try/except
  would silently break SSE auth. | fix: add a one-line comment on
  each: "MUST raise — the ownership key is the SSE authorization
  invariant. Caller decides fail-open posture."

- [cleanup] `worker/progress.py:239–244` — `arelease_slot` calls
  `DECR` with no clamp at zero; the counter goes negative on
  disconnect-after-TTL. Acknowledged in the docstring. Worth a
  one-Lua-roundtrip clamp:
  ```lua
  local v = redis.call('DECR', KEYS[1]); if v < 0 then redis.call('SET', KEYS[1], 0) end
  ```

### Cross-cutting (verified, wave-4)

- ✅ **No blocking calls in any new path.** `_purge_stale_youtube_
  analytics_async` runs entirely on the worker's singleton event
  loop. Imports (`from datetime import timedelta`, `from sqlalchemy
  import delete, select`, `from config import settings`, the late
  `from models import AudienceActivity, Demographics`) are all cheap
  module-level lookups. All DB calls are `await session.execute(...)`
  on the async session. No `subprocess`, no `requests`, no
  `time.sleep`.
- ✅ **No PII / token leakage in the Wave-4 path.** The only logged
  data is the cutoff timestamp + four integer rowcounts. No creator
  ids, no video ids, no exception text. No emit calls (Beat sweeps
  have no SSE consumer).
- ✅ **Per-creator isolation considered.** The purge operates across
  ALL creators by design — it's a system-wide retention sweep, not a
  creator-scoped query. This is correct for the ToS-compliance use
  case (the policy applies to every authorized user uniformly).
  Cross-tenant leak risk is zero because the operation deletes,
  not reads or returns.
- ✅ **Parameterized SQL.** Every WHERE clause uses SQLAlchemy core
  comparison operators (`fetched_at < cutoff`, `video_id.in_(stale_
  video_ids)`). No f-strings or % formatting in any query.
- ✅ **YouTube ToS / retention claim verified.** The cutoff defaults
  to 30 days (`YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS=30` at
  `config.py:95`); §III.E.4.b of the YouTube API Services Developer
  Policies requires re-verification or deletion of stored YouTube
  data within 30 days. The implementation deletes stale rows
  unconditionally — correct fallback when re-verification fails (the
  daily refresh task already handles the success path by advancing
  `fetched_at`).
- ✅ **Honesty / no-virality strings.** The Wave-4 path contains
  only operational log messages — no virality promises, no
  guarantees, no creator-facing copy.
- ✅ **Idempotency invariants preserved across the module.**
  `_render_clip_async`, `_generate_clips_async`, `_build_dna_async`,
  `_sync_channel_catalog_async`, `_generate_improvement_brief_async`
  guards from Wave-3 remain in place. The new purge sweep is
  trivially idempotent (`DELETE WHERE fetched_at < cutoff` is a
  monotone operation: once a row is deleted, the same query is a
  no-op on the next tick).

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | 4 findings carried from wave-3 (concurrent-render double-encode, ingest re-do, async-client rebuild-without-aclose, aemit churn on transient failures). Wave-4 purge sweep is a clean addition — single transaction, no resource leaks, idempotent. |
| 2 Concurrency & scale | 2 findings carried from wave-3 (`poll_clip_outcomes` no quota break; blocking XREAD pool sizing). Wave-4 adds no new pressure (purge is internal-only, no SSE consumer). |
| 3 Security & compliance | 2 findings carried (raw ValueError leak into SSE event; unschema'd emit payload). Wave-4 ADVANCES compliance posture by introducing the 30-day analytics purge that §III.E.4.b requires; per-creator scoping verified end-to-end on existing queries. |
| 4 Clip-quality | n/a (orchestration module; ranking is in clip_engine). |
| 5 Anthropic SDK | 2 findings carried (no terminal emit on stream interrupt; silent-zero usage defaults). Wave-4 unchanged. |
| 6 Cleanliness & typing | 6 cleanups — Wave-4 adds two (schedule-docstring vs Beat-config drift on the "6h offset" claim; no try/except wrapper on the new task body for observability). Pre-existing four carry forward (relative LOCAL_MEDIA_DIR; wire-shape contract; aset_owner/aget_owner asymmetry; arelease_slot zero-clamp). |
| 7 Error handling / API | ok — Wave-4 task surfaces errors via Celery's default handler. Cleanup proposed to make sweep failures self-describing in the logs. |
| 8 Config & paths | ok — `YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS` is in `config.py` (default 30), no new path defaults introduced. Carry-over relative-path cleanup remains. |

## Module verdict

NEEDS-WORK — no BLOCKERs. Wave-4 Fix 3 lands cleanly:
`_purge_stale_youtube_analytics_async` correctly cascades RetentionCurve
via parent VideoMetrics, uses each table's own `fetched_at` where
present, executes in one short transaction, is trivially idempotent,
and logs nothing that could leak PII or tokens. The Beat registration
is correct and the task name matches the registered task. Two minor
cleanups arise from the Wave-4 surface — a documentation/behavior drift
on the claimed "6-hour offset" (Beat anchors don't enforce it) and a
missing try/except wrapper that would help operators diagnose sweep
failures without grepping Celery internals. All eight carried-forward
SEV2s from Wave-3 remain unchanged; the highest-leverage remaining
work is still the `worker/progress.py` pool sizing + aemit-churn pair
(SEV2 ×2) under the 200-creator scale target, plus the Anthropic
stream-interrupt gap (affecting both DNA + improvement-brief flows).
The render double-encode race and ingest re-do are bounded (wasted
compute, not corruption).
