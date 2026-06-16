# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a
> source of truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-06-16 (Issue 138 SEV1 bulk sweep — shipped + PR open; CI partially red)
**Branch:** `issue-138-sev1-bulk-sweep` — HEAD `6236c79`, **6 ahead / 0 behind** `origin/main`
**Working tree:** clean (all committed + pushed)
**PR:** [#19](https://github.com/reese8272/creatorclip/pull/19) → base `main`, OPEN, mergeable
**CI on the PR:** ⚠️ **partially red** — the `ruff format` Lint failure is **fixed + pushed** (commit `6236c79`, re-run pending). The **`pip-audit` gate is still red** and is the one open blocker (see NEXT ACTION #2).

---

## CURRENT FOCUS

**Issue 138 closed all 7 SEV1s from the 2026-06-09 `/assess` (verified 7→0). Code is done, pushed, PR #19 open. The only thing between here and a green, mergeable PR is the `pip-audit` CI gate — failing on newly-disclosed 2026 CVEs in pinned deps, unrelated to this PR (it would fail `main` too).**

### → NEXT ACTION

1. **Confirm the Lint gate went green** after the format push:
   ```bash
   gh run list --branch issue-138-sev1-bulk-sweep --limit 4
   ```
   Expect `Lint (ruff)` ✅. If still red: `.venv/bin/ruff format --check .` locally (pinned ruff 0.15.15, whole-repo).
2. **Decide how to clear the `pip-audit` gate** (the real blocker). 8 advisories outside the Issue-107 accepted-risk ignore list (`pyproject.toml` `[tool.pip-audit]`). Enumerate live:
   ```bash
   PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pip_audit
   ```
   Flagged-but-unignored (with fix versions): `cryptography 46.0.7→48.0.1` (GHSA-537c-gmf6-5ccf), `pytest 8.3.3→9.0.3` (CVE-2025-71176, dev-only), `python-multipart 0.0.27→0.0.30/.31` (CVE-2026-53538/53539/53540), `starlette 0.49.1→1.1.0/1.3.x` (CVE-2026-48817/48818/54282/54283). **None in the `anthropic` tree** — advisory-DB drift since the 2026-05-31 ignore-list pass, NOT an Issue-138 regression.
   - **Recommended scoping (confirm with user — it's a scope call, not mechanical):** do NOT bundle the risky bumps into the SEV1 PR. `starlette 0.49→1.x` is a FastAPI-coupled **major** bump; `pytest 8→9` a dev major. Cheapest correct unblock that matches the Issue-107 pattern: safe patch-bump `python-multipart` (→0.0.31) + `cryptography` (→48, re-test Fernet decrypt round-trip), and **add the `pytest`/`starlette` CVE IDs to the `[tool.pip-audit]` ignore list with justification**, deferring the `starlette` 1.x major to its own tracked issue.
3. **Re-run gates + push** the decision, then **merge PR #19 once all gates are green.** ⚠️ Merging to `main` triggers a production deploy — only merge when intended.

---

## WHAT WORKS NOW (don't re-investigate)

- **All 7 SEV1s fixed + re-verified** by a fresh `/assess` on this branch (`docs/assessment/REPORT.md`, 2026-06-16): **0 BLOCKER · 0 SEV1**.
- **Layer 0 green locally** (venv): ruff 0 · ruff-format clean · mypy 0 · coverage **76.15%** · bandit 0/0.
- **anthropic 0.40.0→0.105.2 bump is safe** — 967 non-integration tests pass, clip eval harness (`tests/test_clip_engine.py`) green, `test_scoring` still asserts the `ttl:"1h"` request shape, no new advisory in the anthropic dep tree.
- The 7 fixes (file-level detail in the PR #19 body): XSS escaper `static/util.js`; `analysis.html` dead-id CTA; `_expire_trials` email PII; `chapters.py` max_tokens 512→2000 + schema trim; thumbnail-patterns rate-limit + single-flight; SDK bump + ttl `type:ignore` retired; inert cache markers removed (**Sonnet 4.6 floor = 2048**, corrected in `DECISIONS.md`).
- **`pip-audit` red ≠ regression** — the 8 CVEs are all outside the anthropic tree and predate this branch.

## THE ARC THAT LED HERE

1. 2026-06-09 `/assess` → **CONDITIONAL** with 7 new SEV1s across 5 modules.
2. User asked to fix all SEV1s in bulk via the issue workflow → planned (Phase-1 research via the `claude-api` skill resolved Sonnet-4.6 cache-floor = 2048) → approved.
3. Built in 3 risk-ordered phases (A mechanical, B rate-limit, C anthropic SDK), each gated + committed.
4. Re-ran `/assess` → 7→0; pushed branch; opened PR #19.
5. Close-out found PR CI red: `ruff format` (fixed this session) + `pip-audit` (open blocker, advisory drift).

## KEY COORDINATES & FACTS

| Item | Value |
|---|---|
| Repo | `/home/reese/workspace/Youtube-Video-AI-Editor` |
| Branch / HEAD | `issue-138-sev1-bulk-sweep` / `6236c79` (6 ahead of `origin/main`) |
| PR | #19 — https://github.com/reese8272/creatorclip/pull/19 |
| Phase commits | A `e12111f` · B `1fee950` · C1 `41e5eaf` · C2 `bb78a64` · assess `283a7a2` · format `6236c79` |
| Open CI blocker | Quality-Gates → static gates → `pip_audit fail (8)` |
| pip-audit ignore list | `pyproject.toml` `[tool.pip-audit] ignore-vulns` (Issue 107, 2026-05-31) |
| Anthropic SDK pin | `anthropic==0.105.2` (`requirements.txt:35`) |
| Test runner (local) | `.venv/bin/python -m pytest -m "not integration" -p no:langsmith -q` (needs Redis up) |
| Layer-0 (local) | `PATH="$PWD/.venv/bin:$PATH" .venv/bin/python .claude/skills/production-assessment/scripts/run_layer0.py` |
| Deploy trigger | merge to `main` → `Deploy to production` workflow (auto `alembic upgrade head`) |
| Secrets | by NAME only — canonical list in `docs/SECRETS.md` (`TOKEN_ENCRYPTION_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_OAUTH_CLIENT_ID`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `JWT_SECRET`, `VOYAGE_API_KEY`) — never write values here |

## CONSTRAINTS & GOTCHAS

- **Merging PR #19 to `main` auto-deploys to production.** Only merge when all gates are green and you intend to ship.
- **`pip-audit` failing is advisory-DB drift, not this PR's fault** — but it blocks merge. `starlette 0.49→1.x` is a major, FastAPI-coupled bump; don't do it casually inside the SEV1 PR.
- **CI installs `ruff==0.15.15` (same as local venv)** and runs `ruff format --check .` whole-repo — one unformatted file anywhere fails Lint. Always run `ruff format` (not just `ruff check`) before pushing.
- **Local env: no Docker/Postgres.** Use `.venv` (NOT user-site `python3.12`, whose `langsmith` pytest plugin breaks collection — pass `-p no:langsmith`). Integration tests need Postgres and only run in CI. Redis must be up locally: `redis-server --daemonize yes --save '' --appendonly no`.
- **Out-of-scope finding logged, not fixed:** a 4th, lower-severity self-XSS `innerHTML` sink at `onboarding.html:461` (creator's own channel title) — see `docs/OFF_COURSE_BUGS.md`.
- Verdict stays **CONDITIONAL** until the deferred **Locust 300-user run** (user-side, `tests/perf/README.md`) + the `TOKEN_ENCRYPTION_KEY` rotation runbook — both pre-launch-checklist items, not code.
- **CLAUDE.md One Rule** holds for every non-trivial decision: research the current industry standard first; log deviations in `docs/DECISIONS.md`.

## POINTERS (sources of truth — this file is NOT one)

- `docs/PROJECT_STATE.md` — issue progress (Issue 138 entry current) · `docs/issues.md` — work queue
- `docs/assessment/REPORT.md` — latest verdict (2026-06-16) + ranked register · `docs/assessment/history/` — snapshots · `docs/assessment/modules/` — per-module findings
- `docs/DECISIONS.md` — design decisions (2026-06-16 entry: cache-floor, marker removal, SDK bump)
- `docs/OFF_COURSE_BUGS.md` — incidental defects (incl. the onboarding.html XSS)
- `docs/SOT.md` · `docs/COMPLIANCE.md` · `docs/CLIPPING_PRINCIPLES.md` · `docs/DEPLOYMENT.md` · `docs/BETA_LAUNCH_RUNBOOK.md` · `docs/SECRETS.md`
- `CLAUDE.md` — project rules / issue workflow · `.claude/skills/production-assessment/SKILL.md`
- Memory: `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/MEMORY.md` (`local_dev_test_env.md` refreshed this session)
