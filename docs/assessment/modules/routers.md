# routers — assessed 2026-05-30

Slice: routers/auth.py, billing.py, clips.py, creators.py, improvement.py,
review.py, tasks.py (NEW — Issue 86 SSE), upload_intel.py, videos.py.
Focus this round: the new `/tasks/{task_id}/events` SSE endpoint (auth/ownership
behavior, per-creator concurrent-cap correctness, Cloudflare header set, slot
release on disconnect / terminal event / lifetime cap, `Last-Event-ID` resume
hardening), and confirmation of the prior carryover SEV1 (response_model gap).

## Findings

### Security & compliance (category 3)

- [SEV2] routers/tasks.py:131-138 — ownership check is an enumeration oracle:
  the 404/403 split lets an unauthenticated probe distinguish "task_id never
  existed" (404) from "exists but belongs to another creator" (403). The
  in-code comment at line 133-135 explicitly says "Either way: don't leak
  whether the task ever existed" — but the implementation contradicts the
  comment. With 32-char Celery UUIDs the search space is huge, so this is
  bounded, but it's still a discoverable side channel and easy to fix. | fix:
  return `404 "Unknown task"` for both the `owner is None` AND the
  `owner != str(creator.id)` branches; log the distinction server-side for
  debugging if needed. Add a regression test asserting creator B sees 404
  (not 403) when probing creator A's task_id.

- [SEV2] routers/tasks.py:140 + worker/progress.py:204 — the client-supplied
  `Last-Event-ID` header is forwarded raw into `XREAD` with no validation.
  Redis expects the form `<ms>-<seq>` (or `$` / `0-0`); any other string —
  e.g. `Last-Event-ID: bogus` or a multi-KB payload — triggers
  `redis.exceptions.ResponseError: Invalid stream ID specified as stream
  command argument`, which `aread_since` does not catch. The exception
  propagates out of `_event_stream`; the `finally:` does release the SSE slot
  (good — verified by Python semantics), but the client gets a mid-stream
  500 / connection drop instead of a clean SSE `error` event, and a noisy
  500 lands in the server logs on every malformed reconnect. | fix: at the
  top of `_event_stream`, after acquiring the slot, validate
  `last_event_id` matches `^\d+-\d+$` (or is the empty string); on
  mismatch reset `cursor = "0-0"` and continue. Optionally also bound
  the header length (e.g. reject >64 chars) before that check so a huge
  header isn't even parsed. Add a unit test sending
  `Last-Event-ID: <evil>` and asserting a clean SSE error frame + slot
  released.

- Auth path on `/tasks/{task_id}/events` clean: `get_current_creator` is a
  Depends so an unauthenticated client gets 401 before any Redis lookup. No
  `task_id` lookup leaks information to unauthenticated probes.

- Per-creator isolation still verified clean across every other route in the
  module (every creator-scoped read/write derives `creator.id` from the JWT;
  the creators.py:158 `aset_owner(task.id, str(creator.id))` correctly stamps
  the SSE ownership from the authenticated principal, not from any
  client-supplied value).

- OAuth token handling unchanged from prior pass: `decrypt()` only used in
  `auth.py:170` for revocation; no token / email / secret appears in any
  `logger.*` call in the module.

- No virality promise anywhere in the new strings (`tasks.py` docstrings, SSE
  event messages, BuildQueuedOut field).

### Concurrency & scale (category 2)

- [SEV2] routers/tasks.py:77 + worker/progress.py:214-232 — concurrent-cap
  recovery window is up to 1 hour on abnormal process exit. The cap counter
  `sse:count:{creator_id}` is INCR'd on slot acquire and DECR'd in the
  generator's `finally:`. If the API replica is `SIGKILL`'d / OOM-killed /
  hard-crashed mid-stream, the `finally:` never runs and the counter stays
  inflated until the 3600s TTL set on first-INCR expires. A creator who hit
  the cap (3) at the moment of crash is then locked out for up to an hour
  with no manual recovery path. With ~hundreds of creators and one OOM, this
  becomes a small but real support load. | fix: lower the TTL on
  `sse:count:{creator_id}` to ~120-300s (an SSE stream that hasn't sent a
  keepalive in that window is dead anyway, and the active streams will
  re-INCR + re-set TTL on each reconnect — they self-heal). Alternatively,
  refresh the TTL on every keepalive iteration of `_event_stream` so a
  healthy long-running stream keeps the key fresh and a crashed stream's
  key actually expires promptly.

- routers/tasks.py:89 disconnect-poll cadence: `request.is_disconnected()`
  is only consulted once per loop iteration, and `aread_since` then blocks
  up to 12s on `XREAD`. A client that disconnects right after iteration
  start can therefore waste one slot for ~12s before detection. This is
  the design — `KEEPALIVE_INTERVAL_S = 12.0` is the intentional ceiling on
  detection latency — and is acceptable given the 3-slot cap + 600s
  lifetime ceiling. No change required.

- routers/tasks.py:142-145 + worker/progress.py:204 — `StreamingResponse`
  with `media_type="text/event-stream"` correctly emits headers before
  iterating the async generator. The `_event_stream` body uses
  `await client.xread(...)` (async redis) and `await request.is_disconnected()`
  — no sync/blocking call lands on the loop thread. Clean.

- creators.py:158 `await progress.aset_owner(task.id, str(creator.id))` —
  runs BEFORE the 202 response is returned to the client. Even though
  `build_dna_task.delay()` could in principle dispatch + complete (and the
  worker emit a `done` event) faster than this two-Redis-call sequence, the
  client cannot see the `stream_url` until *after* `aset_owner` has
  succeeded. So the documented race ("task fires + completes between
  `.delay()` and `aset_owner`") only matters for the events emitted by the
  worker between `.delay()` returning and `aset_owner` finishing — the
  stream is persistent in Redis (XADD), so the client's subsequent
  subscribe will simply replay those events from `0-0`. No correctness
  issue.

- routers/tasks.py:118 `@limiter.limit("120/minute")` shape correct for an
  SSE endpoint: slowapi consults the limiter once per request, at connect.
  Long-lived stream → one limiter check per stream → bounded reconnect
  storms (max 120 connects/min/creator). The actual stream lifetime is
  hard-capped to 600s via `MAX_STREAM_LIFETIME_S` so the limiter is not
  responsible for stream-length enforcement.

- All Cloudflare-safe headers confirmed emitted (routers/tasks.py:148-150):
  `Cache-Control: no-cache`, `Connection: keep-alive`, `X-Accel-Buffering: no`.
  All three required for the prod Cloudflare Tunnel + Nginx-style proxy
  buffering inhibition, all three present.

- [SEV2 — carried from prior pass, still present] videos.py:65-66,
  clips.py:118-123, upload_intel.py:37-40 — unbounded `list(result.scalars())`
  with no LIMIT/pagination on `GET /videos`, `GET /videos/{id}/clips`, and
  the audience-activity read. A creator with thousands of rows loads the
  whole set into memory and serializes it in a single response. | fix:
  add keyset or offset pagination (`?limit=&before=`) with a hard cap
  (e.g. 100) on list_videos and list_clips; bound the AudienceActivity
  read.

### Resource lifecycle (category 1)

- SSE generator slot release path verified: `aacquire_slot` runs INSIDE the
  `try:` block (routers/tasks.py:77); `arelease_slot` runs in the `finally:`
  at line 113-114. Python guarantees `finally:` executes on every normal
  exit path (terminal event return, lifetime exceeded return, client
  disconnect return) AND on every exception (including the `Last-Event-ID`
  XREAD exception flagged above), AND when the async generator is GC'd
  mid-iteration (StreamingResponse's anyio context calls `.aclose()` on
  cancellation). The only case where DECR is skipped is hard process kill,
  which is the SEV2 above. Clean apart from that.

- DB sessions across the module still come from `Depends(get_session)`;
  closed on all paths by FastAPI dependency teardown. Clean.

- Module-level singletons: `limiter`, stripe client, and now the
  `worker/progress._SYNC` + `_AIO` Redis clients (used by the SSE endpoint).
  No per-request Redis client construction. Clean.

- Upload temp file removed in `finally` (videos.py:148-149, 165, 174) and
  on the size-limit early-abort path. No leak.

- billing webhook idempotency still in place (billing.py:144-148).

### Error handling & API surface (category 7)

- PRIOR SEV1 CLOSED: response_model coverage is now complete across the
  module. All 18 endpoints listed in the 2026-05-29 pass now declare a
  `*Out` Pydantic response_model (verified by grep of every `@router.` /
  `@clips_router.` decorator against `response_model=`). The new
  `routers/tasks.py:117` is the sole exemption — it returns
  `text/event-stream`, not a JSON body, and is correctly listed in
  `tests/test_response_models.py:23` with a citation to Issue 86. The
  hand-built `_clip_response` dict at clips.py:45 is still present but
  now flows through `ClipListOut`/`ClipOut`, so the response shape is
  validated; it's a cleanup item, not a SEV.

- HTTP status codes correct on the new endpoint:
  - 401 (via `get_current_creator` Depends) for unauthenticated requests
  - 403 for ownership mismatch (see SEV2 enumeration-oracle finding above
    for the recommendation to collapse to 404)
  - 404 for unknown task
  - 200 + `text/event-stream` for the streaming success path
  All other status codes verified clean in the prior pass and unchanged.

- Error messages safe across new code paths: tasks.py:136 / :138 details
  are generic (`"Unknown task"` / `"Not your task"`); the in-stream
  error events at lines 80 / 92 carry only short fixed strings — no
  internal detail, no stack trace.

### Code cleanliness & typing (category 6)

- [cleanup] clips.py:45 `_clip_response(clip: Clip) -> dict` — the
  hand-maintained field mapping is now covered by `ClipListOut`/`ClipOut`
  validation, but the dict shape is still maintained by hand and
  duplicated by the response_model. Build the ClipOut directly:
  `ClipOut.model_validate(...)` from a Clip → dict adapter or, better,
  add `model_config = ConfigDict(from_attributes=True)` on ClipOut and
  return `ClipOut.model_validate(clip)` directly so the field list lives
  in exactly one place.

- [cleanup] routers/tasks.py:83 `loop = asyncio.get_event_loop()` —
  deprecated in 3.12+ when not called from a coroutine that already has
  a running loop. In this context there's always a running loop, so
  prefer `loop = asyncio.get_running_loop()` to match the documented
  3.12+ API and avoid a future deprecation warning.

- [cleanup] improvement.py:47 `_avg(lst)` from the prior pass — the file
  no longer contains this helper; closed.

- Inline `from ... import` inside handlers (clips.py / creators.py /
  auth.py / improvement.py / tasks.py:154-155) remains deliberate
  (import cycle / heavy-worker import avoidance) — not flagged.

### Config & paths (category 8)

- All paths absolute / tempfile-based.
- New tunables in routers/tasks.py are module-level constants
  (`MAX_CONCURRENT_SSE_PER_CREATOR=3`, `KEEPALIVE_INTERVAL_S=12.0`,
  `MAX_STREAM_LIFETIME_S=600.0`) rather than `settings`. For a SSE-policy
  knob this is fine (the values are operational defaults that rarely
  change), and the choice is documented in-line. Not flagged — but if any
  of the three need to be tuned in prod without a deploy, lift them into
  `config.Settings`.
- Per-creator rate limiting unchanged: `_creator_key` keys on JWT `sub`,
  fallback to remote IP; acceptable.

### Anthropic SDK (category 5)

- n/a — the LLM streaming call lives in `worker/anthropic_stream.py` and
  `dna/brief.py`; the router only opens / closes the SSE pipe. SDK
  correctness is those modules' slice.

### Clip-quality correctness (category 4)

- n/a — not a clip-scoring module.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok (generator finally verified; one slot-recovery edge in cat. 2) |
| 2 Concurrency & scale | 2 SEV2 (slot recovery on hard-kill; unbounded list endpoints carried over) |
| 3 Security & compliance | 2 SEV2 (404/403 enumeration oracle; Last-Event-ID unvalidated) |
| 4 Clip-quality | n/a |
| 5 Anthropic SDK | n/a (SDK call lives in worker/anthropic_stream.py + dna/brief.py) |
| 6 Cleanliness & typing | 2 cleanup |
| 7 Error handling / API | ok — PRIOR SEV1 response_model gap CLOSED (18/18 endpoints have *Out) |
| 8 Config & paths | ok |

## Module verdict
NEEDS-WORK — no BLOCKERs; the prior SEV1 response_model gap is fully closed
across all 18 endpoints, the new SSE endpoint correctly enforces ownership,
emits all three Cloudflare-safe headers, hard-caps stream lifetime, and
releases its concurrent-cap slot on every normal-exit path. Remaining work
is small and isolated: collapse the 404/403 ownership oracle, validate the
`Last-Event-ID` header before XREAD, and shorten the `sse:count` key TTL so
a hard-killed API replica doesn't lock a creator out for an hour. The
prior pagination SEV2 on three list endpoints is still open.
