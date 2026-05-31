# routers — assessed 2026-05-31

Slice: routers/auth.py, billing.py, clips.py, creators.py, improvement.py,
review.py, tasks.py, upload_intel.py, videos.py.

**Focus this round (Wave-2 / Issue 92):** four endpoints gained a
`progress.aset_owner(...)` stamp + `stream_url` in their response so the
frontend can subscribe to live progress events.
- `routers/creators.py::sync_catalog` — stamps `task.id` (Celery id)
- `routers/improvement.py::start_improvement_brief` — stamps `task.id`;
  debounce-collapse reuses prior task's ownership; `stream_url` Optional
- `routers/clips.py::render_clip` — stamps `clip_id` (deterministic, not
  `task.id`); returns `stream_url: /tasks/{clip_id}/events`
- `routers/videos.py::upload_video` — stamps `video.id` (deterministic);
  call ordered BEFORE `start_pipeline(...)`

Carryover (re-checked): `tasks.py` 404/403 enumeration oracle, unvalidated
`Last-Event-ID`, and the three unbounded list endpoints.

## Findings

### Wave-2 deltas verified clean (no new auth/isolation gaps)

- creators.py:152-177 (sync_catalog) — `aset_owner(task.id, str(creator.id))`
  stamps the authenticated principal; `task.id` is Celery-generated UUID, so
  no creator-supplied input ever influences the owner key. Pydantic
  `CatalogSyncQueuedOut.stream_url: str` validates as required string on the
  response model. Clean.

- creators.py:180-205 (build_dna) — unchanged shape, same pattern, still
  clean.

- improvement.py:96-107 — happy-path stamps owner with `task.id` after
  `delay()`; row.job_id then updated and committed. Debounce-collapse at
  :77-80 reuses the in-flight task's owner-key (already stamped on the
  prior call's :98) and derives `stream_url` from `row.job_id`. The
  `BriefQueuedOut.stream_url: str | None = None` Optional is correct: the
  ONLY path that can hit `stream_url=None` is the debounce-collapse when
  `row.job_id is None`, which is the very-narrow window between the
  in-flight commit (:91 sets `job_id=None`) and the post-stamp commit
  (:99-100 sets `job_id=task.id`). Pydantic validates correctly. Clean.

- clips.py:131-161 (render_clip) — `aset_owner(str(clip_id), str(creator.id))`
  uses the *deterministic* clip_id (not task.id). Critically, the owner
  stamp runs AFTER the creator-isolation check at :143 (`clip.creator_id
  != creator.id → 404`), so a creator can never stamp ownership over a
  clip that isn't theirs. `stream_url: f"/tasks/{clip_id}/events"` matches
  the deterministic-stream-key invariant in DECISIONS.md (Issue 92).
  `RenderQueuedOut.stream_url: str` required — correct (this endpoint
  always returns a stream_url). Clean.

- videos.py:262-284 (upload_video) — `aset_owner(str(video.id), str(creator.id))`
  uses the deterministic video.id. The Video row at :246-247 was just
  constructed with `creator_id=creator.id` so the stamp can never be
  wrong-creator. Ordering is intentional: `aset_owner` runs BEFORE
  `start_pipeline(...)` so the SSE owner key exists before the worker
  emits its first `task:{video.id}:events` payload. `VideoLinkedOut.stream_url:
  str | None = None` Optional — correct because `/videos/link` (the sister
  endpoint at :100) does NOT enqueue a pipeline and returns no stream_url.
  Pydantic schema accommodates both. Clean.

- Deterministic-stream-key invariant consistency verified against
  `docs/DECISIONS.md` 2026-05-31 Issue 92 entry: clip_id for render,
  video.id for upload, Celery task.id for sync_catalog and DNA build (no
  prior natural key client-side), Celery task.id for improvement-brief
  (one-row-per-creator semantics carry the debounce; client doesn't need
  a creator-scoped natural key). All four match the documented contract.

- UUID4 unguessability of clip_id/video.id confirmed
  (`models.py:84,144,212` — `default=uuid.uuid4`, 122 bits of entropy).
  An attacker can't predict another creator's video.id/clip.id to attempt
  cross-tenant stream attachment, and the SSE endpoint's
  `owner == str(creator.id)` check at `routers/tasks.py:137` would reject
  it regardless.

### Security & compliance (category 3)

- [SEV1] routers/improvement.py:91-100 — `aset_owner` is **load-bearing**
  (worker assessment confirms it MUST raise on Redis failure, unlike
  `aemit` which is observational). On a Redis blip between :91 (commits
  `pending` row with `job_id=None`) and :98 (`aset_owner`), the endpoint
  500s back to the client AFTER:
  (a) the ImprovementBrief row is already committed `pending` with
      `job_id=None`,
  (b) the Celery task was already enqueued at :96 and will execute,
  (c) the next call hits the debounce branch at :76 → returns
      `{"status": "pending", "task_id": None, "stream_url": None}` —
      *forever*, because line :99-100 (which would set `row.job_id`) was
      never reached, and the row stays `pending` until the worker
      completes and writes `ready`/`failed`.
  Net effect on Redis blip: client sees 500; subsequent polls show
  perpetual `pending` with no `stream_url`. The worker WILL complete and
  update the row to `ready`, so the user eventually recovers — but during
  the window they have no way to know progress is happening. | fix:
  reorder so `aset_owner` happens BEFORE the commit at :91, with
  `task.id` captured into a local first. Concretely: enqueue → aset_owner
  → set `row.job_id = task.id` → single commit. That way a Redis blip
  on `aset_owner` raises BEFORE any DB write or task enqueue, FastAPI
  returns 503 (or the existing 500), and the user can simply retry —
  no orphaned pending row. Add a regression test that monkey-patches
  `progress.aset_owner` to raise and asserts no ImprovementBrief row
  is left in `pending` afterward.

- [SEV2] routers/videos.py:262-266 — same load-bearing-`aset_owner`
  ordering issue, but bounded blast radius. Order is: commit Video row
  (:255) → `aset_owner` (:265) → `start_pipeline` (:266). On Redis blip,
  the Video row is committed `pending` with `source_uri` already set, the
  pipeline never starts, the 402-equivalent balance debit (which happens
  inside `_ingest_async`) never fires (good — no double-charge), but the
  Video row sits at `ingest_status=pending` forever with the R2 source
  blob orphaned. The dashboard list (after Issue 90) filters out
  `source_uri IS NULL` so this row WILL appear and poll `/status`
  indefinitely. | fix: same shape as the improvement.py fix — move the
  `aset_owner` call BEFORE the Video INSERT/commit, so a Redis failure
  surfaces as a clean 503 before any DB state or R2 blob is committed.
  Either that, or wrap `aset_owner` failures in a compensating step that
  deletes the orphan Video row + R2 blob. Add a regression test.

- [SEV2 — CARRIED, STILL OPEN] routers/tasks.py:131-138 — ownership check
  is an enumeration oracle. `owner is None → 404 "Unknown task"` vs
  `owner != creator.id → 403 "Not your task"` lets an authenticated
  creator distinguish "task_id never existed" from "exists but belongs
  to another creator". The in-code comment at :133-135 explicitly says
  "don't leak whether the task ever existed" but the implementation
  contradicts itself. Wave-2 did not touch this. Note that with Issue 92
  using deterministic clip_id/video.id keys, this also leaks "this
  clip_id / video.id exists somewhere in the system" — UUID4 entropy
  blunts the attack, but the principle violation stands. | fix: return
  404 `"Unknown task"` for BOTH branches; if you need to differentiate
  for debugging, log it server-side via `logger.info("sse_auth_denied
  task_id=%s reason=%s", task_id, "wrong_owner"|"missing")`. Add a
  regression test asserting creator B sees 404 (not 403) when probing
  creator A's task_id.

- [SEV2 — CARRIED, STILL OPEN] routers/tasks.py:140 — the client-supplied
  `Last-Event-ID` header is forwarded raw into `XREAD` at
  `worker/progress.py:204` with no validation. Redis expects `<ms>-<seq>`
  (or `$` / `0-0`); any other string raises
  `redis.exceptions.ResponseError: Invalid stream ID...`, which
  `aread_since` does not catch. The exception escapes `_event_stream`;
  the `finally:` does release the slot (Python guarantees), but the
  client sees a 500 / connection drop and the server log fills with
  errors per malformed reconnect. Wave-2 did not touch this. | fix: at
  the top of `_event_stream`, after acquiring the slot, validate
  `last_event_id` against `^\d+-\d+$` (or empty); on mismatch reset
  `cursor = "0-0"`. Cap header length at e.g. 64 chars before parsing.
  Add a unit test sending `Last-Event-ID: <evil>` and asserting a clean
  SSE error frame + slot released.

- Per-creator isolation verified clean across all 19 endpoints, with the
  Wave-2 owner stamps cross-checked:
  - auth.py: token decrypt only at :188; cascade-delete at :237.
  - billing.py: webhook is signature-verified + idempotent (:144-148);
    creator_id derived from Stripe metadata.
  - clips.py: all reads/writes filter `creator_id == creator.id`
    (:73, :115, :120, :142, :173). The new `aset_owner` at :156 stamps
    only AFTER the :143 isolation check passes — no leak.
  - creators.py: every `creator.id` derives from `get_current_creator`.
    `aset_owner` at :167 and :195 both stamp the authenticated principal,
    never client input.
  - improvement.py: VideoMetrics join scoped to `Video.creator_id`
    (:59-64). `ImprovementBrief` filtered by `creator_id` on both POST
    and GET (:73-74, :118-119). `aset_owner` at :98 stamps the
    authenticated principal.
  - review.py:50 — `clip.creator_id != creator.id` check before the
    ClipFeedback write.
  - tasks.py: ownership check at :131-137 (modulo the enumeration-oracle
    SEV2 above; the isolation itself is enforced).
  - upload_intel.py:38 — AudienceActivity filtered by `creator_id`.
  - videos.py: every Video read/write is creator-scoped. The new
    `aset_owner` at :265 stamps from `creator.id`, never client input.

- OAuth token handling unchanged: `decrypt()` used at auth.py:188 for
  revocation only; no token / refresh_token appears in any `logger.*`
  line in the slice. PII safe in logs (channel_id / video_id / task_id /
  clip_id only). No virality promise in any string, response, or prompt.

### Concurrency & scale (category 2)

- [SEV2 — CARRIED, still open] videos.py:80-85, clips.py:118-124,
  upload_intel.py:37-40 — unbounded `list(result.scalars())` with no
  LIMIT/pagination on `GET /videos`, `GET /videos/{video_id}/clips`, and
  `GET /me/upload-intel`. Issue 87's catalog sync bulk-loads full upload
  playlists (hundreds–thousands of rows for established creators); even
  after Issue 90's `source_uri IS NULL` filter, a creator with hundreds
  of ingested videos still serializes the entire list per dashboard hit.
  | fix: keyset pagination `?limit=&before=` with a hard cap (e.g. 100)
  on list_videos and list_clips; bound the AudienceActivity read by the
  documented 168-row max (7 days × 24 hours).

- routers/tasks.py:89 disconnect-poll cadence (12s detection ceiling)
  is documented and acceptable given the 3-slot cap + 600s lifetime
  ceiling. No change.

- routers/tasks.py:142-150 — StreamingResponse with `text/event-stream`,
  three Cloudflare-safe headers all emitted; no sync/blocking call lands
  on the loop thread (`await client.xread(...)`, `await request.is_disconnected()`).
  Clean.

- Module-level singletons (limiter, stripe client, `progress._SYNC` /
  `_AIO` Redis clients) verified — no per-request client construction.

### Resource lifecycle (category 1)

- DB sessions: every endpoint takes `session: AsyncSession = Depends(get_session)`
  and FastAPI teardown closes on every path including exception. Clean.

- videos.py:184-244 — temp-file lifecycle correct: `delete=False` for the
  initial NamedTemporaryFile, `tmp_path.unlink(missing_ok=True)` runs on
  (a) HTTPException during size-limit abort, (b) HTTPException from
  Issue-89 balance pre-check, (c) the duplicate-video 409 path, (d) the
  R2 upload `finally`. All four exits verified. No leak.

- SSE generator slot release verified: `aacquire_slot` inside `try:`,
  `arelease_slot` in `finally:` (tasks.py:113-114). `finally:` runs on
  every exit path including the unvalidated-Last-Event-ID exception and
  on async-generator GC at StreamingResponse cancel.

- auth.py:189-193 — `httpx.AsyncClient(timeout=10)` constructed per
  account-deletion call. At most ~5/hour per creator and only on
  right-to-erasure; per-call construction is not a hot path. Acceptable.

### Error handling & API surface (category 7)

- Pydantic response_model coverage: every endpoint in the slice declares
  a `*Out` model except `/tasks/{task_id}/events` (correctly exempt —
  text/event-stream, not JSON) and `/billing/webhook` (correctly hidden
  from schema; returns dict to Stripe). 19/19 conformant.

- Wave-2 response-model contracts verified:
  - `CatalogSyncQueuedOut.stream_url: str` (required) — always returned by
    `sync_catalog`. Matches handler.
  - `RenderQueuedOut.stream_url: str` (required) — always returned by
    `render_clip`. Matches handler.
  - `VideoLinkedOut.stream_url: str | None = None` (Optional) — required
    because the sister `/videos/link` endpoint shares the model and
    returns no stream_url. Correctly Optional.
  - `BriefQueuedOut.stream_url: str | None = None` (Optional) —
    debounce-collapse can hit `row.job_id is None` (narrow window between
    initial commit :91 and post-stamp commit :100). Correctly Optional.

- HTTP status codes verified across all endpoints (200/201/202/204/
  400/401/402/404/409/413/422/502/503). Webhook returns 200 with
  `{"status": "ignored"|"already_fulfilled"|"ok"}` — correct Stripe
  contract.

- Error message safety: every `HTTPException(detail=...)` carries a
  short generic string or the user-facing balance-check copy. No DB
  error / stack trace / internal detail leaks. `auth.py:69` includes
  the upstream Google `error` query param in the 400 detail —
  acceptable (fixed vocabulary like `access_denied`, not stack data).

### Code cleanliness & typing (category 6)

- [cleanup — CARRIED, still open] clips.py:46 `_clip_response(clip: Clip) -> dict`
  — hand-mapped field dict still maintained alongside `ClipOut`. | fix:
  add `model_config = ConfigDict(from_attributes=True)` to `ClipOut`
  and populate via `ClipOut.model_validate(clip)`. `principle`/`reasoning`
  from `signals_jsonb` need a `@model_validator(mode='before')` or a
  thin pre-adapter — still simpler than a hand dict.

- [cleanup — CARRIED, still open] routers/tasks.py:83
  `loop = asyncio.get_event_loop()` — deprecated when called from a
  coroutine with a running loop in 3.12+. | fix: use
  `asyncio.get_running_loop()`.

- [cleanup — CARRIED, still open] routers/videos.py:132-135
  `import logging as _logging` inside an `except` handler — the file
  has no top-level `import logging`. | fix: hoist
  `import logging` + `logger = logging.getLogger(__name__)` to module
  scope to match the rest of the slice.

- [cleanup] routers/improvement.py:79 — `stream_url` literal
  `f"/tasks/{row.job_id}/events"` duplicates the format string used at
  :106, :160 (clips.py), :176 (creators.py), :204 (creators.py), and
  :283 (videos.py). | fix: extract a single
  `progress.stream_url(task_or_entity_id: str) -> str` helper in
  `worker/progress.py` so the URL shape is owned in one place; reduces
  the blast radius of a future "move SSE under /api/v1/tasks/..." rename.

- Inline `from worker.tasks import ...` / `from observability import ...`
  inside handlers is intentional (import-cycle / cold-import avoidance).
  Not flagged.

- All function signatures typed (mypy gate would catch any gap).

### Config & paths (category 8)

- All paths absolute (`tempfile.NamedTemporaryFile`, `Path(...)`).
- New tunables (`MAX_CONCURRENT_SSE_PER_CREATOR=3`,
  `KEEPALIVE_INTERVAL_S=12.0`, `MAX_STREAM_LIFETIME_S=600.0`) remain
  module-level constants. Acceptable for SSE policy knobs.
- No new env vars introduced in Wave-2.

### Anthropic SDK (category 5)

- n/a — LLM streaming call lives in `worker/anthropic_stream.py` and
  `dna/brief.py`. Routers only open/close the SSE pipe.

### Clip-quality correctness (category 4)

- n/a — not a clip-scoring module.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok (temp-file cleanup verified; SSE slot release verified) |
| 2 Concurrency & scale | 1 SEV2 CARRIED (unbounded list endpoints) |
| 3 Security & compliance | 1 SEV1 NEW (improvement.py orphan-pending on aset_owner Redis-blip); 1 SEV2 NEW (videos.py orphan-pending Video+R2 on aset_owner Redis-blip); 2 SEV2 CARRIED (tasks.py 404/403 oracle; unvalidated Last-Event-ID) |
| 4 Clip-quality | n/a |
| 5 Anthropic SDK | n/a |
| 6 Cleanliness & typing | 4 cleanup (clip_response dup; get_event_loop deprecation; videos.py inline-logging hoist; stream_url f-string duplication) |
| 7 Error handling / API | ok (19/19 endpoints have *Out or are correctly exempted; Wave-2 Optional/required Pydantic shapes match handler paths) |
| 8 Config & paths | ok |

## Module verdict
NEEDS-WORK — no BLOCKERs. Wave-2's Issue 92 owner-stamp + `stream_url`
additions are correctly creator-scoped, the deterministic-stream-key
invariant (clip_id / video.id) is honored, and Pydantic response models
match the handler return paths (including the correct Optional on
`BriefQueuedOut.stream_url` for the debounce-collapse window and on
`VideoLinkedOut.stream_url` for the sister `/videos/link` endpoint).
What Wave-2 introduced: the load-bearing `aset_owner` call now lives on
four endpoints AFTER the DB commit / task enqueue / R2 PUT, so a Redis
blip on the owner-stamp leaves orphaned pending state in the database
(SEV1 on improvement.py — perpetual pending row with no stream_url;
SEV2 on videos.py — pending Video row + orphan R2 blob). Both fixes are
mechanical: reorder so `aset_owner` runs first, then commit. The three
carry-forward SEV2s (tasks.py 404/403 oracle, unvalidated Last-Event-ID,
unbounded list endpoints) remain untouched by Wave-2.
