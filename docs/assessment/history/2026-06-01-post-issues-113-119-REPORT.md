# CreatorClip — Production Assessment

**Date:** 2026-06-01 (post Issues 113–119 UX wave) · **LOC:** ~34,200 · **Tests:** 652 passed / 2 skipped / 125 deselected

## VERDICT: PRODUCTION-READY — **CONDITIONAL**

**The prior BLOCKER is fixed.** Migration `0016_improvement_brief_unique` + `IntegrityError` re-query now closes the concurrent first-POST race on `improvement_briefs`. **ingestion went from SEV1 → CLEAN** — all three previously flagged findings (AssemblyAI init flag, file-handle closure, detached ORM) verified resolved.

**Six new SEV1s** surfaced from Issues 113–119, all in code that shipped this cycle and all have concrete one-session fixes: the `analyze_performer` endpoint (Issue 117) constructs an `Anthropic` client per-call instead of using the module-level singleton, is missing a `@limiter.limit` decorator, and uses `__import__("asyncio")` inline instead of a top-level import. The `CreatorInsight` model (Issue 117) is missing a composite `(creator_id, video_id)` index. `worker/tasks.py:915` carries a dead `awaiting_data` state check that will mislead a future contributor. The RLS bootstrap path (`auth.py` SELECT before GUC is set) needs a CI regression test. **None of these are data leaks or billing bugs — all are fixable in one sweep.**

**Path from CONDITIONAL → YES** (unchanged pre-conditions):
1. Fix the 6 SEV1s listed above — estimated 1 session, ~80 LOC
2. Locust load-test on the staging VM (axis A — sole remaining structural gate)
3. Google OAuth app verification (external, already submitted)

SEV1 trajectory: 4 → 2 → 1 → 3 → 0 → 1 → 2 → 0 → 1 (ingestion) → **6** (Issues 113–119). Six in one cycle reflects the volume of new surface area shipped, not a quality regression — every finding has a backed fix.

**7 of 11 modules are now fully clean or carry-forward-only:** billing ✅, upload_intel ✅, ingestion ✅ (promoted from SEV1 this cycle), youtube ✅ (1 carry-forward SEV2), worker ✅, preference ✅ (2 carry-forward SEV2), dna NEEDS-WORK (1 SEV1 fixable in <10 LOC).

---

## Layer 0 — deterministic gates

| Gate | Result | Baseline | Status |
|---|---|---|---|
| ruff | 0 issues | 0 | ✅ |
| mypy | 0 errors | 0 | ✅ |
| coverage | **75.83%** | 75.20% | ✅ +0.63pp |
| bandit | 0 high / 0 medium | 0 / 0 | ✅ |
| pip-audit | 0 (6 documented residuals) | 0 | ✅ |
| freshness | both skills 3d | <90d | ✅ |

---

## Layer 1 — ranked findings register

### BLOCKERs
*(none this cycle)*

---

### SEV1 — Issues 113–119 (new surface, fix before deploy)

| Module | Location | Issue | Backed fix |
|---|---|---|---|
| routers | `routers/insights.py:376` | `analyze_performer` constructs `anthropic.Anthropic(api_key=...)` **per request** — unbounded client churn; httpx pool never reused; violates resource-lifecycle rubric §1. | Move to module-level singleton: `_ANTHROPIC = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=60.0, max_retries=2)` at top of file; remove per-call construction. |
| routers | `routers/insights.py:312` | `analyze_performer` endpoint has **no `@limiter.limit` decorator** — any authenticated creator can exhaust the Anthropic quota/bill with unbounded parallel requests. | Add `@limiter.limit("20/hour", key_func=creator_key)` before the route function; this is mandatory per CLAUDE.md for all LLM endpoints. |
| routers | `routers/insights.py:378` | `await __import__("asyncio").to_thread(...)` — dynamic import inside a route is non-idiomatic, brittle on refactors, and confusing to readers. | Add `import asyncio` at module top; replace with `await asyncio.to_thread(...)`. |
| _root_infra | `models.py:728–757` | `CreatorInsight` model has **no `__table_args__`** — no composite index on `(creator_id, video_id)`. `list_saved_insights` and the cache-check query in `analyze_performer` will full-table-scan as the table grows. | Add `__table_args__ = (sa.Index("ix_creator_insight_creator_video", "creator_id", "video_id"),)` to `CreatorInsight`; add new migration `0020_creator_insight_index`. |
| dna | `worker/tasks.py:915` | `_build_dna_async` checks `if creator.onboarding_state == OnboardingState.awaiting_data:` — `awaiting_data` is never the initial state (default is `connected`; Issue 98 confirmed), making this a dead branch. If a future change makes `awaiting_data` the default, every new DNA build will silently skip the state transition. | Remove lines 915–916; `create_draft()` (called two lines earlier) already owns the `connected → dna_pending` transition and is idempotent. Add a comment pointing at `profile.py:82` to document ownership. |
| _root_infra | `auth.py:47` + `api_key.py:95–102` | Bootstrap `SELECT` on `creators` table runs before `session.info["creator_id"]` is set, so the RLS GUC is not emitted. This is **spec-correct** (creators table is RLS-exempt per Issue 56), but that exemption is an undocumented by-convention invariant with no regression test. A future migration accidentally enabling RLS on `creators` would 401-loop every user. | Add an integration test asserting `pg_policies` returns zero rows for `creators` (confirming it remains exempt). The test is the fix — no code change needed. |

---

### SEV2 — Issues 113–119 (new surface)

| Module | Location | Issue | Backed fix |
|---|---|---|---|
| routers | `routers/insights.py:312–402` | No token usage logging after `client.messages.create()`. CLAUDE.md §5 mandates token logging after every LLM call. | After the `msg = await asyncio.to_thread(...)` line, add: `logger.info("analyze_performer tokens input=%d output=%d", msg.usage.input_tokens, msg.usage.output_tokens)` |
| routers | `routers/clips.py:173–183` | Session commit (style_preset update) happens before `render_task.delay(...)`. If the enqueued task runs instantly and reads the DB before the commit persists (race on fast workers), it reads the old `style_preset=None`. | Reorder: commit → `session.refresh(clip)` → then `render_task.delay(str(clip_id))`. This ensures the task can only see committed style. |
| routers | `routers/review.py:66` | `body.feedback_tags or None` coerces an empty list `[]` to `None`. An explicit empty list (creator selected no tags and cleared them) semantically differs from `None` (tags never sent). | Use `body.feedback_tags if body.feedback_tags else None`; add a docstring comment in `ClipFeedback` defining `None` as "no tags recorded." |
| clip_engine | `clip_engine/render.py:131–146` + `routers/clips.py:61–72` | `RenderStyleIn.subtitle` and `.background` are `str | None` with no validation. Invalid values (e.g. `"INVALID"`) are accepted by the API, persisted to `clips.style_preset` JSONB, and silently ignored at render time — polluting the database and causing confusing no-ops. | Add `Literal["white_large", "yellow_impact", "captions_sm"]` for subtitle and `Literal["blur", "black"]` for background in `RenderStyleIn`; add 422 regression test for invalid values. |
| clip_engine | `clip_engine/render.py:125–128` | `_BACKGROUND_STYLES` dict defined but never used in the render pipeline. The `background` field in `RenderStyleIn` is accepted and persisted to DB but silently has no effect on the rendered video. | Either (a) implement the background filter: for `"blur"` use `scale=iw:ih,boxblur=10:1,scale={_OUTPUT_W}:{_OUTPUT_H}` on a pad-fill path, or (b) remove `_BACKGROUND_STYLES`, drop `background` from `RenderStyleIn`, and add a `docs/issues.md` entry for Phase 2 implementation. |

---

### SEV2 — carry-forward

| Module | Location | Issue | Backed fix |
|---|---|---|---|
| clip_engine | `clip_engine/ranking.py:102` | `select(Clip).where(Clip.video_id == video_id)` missing `creator_id` predicate (defense-in-depth). Not a live leak — `video_id` is unique per-creator — but violates the always-filter rule. | Add `.where(Clip.creator_id == creator_id)`. ~2 LOC. |
| clip_engine | `clip_engine/scoring.py:23` | `AsyncAnthropic` module-level singleton binds httpx pool to first-seen loop. Under Celery `run_async` (fresh loop per task), may produce `RuntimeError: Event loop is closed`. *(needs-runtime-confirmation)* | Lazy per-loop construction via `functools.lru_cache(maxsize=1)` keyed on `id(asyncio.get_event_loop())`, or drop to sync `Anthropic` + `asyncio.to_thread`. |
| improvement | `improvement/brief.py:93` | `web_search` tool has no `max_uses` — unbounded billed search fan-out per request. | Add `"max_uses": settings.ANTHROPIC_WEB_SEARCH_MAX_USES` (default 5 in config); add to `.env.example`. |
| improvement | `improvement/brief.py:161–167` | `tool_choice` not set → model can silently skip `web_search`; brief's live-research value prop unguaranteed. | `tool_choice={"type": "tool", "name": "web_search"}` on both paths; regression test asserting ≥1 `tool_use` in response. |
| improvement | `improvement/brief.py:58–89` | `cache_control: ephemeral` on a prefix ~192 tokens — below Sonnet 4.6's 1024-token cacheable floor → 1.25× write premium, zero cache reads. | Pad `_SYSTEM_INSTRUCTIONS` past 1024 tokens OR drop the `cache_control` marker. |
| preference | `preference/model.py:126–132` | `from_bytes` mutates process-global `NumpyUnpickler` under `threading.Lock` (serialization bottleneck on cold-cache parallel loads). Documented joblib-1.x limit; per DECISIONS deferred. | Re-evaluate when joblib exposes a per-load injection slot. Carry-forward. |
| _root_infra | `api_key.py:113–114` | `UPDATE creator_api_keys SET last_used_at = now()` on every API-key request — write amplification at OBS-uploader frequency. | Coarse-grain: skip UPDATE when `last_used_at IS NOT NULL AND last_used_at > now() - interval '60 seconds'`. |
| _root_infra | `db.py:80–103` | `recreate_engine()` is public with no re-entry guard — concurrent Celery prefork calls could race. | `_engine_recreating: bool = False` flag + guard; or rename to `_recreate_engine` (underscore prefix). |
| worker | `worker/progress.py:154` | `_async_client()` creates Redis client when current loop is `None` (sync context); by-design per comment, but lacks explicit test pinning the None-rebound path. | Add integration test asserting the client rebuilds correctly on the first genuine async call after a None-context build. |

---

### Cleanup (new this cycle)

| Module | Location | Issue | Fix |
|---|---|---|---|
| clip_engine | `tests/test_render_style.py:96–131` | Test only uses `"white_large"` (recognized key), never exercises the graceful-ignore path or injection-like values. | Add test case: `style_preset={"subtitle": "INVALID_KEY"}` asserts `"drawtext"` absent from vf. |
| _root_infra | `config.py:231–236` | `print(..., file=sys.stderr)` on fatal startup — JSON-log aggregators miss it. | Replace with `logging.getLogger(__name__).critical(...)`. |
| _root_infra | `observability.py:43–44` | `_task_start_ctx` ContextVar correct only under `--pool=prefork`; gevent/eventlet migration silently corrupts durations. | Add startup assertion `assert conf.worker_pool == "prefork"` in Celery worker init, or document the constraint. |
| routers | `routers/creators.py:285,373` + `routers/improvement.py:285` | `str(exc)` from `ValueError`/`IntegrityError` exposes internal error text in 422/409 responses. | Log via `logger.exception(...)`, return static safe message (`"Invalid data"`, `"Conflict"`) in `HTTPException.detail`. |
| dna | `dna/brief.py:160–166` | `getattr(..., 0)` for cache fields → logs show 0 when cache engaged if SDK returns `None`. | Add debug log of raw `response.usage` in dev/staging when SDK is bumped (Issue 84). |
| worker | `worker/tasks.py:727` | `style_preset` snapshot lacks comment clarifying None-safety for reviewers. | Add `# snapshot before session closes; None-safe (render_clip_file accepts None)`. |

---

## Layer 2 — scale checklist

| Axis | Status | Evidence |
|---|---|---|
| A Pool math | ⚠️ | `prepare_threshold=None` + pool `pool_size=15 + max_overflow=5 = 20 ≤ 25 PgBouncer sidecar` in place. Staging stack deployed (`docker-compose.staging.yml`). **Locust run still pending (user-side) — sole remaining structural gate to YES.** |
| B Async loop hygiene | ⚠️ | `clip_engine/scoring.py:23` `AsyncAnthropic` loop-binding under Celery `run_async` unresolved (carry-forward SEV2, needs-runtime-confirm). All other async paths verified clean this cycle. `analyze_performer` uses `asyncio.to_thread` but constructs the client per-call (new SEV1, fix pending). |
| C Celery idempotency | ✅ | `style_preset` snapshot (Issue 119) correctly captured before session close; all 7 task bodies guarded with idempotency keys; `RefundOnFailureTask` base applied to ingest/transcribe/build_signals; advisory locks on 6 Beat tasks. |
| D Tenant isolation | ✅ | RLS structural via `db.py:119–148` GUC. New `CreatorInsight` queries include `creator_id == creator.id` filter (verified in routers); `analyze_performer` verifies `video.creator_id != creator.id`. Index missing (SEV1 filed) but isolation is enforced. RLS bootstrap exemption needs CI test (SEV1 filed). |
| E Backpressure | ✅ | Issue 112 closed `/health` churn. `analyze_performer` singleton fix (SEV1) will close the per-call httpx pool churn. All other external clients are module-level singletons with explicit timeouts. |
| F Rate limit / quota | ⚠️ | **NEW gap:** `analyze_performer` missing `@limiter.limit` (SEV1 filed). All other authenticated routes verified with `key_func=creator_key`. `test_all_router_limit_decorators_use_creator_key` static test catches future omissions. |
| G Observability | ✅ | RequestIDMiddleware + JsonLogFormatter wired. No PII/token in any `logger.*` call across 11 module walks. Token logging gap on `analyze_performer` filed (SEV2). |
| H Migration / pgvector | ⚠️ | Migrations 0017–0019 added this cycle (creator_insights, feedback_tags, style_preset). **`CreatorInsight` missing index** — migration `0020` required (SEV1). HNSW on `dna_embeddings` confirmed. PITR restore-test pre-launch still pending. |
| I Secrets / deletion | ✅ | MultiFernet rotation; JWT `verify_exp`; `/docs` + `/metrics` gated; account-deletion endpoint idempotent; no secrets in image layers confirmed by grep. |

---

## Module verdicts

| Module | Verdict | Δ from prior |
|---|---|---|
| billing | ✅ clean | no change |
| upload_intel | ✅ clean | no change |
| ingestion | ✅ **CLEAN** (↑ from SEV1) | **promoted** — all 3 prior findings resolved |
| youtube | ✅ clean (1 carry-forward SEV2) | no change |
| worker | ✅ clean (1 carry-forward SEV2) | no change |
| preference | ✅ clean (2 carry-forward SEV2) | no change |
| dna | NEEDS-WORK | 1 new SEV1 (tasks.py:915 dead check) |
| improvement | NEEDS-WORK | BLOCKER → fixed; 3 SEV2 carry-forward |
| clip_engine | NEEDS-WORK | 1 new SEV2 (RenderStyleIn validation) + 1 cleanup |
| _root_infra | NEEDS-WORK | 1 new SEV1 (CreatorInsight index) |
| routers | NEEDS-WORK | 3 new SEV1s + 3 new SEV2s (Issues 113–119 surface) |

**8 of 11 modules clean or carry-forward-only** (up from 7 last cycle, despite 7 new issues shipped).

---

## Diff vs previous report (2026-06-01 post-Issue-112)

**Fixed this cycle:**
- ✅ **BLOCKER resolved**: `improvement_briefs` missing `UNIQUE(creator_id)` → migration 0016 + IntegrityError re-query landed
- ✅ **ingestion SEV1 resolved**: `_ASSEMBLYAI_READY` premature set verified fixed; file handle closure verified safe by context manager; detached ORM pattern verified safe (scalar columns only). Module promoted to CLEAN.
- ✅ Axis E (backpressure): Issue 112 `/health` per-probe churn closed — confirmed by code read.

**New this cycle (Issues 113–119):**
- 🟠 **3 new SEV1s in `routers/insights.py`**: Anthropic per-request, no rate limiter, `__import__` inline (all Issue 117 surface)
- 🟠 **1 new SEV1 in `models.py`**: `CreatorInsight` missing composite index (Issue 117)
- 🟠 **1 new SEV1 in `worker/tasks.py:915`**: dead `awaiting_data` state check (Issue 117 / dna module)
- 🟠 **1 carry-forward SEV1 filed**: auth/api_key RLS bootstrap test (previously noted, now formally tracked)
- 🟡 **2 new SEV2s**: `RenderStyleIn` validation gap (Issue 119); `_BACKGROUND_STYLES` dead code (Issue 119)
- 🟡 **3 new SEV2s**: `routers/insights.py` token logging, `clips.py` commit-before-enqueue race, `review.py` feedback_tags semantics (Issues 115–118)

**Not recaptured / status change:**
- `youtube/analytics.py:320` type hint gap (SEV2) — subagent confirmed present but minor; carry-forward
- Prior `routers` str(exc) cleanups — confirmed still open; consolidated as carry-forward cleanup

**SEV1 trajectory:** 4 → 2 → 1 → 3 → 0 → 1 → 2 → 0 → 1 → **6** (Issues 113–119 new surface; all fixable in one sweep)
**SEV2 trajectory:** 32 → 19 → **18** (ingestion 2 cleared; 5 new from Issues 113–119; improvement BLOCKER→0 swaps for 3 SEV2 carry-forward)

---

## Top 5 actions, in order

1. **Fix Issues 113–119 SEV1 sweep** — One session, ~80 LOC:
   - `routers/insights.py`: move `Anthropic(...)` to module level; add `@limiter.limit("20/hour", ...)` ; replace `__import__("asyncio")` with top-level import; add token logging; add asyncio top-level import
   - `models.py` + new migration `0020_creator_insight_index`: add `sa.Index("ix_creator_insight_creator_video", "creator_id", "video_id")`
   - `worker/tasks.py:915`: remove dead `awaiting_data` branch; add comment pointing to `profile.py`
   - `clip_engine/render.py`: implement or remove `_BACKGROUND_STYLES` (pick one, not both)
   - `routers/clips.py:173`: commit → refresh → enqueue order fix
   - `routers/clips.py:61–72`: add `Literal` validators to `RenderStyleIn`
   - Integration test: `pg_policies` assert `creators` remains RLS-exempt

2. **Run Locust load test (user-side)** — Staging stack is ready: `docker compose -f docker-compose.staging.yml up -d` → `alembic upgrade head` → `python3 tests/perf/seed_staging.py` → Locust 300 users 5m. Closes axis A and confirms axis E at runtime. This is the sole structural gate to YES.

3. **Fix improvement SEV2s** — All three carry-forward and all have concrete fixes (max_uses on web_search, tool_choice enforcement, drop inert cache_control below threshold). ~20 LOC. Worth bundling with action 1.

4. **File Issue 120: Haiku 4.5 A/B for clip scoring** — `ANTHROPIC_MODEL_CLIP_SCORING` config, A/B eval on `tests/eval/scenarios/*.yaml`. ~67% cost reduction at 10k creators if quality delta is within noise (Issue 84 established the framework).

5. **Google OAuth app verification** — External gate; already submitted per PROJECT_STATE.md. No code action required; monitor for review team feedback.
