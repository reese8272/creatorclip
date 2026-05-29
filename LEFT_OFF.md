# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a source of
> truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-05-28 (PM session 5 close-out — Issue 46)
**Branch:** `main` — HEAD `d5b92df` (pre-Issue-46 commit; this session's commit lands next)
**Working tree:** Issue 46 changes staged + tested but not yet committed
**Sync with `origin/main`:** **0 / 0** at last push
**Production:** ✅ green on `autoclip.studio` — last successful deploy at `e928197`;
`d5b92df` deploy completed mid-session (brief 502 observed during container restart,
recovered <30s). Alembic head on prod confirmed: `d4e5f6a7b8c9` (Issues 43+47 schema).

---

## 1. CURRENT FOCUS

**Batch 3 of Phase 2 hardening is 4 of 5 closed (Issues 39, 43, 46, 47). One issue
remains in the batch: 57 (refund on terminal ingest failure — still needs a Phase 1
policy decision before research starts).**

This session shipped:

- **Issue 46** — generate-clips retry safety + outcomes 30-day floor. Three changes:
  1. `clip_engine/ranking.py:generate_and_rank_clips` — DELETE narrowed to exclude
     `RenderStatus.done` and `RenderStatus.running` rows.
  2. `worker/tasks.py:_generate_clips_async` — early-return idempotency guard: if any
     `done` clip exists for the video, log + return without touching the timeline.
  3. `worker/tasks.py:_poll_clip_outcomes_async` — added 30-day floor on `Clip.created_at`
     to bound the previously-unbounded 7d re-poll arm.
- **Tests** — 2 new unit predicates in `tests/test_outcomes.py`; 3 new integration tests
  in `tests/test_generate_clips_retry_integration.py` (covers all three regressions).
- **Docs** — `DECISIONS.md` entry; `PROJECT_STATE.md` close-out; `issues.md` flipped
  Issue 46 → ✅ Done.

### → NEXT ACTION

1. **Commit Issue 46.** Single bundled commit. Suggested message:
   ```
   fix(worker): retry-safe generate_clips + 30d floor on poll_clip_outcomes (Issue 46)
   ```
   No alembic migration needed for this issue.

2. **Watch CI green and the auto-deploy land.** Same gates as before — Deploy is
   gated on Docker publish only.

3. **Decide the next pick.** Two paths:
   - **Issue 57 (Batch 3 close-out)** — refund-on-terminal-failure. Still needs a
     Phase 1 policy decision: always refund on `failed`? Refund only after retries
     exhausted? Refund minus a fixed "we tried" overhead? Bring to alignment
     before starting any research.
   - **Batch 4 (parallel-safe, all now unblocked):** Issues 38, 52, 56.
     - Issue 38 (SEV-1): sync external calls inside `async def` + held DB sessions.
     - Issue 52 (TESTS): worker pipeline integration tests — was blocked on Issue
       39, now ready.
     - Issue 56 (RESEARCH): evaluate Postgres Row-Level Security for tenant tables —
       decide-and-document, no code.

   Recommendation: get the Issue 57 policy call done, then run Batch 4 in parallel.

---

## 2. WHAT WORKS NOW (do not re-investigate)

- ✅ **22 of 26 Phase 2 hardening issues closed**: 32, 33, 34, 35, 36, 37, 39, 40, 41,
  42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 53, 54, 55.
- ✅ **Test suite green locally**: `375 passed, 1 skipped, 46 deselected` (was 373 at
  session start → +2 unit, +3 integration this session).
- ✅ **Ruff clean** (`ruff check` AND `ruff format --check`) across the repo.
- ✅ **Production health verified**: `{"status":"ok","postgres":"ok","redis":"ok"}` on
  `autoclip.studio`. Alembic head on prod confirmed `d4e5f6a7b8c9 (head)` —
  note the actual prod binary is at `/root/.local/bin/alembic`, NOT `.venv/bin/alembic`
  (prior LEFT_OFF was stale on this path).
- ✅ **Celery event loop** (Issue 39): one `asyncio` loop per worker process installed by
  `worker_process_init`; engine rebound via `db.recreate_engine()` with
  `sync_engine.dispose(close=False)`. All 11 task bodies use `run_async`; all 16
  `AsyncSessionLocal()` sites use the `import db` style so the rebind is picked up
  at call time.
- ✅ **Source-media retention clock** (Issue 43): `videos.ingest_done_at` is the canonical
  "ingest complete" boundary. Stamped idempotently in `_signals_async`.
- ✅ **Analytics-refresh fairness** (Issue 47): `creators.last_analytics_refreshed_at` +
  `ORDER BY ... NULLS FIRST, id`.
- ✅ **Generate-clips retry safety** (Issue 46): selective DELETE + idempotency
  early-return. Done/running rows are now guaranteed to survive a late retry. A retry
  on an already-rendered video is a no-op.
- ✅ **Poll-clip-outcomes bounded** (Issue 46): clips >30 days old drop out of the poll
  set. Stops the unbounded 7d re-poll quota burn.
- ✅ **Alembic 0004 deployed**: revision `d4e5f6a7b8c9`
  (`0004_video_done_creator_refreshed`). Partial index `ix_videos_purge_candidates`,
  ordering index `ix_creators_refresh_order`.
- ✅ **All prior session work** (see `docs/PROJECT_STATE.md` for Batches 1 + 2 + Issue 36).

---

## 3. THE ARC THAT LED HERE

1. **Phase 1 (Issues 1–31)** closed in earlier sessions; beta live on `autoclip.studio`.
2. **Earlier Phase 2** (Issues 32–35, 40–42, 44) closed in prior sessions.
3. **2026-05-28 PM session 1** — Issue 36 OAuth lifecycle.
4. **2026-05-28 PM session 2 (Batch 1)** — Issues 37, 45, 48, 50, 53, 54 via parallel agents.
5. **2026-05-28 PM session 3 (Batch 2)** — Issues 49, 51, 55 via parallel agents.
6. **2026-05-28 PM session 4 (Batch 3 kickoff)** — Issues 39 + 43 + 47.
7. **2026-05-28 PM session 5 (this session)** — Issue 46:
   - Bug A: selective DELETE in `generate_and_rank_clips` + idempotency guard in
     `_generate_clips_async`.
   - Bug B: 30-day floor on `_poll_clip_outcomes_async` (LEFT_OFF previously described
     the 48h arm as unbounded — actually the 48h arm self-bounds via `performed_well
     IS NULL`; the bug was in the 7d arm).

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
| **App secrets on VM** | `/opt/autoclip/.env` (chmod 600 — see `docs/SECRETS.md` for the key list) |
| **GH Actions secret names** | `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, `VPS_PORT`, `GHCR_TOKEN`, `PRODUCTION_URL` |
| **Test runner** | **`.venv/bin/python -m pytest -q`** (system `python3.12` is broken — see Gotchas) |
| **Lint runner** | **BOTH `.venv/bin/python -m ruff check .` AND `.venv/bin/python -m ruff format --check .`** — CI runs both. |
| **Active issue** | _(none in flight)_ — Batch 3 remainder: Issue 57 only |
| **Last completed** | Issue 46 (2026-05-28 PM session 5) |
| **Latest alembic revision** | `d4e5f6a7b8c9` — `0004_video_done_creator_refreshed` (Issues 43 + 47); Issue 46 needed no migration |
| **Phase 2 progress** | 22 of 26 hardening issues closed |
| **Test count** | 375 passed, 1 skipped, 46 deselected (422 collected) |

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
  (`@pytest.mark.integration`, 46 deselected) only run in CI / with a live Postgres.
- **Source-of-truth ordering for issue status:** `docs/PROJECT_STATE.md` and
  `docs/DECISIONS.md` lead; `docs/issues.md` follows.
- **Issue 39's `db.recreate_engine()` rebinds module globals.** `worker/tasks.py` uses
  `import db` + `db.AsyncSessionLocal(...)` style; new Celery code MUST do the same.
- **Issue 43's `ingest_done_at` write is idempotent**: `if video.ingest_done_at is None`
  guard. Don't remove.
- **Issue 47's stamp must stay inside the successful inner try, before commit.**
- **Issue 46's selective DELETE keeps `running` rows around**: that's deliberate. A
  separate `render_clip` retry+timeout drives `running` → `failed` when truly stuck;
  the next `generate_clips` retry sweeps the failed row out cleanly.
- **Issue 46's idempotency guard is binary**: any single `done` clip for the video
  short-circuits the whole task. A partially-rendered video won't get its
  `failed` candidates re-ranked — but `render_clip` retries those independently.
- **Issue 46's 30-day floor is hardcoded** in `_poll_clip_outcomes_async`. It's
  aligned with the `preference/decay.py:sample_weight` recency-decay horizon; if
  either ever moves, the other should follow.
- **Alembic migration `d4e5f6a7b8c9` is bundled** (Issues 43 + 47).
- **TestClient cookie jar is session-scoped**: any test that completes an OAuth
  callback MUST `client.cookies.clear()` in teardown.
- **SQLAlchemy 2.0 async sessions cannot cross event loops** (existing constraint).
- **Google OAuth app is still in Testing mode.** Verification required before public
  launch (Issue 29).

---

## 6. WHAT'S LEFT — PHASE 2 REMAINDER

**4 issues remaining (out of 26 in Phase 2 hardening + test coverage):**

| Issue | Severity | Title | Notes |
|---|---|---|---|
| **38** | SEV-1 | Sync external calls inside `async def` + held DB sessions | Unblocked by Issue 37 ✅. Batch 4 |
| **52** | TESTS | Worker pipeline integration tests | Was blocked on Issue 39 — now UNBLOCKED. Batch 4 |
| **56** | RESEARCH | Evaluate Postgres Row-Level Security for tenant-owned tables | Decide-and-document, no code. Batch 4 |
| **57** | SEV-2 | Refund on terminal ingest failure | Needs Phase 1 policy decision before research starts. Batch 3 close-out |

**Batch 3 close-out:** 57 (needs policy call).
**Batch 4 (parallel-safe):** 38, 52, 56.

After Phase 2 closes (all 26 done), the open work is **Phase 3** = pre-public-launch gates:
public-go-live (Issue 30), OAuth app verification, ToS/Privacy pages live, account-deletion
endpoint hardening, billing tiers, eval-harness adversarial expansion. See
`docs/PROJECT_STATE.md` "Pre-Public-Launch Gates" table.

---

## 7. POINTERS

| Doc | Purpose |
|---|---|
| `docs/PROJECT_STATE.md` | Issue table + closed-batch summaries (Phase 2: 22/26 done) |
| `docs/issues.md` | Full issue backlog with acceptance criteria — aligned with PROJECT_STATE as of Issue 46 |
| `docs/DECISIONS.md` | Architectural decisions — 2026-05-28 entries for Issues 32–37, 39, 40–47 |
| `docs/SOT.md` | Architecture + data model |
| `docs/COMPLIANCE.md` | YouTube ToS + Findings & Fixes Log |
| `docs/SECRETS.md` | Every secret by NAME (no values) |
| `docs/ACCESS.md` | SSH access, CI deploy key, Cloudflare Tunnel runbook |
| `docs/DEPLOYMENT.md` | Dev setup + pre-deploy checklists |
| `docs/CLIPPING_PRINCIPLES.md` | Named principles registry cited by the clip engine |
| `CLAUDE.md` | Project rules + Check→Approve→Build→Review workflow |
| `.github/workflows/deploy.yml` | CD pipeline (gated on Docker publish, not lint/CI) |
| `alembic/versions/0004_video_done_creator_refreshed.py` | Latest migration — Issues 43 + 47 (Issue 46 needed no schema change) |
| `tests/test_generate_clips_retry_integration.py` | Issue 46 regression coverage |
| `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/MEMORY.md` | Auto-memory index for this project |
