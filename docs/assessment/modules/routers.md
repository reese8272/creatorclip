# routers — assessed 2026-05-29

Slice: routers/auth.py, billing.py, clips.py, creators.py, improvement.py,
review.py, upload_intel.py, videos.py. Load-bearing focus: per-tenant isolation,
JWT-derived creator_id, Pydantic surfaces, blocking calls on the request loop,
error-message leakage, per-creator rate limiting.

## Findings

### Concurrency & scale (rubric 2 / scale axis B — sync calls on the async loop)

- [SEV1] routers/improvement.py:65 — `generate_improvement_brief(...)` is a
  **synchronous** function that calls the blocking Anthropic client
  (`_ANTHROPIC.with_options(timeout=120.0).messages.create`, confirmed at
  improvement/brief.py:72) and is invoked directly inside the
  `async def get_improvement_brief` request handler. A single in-flight brief
  pins the event loop for up to 120s, stalling every other concurrent request on
  that API worker. Worst scale defect in the module.
  | fix: do not run a 120s LLM+web_search call in the request path. Move it to a
  Celery task (return 202 + task_id and poll, mirroring `build_dna` in
  creators.py:36), OR if it must stay in-process,
  `await asyncio.to_thread(generate_improvement_brief, ...)`. Celery is preferred
  — matches the `build_dna` / `render_clip` pattern and keeps the loop free.

- [SEV1] routers/videos.py:132 — `upload_file(tmp_path, key)` is synchronous
  (worker/storage.py:45 → boto3 `upload_file` for R2, or `shutil.copy2` for local)
  and called inside `async def upload_video`. For a multi-hundred-MB source file
  this blocks the event loop for the entire R2 PUT / disk copy, starving all other
  requests on the worker.
  | fix: `await asyncio.to_thread(upload_file, tmp_path, key)`. (The chunked
  `await file.read(...)` loop at videos.py:104-115 is correctly async; only the
  storage write is blocking.)

- [SEV2] routers/auth.py:187 — `delete_prefix(prefix)` is synchronous boto3
  (paginated `list_objects_v2` + `delete_objects`, worker/storage.py:66) called
  inside `async def delete_account`. Account deletion is rare (5/hour limit) so
  blast radius is small, but a creator with thousands of objects blocks the loop
  for the full multi-page delete.
  | fix: `await asyncio.to_thread(delete_prefix, prefix)` per prefix, or move the
  purge into an idempotent Celery erasure task (re-running a prefix delete is
  naturally idempotent).

### Error handling & API surface (rubric 7)

- [SEV2] Bare `dict` return with **no `response_model`** on nearly every endpoint:
  auth.py:113/120, clips.py:38/80/105/128, creators.py:15/28/38/48/76,
  improvement.py:18, review.py:36, upload_intel.py:16, videos.py:23/49/79/152.
  CLAUDE.md and rubric 7 require a Pydantic model on every request AND response;
  only billing.py declares `response_model=` (BalanceOut/PackOut/CheckoutOut).
  Untyped dicts mean no response validation, no schema in `/docs`, and silent
  drift between handler and contract.
  | fix: declare a Pydantic `*Out` model per endpoint (`CreatorOut`, `ClipOut`,
  `VideoOut`, `FeedbackOut`, `UploadIntelOut`, `DnaOut`, `ImprovementBriefOut`) and
  set `response_model=`. `_clip_response` (clips.py:20) should return a `ClipOut`.

- [SEV2] routers/videos.py:51,81 — `youtube_video_id` arrives as a raw
  `Form(...)` string with no validation, then is interpolated into a storage key
  `source/{creator.id}/{youtube_video_id}{suffix}` (videos.py:131). A value
  containing `../` or `/` can shape the object key — within the creator's own
  prefix (creator_id still scopes it, so NOT cross-tenant), but it can escape the
  intended `source/<id>/` layout and collide/overwrite.
  | fix: validate `youtube_video_id` against the YouTube ID charset
  (`^[A-Za-z0-9_-]{11}$`) via a Pydantic model / `field_validator`; reject with 422.

### Code cleanliness & typing (rubric 6)

- [cleanup] routers/improvement.py:46 — nested `_avg(lst)` has no parameter or
  return type hint (CLAUDE.md mandates types on every signature).
  | fix: `def _avg(lst: list[float]) -> float | None:`.

- [cleanup] routers/clips.py:20 — `_clip_response(clip: Clip) -> dict` returns an
  untyped dict; once `ClipOut` exists, annotate the return as `-> ClipOut`.

### Items verified clean (no finding)

- Per-tenant isolation (rubric 3 / scale axis D): traced EVERY creator-scoped
  query — all filter by the authenticated `creator.id`: videos
  list/link/upload/status (videos.py:30,57,121,161), clips
  generate/list/get/render (clips.py:48,90,95,117,138), feedback (review.py:45,52),
  upload-intel (upload_intel.py:23), improvement metrics (improvement.py:32 —
  re-scoped after the Issue 33 leak), billing balance (ledger.py:33).
  `get_current_creator` (auth.py:31) derives creator_id from the JWT `sub`, never
  from the request body. The Stripe webhook reads `creator_id` from session
  metadata that `create_checkout_session` set server-side from the authenticated
  creator (billing/stripe_client.py:57), not from client input — no body-trust leak.
  Caveat (scale axis D, not a finding): isolation is hand-enforced by per-path
  `WHERE` / `if ... != creator.id: 404`, i.e. vigilant not structural — one
  forgotten clause is a leak. Postgres RLS with per-request `SET app.current_creator`
  is the recommended defense-in-depth backstop (Layer-2/scale level; not blocking
  since current coverage is complete).

- Rate limiting (scale axis F): `limiter._creator_key` (limiter.py:15) keys on the
  JWT `sub` for authenticated routes and only falls back to remote IP with no
  session cookie — correctly per-creator, backed by real Redis, no in-memory
  fail-open. `verify_exp=False` in the limiter decode is acceptable (used only to
  bucket the rate key; the auth dependency still rejects expired tokens). Spend
  gate (`check_positive_balance`) guards render/upload in clips.py:114 / videos.py:91.

- Token handling (rubric 3): only token read is delete_account (auth.py:156) via
  `decrypt(token_row.refresh_token_encrypted)`; POSTed to Google revoke, never
  logged. No `logger.*` line in the module emits a token, refresh token, or PII as
  secret. OAuth state uses `secrets.token_urlsafe(32)`, httponly cookie, equality
  check (auth.py:58) — CSRF covered.

- Error messages (rubric 7): no stack trace or DB error reaches the client.
  Billing and improvement-brief catch broad `Exception`, return generic 502/400,
  and log detail server-side (billing.py:101-103,118-122; improvement.py:70-72).
  Correct status codes throughout (401/402/404/409/413/422/502/503).

- Resource lifecycle (rubric 1): DB sessions via `Depends(get_session)`
  (context-managed in db.py). Upload temp file cleaned in `finally`/except on every
  path (videos.py:116-134). The one per-call client is `httpx.AsyncClient` in
  delete_account (auth.py:157) — acceptable given the 5/hour rarity and proper
  `async with` scoping.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok |
| 2 Concurrency & scale | 3 findings (2 SEV1, 1 SEV2) |
| 3 Security & compliance | ok (isolation, tokens, CSRF verified clean) |
| 4 Clip-quality | n/a (routers do not score clips) |
| 5 Anthropic SDK | n/a here (brief.py owns the call; flagged only its sync invocation) |
| 6 Cleanliness & typing | 2 cleanup |
| 7 Error handling / API | 3 findings (2 SEV2: response_model + input validation) — status codes & error masking ok |
| 8 Config & paths | ok (no new config introduced; paths absolute via storage layer) |

## Module verdict
NEEDS-WORK — no cross-tenant leak and no BLOCKER, but two SEV1 blocking calls on
the async loop (the 120s improvement-brief LLM call and the synchronous large-file
upload) will collapse p99 latency under concurrency, and nearly every response
lacks a Pydantic `response_model`.
