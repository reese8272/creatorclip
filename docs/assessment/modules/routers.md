# routers — assessed 2026-05-31

Baseline commit: `67fddc9`. Slice: `routers/{auth,billing,clips,creators,improvement,review,tasks,upload_intel,videos}.py`.

## Findings

- [SEV1] routers/creators.py:167, routers/creators.py:195, routers/clips.py:156 —
  three `await progress.aset_owner(...)` call sites are still NOT wrapped in
  `try/except redis.RedisError`, so a transient Redis blip on `/me/catalog/sync`,
  `/me/dna/build`, or `/clips/{clip_id}/render` raises through the route and
  returns a 500 — even though the underlying Celery `.delay()` has already
  succeeded (rubric category 7 + 1). Wave-4 Fix 1 partially closed this for
  `routers/videos.py:upload_video`; Wave-3 Fixes B/D closed it for
  `routers/improvement.py` and `routers/auth.py`. The invariant the WAVE-4
  delta asks me to verify is therefore **NOT uniform across all four sites** —
  three are still bare. | fix: apply the exact same pattern as
  `routers/videos.py:269-284` to each call site: import `redis`, wrap
  `aset_owner` in `try/except redis.RedisError`, log a warning, and either
  return `stream_url=None` (for `sync_catalog` and `build_dna`, which already
  have a `stream_url` field in their response models) or skip the stream
  entirely (for `render_clip`, which currently returns a non-optional
  `stream_url: str` — make the field `str | None` first, then fail open). The
  Celery enqueue has already returned a task id; the response should still be
  202 and the user can still poll job status by the documented fallback route.

- [SEV2] routers/tasks.py:131-138 — owner-presence vs owner-mismatch returns
  distinct status codes (404 "Unknown task" when the owner key is absent vs 403
  "Not your task" when present-but-wrong). This is the carry-forward
  enumeration oracle: an attacker (with valid session, rate-limited at 120/min)
  can enumerate which `task_id` strings correspond to real in-flight jobs
  system wide (rubric category 3 — disclosure of cross-tenant resource
  existence). Note the `aget_owner` TTL also makes 404 leak "task is recent"
  vs "task is old / never existed". | fix: collapse both branches into a
  single `HTTPException(status_code=404, detail="Unknown task")`. The owner
  check becomes `if owner is None or owner != str(creator.id): raise 404`.
  The legitimate-but-wrong-creator path is functionally identical to the
  nonexistent path from the caller's perspective.

- [SEV2] routers/tasks.py:96 (call site of `progress.aread_since`) — the
  `Last-Event-ID` request header (line 140) is forwarded verbatim into
  `cursor`, which becomes the `last_id` argument to `client.xread({key: cursor})`.
  Redis Streams rejects malformed IDs (e.g. `"abc"`, `"1-2-3"`,
  empty-but-not-`"0-0"`) with `redis.exceptions.ResponseError`, which is NOT
  caught and propagates as a 500 on every reconnect from a broken / hostile
  client (rubric category 7). At scale this is also a cheap availability
  attack: a single malformed `EventSource` polyfill on a misbehaving browser
  hammers `/tasks/{task_id}/events` with 500s and chews through the
  per-creator SSE slot (which only releases via the `finally` in
  `_event_stream`, but the slot is acquired before the bad XREAD). | fix:
  validate `last_event_id` against `^\d+-\d+$` before entering the generator;
  on miss, coerce to `"0-0"` (the documented "from beginning" sentinel) and
  log a warning with `creator_id` + `task_id` + the offending value
  (truncated). Add a unit test that hits `/tasks/X/events` with
  `Last-Event-ID: bogus` and asserts 200 + start-from-zero, not 500.

- [SEV2] routers/videos.py:63-97 (`list_videos`) — unbounded `select(Video)
  .where(creator_id, source_uri IS NOT NULL).order_by(created_at.desc())`. A
  creator with 1k+ uploads (the PRD scale target is 10k) returns the entire
  catalog in a single hop, both to the DB cursor and to the JSON response.
  Heap, latency, and JSON-serialize all blow up linearly (rubric category 2).
  The `/videos` consumer is the dashboard — pagination is a real UX win, not
  speculative. | fix: add `limit` + `cursor` query params (default
  `limit=50`, max 200; cursor-paginate on `(created_at DESC, id DESC)` —
  not `OFFSET`, which is O(N) at depth). Ensure the existing index covers
  `(creator_id, source_uri, created_at DESC, id DESC)` — if not, file a
  migration as a follow-up. Same shape for `routers/clips.py:106-125`
  (`list_clips`) — even capped at `CLIPS_PER_VIDEO_DEFAULT=8` per video,
  the endpoint should still bound at the query level rather than relying
  on a separate config to enforce the cap.

- [SEV2] routers/auth.py:128-137 — the OAuth-callback `aset_owner` wrap is
  fail-open as required, but on Redis failure the route still issues the
  session cookie and 302-redirects to `/`. The new creator therefore lands
  on the onboarding tutorial with NO live progress stream available and the
  catalog-sync Celery task running blind. The user-visible failure mode is
  "tutorial shows zero videos forever" until the periodic refresh fires.
  Functionally not a regression (matches Wave-3 Fix D intent), but worth
  capturing the UX consequence so the onboarding screen (Issue 100) detects
  the missing stream and falls back to the polling endpoint. | fix: in the
  `except _redis_pkg.RedisError` branch, also set a short-lived cookie
  `cc_sse_unavailable=1; Max-Age=120` so the tutorial JS knows to poll
  `/videos` instead of attaching `EventSource`. Add an integration test that
  monkey-patches `progress.aset_owner` to raise and asserts the redirect
  still succeeds. (Non-blocking — flagging because the Wave-4 delta asked
  for a uniformity check.)

- [SEV2] routers/auth.py:229 — `except Exception as exc:` around the OAuth
  revocation block is over-broad and would also swallow
  `asyncio.CancelledError` if the client disconnects mid-revoke. The intent
  is "revocation is best-effort"; the implementation also catches programming
  errors silently (rubric category 6). | fix: narrow to
  `except (httpx.HTTPError, ValueError, sqlalchemy.exc.SQLAlchemyError) as exc`,
  let `asyncio.CancelledError` propagate.

- [SEV2] routers/clips.py:40-43 (`RenderQueuedOut.stream_url: str`) — the
  field is declared non-optional, so the SEV1 fix above CANNOT be applied to
  `render_clip` without first widening the schema. When the route hits the
  `RedisError` branch and tries to return `stream_url=None`, pydantic will
  500 on `response_model_validate` (rubric category 7). | fix: change line
  43 from `stream_url: str` to `stream_url: str | None` and document the
  None case in the field description ("None when the SSE channel is
  unavailable; poll `/clips/{clip_id}` instead"). Apply the same widen to
  `CatalogSyncQueuedOut.stream_url` and `BuildQueuedOut.stream_url` in
  `routers/creators.py:36, 42` so the SEV1 fix is mechanically possible.

- [SEV2] routers/billing.py:109 — `except Exception as exc:` catches
  everything (including `stripe.AuthenticationError`, which deserves a
  distinct 500 + alert vs the user-facing 502, and including
  `asyncio.CancelledError`). Currently masks misconfigured API keys as
  generic "Could not create checkout session" (rubric category 6 +
  observability). | fix: catch `stripe.error.StripeError` plus
  `asyncio.TimeoutError`; let `CancelledError` propagate; log the exception
  type so misconfig vs Stripe-outage is distinguishable in metrics.

- [cleanup] routers/upload_intel.py:37-40 — `select(AudienceActivity)
  .where(creator_id == creator.id)` returns the per-creator day×hour grid
  (bounded by composite PK at 7×24=168 rows). Not an unbounded fetch, but
  no defensive `.limit()` ceiling guards against a future migration
  broadening the PK. | fix: add `.limit(200)` as defense-in-depth.

- [cleanup] routers/auth.py:117, routers/auth.py:131, routers/auth.py:199,
  routers/clips.py:86, routers/clips.py:91, routers/creators.py:160,
  routers/creators.py:192, routers/creators.py:216, routers/creators.py:246,
  routers/creators.py:291, routers/improvement.py:93, routers/improvement.py:96,
  routers/review.py:65, routers/review.py:77, routers/videos.py:132,
  routers/videos.py:149, routers/videos.py:269, routers/videos.py:271,
  routers/videos.py:288 — heavy use of *function-local* `import` statements
  (a dozen+ in this slice). These re-execute the import-cache lookup on
  every request; for `from worker import progress` and `from observability
  import log_event` that's measurable hot-path overhead under load. Some are
  load-order-driven (circular imports between `worker.tasks` and `routers`),
  but most are not (rubric category 6 / cleanliness). | fix: hoist
  side-effect-free imports (`observability.log_event`, `worker.progress`,
  `redis`, `logging`) to module top. Keep only the genuinely cyclic ones
  (`worker.tasks.*`, `billing.ledger.grant_minutes` inside the OAuth
  callback) local.

- [cleanup] routers/auth.py:131, routers/videos.py:132, routers/videos.py:277
  — `import logging as _logging; _logging.getLogger(__name__).warning(...)`
  is a workaround for not importing `logging` at module top. Both modules
  already have `logger = logging.getLogger(__name__)`; the local `_logging`
  alias is dead-weight. | fix: drop the local re-import and use the existing
  module-level `logger`.

- [cleanup] routers/clips.py:142, routers/clips.py:173, routers/review.py:49,
  routers/clips.py:73, routers/clips.py:115, routers/videos.py:315 —
  `session.get(Clip, clip_id)` (or `Video, video_id`) then post-fetch
  `creator_id` check is fetch-then-validate, which pulls one extra row over
  the wire when the entity belongs to a different creator. Not a leak (the
  check is present and correct, so rubric category 3 is ok), but a single
  `select(Clip).where(Clip.id == clip_id, Clip.creator_id == creator.id)`
  is one query at the same cost and avoids loading the foreign row at all
  (rubric category 2). | fix: replace each `session.get(...)` with a scoped
  `session.scalar(select(...).where(...))`.

- [cleanup] routers/videos.py:184-204 — temp-file lifecycle uses
  `tempfile.NamedTemporaryFile(... delete=False)` + manual `unlink` only in
  the `except HTTPException` branch. If a non-HTTP exception fires inside
  the read loop (e.g. `OSError` on disk full, `CancelledError` on client
  disconnect mid-upload), the temp file leaks (rubric category 1, disk leak
  on error path). | fix: wrap the whole block in
  `try / except BaseException: tmp_path.unlink(missing_ok=True); raise` —
  or copy the `try/finally` shape used at line 238-244 for the R2 PUT.

- [cleanup] routers/upload_intel.py — module is missing
  `logger = logging.getLogger(__name__)`. No defects today (no error paths),
  but the inconsistency with the rest of the slice is jarring. | fix: add
  the logger declaration for grep-uniformity with the rest of the slice.

- [cleanup] routers/creators.py:33-43, routers/clips.py:40-43,
  routers/improvement.py:18-24 — `BuildQueuedOut`, `CatalogSyncQueuedOut`,
  `RenderQueuedOut`, `BriefQueuedOut` are four near-identical Pydantic
  models (`task_id`, `status`, `stream_url`). DRY violation — four copies,
  one shape (rubric category 6). | fix: extract a `TaskQueuedOut` base in a
  shared `routers/_schemas.py` and let each router subclass or alias it.
  Pair this with the SEV2 widening of `stream_url` to `str | None` so the
  fail-open invariant is enforced at the base type.

- [cleanup] routers/videos.py:269, routers/improvement.py:93,
  routers/auth.py:117, routers/clips.py:148 — `import redis as _redis_pkg`
  inside the function body to avoid a top-level import is needlessly noisy
  and re-runs the package-init cost on every request. `redis` is already a
  hard dependency (via `worker.progress`) and importing it at module top is
  free. | fix: hoist to top: `from redis import RedisError`.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | 1 cleanup (temp-file leak on non-HTTPException error path in `upload_video`). DB sessions OK (FastAPI dep injection + commit/rollback in `get_session`). |
| 2 Concurrency & scale | 2 SEV2 (unbounded `/videos` + the same pattern in `/clips`); Stripe → `asyncio.to_thread` ✅; R2 PUT → `asyncio.to_thread` ✅; account-deletion delete-prefix → `asyncio.to_thread` ✅; ffprobe → `asyncio.to_thread` ✅. No remaining sync-in-async in this slice. |
| 3 Security & compliance | 1 SEV2 (enumeration oracle on `/tasks/{id}/events`). Per-creator isolation verified on every query: `routers/videos.py:82`, `routers/clips.py:121`, `routers/upload_intel.py:38`, `routers/improvement.py:62-64`, `routers/improvement.py:74`, `routers/improvement.py:136`, `routers/creators.py` (all `creator.id`-scoped helpers), `routers/auth.py:203` (token row), `routers/billing.py:163` (`update(Creator).where(Creator.id == creator_id)`). Token handling via `decrypt()` at `routers/auth.py:206` ✅. YouTube ID validated against `^[A-Za-z0-9_-]{11}$` before storage-key interpolation ✅. No PII in log lines reviewed. |
| 4 Clip-quality | n/a (routers do not score). Verified: `routers/clips.py` delegates ranking to `clip_engine.ranking.generate_and_rank_clips` and returns `principle` + `reasoning` from `signals_jsonb` directly — no virality language anywhere in this slice. |
| 5 Anthropic SDK | n/a (routers do not call the LLM directly — `start_improvement_brief` and `build_dna` enqueue Celery tasks). |
| 6 Cleanliness & typing | 5 cleanup findings: function-local import sprawl, fetch-then-validate query shape, duplicated `*QueuedOut` schemas, in-function `import redis`, dead `import logging as _logging` workarounds. Type hints present on every signature in the slice. |
| 7 Error handling / API | 1 SEV1 (three unwrapped `aset_owner` calls → 500 on Redis blip), 1 SEV2 (`Last-Event-ID` → 500 on malformed), 1 SEV2 (`RenderQueuedOut.stream_url` schema cannot represent the fail-open None case), 2 SEV2 (over-broad `except Exception` in auth + billing). Pydantic models on every request & response ✅. Status codes 202/204/400/401/404/409/413/422/500/502/503 used correctly elsewhere. |
| 8 Config & paths | All paths absolute (`tempfile.NamedTemporaryFile` → `Path(tmp.name)` ✅, R2 keys are creator-scoped + youtube-id-validated ✅). Settings referenced (`UPLOAD_MAX_MB`, `CLIPS_PER_VIDEO_DEFAULT`, `STRIPE_SECRET_KEY`, `FREE_TRIAL_MINUTES`, `JWT_EXPIRY_MINUTES`) all present in `.env.example` ✅. |

## WAVE-4 delta verification (Fix 1)

The Wave-4 delta says `routers/videos.py:262-279` (`upload_video`) now wraps
`aset_owner` in `try/except redis.RedisError` and asks me to verify the
fail-open invariant is uniform across all `aset_owner` call sites.

Confirmed state per `grep -n "aset_owner" routers/`:
- `routers/improvement.py:109` — WRAPPED ✅ (Wave-3 Fix B)
- `routers/auth.py:129` — WRAPPED ✅ (Wave-3 Fix D, OAuth-redirect ignores stream_url)
- `routers/videos.py:275` — WRAPPED ✅ (Wave-4 Fix 1, returns `stream_url=None`)
- `routers/creators.py:167` (`sync_catalog`) — **NOT WRAPPED** ❌ (SEV1 above)
- `routers/creators.py:195` (`build_dna`) — **NOT WRAPPED** ❌ (SEV1 above)
- `routers/clips.py:156` (`render_clip`) — **NOT WRAPPED** ❌ (SEV1 above)

The invariant is **not uniform**. Three call sites still 500 on Redis blip
despite the Celery enqueue having already succeeded. See top finding for the
concrete fix.

## Carry-forward SEV2 status (re-checked)

- `routers/tasks.py:131-138` — 404/403 enumeration oracle: **still open**
- `routers/tasks.py:140` — unvalidated `Last-Event-ID` → 500 on malformed: **still open**
- `routers/videos.py:63-97` + `routers/clips.py:106-125` — unbounded list
  endpoints: **still open**
- `routers/upload_intel.py:37-40` — bounded by 168-row composite PK,
  demoted to cleanup

## Module verdict

**NEEDS-WORK** — the Wave-4 fix landed for `upload_video` but the same
pattern was never extended to `sync_catalog`, `build_dna`, or `render_clip`,
so three SEV1-equivalent 500-on-Redis-blip paths remain; the three
carry-forward SEV2s (`Last-Event-ID`, enumeration oracle, unbounded list
endpoints) are unchanged from the prior wave.
