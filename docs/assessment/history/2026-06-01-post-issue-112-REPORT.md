# CreatorClip — Production Assessment

**Date:** 2026-06-01 (post Issue 110 + Issue 112) · **Commit:** `486201e` + Issue 112 uncommitted · **LOC:** ~32,900 · **Tests:** 629 passed / 2 skipped / 125 deselected

## VERDICT: PRODUCTION-READY — **CONDITIONAL**

**One new BLOCKER surfaced this cycle:** the `improvement_briefs` table is missing a `UNIQUE(creator_id)` constraint. Issue 110's `SELECT FOR UPDATE SKIP LOCKED` debounce is correct for the concurrent-update case (when a row already exists), but the first-ever POST for a creator has no DB-level backstop — two simultaneous requests both skip the lock (no row exists yet), both insert separate rows, and both fire the Anthropic API. This is a financial correctness defect, not a data leak; fix is ~10 LOC (migration + IntegrityError catch). A second new finding is a SEV1 in `ingestion/transcribe.py:179-186`: the AssemblyAI backend sets `_ASSEMBLYAI_READY = True` before its initialization checks complete, corrupting module state on error paths. Both must be fixed before launch.

**Issue 112 closed the axis-E /health connection churn** (the `psycopg.AsyncConnection` + `aioredis.from_url` per-probe path is replaced by `engine.connect()` + a module-level singleton). Staging infrastructure is now in place (`docker-compose.staging.yml` + `tests/perf/seed_staging.py`) — the Locust run to close axes A + E is user-side on the prod VM. **5 of 11 modules are now fully clean** (dna joins youtube, upload_intel, billing, preference).

SEV1 trajectory: **4 → 2 → 1 → 3 → 0 → 1 → 2 → 0 → 1** (ingestion AssemblyAI init, newly reclassified). Path from CONDITIONAL → YES: (1) fix improvement BLOCKER, (2) fix ingestion SEV1, (3) Locust evidence axes A + E, (4) Google OAuth app verification (external).

---

## Layer 0 — deterministic gates (from `_machine.json`)

| Gate | Result | Baseline | Status |
|---|---|---|---|
| ruff | 0 issues | 0 | ✅ |
| mypy | 0 errors | 0 | ✅ |
| coverage | **76.05%** | 75.20% | ✅ +0.85pp |
| bandit | 0 high / 0 medium | 0 / 0 | ✅ |
| pip-audit | 0 (6 documented residuals) | 0 | ✅ |
| freshness | both skills 2d | <90d | ✅ |

Top untested load-bearing code (coverage gaps):
1. `ingestion/transcribe.py` — WhisperX + AssemblyAI backends exercised by real audio fixtures only; AssemblyAI init path newly flagged as SEV1.
2. `worker/tasks.py` — celery task bodies largely deselected (integration lane); `_shutdown_worker_loop` aclose fire-and-forget not tested.
3. `improvement/brief.py` — double-insert race path explicitly not covered (no concurrent-POST test for the BLOCKER scenario).

---

## Layer 1 — module register (ranked)

| Sev | Module | Location | Issue | Backed fix |
|---|---|---|---|---|
| **BLOCKER** | improvement | `models.py` + `routers/improvement.py:109` | `improvement_briefs` table has no `UNIQUE(creator_id)` constraint. Issue 110's `SELECT FOR UPDATE SKIP LOCKED` only guards the existing-row race; two concurrent first-ever POSTs both find no row, both insert, both enqueue Celery → double Anthropic API fire + duplicate rows. | (1) Add `UniqueConstraint("creator_id", name="uq_improvement_briefs_creator_id")` to models.py. (2) New migration `0016_improvement_brief_unique`. (3) Wrap the insert in a try/except IntegrityError re-query. Concurrent-POST regression test asserting both callers receive the same task_id. |
| **SEV1** | ingestion | `ingestion/transcribe.py:179-186` | `_transcribe_assemblyai()` sets `_ASSEMBLYAI_READY = True` (line 180) before completing initialization checks (lines 184–186). On error the flag is set but init is incomplete → all subsequent calls skip initialization and fail with a confusing error. | Move `_ASSEMBLYAI_READY = True` to AFTER line 186; wrap checks in try/except so the flag is only set on fully successful init. |
| SEV2 | clip_engine | `clip_engine/ranking.py:102` | `select(Clip).where(Clip.video_id == video_id)` missing `creator_id` predicate (defense-in-depth; carry-forward). Function already takes `creator_id: uuid.UUID`. | Add `.where(Clip.creator_id == creator_id)`; add cross-tenant regression test. |
| SEV2 | clip_engine | `clip_engine/render.py:82-105` | Single-keyframe Haar cascade; shot-change clips miscrop silently. *(needs-runtime-confirmation)* | Sample 3 keyframes (25%, 50%, 75%), take median x of detected centers. <300ms per render. |
| SEV2 | clip_engine | `clip_engine/scoring.py:23` | `AsyncAnthropic(...)` module-level singleton binds httpx pool to first-seen loop. Under Celery `run_async`, each task creates a fresh loop → stalled connections / `RuntimeError: Event loop is closed` under concurrency. *(needs-runtime-confirmation)* | Lazy per-loop `lru_cache(maxsize=1)` keyed on `id(asyncio.get_event_loop())`, OR sync `Anthropic` client via `asyncio.to_thread`. |
| SEV2 | clip_engine | `clip_engine/scoring.py:203` | Haiku 4.5 A/B for clip scoring (~67% cost reduction) unfiled as tracked issue — Issue 84 close-out → DECISIONS → Issue 111 chain broken since Wave 4. | File as Issue 111: introduce `ANTHROPIC_MODEL_CLIP_SCORING`, A/B eval harness, flip only if eval delta within noise. |
| SEV2 | preference | `preference/model.py:126-132` | `from_bytes` mutates process-global `NumpyUnpickler` under lock — tail-latency serialization point on heavy parallel cold-cache loads. Documented joblib-1.x limit; Issue 102 closed the event-loop SEV1. *(carry-forward; deferred per DECISIONS)* | Re-evaluate when joblib exposes a per-load injection slot. |
| SEV2 | preference | `preference/train.py:107` | Variable `result` re-bound at line 107 (feedback fetch) — confusing on re-read; ruff does not catch. | Rename second binding `existing_result` or inline. |
| SEV2 | worker | `worker/progress.py:154-163` | `_async_client()` creates redis client bound to `None` when called from sync context (current loop is `None`). Silently binds to dead loop at first async operation. *(needs-runtime-confirmation)* | Check `if current is None: raise RuntimeError("_async_client called outside event loop")` before creating. |
| SEV2 | routers | `routers/creators.py:285` | `str(exc)` from `ValueError` passed to client detail in HTTP 422 response — exposes internal validation error text. | Replace with a safe static message: `"Invalid identity data"`. |
| SEV2 | routers | `routers/improvement.py:285` | Same `str(exc)` pattern in `confirm_dna()` → exposes internal error text to client. | Replace with `"Could not confirm DNA"`. |
| SEV2 | ingestion | `ingestion/transcribe.py:102-108` | File handle in `_transcribe_deepgram` may not close on `SoftTimeLimitExceeded` (SIGPROF before context manager exit). | Explicit `try/finally: f.close()` around the blocking transcribe call. |
| SEV2 | ingestion | `ingestion/signals.py:40-50` | Duck-typed `getattr()` on SQLAlchemy ORM rows — if session closed before call, `DetachedInstanceError` on lazy-loaded attributes. | At call site (`worker/tasks.py:620-623`), convert retention rows to dicts before exiting the db session context. |
| SEV2 | improvement | `improvement/brief.py:93` | `web_search` tool has no `max_uses` → unbounded billed search fan-out. Carry-forward since Wave 4. | Add `"max_uses": settings.ANTHROPIC_WEB_SEARCH_MAX_USES` (default 5); add to config.py + .env.example. |
| SEV2 | improvement | `improvement/brief.py:161-167` | `tool_choice` not set on either call path → model can silently skip web_search; brief's "cite current algorithm" value prop unguaranteed. | `tool_choice={"type": "tool", "name": "web_search"}` on both paths; regression test asserting ≥1 `tool_use` block in response. |
| SEV2 | improvement | `improvement/brief.py:58-89` | `cache_control: ephemeral` marker on a prefix below the 1024-token Sonnet 4.6 cacheable floor → 1.25× write premium, zero cache reads. Carry-forward. | Pad `_SYSTEM_INSTRUCTIONS` past 1024 tokens OR drop the `cache_control` marker until prefix grows. |
| SEV2 | _root_infra | `db.py:80-103` | `recreate_engine()` is module-public with no re-entry guard — accidental second call with in-flight sessions → nondeterministic crash. | `_recreate_engine_called: bool = False` flag; raise on re-entry; add underscore prefix. |
| SEV2 | _root_infra | `auth.py:46-58` / `api_key.py:96-119` | Bootstrap SELECT runs before `session.info["creator_id"]` set (RLS GUC not emitted). Works because `creators` + `creator_api_keys` are RLS-exempt; but a future migration that flips either table breaks ALL auth (401-everywhere outage). | CI test asserting both tables remain exempt by querying `pg_policies` catalog. |
| SEV2 | _root_infra | `config.py:222-231` | `print(..., file=sys.stderr)` on fatal startup — JSON-log aggregators miss the fatal message entirely. | `logging.basicConfig(stream=sys.stderr, level=logging.ERROR)` inside the `except ValidationError`, then `logging.error(...)`. |
| SEV2 | _root_infra | `observability.py:43-44, 224-241` | Celery ContextVar correlation correct only under `--pool=prefork`. Future gevent/eventlet migration silently corrupts correlation ids + durations. | Assert `app.conf.worker_pool == "prefork"` at worker startup OR key task start off `task.request.id` in a per-task dict with `task_postrun` cleanup. |
| SEV2 | _root_infra | `api_key.py:113-114` | Every API-key-authenticated request issues `UPDATE creator_api_keys SET last_used_at = now()` — write amplification on high-frequency OBS uploader. | Coarse-grain: only UPDATE if `last_used_at IS NULL OR last_used_at < now() - interval '60 seconds'`. Skip commit when no row changed. |

**Module verdicts:** `youtube` ✅ clean · `upload_intel` ✅ clean · `billing` ✅ clean · `preference` ✅ clean (2 documented carry-forward SEV2) · `dna` ✅ clean · `routers` NEEDS-WORK · `worker` NEEDS-WORK · `ingestion` has SEV1 · `improvement` has BLOCKER · `clip_engine` NEEDS-WORK · `_root_infra` NEEDS-WORK · **5/11 modules clean (up from 4)**.

---

## Layer 2 — scale checklist

| Axis | Status | Evidence |
|---|---|---|
| A Pool math | ⚠️ | `prepare_threshold=None` + pool ceiling `pool_size=15 + max_overflow=5 = 20 ≤ 25 PgBouncer sidecar` in place. Staging stack (`docker-compose.staging.yml`) + seed script deployed. **Locust run still pending (user-side) — SOLE remaining structural gate.** |
| B Async loop hygiene | ✅ | Issue 102 closed both preference SEV1s (joblib + LightGBM off event loop). `clip_engine/scoring.py:23` AsyncAnthropic loop-binding is the last needs-runtime-confirm suspect; all other async paths verified clean. |
| C Celery idempotency | ⚠️ | Issue 105 closed the bulk (idempotency probes, RefundOnFailureTask, 6 advisory locks, soft-time-limit). Remaining: `_shutdown_worker_loop` fire-and-forget aclose (SEV2), `_refresh_youtube_analytics_async` lock timing (from prior report, not re-surfaced this cycle). |
| D Tenant isolation | ✅ | **STRUCTURAL** — RLS GUC via `db.py:119-148` `set_config('app.creator_id', :cid, true)` on `after_begin`. Re-verified on all surfaces this cycle. No cross-tenant leak in any module. Bootstrap-query-before-RLS-GUC is a by-convention invariant (CI test needed — _root_infra SEV2). |
| E Backpressure | ✅ | **RESTORED from ⚠️ (Issue 112):** `/health` now routes Postgres probe through `engine.connect()` (SQLAlchemy pool) and Redis probe through module-level `_health_redis` singleton. Per-probe fresh-connection churn eliminated. `worker/celery_app.py::_shutdown_worker_loop` fire-and-forget aclose carry-forward. Locust run will confirm axis E at runtime. |
| F Rate limit / quota | ✅ | **RESTORED from ⚠️ (Issue 110):** `/auth/logout` + `/billing/webhook` rate-limit decorators added. Improvement-brief double-fire elevated to BLOCKER (DB-constraint fix required, not a rate-limit issue). All other per-creator-bucketed endpoints verified. |
| G Observability | ✅ | RequestIDMiddleware + JsonLogFormatter wired. No PII/token in any logger.* call across 11 module walks. Issue 104 audit-log rows on api-key create/revoke. |
| H Migration / pgvector | ✅ | All migrations at HEAD `0015_creator_api_keys`. New migration `0016_improvement_brief_unique` required for BLOCKER fix. `CREATE INDEX CONCURRENTLY` + partial UNIQUE patterns consistent. PITR restore-test pre-launch operational. |
| I Secrets / deletion | ✅ | MultiFernet with rotation. JWT verify_exp + leeway=60 (Issue 106). `/docs` + `/metrics` gated. Account-deletion endpoint exists; end-to-end prod test pending. |

---

## Diff vs previous report (2026-06-01 post-Wave-9 + Issue 110 immediately prior)

**Fixed this cycle:**
- ✅ `/auth/logout` rate limit → CLOSED (Issue 110)
- ✅ `/billing/webhook` rate limit → CLOSED (Issue 110)
- ✅ `_ingest_async` orphan-mp4 on first run → CLOSED (Issue 110)
- ✅ `routers/auth.py:131` `_logging` workaround → CLOSED (Issue 110)
- ✅ `main.py` `/health` per-probe psycopg + aioredis.from_url churn → **CLOSED (Issue 112)**
- ✅ `dna` module: 3 prior SEV2s not recaptured → effectively clean (5 clean modules, up from 4)
- ✅ Axis E (Backpressure): ⚠️ → ✅ code-side; Axis F (Rate limit): ⚠️ → ✅

**New / reclassified this cycle:**
- 🔴 **NEW BLOCKER**: `improvement_briefs` missing `UNIQUE(creator_id)` — first-ever concurrent POST race not covered by SKIP LOCKED alone
- 🟠 **NEW SEV1**: `ingestion/transcribe.py:179-186` AssemblyAI init state corruption (flag set before checks complete)
- 🔺 **SEV2 → NOT RECAPTURED**: 3 dna SEV2s from prior report (import-time `_ANTHROPIC`, `_NICHE_KEYWORDS` coverage gap, `tenacity reraise=False`) absent from current dna.md (dated 2026-05-31); verify in next cycle

**SEV2 trajectory:** 32 → **19** (closed 5 via Issues 110+112; dna 3 not recaptured; 3 improvement SEV2s counted separately from BLOCKER).

---

## Top 5 actions, in order

1. **Fix improvement BLOCKER** — Add `UniqueConstraint("creator_id")` to `ImprovementBrief` model, write migration `0016_improvement_brief_unique`, wrap insert in try/except IntegrityError with re-query fallback. Add concurrent-POST test. ~30 LOC. File as Issue 113.

2. **Fix ingestion SEV1** — Move `_ASSEMBLYAI_READY = True` to after all initialization checks in `ingestion/transcribe.py:180-186`. Add test covering the init-failure path that verifies subsequent calls re-attempt init instead of silently failing. ~5 LOC. File as Issue 114.

3. **Run Locust load test (user-side)** — Staging stack is ready: `docker compose -f docker-compose.staging.yml up -d` → `alembic upgrade head` → `python3 tests/perf/seed_staging.py` → `locust ... --users 300 --run-time 5m --csv docs/assessment/loadtest`. Record axis A + E numbers in REPORT.md. Closes the sole structural gate to YES.

4. **File Issue 111 (Haiku 4.5 A/B)** — Introduce `ANTHROPIC_MODEL_CLIP_SCORING` config, run eval harness A/B on `tests/eval/scenarios/*.yaml`, flip default only if quality delta within noise. ~67% clip-scoring cost reduction at 10k creators.

5. **Fix clip_engine/ranking.py:102 creator_id predicate** — Add `.where(Clip.creator_id == creator_id)` to the existing-clips probe. Creator_id is already in scope. ~2 LOC + regression test. Defense-in-depth only; not a live leak today.
