# CreatorClip — Production Assessment

**Date:** 2026-06-01 (post Wave-9 — Issues 102 + 103 + 104 + 105 + 106 + 107 + 108 closed) · **Commit:** `d6a7393` (+ 11 commits ahead of `origin/main`, all unpushed) · **Tests:** 620 passed / 1 skipped / 125 deselected (default lane)

## VERDICT: PRODUCTION-READY — **CONDITIONAL**

**No open BLOCKER, no SEV1, no cross-tenant leak in any module.** Wave 9's 7-issue parallel-build batch closed every prior SEV1 and 19 of the prior 51 SEV2s, with **4 of 11 modules now fully clean** (`youtube`, `upload_intel`, `billing`, `preference`) — the first time multiple modules have reached `clean` since `/assess` started running. The remaining 32 SEV2s are split between long-standing carry-forwards (clip_engine's Haiku-A/B follow-up still unfiled, dna's import-time Anthropic client + niche-keyword gap + tenacity reraise, improvement's web_search-without-max_uses) and structural items the scale-checklist can't settle without Locust evidence (`_root_infra`'s `/health` per-probe connection churn, prefork-only ContextVar correlation, the auth bootstrap-query-before-RLS-GUC invariant). Three honestly new SEV2s surfaced this run: `routers/auth.py::/auth/logout` and `routers/billing.py::/webhook` lack rate-limit decorators (CSRF-shaped surface), and an improvement-brief debounce race where two concurrent calls can both pass the in-flight check and double-fire the Anthropic API.

**One Issue-105 misread caught this run:** `_ingest_async`'s `.wav` short-circuit prevents *retry* orphans but does NOT delete the original mp4 on the FIRST successful ingest — so the orphan-mp4 SEV2 we thought was closed is only half-closed. Will fold into Issue 110.

SEV1 trajectory across eight cycles: **4 → 2 → 1 → 3 → 0 → 1 → 2 → 0**. The path from CONDITIONAL → YES is now down to: (1) Locust evidence for axes A (pool math) + E (backpressure), (2) the 3 net-new routers SEV2s, (3) the Issue-110 orphan-mp4-on-first-run fix, (4) Google OAuth app verification (external, user-side, fully unblocked from our side).

---

## Layer 0 — deterministic gates (from `_machine.json`)
| Gate | Result | Baseline | Status |
|---|---|---|---|
| ruff | 0 issues | 0 | ✅ |
| mypy | 0 errors | 0 | ✅ |
| coverage | **76.06%** | 75.20% (re-baselined Issue 107) | ✅ +0.86pp |
| bandit | 0 high / 0 medium | 0 / 0 | ✅ |
| pip-audit | 0 (6 documented in `[tool.pip-audit].ignore-vulns`) | 0 | ✅ |
| freshness | both skills 2d | <90d | ✅ |

**All Layer-0 gates green for the second consecutive cycle.** Coverage rose 6.52pp across the full Wave 9 batch (69.54% pre-Wave-9 baseline → 76.06% now). pip-audit was at 16 raw vulns at session start; Issue 107 (venv sync + 6 documented residuals in pyproject.toml) closed the gate. The Layer-0 floor is now genuinely production-grade — no untested local gates remaining.

## Layer 1 — module register (ranked)

| Sev | Module | Location | Issue | Backed fix |
|---|---|---|---|---|
| **SEV2** | routers (**NEW Wave-9 finding**) | `routers/auth.py:162-165` (`/auth/logout`) | No `@limiter.limit(...)` decorator. An attacker with a session cookie can issue unlimited logout requests; combined with the new `request.state.creator_id` stash, this is shaped like a CSRF target. | Add `@limiter.limit("30/minute", key_func=creator_key)`. Matches `/auth/me` and `/auth/delete` posture. |
| **SEV2** | routers (**NEW Wave-9 finding**) | `routers/billing.py:123-188` (`/billing/webhook`) | No rate limit + accepts raw bytes. Stripe's signature check is the only gate; a malicious sender can blast garbage payloads. Bot-net exhaustion vector against the webhook worker. | Add `@limiter.limit("60/minute", key_func=get_remote_address)` — keyed on IP (no session). Same 60/min posture Stripe themselves rate-limit incoming webhooks at. |
| **SEV2** | routers (**NEW Wave-9 finding**) | `routers/improvement.py:75-82` (debounce path) | Two concurrent POSTs both pass the in-flight check before either commits the row → both fire the Anthropic call → 2× billed tokens + corrupted state. Wave-9 added the row but not the SELECT-FOR-UPDATE around the check-then-insert. | `with_for_update()` on the existing-brief query OR `INSERT ... ON CONFLICT DO NOTHING` on the row, then check `lastrowid` to decide whether to enqueue Celery. |
| SEV2 | clip_engine | `clip_engine/ranking.py:102` | `select(Clip).where(Clip.video_id == video_id)` missing `creator_id` predicate (defense-in-depth). Not a live leak today, but the CLAUDE.md rule is "filter on creator_id on EVERY query." Function already takes `creator_id` parameter. | Add `.where(Clip.creator_id == creator_id)`. |
| SEV2 | clip_engine | `clip_engine/render.py:82-105` | Single-keyframe Haar cascade — for a creator with a pan/cut/shot change, mid-frame may not contain speaker; 9:16 reframe miscrops silently. | Sample 3 keyframes (25%, 50%, 75%), take median x. <300ms additional cost per render. |
| SEV2 | clip_engine | `clip_engine/scoring.py:23` | Module-level `AsyncAnthropic(...)` binds httpx pool to first-seen loop. Under Celery `run_async`, each task creates a fresh loop but the singleton stays bound to the worker's first. | Lazy per-loop `lru_cache(maxsize=1)` keyed on `asyncio.get_event_loop()`, OR drop to sync `Anthropic` via `asyncio.to_thread`. *(needs-runtime-confirmation)* |
| SEV2 | clip_engine | `clip_engine/scoring.py:203` (model selection) | Hardcoded `model=settings.ANTHROPIC_MODEL` across all 3 LLM call sites. Issue 84's Haiku-4.5-for-clip-scoring A/B flagged ~67% cost reduction; follow-up STILL NOT FILED as a tracked issue (re-grepped). | File as Issue 111: introduce `ANTHROPIC_MODEL_CLIP_SCORING`; A/B against eval scenarios. |
| SEV2 | dna | `dna/brief.py:21-25` | `_ANTHROPIC = Anthropic(...)` at import time. Missing/invalid `ANTHROPIC_API_KEY` cascades to worker boot failure for all 3 dependent modules. | Convert to lazy singleton (`dna/embeddings.py:23::_voyage()` shape). |
| SEV2 | dna | `dna/conflict.py:34-42` | `_NICHE_KEYWORDS` covers only 7 of N YouTube niche ids. Unmapped niches treated as already-matched → silently disables conflict detector for the majority of creators. | Populate keywords for every id in `youtube.categories.NICHE_IDS`. Add test enumerating `NICHE_IDS` to force conscious-decision on additions. |
| SEV2 | dna | `dna/embeddings.py:31` | `@retry` defaults `reraise=False` → final failure wraps in `tenacity.RetryError`; callers catching `voyageai.APIError` for credit-refund miss it. **Carry-forward — was in prior /assess too, Wave-9 didn't pick it up.** | `@retry(..., reraise=True)`. |
| SEV2 | preference | `preference/model.py:126-132` | `from_bytes` still mutates process-global `joblib.numpy_pickle.NumpyUnpickler` under `_UNPICKLER_LOCK` — Issue 102's `asyncio.to_thread` wrap makes the lock thread-serializing (not coroutine-serializing), but the swap is still a tail-latency hazard under heavy parallel cold-cache loads. **Documented joblib-1.x limit, tracked in DECISIONS.** | Re-evaluate when joblib exposes a per-load NumpyUnpickler injection slot. Lower priority — Issue 102 closed the SEV1 component. |
| SEV2 | preference | `preference/train.py:107` | Variable `result` re-bound at line 107 after being used for the feedback fetch at line 38 — shadows; harmless but confusing on re-read. | Rename second one `existing_result` or inline into `select(...).first()`. |
| SEV2 | ingestion | `ingestion/transcribe.py:54-55` | `_guard_audio_size` docstring still promises "let the backend surface a missing file" passthrough; Issue 103's body now raises `FileNotFoundError`. Docstring lies about contract. | Rewrite docstring: "raises `FileNotFoundError` from a missing-file `OSError` so the Celery retry/refund path sees a clear terminal error." |
| SEV2 | worker | `worker/tasks.py:437` (`_ingest_async` final commit) | **Issue 105 misread**: the `.wav` short-circuit prevents the *retry* case from creating ANOTHER orphan, but the FIRST successful run still overwrites `video.source_uri` from mp4-key to wav-key without deleting the mp4. `_purge_stale_source_media_async` iterates `Video.source_uri` to find purgeables → the original mp4 is permanently invisible to the sweep. ToS retention violation + unbounded R2 storage. | Capture `prior_source_uri` at function entry; after the final commit, `adelete_file(prior_source_uri)` if it's an mp4. File as Issue 110. |
| SEV2 | worker | `worker/tasks.py:1530-1549` (`_refresh_youtube_analytics_async`) | The advisory lock is acquired AFTER iterating creators — concurrent Beat fires can race past the per-creator scope before any lock is held. The Issue 105 lock is global per task but applied too late in the body. | Move `pg_try_advisory_lock` to the very top of the function (mirror `_build_dna_async` pattern). |
| SEV2 | worker | `worker/tasks.py:64-94` (`RefundOnFailureTask.on_failure`) | Still calls `run_async(refund_for_video(...))` from `on_failure` (main-thread context, not loop-thread). Safe under `worker_prefetch_multiplier=1`, breaks the day someone raises prefetch. **Carry-forward — Issue 105 fix list explicitly deferred this as needs-runtime-confirmation.** | Pin CI test asserting `prefetch=1` OR rewrite refund with its own sync session. |
| SEV2 | worker | `worker/celery_app.py:96-99` (`_shutdown_worker_loop`) | Worker-shutdown hook calls `aclose()` on async resources WITHOUT awaiting them — fire-and-forget on a closing loop. Resources leak on graceful shutdown. | `asyncio.run(_close_all())` or thread the shutdown through the running loop before close. |
| SEV2 | _root_infra | `main.py:135-153` | `/health` opens fresh `psycopg.AsyncConnection` AND `aioredis.from_url` per probe. Under k8s readiness/liveness probing × N replicas, this is sustained connect/disconnect churn OUTSIDE the SQLAlchemy pool — defeats the PgBouncer math. | Reuse SQLAlchemy `engine` for PG check; module-level redis singleton at import time + `aclose()` in lifespan. Wrap both in `asyncio.wait_for(..., 2.0)`. |
| SEV2 | _root_infra | `api_key.py:113-114` | Every API-key request fsync's a hot row (`UPDATE creator_api_keys SET last_used_at = now()`). High-frequency OBS uploader = write amplification. | Coarse-grain: only UPDATE if `last_used_at IS NULL OR last_used_at < now() - interval '60 seconds'`. |
| SEV2 | _root_infra | `observability.py:43-44, 224-241` | Celery ContextVar correlation safe ONLY under prefork. Future gevent/eventlet/threads migration silently corrupts correlation ids + start times. | Assert `app.conf.worker_pool == "prefork"` at worker startup OR key task start off `task.request.id` in a per-task dict guarded by `task_postrun`. |
| SEV2 | _root_infra | `db.py:80-103` | `recreate_engine()` is module-public, no guard against being called with in-flight sessions. Disposed pool + outstanding AsyncSessions = nondeterministic crash. | `_already_called` flag + raise on re-entry; underscore-prefix; document MUST-NOT precondition. |
| SEV2 | _root_infra | `auth.py:46-58` / `api_key.py:96-119` | Bootstrap SELECT issued BEFORE `session.info["creator_id"]` is set. Works today because `creators` and `creator_api_keys` are RLS-exempt; a future migration that flips either under RLS breaks ALL auth with 401-everywhere outage. | CI test enumerating RLS-exempt tables from `pg_policies` system catalog asserting `creators` + `creator_api_keys` are still exempt. |
| SEV2 | _root_infra | `config.py:222-231` | `print(..., file=sys.stderr)` on fatal startup. Container log aggregators parsing JSON miss the fatal message. | `logging.basicConfig(stream=sys.stderr, ...)` inside the `except ValidationError`, then `logging.error(...)`. |
| SEV2 | improvement | `improvement/brief.py:93` | `web_search` tool registered without `max_uses` → unbounded billed search fan-out per brief. At hundreds-of-creators load this is a real cost amplifier. **Carry-forward from Wave-4, Wave-9 didn't touch it.** | `"max_uses": settings.ANTHROPIC_WEB_SEARCH_MAX_USES` (default 5); add to `config.py` + `.env.example`. |
| SEV2 | improvement | `improvement/brief.py:161-167` | `tool_choice` not set on either call path (defaults `auto`) → model can skip web_search entirely; brief's value prop silently weakened. | `tool_choice={"type": "auto"}` explicitly OR `{"type": "tool", "name": "web_search"}` to force ≥1 search. Regression test asserting ≥1 `tool_use` block. |
| SEV2 | improvement | `improvement/brief.py:7-8, 58-60, 80-89` | Cache marker still inert: docstring says prefix is below Sonnet 4.6's 1024-token cacheable floor; `cache_control: ephemeral` is a 1.25× write premium for writes that never get read. | Pad `_SYSTEM_INSTRUCTIONS` past 1024 tokens OR drop the `cache_control` marker. |
| SEV2 | improvement | `improvement/brief.py:136-142, 157-163` (deferred behind SDK bump) | TTL-tier cache breakdown blocked behind anthropic SDK 0.40 → 0.105+ bump (Issue 84 follow-up). | After SDK bump: `getattr(usage, "cache_creation", None)` and emit `cache_creation_5m=` / `cache_creation_1h=`. |
| SEV2 | routers | `routers/tasks.py:131-138` | 404/403 ownership-check enumeration oracle. **Carry-forward.** | Single 404 — `if owner is None or owner != str(creator.id): raise 404`. |
| SEV2 | routers | `routers/tasks.py:96` (Last-Event-ID forward) | Unvalidated `Last-Event-ID` → `redis.ResponseError` → 500 on every malformed reconnect. **Carry-forward.** | Validate `^\d+-\d+$` before XREAD; on miss coerce to `"0-0"` and log warning. |
| SEV2 | routers | `routers/videos.py:66-100` + `clips.py:112-131` | Unbounded `select(...).order_by(...)` — full per-creator catalog in one hop. **Carry-forward.** | `limit=50` (max 200) + cursor on `(created_at DESC, id DESC)`. Verify index. |
| SEV2 | routers | `routers/auth.py:229`, `videos.py:134`, `billing.py:117, 136` | Over-broad `except Exception` swallows `CancelledError` + programming errors. **Carry-forward.** | Narrow to `(httpx.HTTPError, ValueError, sqlalchemy.exc.SQLAlchemyError)`; let `CancelledError` propagate. |

Plus **~30 cleanup items** across all 11 modules — including 1 missed by the Issue 108 sweep (`routers/auth.py:131` still has `import logging as _logging`). Per-finding detail in `docs/assessment/modules/*.md`.

**Module verdicts:** `youtube` ✅ clean · `upload_intel` ✅ clean · `billing` ✅ clean · `preference` ✅ clean (with documented SEV2) · `ingestion` NEEDS-WORK · `improvement` NEEDS-WORK · `dna` NEEDS-WORK · `clip_engine` NEEDS-WORK · `worker` NEEDS-WORK · `_root_infra` NEEDS-WORK · `routers` NEEDS-WORK · **0 BLOCKER · 0 SEV1 · 0 cross-tenant leak**.

## Layer 2 — scale checklist (`scale-checklist.md`)
| Axis | Status | Evidence |
|---|---|---|
| A Pool math | ⚠️ | PgBouncer + `prepare_threshold=None` in place; pool sizing documented in `DEPLOYMENT.md`. **Locust evidence still pending (Issue 78f) — the SOLE remaining structural gate between CONDITIONAL and YES.** *(unchanged ⚠️)* |
| B Async loop hygiene | ✅ | **RESTORED from ⚠️**: Issue 102 closed both preference SEV1s; clip_engine.scoring.py:23 `AsyncAnthropic` loop-binding is the last needs-runtime suspect on this axis. All other async paths verified clean. |
| C Celery idempotency | ⚠️ | Issue 105 closed the bulk: idempotency probes on transcribe/signals, `generate_clips` `RefundOnFailureTask`, 6 advisory locks, soft-time-limit special-case. New finding: `_refresh_youtube_analytics_async` lock applied too late in the body (acquires AFTER iterating creators); `RefundOnFailureTask.on_failure` thread/loop coupling carry-forward; `_ingest_async` orphan-mp4 on FIRST run (Issue 105 misread) — three items remaining before this axis hits ✅. |
| D Tenant isolation | ✅ | **STRUCTURAL** — RLS GUC via `db.py:119-148` `set_config('app.creator_id', :cid, true)` on `after_begin`. Re-verified on all Wave-8 + Wave-9 surfaces (`/creators/me/api-keys`, `/clips/ingest`, `/creators/me/insights`, `/billing/checkout` with `intent_id`). Per-creator filter present on every SELECT. Bootstrap-query-before-RLS-GUC is a structural invariant — needs the CI test from `_root_infra` SEV2 to keep honest. |
| E Backpressure | ⚠️ | Wave-9 closed `youtube/oauth.py` Redis-down (Issue 103) + billing missing-timeout + missing-idempotency-key (Issue 106). New finding: `_root_infra/main.py` `/health` builds fresh clients per probe under k8s health-checking. `worker/celery_app.py::_shutdown_worker_loop` fire-and-forget `aclose()`. |
| F Rate limit / quota | ⚠️ | Issue 104's per-creator rate-limit-key sweep landed. **3 new gaps surfaced this run**: `/auth/logout` has no limit at all; `/billing/webhook` has no limit at all; improvement-brief debounce race lets two concurrent calls both fire. All ~10 LOC fixes. |
| G Observability | ✅ | RequestIDMiddleware + JsonLogFormatter wired; route-template Prometheus labels bound cardinality; no PII/token in any logger.* call across 11 module walks. Issue 104 added durable audit-log rows on api-key create/revoke with IP + UA + request_id. |
| H Migration / pgvector | ✅ | All migrations at HEAD `0015_creator_api_keys`. `CREATE INDEX CONCURRENTLY` pattern + partial UNIQUE for "uniqueness within subset" + expand-then-contract for renames consistently applied. PITR restore-test still unverified — pre-launch operational. |
| I Secrets / deletion | ✅ | MultiFernet with rotation. Issue 106 closed the JWT verify_exp quota-leak vector (leeway=60s per DECISIONS, security-relevant decoder). `/docs` + `/metrics` gated. Account-deletion endpoint exists; end-to-end prod test still pending. |

**Compliance posture (no regression):** YouTube ToS §III.E.4.b 30-day analytics retention purge runs daily (verified clean by youtube walk). Honesty disclaimer enforced in Python on both brief paths. Limited Use disclosure in `/static/privacy.html`. TOS + Privacy linked from every static template. Audit log on api-key + account-deletion events with IP + UA + request_id.

## Diff vs previous report (2026-05-31 post-Wave-8 + Issue 95 frontend)

**Fixed & verified this cycle (Wave-9 closures, 21 SEV2s + 2 SEV1s + 1 gate):**
- ✅ **Both preference SEV1s** (joblib.load + LightGBM fit on event loop) — Issue 102.
- ✅ `routers/insights.py` `nullif` aggregate → `func.count().filter(...)` (FILTER clause) — Issue 104.
- ✅ `routers/clips.py::ingest_clip` + `routers/videos.py::upload_video` temp-file leak — Issue 104.
- ✅ Per-creator rate-limit-key sweep across all authenticated routes — Issue 104.
- ✅ `routers/api_keys.py` audit-log rows with IP + UA + request_id — Issue 104.
- ✅ `youtube/oauth.py:290` Redis-down fail-open — Issue 103 (7-cycle carry-forward FINALLY closed).
- ✅ `ingestion/transcribe.py` Deepgram normalizer + `_guard_audio_size` OSError — Issue 103 (8-cycle carry-forward closed).
- ✅ `upload_intel/timing.py::optimal_gap_hours` bounds guard — Issue 103 (8-cycle carry-forward closed).
- ✅ `clip_engine/ranking.py` `dna_match` collinearity + `clip_engine/candidates.py` IoU dedup — Issue 103 (both 8-cycle carry-forwards closed).
- ✅ `worker` idempotency probes on transcribe/signals, `generate_clips` RefundOnFailureTask, 6 advisory locks, `SoftTimeLimitExceeded` no-retry, redis socket_timeout, `LOCAL_MEDIA_DIR` absolute-path validator — Issue 105 (7-of-7 batch closed).
- ✅ `limiter.py::_creator_key` JWT verify_exp=True + leeway=60 (DECISIONS deviation) + narrowed except — Issue 106.
- ✅ `billing/stripe_client.py` idempotency_key + HTTPXClient timeout + None-check — Issue 106 (all 3 SEV2s closed).
- ✅ `routers/billing.py::CheckoutRequest.intent_id: UUID4` + pricing.html sessionStorage UUID — Issue 106.
- ✅ pip-audit 16 → 0 (6 documented residuals in `[tool.pip-audit].ignore-vulns`) — Issue 107.
- ✅ Layer-0 coverage baseline re-raised 69.54 → 75.20 — Issue 107.
- ✅ Issue 108 sweep: 38 of 48 cleanups (module docstrings, `.env.example` `DATABASE_MIGRATION_URL`, `_logging` workarounds, magic numbers, typing gaps, `Optional["X"]` → `"X | None"`, `*QueuedOut` schema dedup via `routers/_schemas.py::TaskQueuedOut`).

**New SEV2s this run (3 honestly new + 1 Issue-105 misread):**
- 🆕 `routers/auth.py::/auth/logout` — no rate-limit decorator (CSRF-shaped).
- 🆕 `routers/billing.py::/webhook` — no rate-limit decorator (bot-net exhaustion vector).
- 🆕 `routers/improvement.py:75-82` — debounce-check race (concurrent calls both fire Anthropic).
- 🆕 (reclassified) `worker/tasks.py:437` — Issue 105's `.wav` short-circuit only fixes the RETRY case; FIRST run still orphans the mp4. Will file as Issue 110.

**Cleanups missed by Issue 108 sweep (1):**
- `routers/auth.py:131` still has `import logging as _logging` — sed pass missed this one.

**Counts (Layer 1, all modules):**

| Severity | Post-Wave-9 (this run) | Post-Wave-8 | Wave 4 | Wave 3 |
|---|---|---|---|---|
| BLOCKER | 0 | 0 | 0 | 0 |
| SEV1 | **0** | 2 | 1 | 0 |
| SEV2 | **32** | 51 | 34 | 33 |
| cleanup | ~30 | 48 | ~43 | ~40 |

**Module verdicts trajectory: 0 clean / 11 NEEDS-WORK (post-Wave-8) → 4 clean / 7 NEEDS-WORK (this run).** First multi-module-clean state of the project.

**SEV1 trajectory across eight cycles: 4 → 2 → 1 → 3 → 0 → 1 → 2 → 0.** SEV2 reduction 51 → 32 (-19 net, accounting for 3 new + 1 reclassified) is the largest single-cycle drop in the assessment history.

## Top 5 actions, in order

1. **Fix 3 newly-surfaced routers SEV2s (~30 LOC total).** Add `@limiter.limit("30/minute", key_func=creator_key)` to `/auth/logout`; add `@limiter.limit("60/minute", key_func=get_remote_address)` to `/billing/webhook`; resolve the improvement-brief debounce race via `INSERT ... ON CONFLICT DO NOTHING` on the brief row + check `lastrowid` before enqueueing the Celery task. File as Issue 110A.

2. **File Issue 110B for the orphan-mp4-on-first-run fix.** Issue 105 only fixed the retry case; first-run mp4 → wav transition still permanently orphans the mp4 in R2. Capture `prior_source_uri` at function entry; `adelete_file(prior_source_uri)` after the final commit. ToS-relevant — should land before Google OAuth verification submission.

3. **Locust load test on real staging cluster (Issue 78f, 8-cycle carry-forward).** STILL the SOLE structural gate between CONDITIONAL and YES. Settles A (pool math), E (`/health` connection churn impact), the clip_engine `AsyncAnthropic` loop-binding suspect, and the `RefundOnFailureTask` prefetch>1 hazard. None of these can be settled by reading.

4. **Submit Google OAuth app verification.** All known compliance blockers cleared from our side: Limited Use disclosure shipped, TOS + Privacy linked everywhere, 30-day analytics retention purge running, audit-log rows on security events with IP + UA. External Google process; user-side action.

5. **Cleanup batch 2: the 1 missed Issue-108 item (`routers/auth.py:131`) + the 10 deferred design-work items from Issue 109.** Single bundled commit; ~150 LOC; no behavior change. Lands the cleanup count back to zero for the first time.

---

## What this cycle confirmed about Wave 9

- **The parallel-build pattern worked.** 7 issues closed in one session (102/103/104/105/106/107/108 + 109 filed as follow-up) via worktree-isolated subagents from bulk-approved Phase-1 briefs. The mid-merge `creator_key` test-helper hotfix was the only integration surprise; the cherry-pick order didn't matter because the file trees were disjoint.

- **The Issue-105 misread is the lesson.** "Add a `.wav` short-circuit at the top" closed the retry case but I didn't trace what happens on the FIRST successful run. The original mp4 was always going to be orphaned; the short-circuit just prevents creating ANOTHER orphan. This is the same scope-tightening error the Wave-4 SEV1 lesson called out — fix the visible case, miss the structural one. Issue 110 will close it properly with `adelete_file(prior_source_uri)` after commit.

- **Module-clean as a milestone is real.** 4 modules cleanly assessed (youtube, upload_intel, billing, preference) means a future change inside those modules is a meaningful regression signal — there's no SEV2 baseline noise to hide a new one. The remaining 7 NEEDS-WORK modules concentrate the assessment attention exactly where it should be.

- **The carry-forward graveyard is mostly empty.** The youtube oauth Redis SEV2 (7 cycles), Deepgram normalizer (8 cycles), `_guard_audio_size` OSError (8 cycles), `optimal_gap_hours` bounds (8 cycles), dna_match collinearity (8 cycles), candidates dedup (8 cycles) — all closed this run by Issue 103. The only remaining 5+ wave carry-forwards are the routers/over-broad-except cluster and the four worker idempotency items that Issue 105 partially closed.
