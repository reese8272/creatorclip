# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a
> source of truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-06-08 (Issue 125 + 126 code-complete locally; awaiting commit + push)
**Branch:** `main` — HEAD `b554981` (Issue 137 — UI overhaul, last pushed commit; `0` ahead / `0` behind `origin/main`)
**Working tree:** **DIRTY** — two issues' worth of changes are uncommitted (Issue 125 + Issue 126; see "CURRENT FOCUS")
**CI (most recent push `b554981`):** Quality Gates ✅ · Docker publish ✅ · Deploy ✅ — but **CI ❌** and **Integration tests ❌** (same pre-existing flake noted previously, not blocking deploy)

---

## CURRENT FOCUS

**Issues 125 + 126 are code-complete, all tests green locally — they need to be committed and pushed in one batch so production picks up the new analysis-mode setting + trial-UX surface.**

### → NEXT ACTION

1. **Inspect the dirty tree** (22 modified files + 4 new files). All belong to either Issue 125 (`analysis_mode` + minutes transparency) or Issue 126 (trial UX + billing clarity):
   ```bash
   git status --short
   ```
   Expect: changes to `models.py`, `config.py`, `routers/{auth,billing,creators,videos,analysis}.py`, `billing/ledger.py`, `worker/{tasks,schedule}.py`, `static/{index,analysis,profile}.html`, `static/auth.js`, `static/page-shell.css`, `.env.example`, plus all 4 docs in `docs/`. New: `alembic/versions/0022_creator_analysis_mode.py`, `alembic/versions/0023_creator_trial_ends_at.py`, `tests/test_issue_125.py`, `tests/test_issue_126.py`. Touched existing tests: `tests/test_billing.py` (mock contract update for the new second `scalar()` call), `tests/test_rate_limiting.py` (stub for new `analysis_mode` field on `CreatorMeOut`).

2. **Run the full suite one more time before commit** — should be **940 passed / 2 skipped / 0 failures**:
   ```bash
   .venv/bin/python -m pytest -q --no-header
   .venv/bin/python -m ruff check . && .venv/bin/python -m mypy .
   ```

3. **Commit + push as ONE commit covering both issues.** They're tightly related (125 lays the `analysis_mode` + `analytics_available` foundation; 126 layers `trial_ends_at` + low-balance surface on top). Suggested message:
   ```
   feat(125+126): video control modes + trial UX + billing clarity

   Issue 125: analysis_mode enum (auto/selective/manual) + PATCH endpoint,
   analytics_available on /me/video-analysis, "what costs minutes" tooltip,
   per-video /queue endpoint + dashboard CTA.

   Issue 126: trial_ends_at column + first-login wiring, /billing/balance
   extends with trial_active + days_remaining + low_balance, differentiated
   402 copy, daily expire_trials Beat watchdog, dashboard trial banner +
   low-balance pre-action warning, .nav-balance.is-low chip state.
   ```
   Note that **pushing to `main` auto-deploys to production** (self-hosted runner pipeline: `docker-publish → workflow_run → deploy`). Confirm the deploy lands with `gh run list --limit 3`.

4. **After deploy: run alembic migration on prod** — both 0022 and 0023 must apply before the new endpoints work:
   ```bash
   # On the prod VM
   docker compose -f docker-compose.prod.yml exec app alembic upgrade head
   ```
   Both migrations are `op.add_column` only (analysis_mode has a `server_default='auto'` backfill; trial_ends_at is NULL-able with no backfill — legacy creators stay NULL).

5. **Smoke-check on autoclip.studio after deploy:**
   - Sign in → dashboard nav minute chip carries `?` tooltip explaining what's billable.
   - Profile page → new "Video intake" radio (Auto / Selective / Manual); save persists across reloads.
   - A linked-but-pending video shows a "Queue for analysis" button on the dashboard.
   - Trial banner renders at top of dashboard with a countdown (only for creators who sign up AFTER the migration — `trial_ends_at` is NULL for everyone created before, which is correct).

---

## WHAT WORKS NOW

1. **Cache-busting (`?v=<sha>`)** — Static CSS/JS URLs carry `?v=sha-<commit>` on every deploy; HTML has `Cache-Control: no-store`; ETag stripped from HTML. **You should never need to manually purge Cloudflare again.** Verify with `curl -s https://autoclip.studio/ | grep -o '/static/[^"]*\.css[^"]*' | head -1`.

2. **Issue 137 (project-wide UI overhaul) shipped to prod (`b554981`).** Every authenticated page (`index`, `insights`, `profile`, `onboarding`, `analysis`, `pricing`, `walkthrough`, `review`) carries `<body class="app-page">` and the shared `page-shell.css` shell: aurora backdrop + glassmorphism nav + soft 12px-radius cards + gradient-pill primary CTAs + `.table-wrap` overflow guard + global `overflow-x: clip`. **No horizontal scroll at any viewport width.** Glassmorphism is scoped to chrome only — tables/forms/transcripts stay flat per WCAG 2.2.

3. **Issue 136 follow-up shipped (`b554981` includes it).** Editor tool rail now shows icon + text label ("Why this clip" / "Captions" / "Clean pass" / "Feedback") instead of icon-only. Pre-auth hero has a prominent "Sign in" pill button in the nav for visitors without a YouTube URL handy.

4. **Issue 125 (locally complete; 17 new tests green):**
   - `AnalysisMode` enum + `Creator.analysis_mode` column + migration 0022 (server_default backfills existing creators to `'auto'`)
   - `PATCH /creators/me/analysis-mode` (60/min rate-limited, Pydantic enum validation, 422 on bogus value)
   - `GET /creators/me` exposes `analysis_mode` so the dashboard reads `window.__USER__.analysis_mode`
   - `POST /videos/{id}/queue` (idempotent — `queued: false` when status ≠ pending; 404 on cross-creator access)
   - `analytics_available: bool` on `AnalysisQueuedOut` (alongside back-compat `has_metrics`, populated identically so they can't drift)
   - `profile.html` has the radio form + `saveAnalysisMode()`
   - `analysis.html` shows explicit "Full analytics unavailable — this video isn't in your ingested catalog yet" panel + "Ingest this video" CTA when `analytics_available === false`
   - `index.html` balance chip carries the "What costs minutes" tooltip; pending video rows show "Queue for analysis" buttons (primary-styled when mode is selective/manual, secondary in auto mode)

5. **Issue 126 (locally complete; 16 new tests green):**
   - `Creator.trial_ends_at` nullable TIMESTAMPTZ column + migration 0023
   - First OAuth login stamps `trial_ends_at = now + TRIAL_DURATION_DAYS` in the same transaction as `grant_minutes(60)`
   - `GET /billing/balance` returns `trial_ends_at` + `trial_active` + `trial_days_remaining` + `low_balance`
   - Differentiated 402 copy in `billing/ledger.py` via `_trial_expired()` + `_trial_ended_402_detail()`: "Your free trial has ended. Add minutes at /pricing to continue." (NULL legacy creators fall back to generic copy)
   - Daily `expire_trials` Beat watchdog — logs only, no state mutation; `billing/ledger.py` is the single source of truth
   - Dashboard `#trial-banner` with per-day-bucket dismissibility + final-day override + pricing-page CTA (Userpilot 2026 — banners must link to checkout not settings)
   - `.nav-balance.is-low` amber chip + pre-action `.low-balance-warning` above Generate/Queue (dashboard) and Analyze (analysis page)
   - `auth.js` caches `window.__BALANCE__` + emits `billing:ready` custom event

6. **Full test suite: 940 passed / 2 skipped / 0 failures locally.** Ruff 0, mypy 0. (CI's "Integration tests" lane on `b554981` is red — pre-existing flake at `tests/test_worker_pipeline.py::test_poll_clip_outcomes_uses_per_creator_median`, `RuntimeError: Event loop is closed` / `assert None is False`. Not caused by this session's work; not blocking deploy because `workflow_run` chains on `Docker publish` not `Integration tests`.)

---

## THE ARC THAT LED HERE

1. **User report on the live deploy:** the marketing hero looked sleek, but the dashboard/editor felt utility-grade, and there was a horizontal scrollbar on every page. Tabs inside the editor weren't discoverable (icon-only strip).
2. **Issue 136 follow-up (labeled tool rail + hero Sign-in CTA)** — small fix in the same session.
3. **Issue 137 (Project-wide UI overhaul + horizontal-overflow fix)** — built and shipped (`b554981`). New `static/page-shell.css` extends the hero aurora aesthetic across all authenticated pages; `overflow-x: clip` + `.table-wrap` + `.action-row` eliminate horizontal scroll. Explicit reversal of Issue 99's "Linear-utility-for-data-pages" split per industry research (Linear's own 2026 refresh extends aurora into product surfaces).
4. **Pre-launch checklist context** — `CLAUDE.md` flags "Billing + plan-tier wired" as required before public launch. User asked "what's next" — recommendation was Issue 125 → Issue 126 (the two-step transparency arc that unblocks paid signups).
5. **Issue 125 (Video control model + minutes transparency)** — built locally this session. `analysis_mode` enum, PATCH endpoint, explicit "What costs minutes" tooltip, per-video Queue button, explicit analytics-unavailable panel.
6. **Issue 126 (Trial UX + billing clarity)** — built locally this session. `trial_ends_at` on Creator, balance endpoint extension, differentiated 402, daily Beat watchdog, dashboard trial banner, low-balance pre-action warnings.
7. **Awaiting commit + push** — see CURRENT FOCUS.

---

## KEY COORDINATES & FACTS

| What | Value |
|---|---|
| Repository | `/home/reese/workspace/Youtube-Video-AI-Editor` |
| Branch | `main` |
| Last pushed HEAD | `b554981` (Issue 137 — UI overhaul) |
| Production URL | `https://autoclip.studio` (via Cloudflare Tunnel; no open inbound ports on prod VM) |
| Deploy pipeline | GitHub Actions: `docker-publish` → `workflow_run: ["Docker publish"]` → `deploy` — both run on `self-hosted` runner on the prod VM (Issue 101) |
| Python virtual env | `.venv/bin/python` (project-local; system Python 3.12 has an unrelated `pydantic-core` mismatch — **always use the project venv**) |
| Test command | `.venv/bin/python -m pytest -q --no-header` |
| Layer 0 gates | `.venv/bin/python -m ruff check .` + `.venv/bin/python -m mypy .` |
| Latest migration | `0023_creator_trial_ends_at.py` (Issue 126); previous `0022_creator_analysis_mode.py` (Issue 125). Apply with `alembic upgrade head` on prod after deploy. |
| New config | `TRIAL_DURATION_DAYS=7`, `LOW_BALANCE_THRESHOLD_MINUTES=10` (both in `.env.example`; defaults match spec — no prod env-var change needed) |
| Secrets (NAMES only, never values) | `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `GOOGLE_OAUTH_CLIENT_ID` + `_SECRET`, `TOKEN_ENCRYPTION_KEY` (+ optional `_PREVIOUS` for rotation), `JWT_SECRET_KEY`, `STRIPE_SECRET_KEY` + `_WEBHOOK_SECRET`, `R2_*`. Registry in `docs/SECRETS.md`; rotation runbook in `docs/RUNBOOKS.md`. |
| Last completed issue | 137 (UI overhaul). Locally complete: 125 + 126. Backlog candidates: Issue 96 (chat-driven intake — SEV-2 UX), Issue 97 (livestream recap — SEV-3 feature, subscription tier), Issue 109 (deferred design-work cleanups), pre-launch checklist items in `CLAUDE.md` (`TOKEN_ENCRYPTION_KEY` rotation runbook, account-deletion endpoint, Google OAuth app verification). |

---

## CONSTRAINTS & GOTCHAS

- **Pushing to `main` auto-deploys to production.** The user-facing app reloads as soon as the deploy run completes (typically <2 min after push). Confirm before pushing.
- **Migrations don't auto-run on deploy.** After `gh run` shows Deploy ✅, you still need `alembic upgrade head` on the prod container or the new endpoints will 500 (or worse — silently skip new columns on inserts). Both migrations this session are pure `add_column`, fast + reversible.
- **Issue 125 + 126 are tightly coupled — ship them in one commit (or two contiguous commits).** Skipping 125 and shipping just 126 means the `analytics_available` field is missing — `analysis.html` falls back via the `?? has_metrics` nullish-coalesce (intentional backward-compat hedge), but the Profile mode-selector + dashboard Queue button would dangle without their backend endpoints.
- **CI's "Integration tests" lane has a pre-existing flake** (`test_poll_clip_outcomes_uses_per_creator_median` → `Event loop is closed`). Not caused by this session's work; deploy proceeds because `workflow_run` chains on `Docker publish` (which is green), not on Integration. If you debug it, it's its own session.
- **Don't backfill `trial_ends_at`.** The migration is intentionally NULL-able; backfilling existing creators to `created_at + 7 days` would retroactively put many in a "trial expired" state with confusing 402 copy. NULL = "no trial," which is the correct legacy semantic.
- **`window.__USER__.analysis_mode` and `window.__BALANCE__` are populated by `auth.js`** after `/auth/me` and `/billing/balance` return. Pages that conditionally render based on either must listen for `auth:ready` and `billing:ready` respectively — both are dispatched as `CustomEvent` on `document`.
- **`TOKEN_ENCRYPTION_KEY` is write-only/unreadable in prod** — never log it, never echo it, never paste it. Rotation runbook in `docs/RUNBOOKS.md`.
- **System Python 3.12 is poisoned** — `pydantic-core 2.27.2` conflicts with `pydantic 2.46.4` system-wide. Tests fail to import with a `SystemError` if you use system `python3` or `python3.12`. The project's `.venv` has the matched versions; **always** invoke as `.venv/bin/python -m pytest` etc.

---

## POINTERS

| Doc | What it is |
|---|---|
| `docs/SOT.md` | Architecture, stack, file structure, schema. **Updated this session** for `Creator.analysis_mode` + `trial_ends_at` + `page-shell.css`. |
| `docs/PROJECT_STATE.md` | Per-issue session log. **Updated this session** with Issue 125 + 126 close-out entries. |
| `docs/DECISIONS.md` | Design-decision log. **Updated this session** with Issue 125 (auto-default, dual `has_metrics`/`analytics_available`, separate `/queue` endpoint) and Issue 126 (NULL-for-legacy, watchdog-not-state-machine, differentiated-text-not-error-code, per-day-bucket dismissal). |
| `docs/issues.md` | Issue backlog. ACs checked off for 125 + 126 + 137. Open: 96 / 97 / 109. |
| `docs/COMPLIANCE.md` | YouTube ToS posture + data retention. Unchanged this session. |
| `docs/CLIPPING_PRINCIPLES.md` | Named principles the scoring engine cites. Unchanged this session. |
| `docs/RUNBOOKS.md` | Encryption-key rotation procedure. |
| `docs/SECRETS.md` | Canonical secrets/config registry (names + where to obtain — never values). |
| `CLAUDE.md` | Project rules: the One Rule (research industry standard before non-trivial decisions), file structure, pre-launch checklist, issue workflow (Check → Approve → Build → Review & Assess). |
| Memory directory | `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/MEMORY.md` |
