# routers — assessed 2026-05-31 (Wave-9 refresh)

Baseline commit: `78630c6`. Slice: `routers/{_schemas,api_keys,auth,billing,clips,creators,improvement,insights,review,tasks,upload_intel,videos}.py`.

This wave's delta in scope (Issues 104 + 106 + 108):
- Issue 104 — per-creator rate-limit key swept across every `@limiter.limit(...)`
  with `key_func=creator_key`; `insights.py` `nullif` aggregate replaced with
  `func.count().filter(...)`; temp-file leaks in `clips.py::ingest_clip` and
  `videos.py::upload_video` fixed with single outer `try/finally`; api_keys
  create/revoke now writes durable `audit_log` rows with IP/UA/request_id.
- Issue 106 — `billing.py::CheckoutRequest` gained `intent_id: UUID4`;
  `create_checkout_session` now requires + validates UUID + passes as
  Stripe Idempotency-Key.
- Issue 108 — `_QueuedOut` schemas dedup'd via `routers/_schemas.py::TaskQueuedOut`
  base (3 of 4 subclass; `BriefQueuedOut` stays standalone on LSP grounds);
  `_logging` workarounds removed; `upload_intel.py` gained module-level
  `logger`; magic numbers named.

## Findings

- [SEV2] routers/tasks.py:131-138 — owner-presence vs owner-mismatch returns
  distinct status codes (404 "Unknown task" when the owner key is absent vs
  403 "Not your task" when present-but-wrong). **Carry-forward** — still
  open. An attacker with a valid session (rate-limited at 120/min) can
  enumerate which `task_id` strings correspond to real in-flight jobs
  system-wide (rubric category 3 — disclosure of cross-tenant resource
  existence). `aget_owner` TTL also turns 404 into a "task is recent" vs
  "task is old / never existed" oracle. | fix: collapse both branches into
  a single `HTTPException(status_code=404, detail="Unknown task")`. The
  owner check becomes
  `if owner is None or owner != str(creator.id): raise 404`. The legitimate-
  but-wrong-creator path is functionally identical to the nonexistent path
  from the caller's perspective.

- [SEV2] routers/tasks.py:96 (call site of `progress.aread_since`) — the
  `Last-Event-ID` request header (line 140) is forwarded verbatim into
  `cursor`, which becomes the `last_id` argument to
  `client.xread({key: cursor})`. **Carry-forward** — still open. Redis
  Streams rejects malformed IDs (e.g. `"abc"`, `"1-2-3"`, empty-but-not-
  `"0-0"`) with `redis.exceptions.ResponseError`, which is NOT caught and
  propagates as a 500 on every reconnect from a broken / hostile client
  (rubric category 7). Also a cheap availability attack: a single malformed
  `EventSource` polyfill hammers `/tasks/{task_id}/events` with 500s and
  chews through the per-creator SSE slot (acquired BEFORE the bad XREAD,
  released only via `finally`). | fix: validate `last_event_id` against
  `^\d+-\d+$` before entering the generator; on miss, coerce to `"0-0"`
  (the documented "from beginning" sentinel) and log a warning with
  `creator_id` + `task_id` + the offending value (truncated). Add a unit
  test that hits `/tasks/X/events` with `Last-Event-ID: bogus` and asserts
  200 + start-from-zero, not 500.

- [SEV2] routers/videos.py:66-100 (`list_videos`) + routers/clips.py:112-131
  (`list_clips`) — unbounded `select(...).order_by(...)` returns the entire
  per-creator catalog in a single hop. **Carry-forward** — still open. A
  creator at the 10k-PRD-scale target streams the entire catalog through DB
  cursor → JSON serialize → response heap, all linear. The dashboard is the
  consumer; pagination is a real UX win, not speculative (rubric category 2).
  | fix: add `limit` + `cursor` query params (default `limit=50`, max 200;
  cursor-paginate on `(created_at DESC, id DESC)` — not `OFFSET`, which is
  O(N) at depth). Verify the index covers
  `(creator_id, source_uri, created_at DESC, id DESC)`; file a migration if
  not.

- [SEV2] routers/auth.py:229 — `except Exception as exc:` around the OAuth
  revocation block is over-broad and would also swallow
  `asyncio.CancelledError` if the client disconnects mid-revoke. **Carry-
  forward** — still open. The intent is "revocation is best-effort"; the
  implementation also catches programming errors silently (rubric category
  6). | fix: narrow to
  `except (httpx.HTTPError, ValueError, sqlalchemy.exc.SQLAlchemyError)
  as exc`, let `asyncio.CancelledError` propagate. Same pattern at
  routers/auth.py:241 (`delete_prefix` failure inside the deletion loop)
  — narrow to `(BotoCoreError, ClientError)`.

- [SEV2] routers/videos.py:134 — the manual-link path's `except Exception
  as exc:` swallows ALL errors (including `CancelledError` and config
  errors like a missing decrypt key) and silently records the video as
  `kind=long` with `duration_s=None`. **Carry-forward** — still open. A
  persistent OAuth/quota fault produces a stream of mis-bucketed long-form
  rows that the next catalog/analytics sync may not visit (e.g. if the
  video is private and vanishes from the catalog) (rubric category 6).
  | fix: narrow to `except (httpx.HTTPError, QuotaExhaustedError,
  ValueError) as exc`; on a config-level failure (token decrypt, missing
  setting) re-raise and let the caller see 500 — better an honest failure
  than a silently wrong row.

- [SEV2] routers/billing.py:117 + routers/billing.py:136 — `except
  Exception as exc:` catches everything (including
  `stripe.AuthenticationError`, which deserves a distinct 500 + alert vs
  the user-facing 502, and including `CancelledError`). **Carry-forward**
  — still open. Currently masks misconfigured API keys as generic "Could
  not create checkout session" (rubric category 6 + observability).
  | fix: catch `stripe.error.StripeError` plus `asyncio.TimeoutError`;
  let `CancelledError` propagate; log the exception type so misconfig vs
  Stripe-outage is distinguishable in metrics.

- [SEV2] routers/auth.py:162-165 — `/auth/logout` has neither
  `@limiter.limit(...)` nor any auth dep. Every other authenticated route
  in this module carries `key_func=creator_key`. An unauthenticated POST
  with no rate limit means a botnet can replay logout to flood the
  `Set-Cookie` response path / log volume without any throttle. The
  Wave-9 contract claim "creator_key is on EVERY authenticated route" is
  technically true only because logout has no auth dep — but the endpoint
  is otherwise undefended (rubric category 7 / scale-checklist F). | fix:
  add `@limiter.limit("60/minute", key_func=get_remote_address)` (IP-keyed
  is correct here because the logout target has no creator context to
  rate-limit against) and accept `Request` in the signature.

- [SEV2] routers/billing.py:123-188 — `/billing/webhook` accepts a raw
  request body and runs DB writes (Creator update, grant_minutes, commit)
  but has no `@limiter.limit(...)` and no IP allowlist. Stripe's signature
  verification at line 132 IS the auth gate, so an unsigned flood is
  rejected at 400 — but signature *verification itself* costs CPU (HMAC-
  SHA256 over the body) and a flood of bogus payloads can saturate one
  worker without ever reaching `construct_webhook_event`'s reject path
  being throttled (rubric category 7 / scale-checklist F). | fix: add
  `@limiter.limit("600/minute", key_func=get_remote_address)` (Stripe's
  published webhook rate is well under this; legitimate retries +
  concurrent events fit comfortably) so an unsigned flood from one IP is
  shed before HMAC.

- [SEV2] routers/improvement.py:75-82 — debounce path returns
  `task_id=row.job_id`, but `row.job_id` is only guaranteed set by the
  PREVIOUS request's `session.commit()` at line 102 of the *same* code
  path. If the prior request raced — created the row, set status=pending
  at line 87-92, committed at line 93, then crashed before the second
  commit at 102 — a subsequent debounce request reads
  `status=pending, job_id=None` and returns `task_id=None,
  stream_url=None`. The client sees a "pending" with no task to attach to,
  and no recovery path because the next request still hits the debounce
  branch (rubric category 7). | fix: in the debounce branch, if
  `row.job_id is None` and `row.requested_at` is older than a small
  staleness window (e.g. 60s), treat the row as orphaned and fall through
  to re-enqueue rather than returning a debounced response with no task
  id.

- [cleanup] routers/auth.py:131-137 — Issue 108 was supposed to remove
  ALL `import logging as _logging` workarounds, but the OAuth callback's
  `aset_owner` failure path still imports a function-local logger even
  though `logger = logging.getLogger(__name__)` is already declared at
  module scope (line 26). The other five sites (clips.py:171, clips.py:302,
  creators.py:181, creators.py:223, improvement.py:113, videos.py:275)
  all use the module-level `logger` correctly — this is the lone miss.
  | fix: replace the inline `import logging as _logging;
  _logging.getLogger(__name__).warning(...)` block with a single
  `logger.warning("auth callback aset_owner failed (Redis down?) task=%s
  err=%s", task.id, exc)`.

- [cleanup] routers/clips.py:154, 293; creators.py:164, 210;
  improvement.py:95; videos.py:266 — `import redis as _redis_pkg` is
  duplicated as a function-local import in six call sites, every one
  followed by `from worker import progress` and `from worker.tasks
  import …`. Six function-local imports just to alias `redis` as
  `_redis_pkg` (the alias is needed nowhere — there is no module-scope
  `redis` symbol to shadow). Pure DRY/KISS (rubric category 6). | fix:
  hoist `import redis` and the `worker.progress` / `worker.tasks` symbols
  to module scope; drop the `_redis_pkg` alias entirely.

- [cleanup] routers/auth.py:91-99 — `from billing.ledger import
  grant_minutes` is a function-local import inside `callback` for no
  load-order reason (`billing.ledger` is imported at module top of
  `routers/billing.py` without issue). Same pattern at
  routers/auth.py:197-200 (`from sqlalchemy import select; from crypto
  import decrypt; from models import YoutubeToken`) and
  routers/auth.py:233 (`from worker.storage import delete_prefix`).
  | fix: hoist to module top; if a circular import surfaces, document
  the cycle in a one-line comment and keep the deferred import.

- [cleanup] routers/clips.py:142, routers/clips.py:340,
  routers/review.py:49, routers/videos.py:311, routers/api_keys.py:192 —
  `session.get(Clip|Video|CreatorApiKey, id)` then post-fetch `creator_id`
  check is fetch-then-validate. Pulls one extra row over the wire when the
  entity belongs to a different creator. Not a leak (the check is present
  and correct, so rubric category 3 is OK), but a single
  `session.scalar(select(...).where(id == X, creator_id == creator.id))`
  is one query at the same cost and avoids loading the foreign row at all
  (rubric category 2). | fix: replace each `session.get(...)` with a
  scoped `session.scalar(select(...).where(...))`.

- [cleanup] routers/insights.py:77-109 (`_fetch_performers`) — re-filters
  the video ids with `Video.creator_id == creator_id` even though the ids
  came from the creator's own DNA jsonb. **Defense-in-depth, correct
  posture** — no defect, noting it as a positive. The drop-on-foreign
  pattern is the right shape for a stale/migrated DNA row carrying an id
  the creator no longer owns.

- [cleanup] routers/_schemas.py — single-class file is fine, but the
  inheritance pattern at clips.py:48
  (`class RenderQueuedOut(TaskQueuedOut): """..."""`), creators.py:41/45
  (same), and improvement.py:22 (`BriefQueuedOut(BaseModel)`, not
  subclassed) means three of four use the base purely for an OpenAPI alias
  and not for any actual divergence. Acceptable per the docstring; flag
  only because the value of the base is "named OpenAPI shapes," not type
  reuse — worth a one-line note in `_schemas.py` so the next reader
  doesn't try to add fields to `TaskQueuedOut` thinking they propagate.

- [cleanup] routers/clips.py:197-213 (`_obs_clip_youtube_id`) — synthetic
  id pattern `obs-<12 hex>` provides 48 bits of entropy. Per the birthday
  bound, collision probability hits ~50% at ~16M rows; per-creator the
  schema's UNIQUE(creator_id, youtube_video_id) means real risk is
  per-creator, not global. At 10k uploads per creator (the PRD ceiling)
  collision is astronomically unlikely. **Not a defect today.** The
  docstring already cites the math (Issue 108 improvement). Carry-forward
  only because a future migration that drops the per-creator scope would
  silently raise the collision floor; consider widening to 16 hex chars
  (still inside `String(32)`) for zero-cost future-proofing.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — Issue 104's single-outer-`try/finally` fix verified at both `clips.py::ingest_clip` (245-277) and `videos.py::upload_video` (186-241). DB sessions OK (`get_session` is an async-context-manager generator; commit/rollback in caller). `httpx.AsyncClient` for OAuth revoke is per-call inside a context manager so it closes (a module-level singleton would be cheaper — root-infra concern, not here). |
| 2 Concurrency & scale | 2 SEV2 (unbounded `/videos` + `/clips` list, carry-forward); fetch-then-validate cleanup. Stripe → `asyncio.to_thread` ✅ (billing.py:108); R2 PUT → `asyncio.to_thread` ✅ (both upload paths); ffprobe → `asyncio.to_thread` ✅ (both); account-deletion delete-prefix → `asyncio.to_thread` ✅. No remaining sync-in-async on the hot path. |
| 3 Security & compliance | 1 SEV2 (enumeration oracle on `/tasks/{id}/events`, carry-forward); 2 SEV2 (unrate-limited `/auth/logout` + `/billing/webhook`, scale-checklist F). Per-creator isolation **verified on every query** including the Wave-9 surfaces: api_keys.py:96-101, 192-193 ✅; insights.py:159, 178, 93-96 ✅; clips.py:280 (`creator_id` derived from bearer-resolved Creator, never client-supplied) ✅. `creator_id` is *never* read from a request body in any handler — sourced exclusively from `get_current_creator` / `get_current_creator_via_api_key` deps. Token handling via `decrypt()` at auth.py:206 ✅. YouTube ID validated against `^[A-Za-z0-9_-]{11}$` before storage-key interpolation ✅. RLS dual-defense via `db.py::_set_app_creator_id` after-begin listener ✅. Raw API keys returned ONLY in the create response, never logged, never retrievable thereafter ✅. **Issue 104 audit-log fix verified**: api_keys.py:138-152 (create) and 200-214 (revoke) both write durable `audit_log` rows via `append_audit` with `ip_address`, `user_agent`, and `request_id` captured into the `after`/`before` JSONB; pre-revoke state captured BEFORE mutation (line 199 reads `row.name` + `row.key_prefix` before `row.revoked_at = ...` at 216). No PII in log lines reviewed. |
| 4 Clip-quality | n/a (routers do not score). `routers/clips.py` delegates ranking to `clip_engine.ranking.generate_and_rank_clips` and returns `principle` + `reasoning` from `signals_jsonb` directly — no virality language anywhere in this slice. |
| 5 Anthropic SDK | n/a (routers do not call the LLM directly — `start_improvement_brief` and `build_dna` enqueue Celery tasks). |
| 6 Cleanliness & typing | 4 SEV2 (over-broad `except Exception` in auth.py:229+241, billing.py:117+136, videos.py:134 — all carry-forward); cleanup: lone leftover `_logging` import at auth.py:131 (Issue-108 sweep miss), `_redis_pkg` alias duplication across 6 sites, function-local import sprawl in `auth.py`, fetch-then-validate query shape. Type hints present on every signature in the slice. No TODOs/FIXMEs/`print()`. |
| 7 Error handling / API | 1 SEV2 (`Last-Event-ID` → 500 on malformed, carry-forward); 1 SEV2 (orphaned-debounce race in improvement-brief). Pydantic models on every request & response ✅. Status codes correct throughout (200/201/202/204/400/401/404/409/413/422/502/503). No stack traces or DB errors leaked to client. **Issue 106 verified**: billing.py:54 has `intent_id: UUID4` (Pydantic v4-shape validation runs before Stripe); billing.py:115 passes `str(body.intent_id)` to `create_checkout_session` as the Idempotency-Key. |
| 8 Config & paths | All paths absolute (`Path(tmp.name)` ✅). R2 keys creator-scoped + youtube-id-validated ✅. Settings (`UPLOAD_MAX_MB`, `CLIPS_PER_VIDEO_DEFAULT`, `STRIPE_SECRET_KEY`, `FREE_TRIAL_MINUTES`, `JWT_EXPIRY_MINUTES`, `ENV`) all present in `.env.example` ✅. No new env vars introduced in Wave 9. |

## Wave-9 re-verification (the contract checklist)

- **104 — per-creator rate-limit key**: 32 of 32 `@limiter.limit(...)`
  decorators across all 11 router files use `key_func=creator_key`
  (verified by `grep -nP "@limiter\.limit"`). The two unauthenticated
  routes (`/auth/login`, `/auth/callback`) and three public/webhook routes
  (`/auth/logout`, `/billing/packs`, `/billing/webhook`) carry NO limiter
  at all — see the new SEV2 entries for logout and webhook. ✅ (with two
  new SEV2 callouts on the no-auth surfaces).
- **104 — `func.count().filter(...)` insights fix**: insights.py:152-157
  correctly uses `func.count().filter(Video.kind == VideoKind.short)`
  instead of the broken `nullif(... != ..., True)` pattern; the new shape
  is the ANSI SQL:2003 FILTER clause and counts honestly. ✅
- **104 — temp-file leak fixes**: clips.py::ingest_clip 253-277 and
  videos.py::upload_video 194-241 both wrap the entire post-
  NamedTemporaryFile block in a single `try/finally
  tmp_path.unlink(missing_ok=True)`. The cleanup runs on OSError and
  CancelledError as advertised — verified by inspection. ✅
- **104 — api_keys audit rows**: api_keys.py:138-152 (create) and 200-214
  (revoke) both write durable `audit_log` rows via `append_audit` with
  `ip_address`, `user_agent`, and `request_id` captured into the `after`/
  `before` JSONB. Pre-revoke state captured BEFORE mutation. ✅
- **106 — billing intent_id**: billing.py:54 has `intent_id: UUID4`
  (Pydantic v4-shape validation runs before Stripe). billing.py:115
  passes `str(body.intent_id)` to `create_checkout_session` as the
  Idempotency-Key — chain confirmed. ✅
- **108 — `_QueuedOut` dedup via `routers/_schemas.py`**:
  `RenderQueuedOut` (clips.py:48), `BuildQueuedOut` (creators.py:41),
  `CatalogSyncQueuedOut` (creators.py:45) all subclass `TaskQueuedOut`.
  `BriefQueuedOut` (improvement.py:22) stays standalone with
  `task_id: str | None` — the comment at 18-21 correctly justifies this
  on LSP grounds (debounce path returns no task_id). ✅
- **108 — `_logging` workarounds removed**: ONE leftover at auth.py:131-137
  (see cleanup above). The other five sites all use the module-level
  `logger` correctly. ⚠️ (partial — one miss).
- **108 — `upload_intel.py` module-level logger**: upload_intel.py:14
  has `logger = logging.getLogger(__name__)` — confirmed. ✅
- **108 — magic numbers named**: tasks.py:40-50 names
  `MAX_CONCURRENT_SSE_PER_CREATOR=3`, `KEEPALIVE_INTERVAL_S=12.0`,
  `MAX_STREAM_LIFETIME_S=600.0`. videos.py:183 names `chunk_size = 1 *
  1024 * 1024` with a comment; clips.py:242 mirrors it. ✅

## Scale-checklist hits

- **D (Tenant isolation)**: per-creator isolation enforced at *both*
  application layer (every SELECT has `creator_id == creator.id`; every
  `session.get(Model, id)` is followed by a `row.creator_id ==
  creator.id` check) AND database layer (RLS policies in alembic 0010
  + the `_set_app_creator_id` after-begin listener in db.py:119-148
  emits `set_config('app.creator_id', :cid, true)` per transaction).
  `creator_id` is **never** read from a request body in any handler —
  sourced exclusively from `get_current_creator` /
  `get_current_creator_via_api_key` deps. ✅
- **F (Rate limit / quota)**: per-creator `key_func=creator_key` is
  universal on authenticated routes (32/32). Two SEV2 gaps on
  unauthenticated routes (`/auth/logout`, `/billing/webhook`). Quota
  enforcement (cost control, not abuse control) lives in
  `billing.ledger.check_positive_balance` +
  `check_balance_for_minutes`, called before the expensive paths in
  clips.py:146 (render), clips.py:239 (ingest), videos.py:179 + 234
  (upload). ✅ (modulo the two SEV2s above).

## Carry-forward status (re-checked against `78630c6`)

- `routers/tasks.py:131-138` — 404/403 enumeration oracle: **still open**
  (SEV2)
- `routers/tasks.py:140` — unvalidated `Last-Event-ID` → 500 on
  malformed: **still open** (SEV2)
- `routers/videos.py:66-100` + `routers/clips.py:112-131` — unbounded
  list endpoints: **still open** (SEV2)
- `routers/auth.py:229, 241` + `routers/billing.py:117, 136` +
  `routers/videos.py:134` — over-broad `except Exception`: **still open**
  (SEV2)
- prior SEV2 (`insights.py` `nullif` aggregate): **CLOSED by Issue 104**
  ✅
- prior SEV2 (temp-file leak in `videos.py::upload_video` and
  `clips.py::ingest_clip`): **CLOSED by Issue 104** ✅
- prior SEV2 (rate-limit key not per-creator on `/clips/ingest` and
  others): **CLOSED by Issue 104** ✅
- prior SEV2 (missing audit-log rows on api-key create/revoke): **CLOSED
  by Issue 104** ✅
- prior SEV2 (duplicated `*QueuedOut` schemas): **CLOSED by Issue 108**
  ✅
- prior cleanup (fetch-then-validate, function-local imports, `_logging`
  alias, missing logger in upload_intel): **mostly CLOSED by Issue 108**
  (one `_logging` leftover at auth.py:131 remains).

## Module verdict

NEEDS-WORK — no BLOCKERs and no SEV1. Wave-9 (Issues 104 + 106 + 108)
correctly closed five prior SEV2s (broken `nullif` aggregate, temp-file
leaks in both upload paths, rate-limit-key universality, missing audit-log
rows on api-key mutations) and cleaned up the duplicated `*QueuedOut`
schemas via `routers/_schemas.py`. Per-creator isolation is structurally
sound (RLS via `session.info["creator_id"]` plus explicit
`WHERE creator.id`) on every endpoint in the slice, and `creator_id` is
never derived from a request body. Eight SEV2s remain — six are carry-
forward (404/403 oracle, `Last-Event-ID` 500, unbounded list endpoints,
four over-broad `except Exception` blocks) and two are new Wave-9
callouts (unrate-limited `/auth/logout` + `/billing/webhook`) plus one
new SEV2 (orphaned-debounce race in improvement-brief). Lone Wave-9
cleanup miss: one stale `import logging as _logging` at auth.py:131 that
the Issue-108 sweep didn't catch.
