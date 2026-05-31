# routers — assessed 2026-05-31

Baseline commit: `78630c6`. Slice: `routers/{api_keys,auth,billing,clips,creators,improvement,insights,review,tasks,upload_intel,videos}.py`.

This wave's delta in scope:
- NEW: `routers/api_keys.py` (Issue 95 — GET/POST/DELETE `/creators/me/api-keys`)
- NEW: `routers/insights.py` (Issue 93 — `GET /creators/me/insights`)
- NEW: `routers/clips.py::ingest_clip` (Issue 95 — `POST /clips/ingest` via bearer/api-key auth)
- CHANGED: every `aset_owner` call site now wrapped in `try/except redis.RedisError`
  (Wave-5 Fix 1 closed the prior SEV1 across `sync_catalog`, `build_dna`,
  `render_clip`, and `/clips/ingest` is born wrapped).

## Findings

- [SEV2] routers/tasks.py:131-138 — owner-presence vs owner-mismatch returns
  distinct status codes (404 "Unknown task" when the owner key is absent vs 403
  "Not your task" when present-but-wrong). **Carry-forward** — unchanged since
  the prior wave. An attacker with a valid session (rate-limited at 120/min)
  can enumerate which `task_id` strings correspond to real in-flight jobs
  system-wide (rubric category 3 — disclosure of cross-tenant resource
  existence). `aget_owner` TTL also turns 404 into a "task is recent" vs
  "task is old / never existed" oracle. | fix: collapse both branches into a
  single `HTTPException(status_code=404, detail="Unknown task")`. The owner
  check becomes `if owner is None or owner != str(creator.id): raise 404`.
  The legitimate-but-wrong-creator path is functionally identical to the
  nonexistent path from the caller's perspective.

- [SEV2] routers/tasks.py:96 (call site of `progress.aread_since`) — the
  `Last-Event-ID` request header (line 140) is forwarded verbatim into
  `cursor`, which becomes the `last_id` argument to
  `client.xread({key: cursor})`. **Carry-forward** — unchanged. Redis Streams
  rejects malformed IDs (e.g. `"abc"`, `"1-2-3"`, empty-but-not-`"0-0"`) with
  `redis.exceptions.ResponseError`, which is NOT caught and propagates as a
  500 on every reconnect from a broken / hostile client (rubric category 7).
  Also a cheap availability attack: a single malformed `EventSource` polyfill
  hammers `/tasks/{task_id}/events` with 500s and chews through the per-creator
  SSE slot (acquired BEFORE the bad XREAD, released only via `finally`). | fix:
  validate `last_event_id` against `^\d+-\d+$` before entering the generator;
  on miss, coerce to `"0-0"` (the documented "from beginning" sentinel) and
  log a warning with `creator_id` + `task_id` + the offending value
  (truncated). Add a unit test that hits `/tasks/X/events` with
  `Last-Event-ID: bogus` and asserts 200 + start-from-zero, not 500.

- [SEV2] routers/videos.py:63-97 (`list_videos`) + routers/clips.py:115-134
  (`list_clips`) — unbounded `select(...).order_by(...)` returns the entire
  per-creator catalog in a single hop. **Carry-forward** — unchanged. A
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
  forward** — unchanged. The intent is "revocation is best-effort"; the
  implementation also catches programming errors silently (rubric category 6).
  | fix: narrow to
  `except (httpx.HTTPError, ValueError, sqlalchemy.exc.SQLAlchemyError) as exc`,
  let `asyncio.CancelledError` propagate. Same pattern at routers/auth.py:241
  (`delete_prefix` failure inside the deletion loop) — narrow to
  `(BotoCoreError, ClientError)`.

- [SEV2] routers/videos.py:131 — the manual-link path's `except Exception as
  exc:` swallows ALL errors (including `CancelledError` and config errors like
  a missing decrypt key) and silently records the video as `kind=long` with
  `duration_s=None`. **Carry-forward / re-flagged.** A persistent OAuth/quota
  fault produces a stream of mis-bucketed long-form rows that the next
  catalog/analytics sync may not visit (e.g. if the video is private and
  vanishes from the catalog) (rubric category 6). | fix: narrow to
  `except (httpx.HTTPError, QuotaExhaustedError, ValueError) as exc`; on a
  config-level failure (token decrypt, missing setting) re-raise and let the
  caller see 500 — better an honest failure than a silently wrong row.

- [SEV2] routers/billing.py:109 + routers/billing.py:128 — `except Exception
  as exc:` catches everything (including `stripe.AuthenticationError`, which
  deserves a distinct 500 + alert vs the user-facing 502, and including
  `CancelledError`). **Carry-forward** — unchanged. Currently masks
  misconfigured API keys as generic "Could not create checkout session"
  (rubric category 6 + observability). | fix: catch `stripe.error.StripeError`
  plus `asyncio.TimeoutError`; let `CancelledError` propagate; log the
  exception type so misconfig vs Stripe-outage is distinguishable in metrics.

- [SEV2] routers/clips.py:213-329 (`ingest_clip`) — **NEW Wave-8 surface;
  temp-file leak on non-HTTPException error path.** The bearer-auth ingest
  endpoint mirrors `upload_video`'s shape including its temp-file lifecycle
  bug: `tempfile.NamedTemporaryFile(... delete=False)` + manual `unlink`
  only in the `except HTTPException` arm (line 259-261). If a non-HTTP
  exception fires inside the read loop (`OSError` on disk full,
  `CancelledError` on client disconnect mid-upload) or during the post-loop
  `check_balance_for_minutes` call, the temp file leaks (rubric category 1).
  At scale this is unbounded disk consumption on the API replica. | fix:
  use the `try/finally: tmp_path.unlink(missing_ok=True)` shape from the R2
  PUT block at line 274-278 around the ENTIRE post-NamedTemporaryFile
  sequence (read loop + ffprobe + balance check + upload). Same fix applies
  to routers/videos.py:184-204 (still open from the prior wave).

- [SEV2] routers/clips.py:213-329 (`ingest_clip`) — **NEW surface;
  rate-limiter key likely defaults to IP, not creator.** The endpoint is
  decorated `@limiter.limit("20/hour")` with no explicit `key_func` override,
  which means slowapi's default key (typically the remote address) is used
  even though authentication is via API key. For OBS-style ingest, a single
  creator behind CGNAT will share the budget with unrelated creators on the
  same NAT, and a single creator with many keys can each consume the full
  20/h (rubric category 6 / scale-checklist F — "Confirm limits are
  per-creator, not per-IP, for authenticated routes"). | fix: hoist the
  per-creator key into a shared helper that resolves the authenticated
  creator id from either the session cookie OR the bearer auth dependency
  cache, and apply it as `@limiter.limit("20/hour", key_func=creator_key)`
  to every authenticated route in this slice. The
  `get_current_creator_via_api_key` dependency already attaches `creator.id`
  to `session.info`; surface it on `request.state` for the limiter to read.
  Confirm the same for `routers/clips.py:render_clip`, `videos.py:upload_video`,
  `creators.py:build_dna`, etc. as a uniform sweep.

- [SEV2] routers/api_keys.py:163-167 (`revoke_api_key`) — **NEW surface;
  privilege boundary OK but no audit-log row written on revocation.** The
  endpoint correctly returns 404 in all three of (wrong-creator, missing,
  already-revoked) so there's no enumeration oracle (good). However, key
  revocation is a security-relevant action and we already have an audit-log
  facility (`append_audit` in models, used by `delete_account` at
  routers/auth.py:245). Currently only an `observability.log_event` is
  written, which is not the durable, query-able audit surface (rubric
  category 3 — security-event traceability). | fix: add an
  `append_audit(session, action="api_key.revoked", actor=str(creator.id),
  entity_type="api_key", entity_id=key_id, before={"name": row.name})` call
  before the commit. Do the same for `api_key.created` in the POST handler.

- [SEV2] routers/insights.py:147-149 (`get_insights` totals query) —
  **NEW surface; the `nullif(... != value, True)` trick produces incorrect
  counts.** The intent is "count rows where kind=short" / "count rows where
  ingest_status=done", but `func.nullif(Video.kind != VideoKind.short, True)`
  evaluates to `NULL` when the kind IS short (the predicate is `False`,
  which is NOT equal to `True`, so nullif returns `False`) and to `NULL`
  when kind is NOT short (`True == True` → NULL). Both branches return
  `NULL` and `count(NULL)` is 0 in every case. **Verify with a query
  against a populated DB** (`needs-runtime-confirmation`), but the SQL as
  written looks broken — the `nullif` second arg should be `False`, not
  `True`, for the "keep rows matching predicate" intent. (rubric category 6
  / correctness on an endpoint the insights page consumes.) | fix: rewrite
  as the standard idiom `func.count(case((Video.kind == VideoKind.short,
  1), else_=None))`, which is unambiguous; or use `func.sum(case(...,
  else_=0))`. Add a unit test that seeds 3 shorts + 2 longs + 4 done + 1
  pending and asserts the returned totals match. (Flagging SEV2 not SEV1
  because the bug, if confirmed, returns zeros — not wrong-creator data —
  so it's a UX defect not a leak.)

- [SEV2] routers/insights.py:142-153 — totals query produces five aggregates
  in one SELECT but groups them across the full video table per creator;
  for a 10k-video creator that's a sequential scan over ~10k rows on every
  insights pageview (rubric category 2). | fix: either (a) memoize the
  totals as a denormalized counter on the `Creator` row updated by the
  catalog/sync task, or (b) cache the response for ~60s in Redis keyed by
  `(creator_id, "insights")`. The insights page is a polled dashboard view,
  not real-time. (Pair with the count-correctness fix above.)

- [cleanup] routers/clips.py:201-210 (`_obs_clip_youtube_id`) — synthetic
  id pattern `obs-<12 hex>` provides 48 bits of entropy (~2^48). Per the
  birthday bound, collision probability hits ~50% at ~16M rows; per-creator
  the schema's UNIQUE(creator_id, youtube_video_id) means real risk is
  per-creator, not global. At 10k uploads per creator (the PRD ceiling)
  collision is astronomically unlikely. **Not a defect today**, but document
  the math so a future migration that drops the creator-scoped uniqueness
  doesn't silently raise the collision floor. | fix: add a one-line comment
  citing the collision math, or widen to 16 hex chars (still inside
  `String(32)`) for zero-cost future-proofing.

- [cleanup] routers/insights.py:77-109 (`_fetch_performers`) — re-filters
  the video ids with `Video.creator_id == creator_id` even though the ids
  came from the creator's own DNA jsonb. **Defense-in-depth, correct
  posture** — no defect, just noting it as a positive. The drop-on-foreign
  pattern is the right shape for a stale/migrated DNA row carrying an id
  the creator no longer owns.

- [cleanup] routers/auth.py:117, routers/auth.py:131, routers/auth.py:199,
  routers/clips.py:86, routers/clips.py:91, routers/clips.py:157,
  routers/clips.py:159, routers/clips.py:160, routers/clips.py:294,
  routers/clips.py:296, routers/clips.py:297, routers/clips.py:312,
  routers/creators.py:164, routers/creators.py:166, routers/creators.py:168,
  routers/creators.py:213, routers/creators.py:215, routers/creators.py:289,
  routers/improvement.py:93, routers/improvement.py:96, routers/review.py:65,
  routers/review.py:77, routers/videos.py:132, routers/videos.py:269,
  routers/videos.py:271, routers/videos.py:288 — heavy use of *function-local*
  `import` statements. **Carry-forward** — unchanged across waves. These
  re-execute the import-cache lookup on every request; for `from worker
  import progress` and `from observability import log_event` that's
  measurable hot-path overhead under load. Some are load-order-driven
  (circular imports between `worker.tasks` and `routers`), but most are
  not (rubric category 6). | fix: hoist side-effect-free imports
  (`observability.log_event`, `worker.progress`, `redis`, `logging`) to
  module top. Keep only the genuinely cyclic ones (`worker.tasks.*`,
  `billing.ledger.grant_minutes`) local.

- [cleanup] routers/auth.py:131, routers/videos.py:132, routers/videos.py:277
  — `import logging as _logging; _logging.getLogger(__name__).warning(...)`
  is a workaround for not importing `logging` at module top. **Carry-
  forward**. Both modules already have `logger = logging.getLogger(__name__)`;
  the local `_logging` alias is dead-weight. | fix: drop the local re-import
  and use the existing module-level `logger`.

- [cleanup] routers/clips.py:142, routers/clips.py:173, routers/clips.py:332,
  routers/review.py:49, routers/videos.py:315, routers/api_keys.py:163 —
  `session.get(Clip|Video|CreatorApiKey, id)` then post-fetch `creator_id`
  check is fetch-then-validate. Pulls one extra row over the wire when the
  entity belongs to a different creator. Not a leak (the check is present
  and correct, so rubric category 3 is OK), but a single
  `session.scalar(select(...).where(id == X, creator_id == creator.id))`
  is one query at the same cost and avoids loading the foreign row at all
  (rubric category 2). | fix: replace each `session.get(...)` with a
  scoped `session.scalar(select(...).where(...))`.

- [cleanup] routers/videos.py:184-204 — temp-file lifecycle uses
  `tempfile.NamedTemporaryFile(... delete=False)` + manual `unlink` only in
  the `except HTTPException` branch. **Carry-forward**. If a non-HTTP
  exception fires inside the read loop (`OSError` on disk full,
  `CancelledError` on client disconnect mid-upload), the temp file leaks
  (rubric category 1, disk leak on error path). | fix: wrap the whole block
  in `try / except BaseException: tmp_path.unlink(missing_ok=True); raise`
  — or copy the `try/finally` shape used at line 238-244 for the R2 PUT.
  **Same fix applies to the new `routers/clips.py::ingest_clip`** (see SEV2
  above).

- [cleanup] routers/upload_intel.py — module is missing
  `logger = logging.getLogger(__name__)`. **Carry-forward.** No defects
  today (no error paths), but the inconsistency with the rest of the slice
  is jarring. | fix: add the logger declaration for grep-uniformity.

- [cleanup] routers/creators.py:33-46, routers/clips.py:47-52,
  routers/improvement.py:18-24 — `BuildQueuedOut`, `CatalogSyncQueuedOut`,
  `RenderQueuedOut`, `BriefQueuedOut` are four near-identical Pydantic
  models (`task_id`, `status`, `stream_url`). **Carry-forward**. DRY
  violation — four copies, one shape (rubric category 6). Note: every
  `stream_url` field is now correctly `str | None` (Wave-5 Fix 1 closed
  the prior SEV2 here), so the only remaining issue is the duplication.
  | fix: extract a `TaskQueuedOut` base in `routers/_schemas.py` and let
  each router subclass or alias it.

- [cleanup] routers/api_keys.py:131-138, routers/api_keys.py:169-175 —
  `from observability import log_event` inlined inside the function.
  Same hot-path / consistency issue as the carry-forward import-sprawl
  finding above; flagging the new file so the next pass picks it up.
  | fix: hoist `from observability import log_event` to module top.

- [cleanup] routers/clips.py:294, routers/clips.py:296 — `import redis as
  _redis_pkg` and `from worker import progress` inside the new
  `ingest_clip` body. Same hot-path / consistency issue as the carry-
  forward sprawl finding. | fix: hoist to module top alongside the existing
  imports.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | 1 SEV2 (temp-file leak on non-HTTPException error path in new `ingest_clip`); 1 cleanup (same in `upload_video`, carry-forward). DB sessions OK (FastAPI dep injection + commit/rollback in `get_session`); `httpx.AsyncClient` for OAuth revoke is per-call but inside a context manager so it closes (not a leak, though a module-level singleton would be cheaper — flagged under root-infra, not here). |
| 2 Concurrency & scale | 2 SEV2 (unbounded `/videos` + `/clips` list); 1 SEV2 (insights totals scan); fetch-then-validate cleanup. Stripe → `asyncio.to_thread` ✅; R2 PUT → `asyncio.to_thread` ✅ (both upload paths); ffprobe → `asyncio.to_thread` ✅ (both); account-deletion delete-prefix → `asyncio.to_thread` ✅. No remaining sync-in-async on the hot path. |
| 3 Security & compliance | 1 SEV2 (enumeration oracle on `/tasks/{id}/events`, carry-forward); 1 SEV2 (per-creator rate-limit key likely IP-based on new `/clips/ingest`); 1 SEV2 (missing audit-log row on api-key create/revoke). Per-creator isolation **verified on every query** including the three NEW surfaces: `api_keys.py:96-101, 122, 164` ✅; `insights.py:151, 170, 93-96` ✅; `clips.py:281` (creator_id derived from bearer-resolved Creator, never client-supplied) ✅. Token handling via `decrypt()` at `auth.py:206` ✅. YouTube ID validated against `^[A-Za-z0-9_-]{11}$` before storage-key interpolation ✅. Synthetic OBS id (`obs-<12 hex>`) is structurally distinct from real YT ids and per-creator-unique by schema constraint ✅. Raw API keys returned ONLY in the create response, never logged, never retrievable thereafter ✅. No PII in log lines reviewed. |
| 4 Clip-quality | n/a (routers do not score). `routers/clips.py` delegates ranking to `clip_engine.ranking.generate_and_rank_clips` and returns `principle` + `reasoning` from `signals_jsonb` directly — no virality language anywhere in this slice (including all three new endpoints). |
| 5 Anthropic SDK | n/a (routers do not call the LLM directly — `start_improvement_brief` and `build_dna` enqueue Celery tasks). |
| 6 Cleanliness & typing | 2 SEV2 (over-broad `except Exception` in auth + billing, carry-forward; mirrored in videos.py:131); 1 SEV2 (insights `nullif` aggregate likely returns 0); cleanup: function-local import sprawl (worsened by the two new files inheriting the pattern), fetch-then-validate query shape, duplicated `*QueuedOut` schemas, dead `import logging as _logging` workaround, OBS-id entropy comment. Type hints present on every signature in the slice including new files. |
| 7 Error handling / API | 1 SEV2 (`Last-Event-ID` → 500 on malformed, carry-forward). Pydantic models on every request & response including new `ApiKeyOut`, `ApiKeyCreatedOut`, `ApiKeyCreateIn`, `ApiKeyListOut`, `InsightsOut`, `ChannelTotalsOut`, `DnaStatsOut`, `PerformerOut`, `ClipIngestedOut` ✅. Status codes used correctly throughout (201 on create, 204 on revoke + Response shape, 202 on async-queue, 401 on bearer-miss, 404 on isolation-miss, 409 on conflict, 413 on size, 422 on validation). No stack traces or DB errors leaked to client. |
| 8 Config & paths | All paths absolute (`Path(tmp.name)` ✅). R2 keys creator-scoped + youtube-id-validated ✅. Settings (`UPLOAD_MAX_MB`, `CLIPS_PER_VIDEO_DEFAULT`, `STRIPE_SECRET_KEY`, `FREE_TRIAL_MINUTES`, `JWT_EXPIRY_MINUTES`) all present in `.env.example` ✅. No new env vars introduced in Wave 8 surfaces. |

## Wave-8 delta verification (the three NEW endpoint surfaces)

`routers/api_keys.py` — GET/POST/DELETE `/creators/me/api-keys`:
- GET (line 81-102): `where(creator_id == creator.id, revoked_at IS NULL)` ✅
- POST (line 105-146): `creator_id=creator.id` from session-cookie creator;
  raw key returned ONCE and never persisted (hash only) ✅
- DELETE (line 149-177): `session.get(...)` then triple-check
  `(row is None OR creator_id mismatch OR already revoked) → 404` —
  isolation correct, no enumeration oracle, soft-delete via `revoked_at`,
  but no audit-log row (SEV2 above)
- Bearer-auth dependency at `api_key.py:79-117`: returns 401 (not 404) on
  missing/malformed/revoked, sets `session.info["creator_id"]` for RLS,
  updates `last_used_at` — all correct

`routers/clips.py::ingest_clip` — `POST /clips/ingest` via bearer auth:
- Auth via `get_current_creator_via_api_key` (api_key.py:79) ✅
- `creator_id` derived from resolved Creator, NEVER from request body ✅
- Balance check before upload, balance re-check after ffprobe ✅
- ffprobe + R2 PUT both off-loop via `asyncio.to_thread` ✅
- Synthetic `obs-<12hex>` id avoids collision with real YT ids ✅
- aset_owner wrapped in `try/except RedisError` (born wrapped) ✅
- **Temp-file leak on non-HTTPException error path** (SEV2 above)
- **Rate-limit key likely IP-based, not per-creator** (SEV2 above)

`routers/insights.py` — `GET /creators/me/insights`:
- Every SELECT filters on `creator.id` (totals, dna, performers) ✅
- `_fetch_performers` re-filters by `creator_id` defensively ✅
- Single-fetch design ✅
- **`nullif(... != X, True)` aggregate idiom looks broken — likely returns 0
  for shorts/longs/ingested_done counts** (SEV2 above, needs DB-level
  verification)
- **Full-table scan per pageview on a 10k-video creator** (SEV2 above)

## Carry-forward status (re-checked against `78630c6`)

- `routers/tasks.py:131-138` — 404/403 enumeration oracle: **still open** (SEV2)
- `routers/tasks.py:140` — unvalidated `Last-Event-ID` → 500 on malformed: **still open** (SEV2)
- `routers/videos.py:63-97` + `routers/clips.py:115-134` — unbounded list endpoints: **still open** (SEV2)
- `routers/auth.py:229, 241` + `routers/billing.py:109, 128` + `routers/videos.py:131` — over-broad `except Exception`: **still open** (SEV2)
- prior SEV1 (`aset_owner` unwrapped at three sites): **CLOSED by Wave-5 Fix 1** ✅
- prior SEV2 (`RenderQueuedOut.stream_url` non-optional): **CLOSED by Wave-5 Fix 1** ✅ (every `*QueuedOut.stream_url` is now `str | None`)
- prior cleanup (fetch-then-validate, function-local imports, duplicated schemas, `_logging` alias, missing logger in upload_intel, temp-file leak in upload_video): **still open**

## Module verdict

**NEEDS-WORK** — Wave-5 Fix 1 correctly closed the prior `aset_owner` SEV1
and the `stream_url` schema SEV2, and the three NEW Wave-8 surfaces
(`api_keys`, `insights`, `clips.ingest`) all enforce per-creator isolation
correctly at every SELECT. However, four carry-forward SEV2s remain
(`Last-Event-ID` 500, `/tasks/{id}/events` 404/403 oracle, unbounded list
endpoints, over-broad `except Exception`) AND Wave 8 introduced three new
SEV2s of its own (likely-broken `nullif` insights aggregate, temp-file leak
in `ingest_clip`, rate-limit key not per-creator on `/clips/ingest`). Per-
tenant isolation is structurally sound (RLS via `session.info["creator_id"]`
plus explicit `WHERE creator.id`) on every endpoint in the slice including
the new ones — no BLOCKER cross-tenant leak.
