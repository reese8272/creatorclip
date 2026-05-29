# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a source of
> truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-05-28 (PM session 4 close-out)
**Branch:** `main` — HEAD `2890f4d`
**Working tree:** clean (`.claude/` now gitignored — see Constraints)
**Sync with `origin/main`:** **0 / 0** (fully pushed)
**Production:** ✅ green on `autoclip.studio` — last successful deploy at `0f02077` (Issue 39);
deploy for `3365224` (Issues 43+47) completed at 01:24 UTC. Format-only `2890f4d` will redeploy
shortly.

---

## 1. CURRENT FOCUS

**Batch 3 of Phase 2 hardening is 3 of 5 closed (Issues 39, 43, 47). Two issues remain in the
batch: 46 (generate-clips retry safety + outcomes time-window bug) and 57 (refund on terminal
ingest failure — needs a Phase 1 policy decision).**

This session shipped:

- **`0f02077` Issue 39** — Celery event-loop strategy (per-worker singleton loop + engine rebind
  on `worker_process_init`).
- **`3365224` Issues 43 + 47** — bundled into alembic `0004_video_done_creator_refreshed`:
  `videos.ingest_done_at` retention clock + `creators.last_analytics_refreshed_at` fairness
  ordering.
- **`2890f4d`** — `ruff format` on the two new integration test files (CI uses `ruff format
  --check`, my local pre-commit only ran `ruff check`).

### → NEXT ACTION

1. **Confirm CI is green on `2890f4d`.**
   ```bash
   gh run list --branch main --limit 4
   ```
   The Issue 43+47 commit (`3365224`) failed CI's `ruff format --check` but Docker publish +
   Integration tests + Deploy all succeeded; deploy is gated on Docker publish only.
   `2890f4d` is the format fix — should pass clean.

2. **Verify the alembic migration landed on prod Postgres.**
   The deploy at `3365224` shipped the new migration. Confirm:
   ```bash
   curl -fsS https://autoclip.studio/health    # expect {"status":"ok","postgres":"ok","redis":"ok"}
   ssh creatorclip-vm "cd /opt/autoclip && docker compose exec app .venv/bin/alembic current"
   # expect: d4e5f6a7b8c9 (head)
   ```
   If alembic shows the prior head (`c3d4e5f6a7b8`) the deploy didn't run `alembic upgrade`.
   The Dockerfile entrypoint runs migrations on container start, so the most likely cause
   would be a startup failure — check `docker compose logs --tail 100 app`.

3. **Pick the next issue.**
   - **Issue 46 — generate-clips retry safety + outcomes time-window bug** (SEV-1):
     `_poll_clip_outcomes_async` uses `now - timedelta(hours=48)` as the floor for "48h
     checkpoints" but doesn't bound the *upper* end, so clips published >48h ago re-poll
     every cycle. `generate_clips` retry path doesn't dedupe candidates — a retry can
     double-create clip rows.
   - **Issue 57 — Refund on terminal ingest failure** (SEV-2, needs Phase 1 policy
     decision): `_set_status(IngestStatus.failed)` doesn't refund the minutes deducted
     for the failed video. Need to decide refund policy first: always refund on `failed`?
     Refund only after N retries exhausted? Refund minus a "we tried" overhead? Bring
     this to alignment before Phase 3.

   Issue 46 is the unblocked engineering work; 57 needs a product call before Phase 1
   research starts.

4. **Optional cleanup — `docs/issues.md` has stale `Status: 🔲 Not started` markers** on
   Issues **35, 37, 44** even though they're closed (per `docs/PROJECT_STATE.md` /
   `docs/DECISIONS.md`). One-shot doc fix; not blocking. Possibly other Done issues are
   stale-marked too.

---

## 2. WHAT WORKS NOW (do not re-investigate)

- ✅ **21 of 26 Phase 2 hardening issues closed**: 32, 33, 34, 35, 36, 37, 39, 40, 41, 42, 43,
  44, 45, 47, 48, 49, 50, 51, 53, 54, 55.
- ✅ **Test suite green locally**: `373 passed, 1 skipped, 43 deselected` (was 362 at session
  start → +11 unit, +2 integration this session).
- ✅ **Ruff clean** (`ruff check` AND `ruff format --check`) across the repo.
- ✅ **Production health verified**: `{"status":"ok","postgres":"ok","redis":"ok"}` on
  `autoclip.studio`. Deploys for Issues 39 and 43+47 both succeeded.
- ✅ **Celery event loop** (Issue 39): one `asyncio` loop per worker process installed by
  `worker_process_init`; engine rebound via `db.recreate_engine()` with
  `sync_engine.dispose(close=False)` so parent FDs aren't yanked. `run_async[T]` helper
  with `asyncio.run` fallback for unit tests. All 11 task bodies use it; all 16
  `AsyncSessionLocal()` sites now `db.AsyncSessionLocal()` so the rebind is picked up at
  call time.
- ✅ **Source-media retention clock** (Issue 43): `videos.ingest_done_at` is the canonical
  "ingest complete" boundary. Stamped exactly once in `_signals_async` under an
  `if video.ingest_done_at is None:` guard (Celery is at-least-once — retries must not
  refresh the stamp). Purge filter gates on `ingest_done_at IS NOT NULL AND ingest_done_at
  < cutoff`. Alembic backfilled existing `done` rows with `created_at`.
  `docs/COMPLIANCE.md` row updated.
- ✅ **Analytics-refresh fairness** (Issue 47): `creators.last_analytics_refreshed_at` +
  `ORDER BY ... NULLS FIRST, id`. Newly-connected creators jump first; yesterday's starved
  creators go first today. Stamp inside the successful inner try; rollback on
  `QuotaExhaustedError` un-stamps by design.
- ✅ **Alembic 0004 deployed**: revision `d4e5f6a7b8c9` (`0004_video_done_creator_refreshed`)
  bundles both Issue 43 + 47 schema changes. Partial index `ix_videos_purge_candidates`,
  ordering index `ix_creators_refresh_order`. Single transaction.
- ✅ **All prior session work** (see `docs/PROJECT_STATE.md` for Batches 1 + 2 + Issue 36).

---

## 3. THE ARC THAT LED HERE

1. **Phase 1 (Issues 1–31)** closed in earlier sessions; beta live on `autoclip.studio`.
2. **Earlier Phase 2** (Issues 32–35, 40–42, 44) closed in prior sessions.
3. **2026-05-28 PM session 1** — Issue 36 OAuth lifecycle.
4. **2026-05-28 PM session 2 (Batch 1)** — Issues 37, 45, 48, 50, 53, 54 via parallel agents.
5. **2026-05-28 PM session 3 (Batch 2)** — Issues 49, 51, 55 via parallel agents; two real
   test-infra bugs caught at merge time.
6. **2026-05-28 PM session 4 (this session)** — Batch 3 kickoff:
   - Issue 39 (Celery event-loop strategy) — foundational; unblocks Issue 52.
   - Issue 43 (source-media purge correctness) — SEV-1 retention-window race.
   - Issue 47 (analytics-refresh fairness) — SEV-2 starvation; bundled migration with 43.
   - One CI hiccup: ruff *format* (not just `check`) failed on the new integration test
     files. Fixed in `2890f4d`. Production unaffected because Deploy is gated on Docker
     publish, not on the lint job.

---

## 4. KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| **Public URL** | `https://autoclip.studio` |
| **Health endpoint** | `https://autoclip.studio/health` |
| **VM** | `147.182.136.107` — Ubuntu 24.04, 4 vCPU / 8 GB, NYC1 |
| **SSH alias** | `ssh creatorclip-vm` |
| **Deploy dir on VM** | `/opt/autoclip/` |
| **Active Cloudflare tunnel** | `autoclip-prod` (token in `/opt/autoclip/.env`) |
| **R2 bucket** | `creatorclip-beta` |
| **Docker image** | `ghcr.io/reese8272/creatorclip:latest` |
| **GitHub repo** | `github.com/reese8272/creatorclip` (private) |
| **App secrets on VM** | `/opt/autoclip/.env` (chmod 600 — see `docs/SECRETS.md` for the key list) |
| **GH Actions secret names** | `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, `VPS_PORT`, `GHCR_TOKEN`, `PRODUCTION_URL` |
| **Test runner** | **`.venv/bin/python -m pytest -q`** (system `python3.12` is broken — see Gotchas) |
| **Lint runner** | **BOTH `.venv/bin/python -m ruff check .` AND `.venv/bin/python -m ruff format --check .`** — CI runs both. |
| **Active issue** | _(none in flight)_ — Batch 3 remainder: Issues 46 + 57 |
| **Last completed** | Issues 39, 43, 47 (2026-05-28 PM session 4) |
| **Latest alembic revision** | `d4e5f6a7b8c9` — `0004_video_done_creator_refreshed` (bundles Issues 43 + 47) |
| **Phase 2 progress** | 21 of 26 hardening issues closed |
| **Test count** | 373 passed, 1 skipped, 43 deselected (416 collected) |

---

## 5. CONSTRAINTS & GOTCHAS

- **`ruff check` ≠ `ruff format --check`.** CI runs both; my local pre-commit only ran the
  first. Always run `ruff format --check .` before pushing, or `ruff format .` to apply.
- **Deploy is gated on Docker publish, NOT on CI.** A lint or unit-test regression won't
  block production. The Issue 43+47 commit `3365224` shipped to prod even with a lint
  failure. (Open question for a future hardening issue — should CI gate deploy?)
- **`.claude/` is now gitignored** (added in `0f02077`). LEFT_OFF previously claimed this;
  it wasn't actually true until this session.
- **System `python3.12` cannot run pytest** — uv-managed Python has pydantic 2.46.4 but
  user-site has pydantic-core 2.27.2 → `SystemError` at plugin load. Always use
  `.venv/bin/python`. Fix (deferred):
  `python3.12 -m pip install --user --break-system-packages "pydantic-core>=2.46.4"`.
- **Postgres is not running locally on this WSL machine** — integration tests
  (`@pytest.mark.integration`, 43 deselected) only run in CI / with a live Postgres.
  Default `pytest -q` excludes them via `pytest.ini` `addopts = -m "not integration"`.
- **`docs/issues.md` has stale `Status: 🔲 Not started` markers** on Issues 35, 37, 44 even
  though they're done (per `PROJECT_STATE.md` and `DECISIONS.md`). Trust PROJECT_STATE
  / DECISIONS as the source of truth. Worth a one-shot cleanup pass.
- **Issue 39's `db.recreate_engine()` rebinds module globals**, but `from db import
  AsyncSessionLocal` captures a stale reference. `worker/tasks.py` was switched to
  `import db` + `db.AsyncSessionLocal(...)`. **Other consumers** (`scripts/rotate_token_key.py`,
  tests that `from db import AsyncSessionLocal`) are OK because they call only in the
  main process (no fork). New Celery code MUST use `import db` style.
- **Issue 43's `ingest_done_at` write is idempotent**: `if video.ingest_done_at is None`
  guard. Without it, Celery's at-least-once redelivery would silently extend retention.
  Don't remove that guard.
- **Issue 47's stamp must stay inside the successful inner try, before commit.** Moving
  it outside (e.g. into a finally) would advance the timestamp on quota exhaustion,
  re-introducing the starvation bug.
- **Alembic migration `d4e5f6a7b8c9` is bundled** (Issues 43 + 47). Rolling back one
  rolls back both. Acceptable: both columns are nullable-additive and low-blast-radius.
- **TestClient cookie jar is session-scoped** (existing constraint). Any test that
  completes an OAuth callback / cookie-setting flow MUST `client.cookies.clear()` in
  teardown.
- **SQLAlchemy 2.0 async sessions cannot cross event loops** (existing constraint).
  TestClient runs handlers in its own loop — never share the test's `db_session` with a
  TestClient request via `dependency_overrides`.
- **Google OAuth app is still in Testing mode.** Verification required before public
  launch (Issue 29).

---

## 6. WHAT'S LEFT — PHASE 2 REMAINDER

**5 issues remaining (out of 26 in Phase 2 hardening + test coverage):**

| Issue | Severity | Title | Notes |
|---|---|---|---|
| **38** | SEV-1 | Sync external calls inside `async def` + held DB sessions | Unblocked by Issue 37 ✅. Batch 4 |
| **46** | SEV-1 | Generate-clips retry safety + outcomes time-window bug | Batch 3. Next engineering pick |
| **52** | TESTS | Worker pipeline integration tests | Was blocked on Issue 39 — now UNBLOCKED. Batch 4 |
| **56** | RESEARCH | Evaluate Postgres Row-Level Security for tenant-owned tables | Decide-and-document, no code. Batch 4 |
| **57** | SEV-2 | Refund on terminal ingest failure | Needs Phase 1 policy decision before research starts. Batch 3 |

**Batch 3 (worker/tasks.py-heavy, serial):** 46, 57. Both touch the worker pipeline; 57 also
needs a policy call.

**Batch 4 (now unblocked, parallel-safe):** 38, 52, 56.

After Phase 2 closes (all 26 done), the open work is **Phase 3** = pre-public-launch gates:
public-go-live (Issue 30), OAuth app verification, ToS/Privacy pages live, account-deletion
endpoint hardening, billing tiers, eval-harness adversarial expansion. See
`docs/PROJECT_STATE.md` "Pre-Public-Launch Gates" table.

---

## 7. POINTERS

| Doc | Purpose |
|---|---|
| `docs/PROJECT_STATE.md` | Issue table + closed-batch summaries (Phase 2: 21/26 done) |
| `docs/issues.md` | Full issue backlog with acceptance criteria — **caveat**: stale `Status` markers on 35/37/44 (they're done) |
| `docs/DECISIONS.md` | Architectural decisions — 2026-05-28 entries for Issues 32–37, 39, 40–45, 47 |
| `docs/SOT.md` | Architecture + data model |
| `docs/COMPLIANCE.md` | YouTube ToS + Findings & Fixes Log (retention-clock row updated for Issue 43) |
| `docs/SECRETS.md` | Every secret by NAME (no values) |
| `docs/ACCESS.md` | SSH access, CI deploy key, Cloudflare Tunnel runbook |
| `docs/DEPLOYMENT.md` | Dev setup + pre-deploy checklists |
| `docs/CLIPPING_PRINCIPLES.md` | Named principles registry cited by the clip engine |
| `CLAUDE.md` | Project rules + Check→Approve→Build→Review workflow |
| `.github/workflows/deploy.yml` | CD pipeline (gated on Docker publish, not lint/CI) |
| `alembic/versions/0004_video_done_creator_refreshed.py` | Latest migration — Issues 43 + 47 |
| `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/MEMORY.md` | Auto-memory index for this project |
