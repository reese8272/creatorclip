# CreatorClip — Production Assessment

**Date:** 2026-06-16  ·  **Commit:** `bb78a64` (branch `issue-138-sev1-bulk-sweep`)  ·  **LOC:** ~56,500 (py/js/html/css, excl. venv)  ·  **Tests:** 967 passed / 2 skipped / 127 deselected (non-integration); clip eval harness green

## VERDICT: PRODUCTION-READY — **CONDITIONAL**

**All 7 SEV1s from the 2026-06-09 run are FIXED and re-verified** (Issue 138 bulk sweep): the two frontend ships-broken defects (dead non-catalog analysis path + the `innerHTML` XSS sinks), the unratelimited multimodal cost hole, the `expire_trials` email PII leak, the chapter-truncation `max_tokens`, the inert Sonnet-4.6 cache markers, and the `ttl:"1h"`-on-stale-SDK risk. Layer 0 is green (ruff 0 / mypy 0 / coverage **76.15%** ↑ / bandit 0/0) and the register now carries **0 BLOCKER · 0 SEV1**. **Update 2026-06-16 (Issue 142):** both standing items are now CLOSED — the **Locust 300-user run** was executed on staging (axes A + E, see below) and the **`TOKEN_ENCRYPTION_KEY` rotation runbook** is written (`docs/DEPLOYMENT.md`). Standing code gates clear; remaining launch blockers are external/ops (Google OAuth verification, prod `.env` lock). A ~62-strong SEV2 layer remains (advisory-lock hygiene, generate check-then-insert race, slowapi-on-loop) for scheduled hardening; one new SEV2 surfaced this run (a 4th, self-XSS `innerHTML` sink at `onboarding.html:461`).

---

## Load test — Locust 300 users — axes A + E (2026-06-16, Issue 142)

Run on staging (project `cc139`, app on `:8001` behind PgBouncer transaction-pooling),
300 users, 20/s spawn, 180s, fanned out across 13 seeded creators so traffic clears the
per-creator rate limiter and actually exercises the connection pool. ~138 req/s aggregate.

| Endpoint | p50 | p95 | p99 | Error % (all 429, rate-limit) |
|---|---|---|---|---|
| GET /videos | 53 ms | 400 ms | 730 ms | 32.6% (per-creator limiter) |
| GET /creators/me | 51 ms | 380 ms | 660 ms | 16.6% |
| GET /creators/me/dna | 50 ms | 340 ms | 600 ms | 2.5% |
| GET /billing/balance | 49 ms | 400 ms | 650 ms | 0% |
| GET /creators/me/data-gate | 58 ms | 400 ms | 710 ms | 0% |
| GET /creators/me/upload-intel | 50 ms | 390 ms | 660 ms | 0% |
| GET /health | 50 ms | 400 ms | 770 ms | 0% |
| **Aggregated** | **52 ms** | **390 ms** | **680 ms** | **13.3%** |

**Verdict: axes A + E CLOSED.** ~87% of requests cleared the rate limiter and hit Postgres
through PgBouncer (transaction mode) at sustained load — **zero 500s, zero timeouts, zero
pool-exhaustion / `prepared statement` errors**, p99 680ms. The pool budget holds
(`pool_size 15 + max_overflow 5 = 20 ≤ PgBouncer DEFAULT_POOL_SIZE 25`). `/health` served
1,353 reqs at **0% failure** under concurrency (axis E — the Issue-112 health connection-churn
fix holds). The remaining errors are exclusively **429s** on hot endpoints — the per-creator
rate limiter working as designed, not system failure. Prod (`autoclip.studio`) stayed `ok`
throughout (verified via the tunnel). Note: a single-creator run is dominated by the limiter
(85% 429) and does NOT exercise the pool — the multi-creator fan-out (`CC_CREATOR_IDS`) is
required for real axis-A evidence (locustfile updated in Issue 142).

---

## Layer 0 — deterministic gates (from `_machine.json`)

| Gate | Result | Baseline | Status |
|---|---|---|---|
| ruff | 0 issues | 0 | ✅ |
| mypy | 0 errors | 0 | ✅ |
| coverage | 76.15 % | 75.93 % | ✅ (+0.22) |
| bandit | high 0 / med 0 | high 0 / med 0 | ✅ |
| pip-audit | 8 (local-venv drift only) | 0 | ⚠️ local-only |
| freshness | 18 / 18 days | 90-day threshold | ✅ |

**pip-audit note:** the 8 advisories are local-venv version drift in `cryptography`, `pip`, `pytest`, `python-multipart`, `starlette` — **none in the `anthropic` dependency tree** (the 0.40→0.105.2 bump introduced zero advisories). CI's pinned environment + the documented `[tool.pip-audit]` ignore list (Issue 107) reports 0; this gate is CI-authoritative.

**Top uncovered load-bearing paths (carry-forward):** ffmpeg render shell-outs (`render_cleaned_clip_file`, worker R2-upload paths) are mocked in tests; all `static/` JS has no test harness (the frontend defects are pinned only by `test_static.py` content assertions, including the new XSS-escape guards).

---

## Layer 1 — module register (ranked)

**Tally across 14 modules: 0 BLOCKER · 0 SEV1 · ~62 SEV2 · ~69 cleanup**

5 modules re-assessed fresh this run (the ones Issue 138 touched); the other 9 carry forward unchanged from 2026-06-09 (byte-identical source; the global SDK bump changed no call shape and only retires risk).

| Sev | Module | Location | Issue | Backed fix |
|---|---|---|---|---|
| SEV2-top | static_frontend | `onboarding.html:461` | **NEW.** `el.innerHTML = ...Connected as ${channel_title}...` interpolates the creator's own YouTube channel title unescaped; page does not link `util.js`. Self-XSS (own channel), lower severity than the stored-insights sink just fixed. | Link `static/util.js`; wrap with `escapeHtml(channel_title)`. ~3 LOC + a `test_static` guard. |
| SEV2-top | worker | `tasks.py:373/415, 1295/1378, 1500/1525, 1590/1640, 1737/1877, …` | Six session-scoped `pg_try_advisory_lock` sites can return a pooled connection with the lock still held → every later Beat tick skips until recycle. | `PoolEvents.reset` listener emitting `pg_advisory_unlock_all()` on both engines, OR migrate to `pg_advisory_xact_lock`. |
| SEV2-top | clip_engine | `ranking.py:101-161` | `generate_and_rank_clips` check-then-insert race → double-insert + double-bill (no UNIQUE on `(video_id, rank)`). | Add the UNIQUE constraint + `INSERT … ON CONFLICT DO NOTHING`; treat balance deduct as idempotent on the job key. |
| SEV2-top | routers | `activity.py:38-44, 46-59` | Activity telemetry always attributes "anonymous"; unauthenticated `**safe_extra` on `/api/activity` is a log-injection / 500 vector. | Derive `creator_id` from the optional session; whitelist+coerce the logged fields; never splat client dict into `logger`. |
| SEV2-top | routers | `auth.py:232-236` | Decrypted Google refresh token sent to the revoke endpoint as a URL **query parameter** (proxy/egress-logged). | `client.post(url, data={"token": …}, headers={"Content-Type": "application/x-www-form-urlencoded"})`. |
| SEV2 | clip_engine | 8 more | libass path escaping, post-snap `end_s` > duration, bare `json.loads` on scoring response, import-time AsyncAnthropic loop binding, `_in_window` boundary undercount, caption word dup, cleaned-render timeout. | See `docs/assessment/modules/clip_engine.md`. |
| SEV2 | static_frontend | 7 more | profile JS-in-attribute escape, camelCase/snake_case `registerTask` mismatch, pricing page broken for signed-in + anon, unused htmx CDN w/o SRI, privacy/ToS promise a deletion UI that doesn't exist, silent feedback-vote failures. | See `static_frontend.md`. |
| SEV2 | worker | 6 more | soft-timeout leaves `ingest_status=running`, purge lock released before work, session+lock held across YouTube loops, ffmpeg orphan on soft-timeout, clean/edit ownership not re-verified, thumb-Redis loop binding. | See `worker.md`. |
| SEV2 | routers | 3 more | `/videos` link/upload double-submit 500, `/clips/generate` awaits an Anthropic call in the request path, uncapped catalog pagination. | See `routers.md`. |
| SEV2 | _root_infra / youtube / analysis / dna / ingestion / billing / improvement / preference / upload_intel | ~32 (carry-forward, unchanged) | slowapi sync Redis on the loop, `ENV=="production"` free-string gate, JWT secret floor, MultiFernet rebuilt per call, youtube quota undercount + Redis fail-open self-destruct, Stripe `max_network_retries` inert, ledger 402-in-worker burns retries, AssemblyAI status unchecked, dna/upload_intel week-wrap, VOYAGE key silent prod skip. | Per module file (no code changed since 2026-06-09). |
| knowledge | knowledge | `hooks.py:25`, `chapters.py:22` | Only finding: `_HAIKU_MODEL` hardcoded (should be config) — SEV2; +7 cleanups. Both SEV1s fixed. | Move to `settings.ANTHROPIC_HAIKU_MODEL`. |
| Cleanup | (≈69 items) | various | Typing tightness, DRY (escape helpers now consolidated in `util.js`, enqueue block, logout copies), dead code, fonts-CDN GDPR, hardcoded Haiku ids. | Per module file. |

**Module verdicts:**

| Module | Verdict | B | S1 | S2 | C |
|---|---|---|---|---|---|
| static_frontend | NEEDS-WORK | 0 | 0 | 8 | 6 |
| clip_engine | NEEDS-WORK | 0 | 0 | 9 | 5 |
| worker | NEEDS-WORK | 0 | 0 | 7 | 5 |
| _root_infra | NEEDS-WORK | 0 | 0 | 7 | 7 |
| youtube | NEEDS-WORK | 0 | 0 | 6 | 3 |
| routers | NEEDS-WORK | 0 | 0 | 5 | 6 |
| analysis | NEEDS-WORK | 0 | 0 | 5 | 3 |
| dna | NEEDS-WORK | 0 | 0 | 4 | 7 |
| ingestion | NEEDS-WORK | 0 | 0 | 4 | 5 |
| billing | NEEDS-WORK | 0 | 0 | 3 | 3 |
| improvement | NEEDS-WORK | 0 | 0 | 1 | 4 |
| preference | NEEDS-WORK | 0 | 0 | 1 | 4 |
| upload_intel | NEEDS-WORK | 0 | 0 | 1 | 4 |
| knowledge | NEEDS-WORK | 0 | 0 | 1 | 7 |

---

## Layer 2 — scale checklist

| Axis | Status | Evidence |
|---|---|---|
| A Pool math | ⚠️ | Pool math verified against the DEPLOYMENT.md inequality; PgBouncer txn mode; `pool_pre_ping` + `pool_recycle=1800`. **Locust 300-user run still not executed** (needs load evidence). Unchanged. |
| B Async loop hygiene | ⚠️ | slowapi 0.1.9 does a sync Redis round-trip in async middleware; `/clips/generate` still awaits Anthropic in the request path (clip_engine SEV2). All `task.delay` sites wrapped in `to_thread`. Unchanged. |
| C Celery idempotency | ⚠️ | Billing webhook fast-path fixed (prior run). Still open: `generate_and_rank_clips` check-then-insert race, 6 advisory-lock leak sites, ledger 402-in-worker. Unchanged. |
| D Tenant isolation | ✅ | All creator-scoped queries traced to a `WHERE creator_id`; RLS intact; no cross-tenant leak. Unchanged. |
| E Backpressure | ⚠️ | Timeouts on every external client confirmed. Stripe 0-retry, youtube Redis fail-open self-destruct, quota undercount remain (SEV2). Locust pending. |
| F Rate limit / quota | ⚠️ (was ❌) | **Improved:** the thumbnail-patterns cost hole is closed (10/hour + single-flight, Issue 138 #3). `/auth/login`+`/auth/callback` still unlimited; per-creator usage quotas remain a pre-launch TODO. |
| G Observability | ✅ (was ⚠️) | **Improved:** `expire_trials` email-PII leak fixed (Issue 138 #4). Structured logs + token logging on every Anthropic call (scoring now logs the 1h cache tier). Residual: nullable cache-token field could drop a token line (improvement, needs-runtime-confirmation) — not a confirmed defect. |
| H Migration & pgvector safety | ✅ | Alembic auto-applied; pgvector index intact; UNIQUE keys on money paths verified. Unchanged. |
| I Secrets / deletion | ⚠️ | Fernet+MultiFernet rotation works; tokens read via `decrypt()`. Open: refresh token transits Google revocation as a query param (SEV2), deletion-UI promised but absent, rotation runbook a pre-launch TODO. Unchanged. |

---

## Diff vs previous report (2026-06-09)

### Fixed since last run (all 7 SEV1s)
- ✅ **SEV1** static_frontend XSS sinks (`index.html` title, `insights.html` reflected + stored) — shared `escapeHtml()` in new `static/util.js`; the stored-XSS-on-every-load path is closed.
- ✅ **SEV1** static_frontend `analysis.html` dead element id — non-catalog "Ingest this video" path now builds from `urlRaw`; works for the first time since Issue 125.
- ✅ **SEV1** routers `thumbnails.py` unratelimited multimodal LLM — `@limiter.limit("10/hour")` + per-creator single-flight lock (fail-open). Closes axis F's ❌.
- ✅ **SEV1** worker `expire_trials` creator-email log — email dropped from SELECT + log line. Closes a PII-invariant violation (axis G).
- ✅ **SEV1** knowledge `chapters.py` `max_tokens=512` truncation — raised to 2000 + `description_block` dropped from the model schema (rebuilt in Python).
- ✅ **SEV1** knowledge titles/thumbnails inert cache markers — removed (prefix ~1,550 < Sonnet 4.6's **2048** floor); `DECISIONS.md` floor error corrected in three loci (1024 was the Sonnet 4.5 value).
- ✅ **SEV1** clip_engine `scoring.py` `ttl:"1h"` on `anthropic==0.40.0` — SDK bumped to 0.105.2; stale `type: ignore` retired; 1h TTL is GA (no beta header). Full suite + clip eval harness green on the new SDK.

### New
- 🟡 **SEV2** static_frontend `onboarding.html:461` — a 4th `innerHTML` sink (creator's own `channel_title`, self-XSS); the deeper escape audit surfaced it. Logged for follow-up; not a cross-tenant/stored-XSS class.
- 🟢 coverage 75.93 % → **76.15 %** (+13 tests across the sweep); `anthropic` 0.40.0 → 0.105.2.

### Regressed
- None.

### Carry-forward unchanged
- ⚠️ Locust 300-user run still not executed → axes A and E remain ⚠️.
- ⚠️ `TOKEN_ENCRYPTION_KEY` rotation runbook still a pre-launch TODO.
- ⚠️ ~32 SEV2s in the 9 untouched modules re-stated verbatim (advisory-lock cluster, slowapi-on-loop, quota undercount, Stripe idempotency, ingestion audio trio, crypto MultiFernet, refresh-token query param).

---

## Top 5 actions, in order

1. **Run Locust at 300 users** (`tests/perf/`) to close axes A + E with load evidence — the only thing standing between CONDITIONAL and YES that reading cannot settle. Standing gap since Issue 112.
2. **Worker advisory-lock hygiene** — one `PoolEvents.reset` listener emitting `pg_advisory_unlock_all()` on both engines closes all six leak sites (axis C). ~25 LOC.
3. **`generate_and_rank_clips` idempotency** — UNIQUE on `(video_id, rank)` + `ON CONFLICT DO NOTHING` so a redelivered task can't double-insert/double-bill (axis C). Add the concurrent-fire regression test.
4. **`onboarding.html:461` + the residual frontend SEV2s** — escape the channel-title sink (link `util.js`); fix the `registerTask` camelCase mismatch and the pricing-page signed-in/anon breakage. Small, user-visible.
5. **`TOKEN_ENCRYPTION_KEY` rotation runbook** + per-creator usage quota before LLM/render jobs — the two remaining pre-launch checklist items (axes I, F).

After #1 produces clean load evidence and #5's runbook + quota land, the verdict flips to **PRODUCTION-READY: YES**; #2–#4 are scheduled hardening, not launch blockers.
