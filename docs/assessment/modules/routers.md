# routers — assessed 2026-06-16

## Findings

- [SEV2] routers/activity.py:38-44 — STILL BROKEN (carry-forward from 2026-06-09).
  `creator = await get_current_creator(request)` calls the FastAPI dependency
  directly with only `request`, so its `session` param stays the bare
  `Depends(get_session)` marker object; `session.execute(...)` at auth.py:47 raises
  `AttributeError`, which the blanket `except Exception: pass` swallows →
  `creator_id` is ALWAYS `"anonymous"`, even for logged-in users. The beta-telemetry
  attribution this endpoint exists for silently never works | fix: decode the session
  cookie directly — `auth.decode_session_token(request.cookies.get(SESSION_COOKIE))`
  → `uuid.UUID(payload["sub"])` (no DB round-trip needed for a log line) — and narrow
  the except to `jwt.PyJWTError | KeyError | ValueError`.

- [SEV2] routers/activity.py:46-59 — STILL BROKEN (carry-forward). Unauthenticated
  client controls `extra` dict keys passed as `**safe_extra` into `log_event(...)`:
  (a) a key equal to `page` / `creator_id` / `event_type` / `target` → duplicate-kwarg
  `TypeError` → 500; (b) a key equal to a reserved `LogRecord` attribute
  (`name`, `message`, `module`, `args`, …) → `KeyError: Attempt to overwrite ...`
  inside `logging` → 500; (c) arbitrary keys land as top-level structured-log fields =
  log-injection surface; (d) only `str` values are truncated — a nested dict/list value
  of arbitrary size bloats the log line | fix: prefix every client key (`f"ui_{k}"`),
  allowlist scalar value types (`str|int|float|bool`), drop the rest. Add a test posting
  `extra={"message": "x", "page": "y"}` asserting 204 not 500.

- [SEV2] routers/auth.py:232-236 — STILL PRESENT (carry-forward). The decrypted Google
  refresh token is sent to the revoke endpoint as a URL query parameter
  (`params={"token": refresh_token}`). Secrets in query strings are recorded by
  proxies / egress logs, and an httpx exception whose message embeds the request URL
  would put the token into the `logger.warning` at line 255
  (needs-runtime-confirmation for the httpx-message vector; the query-string exposure
  is structural). Google documents revocation as a form-encoded POST body | fix:
  `client.post(url, data={"token": refresh_token},
  headers={"Content-Type": "application/x-www-form-urlencoded"})`.

- [SEV2] routers/videos.py:157-189 / :250-293 — STILL PRESENT (carry-forward). Both
  `link_video` and `upload_video` are check-then-insert: two concurrent submits of the
  same `youtube_video_id` (double-click) both pass the SELECT, the loser hits the
  `UNIQUE(creator_id, youtube_video_id)` constraint at `commit()` → unhandled
  `IntegrityError` → raw 500 (verified: videos.py imports no `IntegrityError` and no
  `try/except` wraps either commit). The repo already has the correct pattern at
  improvement.py:115-129 | fix: wrap `commit()` in `try/except IntegrityError` →
  `rollback()` → 409 "Video already registered"; add a two-concurrent-POSTs regression
  test.

- [SEV2] routers/clips.py:100-139 — STILL PRESENT (carry-forward). `POST
  /videos/{id}/clips/generate` awaits `generate_and_rank_clips` →
  `score_candidates` → an AsyncAnthropic `messages.create` (clip_engine/scoring.py)
  inside the request/response cycle. Every other LLM surface in this slice (analysis,
  titles, thumbnail-concepts, improvement brief) was moved to 202 + Celery + SSE
  precisely because LLM latency can exceed the LB timeout; this one endpoint still
  holds the HTTP request open for the full scoring pass
  (needs-runtime-confirmation on p95 duration) | fix: convert to the established
  202 + `TaskQueuedOut` + `aset_owner` pattern; ranking.py's idempotent re-entry
  already makes the worker retry-safe.

- [cleanup] routers/clips.py:139 — STILL PRESENT (carry-forward). The generate endpoint
  returns only `{"clips": [...]}`, so `ClipListOut`'s default `state="populated"` is
  emitted even when the engine produced zero candidates — contradicts the empty-state
  envelope contract the same model implements at the list endpoint (:167-185) | fix:
  return `state=build_envelope_state(len(items))` plus a "no candidates met the
  threshold" message on empty.

- [cleanup] routers/tasks.py:117-123 — STILL PRESENT (carry-forward). `task_events`
  has no return type annotation (only untyped signature in the slice); tasks.py:83 uses
  deprecated `asyncio.get_event_loop()` inside a coroutine | fix: annotate
  `-> StreamingResponse`; use `asyncio.get_running_loop()`.

- [cleanup] routers/insights.py:456 — STILL PRESENT (carry-forward).
  `_HAIKU_MODEL = "claude-haiku-4-5-20251001"` hardcoded in the router while
  config.py owns `ANTHROPIC_MODEL` | fix: add `ANTHROPIC_HAIKU_MODEL` to Settings +
  `.env.example` and read it here.

- [cleanup] routers/insights.py:118-165 — STILL PRESENT (carry-forward). Internal
  symbols `_compute_virality_score` + "Virality score" comment; the wire field is
  correctly `performance_score` and no response string promises virality, but the
  internal name invites future leakage and dirties the no-virality structural grep |
  fix: rename to `_compute_performance_score`.

- [cleanup] routers/insights.py:563-571 — inert `cache_control: {type: ephemeral}` on
  the Haiku-4.5 analyze-performer call. With `max_tokens=256` and a short
  instructions+DNA prefix the cached prefix is almost certainly below the model's
  4096-token cache floor, so the marker is inert (1.25× write premium, zero reads).
  ALREADY LOGGED in docs/OFF_COURSE_BUGS.md (2026-06-16) as out-of-scope cleanup; not
  a new finding | fix (per the log): confirm prefix size via
  `usage.cache_creation` on a live call; if < 4096, remove the marker.

- [cleanup] routers/auth.py:50-62, :65-177 — STILL PRESENT (carry-forward).
  `/auth/login` and `/auth/callback` carry no rate limit (unauthenticated; callback
  does outbound Google token-exchange round-trips per hit). Pre-launch checklist
  already tracks per-creator rate limiting | fix: add IP-keyed `@limiter.limit`
  (e.g. `20/minute`, `key_func=get_remote_address`) to both before launch.

- [cleanup] DRY — STILL PRESENT (carry-forward). The
  `task = await asyncio.to_thread(x.delay, ...)` + `aset_owner` try/except
  `RedisError` → `stream_url=None` block is copied ~12× (analysis.py:119-136,
  217-224, 290-297; clips.py:234-250, 411-421, 612-622, 744-753; creators.py:220-235,
  265-276; auth.py:142-155; thumbnails.py:275-288; titles.py:77-90) | fix: extract
  `async def enqueue_with_stream(task_sig, owner_key, creator_id) -> tuple[str, str | None]`
  into routers/_schemas.py or a new routers/_tasks_util.py.

### SEV1 #3 (Issue 138) verification — CLOSED ✓

- routers/thumbnails.py:140-224 — `GET /creators/me/thumbnail-patterns` now has
  `request: Request` + `@limiter.limit("10/hour", key_func=creator_key)` (line 144),
  matching the POST below it. ✓
- Per-creator single-flight via `_compute_patterns_single_flight` (thumbnails.py:77-122):
  `SET NX EX 130` acquire (line 96), waiters poll the cache `_PATTERNS_WAIT_COUNT=3` ×
  `0.4s` then fall through (lines 101-107), the billed `analyze_thumbnail_patterns`
  runs in a worker thread via `asyncio.to_thread` (line 110), and the lock is released
  in a `finally` (lines 115-119) via a Lua compare-and-delete that only deletes a token
  it still owns (`_LUA_RELEASE_LOCK`, lines 36-42) — no risk of releasing another
  request's lock. Release is gated on `if acquired:` so a waiter never deletes the
  holder's key. Fail-open is correct on every Redis edge: acquire `RedisError` →
  `acquired=True` → proceeds (rate limit still bounds exposure); release `RedisError`
  is suppressed (lock then expires via the 130s TTL > the 120s Anthropic timeout, so
  no permanent lock leak). Cache read/write are independently fail-open
  (lines 57-74, 111-114). Helper is sound. ✓

### Carry-forward verification (2026-06-09 → today)

- Async hygiene (`.delay` wrapped in `to_thread`) — still intact at all enqueue sites. ✓
- Empty-state envelopes (`/videos`, `/insights/saved`, `/clips` list) — intact
  (videos.py:121-144, insights.py:655-670, clips.py:167-185); gap on the generate path
  noted above. ✓
- Onboarding-state aggregation — `SetupStepOut` nested on `/auth/me` (auth.py:195) and
  `/creators/me` (creators.py:158) via `resolve_setup_step`. ✓
- Per-creator isolation — re-traced every SELECT/UPDATE in the slice: all queries on
  creator-scoped tables carry `creator_id == creator.id`, or a `session.get` +
  `creator_id !=` ownership check before any child-table access (Transcript / Signals /
  VideoMetrics / RetentionCurve / Clip keyed by an already-verified parent id); SSE
  streams gated by the Redis owner key (tasks.py:131-138); Stripe webhook stamps
  `session.info["creator_id"]` before its idempotency query (billing.py:215). No
  cross-tenant leak found. ✓
- OAuth token handling — read via `decrypt()` (auth.py:231), never returned, never
  logged directly (the query-param transport finding above is the residual structural
  risk). ✓
- anthropic 0.40.0→0.105.2 bump — the slice's only direct SDK call (insights.py:573-585)
  uses `system` block + `messages.create` + token logging; signature unaffected by the
  bump. The inert cache marker is the sole residue (logged, out-of-scope). ✓

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — temp files unlinked in `finally` (videos.py:281, clips.py:722); Anthropic + Redis clients module-level singletons; SSE slot released in `finally` (tasks.py:113-114); sessions via DI |
| 2 Concurrency & scale | 2 findings — link/upload insert race → 500 (SEV2), LLM in request path on `/clips/generate` (SEV2). SEV1 #3 (unratelimited in-request multimodal LLM) now CLOSED; to_thread + single-flight verified |
| 3 Security & compliance | 3 findings — activity log injection + 500s (SEV2), refresh token in query string (SEV2), activity attribution broken → all "anonymous" (SEV2); per-creator isolation verified on every query, no virality promise on the wire |
| 4 Clip-quality | n/a (router layer; engine owned elsewhere) |
| 5 Anthropic SDK | ok — prompt caching marker + token logging present on the slice's one direct call; marker is inert (logged off-course, cleanup) |
| 6 Cleanliness & typing | 5 cleanup — 12× aset_owner duplication, untyped `task_events`, deprecated `get_event_loop`, internal virality naming, hardcoded Haiku model id |
| 7 Error handling / API | ok — Pydantic on every endpoint, correct codes, safe messages; residual: IntegrityError 500 path (counted under SEV2) and activity `**safe_extra` 500 path (counted under SEV2) |
| 8 Config & paths | 1 cleanup — hardcoded Haiku model id; paths absolute, no missing `.env` entries found |

## Module verdict

NEEDS-WORK — the Issue-138 SEV1 #3 is properly CLOSED (rate limit + a sound
single-flight lock with no leak path) and no cross-tenant leak exists, but five
prior SEV2s persist unchanged: broken telemetry attribution (always "anonymous"),
unauthenticated log-injection 500s on `/api/activity`, the Google refresh token in a
query string, the link/upload double-submit 500 race, and the last in-request LLM call
on `/clips/generate`. Fix these before launch.

## Issue 75 Reconciliation (2026-06-23)

| Finding | Disposition |
|---|---|
| [SEV2] activity attribution always "anonymous" (routers/activity.py:38-44) | → tracked in Issue 234 (instrument load-bearing surfaces with log_event) |
| [SEV2] unauthenticated log-injection + 500s (routers/activity.py:46-59) | → tracked in Issue 229 (HTTP security-headers middleware + input hardening) |
| [SEV2] Google refresh token in query param (routers/auth.py:232-236) | → tracked in Issue 229 |
| [SEV2] link/upload IntegrityError → 500 (routers/videos.py:157-189, 250-293) | → tracked in Issue 76 (post-hardening residual SEV-2 cluster) |
| [SEV2] /clips/generate in-request LLM (routers/clips.py:100-139) | → tracked in Issue 82 (async migration wave 2) |
| [cleanup] generate endpoint empty state bug (routers/clips.py:139) | → tracked in Issue 76 |
| [cleanup] task_events untyped + get_event_loop deprecated (routers/tasks.py:117-123) | → tracked in Issue 109 (deferred design cleanups) |
| [cleanup] _HAIKU_MODEL hardcoded (routers/insights.py:456) | → tracked in Issue 221 (model-per-task decision log) |
| [cleanup] internal virality naming _compute_virality_score (routers/insights.py:118-165) | → tracked in Issue 109 |
| [cleanup] inert cache_control on analyze-performer (routers/insights.py:563-571) | → wont-fix: already logged in docs/OFF_COURSE_BUGS.md (2026-06-16); confirmed inert, benign premium only |
| [cleanup] /auth no rate limit (routers/auth.py:50-62) | → tracked in Issue 228 (per-creator pre-job quota + rate limiting) |
| [cleanup] aset_owner duplication ~12× | → tracked in Issue 109 |
