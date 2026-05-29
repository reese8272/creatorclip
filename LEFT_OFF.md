# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a source of
> truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-05-28 (PM session 5 close-out — Issues 46 + 57)
**Branch:** `main` — HEAD `1a8c635` (Issue 46) committed; Issue 57 staged locally,
not yet committed at the moment this file is being written. (Commit lands next; the
`1a8c635` ref will be replaced.)
**Working tree:** Issue 57 changes staged + tested
**Sync with `origin/main`:** **+1 / 0** (Issue 46 commit `1a8c635` is local-only and has
not been pushed; Issue 57 will be a second local commit)
**Production:** ✅ green on `autoclip.studio` — last successful deploy at `d5b92df`.
Issue 46 (`1a8c635`) and Issue 57 are NOT yet on prod; deploys gate on `git push`.

---

## 1. CURRENT FOCUS

**Batch 3 of Phase 2 hardening is fully closed (Issues 39, 43, 46, 47, 57). 23 of 26
Phase 2 issues done. Batch 4 (38, 52, 56) is now unblocked and parallel-safe.**

This session shipped:

- **Issue 46** — generate-clips retry safety + 30-day floor on poll_clip_outcomes.
  Committed locally as `1a8c635`.
- **Issue 57** — automatic refund on terminal ingest failure via Celery `on_failure`
  hook + compensating `MinutePack` row. **The next commit.**
- **New Issues 58 + 59 filed** — Issue 57's UX scope ("billing history + email +
  in-app banner") was deliberately split: history-row UX shipped now; email and banner
  deferred to Issues 58 (transactional email infra) and 59 (in-app notifications
  surface). See `docs/issues.md`.

### → NEXT ACTION

1. **Commit Issue 57.** Single bundled commit. Suggested message:
   ```
   feat(billing): auto-refund on terminal ingest failure (Issue 57)
   ```
   No alembic migration needed.

2. **Push both Issue 46 + Issue 57 to origin** (currently 2 commits ahead of
   `origin/main`, neither on prod). Single push → CI runs both, Docker publish triggers
   deploy. Note: Deploy is gated on Docker publish only, so a lint regression on either
   won't block prod.

3. **Start Batch 4 — three issues, parallel-safe:**
   - **Issue 38 (SEV-1)** — sync external calls inside `async def` + held DB sessions.
     The most code-heavy of the three; touches multiple modules.
   - **Issue 52 (TESTS)** — worker pipeline integration tests. Was blocked on Issue 39,
     now unblocked.
   - **Issue 56 (RESEARCH)** — evaluate Postgres Row-Level Security for tenant-owned
     tables. Decide-and-document, no code.

   Recommend running Batch 4 via parallel agents (matches the Batch 1 / Batch 2 pattern).
   Issue 38 is the most likely to find an unexpected bug; 56 is the lightest-lift.

---

## 2. WHAT WORKS NOW (do not re-investigate)

- ✅ **23 of 26 Phase 2 hardening issues closed**: 32, 33, 34, 35, 36, 37, 39, 40, 41,
  42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 53, 54, 55, 57.
- ✅ **Test suite green locally**: `381 passed, 1 skipped, 49 deselected` (was 373 at
  session start → +6 unit, +6 integration this session across Issues 46 + 57).
- ✅ **Ruff clean** (`ruff check` AND `ruff format --check`) across the repo.
- ✅ **Production health verified earlier this session**: `{"status":"ok","postgres":"ok","redis":"ok"}`.
  Alembic head on prod: `d4e5f6a7b8c9 (head)`. Actual prod alembic binary at
  `/root/.local/bin/alembic`, NOT `.venv/bin/alembic`.
- ✅ **Celery event loop** (Issue 39): per-worker singleton loop via `worker_process_init`.
- ✅ **Source-media retention clock** (Issue 43): `videos.ingest_done_at` gates purge.
- ✅ **Analytics-refresh fairness** (Issue 47): `creators.last_analytics_refreshed_at`
  + `NULLS FIRST` ordering.
- ✅ **Generate-clips retry safety** (Issue 46): selective DELETE excludes done+running;
  `_generate_clips_async` short-circuits if any done clip exists.
- ✅ **Poll-clip-outcomes bounded** (Issue 46): clips >30 days old excluded.
- ✅ **Auto-refund on terminal ingest failure** (Issue 57): `RefundOnFailureTask` base
  class applied to `ingest_video`, `transcribe_video`, `build_signals`. Compensating
  `MinutePack` row with `reason="refund"`, `pack_id="refund:<video_id>"`. Idempotent
  via read-then-write check.
- ✅ **Alembic 0004 deployed**: revision `d4e5f6a7b8c9`. Issues 46 + 57 needed no
  schema change.

---

## 3. THE ARC THAT LED HERE

1. **Phase 1 (Issues 1–31)** closed in earlier sessions; beta live on `autoclip.studio`.
2. **Earlier Phase 2** (Issues 32–35, 40–42, 44) closed in prior sessions.
3. **2026-05-28 PM session 1** — Issue 36 OAuth lifecycle.
4. **2026-05-28 PM session 2 (Batch 1)** — Issues 37, 45, 48, 50, 53, 54.
5. **2026-05-28 PM session 3 (Batch 2)** — Issues 49, 51, 55.
6. **2026-05-28 PM session 4 (Batch 3 kickoff)** — Issues 39 + 43 + 47.
7. **2026-05-28 PM session 5 (this session)** — Issues 46 + 57:
   - Issue 46 — selective DELETE in `generate_and_rank_clips` + idempotency guard in
     `_generate_clips_async` + 30-day floor on `_poll_clip_outcomes_async`.
   - Issue 57 — automatic refund on terminal ingest failure. User delegated policy;
     industry-standard auto-refund-on-all-terminal-failures was the call.
     Email + in-app banner SPLIT to new Issues 58 + 59 (no infra exists yet for
     either; building both in one issue would explode scope).

---

## 4. KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| **Public URL** | `https://autoclip.studio` |
| **Health endpoint** | `https://autoclip.studio/health` |
| **VM** | `147.182.136.107` — Ubuntu 24.04, 4 vCPU / 8 GB, NYC1 |
| **SSH alias** | `ssh creatorclip-vm` |
| **Deploy dir on VM** | `/opt/autoclip/` |
| **Compose file on VM** | `/opt/autoclip/docker-compose.prod.yml` |
| **Alembic on prod (container)** | `/root/.local/bin/alembic` (NOT `.venv/bin/alembic`) |
| **Active Cloudflare tunnel** | `autoclip-prod` (token in `/opt/autoclip/.env`) |
| **R2 bucket** | `creatorclip-beta` |
| **Docker image** | `ghcr.io/reese8272/creatorclip:latest` |
| **GitHub repo** | `github.com/reese8272/creatorclip` (private) |
| **App secrets on VM** | `/opt/autoclip/.env` (chmod 600 — see `docs/SECRETS.md`) |
| **GH Actions secret names** | `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, `VPS_PORT`, `GHCR_TOKEN`, `PRODUCTION_URL` |
| **Test runner** | **`.venv/bin/python -m pytest -q`** (system `python3.12` is broken — see Gotchas) |
| **Lint runner** | **BOTH `.venv/bin/python -m ruff check .` AND `.venv/bin/python -m ruff format --check .`** — CI runs both. |
| **Active issue** | _(none in flight)_ — Batch 4 ready to pick up |
| **Last completed** | Issues 46 + 57 (2026-05-28 PM session 5) |
| **Latest alembic revision** | `d4e5f6a7b8c9` — `0004_video_done_creator_refreshed` (Issues 43 + 47); Issues 46 + 57 needed no migration |
| **Phase 2 progress** | 23 of 26 hardening issues closed (Batch 4 — 38, 52, 56 — remains) |
| **Test count** | 381 passed, 1 skipped, 49 deselected (431 collected) |

---

## 5. CONSTRAINTS & GOTCHAS

- **`ruff check` ≠ `ruff format --check`.** CI runs both. Always run `ruff format
  --check .` before pushing, or `ruff format .` to apply.
- **Deploy is gated on Docker publish, NOT on CI.** A lint or unit-test regression
  won't block production. (Open question for a future hardening issue.)
- **`.claude/` is now gitignored** (added in `0f02077`).
- **System `python3.12` cannot run pytest** — uv-managed Python has pydantic 2.46.4 but
  user-site has pydantic-core 2.27.2 → `SystemError` at plugin load. Always use
  `.venv/bin/python`.
- **Postgres is not running locally on this WSL machine** — integration tests
  (`@pytest.mark.integration`, 49 deselected) only run in CI / with a live Postgres.
- **Source-of-truth ordering for issue status:** `docs/PROJECT_STATE.md` and
  `docs/DECISIONS.md` lead; `docs/issues.md` follows.
- **Issue 39's `db.recreate_engine()` rebinds module globals.** `worker/tasks.py` uses
  `import db` + `db.AsyncSessionLocal(...)` style; new Celery code MUST do the same.
- **Issue 43's `ingest_done_at` write is idempotent**: `if video.ingest_done_at is None`
  guard. Don't remove.
- **Issue 47's stamp must stay inside the successful inner try, before commit.**
- **Issue 46's selective DELETE keeps `running` rows around**: deliberate. `render_clip`
  retry+timeout drives `running` → `failed` when truly stuck.
- **Issue 46's idempotency guard is binary**: any single `done` clip for the video
  short-circuits the whole task. Acceptable per DECISIONS entry.
- **Issue 46's 30-day floor is hardcoded** in `_poll_clip_outcomes_async`. Aligned
  with `preference/decay.py:sample_weight` recency-decay horizon.
- **Issue 57 refund idempotency is read-then-write (NOT a UNIQUE constraint)**.
  `MinutePack.pack_id` has no unique constraint. The narrow race window is not
  reachable in the current pipeline (one chain runs per video); if real concurrency
  emerges, add `UNIQUE (pack_id) WHERE reason = 'refund'` as a partial index.
- **Issue 57's `on_failure` swallows refund exceptions** — the worker's terminal
  failure must stand even if the refund itself crashes. Manual remediation via
  `await refund_for_video(video_id)` is supported.
- **Issue 57 only applies `RefundOnFailureTask` to the three ingest-chain tasks**.
  `generate_clips` and `render_clip` do NOT deduct minutes, so they don't need
  refund-on-failure logic. New tasks that deduct minutes MUST also use this base.
- **Alembic migration `d4e5f6a7b8c9` is bundled** (Issues 43 + 47).
- **TestClient cookie jar is session-scoped**: any test that completes an OAuth
  callback MUST `client.cookies.clear()` in teardown.
- **SQLAlchemy 2.0 async sessions cannot cross event loops** (existing constraint).
- **Google OAuth app is still in Testing mode.** Verification required before public
  launch (Issue 29).

---

## 6. WHAT'S LEFT — PHASE 2 REMAINDER

**3 issues remaining (out of 26 in Phase 2 hardening + test coverage), all parallel-safe:**

| Issue | Severity | Title | Notes |
|---|---|---|---|
| **38** | SEV-1 | Sync external calls inside `async def` + held DB sessions | Unblocked by Issue 37 ✅. Batch 4 |
| **52** | TESTS | Worker pipeline integration tests | Was blocked on Issue 39 — now UNBLOCKED. Batch 4 |
| **56** | RESEARCH | Evaluate Postgres Row-Level Security for tenant-owned tables | Decide-and-document, no code. Batch 4 |

**Batch 4** ready to run via parallel agents (matches Batch 1 + 2 pattern).

After Phase 2 closes (all 26 done), open work moves to:
- **New Issues 58 + 59** (from Issue 57's deliberate scope split): transactional email
  infrastructure + in-app notifications surface. Both unlock the refund email and
  banner UX deferred from 57.
- **Phase 3** = pre-public-launch gates: public-go-live (Issue 30), OAuth app
  verification, ToS/Privacy pages live, account-deletion endpoint hardening, billing
  tiers, eval-harness adversarial expansion. See `docs/PROJECT_STATE.md`
  "Pre-Public-Launch Gates" table.

---

## 7. POINTERS

| Doc | Purpose |
|---|---|
| `docs/PROJECT_STATE.md` | Issue table + closed-batch summaries (Phase 2: 23/26 done) |
| `docs/issues.md` | Full issue backlog + acceptance criteria; new Issues 58 + 59 stubbed |
| `docs/DECISIONS.md` | Architectural decisions — 2026-05-28 entries for Issues 32–37, 39, 40–47, 57 |
| `docs/SOT.md` | Architecture + data model |
| `docs/COMPLIANCE.md` | YouTube ToS + Billing & Refund Policy (new section, Issue 57) |
| `docs/SECRETS.md` | Every secret by NAME (no values) |
| `docs/ACCESS.md` | SSH access, CI deploy key, Cloudflare Tunnel runbook |
| `docs/DEPLOYMENT.md` | Dev setup + pre-deploy checklists |
| `docs/CLIPPING_PRINCIPLES.md` | Named principles registry cited by the clip engine |
| `CLAUDE.md` | Project rules + Check→Approve→Build→Review workflow |
| `.github/workflows/deploy.yml` | CD pipeline (gated on Docker publish, not lint/CI) |
| `alembic/versions/0004_video_done_creator_refreshed.py` | Latest migration (Issues 43 + 47); Issues 46 + 57 needed none |
| `billing/refund.py` | Issue 57 refund helper |
| `tests/test_billing_refund_integration.py` | Issue 57 regression coverage |
| `tests/test_generate_clips_retry_integration.py` | Issue 46 regression coverage |
| `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/MEMORY.md` | Auto-memory index for this project |
