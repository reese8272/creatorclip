# CreatorClip — Production Assessment

**Date:** 2026-06-08 (UX-focus run)  ·  **Commit:** `d398cff`  ·  **LOC:** ~55,150 (incl. static/tests)  ·  **Tests:** 942 collected (126 deselected)

## VERDICT: PRODUCTION-READY — **CONDITIONAL**

Big positive direction: **all 6 SEV1s from the 2026-06-07 run are FIXED** (3 worker idempotency/RLS, 2 knowledge cache-floor, 1 youtube caller-session) and the cross-cutting routers `task.delay` axis-B SEV2 is RESOLVED. **One new BLOCKER** surfaced this cycle in the frontend (`index.html` runaway poll loop with no stop condition → battery drain + persistent baseline server load) and **one new SEV1** in billing (webhook idempotency fast-path is dead under RLS — masked today by `grant_minutes` IntegrityError catch). Conditional on (a) the frontend poll-loop cap, (b) the billing webhook RLS-context fix, (c) the routers UX shape changes (empty-state contracts, error `action_type`, aggregated onboarding state) that directly address the user-flagged "barren / hard to know how to use" concern, and (d) the still-deferred Locust 300-user run.

---

## Layer 0 — deterministic gates (from `_machine.json`)

| Gate | Result | Baseline | Status |
|---|---|---|---|
| ruff | 0 issues | 0 | ✅ |
| mypy | 0 errors | 0 | ✅ |
| coverage | 75.42 % | 75.20 % | ✅ (+0.22) |
| bandit | high 0 / med 0 | high 0 / med 0 | ✅ |
| pip-audit | 0 vulns | 0 | ✅ |
| freshness | 10 / 10 days | 90-day threshold | ✅ |

**Top uncovered load-bearing paths (carry-forward + this run):**
1. `clip_engine/render.py::render_cleaned_clip_file` — ffmpeg filter_complex assembled in code, but the shell-out is mocked in tests; first prod render is still the real signal.
2. `worker/tasks.py` `_clean_clip_async` + `_edit_clip_async` R2-upload + cleanup path — shell-mocked.
3. `static/editor.js` lines 200+ (~200 LOC after `getSelection()` boundary walker) — no JS test harness in the project; first real-browser session is the integration signal. (The static_frontend subagent flagged that the file's tail wasn't fully read in its single pass — a known coverage gap on the orchestrator side.)
4. `static/index.html` JS polling loops (`_pollTimer` block) — no JS test coverage; the new BLOCKER lives here.

---

## Layer 1 — module register (ranked)

**Tally across 14 modules: 1 BLOCKER · 1 SEV1 · 61 SEV2 · 60 cleanup**

| Sev | Module | Location | Issue | Backed fix |
|---|---|---|---|---|
| **BLOCKER** | static_frontend | `static/index.html:769–770` | `_pollTimer = setInterval(...)` polls `/videos` every 5s with **no stop condition** other than "all videos done or failed". A stalled-pending video → forever-polling client. On a dashboard left open 24/7 this is a battery drain on the creator side AND a persistent 0.2 RPS/creator baseline on the server (200 creators × 0.2 = 40 RPS continuous overhead before real work). | (1) Cap at 120 polls (10 min) then surface a "video appears stuck — refresh" warning; (2) exponential backoff after the first 3 unchanged ticks (5s → 10s → 20s); (3) stop polling when the tab loses focus (`document.visibilityState === "hidden"`) and resume on visible. |
| **SEV1** | billing | `routers/billing.py:203` | Webhook idempotency fast-path query runs on an app-role session but never stamps `session.info["creator_id"]`. Under Issue 79 RLS the policy returns 0 rows always → the intended short-circuit NEVER FIRES. Integrity holds today only because `grant_minutes()` catches the UNIQUE-constraint `IntegrityError`; any future change to that catch-and-noop turns this into a real double-fulfillment money bug. Same class of omission the worker async helpers shipped a fix for this cycle. | Parse `creator_id` from webhook metadata first, then `session.info["creator_id"] = str(creator_id)` BEFORE the idempotency query. Add a regression test: fire the same webhook twice; assert the second call returns `already_fulfilled` from the fast path (today the test passes via `grant_minutes`' catch — masks the bug). |
| SEV2-top | static_frontend | `index.html:276–315` + `auth.js:18–26` | The pre-auth hero block (the "Get clips →" form + clip preview mockup that EXPLAINS what the product does) is only visible when `body.is-hero-mode` is set by `auth.js` on a `/auth/me` 401. Authenticated users with no videos / no DNA see an **empty dashboard with no CTA** — this is the literal "barren / not easy to know how to use" complaint. The hero copy never reaches the user who is most likely to be confused (first authenticated visit). | (1) Render a first-run empty-dashboard state with the same CTA copy as the pre-auth hero, gated on `onboarding_state === "connected"` AND `video_count === 0`. (2) Improve 401-fallback copy to "Sign in to get started →" instead of an orphaned form. (3) Verify the `?yt=` hint auto-fill survives the OAuth redirect. |
| SEV2-top | routers | `creators.py:22-32` + 4 endpoints | `onboarding_state` is good but the frontend has to call 5 endpoints (`/me`, `/data-gate`, `/dna`, `/identity`, `/balance`) to know the EXACT NEXT STEP. State-machine inference is distributed → frontend can't render a single "Step 2 of 5: ingest videos" indicator. | Extend `CreatorMeOut` with derived `setup_step: int`, `setup_step_label: str`, `next_action_type: str \| None`, OR add `/creators/me/setup-status` aggregator. ~30 LOC; directly addresses the discoverability gap. |
| SEV2-top | routers | `videos.py:66-100`, `insights.py:620-640`, `clips.py:130-149` | 3 list endpoints return bare `[]` or `{"clips": []}` with no `message` / `state` field. Frontend cannot distinguish "no videos yet" from "loading" from "ingested but no clips passed threshold" → silent empty UI. | Mirror the `DnaGetOut` pattern (`profile` + `message`): wrap each list response in `{items: [...], state: "no_candidates"\|"not_ingested"\|"ready", message: str \| None}`. ~3 Pydantic models. |
| SEV2-top | routers | `clips.py:101-105`, `analysis.py:83` | 400/404 detail strings are human-readable but not machine-actionable. Frontend can't render generic "wait 30s for ingestion" or "Connect YouTube →" without hardcoding the strings. | Add `action_type: str \| None` and `action_url: str \| None` to all 4xx response shapes. Centralize via middleware that decorates `HTTPException.detail` into a structured body. |
| SEV2 | worker | 11 findings across `tasks.py` | Advisory lock placement issues, retry-loop edge cases, connection-pool sizing for long-running jobs — all bounded blast, structural across 6 task functions. The 3 PRIOR SEV1s (RLS-blind helpers + clean/edit shared idempotency key) are now FIXED. | See `docs/assessment/modules/worker.md` (11 items, each with a fix). |
| SEV2 | clip_engine | 7 findings | Libass path-escaping unfixed; forward-snap duration-clamping; JSON response fragility; async-client loop binding; transcript-segment word dedup; event-overlap semantics. All carry-forward from 2026-06-07. | See `docs/assessment/modules/clip_engine.md`. |
| SEV2 | static_frontend | 6 more (queue button mode, queueing-error state, input-not-cleared, editor.js untread, race in `_registerInFlightIngests`, EventSource pool) | Carry-forward UX gaps that compound the "barren" feel. | See `docs/assessment/modules/static_frontend.md`. |
| SEV2 | _root_infra | 6 findings | `crypto.py:13` `_fernet()` rebuilds MultiFernet every call (singleton-rule miss on per-request decrypt path); `api_key.py:107,113` 2-query lookup + per-request `last_used_at` UPDATE on hot path; `db.py:80` un-locked bool guard; `config.py:262,267` `print()` over `logger`. | See module file. Hot-path SEV2s; compound at scale. |
| SEV2 | dna | 5 findings | `_optimal_upload_gap_h` doesn't wrap the week (Sat-Sun adjacency bias) — top. | See `dna.md`. |
| SEV2 | youtube | 4 findings | Quota consumed per-CALL instead of per-RETRY → local counter under-reports real Google budget; the `_do_token_refresh` SEV1 from 2026-06-07 is FIXED. | See `youtube.md`. |
| SEV2 | knowledge | 4 findings | Both cache-floor SEV1s are FIXED; hardcoded Haiku model strings remain (blocks per-call-site model swap). | See `knowledge.md`. |
| SEV2 | ingestion | 4 findings | Per-clip-peak RMS normalization unanchored to dBFS — top; carry-forward. | See `ingestion.md`. |
| SEV2 | analysis | 4 findings | Untyped tuple return on `_build_request`; cache_control inert (same pattern, already in DECISIONS). | See `analysis.md`. |
| SEV2 | improvement | 3 findings | Untyped tuple, missing empty-text guard on streaming, dead 60s timeout. | See `improvement.md`. |
| Cleanup | (60 items) | various | Typing, docstring drift, JSDoc, walkthrough-page nav, skeleton-loader, enum→user-label helper, refund follow-up. | Per module file. |

**Module verdicts:**

| Module | Verdict | B | S1 | S2 | C |
|---|---|---|---|---|---|
| **static_frontend** *(NEW slice)* | **NEEDS-WORK** | **1** | 0 | 7 | 12 |
| billing | **NEEDS-WORK** | 0 | **1** | 1 | 3 |
| worker | NEEDS-WORK *(3 SEV1s fixed)* | 0 | 0 | 11 | 1 |
| clip_engine | NEEDS-WORK | 0 | 0 | 7 | 5 |
| _root_infra | NEEDS-WORK | 0 | 0 | 6 | 4 |
| dna | NEEDS-WORK | 0 | 0 | 5 | 6 |
| routers | NEEDS-WORK *(axis-B RESOLVED, +5 UX)* | 0 | 0 | 5 | 4 |
| ingestion | NEEDS-WORK | 0 | 0 | 4 | 3 |
| knowledge | NEEDS-WORK *(2 SEV1s fixed)* | 0 | 0 | 4 | 6 |
| youtube | NEEDS-WORK *(SEV1 fixed)* | 0 | 0 | 4 | 3 |
| analysis | NEEDS-WORK | 0 | 0 | 4 | 3 |
| improvement | NEEDS-WORK | 0 | 0 | 3 | 2 |
| **preference** | **clean** | 0 | 0 | 0 | 4 |
| **upload_intel** | **clean** | 0 | 0 | 0 | 4 |

---

## Layer 2 — scale checklist

| Axis | Status | Evidence |
|---|---|---|
| A Pool math | ⚠️ | PgBouncer-staging stack built (Issue 112); _root_infra confirms pool math matches `docs/DEPLOYMENT.md` (15+5 ≤ 25), `prepare_threshold=None`, `pool_pre_ping=True`, `pool_recycle=1800`. **Locust at 300 users still not run.** |
| B Async loop hygiene | ✅ | Cross-cutting routers `task.delay` sweep from 2026-06-07 is RESOLVED — all 8 verified sites wrap in `await asyncio.to_thread(...)` with axis-B comments. Worker async helpers stamp `creator_id` correctly. |
| C Celery idempotency | ✅ | The 2026-06-07 SEV1 (clean/edit sharing `cleaned_render_uri`) is FIXED. Worker subagent confirms all 11 task bodies idempotent under at-least-once. **Billing webhook fast-path idempotency is dead-code under RLS (NEW SEV1)** — but UNIQUE constraint downstream still preserves correctness. |
| D Tenant isolation | ✅ | The 2 worker RLS-blind async helpers (`_retrain_preference_async`, `_generate_improvement_brief_async`) FIXED. RLS infrastructure (Issue 79) intact. One outstanding billing-webhook RLS-context omission (above) — NOT a leak, just a dead optimization. |
| E Backpressure | ⚠️ | Timeouts on every external client; circuit-breaker pattern not formal but retries-with-backoff present. Locust not run. **New BLOCKER on frontend `_pollTimer`** adds persistent baseline server load — a backpressure consideration distinct from spike response. |
| F Rate limit / quota | ✅ | slowapi on Redis, per-creator key, every authenticated route covered. |
| G Observability | ✅ | Structured JSON logs, request IDs, token-usage logging on Anthropic calls (knowledge cache counters now real — no longer always 0). |
| H Migration & pgvector safety | ✅ | Deploy workflow auto-applies `alembic upgrade head` (lesson banked 2026-06-07). pgvector index intact. |
| I Secrets / deletion | ⚠️ | Fernet tokens never logged; account-deletion + media purge intact. `TOKEN_ENCRYPTION_KEY` rotation runbook STILL listed pre-launch TODO. New `_root_infra` SEV2: `_fernet()` rebuilds MultiFernet per call — moves the rotation hook from "hardcoded import-time" to "callable function", a minor improvement for the rotation runbook. |

---

## UX register — the user's "barren / not easy to know how to use" concern, mapped

This run added a focused lens. The findings cluster into 5 shapes, each with a concrete fix:

1. **First-run empty dashboard** *(static_frontend SEV2)* — Authenticated user with 0 videos sees an empty page; the explanatory hero block exists but is gated to the 401 path. **Fix**: render the same hero content as an authenticated-empty-state.

2. **Empty list responses are bare `[]`** *(routers SEV2 × 3)* — `/videos`, `/clips`, `/insights/saved` return arrays with no `state`/`message` field, so the frontend can't render "you have nothing because X". **Fix**: extend the `DnaGetOut` wrapper pattern to all list endpoints.

3. **Onboarding state is scattered across 5 endpoints** *(routers SEV2)* — Frontend can't easily say "Step 3 of 5". **Fix**: aggregate `setup_step` + `next_action_type` into `CreatorMeOut` (or a new `/creators/me/setup-status`).

4. **Error responses are descriptive but not machine-actionable** *(routers SEV2 + cleanup)* — `"Channel not connected"` is honest but the frontend hardcodes the redirect URL. **Fix**: add `action_type`/`action_url` to 4xx bodies.

5. **Progress / status enums leak engineer jargon** *(routers cleanup + static_frontend SEV2)* — `InsightType.performer_analysis` rendered as-is, "Queuing…" sticks after fetch error, queue depth/ETA absent on 202. **Fix**: helper `_user_friendly_enum`, error-state copy on the queue button, queue-depth fields on `TaskQueuedOut`.

The good news: **F (permission/plan-gate) is already exemplary** — `BalanceOut` returns `trial_ends_at`/`trial_active`/`low_balance` so the UI can preemptively warn. **H (Honesty Constraint) is clean** — zero "viral"/"guaranteed" strings in static, disclaimer on every page. Tooltips are extensive and WCAG-compliant.

---

## Diff vs previous report (2026-06-07 post-Issue-135)

### Fixed since last run (6 SEV1s and 1 SEV2-cluster)
- ✅ worker `_clean_clip_async` + `_edit_clip_async` shared `cleaned_render_uri` idempotency collision — FIXED (no more silent noop).
- ✅ worker `_retrain_preference_async` + `_generate_improvement_brief_async` RLS-blind sessions — FIXED (creator_id stamped).
- ✅ knowledge `hooks.py:176` + `chapters.py:182` cache_control inert markers — FIXED (markers removed, audit comments added).
- ✅ youtube `_do_token_refresh` committing caller-owned session — FIXED.
- ✅ Routers `task.delay` cross-cutting axis-B (8+ sites) — RESOLVED (all sites wrap in `asyncio.to_thread`).

### New BLOCKER introduced
- 🔴 **static_frontend** `index.html:769-770` runaway `_pollTimer` with no stop condition — battery drain on idle dashboards; persistent baseline server load.

### New SEV1 introduced
- 🔴 **billing** `routers/billing.py:203` webhook idempotency fast-path is dead under RLS — masked today by grant_minutes IntegrityError catch.

### New SEV2 cluster (UX-focused, this run's lens)
- 🟡 5 routers UX SEV2s + 7 static_frontend SEV2s addressing first-run discoverability, empty-state contracts, error actionability, onboarding-state aggregation. This cluster did not exist in prior runs because the lens was not applied; they are now first-class register entries.

### Regressed / new non-UX SEV2s
- 🟡 worker grew from 7 → 11 SEV2 (advisory-lock placement and connection-pool concerns surfaced as the SEV1s cleared and the subagent went deeper).
- 🟡 static_frontend added as a NEW MODULE (first time formally assessed); brings 7 SEV2 + 12 cleanup of its own.
- 🟡 youtube: quota counter consumes per-call vs per-retry (top SEV2 this run).

### Carry-forward unchanged
- ⚠️ Locust 300-user run still not executed → axes A and E remain ⚠️.
- ⚠️ `TOKEN_ENCRYPTION_KEY` rotation runbook still listed pre-launch TODO.

---

## Top 10 actions, in order

1. **`static/index.html:769-770` — cap the `_pollTimer` and add exponential backoff + tab-visibility gate.** BLOCKER. ~30 LOC.
2. **`routers/billing.py:203` — stamp `session.info["creator_id"]` before the webhook idempotency query.** New SEV1. Add regression test that fires the same webhook twice and asserts `already_fulfilled` from the FAST path (today the test passes via `grant_minutes` catch — masks the bug). ~10 LOC + test.
3. **Empty-state contract sweep on routers** (`videos.py`, `insights.py`, `clips.py`) — wrap list responses in `{items, state, message}`. Direct fix for the "barren" complaint. ~3 Pydantic models + ~50 LOC. SEV2 ×3 in one PR.
4. **`static/index.html` — render an authenticated-empty-state hero** that mirrors the pre-auth hero copy, gated on `onboarding_state === "connected" && video_count === 0`. SEV2 — second-biggest UX lever after #3. ~40 LOC.
5. **Aggregate onboarding state into `CreatorMeOut`** — add `setup_step`, `setup_step_label`, `next_action_type`. SEV2; eliminates 5-endpoint polling on every page load. ~20 LOC + 1 Pydantic field set.
6. **Structured 4xx error shape** — add `action_type` + `action_url` fields to all 4xx bodies; centralize via middleware or a `make_error()` helper. SEV2; unlocks generic frontend error rendering.
7. **`billing/stripe_client.py:101` — tenant-scope the Stripe `Idempotency-Key`** as `f"{creator_id}:{intent_id}"`. SEV2; defense-in-depth on the money path. ~3 LOC.
8. **`_root_infra/crypto.py:13` — cache `MultiFernet` at module level** (or via `lru_cache(maxsize=1)`); keep an explicit invalidation hook for the rotation script. SEV2 hot-path fix.
9. **`youtube/quota.py:64` — debit quota per RETRY, not per CALL.** Current local counter under-reports real Google budget consumption. SEV2.
10. **Run Locust at 300 users to close axes A + E with evidence.** The deferred step from Issue 112. Once done AND the BLOCKER + new SEV1 land, this report flips to **PRODUCTION-READY: YES**.

After actions 1–10 land, the verdict moves to **PRODUCTION-READY: YES** subject to the still-pending TOKEN_ENCRYPTION_KEY rotation runbook (CLAUDE.md pre-launch item, not a code defect).
