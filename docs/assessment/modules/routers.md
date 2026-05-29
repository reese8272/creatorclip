# routers — assessed 2026-05-29

Slice: routers/auth.py, billing.py, clips.py, creators.py, improvement.py,
review.py, upload_intel.py, videos.py. Cross-referenced auth.py (root),
limiter.py, worker/storage.py, improvement/brief.py, models.py, main.py.

## Findings

### Per-tenant isolation (axis D) — verified, no leaks
Every creator-scoped query was traced to its `WHERE`/ownership check. All
`creator_id` values are derived from the session JWT via
`get_current_creator` (auth.py:31), never read from the request body. Confirmed
clean:
- videos.py:30, 58, 122, 161 — all filter `Video.creator_id == creator.id`.
- clips.py:48, 90, 95, 117, 138 — video + clip ownership enforced (`session.get`
  then `!= creator.id` → 404).
- review.py:45 — clip ownership checked before writing feedback;
  `ClipFeedback.creator_id` set from session (review.py:51).
- improvement.py:32 — metrics joined and filtered to `Video.creator_id`
  (the prior Issue-33 leak is fixed).
- upload_intel.py:24, creators.py (delegates to `get_active`/`check_data_gate`
  with `creator.id`), billing.py:63 (`get_balance(creator.id)`).
- auth.py:153 — token lookup filtered by `creator_id`; deletion cascades.
No missing `WHERE creator_id` found. No f-string/`%` SQL; all parameterized
through SQLAlchemy core/ORM.

### Concurrency & scale (rubric 2 / axis B)
- [SEV1] improvement.py:65 — `generate_improvement_brief(...)` is a **synchronous,
  60–120s blocking** Anthropic web_search call (improvement/brief.py:72 uses the
  sync SDK with `timeout=120.0`) invoked directly inside the `async def`
  handler. This stalls the single event loop for up to 2 minutes per request,
  collapsing p99 for every other concurrent request on that worker | fix: move
  brief generation to a Celery task and return 202 + task_id (matches the
  `build_dna` / `render` pattern already used in creators.py:42 and
  clips.py:124), or at minimum wrap in `await asyncio.to_thread(generate_improvement_brief, ...)`.
  Celery is the correct choice — this is long-running LLM work that belongs off
  the request path per scale-checklist B.
- [SEV1] videos.py:132 — `upload_file(tmp_path, key)` is a **blocking boto3
  `upload_file`** (worker/storage.py:48) of an up-to-500 MB object run on the
  event loop inside `upload_video`; the whole multi-second/minute R2 PUT blocks
  the loop | fix: `await asyncio.to_thread(upload_file, tmp_path, key)`, or hand
  the temp object off to the Celery pipeline for upload. (Note: the chunked
  `await file.read()` streaming-to-disk loop above it is correct and bounded.)
- [SEV2] auth.py:187 — `delete_prefix(prefix)` (blocking boto3 list+delete
  paginator, worker/storage.py:71–76) runs synchronously in the async
  `delete_account` handler; for a creator with many objects this blocks the loop
  for the full purge | fix: `await asyncio.to_thread(delete_prefix, prefix)` or
  enqueue a purge task (the endpoint is rate-limited 5/hour so blast radius is
  bounded, hence SEV2 not SEV1).
- [SEV2] videos.py:29-32, clips.py:93-98, upload_intel.py:22-25,
  improvement.py:29-35 — list endpoints do `select(...)` then materialize the
  full result with `list(result.scalars())` and no `LIMIT`. A creator with
  thousands of videos/clips returns an unbounded payload and loads it all into
  memory | fix: add pagination (`limit`/`offset` query params, default e.g.
  50, hard cap 200) on list_videos and list_clips; AudienceActivity is naturally
  bounded (168 hour-buckets) so upload_intel is acceptable, but list_videos and
  list_clips are unbounded per scale-checklist "no unbounded fetchall".

### Error handling & API surface (rubric 7)
- [SEV2] Response models missing on nearly every endpoint. Only billing.py
  declares `response_model=` (BalanceOut/PackOut/CheckoutOut). All of
  auth.py /me, creators.py (all 5), videos.py (all 5), clips.py (all 4),
  review.py, upload_intel.py, improvement.py return bare `dict`. Rubric 7
  requires a Pydantic model on every request AND response; bare dicts mean no
  output validation, no schema in /docs, and silent shape drift | fix: define
  `*Out` BaseModels (e.g. `ClipOut`, `VideoOut`, `DnaProfileOut`,
  `FeedbackOut`, `MeOut`) and set `response_model=` on each route; `_clip_response`
  (clips.py:20) should return a `ClipOut`.
- [SEV2] billing.py:144-167 — webhook idempotency is a check-then-insert
  (select on stripe_session_id, then grant). `MinutePack.stripe_session_id` is
  `unique=True` (models.py:446-447), so a concurrent duplicate Stripe delivery is
  prevented from double-granting — but the resulting `IntegrityError` on
  `session.commit()` (billing.py:168) is uncaught and surfaces as a 500 to
  Stripe, which then **retries**, looping | fix: wrap the grant+commit and catch
  `IntegrityError` → return `{"status": "already_fulfilled"}` (200). The unique
  index makes the data safe; the missing catch makes the response wrong.
- [SEV2] review.py:30-31 — `coerce_action` validator calls `FeedbackAction(v)`
  which raises a bare `ValueError` on an invalid action; Pydantic surfaces this
  as 422 (acceptable) but the redundant validator is unnecessary since the field
  is already typed `FeedbackAction` and Pydantic coerces enums natively | fix:
  drop the `field_validator`; let Pydantic's native enum coercion return 422.
- [cleanup] clips.py:38 `generate_clips`, videos.py status/list, etc. return
  `-> dict`; once response_models are added these annotations should become the
  model type. HTTP status codes themselves are correct throughout (404 on
  not-found/cross-tenant, 409 on conflict, 400 on bad input, 413 on oversize,
  502/503 on upstream, 401 on auth, 201/202/204 where appropriate).

### Rate limiting & quota (axis F)
- [SEV2] limiter.py:15-28 — `_creator_key` decodes the JWT with
  `options={"verify_exp": False}` and **no signature failure distinction**: it
  trusts `payload["sub"]` from any token that decodes. Because `jwt.decode`
  still verifies the HS256 signature against `JWT_SECRET_KEY`, an attacker
  cannot forge a sub — but an *expired* token is accepted for keying, so a
  logged-out/expired session is still rate-limited per-creator (minor) rather
  than per-IP. Acceptable, but the per-creator limit is keyed even for tokens
  the auth layer would reject. Confirm this is intended | fix (optional):
  re-verify exp so abandoned tokens fall back to IP keying; low risk, hence
  SEV2-bordering-cleanup.
- [SEV1] No per-creator usage **quota** check before the LLM/render jobs beyond
  `check_positive_balance` (clips.py:114 render, videos.py:91 upload).
  improvement.py (the 60-120s Claude web_search call) has only a 10/hour rate
  limit and **no balance/quota gate** — a creator with zero balance can still
  burn LLM cost on improvement briefs | fix: add a quota/balance check before
  the brief call consistent with CLAUDE.md pre-launch "per-creator usage quotas
  before each LLM/render job". generate_clips (clips.py:38) likewise runs the
  ranking LLM with only a rate limit and no balance gate.

### Security & compliance (rubric 3)
- Tokens: auth.py:156 reads `refresh_token_encrypted` strictly via
  `decrypt()`; never logged. Revocation log lines (auth.py:168-180) log only
  `creator_id` and the error code, no token. Clean.
- Logging: grepped all `logger.*` calls — auth.py, billing.py, clips.py,
  improvement.py, review.py log only ids/counts/actions. No token, no email in
  logs. (auth.py:200 puts email in the audit `before` JSON, which is an audit
  record by design, not a log line — acceptable per COMPLIANCE.)
- [cleanup] No virality promise found in any response string or docstring;
  honesty constraint present in auth.py:121 and main.py:48. Clean.
- Unhandled exceptions: no custom 500 handler, so FastAPI's default returns a
  generic body with no stack trace; `/docs` disabled when `ENV != development`
  (main.py:51). Safe.

### Resource lifecycle (rubric 1)
- DB sessions via `Depends(get_session)` context-managed dependency on every
  endpoint — guaranteed close. Clean.
- videos.py:98-134 — temp file lifecycle is correct: created with
  `delete=False`, cleaned in the 413 path (line 117), the duplicate-video path
  (line 127), and the upload `finally` (line 134). No leak on any traced path.
- httpx client in auth.py:157 is a per-call `async with` with a 10s timeout —
  acceptable for the one-shot revocation call, though scale-checklist A/B prefer
  a module-level singleton. [cleanup] auth.py:157 — consider a shared
  `httpx.AsyncClient` singleton if revocation volume grows.

### Config & paths (rubric 8)
- All paths absolute / derived (videos.py uses `tempfile`, `Path`; storage keys
  are prefixed by creator_id). STRIPE_*, FREE_TRIAL_MINUTES, UPLOAD_MAX_MB,
  JWT_SECRET_KEY, REDIS_URL, ALLOWED_ORIGINS, ENV all present in `.env.example`
  with descriptions. Clean.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok (1 cleanup: httpx singleton) |
| 2 Concurrency & scale | 2 SEV1 (blocking LLM + boto3 upload on loop), 2 SEV2 (blocking purge, unbounded lists) |
| 3 Security & compliance | ok — isolation verified clean, no token/PII in logs, no virality promise |
| 4 Clip-quality | n/a (routers, not a clip-scoring module) |
| 5 Anthropic SDK | n/a in slice — the brief call lives in improvement/brief.py (assessed by that module); routers only invokes it (flagged under cat 2) |
| 6 Cleanliness & typing | 2 cleanup (dict return types, redundant validator) |
| 7 Error handling / API | 3 SEV2 (missing response_models, webhook IntegrityError 500→Stripe retry loop, redundant validator) |
| 8 Config & paths | ok |

## Module verdict
NEEDS-WORK — no cross-tenant leak and no BLOCKER (isolation is solid and
JWT-derived throughout), but two SEV1 blocking calls on the event loop
(improvement brief 60–120s, 500 MB boto3 upload) and a missing per-creator
quota gate on LLM endpoints must be fixed before this module is production-ready
at concurrency; response_models and the webhook IntegrityError handler are the
next tier.
