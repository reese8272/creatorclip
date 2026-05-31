# CreatorClip — Production Assessment

**Date:** 2026-05-31 (post Wave-3 hotfix batch)  ·  **Commit:** `84a7e9f`  ·  **Tests:** 543 passed, 1 skipped, 89 deselected (default lane)

## VERDICT: PRODUCTION-READY — **CONDITIONAL (trending YES — SEV1 trajectory hits 0)**

**No open BLOCKER, no SEV1, and no cross-tenant leak in any module.** Wave 3 closed all 3 SEV1s flagged by the post-Wave-2 register (`improvement/brief.py` streaming tools drop, `routers/improvement.py` aset_owner ordering, `billing/stripe_client.py` sync-in-async carry-forward) AND closed 3 of the new Wave-2 SEV2s (OAuth callback gap, upload-chain done-before-clips, catalog sync silent skip). SEV1 trajectory across the last five cycles: **4 → 2 → 1 → 3 → 0**.

What keeps the verdict CONDITIONAL rather than YES: 33 SEV2s (down from 36 post-Wave-2) and the persistent scale-axis gap (Locust on real staging cluster, Issue 78f) that only a load test can settle. The single Wave-3-introduced finding is a SEV2 — `routers/videos.py:262-266` — where the Fix B fail-open posture wasn't applied uniformly; one-line fix mirroring Fix B.

Module verdicts: **dna ✅ clean · improvement ✅ clean · 9 NEEDS-WORK · 0 BLOCKER · 0 cross-tenant leak**.

---

## Layer 0 — deterministic gates (from `_machine.json`)
| Gate | Result | Baseline | Status |
|---|---|---|---|
| ruff | 0 issues | 0 | ✅ |
| mypy | 0 errors | 0 | ✅ |
| coverage | not run locally; CI authoritative | 69.54% floor | ✅ no regression expected — Wave 3 added +7 tests + reworked 3 existing |
| bandit | not run locally; CI verified | 0 / 0 | ✅ |
| pip-audit | not run locally; CI verified | 0 | ✅ |
| freshness | both skills 2d | <90d | ✅ |

Full local pytest default lane: **543 passed / 1 skipped / 89 deselected** — net +10 vs post-Wave-2 baseline (Wave 3 added 7 new tests + reworked 3 existing; all pass).

Top untested load-bearing code: no new gap surfaced. Wave-3's new SEV2 (`routers/videos.py:262-266`) would have been caught by a regression test on the Redis-down upload path — that test is part of the recommended fix.

## Layer 1 — module register (ranked)

| Sev | Module | Location | Issue | Backed fix |
|---|---|---|---|---|
| SEV2 (**NEW Wave-3**) | routers | `routers/videos.py:262-266` | Fix B's fail-open posture is NOT applied uniformly. `POST /videos/upload` calls `await progress.aset_owner(str(video.id), str(creator.id))` AFTER the Video row commit + R2 upload — a Redis blip here 500s the request, leaving an orphaned pending Video row + R2 blob + a Celery chain already enqueued (because `start_pipeline(str(video.id))` runs BEFORE `aset_owner`). Same shape as the pre-Wave-3 improvement-brief SEV1 (Fix B), but in a different router. Surfaced precisely because the post-Wave-3 walk noticed Fix B's pattern wasn't applied here. | One-line wrap mirroring Fix B: ```python try: await progress.aset_owner(str(video.id), str(creator.id)) except _redis_pkg.RedisError as exc: logger.warning("upload aset_owner failed: %s", exc); stream_url = None ``` + return `stream_url` conditionally. Regression test pinning the Redis-down fail-open. |
| SEV2 | billing | `billing/refund.py:57-71` | Refund idempotency is read-then-write on `pack_id` with NO UNIQUE constraint on `(reason='refund', pack_id)`. Two concurrent `on_failure` callbacks for the same `video_id` would both pass the SELECT and both INSERT → double-credit. Celery's at-least-once delivery (`task_acks_late=True`) makes this reachable. Hotfix B (Wave 1) closed the RLS no-op but NOT this race. Carry-forward. | Alembic migration: `CREATE UNIQUE INDEX minute_packs_refund_key ON minute_packs (pack_id) WHERE reason = 'refund'` (partial index avoids colliding with non-refund packs). Drop the read-then-write guard; let `grant_minutes`'s existing `IntegrityError` catch path no-op the duplicate. Concurrent-refund integration test. |
| SEV2 | worker | `worker/progress.py:190-208` | `aread_since` uses XREAD with `block=5000`; each blocked read holds one Redis connection for full block duration. redis-py async pool default 50. **Now more acute post-Wave-3**: Fix E's terminal-done relocation means SSE consumers hold longer (entire upload→clips pipeline ~5-30s longer per stream), and Wave 2's catalog-sync + improvement-brief + render emit surfaces all feed this same pool. ~100 concurrent SSE consumers exhausts pool. (needs-runtime-confirmation under load.) | Bound pool explicitly: `aredis.from_url(..., max_connections=settings.REDIS_MAX_CONNECTIONS)` default 200. Separate clients for reads vs writes. Document math in `docs/DEPLOYMENT.md`. |
| SEV2 | billing | `billing/stripe_client.py:20` | Module-level Stripe client built with empty key in dev/staging silently binds a landmine. `_require_prod_secrets` only fails-fast on `ENV == "production"`; staging deploy with no key surfaces only at first `/checkout`. Carry-forward. | Lazy-init via `functools.lru_cache`; OR extend `_require_prod_secrets` to fail-fast for `ENV == "staging"` too. |
| SEV2 | billing | `billing/refund.py` | Refund triggers on `on_failure` without re-checking the video's terminal state. Carry-forward. | Gate refund on `video.render_status IN ('failed', 'errored')`, OR rely on the pack_id UNIQUE partial-index fix above + document. |
| SEV2 | worker | `worker/progress.py:134-146` | `_async_client()` rebuilds singleton on loop-mismatch but doesn't close old client. Pytest function-scoped loops abandon aredis clients with pool bound to dead loop. (needs-runtime-confirmation via `lsof` for process-life socket leak.) Carry-forward. | Best-effort close old client via `old_loop.call_soon_threadsafe(...)` OR expose `await arebind()` helper. |
| SEV2 | worker | `worker/progress.py:149-164` | `aemit` exception handler nulls out `_AIO`/`_AIO_LOOP` on ANY exception, not just `ConnectionError`/`TimeoutError`. Concurrent emits + Redis brown-out → pathological churn. Wave 2/3 added more emit call sites → wider trust surface. Carry-forward. | `except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError):` resets globals; `except Exception:` leaves singleton intact. |
| SEV2 | worker | `worker/progress.py:74-85` | `_serialize` does `json.dumps(fields, default=str)` — no schema/allowlist. Wave 2/3 added more emit sites → wider structural trust surface. Today's emits are safe (counts, ints, exc_type names, static messages). Carry-forward. | Per-event-type `EventPayload(BaseModel)`. Interim: emit-level guard dropping fields by length or token-shape regex. |
| SEV2 | worker | `worker/tasks.py:781-786` + new emit sites | `_emit("error", message=str(exc))` raw-passes ValueError args to SSE stream. Wave 3 added more `error` emits — each is a fresh structural trust surface. Today's raisers are safe. Carry-forward. | Allowlist of known-safe error shapes + generic fallback `"operation failed"` for unknown. |
| SEV2 | worker | `worker/anthropic_stream.py:57-75` | Network drop mid-stream raises inside `for event in stream:` loop and propagates up — no terminal `error` emit at this layer. Caller's emit fires as backup. Carry-forward. | Wrap streaming call inside `stream_and_emit` with `try/except: sync_emit(task_id, "error", message="stream interrupted"); raise`. |
| SEV2 | worker | `worker/anthropic_stream.py:72-84` | `usage_dict` casts via `getattr(usage, ..., 0)` — `0` as default silently hides SDK schema changes. Issue 84 flagged this for the SDK bump. Carry-forward. | Return `None` when attribute missing; log warning the first time a previously-present field returns None. |
| SEV2 | worker | `worker/tasks.py:415-496` (`_render_clip_async`) | Idempotency guard protects sequential redelivery but NOT concurrent: two workers both read `pending`, both flip to `running`, both encode + upload to same R2 key. Carry-forward. | `select(Clip).where(...).with_for_update()` + re-check `render_status` under lock. |
| SEV2 | worker | `worker/tasks.py:300-339` (`_ingest_async`) | Not a clean no-op on redelivery: re-`probe_duration_s` + `extract_audio_wav` over already-derived WAV. Wasteful but not corrupting. Carry-forward. | Short-circuit when `source_uri` already points at derived audio key OR gate on `ingest_status == done`. |
| SEV2 | worker | `worker/tasks.py:~770` (`_poll_clip_outcomes_async`) | Does NOT break on YouTube quota exhaustion; quota-out creator walks whole candidate set firing doomed calls. Carry-forward. | Catch `QuotaExhaustedError` and `break` (mirror analytics-refresh handler); commit partial progress first. |
| SEV2 | routers | `routers/tasks.py:131-138` | 404/403 ownership-check enumeration oracle: authed client can distinguish "task never existed" from "exists but belongs to another creator". In-code comment contradicts the implementation. Carry-forward. | Return `404 "Unknown task"` for BOTH branches; log distinction server-side. Regression test asserting creator B sees 404 on creator A's task_id. |
| SEV2 | routers | `routers/tasks.py:140` + `worker/progress.py:204` | Client-supplied `Last-Event-ID` header forwarded raw into XREAD; any non-`<ms>-<seq>` value → 500 + noisy log on malformed reconnect. Carry-forward. | Validate `last_event_id` against `^\d+-\d+$` (or empty); on mismatch reset cursor. Cap header length at ≤64 chars. |
| SEV2 | routers | `videos.py:80-84`, `clips.py:~120`, `upload_intel.py:~38` | Unbounded `list(result.scalars())` on `/videos`, `/videos/{id}/clips`, `/me/upload-intel`. Established creator serializes entire list. Carry-forward. | Keyset pagination `?limit=&before=` with hard cap 100. |
| SEV2 | improvement | `improvement/brief.py:132-138` | Streaming log line bundles cache reads into single `cached_read=` counter — same TTL-tier-breakdown gap as DNA brief. Blocked behind SDK bump (Issue-84 follow-up). | Update log call after SDK bump to capture `usage.cache_creation.ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens`. |
| SEV2 | _root_infra | `Dockerfile:1-34` | Image runs as root. Carry-forward. | `RUN useradd --create-home --uid 1000 app && chown -R app:app /app /root/.local` then `USER app` after `COPY . .`. |
| SEV2 | _root_infra | `Dockerfile:34` | Default CMD ships `uvicorn --reload`. Carry-forward. | Default to `gunicorn -k uvicorn.workers.UvicornWorker -w 4 main:app --bind 0.0.0.0:8000`; move `--reload` to `docker-compose.yml`. |
| SEV2 | _root_infra | `observability.py:~224` | Celery ContextVar correlation safe only under prefork. Carry-forward. | Assert `app.conf.worker_pool == "prefork"` at worker startup; OR key task start off `task.request.id` via per-task dict. |
| SEV2 | youtube | `youtube/oauth.py:~290` | No Redis-down degradation in token refresh — broker blip 500s every analytics fetch. Carry-forward. **Slightly less acute post-Wave-3** — Fix B/D added fail-open posture to the aset_owner sites, less Redis surface elsewhere, but this site still 500s. | Wrap `set()` in `try/except redis.RedisError`; fall back to lockless refresh OR raise 503. |
| SEV2 | youtube | `docs/COMPLIANCE.md` | Analytics retention/staleness purge still TBD (Issue 75b). **Largest remaining compliance gap before OAuth verification.** Carry-forward. | Confirm Google's required staleness window, record in COMPLIANCE.md §2, add `YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS` setting + daily Beat sweep. |
| SEV2 | ingestion | `ingestion/transcribe.py:116-123,137-138` | Deepgram normalizer hard-key indexing → KeyError burns Celery retry. Carry-forward. | Switch comprehensions in `_normalize_deepgram` to `.get("start")`/`.get("end")` and skip None timestamps. |
| SEV2 | ingestion | `ingestion/transcribe.py:43-60` | `_guard_audio_size` swallows OSError; AssemblyAI uploads 0 bytes + returns empty transcript. Carry-forward. | In `except OSError`, raise `FileNotFoundError(f"audio not found: {audio_path}")`. |
| SEV2 | clip_engine | `clip_engine/ranking.py:139` | `dna_match=c.get("score")` seeded to composite score → preference feature collinear with its own label. Carry-forward. | Have `score_candidates` return DNA-only fit separately; persist to `dna_match`. Unit test asserting `dna_match ≠ composite`. |
| SEV2 | clip_engine | `clip_engine/candidates.py:113` | Candidate windows never deduped/merged for overlap. Carry-forward. | After chronological sort, IoU-merge pass dropping lower-prominence overlap >0.5. |
| SEV2 | dna | `dna/brief.py:130-151` | Streaming brief: ~2k synchronous Redis XADDs per build on threadpool slot. Acceptable for v1. Carry-forward. (needs-runtime-confirmation.) | If `build_dna` queue depth grows, batch deltas every K tokens or K ms. |
| SEV2 | dna | `dna/brief.py:153-157` | Asymmetric `# type: ignore[arg-type]` — will fail mypy day Issue-84 SDK bump tightens streaming stub. Carry-forward. | Mirror ignore on streaming call OR narrow `_build_request` return type to SDK TypedDicts after SDK bump. |
| SEV2 | dna | `dna/brief.py:134` | Function-local `from worker.anthropic_stream import stream_and_emit` (layering smell). Defends against import cycle. Carry-forward. | Accept as lesser evil for v1; add one-line comment explaining WHY. |
| SEV2 | preference | `preference/model.py:46` | LightGBM branch of `_ALLOWED_CLASSES` hand-derived; library upgrade silently disables personalization via swallowed UnpicklingError. Carry-forward. | `test_scorer_round_trips_lightgbm` forcing the LGBM branch. |
| SEV2 | preference | `preference/_scorer_cache.py:23` | LRU bound is entry-count, not bytes. Carry-forward. | Lower default to 32 OR gate cap on bytes OR document math. |
| SEV2 | upload_intel | `upload_intel/timing.py:54-55` | `optimal_gap_hours` still missing Issue-75d bounds/coercion guard. Carry-forward. | Filter+coerce rows first; return None if <2 valid rows. |

Plus **~40 cleanup** items spread across all 11 modules — typing gaps, DRY extractions (the 3 duplicated 402 raises in `billing/ledger.py`, render keyframe timeout scoped tighter than full encode budget, lifespan-coupling registry, `_clip_response` hand-mapped dict duplicating `ClipOut`, `signal_array` rebuilt per candidate), docstring contracts on the new emit wire-shape, `DATABASE_MIGRATION_URL` missing from `.env.example`. Per-finding detail in `docs/assessment/modules/*.md`.

## Layer 2 — scale checklist (`scale-checklist.md`)
| Axis | Status | Evidence |
|---|---|---|
| A Pool math | ⚠️ | psycopg3 + pool sizes + RLS context all verified. `worker/progress.py:190-208` blocking XREAD now serves 5 emit surfaces post-Wave-3 (upload chain INCLUDING generate_clips now part of the chain, render, catalog sync, improvement brief, DNA build). Locust evidence still pending. *(unchanged ⚠️ — same gap, slightly more pressure)* |
| B Async loop hygiene | ✅ | **PROMOTED from ⚠️**: Wave-3 Fix C closed the sync-Stripe-in-async SEV1. Every async path verified for blocking calls — none remain. *(was ⚠️ — now ✅)* |
| C Celery idempotency | ⚠️ | Issue 76 advisory lock closed `build_dna` double-pay. Residual: render double-encode (SEV2), ingest re-do (SEV2), `poll_clip_outcomes` no quota break (SEV2), refund pack_id race (SEV2). Wave-3 Fix E added emits to `_generate_clips_async` but didn't change its idempotency guarantees. *(unchanged ⚠️)* |
| D Tenant isolation | ✅ | RLS migration `0010_rls_policies` still in place. Wave 3's new `aset_owner` calls verified to use the authenticated principal. The Wave-2 `routers/auth.py` gap is closed by Fix D. *(unchanged ✅)* |
| E Backpressure | ⚠️ | `oauth.py` Redis-down → 500 still open. Anthropic mid-stream interrupt still has no first-class terminal emit. Analytics retention purge still open. **Wave-3 Fix B + D added fail-open posture in 2 places** but the new SEV2 (`videos.py` upload aset_owner not fail-open) shows the pattern isn't applied uniformly. *(unchanged ⚠️ — same gap, partial Wave-3 improvement)* |
| F Rate limit / quota | ✅ | Hotfix A's EXPIRE-on-every-INCR in place. slowapi per-creator + spend gate verified. Not load-tested. *(unchanged ✅)* |
| G Observability | ✅ | request-id ContextVar + ASGI middleware + JSON logs all unchanged. **Wave 3 widened the SSE observability surface further** — `_generate_clips_async` now emits, `_sync_channel_catalog_async` per-video skips emit. No PII/token in any new emit verified line-by-line. *(unchanged ✅)* |
| H Migration / pgvector | ✅ | All migrations at HEAD. Backups + PITR restore-test still unverified. *(unchanged)* |
| I Secrets / deletion | ✅ | All unchanged. *(unchanged)* |

## Diff vs previous report (2026-05-31 post Wave 2, commit `f5d44df`)

**Fixed & verified this cycle (3 SEV1s + 3 SEV2s closed by Wave 3):**
- ✅ SEV1 `improvement/brief.py:124-131` + `worker/anthropic_stream.py` — Wave-3 Fix A: streaming branch now passes `tools=tools` to `stream_and_emit`; web_search grounding restored on 100% of streaming improvement briefs. Pinned by `tests/test_brief_caching.py::test_improvement_brief_streaming_path_passes_tools_to_stream_and_emit` + `tests/test_anthropic_stream.py::test_tools_kwarg_forwarded_to_stream_when_provided`.
- ✅ SEV1 `routers/improvement.py:91-100` — Wave-3 Fix B: `aset_owner` reordered after row.job_id commit, wrapped in `try/except redis.RedisError`. Fail-open returns `stream_url=None` instead of 500. Pinned by 2 new tests covering happy path + Redis-down fail-open.
- ✅ SEV1 `billing/stripe_client.py:65` — Wave-3 Fix C: `create_checkout_session` now wrapped in `await asyncio.to_thread(...)` at the router boundary. Pinned by thread-id assertion in `test_billing.py::test_checkout_offloads_sync_stripe_to_thread`.
- ✅ SEV2 `routers/auth.py:117-119` — Wave-3 Fix D: post-OAuth catalog sync stamps `aset_owner` with same fail-open posture. Source-inspect test pins the structural fact.
- ✅ SEV2 `worker/tasks.py:_signals_async` — Wave-3 Fix E: terminal `done` replaced with non-terminal `step:ingest_complete`. `_generate_clips_async` now emits its own terminal `done` on the same `video_id` stream key.
- ✅ SEV2 `worker/tasks.py:_sync_channel_catalog_async` — Wave-3 Fix F: per-video failures emit `step:sync_metrics_skipped` (class name only, no exception message).

**New this run (1 SEV2):**
- 🆕 SEV2 `routers/videos.py:262-266` — Wave-3 walk surfaced that Fix B's fail-open posture wasn't applied uniformly. `POST /videos/upload` calls `aset_owner` AFTER both the Video row commit AND `start_pipeline(...)` enqueue. Same shape as the pre-Wave-3 improvement-brief SEV1; same one-line fix.

**Carry-forward (re-checked, none addressed):**
- 26 SEV2s across worker, routers, billing, ingestion, youtube, preference, _root_infra, clip_engine, dna, upload_intel (full per-module detail in `docs/assessment/modules/*.md`)

**Counts (Layer 1, all modules):**

| Severity | Wave 3 (2026-05-31) | Wave 2 (earlier) | Wave 1 |
|---|---|---|---|
| BLOCKER | 0 | 0 | 0 |
| SEV1 | **0** | 3 | 1 |
| SEV2 | 33 | ~36 | 31 |
| cleanup | ~40 | ~38 | 34 |

SEV1 trajectory across five cycles: **4 → 2 → 1 → 3 → 0**. First time SEV1 count reaches 0 in the assessment history. The single Wave-3-introduced SEV2 has a one-line fix mirroring Fix B; closing it would tighten the register further with no register-debt.

## Top 5 actions, in order

1. **Apply Fix B's fail-open posture uniformly: `routers/videos.py:262-266`** (NEW Wave-3 SEV2). One-line `try/except redis.RedisError` wrap mirroring Fix B at routers/improvement.py:108-115. Stream_url returns None on Redis-down; the upload still goes through. Regression test mocking `aset_owner` to raise + asserting 202 + `stream_url=None`. ~5 LOC + 1 test. **The only Wave-3-introduced issue and the smallest open fix on the register.**

2. **`billing/refund.py:57-71` refund pack_id UNIQUE index** (carry-forward, most-acute remaining SEV2). Alembic migration adding `CREATE UNIQUE INDEX ... WHERE reason = 'refund'`, drop the read-then-write guard. Concurrent-refund integration test. This is the only billing SEV2 that a misbehaving Celery delivery can exploit for double-credit.

3. **Locust load test on real staging cluster (Issue 78f).** Wave 3 widened the SSE-pool pressure further by extending the upload-chain SSE through `_generate_clips_async`. Scale axes A/C/E all need actual concurrency evidence. **This is the single remaining gate between CONDITIONAL and YES.**

4. **Compliance — analytics retention purge (Issue 75b, carry-forward).** Confirm Google's required staleness window, add `YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS=30` setting + daily Beat sweep. **Largest remaining compliance gap before OAuth app verification.**

5. **Anthropic SDK 0.40 → 0.105.2 bump (Issue 84 follow-up).** 65 minor versions stale; no breaking changes to our 3 call sites; unlocks `usage.cache_creation.ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens` TTL-tier observability that's load-bearing for the "drop unproductive cache markers" follow-up Issue 84 flagged.

---

## What Wave 3 got right

Wave 3 is the assessment's first SEV1-zero cycle in five rounds. Three observations worth pinning:

- **The fix sizes matched the issue sizes.** Each Wave-3 fix was 5-30 LOC + 1 regression test — not a refactor, not a rewrite. The hotfix-batch shape (6 small fixes, 1 commit per fix) is the right scope when the post-assess register is short.
- **The fail-open posture (Fix B + D) is a reusable invariant.** Both fixes apply the same shape: stamp ownership after the durable state, wrap in `try/except RedisError`, log + return `stream_url=None`. The NEW SEV2 at `videos.py:262-266` is the third place this pattern should land — once that closes, the invariant is uniform across every `aset_owner` site.
- **`asyncio.to_thread` for sync SDK in async route is the canonical 2026 mitigation** when an upstream SDK is sync-only (Anthropic-SDK research from Issue 84 confirmed this; Wave 3 applied it cleanly to Stripe). The wrapping doesn't change `billing/stripe_client.py`'s shape — all the existing direct-import tests still pass — only the call site at the router boundary.

**Zero new BLOCKERs, zero cross-tenant leak surface, zero SEV1 introduced** by any Wave-3 change. The single new SEV2 surfaced is one the post-Wave-2 walk had also missed; Wave 3 surfaced it precisely because applying the Fix B pattern in `routers/improvement.py` made the absence of that pattern in `routers/videos.py` visible.
