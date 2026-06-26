# LEFT_OFF — LLM lane (L20) shipped + deployed; scope locked to ≤100-user beta

> **Read this first.** Living "where we are right now" handoff for a fresh session with zero memory.
> Source-of-truth docs live in `docs/`; this file orients and points to them — it is NOT a source of truth.

**Last updated:** 2026-06-26
**Checked out:** `main` @ `51771b4` (== `origin/main` == `origin/staging` == local `staging`; 0 ahead / 0 behind).
**Working tree:** clean. **Worktrees:** only the main checkout (all wave worktrees cleaned up).
**Last prod deploy:** `51771b4` → autoclip.studio succeeded (Docker publish + Deploy: migrations → rollout →
smoke passed, no rollback).

---

## WHAT JUST HAPPENED (this session)

1. **Scope locked to a ≤100-user PRIVATE BETA** (user directive). The build-for-10k infra track is
   **DESCOPED**: Lane **L12** (K8s/GKE/KEDA — #275–280, 287) in full + most of **L13** (10k load test #261,
   PgBouncer #58/259, #262/263) + Batch API **#219**. Beta runs on Render/the existing VM. Recorded in
   `docs/DECISIONS.md` (two 2026-06-26 entries), `docs/PRD.md`, `CLAUDE.md`, `docs/PROJECT_STATE.md`, and the
   `docs/issues.md` Lane×Wave matrix (lanes annotated **DESCOPED-BETA**).
2. **New lane L20 — LLM Features & Hardening (#318–325) BUILT + MERGED + DEPLOYED**, in two Sonnet-4.6 waves:
   - **W0 (#318–321):** killed hardcoded model IDs → per-task model registry in `config.py`; **live-API E2E
     harness** (`scripts/llm_e2e.py` + `pytest -m llm_live` + `.github/workflows/llm-e2e-nightly.yml`); SDK
     conformance test (typed exceptions across all 10 LLM modules); usage-ledger guard + per-creator brief quota.
   - **W1 (#322–325):** per-clip AI Short-title + hook-rewrite, per-clip caption/overlay-text, agentic chat
     tools over clips & outcomes (creator-isolated), and an "explain this clip" narrative (cites a named
     principle). New `knowledge/clip_{titles,captions,explain}.py`, 3 `/clips/{id}/*` endpoints, 3 chat tools,
     Review-surface UI cards.
   - **Verified:** backend unit lane **1599 passed / 0 failed** (+123 tests); frontend `tsc -b`+`vite` clean,
     vitest **206 passed**. Layer 0 + the Postgres integration lane were NOT run here (no Docker/PG) — run on CI.
3. **Repo cleanup:** removed all stale wave worktrees + branches; only `main` + `staging` remain. The descoped
   #279 cosign/SBOM/SLSA work is preserved as tag **`parked/issue-279-cosign`** (re-attach if L12 is un-parked).
   An abandoned dirty worktree's uncommitted changes were saved to `/tmp/abandoned-wf127-13-uncommitted.patch`.

---

## → NEXT ACTIONS (the remaining items are external/CI, not build work)

1. **Activate the nightly live-LLM verification (#319).** Add the **`ANTHROPIC_API_KEY`** GitHub Actions repo/org
   secret, then trigger `llm-e2e-nightly` via workflow_dispatch and confirm all 6 `llm_live` tests PASS. This
   clears the deferred live assertions: `cache_read_input_tokens>0` on 2nd same-creator call, honesty disclaimer
   present in real output, typed-exception path on a bad request.
2. **Run Layer 0 + the Postgres integration lane on CI/staging** to clear: #321 Redis per-creator quota
   concurrency (51st request → 429, per-creator isolated) and #324 chat clip/outcome isolation integration test.
3. **#29 (Google OAuth app verification)** remains the one hard public-launch gate (external Google review;
   required for the YouTube scopes regardless of user count).

---

## CAN I ACTUALLY RUN EVERYTHING NOW?

- **In prod (autoclip.studio):** YES — `51771b4` is deployed with an always-on Celery worker, real Postgres/Redis,
  and the live Anthropic API, so the full pipeline (chat SSE, scoring, the new per-clip features) runs there.
- **On this dev box:** NO, not the full stack — there is **no Docker / Postgres / Redis / ffmpeg / live API key**
  here. You can run the **unit lane** (`.venv/bin/python -m pytest -m "not integration"`) and the **frontend**
  (`cd frontend && npm run build && npx vitest run`). The integration lane, render path, and live-LLM harness
  need real services (run them on CI/staging/prod).
- **The new LLM features work end-to-end only once #319's nightly proves it against the real API** — until then
  they are static-verified (mocked) + deployed, not yet live-proven.

---

## KEY COORDINATES

| Item | Value |
|------|-------|
| Trunk | `main` == `origin/main` == `origin/staging` == `staging` @ `51771b4`; deployed |
| Prod | `autoclip.studio` (self-managed VM, docker-compose; self-hosted GH runner) |
| Beta host (chosen) | **Render** — `render.yaml` at root; runbook `docs/RENDER_DEPLOY.md` |
| Deploy trigger | push `main` → "Docker publish" (GHCR) → "Deploy to production" (migrations → rollout → smoke + auto-rollback) |
| Backend tests | `.venv/bin/python -m pytest -m "not integration" -q` (needs local Redis; use `.venv`) |
| Frontend gates | `cd frontend && npm run build` · `npx vitest run` |
| Live-LLM harness | `RUN_LLM_LIVE=1 ANTHROPIC_API_KEY=… .venv/bin/python -m pytest -m llm_live` (or `python scripts/llm_e2e.py`) |
| Parked work | tag `parked/issue-279-cosign` (descoped K8s supply-chain signing) |

---

## POINTERS (the real source-of-truth docs)

- `docs/issues.md` — work queue (Lane×Wave matrix; L20 lane briefs #318–325; descoped lanes annotated).
- `docs/PROJECT_STATE.md` — status; the 2026-06-26 note records the scope change + L20 delivery.
- `docs/DECISIONS.md` — two 2026-06-26 entries (scope lock + LLM track) at the top.
- `docs/SOT.md` — architecture/stack (per-task model registry + new clip endpoints documented).
- `CLAUDE.md` — project rules (research current standard first; CHECK→APPROVE→BUILD→REVIEW per issue).
