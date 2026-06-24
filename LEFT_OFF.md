# LEFT_OFF — backend/LLM health pass SHIPPED to prod (malware-free); stealer quarantined out of history

> **Read this first.** Living "where we are right now" handoff for a fresh session with zero memory.
> Source-of-truth docs live in `docs/`; this file orients and points to them — it is NOT a source of truth.

**Last updated:** 2026-06-24
**Checked out:** `main` (== `origin/main` == `staging` == `origin/staging`). Working tree clean.
**Prod:** verified green — `/health` 200, `/` 302→`/app/dashboard`. Deploy smoke-test passed.

---

## ✅ CURRENT FOCUS — done & deployed; no active blocker

The backend/LLM health pass is **complete, verified, and live in production**. It was briefly commingled
(in a local-only commit) with an unrelated **clipboard/seed-phrase stealer**; that malware was isolated OUT,
and only the legitimate work was committed (`bf95ce2`) and shipped. The stealer was **never pushed** and exists
only in the orphaned local commit `0db9b71` (reflog), not in any remote branch or the working tree.

**→ NEXT ACTION (optional follow-ups, in priority order):**
1. **Prune the orphaned stealer commit** so it can't be resurrected: `git reflog` → confirm `0db9b71` is
   unreferenced, then `git reflog expire --expire=now --all && git gc --prune=now`. (It is already absent from
   `origin/main`, `origin/staging`, and the working tree.)
2. **Wire backend pytest into self-hosted CI** — the structural reason ~10 stale tests drifted red unnoticed.
   CI (`ci.yml`) already has the unit + integration jobs; they just need the self-hosted runner to actually run
   them (and `push:` was deliberately dropped until a 2nd runner — see `docs/runbooks/local-ci-cd.md`).
3. **Issue 275 — GKE staging** (the long-standing deploy-track linchpin) remains the next product-infra goal.

---

## WHAT WORKS NOW (verified this session — don't re-investigate)

- **LLM backend audited, no functional defects:** all ~12 Anthropic call sites use `settings.ANTHROPIC_MODEL`
  (no hardcoded models), `max_tokens ≤ 2000`, module-level singletons w/ timeout+retries. Per-creator isolation
  clean on all 5 chat tools; `chat/intake.py` injection gate sound. Web-search tool `web_search_20260209`.
  Sonnet 4.6 cacheable floor live-confirmed at **1024** tokens. SDK is `anthropic==0.105.2`.
- **Backend unit lane reliably green: `1400 passed / 0 failed` (confirmed ×3).** Was un-runnable before — fixed
  the conftest Postgres-guard substring bug and added an autouse fixture clearing `dependency_overrides`, the
  shared TestClient cookie jar, and the slowapi limiter between tests (killed the `clip_counts`/`test_data_export`
  ordering flakes). Needs local Redis up to run.
- **10 stale tests fixed to shipped state** (no real regressions): DNA-brief cache markers (Issue 224 over 223),
  brand-kit migration 0028→0029, SPA-cutover URLs, legacy-UI retirement (Issue 226), Deepgram `addons` mock
  (Issue 251), virality-negation whitelist, Signals mock, refund dispatch count.
- **Billing fix (SEV2):** `_estimate_cost_usd` now prices cached tokens (reads 0.1×, writes 1.25×/2×) instead of
  0×; threaded through `record_llm_usage` + scoring + chat. Regression test + DECISIONS entry.
- **Docs cleaned of drift:** SOT, CLAUDE.md, PROJECT_STATE, OFF_COURSE_BUGS, DECISIONS. Gates: ruff+format+mypy clean.

---

## THE ARC THAT LED HERE

1. Asked to make the backend "perfectly functional," LLM especially. Audited every Anthropic call site → sound.
2. Fixing the conftest guard made the unit lane runnable and unmasked ~10 red tests (all stale) + a SEV2 billing
   under-bill. Fixed all; lane → 1400 green (×3). Cleaned doc drift.
3. At close-out, found a local commit had bundled the work with a clipboard crypto-stealer. Refused to push it.
4. Re-isolated the legitimate work onto a clean branch, reset `main` to it (dropping the commingled commit),
   pushed `main` + matched `staging`, and the prod deploy went green. Stealer never reached any remote.

---

## KEY COORDINATES & FACTS

| Item | Value |
|------|-------|
| Trunk | `main` == `origin/main` == `staging` == `origin/staging` — all at the shipped clean commit |
| Shipped commit | `a79b456` (backend/LLM health work, malware-free) + this LEFT_OFF refresh on top |
| Prod | `autoclip.studio`; deploy chain: push `main` → "Docker publish" (→ GHCR) → "Deploy to production" (self-hosted: migrations → rollout → smoke test w/ auto-rollback → cleanup) |
| Watch a run | `gh run list --workflow deploy.yml --limit 2`; `gh run watch <id> --exit-status` |
| Orphaned malware | local commit `0db9b71` only (reflog) — the clipboard stealer; not in any remote/branch/worktree |
| Test prereq | local Redis up (`redis-server --daemonize yes --save '' --appendonly no`); unit lane needs no Postgres |
| Run the lane | `python3.12 -m pytest -q` |
| Secrets | env-only by name (`ANTHROPIC_API_KEY`, `DATABASE_URL`, `JWT_SECRET_KEY`, `TOKEN_ENCRYPTION_KEY`, …); not in repo |

---

## CONSTRAINTS & GOTCHAS

- **`tests/eval/client/` + `tests/eval/run_bot.py` were MALWARE** (a clipboard crypto-stealer: bip39 seed /
  private-key detection → exfil to a hardcoded HTTP IP → hidden Windows persistence). They are NOT in the repo
  now. The **real** eval harness is `tests/eval/scenarios/*.yaml` (clip-quality). Do not re-add/build/deploy the bot.
- **Pushing `main` triggers a PROD deploy.** Don't push unless you intend to deploy.
- **`origin/staging` is matched to `main`** as of this session — keep matching it only to a clean `main`.
- Never trust a build-agent "tests passed" — re-run gates at integration. Frontend lint baseline: 10 pre-existing.

---

## POINTERS (the real source-of-truth docs)

- `docs/PROJECT_STATE.md` — top entry summarizes this session.
- `docs/SOT.md` — architecture/stack/file layout (cleaned this session). `docs/DECISIONS.md` — incl. the
  2026-06-24 billing cache-cost entry. `docs/OFF_COURSE_BUGS.md` — test-infra fixes + the billing finding.
- `docs/COMPLIANCE.md` · `docs/CLIPPING_PRINCIPLES.md` · `docs/DEPLOYMENT.md` · `docs/runbooks/`.
- `CLAUDE.md` — project rules (research current standard first; per-issue CHECK→APPROVE→BUILD→REVIEW).
- Memory: `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/`.
