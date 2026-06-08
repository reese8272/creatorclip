# routers — assessed 2026-06-07

Scope: every file under `routers/` (clips, creators, videos, insights, auth, billing,
analysis, thumbnails, titles, api_keys, improvement, activity, review, tasks,
upload_intel, _schemas). HEAD `7af18b2`. Focus per orchestrator: rubric categories
3 (per-creator isolation), 7 (Pydantic / status / safe errors), 2 (async hygiene),
1 (DB session lifecycle).

## Findings

### Per-creator isolation (Category 3)

Re-traced every endpoint that touches a creator-scoped table. Every new endpoint
shipped this session (`/clean-preview`, `/clean`, `/clean/confirm`, `/cuts`,
`/transcript`, `/hook-analysis`, `/chapters`) gates on `clip.creator_id == creator.id`
or `Video.creator_id == creator.id` before any read of children. No cross-tenant
leak found. The session-info / RLS guard set by `get_current_creator_via_api_key`
(see `clips.py:619-622`) is consistent with the cookie-auth surface.

- [cleanup] routers/clips.py:469-471 — `clip_transcript` correctly checks
  `clip.creator_id != creator.id`, then loads `Transcript` by `clip.video_id`.
  Transitive isolation is fine but worth a one-line comment so a future reader
  doesn't worry about it | fix: add `# transitively scoped via the clip-ownership
  check above` next to the `session.get(Transcript, clip.video_id)` line.

### API surface — Pydantic / status codes / safe errors (Category 7)

- [SEV2] routers/clips.py:540-542 — `submit_cuts` raises
  `HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)})`.
  `str(exc)` on `CutValidationError` may surface internal field names / values
  not intended for the wire (e.g. clip duration arithmetic). Low risk because
  these are validator messages, but they bypass the "no internal detail to
  client" rule | fix: map `exc.code` → a fixed short string table (e.g.
  `_USER_CUT_ERROR_MESSAGES[exc.code]`) and drop `str(exc)` from the body;
  log the full `exc` server-side with `logger.info` keyed by `clip_id` for
  triage.
- [SEV2] routers/analysis.py:160-163 — `POST /me/videos/{video_id}/hook-analysis`
  is declared with no `status_code=` but returns BOTH 200 (no data) and 202
  (queued) via raw `JSONResponse`. OpenAPI documents whatever the framework
  default is (200), so a generated client treats 202 as a deviation | fix: add
  `responses={200: {...}, 202: {...}}` to the decorator so both shapes are
  documented; or split into two endpoints if the OpenAPI surface matters more
  than the single round-trip.
- [SEV2] routers/billing.py:118-120 — `checkout` catches `Exception` and surfaces
  HTTP 502. The `Stripe checkout creation failed: %s` log emits `exc` directly;
  Stripe error messages can echo back the customer email or api-key prefix.
  Combined with the `creator.stripe_customer_id` argument that immediately
  precedes it on line 113, that is enough to put PII near an error log line |
  fix: log `type(exc).__name__` and `getattr(exc, "code", None)` instead of the
  raw exception; keep the user-facing 502 detail unchanged.
- [SEV2] routers/billing.py:46-51 — `CheckoutRequest.success_url` /
  `cancel_url` are unvalidated `str`. A creator can supply any URL; Stripe
  enforces https in live mode but in dev/test these flow through unchecked.
  An attacker who phishes a creator's session could redirect post-checkout to
  an attacker-controlled domain | fix: validate against an `ALLOWED_REDIRECT_HOST`
  allowlist in pydantic-settings, or use `pydantic.HttpUrl` + a host-suffix
  check matching the deploy domain.
- [SEV2] routers/clips.py:600-712 — `ingest_clip` uses
  `@limiter.limit("20/hour", key_func=creator_key)` but the auth dependency is
  `get_current_creator_via_api_key`, not the cookie-auth `get_current_creator`
  that `creator_key` was originally written against. Easy to silently break
  the limiter on the bearer-auth surface if `creator_key` ever inspects
  cookies rather than the resolved `request.state.creator_id` | fix: confirm
  `creator_key` resolves the creator id from a path that both auth surfaces
  populate, and add a unit test that hits `/clips/ingest` 21× with a Bearer
  token and asserts a 429 on the 21st call. (needs-runtime-confirmation)
- [cleanup] routers/auth.py:69 — `HTTPException(400, f"Google OAuth error: {error}")`
  echoes the raw OAuth `error` query parameter to the client. Google's error
  codes are public but echoing them invites injection in error-log replay |
  fix: map to a fixed short message ("Google OAuth failed; please try again")
  and log the raw `error` server-side.
- [cleanup] routers/analysis.py:84-85 vs 256 — `"Could not extract a valid
  YouTube video ID from the URL."` and `"Invalid video_id format."` are two
  different phrasings for the same 422 condition | fix: extract a single
  `_INVALID_VIDEO_ID_DETAIL` constant.

### Concurrency & scale (Category 2)

- [SEV2] routers/clips.py:196, 360, 550 + routers/videos.py:278 +
  routers/review.py:87 + ~10 other call sites — `task.delay(...)` and
  `start_pipeline(...)` are sync Celery producers that block on Redis/AMQP I/O
  from inside `async def`. Each call is ~1-5 ms in healthy state but spikes
  under Redis pressure, and the blocking happens on the event loop thread —
  serialising every request behind it. At hundreds of users this is the next
  p99 latency cliff after the Stripe one Wave-3 Fix C already addressed
  (billing.py:109) | fix: wrap producer calls in
  `await asyncio.to_thread(task.delay, ...)` — same recipe as the
  Stripe / Voyage / WhisperX offload. Pair with a single helper
  `async def _enqueue(t, *a, **kw)` to avoid the boilerplate.
  (needs-runtime-confirmation under real Redis load.)
- [SEV2] routers/insights.py:563-569 — `_ANTHROPIC.messages.create` is wrapped
  in `asyncio.to_thread` (good), but the Anthropic singleton at line 450-454
  is constructed with `timeout=120s`. A stuck connection blocks one threadpool
  worker for two minutes. Default FastAPI threadpool is 40 workers, so 40
  in-flight stuck Haiku calls saturates every other `to_thread` caller (R2
  PUT, ffprobe, Stripe, transcription) | fix: add an asyncio-level wait
  timeout: `await asyncio.wait_for(asyncio.to_thread(_ANTHROPIC.messages.create,
  ...), timeout=60)` so a stuck Anthropic call doesn't pin a worker for the
  full 120 s.
- [SEV2] routers/thumbnails.py:23-35 — `_aio_redis` lazy singleton uses a
  module-level `global` with no lock. FastAPI single-process / single-loop
  guarantees this never actually races today, but it's fragile under gunicorn
  worker reload or any future thread-pool refactor; the loser becomes
  unreachable garbage | fix: build the singleton at module import (same as
  `insights.py:_ANTHROPIC`) or use `functools.cache` on the factory.
- [cleanup] ~30 `import` statements live inside `async def` bodies across the
  routers tree (clips.py:191, 263, 355, 379, 522, 544, 677, 691; auth.py:117;
  improvement.py:142; creators.py:164, 209; analysis.py:109, 202, 275;
  insights.py:550; thumbnails.py:190; titles.py:71; videos.py:263; etc.). Some
  are intentional Celery-import-cycle breakers; most are not | fix: keep the
  3-4 sites that genuinely need lazy import (Celery worker tasks) with a
  one-line comment explaining why, lift the rest to module-level.

### Resource lifecycle (Category 1)

- DB sessions: every endpoint uses `session: AsyncSession = Depends(get_session)`;
  `db.py:167-169` is a proper `async with AsyncSessionLocal() as session: yield`
  generator — guaranteed close on exception. **clean.**
- Temp files: `routers/videos.py:191-238` and `routers/clips.py:637-661` both
  wrap the entire post-`NamedTemporaryFile` block in a single `try/finally`
  with `tmp_path.unlink(missing_ok=True)`. **clean** — Issue 104 fix is intact.
- External clients: `_ANTHROPIC` in `insights.py:450` is module-level (Issue 123
  fix); `_aio_redis` in `thumbnails.py:23` is module-level (lazy but global);
  Stripe is configured globally in `billing/stripe_client.py`. **clean.**
- routers/auth.py:206-210 — `async with httpx.AsyncClient(timeout=10)` builds
  a fresh client per `delete_account` call. The endpoint is rate-limited to
  `5/hour` per creator so this is genuinely low-frequency. **leave as-is.**

### Security & compliance (Category 3, secondary)

- [SEV2] routers/activity.py:42-44 — `record_activity` swallows ANY exception
  from `get_current_creator` with bare `except Exception: pass`. Public
  endpoint, intentional anonymous fallback. But a bare `except Exception` also
  masks code bugs (DB pool exhaustion would be logged as a normal anonymous
  event) | fix: narrow to `except HTTPException: pass` so only the expected
  401/403 paths are swallowed; let everything else surface for observability.
- routers/auth.py:228 — bare `except Exception` around OAuth revocation. Best-
  effort revocation is the right pattern here; leave as-is. **noted.**
- routers/auth.py:195-211 — OAuth token decrypt → revoke flow correctly uses
  `decrypt(token_row.refresh_token_encrypted)` and never logs the plaintext.
  The revocation HTTP response status is logged but not the token. **clean.**
- routers/api_keys.py:124-171 — `create_api_key` returns the raw key once,
  stores SHA-256 hash + `key_prefix`. Durable audit row written. **clean.**
- routers/insights.py:469-475 — `_build_analysis_prompt` ends with "Do not
  promise virality or make guarantees." Honesty constraint propagated to
  every Haiku call. **clean.**

### Code cleanliness & typing (Category 6)

- [cleanup] 12 copies of the same 8-line `aset_owner` + `stream_url` + redis-
  blip-log block: clips.py:204-212, 362-370, 552-560, 683-691; auth.py:128-135;
  improvement.py:156-165; creators.py:177-185, 219-227; analysis.py:119-127,
  209-214, 282-287; thumbnails.py:198-206; titles.py:79-87; videos.py:268-276
  | fix: extract `async def stamp_owner_or_warn(task_id: str, creator_id: str,
  *, label: str) -> str | None` into `routers/_helpers.py` and return
  `stream_url` or `None`.
- [cleanup] routers/clips.py:269-302 vs 475-491 — `_clip_clean_cuts` and the
  inline loop in `clip_transcript` both walk
  `transcript.segments_jsonb["segments"][i]["words"]` and window by
  `clip_origin_s` / `clip.end_s`. Same window logic, twice | fix: extract
  `_iter_clip_relative_words(transcript, clip) -> Iterator[dict]` and have
  both endpoints consume it.
- [cleanup] routers/improvement.py:22-27 — `BriefQueuedOut` deliberately does
  NOT subclass `TaskQueuedOut` (docstring explains: `task_id: str | None` vs
  `str`). Reasonable LSP guard. The same comment should appear on
  `analysis.py:147-157` `HookAnalysisOut`, which has the same `task_id: str |
  None` shape and no explanation | fix: add a one-line comment.
- [cleanup] routers/clips.py:69, 117, 257, 281, 533, 549 — helper functions
  return `-> dict` where the matching Pydantic model already exists; FastAPI
  revalidates at runtime so this is correct, but typing `-> ClipOut` etc.
  buys static guarantees. Low payoff for high churn — leave unless a typed
  sweep happens.

### Anthropic SDK usage (Category 5)

- routers/insights.py:553-562 — `_system` uses
  `cache_control: {"type": "ephemeral"}` on the static system prefix. Token
  usage logged with `cache_read_input_tokens` on line 571. `max_tokens=256`
  set. **clean.**
- Other Anthropic calls in the routers slice route through Celery tasks (worker
  module owns them) so are out of scope here.

### Config & paths (Category 8)

- All paths in temp-file handling use `pathlib.Path` + `tempfile.NamedTemporaryFile`
  (clips.py:629, videos.py:183) — absolute paths returned by `tmp.name`.
  **clean.**
- New `SILENCE_REMOVAL_THRESHOLD_MS`, `SILENCE_TAIL_MS`,
  `FILLER_TIER2_FLANK_GAP_MS`, `FILLER_TIER2_MAX_DURATION_MS` (clips.py:292-295)
  consumed at request time. **needs-runtime-confirmation** that they're
  present in `.env.example` with descriptions (out of slice — `config.py`
  belongs to `_root_infra`).

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — 0 findings |
| 2 Concurrency & scale | NEEDS-WORK — 3 SEV2 (sync Celery producer in async, Haiku threadpool timeout, lazy redis singleton) |
| 3 Security & compliance | ok on per-creator isolation; 1 SEV2 on billing PII-near-log; 1 SEV2 on bare `except Exception` in activity |
| 4 Clip-quality | n/a (not a clip-engine module) |
| 5 Anthropic SDK | ok — caching + token logging in place |
| 6 Cleanliness & typing | NEEDS-WORK — 12-copy DRY violation (aset_owner block), duplicated word-window walk |
| 7 Error handling / API | NEEDS-WORK — 5 SEV2 (cut validation detail, dual-status hook endpoint, Stripe exception log, unvalidated redirect URLs, slowapi+API-key) |
| 8 Config & paths | ok pending config.py confirmation outside slice |

## Module verdict

**NEEDS-WORK** — no BLOCKER, no SEV1. Per-creator isolation is intact on every
new endpoint (the highest-severity defect class is clean). The cluster of SEV2
findings is two themes: (a) blocking Celery enqueues inside async endpoints
that will show up as a p99 cliff at hundreds of users, and (b) a handful of
API-surface tightening opportunities (cut-error detail, dual-status hook
endpoint, Stripe error logging, redirect-URL allowlist). Single highest-value
fix is the `asyncio.to_thread` wrap on the ~15 `task.delay` / `start_pipeline`
sites — mechanical, large blast radius.
