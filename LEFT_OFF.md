# LEFT_OFF — Wave-0 integration

**Last updated:** 2026-06-23 · **Branch:** `wave0-integration` (off `main` @ `65a1d4f`) · **Working tree:** clean
(this branch only; `main` untouched, nothing pushed, nothing deployed).

---

## CURRENT FOCUS

All 14 code-bearing lanes of roadmap **Wave W0** were built autonomously (per-lane `wave0/*` branches via
`.claude/workflows/issue-wave.js`) and **integrated onto `wave0-integration`** with a conflict-aware merge.
The 4 pure-runbook lanes were hand-drafted to `docs/runbooks/`. Nothing is merged to `main` or deployed —
W0's load-bearing ACs (migrations, RLS, SSE round-trips, Stripe races) are staging-gated.

**→ NEXT ACTION**
1. **Review the diff:** `git diff main..wave0-integration` (162 files). Per-lane: `git log --oneline main..wave0-integration`.
2. **Reconcile two known items** before merge (see CONSTRAINTS): the 223-vs-224 caching contradiction in
   `docs/DECISIONS.md`, and the union-merged `docs/PROJECT_STATE.md` lane entries.
3. **Verify on staging (Issue 275):** stand up GKE staging (or any real Postgres 16 + Redis), then
   `alembic upgrade head` (chain `0027 → 0028_usage_cost_estimate → 0029_creator_brand_kit`) and run the
   FULL suite there — `.venv/bin/pytest`. This dev box has no Postgres, so DB/RLS/migration/integration
   tests have NOT run; production code is ruff-clean + `py_compile`-clean only.
4. **Then** merge `wave0-integration → staging → main` (pushing `main` auto-deploys via the self-hosted runner).

## WHAT WORKS NOW (verified on this box)

- 14 lanes merged in dependency order; **0 conflict markers**; all changed Python `py_compile`s.
- **Production code is ruff-clean.** Migration chain is linear (no fork).
- Conflict resolutions done + reasoned: privacy↔billing (Beat tasks/schedule — both kept), agentic↔observability
  (cost-ledger + LLM metric both kept; `hooks.py` keeps agentic's `(text, usage)` tuple return), and the
  **semantic** agentic↔security clash in `dna/brief.py` → **security (Issue 224 trust boundary) won**.
- Fixed a real latent lane bug: 7 LLM-task fns used `settings.COST_*` with no local `from config import settings`
  (F821) — added the local imports (module-level is deliberately avoided to keep config-load deferred).

## KEY COORDINATES & FACTS

| Item | Value |
|------|-------|
| Integration branch | `wave0-integration` (off `main` @ `65a1d4f`) |
| Lane branches | `wave0/{ui-core, qa-release-engineering, activation-onboarding, security-platform, notifications-lifecycle, scoring-eval-preference, agentic-caching-cost, editorial-render, billing-monetization, carry-over-cleanup, privacy-compliance, observability, scale-quota-load, security-prompt-trust-boundary}` |
| New migrations | `0028_usage_cost_estimate` (agentic), `0029_creator_brand_kit` (editorial, renumbered from a colliding 0028) |
| Runbooks (external lanes) | `docs/runbooks/{24-25-26-beta-deploy-gates, 255-258-dr-durability, 275-279-k8s-deploy, 194-youtube-publish}.md` |
| Test env | `.venv/bin/pytest` (8.3.3); needs live **Redis** always + **Postgres** (conftest gate). Memory: python3.12 + brew redis. |
| Reusable harness | `.claude/workflows/issue-wave.js` — `Workflow({scriptPath, args:{wave, mode, model:'sonnet', lanes:[...]}})` |

## CONSTRAINTS & GOTCHAS

- **Pushing `main` auto-deploys to prod** — do NOT merge to `main` until staging-verified (Issue 275).
- **`docs/DECISIONS.md` has a contradiction to resolve:** Issue 223 removed the `dna/brief` cache marker
  (cost spike); Issue 224 re-added it while moving identity to the user turn. The merge kept **224**
  (security). Decide whether to re-strip the marker on top of 224's structure, and remove the stale 223 entry.
- **`docs/PROJECT_STATE.md` + `.env.example`** were union-merged across 14 lanes — entries are all present
  but may be unordered/duplicated; do a cleanup pass.
- **9 `SIM117`** (nested-`with`) ruff nits remain in `tests/test_mailer.py` (notifications lane, pre-existing,
  test-only, not auto-fixable) — trivial cleanup, non-blocking.
- **`stream-vod-recap` (Issue 190)** lane was NOT run — an L-spike that triages to plan-only; run it
  research-only or hand-plan when scheduled.
- Per-lane "passed N tests" claims from the build agents could NOT have run here (no Postgres) — treat as
  staging-pending until re-run on Issue-275 staging.

## POINTERS

- Roadmap + per-issue briefs: `docs/issues.md` (Master Roadmap; W0 lanes/issues).
- Architecture / compliance / decisions: `docs/SOT.md`, `docs/COMPLIANCE.md`, `docs/DECISIONS.md`.
- Memory: `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` (see `project_wave_execution_workflow.md`).
