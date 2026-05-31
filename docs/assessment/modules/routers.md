# routers — assessed 2026-05-31 (Wave-3)

Slice: routers/auth.py, billing.py, clips.py, creators.py, improvement.py,
review.py, tasks.py, upload_intel.py, videos.py.

**Wave-3 focus (verify three critical fixes from the post-Wave-2 sweep):**
- **Fix B (improvement.py)** — was Wave-2 SEV1 ("orphan-pending on Redis blip").
  Expected delta: `aset_owner` reordered AFTER the `row.job_id = task.id`
  commit, wrapped in `try/except redis.RedisError`, fail-open returns
  `stream_url=None` (no 500).
- **Fix C (billing.py)** — was Wave-2 SEV1 (sync Stripe SDK on the loop
  thread). Expected delta: `await asyncio.to_thread(create_checkout_session,
  ...)` with positional args; HTTPException 502 still on any exception;
  new `import asyncio` at the top.
- **Fix D (auth.py)** — was Wave-2 SEV2 (catalog-sync task discarded after
  `.delay()`, no SSE ownership stamped). Expected delta: `task` bound from
  `.delay()`, `aset_owner` called, wrapped in `try/except redis.RedisError`,
  fail-open log + continue.

**Carry-forward SEV2s** (re-checked, all still open):
- `tasks.py:131-138` 404 vs 403 enumeration oracle.
- `tasks.py:140` unvalidated `Last-Event-ID` → 500 on malformed reconnect.
- `videos.py`/`clips.py`/`upload_intel.py` unbounded list endpoints.

## Findings

### Wave-3 deltas verified

- [SEV1 → CLOSED] **improvement.py:91-116 (Fix B)** — verified applied correctly.
  Ordering is now: line 91 `await session.commit()` (commits `pending` row
  with `job_id=None`) → line 98 `task = ...delay(...)` → line 99
  `row.job_id = task.id` → line 100 `await session.commit()` (commits
  job_id) → line 107 `stream_url = f"/tasks/{task.id}/events"` → lines
  108-116 `try/except redis.RedisError` around `await
  progress.aset_owner(task.id, str(creator.id))`. On Redis failure: warning
  log via `logger.warning(...)` + `stream_url = None`. The endpoint still
  returns 202 with `task_id=task.id`. The previously-flagged orphan-pending
  scenario is closed: even on Redis blip, `row.job_id` IS set, so the
  debounce-collapse path at :77-80 derives a `stream_url` from `row.job_id`
  on the next call (the SSE endpoint will 404 it since no owner key exists,
  but the brief itself completes via the worker and the user recovers via
  the GET poll). **Posture-correctness note:** this matches `progress.aemit`
  precisely (aemit logs + swallows; aset_owner now also logs + degrades
  gracefully). Worker assessment's earlier claim that aset_owner is
  "load-bearing — must raise" was overruled by Wave-3's product-level call
  (graceful degradation > 500-on-Redis-blip), which is the right call given
  the GET-poll fallback. Clean.

- [SEV1 → CLOSED] **billing.py:94-111 (Fix C)** — verified applied correctly.
  `import asyncio` present at the top (line 1). The Stripe call is now:
  ```
  url = await asyncio.to_thread(
      create_checkout_session,
      body.pack_id,
      str(creator.id),
      creator.stripe_customer_id,
      body.success_url,
      body.cancel_url,
  )
  ```
  Positional args verified against `billing/stripe_client.py:23-29` —
  `create_checkout_session(pack_id, creator_id, stripe_customer_id,
  success_url, cancel_url) -> str` matches exactly. The `try/except
  Exception` wrapper still preserves the 502 ("Could not create checkout
  session") path on any failure (including `asyncio.to_thread` re-raising
  Stripe SDK errors). The 503 / 400 pre-checks for missing key /
  invalid pack are still upstream of the threadpool offload. Clean.

- [SEV2 → CLOSED] **auth.py:116-137 (Fix D)** — verified applied correctly.
  Structural change confirmed: `task = sync_channel_catalog.delay(str(creator.id))`
  at :122 (previously this was a bare `.delay()` whose return was
  discarded — that was the load-bearing pre-fix gap). Now `task.id` flows
  into `await progress.aset_owner(task.id, str(creator.id))` at :129, all
  wrapped in `try/except _redis_pkg.RedisError as exc` (:130) with a
  warning-log fallback (:131-137). Posture matches Fix B. The catalog sync
  itself runs regardless of Redis state. Caveat: there's no `stream_url`
  returned to the OAuth callback (this is a RedirectResponse to `/`, not a
  JSON body), so the frontend onboarding tutorial (Issue 100) will need to
  reconstruct the SSE URL on its own from a follow-up `/me/catalog/sync`
  or by polling the data-gate. Outside this module's responsibility —
  acceptable. Clean.

### Industry-standard check for Fix C (asyncio.to_thread for sync SDKs)

`asyncio.to_thread(sync_callable, *args)` is the canonical 2026 idiom (Python
3.9+, recommended by both CPython docs and the FastAPI/Starlette async-best-
practices guide) when an SDK is sync-only and you don't want to block the
event loop. Confirmed pattern matches the Anthropic-SDK / Voyage-SDK
treatment from Issue 78d and the Issue 84 research outcome. Stripe's
`StripeClient` (urllib3 under the hood) is in the same sync-only category
as Anthropic's pre-async SDK, so the same recipe applies. No alternative
considered: `httpx.AsyncClient` + manual Stripe REST construction would
duplicate signature/retry logic the SDK already encapsulates, and Stripe's
official async SDK is still labeled experimental as of 2026-05. The
`asyncio.to_thread` route is the documented best practice.

### Wave-2 deltas re-verified (Issue 92 owner-stamp + stream_url)

- creators.py:152-177 (sync_catalog) — unchanged. Stamps `task.id` (Celery
  UUID) under the authenticated principal. `CatalogSyncQueuedOut.stream_url:
  str` matches handler. Clean. **Note:** unlike Fix B / D, this endpoint's
  `aset_owner` is NOT wrapped in `try/except redis.RedisError` — a Redis
  blip here 500s the request. Lower blast radius than improvement.py's
  former orphan-pending (no DB row written), but a Wave-3 consistency fix
  would either (a) wrap this too, or (b) document that the
  fail-open-on-Redis posture is only applied to endpoints that wrote DB
  state before the stamp. SEV-cleanup, flagged below.

- creators.py:180-205 (build_dna) — same shape, same lack of try/except.
  Same SEV-cleanup note.

- improvement.py:96-116 — see Fix B above.

- clips.py:131-161 (render_clip) — `aset_owner(str(clip_id), str(creator.id))`
  at :156 runs AFTER the creator-isolation check at :143 (`clip.creator_id
  != creator.id → 404`), so stamp can never be wrong-creator. NOT wrapped
  in try/except. On Redis blip: the Celery task is enqueued (line 151
  before stamp), the stamp 500s, and the client retries — but the next
  call hits the same "render already in progress" check at :145 only if
  the worker has updated `RenderStatus.running` yet. Bounded race; the
  worker's idempotency guards handle a double-enqueue. SEV-cleanup; flagged
  with the other unwrapped `aset_owner` calls.

- videos.py:262-266 (upload_video) — **NOT touched by Wave-3.** Still has
  the previously-flagged SEV2 ordering: Video row committed (:255) →
  `aset_owner` (:265) → `start_pipeline` (:266). On Redis blip, the Video
  row is committed `pending` with `source_uri` set, the pipeline never
  starts, and the row sits at `ingest_status=pending` until manual
  cleanup. The dashboard's Issue-90 filter (`source_uri IS NOT NULL`)
  surfaces this row. The Fix-B treatment (wrap stamp + return
  `stream_url=None` on Redis blip) would NOT close this — the bug here
  isn't the 500 response, it's that `start_pipeline(...)` runs UNCONDITIONALLY
  inside the same handler, so if the stamp raises, start_pipeline never
  reaches line 266 and the row is orphaned. Carried forward as SEV2 below.

### Security & compliance (category 3)

- [SEV2 — CARRIED, STILL OPEN] **videos.py:262-266** — orphan-pending Video
  row + orphan R2 source blob on `aset_owner` Redis blip. Same as Wave-2
  finding; Wave-3 did not address this. | fix: either (a) move the
  `aset_owner` call BEFORE the Video INSERT/commit so a Redis failure
  surfaces as 503 before any DB state or R2 blob lands; or (b) wrap the
  stamp in `try/except redis.RedisError` matching Fix B, AND call
  `start_pipeline(...)` regardless (the SSE feature degrades gracefully,
  the pipeline still runs, status moves to `done` or `failed` via the
  worker). Option (b) is the more aligned-with-Fix-B fix. Add a regression
  test that monkey-patches `progress.aset_owner` to raise and asserts the
  Video row reaches a terminal state.

- [SEV2 — CARRIED, STILL OPEN] **routers/tasks.py:131-138** — ownership
  check is an enumeration oracle. `owner is None → 404 "Unknown task"` vs
  `owner != creator.id → 403 "Not your task"` lets an authenticated
  creator distinguish "task_id never existed" from "exists but belongs
  to another creator". The in-code comment at :133-135 explicitly says
  "don't leak whether the task ever existed" but the implementation
  contradicts itself. With Issue 92's deterministic clip_id/video.id keys,
  this also leaks "this entity exists in the system" — UUID4 entropy blunts
  it but the principle violation stands. | fix: return 404 `"Unknown task"`
  for BOTH branches; log differentiating reason server-side only via
  `logger.info("sse_auth_denied task_id=%s reason=%s", task_id, "wrong_owner"|"missing")`.
  Add a regression test asserting creator B sees 404 (not 403) when
  probing creator A's task_id.

- [SEV2 — CARRIED, STILL OPEN] **routers/tasks.py:140** — the
  client-supplied `Last-Event-ID` header is forwarded raw into `XREAD` at
  `worker/progress.py:204` with no validation. Redis expects `<ms>-<seq>`
  (or `$` / `0-0`); any other string raises
  `redis.exceptions.ResponseError: Invalid stream ID...`, which
  `aread_since` does not catch. The exception escapes `_event_stream`;
  the `finally:` does release the slot (Python guarantees), but the client
  sees a 500 / connection drop and the server log fills with errors per
  malformed reconnect. | fix: at the top of `_event_stream`, after
  acquiring the slot, validate `last_event_id` against `^\d+-\d+$` (or
  empty); on mismatch reset `cursor = "0-0"`. Cap header length at e.g.
  64 chars before parsing. Add a unit test sending
  `Last-Event-ID: <evil>` and asserting a clean SSE error frame + slot
  released.

- Per-creator isolation re-verified across all 19 endpoints, including the
  Fix-B/C/D code paths:
  - auth.py: token decrypt only at `:206` (account deletion); cascade-delete
    at :255. Fix-D's `aset_owner` uses `str(creator.id)` from the just-upserted
    creator at :81-87 — no client input influences the owner key.
  - billing.py: webhook signature-verified + idempotent at :152-155;
    creator_id derived from Stripe metadata (:137). Fix-C's threadpool call
    passes the authenticated `creator.id` and `creator.stripe_customer_id`
    only — no client input substitution.
  - clips.py: all reads/writes filter `creator_id == creator.id`
    (:74, :116, :121, :143, :174). `aset_owner` at :156 stamps only AFTER
    the :143 isolation check passes.
  - creators.py: every `creator.id` derives from `get_current_creator`.
    `aset_owner` at :167 and :195 both stamp the authenticated principal.
  - improvement.py: VideoMetrics join scoped to `Video.creator_id`
    (:59-64). `ImprovementBrief` filtered by `creator_id` on both POST and
    GET (:74, :136). Fix-B's `aset_owner` at :109 stamps the authenticated
    principal.
  - review.py:50 — `clip.creator_id != creator.id` check before the
    ClipFeedback write.
  - tasks.py: ownership check at :131-138 (modulo the enumeration-oracle
    SEV2 above; the isolation itself is enforced).
  - upload_intel.py:38 — AudienceActivity filtered by `creator_id`.
  - videos.py: every Video read/write is creator-scoped. `aset_owner` at
    :265 stamps from `creator.id`.

- OAuth token handling unchanged: `decrypt()` used at auth.py:206 for
  revocation only; no token / refresh_token in any `logger.*` line. PII
  safe in logs (channel_id / video_id / task_id / clip_id only). No
  virality promise in any string, response, or prompt.

### Concurrency & scale (category 2)

- [SEV2 — CARRIED, STILL OPEN] **videos.py:80-85**, **clips.py:119-124**,
  **upload_intel.py:37-40** — unbounded `list(result.scalars())` with no
  LIMIT/pagination on `GET /videos`, `GET /videos/{video_id}/clips`, and
  `GET /me/upload-intel`. Issue 87's catalog sync bulk-loads full upload
  playlists (hundreds–thousands of rows for established creators); even
  after Issue 90's `source_uri IS NULL` filter, a creator with hundreds
  of ingested videos still serializes the entire list per dashboard hit.
  | fix: keyset pagination `?limit=&before=` with a hard cap (e.g. 100)
  on list_videos and list_clips; bound the AudienceActivity read by the
  documented 168-row max (7 days × 24 hours).

- billing.py:101 (Fix C) — verified: the threadpool offload uses the
  default executor (`asyncio.to_thread` → `loop.run_in_executor(None, ...)`
  → default `ThreadPoolExecutor`, max workers = `min(32, os.cpu_count()+4)`).
  Under sustained 10/min checkout load (the route's rate limit) per
  creator and N concurrent creators, the executor pool is the bottleneck
  but won't saturate at the documented 100-creator scale. Acceptable; if
  scale grows, a dedicated `_STRIPE_EXEC = ThreadPoolExecutor(max_workers=8)`
  with `loop.run_in_executor(_STRIPE_EXEC, ...)` is the documented
  follow-up.

- routers/tasks.py:89 disconnect-poll cadence (12s detection ceiling)
  acceptable given 3-slot cap + 600s lifetime ceiling. No change.

- Module-level singletons (limiter, `_STRIPE` client at
  `billing/stripe_client.py:20`, `progress._SYNC`/`_AIO` Redis clients)
  verified — no per-request client construction.

### Resource lifecycle (category 1)

- DB sessions: every endpoint takes `session: AsyncSession =
  Depends(get_session)` — FastAPI teardown closes on every path including
  exception. Clean.

- videos.py:184-244 — temp-file lifecycle correct: `delete=False` for the
  initial NamedTemporaryFile, `tmp_path.unlink(missing_ok=True)` runs on
  (a) HTTPException during size-limit abort, (b) HTTPException from
  Issue-89 balance pre-check, (c) the duplicate-video 409 path, (d) the
  R2 upload `finally`. No leak.

- SSE generator slot release verified: `aacquire_slot` inside `try:`,
  `arelease_slot` in `finally:` (tasks.py:113-114). `finally:` runs on
  every exit path including the unvalidated-Last-Event-ID exception and
  on async-generator GC at StreamingResponse cancel.

- auth.py:207-211 — `httpx.AsyncClient(timeout=10)` constructed per
  account-deletion call (max 5/hour per creator, only on right-to-erasure).
  Acceptable.

### Error handling & API surface (category 7)

- Pydantic response_model coverage: every endpoint in the slice declares
  a `*Out` model except `/tasks/{task_id}/events` (correctly exempt —
  text/event-stream, not JSON) and `/billing/webhook` (correctly hidden
  from schema; returns dict to Stripe). 19/19 conformant.

- Wave-3 response-model contracts verified:
  - `CheckoutOut.checkout_url: str` (required) — Fix C still returns this
    on success; on Stripe failure raises 502 instead. Matches handler.
  - `BriefQueuedOut.stream_url: str | None = None` (Optional) — now
    correctly utilized by Fix B's Redis-blip fail-open path (returns
    `stream_url=None` instead of 500). The Optional was already in place
    pre-Wave-3 (debounce-collapse `row.job_id is None` window), so the
    schema accommodates Fix B's new path without modification. Clean.

- HTTP status codes verified across all endpoints
  (200/201/202/204/400/401/402/404/409/413/422/502/503). Webhook returns
  200 with `{"status": "ignored"|"already_fulfilled"|"ok"}` — correct
  Stripe contract.

- Error message safety: every `HTTPException(detail=...)` carries a short
  generic string or the user-facing balance-check copy. No DB error /
  stack trace / internal detail leaks. `auth.py:69` includes the upstream
  Google `error` query param in the 400 detail — acceptable (fixed
  vocabulary like `access_denied`, not stack data).

### Code cleanliness & typing (category 6)

- [cleanup — NEW] **Fix-B/D consistency: stamp posture differs across
  endpoints.** Fix B and Fix D wrap `aset_owner` in try/except; the
  Wave-2 stamps at creators.py:167 (sync_catalog), creators.py:195
  (build_dna), clips.py:156 (render_clip), and videos.py:265 (upload)
  do NOT. The Wave-3 product call (graceful Redis-degradation) should
  apply uniformly. | fix: extract `progress.aset_owner_graceful(task_id,
  creator_id, logger) -> bool` that returns `True` on success / `False`
  on `redis.RedisError`, and have all six call sites use it. Callers
  conditionalize `stream_url` on the return value. This also closes the
  videos.py:262-266 SEV2 above when the call site additionally moves
  `start_pipeline(...)` outside the success path.

- [cleanup — CARRIED] **auth.py:131-137** — Fix D has a nested
  `import logging as _logging` inside the `except` block, despite the
  file already having `logger = logging.getLogger(__name__)` at :26.
  The right call here is just `logger.warning(...)`. | fix: replace
  `import logging as _logging; _logging.getLogger(__name__).warning(...)`
  with `logger.warning(...)`. Identical issue to the videos.py:132-134
  one (still open).

- [cleanup — CARRIED] **auth.py:117** — `import redis as _redis_pkg`
  inside the handler. Hoist to module top alongside `import httpx`.
  `improvement.py:93` has the same pattern with the same fix.

- [cleanup — CARRIED] clips.py:46 `_clip_response(clip: Clip) -> dict`
  — hand-mapped field dict still maintained alongside `ClipOut`. | fix:
  add `model_config = ConfigDict(from_attributes=True)` to `ClipOut` and
  populate via `ClipOut.model_validate(clip)`. `principle`/`reasoning`
  from `signals_jsonb` need a `@model_validator(mode='before')` or a
  thin pre-adapter.

- [cleanup — CARRIED] routers/tasks.py:83
  `loop = asyncio.get_event_loop()` — deprecated when called from a
  coroutine with a running loop in 3.12+. | fix: use
  `asyncio.get_running_loop()`.

- [cleanup — CARRIED] routers/videos.py:132-134
  `import logging as _logging` inside an `except` handler — the file
  has no top-level `import logging`. | fix: hoist
  `import logging` + `logger = logging.getLogger(__name__)` to module
  scope to match the rest of the slice.

- [cleanup — CARRIED] routers/improvement.py:79 — `stream_url` literal
  `f"/tasks/{row.job_id}/events"` duplicates the format string used at
  :107 (here), :160 (clips.py), :176 (creators.py), :204 (creators.py),
  and :283 (videos.py). | fix: extract a single
  `progress.stream_url(task_or_entity_id: str) -> str` helper in
  `worker/progress.py` so the URL shape is owned in one place; reduces
  the blast radius of a future "move SSE under /api/v1/tasks/..." rename.

- Inline `from worker.tasks import ...` / `from observability import ...`
  inside handlers is intentional (import-cycle / cold-import avoidance).
  Not flagged.

- All function signatures typed (mypy gate enforces).

### Config & paths (category 8)

- All paths absolute (`tempfile.NamedTemporaryFile`, `Path(...)`).
- SSE policy knobs (`MAX_CONCURRENT_SSE_PER_CREATOR=3`,
  `KEEPALIVE_INTERVAL_S=12.0`, `MAX_STREAM_LIFETIME_S=600.0`) remain
  module-level constants. Acceptable.
- No new env vars introduced in Wave-3.

### Anthropic SDK (category 5)

- n/a — LLM streaming call lives in `worker/anthropic_stream.py` and
  `dna/brief.py`. Routers only open/close the SSE pipe.

### Clip-quality correctness (category 4)

- n/a — not a clip-scoring module.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok (temp-file cleanup, SSE slot release, threadpool offload all verified) |
| 2 Concurrency & scale | 1 SEV2 CARRIED (unbounded list endpoints); Fix C threadpool offload verified canonical |
| 3 Security & compliance | 2 SEV2 CARRIED (tasks.py 404/403 oracle; unvalidated Last-Event-ID; videos.py orphan-pending on Redis blip) |
| 4 Clip-quality | n/a |
| 5 Anthropic SDK | n/a |
| 6 Cleanliness & typing | 6 cleanup (stamp-posture inconsistency NEW; nested-logging-import in auth.py NEW; clip_response dup; get_event_loop deprecation; videos.py inline-logging hoist; stream_url f-string duplication) |
| 7 Error handling / API | ok (19/19 endpoints have *Out or are correctly exempted; Fix-B/C Optional/required Pydantic shapes match handler paths) |
| 8 Config & paths | ok |

## Module verdict
NEEDS-WORK — no BLOCKERs. Wave-3 closes both Wave-2 SEV1s (Fix B
improvement.py orphan-pending and Fix C billing.py event-loop blocking)
and the Wave-2 SEV2 in auth.py (Fix D catalog-sync ownership stamp). All
three deltas verified applied correctly: Fix B's ordering puts the
`row.job_id` commit BEFORE the `aset_owner` attempt and degrades gracefully
on Redis failure; Fix C's `asyncio.to_thread` shape matches
`create_checkout_session`'s positional signature and preserves the 502
error path; Fix D binds `task` from `.delay()` (the load-bearing structural
change) and wraps the stamp in `try/except redis.RedisError`. The
`asyncio.to_thread` recipe is the canonical 2026 idiom for sync-only SDKs
on async routes — confirmed against the Issue 84 Anthropic-SDK research.
What Wave-3 did NOT close: (a) videos.py:262-266 still has the same orphan-
pending Video + R2 blob risk on Redis blip (Fix B's pattern would close it
if applied uniformly — flagged as cleanup-NEW under stamp-posture
inconsistency); (b) the three carry-forward SEV2s (tasks.py 404/403 oracle,
unvalidated Last-Event-ID, unbounded list endpoints) are untouched. None
of the four open SEV2s block launch on their own, but the stamp-posture
inconsistency across six endpoints should be unified before another
feature lands on `aset_owner`.
