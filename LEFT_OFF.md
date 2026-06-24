# LEFT_OFF — backend/LLM health work isolated on a CLEAN branch; `main` is poisoned, do NOT push it

> **Read this first.** Living "where we are right now" handoff for a fresh session with zero memory.
> Source-of-truth docs live in `docs/`; this file orients and points to them — it is NOT a source of truth.

**Last updated:** 2026-06-24
**Checked out:** `backend-llm-health` @ `bf95ce2` (branched from `origin/main` @ `518b4ee`). Working tree clean.
**⚠️ Do NOT push `main` as-is — see CONSTRAINTS.**

---

## ⚠️ CURRENT FOCUS — ship the backend/LLM health work WITHOUT shipping the clipboard-stealer

A backend/LLM health pass is **complete and verified** but it got commingled, in a later local commit
(`0db9b71` on `main`), with an **unrelated clipboard/seed-phrase stealer** added under `tests/eval/client/`.
That malware-free, legitimate work has been **re-isolated onto branch `backend-llm-health` (`bf95ce2`)**,
which branches cleanly from `origin/main` and contains only the 21 real files. The decision left to the
human is how to land it given `main` cannot be pushed as-is.

**→ NEXT ACTION (human decides; pushing to `main` triggers a PROD deploy):**
1. **Review the clean branch** — `git diff origin/main...backend-llm-health` (21 files; no `tests/eval/client/`,
   no design-artifact blobs). Lane is green: `python3.12 -m pytest -q` → **1400 passed** (needs local Redis up:
   `redis-server --daemonize yes --save '' --appendonly no`).
2. **Decide how to land it.** Either fast-forward/merge `backend-llm-health` into a clean `main` and push, OR
   reset `main` to `origin/main` and re-commit only the legit files. **Whichever path, the stealer must NOT be
   committed/pushed.** Pushing `main` → GHCR build → **prod deploy** (`autoclip.studio`).
3. **Match `staging`** to the clean `main` once it's pushed (`git branch -f staging <clean-main> && git push origin staging`),
   per the original close-out request — only after `main` is clean.
4. **Handle the malware** in `main`/working tree: the files in `tests/eval/client/`, `tests/eval/run_bot.py`,
   `tests/eval/config.py`, `tests/eval/check_status.py`, `tests/eval/requirements.txt`, `tests/eval/data/` are
   still reachable via `main`'s commit `0db9b71`. Remove/quarantine as the human sees fit.

---

## WHAT WORKS NOW (verified this session — don't re-investigate)

- **LLM backend audited, no functional defects:** all ~12 Anthropic call sites use `settings.ANTHROPIC_MODEL`
  (no hardcoded models), `max_tokens ≤ 2000` (no non-streaming ValueError risk), module-level singletons w/
  timeout+retries. Per-creator isolation clean on all 5 chat tools; `chat/intake.py` injection gate sound.
  Web-search tool `web_search_20260209`. Sonnet 4.6 cacheable floor live-confirmed at **1024** tokens. SDK is
  `anthropic==0.105.2` (the "0.40" comments in `worker/anthropic_stream.py` are stale post-Issue-84 leftovers).
- **Test suite repaired → reliably green (1400 passed / 0 failed, confirmed ×3):** root cause was the suite
  hadn't been *running* — conftest Postgres-guard substring bug (`"integration" in "not integration…"`) +
  backend pytest not yet wired into self-hosted CI. Fixed the guard; added an autouse fixture that clears
  `dependency_overrides`, the shared TestClient cookie jar, and resets the slowapi limiter between tests
  (killed the long-standing `clip_counts` / `test_data_export` ordering flakes).
- **10 stale tests fixed to shipped state** (no real regressions): DNA-brief cache markers (Issue 224 over 223),
  brand-kit migration 0028→0029, SPA-cutover `next_action` URLs `/static/*.html`→`/app/*`, legacy-UI retirement
  (Issue 226), Deepgram `addons` mock (Issue 251), virality-negation whitelist, Signals mock, refund dispatch count.
- **Billing fix (SEV2, money path):** `_estimate_cost_usd` priced cached tokens at 0×; now prices cache reads at
  0.1× and writes at 1.25×/2× (1h-TTL for scoring), threaded through `record_llm_usage` + `chat/runner` + scoring.
  `COST_CACHE_WRITE_MULTIPLIER` added. Regression test + DECISIONS.md entry.
- **Docs cleaned of drift:** SOT (`clients.py` doesn't exist — per-module singletons; static app pages retired;
  migration COMPLETE), CLAUDE.md (Deepgram is the transcription default; React frontend; two-lane testing reality),
  PROJECT_STATE entry added, OFF_COURSE_BUGS updated.
- Gates: ruff + format + mypy clean on all changed files.

---

## THE ARC THAT LED HERE

1. Asked to make the backend "perfectly functional," LLM especially. Audited every Anthropic call site → sound.
2. Fixing the conftest Postgres-guard bug made the unit lane runnable and **unmasked ~10 red tests** (all stale,
   no prod regressions) plus a real **SEV2 billing under-bill**. Fixed all; lane → 1400 green (×3).
3. Cleaned doc drift across SOT / CLAUDE / PROJECT_STATE / OFF_COURSE_BUGS / DECISIONS.
4. At close-out, discovered the human's local commit `0db9b71` had **bundled all of the above with a clipboard
   crypto-stealer** under `tests/eval/client/`. Refused to push/deploy it.
5. **Re-isolated** the legitimate work onto clean branch `backend-llm-health` (`bf95ce2`) off `origin/main`,
   excluding the stealer + artifact blobs. That is the current state.

---

## KEY COORDINATES & FACTS

| Item | Value |
|------|-------|
| Clean, shippable branch | `backend-llm-health` @ `bf95ce2` (off `origin/main` `518b4ee`; 1 commit, 21 files, no malware) |
| `origin/main` (remote trunk) | `518b4ee` — does NOT contain `0db9b71` (the commingled commit was never pushed) |
| Poisoned local `main` | `0db9b71` — backend work **+** the clipboard-stealer in one commit. **Unpushed. Do not push.** |
| Prod | `autoclip.studio`; deploy chain: push `main` → "Docker publish" → "Deploy to production" (self-hosted runner, smoke test + auto-rollback) |
| Test prereq (local) | Redis up; unit lane runs without Postgres after this session's conftest fix |
| Run the lane | `python3.12 -m pytest -q` (default lane excludes integration/quarantine) |
| Secrets | env-only by name (`ANTHROPIC_API_KEY`, `DATABASE_URL`, `JWT_SECRET_KEY`, `TOKEN_ENCRYPTION_KEY`, …) — none in repo; not readable here |

---

## CONSTRAINTS & GOTCHAS

- **`tests/eval/client/` + `tests/eval/run_bot.py` are MALWARE, not the eval harness.** They form a clipboard
  crypto-stealer: `track_rules.py` detects wallet seed phrases / private keys (bip39 wordlist, 64-hex, 88-char
  Base58); `api_client.py`+`config.py` exfiltrate to a hardcoded `http://166.88.8.133:8000/import_data`;
  `autostart.py`/`windows_runner.py` add hidden Windows Run-key/Startup persistence. **The real eval harness is
  `tests/eval/scenarios/*.yaml`** (clip-quality), which lives in `origin/main` and is untouched. Do not build,
  improve, push, or deploy the stealer.
- **Pushing `main` triggers a prod deploy.** Combined with the above, never push `main` until it is verified
  malware-free. `origin/staging` is behind `origin/main` — only match it to a clean `main`.
- **`origin/main` is the clean base** (`518b4ee`); the malware exists only on local `main`'s `0db9b71` and in the
  untracked working tree when on that branch.
- Never trust a build-agent "tests passed" — re-run gates at integration. Frontend lint baseline: 10 pre-existing.

---

## POINTERS (the real source-of-truth docs)

- `docs/PROJECT_STATE.md` — top entry summarizes this session.
- `docs/SOT.md` — architecture/stack/file layout (cleaned this session). `docs/DECISIONS.md` — incl. the 2026-06-24
  billing cache-cost entry. `docs/OFF_COURSE_BUGS.md` — test-infra fixes logged + the billing finding.
- `docs/COMPLIANCE.md` · `docs/CLIPPING_PRINCIPLES.md` · `docs/DEPLOYMENT.md` · `docs/runbooks/`.
- `CLAUDE.md` — project rules (research current standard first; per-issue CHECK→APPROVE→BUILD→REVIEW).
- Memory: `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/`.
