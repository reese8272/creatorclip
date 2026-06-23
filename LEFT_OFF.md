# LEFT_OFF — Wave-0 build, integrated to local `main`/`staging` (NOT pushed/deployed)

> **Read this first.** Living "where we are right now" file for a brand-new session with zero memory.
> Source-of-truth docs live in `docs/`; this file orients and points to them.

**Last updated:** 2026-06-23
**Checked out:** `main` @ **`e89574f`** · working tree clean
**`main` == `staging` == `e89574f`**, both **31 commits AHEAD of `origin/main` (`65a1d4f`) — UNPUSHED, UNDEPLOYED.**
`origin/main` == `origin/staging` == `65a1d4f` (untouched on the remote).

---

## ⚠️ THE ONE DECISION PENDING (start here)

All of roadmap **Wave W0** is built, integrated, and merged into **local** `main` + `staging`. It is **not
pushed**. The previous session deliberately held the `git push` because:

- **Pushing `main` auto-deploys to prod (`autoclip.studio`)** via the self-hosted GitHub Actions runner, and
- it runs migrations **`0028_usage_cost_estimate`** + **`0029_creator_brand_kit`** on the **prod database**, and
- **none of this 14-lane batch has been verified on real Postgres/Redis** (this dev box has neither — see
  "Verification reality" below). The code only has: ruff-clean + `py_compile`-clean + conflict-free merge.

**→ NEXT ACTION — choose one:**
- **Ship it:** `git push origin main staging` (this triggers the prod deploy + prod migrations). Then watch:
  `gh run watch` / `gh run list --limit 3`, and health-check `curl -sI https://autoclip.studio/ ` (expect 302 → `/app/dashboard`).
  Migrations are **additive only** (one new column + one new table) — low risk, but unverified.
- **Verify first (safer):** push `staging` only (does NOT deploy — deploy triggers on `main`), bring up a real
  Postgres 16 + Redis, `alembic upgrade head`, run the full suite, then push `main` to deploy.
- **Reconcile the two doc items below before either** (5 min, recommended regardless).

If a deploy goes bad: `git push -f origin 65a1d4f:main` reverts the trunk (then redeploy), or use the image
rollback in `scripts/deploy.sh`. Migrations are additive so a code rollback leaves a harmless unused column/table.

---

## WHAT HAPPENED THIS SESSION (the arc)

1. Built a **reusable wave-execution harness** — `.claude/workflows/issue-wave.js` (multi-agent Workflow):
   per lane it does *research → CHECK brief → buildability triage → autonomous build in an isolated git
   worktree → conflict-aware merge plan*. Lessons baked in after real failures: **Sonnet 4.6** sub-agents
   (`model:'sonnet'` — Opus burned the usage cap), **slimmed prompts** (read each issue's section, not the
   650KB `issues.md` / 81k-token `PROJECT_STATE.md`), **discovery 529-retry**, a **loud lane-filter guard**,
   and **defensive args parsing** (a stringified-args bug had silently run all 19 lanes on Opus twice).
2. Ran W0 in batches (usage-cap-aware): one 5-lane batch + three 3-lane trios = **14 code-bearing lanes**,
   each committed to a `wave0/<lane>` branch.
3. **Integrated** all 14 onto `wave0-integration` in dependency order; resolved every conflict by hand
   (additive "keep both" for Beat tasks/metrics; **semantic** calls where they clashed — see below);
   renumbered the colliding migration; fixed a real F821 bug; got production code ruff-clean.
4. Hand-drafted the **4 external/runbook lanes** to `docs/runbooks/`.
5. Fast-forwarded `wave0-integration` → local `main` → local `staging`; deleted the per-lane branches +
   worktrees; wrote this handoff. **Held the push** for explicit go.

## THE 14 LANES (what's in this deploy)

| Lane (merged) | Issues shipped |
|---|---|
| ui-core | 99 mono polish · 210 per-video pipeline stepper |
| qa-release-engineering | 265/266/267/269/270/271/273/274 (QA + release-eng tooling subset) |
| activation-onboarding | 214 labeled onboarding TaskStepper + sessionStorage re-attach |
| security-platform | 226 retire legacy static UI · 229 security headers · 230 CSRF · 232 content-length guard |
| notifications-lifecycle | 242 transactional email infra (Resend, `console` dev backend) |
| scoring-eval-preference | 216 honest personalization-status surface (198 left plan-only) |
| agentic-caching-cost | 218 prompt caching · 220 Usage cost ledger (+migration 0028) · 221 model-per-task · 222 tool `is_error` · 223 DNA-cache spike |
| editorial-render | 186 Creator Brand Kit (+migration 0029) — 188/189 left plan-only |
| billing-monetization | 205 Stripe↔ledger reconcile Beat · 206 payment_status webhook guard · 207 Stripe Tax flag · 208 refund runbook · 209 Stream pack |
| carry-over-cleanup | 73 response_model long tail · 75 SEV-2 tracker close · 76 /assess residuals |
| privacy-compliance | 250 retention purge sweeps (+ Deepgram model-improvement opt-out) |
| observability | 233 log redaction backstop · 237 LLM-cost metrics · 239 durable worker log sink |
| scale-quota-load | 260 YT quota-at-scale · 264 PgBouncer image pin (worker PgBouncer sidecar, RedBeat HA) |
| security-prompt-trust-boundary | 224 untrusted-content trust boundary · 227 honesty guard + ingest clamp |

**Triaged NOT built (correctly):** the L-spikes 188/189/198 (editor/reframe/eval-harness) and the
`external` lanes — they're plan-only/runbook, see `docs/runbooks/` + `docs/issues.md`.

## VERIFICATION REALITY (important honesty)

- This dev box has **no Docker / Postgres / ffmpeg / live APIs**. `tests/conftest.py` hard-requires live
  **Redis** always, and (via an `"integration" in "not integration"` substring quirk) probes **Postgres**
  on the default run too — so the **full pytest suite cannot run here**.
- Therefore the lane build-agents' "N tests passed" claims **could not have actually run on this box** —
  treat them as **staging-pending**. What IS verified here: ruff (production code clean), `py_compile`
  (all changed Python), conflict-free merge, linear migration chain.
- Net: real behavioral verification of W0 = **Issue 275 staging** (GKE or any real PG16 + Redis).

## CONFLICT-RESOLUTION DECISIONS (so you trust the merge)

- **Additive (kept both):** privacy's `purge_stale_event_logs` + billing's `reconcile_stripe_ledger`
  (Beat entries, tasks, async helpers); agentic's cost-ledger write + observability's `record_llm_tokens`
  metric in `chat/runner.py` + `knowledge/hooks.py` (the latter keeps agentic's `(text, usage)` tuple return).
- **Semantic call — `dna/brief.py`:** agentic (Issue 223) *removed* the cache marker and kept identity in the
  system role; security (Issue 224) *moved creator-authored identity to the user turn* (the trust boundary)
  and kept cache_control. **Security (224) WON** — a prompt-injection boundary outranks a caching micro-opt.
- **Migration collision:** two `0028`s → kept agentic's `0028_usage_cost_estimate`, renumbered editorial's to
  **`0029_creator_brand_kit`** (`down_revision="0028"`). Chain is `0027 → 0028 → 0029` (verified linear).
- **Real bug found + fixed:** 7 LLM-task fns (`_build_dna_async`, `_generate_*`, `_analyze_hook_async`,
  `_generate_chapters_async`) used `settings.COST_*` with no local `from config import settings` (F821 →
  runtime `NameError`). Added local imports (module-level is deliberately avoided in `worker/tasks.py` to
  keep config-load deferred).

## RECONCILE BEFORE/AFTER MERGE (cleanup debts)

1. **`docs/DECISIONS.md` contradicts itself** — it now has BOTH the 223 "removed cache marker" and 224
   "added cache marker" entries. Decide final caching stance (security structure kept either way) and delete
   the stale entry.
2. **`docs/PROJECT_STATE.md` + `.env.example`** were **union-merged** across 14 lanes (all entries present,
   possibly unordered/duplicated). Do a de-dup/ordering pass; then write a proper PROJECT_STATE "W0 done" entry.
3. **9 `SIM117`** (nested-`with`) ruff nits in `tests/test_mailer.py` — pre-existing (notifications lane),
   test-only, not auto-fixable, non-blocking.
4. **`stream-vod-recap` (Issue 190)** lane never ran (L-spike → plan-only). Run research-only or hand-plan.

## KEY COORDINATES

| Item | Value |
|------|-------|
| Trunk now | local `main`==`staging` @ `e89574f`; `origin/main`==`origin/staging` @ `65a1d4f` (behind 31) |
| Backup ref | `wave0-integration` @ `e89574f` (identical to main; delete after push) |
| Held separate | `feat/batch-b-publish` (your YouTube-publish work — its 0027 migration may collide; renumber if it lands after this) |
| Remote | `origin` = github.com/reese8272/creatorclip.git (no `wave0/*` ever pushed) |
| Migrations added | `alembic/versions/0028_usage_cost_estimate.py`, `0029_creator_brand_kit.py` |
| Runbooks | `docs/runbooks/{24-25-26-beta-deploy-gates,255-258-dr-durability,275-279-k8s-deploy,194-youtube-publish}.md` |
| Tests | `.venv/bin/pytest` (8.3.3); needs live Redis + Postgres. `.venv/bin/ruff` for lint. |
| Wave harness | `.claude/workflows/issue-wave.js` — run W1: `Workflow({scriptPath, args:{wave:1, mode:'build', model:'sonnet', lanes:[...short tokens...]}})`; batch ~3 lanes/run to respect the usage cap; runbook/external lanes hand-draft instead. |

## POINTERS

- `docs/issues.md` — Master Roadmap (waves/lanes/batches, per-issue briefs). W1+ lanes live here.
- `docs/SOT.md` · `docs/DECISIONS.md` · `docs/COMPLIANCE.md` · `docs/CLIPPING_PRINCIPLES.md`.
- `CLAUDE.md` — project rules (One Rule: research current standard first; per-issue workflow).
- Memory: `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` (`project_wave_execution_workflow.md` has the W0 outcome + harness usage).
