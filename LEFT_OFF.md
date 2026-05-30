# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a source
> of truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-05-30 (Issue 86 closed end-to-end · Dockerfile PYTHONPATH hotfix · full `/assess` re-run)
**Branch:** `main` — HEAD `bbfa3c8`. Only `main` exists locally and on origin.
**Working tree:** dirty — `LEFT_OFF.md` (this file, in-flight), `docs/assessment/REPORT.md` + all 11 `docs/assessment/modules/*.md` + new `docs/assessment/history/2026-05-30-post-issue-86-REPORT.md` (fresh `/assess` output). Plus untracked `Screenshot 2026-05-30 155339.png` (the user's stuck-DNA screenshot that kicked off the session — can be deleted).
**Sync with `origin/main`:** **0 / 0** — in sync.
**Production:** ✅ Deployed. Last deploy `26696877304` succeeded on commit `bbfa3c8`; `/health` returning `{"status":"ok","postgres":"ok","redis":"ok"}`. All 5 CI lanes green on HEAD (CI, Quality Gates, Integration tests, Docker publish, Deploy).
**RLS posture:** ⚠️ unchanged from prior session — migration `0010_rls_policies` applied, roles exist, BUT not yet *enforced*. App still connects as SUPERUSER `creatorclip`. Activation is a manual one-time step via `.github/workflows/activate-rls.yml` once the user adds the two `POSTGRES_*_PASSWORD` repo Secrets.

---

## 1. CURRENT FOCUS

**Nothing in flight.** Issue 86 closed this session — live SSE progress streaming for long-running LLM + worker tasks (DNA build is the first wired call site). A full `/assess` was run after: **PRODUCTION-READY = CONDITIONAL**, 0 BLOCKERs, 2 SEV1s, 27 SEV2s, 28 cleanups. Pre-existing CONDITIONAL posture maintained; SEV1 count went 4 → 2 (net improvement); scale axis D moved ⚠️ → ✅ via RLS structural enforcement; scale axis B moved ⚠️ → ✅ via clip_engine CPU off the loop.

Four independent threads are queued. The user picks which to start next — each has a clean Phase-1 CHECK in front of it; do NOT start any without one.

### → NEXT ACTION (pick one, in any order)

1. **Commit the `/assess` artifacts + this LEFT_OFF.md** — currently dirty in the working tree, not yet on `main`. Single commit, no code change, safe to land:
   ```bash
   git add LEFT_OFF.md docs/assessment/REPORT.md docs/assessment/modules/*.md docs/assessment/history/2026-05-30-post-issue-86-REPORT.md
   git commit -m "chore(assessment): /assess re-run post Issue 86 — VERDICT: CONDITIONAL"
   git push origin main
   ```
   The untracked screenshot can be deleted (`rm "Screenshot 2026-05-30 155339.png"`) — it was the user's stuck-DNA screenshot that triggered the PYTHONPATH hotfix; no longer needed.

2. **Fix the top SEV1 from this morning's `/assess`** (`worker/progress.py:214-232`, ~5 lines). `aacquire_slot` only sets EXPIRE on the INCR→1 transition — a creator holding ≥1 SSE streams continuously past 1h has the counter TTL elapse → cap silently bypassed. One-line fix: `EXPIRE` on every INCR, not just count==1. **Only SEV1 a misbehaving client can exploit today; lowest-LOC highest-leverage fix on the register.** Full backing in `docs/assessment/REPORT.md` row 1.

3. **Fix the latent SEV1 in `billing/refund.py:41`** (~1 line + 1 test). `refund_for_video` opens `db.AsyncSessionLocal()` (RLS-gated app role) without setting `session.info["creator_id"]` → silently no-ops once the prod RLS role split flips. Mechanical fix: switch to `db.AdminSessionLocal()` to match the rest of the worker surface. Must land **before** the RLS activation step (#5 below) — flagged in CLAUDE.md as pending.

4. **Activate RLS on prod** (manual, ~5 min, no new code) — same procedure as last session, still pending. **Land action #3 above first** so refund doesn't silently break the moment RLS is enforced:
   1. Generate two passwords: `echo "APP: $(openssl rand -hex 24)"; echo "MIGRATE: $(openssl rand -hex 24)"`
   2. Add as repo Secrets at **Settings → Secrets and variables → Actions**: `POSTGRES_APP_PASSWORD` and `POSTGRES_MIGRATE_PASSWORD`.
   3. Trigger **Actions → "Activate RLS (Issue 79)" → Run workflow** with `dry_run=true`. Verify printed SQL + .env plan.
   4. Re-run with `dry_run=false` to apply. Workflow rolls back automatically on a failed verification; timestamped `/opt/autoclip/.env.backup-…` is created first.

5. **Issue 84 — AI/LLM efficiency assessment** (user-requested follow-up, prerequisite for the Issue 86 streaming wrapper to ever surface real `thinking_delta` events). The Issue-86 build also produced **free cache-hit observability** at every Anthropic call site via the new `cache` SSE event — that data is now Issue 84's raw material. **Start with a Phase-1 CHECK** — Anthropic SDK + caching state moves fast. Scope brief at `docs/issues.md::Issue 84`.

6. **Issue 85 — UI redesign** (user-requested follow-up). Same scope as last session — sleek modern editing-tool aesthetic (CapCut / Descript / Riverside / Final Cut for web). Includes reworking the Issue 83 intake form **and** dressing up the brand-new Issue 86 terminal-style progress block. Soft-depends on Issue 84. Scope brief at `docs/issues.md::Issue 85`.

7. **Backboard Media data investigation** (incidental finding from today's hotfix session). User's own creator account (`eb9af967-5d2f-4063-a05e-9f4f070ce840`, channel "Backboard Media", state `connected`) has **0 videos in the DB** — `build_dna` correctly raises `ValueError: Insufficient data for DNA build: 0 long videos (min 10), 0 shorts (min 5)`. The YouTube analytics fetch from Issue 4 either never completed for this account or hasn't run since. Worth investigating before the user retries the DNA flow.

### Other open work (not user-prioritized this session)

- **Top SEV2s from `/assess` row 3-10** (worker progress.py XREAD pool sizing, routers/tasks.py 404/403 enumeration oracle, unvalidated Last-Event-ID, Anthropic stream mid-interrupt no terminal emit, Dockerfile root user + `--reload` default, refund pack_id needs UNIQUE). All small and well-backed. See `docs/assessment/REPORT.md` for the full ranked register.
- **Issue 78e** — YouTube analytics-retention purge. Still needs ToS staleness figure in `docs/COMPLIANCE.md` §2 and sign-off to delete creator analytics.
- **Issue 78f** — PgBouncer load-test harness. Needs a real staging cluster. This is the single highest-leverage action for moving the overall verdict from CONDITIONAL to YES — converts scale axes A/C/E/F from ⚠️ to ✅.
- **`disallow_untyped_defs` ratchet** (deferred from 78c). Still ~20 pre-existing untyped-def signatures.
- **Local-track placeholders** (Issues 80–82): transactional email, in-app notifications, Wave 2 of Issue 38.

---

## 2. WHAT WORKS NOW (do not re-investigate)

### Just-shipped this session (Issue 86 + the PYTHONPATH hotfix that preceded it)

**Dockerfile PYTHONPATH hotfix (commit `c2a76d4`)** — prod incident root cause was Celery's console-script entry at `/root/.local/bin/celery` setting `sys.path[0]` to the script dir instead of `/app`. Forked pool workers couldn't import first-party packages → `ModuleNotFoundError("No module named 'dna'")` → 4-retry crash-loop with UI frozen for 3+ min. Fix: `ENV PYTHONPATH=/app` in Dockerfile. Subprocess integration test at `tests/test_worker_imports_integration.py` guards it forever — spawns a real Celery worker subprocess and asserts `from dna.brief import generate_brief` succeeds. Decision logged in `docs/DECISIONS.md`.

**Issue 86 — live SSE progress surface (commits `8cf33a4` → `bbfa3c8`)** — DNA build no longer feels like a frozen spinner. Six commits including four post-push CI fixes (cross-loop Redis binding, generate_brief streaming unification, aclose defensive teardown, mypy 1.14.1 type narrowing). All 5 CI lanes green; production deploy succeeded.

- **`worker/progress.py`** (NEW) — `sync_emit` (for inside `asyncio.to_thread`) / `aemit` (async) + `aset_owner` / `aget_owner` / `aacquire_slot` / `arelease_slot` / `aread_since` / `aclose`. Per-task Redis Stream `task:{task_id}:events` with `MAXLEN ~200` + `EXPIRE 3600` on terminal events. Per-creator concurrent SSE counter (cap 3). Ownership key for SSE auth. Loop-aware singleton survives pytest's per-test loop scope.
- **`worker/anthropic_stream.py`** (NEW) — wraps `Anthropic.messages.stream()` to forward `message_start.usage` as `cache` event (HIT/miss visible BEFORE first token), `text_delta` as `token`, `thinking_delta` as `thinking` (forward-compat — fires once SDK is bumped in Issue 84). Returns `(final_text, usage_dict)`.
- **`dna/brief.py`** — extracted `_build_request` helper; `generate_brief()` got a `task_id: str | None = None` kwarg that internally routes to the streaming path when set. **Same prompt structure either way** — cache breakpoint identical, so prior cache writes are interchangeable between paths. Existing unit-test mocks of `generate_brief` keep working untouched.
- **`worker/tasks.py::_build_dna_async`** — `aemit("step", label=...)` at every stage boundary (`acquire_lock`, `analyze_patterns`, `analyzed_patterns` with counts, `call_claude`, `embed`); terminal `done`/`error` with safe messages; whole flow wrapped in try/except for clean error propagation.
- **`routers/tasks.py`** (NEW) — `GET /tasks/{task_id}/events` SSE endpoint with session-cookie auth, ownership check via Redis key, EventSource `Last-Event-ID` resume, 12s `: keepalive` comment, 600s hard lifetime cap, per-creator concurrent cap = 3. Cloudflare-Tunnel-safe headers (`Cache-Control: no-cache` + `X-Accel-Buffering: no`).
- **`routers/creators.py::build_dna`** — sets ownership in Redis after `.delay()`, returns `stream_url` in the 202 response.
- **`static/progressStream.js`** (NEW) + **`static/onboarding.html`** — vanilla-JS EventSource reducer renders progress into a terminal-style `<pre>` block. Pollers stay as belt-and-suspenders fallback.
- **`main.py`** — mounts the new router; lifespan shutdown drains `worker.progress.aclose()`.
- **Tests**: +24 unit + 1 subprocess integration test. **492 passed / 1 skipped / 85 deselected** on default lane; integration lane green on CI. Seven sub-decisions captured in `docs/DECISIONS.md`.

**Today's `/assess` re-run** — `docs/assessment/REPORT.md` plus snapshot at `docs/assessment/history/2026-05-30-post-issue-86-REPORT.md`. **VERDICT: CONDITIONAL** (was CONDITIONAL — held with improvements). 11 module files refreshed. Net SEV1 trend: −2 (closed 4 prior, surfaced 2 new). Scale axes B and D moved ⚠️ → ✅. The single gating action between CONDITIONAL and YES is the Locust-behind-PgBouncer load test (Issue 78f) — no code reading can substitute.

### Stable foundations from prior sessions (unchanged this session)

- **Beta production** live at `https://autoclip.studio` (Cloudflare Tunnel → VM `app:8000`).
- **Reconcile merge + RLS + Issue 83 identity** all deployed. Migration head `0012_creator_identity`.
- **`activate-rls.yml`** workflow ready (`workflow_dispatch`, `dry_run=true` default, idempotent SQL, timestamped `.env` backup before edit). Sanity check accepts any head ≥ `0010_rls_policies`.
- **mypy baseline 0** (CI pinned to `1.14.1`; local `.venv` runs `2.1.0` — re-pin if you re-baseline).

---

## 3. THE ARC THAT LED HERE

1. **2026-05-30 (this session, pt 1)** — user reported stuck DNA build screenshot. Traced to prod worker `ModuleNotFoundError: 'dna'` 4× crash-loop. Filed as off-course bug, hotfixed with `ENV PYTHONPATH=/app` in Dockerfile + subprocess integration test as a permanent guard. Pushed and deployed before continuing.
2. **2026-05-30 (pt 2)** — user asked "what's a good way to test this internally? Maybe for the DNA testing or really ANY LLM analysis, I think having it show it's thinking is HUGE." Phase-1 CHECK with deep industry-standards research → SSE + Redis Streams + Anthropic streaming wrapper, plain JSON wire format. User approved with "good to go, make sure we test while we build, and then do a deep assessment after."
3. **2026-05-30 (pt 3)** — built Issue 86 end-to-end: test-first (TDD red → green for `worker/progress.py` and `worker/anthropic_stream.py`), then wiring into DNA build, the SSE endpoint, the frontend reducer. Invoked `/claude-api` before writing the streaming wrapper per CLAUDE.md project rule. Single feature commit (`8cf33a4`) followed by four CI-driven fix-forwards as integration tests + Quality Gates surfaced cross-loop Redis binding, test-mock unification, aclose defensiveness, and mypy 1.14.1 type-narrowing. All five CI lanes green on `bbfa3c8`.
4. **2026-05-30 (pt 4 — current)** — ran `/assess` (full 3-layer: deterministic gates + 11 parallel module subagents + Layer-2 verdict). Result: CONDITIONAL, SEV1 4→2, scale axes B+D promoted to ✅. Report + 11 module findings + history snapshot written; not yet committed.

---

## 4. KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| **Public URL / health** | `https://autoclip.studio` · `/health` |
| **VM / SSH / deploy dir** | `147.182.136.107` (Ubuntu 24.04) · `ssh creatorclip-vm` · `/opt/autoclip/` |
| **R2 bucket / image** | `creatorclip-beta` · `ghcr.io/reese8272/creatorclip:latest` |
| **GitHub repo** | `github.com/reese8272/creatorclip` (private) — single branch `main` |
| **Test runner** | `.venv/bin/python -m pytest -q` — venv MUST be Python 3.12. Needs a running Redis. |
| **Lint runner** | `ruff check .` AND `ruff format --check .` — CI runs both. `ruff==0.15.15`. |
| **mypy** | `.venv/bin/python -m mypy .` — **CI pins `mypy==1.14.1`**; local `.venv` may have newer. Re-pin (`pip install "mypy==1.14.1"`) if a baseline diverges. Baseline: 0. |
| **Assessment gate** | `python3 .claude/skills/production-assessment/scripts/run_layer0.py` |
| **Latest assessment** | `docs/assessment/REPORT.md` (today, post Issue 86) · snapshot `history/2026-05-30-post-issue-86-REPORT.md` |
| **Active issue** | _(none in flight)_ — pick from NEXT ACTION above |
| **Last completed** | Issue 86 — live SSE progress streaming (this session) |
| **Latest alembic head** | `0012_creator_identity` (deployed) |
| **Test count** | 492 passed, 1 skipped, 85 deselected (default); integration lane green |
| **Safety tag (pre-merge rollback)** | `safety/pre-reconcile-2026-05-30` |
| **Secrets registry** | `docs/SECRETS.md` — names only, values in `.env` / GitHub Secrets |
| **Secrets needed for RLS activation** | `POSTGRES_APP_PASSWORD`, `POSTGRES_MIGRATE_PASSWORD` (both write-only — generate fresh with `openssl rand -hex 24`) |
| **Memory dir** | `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/MEMORY.md` |
| **Backboard Media creator id** | `eb9af967-5d2f-4063-a05e-9f4f070ce840` — currently 0 videos in DB (see Next Action #7) |

---

## 5. CONSTRAINTS & GOTCHAS

- **Pushing to `main` triggers the production deploy pipeline.** No staging gate today. CI must be green before push; alembic `upgrade head` runs as part of deploy. The dirty assessment artifacts in the working tree right now (`docs/assessment/*.md` + this file) are doc-only and safe to push.
- **RLS is on the tables but NOT yet enforced** — the app still uses SUPERUSER. The `creator_identity.uq_one_current_identity_per_creator` invariant + every other tenant-scoped invariant is defended only by the application layer + indices until the activation workflow runs. **Land action #3 (refund AdminSessionLocal fix) BEFORE running the activation workflow** — otherwise terminal-ingest refunds will silently no-op the moment RLS is enforced.
- **`SET LOCAL` does NOT accept bind parameters in Postgres** — always use `SELECT set_config('name', :value, true)` instead. Caught the hard way; see `db.py:138` for canonical pattern.
- **`pack_id` is now VARCHAR(64)** (was 32) — long enough for `refund:<uuid>` (43 chars). If you invent any longer pack_id shape, re-check the column width.
- **`dna_brief` is the cached prefix for clip scoring** — identity reaches the scorer transitively via the brief, NOT through a separate scorer-prompt block. Identity edits don't take effect on scoring until the next DNA rebuild.
- **Anthropic prompt-cache TTL is 5 minutes** (2026 change). Identity caching rarely engages for a creator's single isolated DNA build. **Issue 86 added free cache-hit observability** via the new `cache` SSE event — Issue 84 inherits this as evidence.
- **Worker tasks use `db.AdminSessionLocal()`** — when RLS is activated, this is the BYPASSRLS role. New worker tasks must use `AdminSessionLocal`, NOT `AsyncSessionLocal`, or cross-tenant sweeps (purge, poll, refresh, refund) will silently see zero rows. **`billing/refund.py:41` is the one place this is wrong today — fix it before RLS flip.**
- **`_build_dna_async` holds a `pg_advisory_xact_lock`** + double-checks `job_id` idempotency. Both must stay — they close a double-spend race on paid LLM/Voyage calls.
- **Anthropic SDK 0.40 TextBlockParam stub predates `cache_control`** — hence the targeted `# type: ignore[arg-type]` on the `system=` kwarg. Asymmetric: `.create()` has the ignore, `stream_and_emit` doesn't — fine today (stream_and_emit's signature is `Any`), but watch when Issue 84 bumps the SDK.
- **`worker.progress` async Redis singleton is loop-aware** (rebinds on `asyncio.get_running_loop()` mismatch) so it survives pytest's per-test loop scope. Production cost: zero (one loop per worker process). Resets singleton on emit failure to recover from a wedged client — observability NEVER load-bearing.
- **`worker.progress.aset_owner` / `aget_owner` MUST raise on Redis failure** (unlike `aemit` which swallows). They are the SSE authorization invariant — a swallow would let a leaked task_id read another creator's stream after a Redis blip. Asymmetry is intentional; documented inline.
- **Integration tests are deselected from default `pytest -q`** (`pytest.ini`); only the integration-tests CI lane runs them (needs real Postgres + Redis).
- **mypy version mismatch trap**: CI pins `1.14.1`, local `.venv` may have `2.x`. `2.x` narrows union types more aggressively, so a local-green change can fail Quality Gates. If you ever see "passes locally, fails CI" on mypy, pin locally first.
- **Google OAuth app still in Testing mode.** Verification required before public launch.
- **Cannot delete remote branches from a fresh agent env** sometimes — git proxy has returned 403 on delete-refspec pushes. Branch cleanup may need the GitHub UI.

---

## 6. POINTERS

| Doc / path | Purpose |
|---|---|
| `docs/assessment/REPORT.md` | **TODAY'S** production-readiness verdict (CONDITIONAL); top-10 register; scale checklist with axis-by-axis evidence |
| `docs/assessment/history/2026-05-30-post-issue-86-REPORT.md` | Immutable snapshot of today's run |
| `docs/assessment/modules/*.md` | Per-module findings (11 files, all refreshed today) |
| `docs/PROJECT_STATE.md` | Per-issue close log (reverse chronological; Issue 86 at top) |
| `docs/issues.md` | Backlog incl. Issue 86 closed + Issues 84 + 85 open |
| `docs/DECISIONS.md` | Architecture decisions — Issue 86 (7 sub-decisions) + Dockerfile PYTHONPATH hotfix both captured today |
| `docs/SOT.md` | Tech stack + data model + file tree (Issue 86 additions reflected: `routers/tasks.py`, `worker/progress.py`, `worker/anthropic_stream.py`, `static/progressStream.js`) |
| `docs/COMPLIANCE.md` | YouTube ToS posture + retention + honesty constraint |
| `docs/CLIPPING_PRINCIPLES.md` | Named principles registry the clip engine cites |
| `docs/DEPLOYMENT.md` | Dev setup + RLS one-time setup runbook (Issue 79) |
| `docs/SECRETS.md` | Every secret by NAME (incl. the two `POSTGRES_*_PASSWORD` slots for RLS activation) |
| `docs/ACCESS.md` | SSH + Cloudflare Tunnel runbook |
| `docs/OFF_COURSE_BUGS.md` | Off-course bug log — PYTHONPATH ModuleNotFoundError entry added this session |
| `.github/workflows/activate-rls.yml` | Manual one-time RLS activation (workflow_dispatch only) |
| `.github/workflows/quality.yml` | Ratcheted CI gates (types/coverage/SAST/CVEs) |
| `.github/workflows/deploy.yml` | CD pipeline (gated on Docker publish, runs alembic upgrade) |
| `alembic/versions/0001..0012` | Migration chain (head: `0012_creator_identity`) |
| `CLAUDE.md` | Project rules + Check→Approve→Build→Review workflow |
| `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/MEMORY.md` | Auto-memory index |
