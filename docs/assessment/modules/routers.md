# routers — assessed 2026-05-31

Slice: routers/auth.py, billing.py, clips.py, creators.py, improvement.py,
review.py, tasks.py, upload_intel.py, videos.py. (No `health.py` /
`improvement_brief.py` in tree — the prompt's filename guess was off; actual
files are improvement.py and there is no separate health router.)
Focus this round: Wave-1 deltas — videos.py Issues 89/90 (duration-aware
balance pre-check + catalog-row filter) and re-verification of the prior
SEV2 carryovers in tasks.py (404/403 enumeration oracle, unvalidated
`Last-Event-ID`), plus confirmation that the Wave-1 hotfix in
`worker/progress.py` (EXPIRE-on-every-INCR) actually closes the
slot-recovery SEV2 the previous pass flagged.

## Findings

### Wave-1 deltas confirmed clean

- videos.py:228-233 — Issue 89 balance pre-check is correctly wired:
  `check_balance_for_minutes(creator.id, video_minutes(duration_s), session)`
  runs AFTER `probe_duration_s` returns a value and BEFORE the R2 PUT, gated
  on `duration_s is not None` so unknown-duration uploads fall through to
  the legacy path. The `except HTTPException: tmp_path.unlink(...); raise`
  cleanup is correct — the 402 propagates with the temp file removed.
  Predicate matches `deduct_for_video`'s internal `balance >= minutes`
  (billing/ledger.py:203). No new isolation gap.

- videos.py:77-82 — Issue 90 `Video.source_uri.isnot(None)` filter is
  correctly added to the per-creator `WHERE` and excludes catalog-only
  rows from the dashboard list. `creator_id` clause still present. No
  change to the other catalog touchpoints in this module — those don't
  surface lists.

- worker/progress.py:225-232 — the Wave-1 EXPIRE-on-every-INCR hotfix is
  in place and correctly closes the previous slot-recovery SEV2: a
  long-held N>=1 stream now keeps its `sse:count:{creator_id}` key fresh
  rather than letting it silently expire under it.

### Security & compliance (category 3)

- [SEV2 — STILL OPEN, prompt-flagged] routers/tasks.py:131-138 — ownership
  check is an enumeration oracle: `owner is None → 404 "Unknown task"`
  versus `owner != creator.id → 403 "Not your task"` lets an authenticated
  creator distinguish "task_id never existed" from "exists but belongs to
  another creator". The in-code comment at lines 133-135 explicitly
  acknowledges "don't leak whether the task ever existed", but the
  implementation contradicts itself. Wave-1 did not touch this. | fix:
  return `404 "Unknown task"` for BOTH branches; if you need the 403
  signal for debugging, log it server-side via `logger.info(...)`. Add a
  regression test asserting creator B sees 404 (not 403) when probing
  creator A's task_id.

- [SEV2 — STILL OPEN, prompt-flagged] routers/tasks.py:140 — the
  client-supplied `Last-Event-ID` header is forwarded raw into XREAD
  (`worker/progress.py:204`) with no validation. Redis expects
  `<ms>-<seq>` (or `$` / `0-0`); any other string raises
  `redis.exceptions.ResponseError: Invalid stream ID...`, which
  `aread_since` does not catch. The exception escapes `_event_stream`;
  the `finally:` does release the slot (verified — Python semantics
  guarantee `finally:` runs on exception), but the client sees a 500 /
  connection drop and the server log fills with errors on each malformed
  reconnect. Wave-1 did not touch this. | fix: at the top of
  `_event_stream`, after acquiring the slot, validate
  `last_event_id` against `^\d+-\d+$` (or empty); on mismatch reset
  `cursor = "0-0"`. Cap header length at e.g. 64 chars before that check
  so a huge header isn't even parsed. Add a unit test sending
  `Last-Event-ID: <evil>` and asserting a clean SSE error frame + slot
  released.

- Per-creator isolation verified clean across all 19 endpoints:
  - auth.py: token decrypt only at :188; cascade-delete via session.delete(creator) at :237.
  - billing.py: webhook is signature-verified and idempotent (:144-148);
    creator_id derived from Stripe metadata (creator-scoped on every write).
  - clips.py: all reads/writes filter `creator_id == creator.id`
    (:73, :115, :120, :142, :163). `_clip_response` reads from the
    already-isolated row.
  - creators.py: every `creator.id` derives from `get_current_creator`.
    `aset_owner(task.id, str(creator.id))` at :186 stamps SSE ownership
    from the authenticated principal, not from client input.
  - improvement.py: VideoMetrics join scoped to `Video.creator_id` (:56-61),
    closing the prior Issue-33 SEV-0. `ImprovementBrief` filtered by
    `creator_id` on both POST and GET (:71, :106).
  - review.py:50 — `clip.creator_id != creator.id` check before the
    ClipFeedback write.
  - tasks.py: ownership check at :131-137 (modulo the enumeration-oracle
    SEV2 above; the isolation itself is enforced).
  - upload_intel.py:38 — AudienceActivity filtered by `creator_id`.
  - videos.py: every Video read/write is creator-scoped.

- OAuth token handling unchanged: `decrypt()` used at auth.py:188 for
  revocation only; no token / refresh_token appears in any `logger.*`
  line in the slice. PII safe in logs (channel_id / video_id / task_id
  only).

- No virality promise in any string, response, or prompt.

### Concurrency & scale (category 2)

- [SEV2 — CARRIED, still open] videos.py:77-82, clips.py:118-123,
  upload_intel.py:37-40 — unbounded `list(result.scalars())` with no
  LIMIT/pagination on `GET /videos`, `GET /videos/{video_id}/clips`, and
  `GET /me/upload-intel`. With Issue-87's catalog sync now bulk-loading
  full upload playlists into `videos` (hundreds–thousands of rows for
  established creators), the videos endpoint is the most acute: even
  after the Issue-90 `source_uri IS NULL` filter, a creator with hundreds
  of ingested videos still serializes the entire list per dashboard
  hit. | fix: keyset pagination `?limit=&before=` with a hard cap (e.g.
  100) on list_videos and list_clips; bound the AudienceActivity read by
  the documented 168-row max (7 days × 24 hours).

- routers/tasks.py:89 disconnect-poll cadence (12 s detection ceiling)
  is documented and acceptable given the 3-slot cap + 600s lifetime
  ceiling. No change.

- routers/tasks.py:142-150 — StreamingResponse with `text/event-stream`,
  three Cloudflare-safe headers all emitted; no sync/blocking call lands
  on the loop thread (`await client.xread(...)`, `await
  request.is_disconnected()`). Clean.

- Module-level singletons (limiter, stripe client, `progress._SYNC` /
  `_AIO` Redis clients) verified — no per-request client construction in
  this slice.

### Resource lifecycle (category 1)

- DB sessions: every endpoint takes `session: AsyncSession = Depends(get_session)`
  and FastAPI teardown closes them on every path including exception.
  No bare `AsyncSession()` construction. Clean.

- videos.py:181-242 — temp-file lifecycle correct: `delete=False` for the
  initial NamedTemporaryFile, `tmp_path.unlink(missing_ok=True)` runs on
  (a) HTTPException during size-limit abort, (b) HTTPException from the
  Issue-89 balance pre-check, (c) the duplicate-video 409 path, (d) the
  R2 upload `finally`. All four exits verified. No leak.

- SSE generator slot release verified: `aacquire_slot` inside `try:`,
  `arelease_slot` in `finally:` (tasks.py:113-114). `finally:` runs on
  every exit path including the unvalidated-Last-Event-ID exception
  flagged above and on async-generator GC at StreamingResponse cancel.

- auth.py:189-193 — `httpx.AsyncClient(timeout=10)` constructed per
  account-deletion call. This is at most ~5/hour per creator (the
  rate-limit ceiling at :164) and only fires on right-to-erasure, so the
  per-call construction is not a hot path. Acceptable.

### Error handling & API surface (category 7)

- Pydantic response_model coverage: every endpoint in the slice declares
  a `*Out` model except `/tasks/{task_id}/events` (correctly exempt —
  text/event-stream, not JSON) and the `/billing/webhook` (correctly
  hidden from schema; returns dict to Stripe). Prior SEV1 stays closed.
  19/19 endpoints conformant.

- HTTP status codes verified across all endpoints:
  - 200 default; 201 on `/me/identity`, `/clips/{id}/feedback`; 202 on
    queued jobs (catalog sync, DNA build, render, improvement-brief);
    204 on account delete.
  - 400 for invalid input / not-enough-data; 401 from `get_current_creator`
    Depends; 402 from balance checks; 404 for not-found; 409 for
    duplicate video / DNA-confirm race / render-in-progress; 413 for
    upload size; 422 for invalid identity / youtube_video_id;
    502 for Stripe upstream; 503 for billing-disabled.
  - Webhook returns `{"status": "ignored"|"already_fulfilled"|"ok"}`
    with 200 — correct Stripe contract (avoid retries on consumed
    events).

- Error message safety: every `HTTPException(detail=...)` carries a
  short generic string or the user-facing `check_balance_for_minutes`
  copy. No DB error / stack trace / internal detail leaks to the client.
  `auth.py:69` does include the upstream Google error string in the 400
  detail — acceptable (Google's `error` query param is a fixed
  vocabulary like `access_denied`, not stack data) but worth noting.

### Code cleanliness & typing (category 6)

- [cleanup — CARRIED, still open] clips.py:45 `_clip_response(clip: Clip) -> dict`
  — the hand-mapped field dict is still maintained alongside `ClipOut`;
  same fields in two places. | fix: add
  `model_config = ConfigDict(from_attributes=True)` to `ClipOut` and
  populate via `ClipOut.model_validate(clip)` so the field list lives
  in one place. (Note: `principle`/`reasoning` come from
  `signals_jsonb` so they need a `@model_validator(mode='before')` or
  a thin pre-adapter — still simpler than a hand dict.)

- [cleanup — CARRIED, still open] routers/tasks.py:83
  `loop = asyncio.get_event_loop()` — deprecated when called from a
  coroutine with a running loop in 3.12+. | fix: use
  `asyncio.get_running_loop()`.

- [cleanup] routers/videos.py:129 `import logging as _logging` inside
  the `except` handler — the module already has logging available at
  the top of `__init__` via the other routers, but this file has no
  top-level `import logging`. Hoist `import logging` + `logger = logging.getLogger(__name__)`
  to module scope to match the rest of the slice.

- Inline `from worker.tasks import ...` / `from observability import ...`
  inside handlers is intentional (import-cycle / cold-import avoidance)
  and matches the rest of the slice — not flagged.

- All function signatures typed (mypy gate would catch any gap).

### Config & paths (category 8)

- All paths absolute (`tempfile.NamedTemporaryFile`, `Path(...)`).
- New tunables (`MAX_CONCURRENT_SSE_PER_CREATOR=3`,
  `KEEPALIVE_INTERVAL_S=12.0`, `MAX_STREAM_LIFETIME_S=600.0`) remain
  module-level constants. Acceptable for SSE policy knobs that rarely
  change in prod; not flagged.
- No new env vars introduced this wave.

### Anthropic SDK (category 5)

- n/a — LLM streaming call lives in `worker/anthropic_stream.py` /
  `dna/brief.py`. Routers only open / close the SSE pipe.

### Clip-quality correctness (category 4)

- n/a — not a clip-scoring module.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok (Issue-89 temp-file cleanup verified; SSE slot release verified) |
| 2 Concurrency & scale | 1 SEV2 (unbounded list endpoints — carried; the previous slot-recovery SEV2 is CLOSED by Wave-1 EXPIRE-on-every-INCR hotfix) |
| 3 Security & compliance | 2 SEV2 (404/403 enumeration oracle; unvalidated Last-Event-ID) — both prompt-confirmed NOT addressed in Wave 1 |
| 4 Clip-quality | n/a |
| 5 Anthropic SDK | n/a (lives in worker/anthropic_stream.py + dna/brief.py) |
| 6 Cleanliness & typing | 3 cleanup (clip_response dup; get_event_loop deprecation; videos.py inline logging hoist) |
| 7 Error handling / API | ok (19/19 endpoints have *Out or are correctly exempted; status codes correct) |
| 8 Config & paths | ok |

## Module verdict
NEEDS-WORK — no BLOCKERs; Wave-1's Issue-89 duration-aware balance pre-check
and Issue-90 catalog-row filter are correctly wired and creator-scoped, and
the upstream slot-recovery SEV2 from the prior pass is closed by the
EXPIRE-on-every-INCR hotfix in `worker/progress.py`. Remaining open work
is exactly the three SEV2s carried from the prior pass: collapse the
tasks.py 404/403 ownership oracle, validate `Last-Event-ID` before XREAD,
and add a hard-cap + keyset pagination on the three unbounded list
endpoints (`/videos`, `/videos/{id}/clips`, `/me/upload-intel`).
