# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a
> source of truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-06-08 (post-`/assess` + top-of-register fix sweep — uncommitted)
**Branch:** `main` — HEAD `d398cff` (Issue 137 video-intake + trial-expiration), `0` ahead / `0` behind `origin/main`
**Working tree:** **DIRTY** — 23 modified + 3 untracked files from this session's assessment refresh + 4 register fixes. Nothing committed yet.
**CI / Prod (latest push `d398cff`):** Deploy to production ✅ · scheduled production health checks running

---

## CURRENT FOCUS

**The 2026-06-08 UX-focus production assessment ran end-to-end and I shipped the four highest-leverage fixes from the resulting top-10 register. Everything is local + green; ready to commit + push, then begin the deferred UX issues.**

### → NEXT ACTION

1. **Inspect the dirty tree** and confirm everything is intended:
   ```bash
   git status --short
   git diff --stat
   ```
   You should see two clusters — assessment artifacts (`docs/assessment/**`) and the 4 fix patches (`static/`, `routers/billing.py`, `youtube/oauth.py`, `tests/`).
2. **Run the gates one more time before committing** (they're already green; this is the muscle memory):
   ```bash
   PATH=".venv/bin:$PATH" python3 .claude/skills/production-assessment/scripts/run_layer0.py
   PATH=".venv/bin:$PATH" python3 -m pytest -q
   ```
   Expect: ruff 0 / mypy 0 / coverage 75.38% / bandit 0 / pip-audit 0 · 940 passed / 2 skipped.
3. **Commit + push in two logical commits** so the assessment snapshot can be reverted independently of the code fixes if needed:
   - Commit A: `docs/assessment/**` only (the new REPORT.md + module re-assessments + the 2026-06-08-ux-focus history snapshot).
   - Commit B: the 4 fixes (`static/index.html`, `static/auth.js`, `static/login.html`, `routers/billing.py`, `youtube/oauth.py`, 7 other static pages with the `/auth/login` → `/static/login.html` redirect rename, plus 3 test files).
   Verify after push: `gh run list --limit 3` should show CI green within ~5 min.
4. **Verify the four fixes in the browser** (the user explicitly asked to be shown the new look before extending the pattern):
   - Logout from the dashboard → should land on the new `/static/login.html` branded page, NOT a Google redirect.
   - Click "Sign in with Google" → Google account picker should appear (because `prompt=consent select_account` now).
   - Sign in with a fresh creator account → dashboard should show the new `#empty-dashboard-hero` ("Let's get your first clip") with the 3 numbered next-step cards.
   - Open a video that's stuck in pending → after ~10 min the polling cap should kick in and the "video appears stuck" banner should appear.
5. **Open follow-up issues** for the six items the user asked about that need Phase-1 CHECK briefs before code (see DEFERRED FOLLOW-UPS below). Each is a separate issue, not a single sweep.
6. **Begin the highest-leverage deferred work** when the user gives direction: most likely the empty-state response wrappers on `/videos`, `/clips`, `/insights/saved` (touches Pydantic + every consumer). That's the natural follow-up to the dashboard empty-hero already shipped this session.

---

## WHAT WORKS NOW (do not re-investigate)

- **Layer 0 gates all green** (ruff 0 · mypy 0 · coverage 75.38% above baseline 75.20% · bandit 0 · pip-audit 0 · freshness ok). Baseline file: `docs/assessment/baselines.json`.
- **940 unit tests pass · 2 skipped · 127 integration deselected by default** (need Postgres; CI runs them). The new regression test `test_webhook_fast_path_short_circuits_before_grant` is registered in the integration marker group.
- **`/assess` skill ran end-to-end this session** — Layer 0 + 14 parallel Layer-1 subagents + Layer-2 verdict synthesis. The full register is at `docs/assessment/REPORT.md` and snapshotted at `docs/assessment/history/2026-06-08-ux-focus-REPORT.md`.
- **All 6 SEV1s from the 2026-06-07 assessment are FIXED** (verified by re-running module subagents this cycle): worker clean/edit shared-idempotency, worker RLS-blind helpers ×2, knowledge cache_control inert markers ×2, youtube `_do_token_refresh` caller-session.
- **Routers axis-B (`task.delay` inside `async def`) cross-cutting SEV2 is RESOLVED** — all 8 sites wrap in `await asyncio.to_thread(...)`.
- **The 4 fixes shipped this session are wired and tested:**
  - `static/index.html:759-815` — `_pollTimer` capped at 120 ticks (~10 min base), exponential backoff 5s → 30s ceiling, pauses while tab hidden, "video appears stuck" banner.
  - `routers/billing.py:202-211` — `session.info["creator_id"]` stamped BEFORE the idempotency query so RLS matches; new regression test spies on `grant_minutes` and asserts the fast path actually fires (the prior test passed via `IntegrityError` catch — masked the bug).
  - New `static/login.html` — branded sign-in landing page. All 401 redirects across 8 static pages now land here. `youtube/oauth.py` adds `select_account` to the prompt so Google's account picker appears after logout. Test `test_authorization_url_forces_consent_for_refresh_token` updated to accept both values.
  - `static/index.html` — `#empty-dashboard-hero` lights up when the authenticated user has 0 videos; 3 numbered step cards + primary "Link a video →" CTA that auto-expands the link form and focuses the input.
- **CI deploy workflow auto-applies `alembic upgrade head` before container rollout** (verified live 2026-06-07). No manual migration step needed on prod.
- **Honesty-constraint scanner** (`tests/test_compliance_no_virality.py`) uses exact-substring whitelisting — disclaimers MUST keep the canonical phrases on a single line ("does not promise virality", "Audience-fit over generic virality"). Whitespace runs inside those phrases break the test.

---

## THE ARC THAT LED HERE

1. **2026-06-08 morning** — User ran `/assess` with explicit UI/UX emphasis ("the app feels barren or not easy to know how to use") instead of naming a single module to slice on.
2. Layer 0 ran clean. Fourteen Layer-1 subagents dispatched in parallel (added a new `static_frontend` module slice given the UX focus); 11 wrote module findings files directly, 4 ran under read-only Explore and returned 3-line summaries that the orchestrator merged into the existing module files (billing required a SEV1 escalation from a new finding).
3. Layer-2 verdict synthesized: **CONDITIONAL** — all 6 prior SEV1s fixed + axis-B sweep resolved; 1 new BLOCKER (`_pollTimer` runaway) + 1 new SEV1 (billing webhook RLS-blind idempotency, masked by `grant_minutes` IntegrityError catch) + a 12-item UX SEV2 cluster directly mapping to the "barren" complaint.
4. **User said "can we fix these diffs then?"** plus raised six new UX/product concerns: logout auto-relogs, dashboard/insights "still old style", editor "bland", tutorial walkthrough needed, "show how to connect the API key", and "have it BE the OBS editor" or far easier OBS connect.
5. Sorted the work: 4 unambiguous defect fixes this session vs. 6 deferred items requiring Phase-1 CHECK briefs (per CLAUDE.md One Rule).
6. **Shipped the 4 fixes** (BLOCKER, SEV1, logout flow, authenticated empty hero) + regression tests + Layer-0 re-run. All green. Working tree is the assessment artifacts + the fixes.

---

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Repo | `/home/reese/workspace/Youtube-Video-AI-Editor` |
| Branch | `main` (work directly on main per project convention) |
| HEAD | `d398cff` — "feat: Implement video intake modes and trial expiration handling" |
| Trunk distance | `0` ahead / `0` behind `origin/main` |
| Test command | `PATH=".venv/bin:$PATH" python3 -m pytest -q` |
| Layer-0 command | `PATH=".venv/bin:$PATH" python3 .claude/skills/production-assessment/scripts/run_layer0.py` |
| Latest assessment report | `docs/assessment/REPORT.md` |
| Latest snapshot | `docs/assessment/history/2026-06-08-ux-focus-REPORT.md` |
| Issue log | `docs/issues.md` |
| Project state | `docs/PROJECT_STATE.md` |
| Decision log | `docs/DECISIONS.md` |
| Off-course bugs log | `docs/OFF_COURSE_BUGS.md` |
| Deploy workflow | `Deploy to production` (auto-runs `alembic upgrade head` pre-rollout) |
| Local Python | `python3.12` via `.venv/bin/` (no Docker available locally) |
| Postgres for integration tests | **not available locally** — CI runs them |
| OAuth scopes / Google prompt | `youtube/oauth.py::build_authorization_url` — now `prompt=consent select_account` |
| Secrets/keys | by NAME only — see `docs/SECRETS.md` for the canonical list (`TOKEN_ENCRYPTION_KEY`, `GOOGLE_OAUTH_CLIENT_ID`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `JWT_SECRET`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`) — never write values here |

---

## DEFERRED FOLLOW-UPS (open as issues before starting)

Each needs a Phase-1 CHECK brief per CLAUDE.md before any code. Ordered by my read of leverage; user can reorder:

1. **Empty-state response wrappers** — wrap `[]` returns from `/videos`, `/clips`, `/insights/saved` in `{items, state, message}` (the existing `DnaGetOut` pattern). Frontend updates everywhere those endpoints are consumed. Direct fix for the "barren" complaint at the API layer.
2. **Onboarding state aggregation** — add `setup_step`, `setup_step_label`, `next_action_type` to `CreatorMeOut`. Replaces frontend polling of 5 endpoints to infer the next step.
3. **4xx `action_type` / `action_url` middleware** — structured error shape so the frontend can render generic guidance instead of hardcoding redirect URLs per status.
4. **"Old-style UI" diagnostic** — user reported dashboard/insights "still feel old style" despite the Issue 137 + Issue 99 design-token rollouts. NEEDS a per-page audit before any redesign decisions. I have not done this audit yet.
5. **Editor visual polish (review/editor "bland")** — design call. Research current shorts editor industry standard (Opus Clip, Descript, Submagic) before touching `static/editor.js` + `review.html`.
6. **"SHOW how to connect the API key" walkthrough** — visual guide for OBS / folder-watcher API-key setup. Screenshots? Animated GIF? Inline copy-paste? Brief covers content + platform coverage.
7. **OBS direction (product pivot question)** — own the recording surface (custom OBS plugin/dock) vs. far easier API-key pairing UX. Multi-week direction either way. NEEDS written direction from the user before code.
8. **Tutorial walkthrough (interactive product tour)** — user said "eventually". Library choice (Shepherd.js / Driver.js / custom) + state-machine for step persistence + anchor selectors that survive UI changes.
9. **Other open register items** — Stripe Idempotency-Key tenant scoping (`billing/stripe_client.py:101`), `_root_infra/crypto.py:13` MultiFernet caching, YouTube quota per-retry accounting (`youtube/quota.py:64`), DNA week-wrap (`dna/builder.py:88`). Each is SEV2; can batch.

---

## CONSTRAINTS & GOTCHAS

- **Working tree is dirty.** Commit before any context switch — the assessment artifacts and the fix patches are entangled in one tree but represent two logical units.
- **Postgres isn't running locally** (per existing memory `local_dev_test_env.md`). Integration tests (including the new billing webhook RLS regression) only run in CI. Do not assume an integration assertion is verified just because the unit suite passed.
- **Honesty-constraint scanner is strict-substring.** Any new user-visible copy that mentions "viral" or "promise" must use the exact whitelist phrases on a single line — multi-line HTML with whitespace inside `does not promise virality` will fail `tests/test_compliance_no_virality.py`. Two existing disclaimers in `index.html` and `login.html` were re-flowed to single lines this session for this reason.
- **The new `static/login.html` does NOT include `static/auth.js`.** That's intentional — auth.js would call `/auth/me` → 401 → redirect to `/static/login.html` → infinite bounce. Keep login.html script-free except for the `?yt=` forwarder.
- **The 8-page redirect rename** (`/auth/login` → `/static/login.html` on 401) was a `sed`-driven sweep; explicit "Sign in" / "Connect YouTube" CTA buttons that are USER-initiated still link to `/auth/login` (the OAuth initiator) intentionally. Don't unify them without thinking.
- **The new `_pollTimer` uses `setTimeout` recursion, not `setInterval`.** This was the BLOCKER fix. The test (`test_dashboard_includes_polling`) was updated to accept either — be careful if someone "simplifies" back to `setInterval`.
- **CLAUDE.md One Rule is in force for every non-trivial decision** (architecture, library, model, scoring math, security boundary, UX pattern). The 6 deferred follow-ups above each need a Phase-1 CHECK brief with industry-standard research before code, captured in `docs/DECISIONS.md` if anything deviates from the standard.
- **Off-course bugs:** when something unrelated surfaces, log it in `docs/OFF_COURSE_BUGS.md` and keep going; don't chase it inline.

---

## POINTERS

- Architecture / stack / structure: `docs/SOT.md`
- Issue queue: `docs/issues.md`
- Project state (what's done, in-progress, blocked): `docs/PROJECT_STATE.md`
- Decision log (deviations from PRD or industry standard): `docs/DECISIONS.md`
- YouTube ToS / data retention / privacy posture: `docs/COMPLIANCE.md`
- Named clip-engine principles: `docs/CLIPPING_PRINCIPLES.md`
- Pre-launch / Kubernetes deployment plan: `docs/DEPLOYMENT.md`
- Beta-launch runbook: `docs/BETA_LAUNCH_RUNBOOK.md`
- Latest assessment report: `docs/assessment/REPORT.md`
- All historical assessment snapshots: `docs/assessment/history/`
- Per-module assessment findings: `docs/assessment/modules/`
- Off-course bug log: `docs/OFF_COURSE_BUGS.md`
- Production assessment skill: `.claude/skills/production-assessment/SKILL.md`
- Memory index: `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/MEMORY.md`
