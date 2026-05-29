# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a source of
> truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-05-28 (PM session 5 — Phase 2 closed + Issue 60 done)
**Branch:** `main` — 6 commits ahead of `origin/main` after the Issue 60 commit
(Issues 46, 57, 56, 52, 38 W1, 60 all committed locally; one push will deploy all).
**Working tree:** clean after Issue 60 commit.
**Sync with `origin/main`:** **+6 / 0**.
**Production:** ✅ green on `autoclip.studio` — last successful deploy at `d5b92df`.
None of this session's commits are on prod yet; awaiting `git push`. **Note**: Issue
60's migration `e5f6a7b8c9d0` requires the one-time prod SQL ops in
`docs/DEPLOYMENT.md` BEFORE the alembic upgrade runs — see "RLS one-time setup"
section.

---

## 1. CURRENT FOCUS

**Phase 2 hardening is fully closed: 26 of 26 issues done. Issue 60 (the RLS implementation split from Issue 56) also shipped.** This session's contributions:

- **Issue 46** — generate-clips retry safety + outcomes 30-day floor. ✅ Committed `1a8c635`.
- **Issue 57** — automatic refund on terminal ingest failure. ✅ Committed `1855035`.
- **Issue 56** — Postgres RLS decide-and-document; adopt-now. ✅ Committed `877eb43`.
- **Issue 52** — worker pipeline integration tests. ✅ Committed `7ec3c1c`.
- **Issue 38 Wave 1** — Celery hot-path sync-in-async fixes. ✅ Committed `2c53959`.
  Wave 2 tracked as **Issue 61**.
- **Issue 60** — Postgres RLS implementation: alembic `0005_rls_policies`, role split,
  `AdminSessionLocal` for worker tasks, `after_begin` listener, runbook. ✅ Committed.

**Five new issues filed this session** for split-out work:
- **Issue 58** — transactional email infrastructure (refund email; future password reset, verification, comms)
- **Issue 59** — in-app notifications surface (refund banner; future deploy notices, quota warnings)
- **Issue 60** — Postgres RLS implementation per Issue 56 decision
- **Issue 61** — Issue 38 Wave 2: AsyncAnthropic/AsyncVoyage migration + router session-order + load test

### → NEXT ACTION

1. **One-time prod SQL ops** before pushing (Issue 60 migration requires this) — see
   `docs/DEPLOYMENT.md` "RLS one-time setup": `ALTER ROLE creatorclip_migrate
   BYPASSRLS`, set role passwords, transfer table ownership, update
   `/opt/autoclip/.env` with `DATABASE_MIGRATION_URL`. The Dockerized
   alembic upgrade will then run cleanly.
2. **Push all 6 commits to origin** in one go after the prod prep. CI runs; Docker
   publish triggers prod deploy for all commits.
3. **Three issues remain in the codebase backlog**:
   - **Issue 61 (Issue 38 Wave 2)** — AsyncAnthropic / AsyncVoyage migration; router
     session-order refactor; pool starvation load test. Closes the remaining ~9 of 23
     findings from the Issue 38 audit.
   - **Issue 58** — transactional email infrastructure. First consumer: refund email
     (Issue 57 carry-over).
   - **Issue 59** — in-app notifications surface. First consumer: refund banner
     (Issue 57 carry-over).
4. After those land, the only open work is **Phase 3 = pre-public-launch gates**:
   public-go-live (Issue 30), Google OAuth app verification (external), eval-harness
   adversarial expansion. See `docs/PROJECT_STATE.md` "Pre-Public-Launch Gates" table.

---

## 2. WHAT WORKS NOW (do not re-investigate)

- ✅ **All 26 Phase 2 hardening issues closed**: 32–57 (with 38 closed as "Wave 1 done";
  Wave 2 separated to Issue 61).
- ✅ **Test suite green locally**: `381 passed, 1 skipped, 54 deselected` (was 373 at session
  start → +6 unit, +8 integration this session: +2 outcomes predicate, +3 generate-clips
  integration, +3 refund integration, +5 worker pipeline integration).
- ✅ **Ruff clean** (`ruff check` AND `ruff format --check`).
- ✅ **Production health verified earlier this session**: `{"status":"ok","postgres":"ok","redis":"ok"}`.
  Alembic head on prod: `d4e5f6a7b8c9 (head)`. Actual prod alembic binary at
  `/root/.local/bin/alembic`, NOT `.venv/bin/alembic`.
- ✅ **Celery event loop** (Issue 39): per-worker singleton loop.
- ✅ **Source-media retention clock** (Issue 43): `ingest_done_at` gate.
- ✅ **Analytics-refresh fairness** (Issue 47): `last_analytics_refreshed_at` ORDER BY.
- ✅ **Generate-clips retry safety** (Issue 46): selective DELETE + idempotency early-return.
- ✅ **Poll-clip-outcomes bounded** (Issue 46): 30-day floor on `Clip.created_at`.
- ✅ **Auto-refund on terminal ingest failure** (Issue 57): `RefundOnFailureTask` base class.
- ✅ **Worker pipeline integration tests** (Issue 52): all 7 async functions + 5 ACs pinned.
- ✅ **Postgres RLS decision** (Issue 56): adopt-now decision shipped; implementation = Issue 60.
- ✅ **Celery hot-path async correctness** (Issue 38 W1): `worker/storage.py` async wrappers
  in use; all sync calls in `_ingest_async`/`_transcribe_async`/`_signals_async`/
  `_render_clip_async`/`_build_dna_async`/`_purge_stale_source_media_async` are now
  thread-offloaded; `dna/embeddings.py` has `_aembed`.
- ✅ **Alembic 0004 deployed**: revision `d4e5f6a7b8c9`. Issues 46, 52, 56, 57 needed no
  schema change. Issue 38 W1 needed no schema change.

---

## 3. THE ARC THAT LED HERE

1. **Phase 1 (Issues 1–31)** closed in earlier sessions.
2. **Earlier Phase 2** (32–35, 40–42, 44) closed in prior sessions.
3. **2026-05-28 PM session 1** — Issue 36.
4. **2026-05-28 PM session 2 (Batch 1)** — Issues 37, 45, 48, 50, 53, 54.
5. **2026-05-28 PM session 3 (Batch 2)** — Issues 49, 51, 55.
6. **2026-05-28 PM session 4 (Batch 3 kickoff)** — Issues 39, 43, 47.
7. **2026-05-28 PM session 5 (this session)** — Issues 46, 57, 56, 52, 38 W1 — **Phase 2 closes**.

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
| **Alembic on prod (container)** | `/root/.local/bin/alembic` |
| **R2 bucket** | `creatorclip-beta` |
| **Docker image** | `ghcr.io/reese8272/creatorclip:latest` |
| **GitHub repo** | `github.com/reese8272/creatorclip` (private) |
| **App secrets on VM** | `/opt/autoclip/.env` (chmod 600 — see `docs/SECRETS.md`) |
| **Test runner** | **`.venv/bin/python -m pytest -q`** |
| **Lint runner** | **BOTH `.venv/bin/python -m ruff check .` AND `.venv/bin/python -m ruff format --check .`** |
| **Active issue** | _(none in flight)_ — Phase 2 closed; pick Issue 60 / 61 / 58 / 59 / Phase 3 |
| **Last completed** | Issue 38 W1 (2026-05-28 PM session 5) |
| **Latest alembic revision** | `d4e5f6a7b8c9` — `0004_video_done_creator_refreshed` |
| **Phase 2 progress** | **26 of 26 closed** |
| **Test count** | 381 passed, 1 skipped, 54 deselected (436 collected) |

---

## 5. CONSTRAINTS & GOTCHAS

- **`ruff check` ≠ `ruff format --check`.** CI runs both.
- **Deploy is gated on Docker publish, NOT on CI.**
- **`.claude/` is gitignored.**
- **System `python3.12` cannot run pytest** — always use `.venv/bin/python`.
- **Postgres is not running locally** — integration tests (54 deselected) only run in CI.
- **Source-of-truth ordering for issue status:** `docs/PROJECT_STATE.md` and
  `docs/DECISIONS.md` lead; `docs/issues.md` follows.
- **Issue 39's `db.recreate_engine()` rebinds module globals.** `worker/tasks.py` uses
  `import db` + `db.AsyncSessionLocal(...)`. New Celery code MUST use this style.
- **Issue 43's `ingest_done_at` write is idempotent**: `if video.ingest_done_at is None`
  guard. Don't remove.
- **Issue 47's stamp must stay inside the successful inner try, before commit.**
- **Issue 46's selective DELETE keeps `running` rows around**: deliberate.
- **Issue 46's idempotency guard is binary**: any single `done` clip short-circuits.
- **Issue 46's 30-day floor is hardcoded** — aligned with `preference/decay.py:sample_weight`.
- **Issue 57 refund idempotency is read-then-write (NOT a UNIQUE constraint)**. Race
  not reachable in current pipeline; if real concurrency emerges, add partial unique
  index `UNIQUE (pack_id) WHERE reason = 'refund'`.
- **Issue 57's `on_failure` swallows refund exceptions** by design.
- **Issue 57 only applies `RefundOnFailureTask` to the three ingest-chain tasks.**
- **Issue 38 W1 created `worker/storage.py` async wrappers** — new async code MUST
  prefer `aupload_file` / `adelete_file` / `adelete_prefix` / `alocal_path` over the
  sync equivalents. The sync versions remain for sync callers (none currently).
- **Issue 38 W1 `_purge_stale_source_media_async` is now two-session**: select
  tuples → close → boto3 loop → reopen → single UPDATE. Tests in
  `test_retention_tasks.py` patch the new shape.
- **Issue 56 RLS is decided but not implemented** — Issue 60 implementation must
  split DB roles before any policies are deployed (the app role must not own the
  tables or it bypasses RLS).
- **Issue 52 tests cannot be validated locally** (no Postgres in WSL); designed
  off established patterns from `test_purge_integration.py` / `test_billing_idempotency.py`.
- **Alembic migration `d4e5f6a7b8c9` is bundled** (Issues 43 + 47).
- **TestClient cookie jar is session-scoped**: clear in teardown after OAuth callbacks.
- **SQLAlchemy 2.0 async sessions cannot cross event loops**.
- **Google OAuth app is still in Testing mode.** Verification required before public launch (Issue 29).

---

## 6. WHAT'S LEFT

**Phase 2 (hardening): CLOSED.** 26 of 26 issues done.

**New issues filed this session (Phase 2 carry-over → Phase 3 pre-launch hardening):**

| Issue | Severity | Title | Notes |
|---|---|---|---|
| **58** | FEATURE | Transactional email infrastructure | Unblocks Issue 57's refund email |
| **59** | FEATURE | In-app notifications surface | Unblocks Issue 57's refund banner |
| **60** | SEV-2 | Implement Postgres RLS per Issue 56 decision | Highest-value pre-launch hardening |
| **61** | SEV-2 | Issue 38 Wave 2 — AsyncAnthropic + router session-order + load test | Closes pool-starvation under web load |

**Phase 3 = pre-public-launch gates**: public-go-live (Issue 30), OAuth app verification,
ToS/Privacy pages live, account-deletion endpoint hardening, billing tiers, eval-harness
adversarial expansion. See `docs/PROJECT_STATE.md` "Pre-Public-Launch Gates" table.

---

## 7. POINTERS

| Doc | Purpose |
|---|---|
| `docs/PROJECT_STATE.md` | Issue table + closed-batch summaries (Phase 2: **26/26 done**) |
| `docs/issues.md` | Full issue backlog; Issues 58–61 newly filed |
| `docs/DECISIONS.md` | 2026-05-28 entries for Issues 32–37, 39, 40–47, 56, 57 |
| `docs/SOT.md` | Architecture + data model |
| `docs/COMPLIANCE.md` | YouTube ToS + Billing & Refund Policy (Issue 57) |
| `docs/SECRETS.md` | Every secret by NAME |
| `docs/ACCESS.md` | SSH + Cloudflare Tunnel runbook |
| `docs/DEPLOYMENT.md` | Dev setup + pre-deploy checklists |
| `docs/CLIPPING_PRINCIPLES.md` | Named principles registry |
| `CLAUDE.md` | Project rules + workflow |
| `.github/workflows/deploy.yml` | CD pipeline (gated on Docker publish, not lint/CI) |
| `alembic/versions/0004_video_done_creator_refreshed.py` | Latest migration |
| `billing/refund.py` | Issue 57 refund helper |
| `worker/storage.py` | R2 / boto3 adapter (Issue 38 W1: now has async wrappers) |
| `tests/test_worker_pipeline.py` | Issue 52 — all 5 ACs against real Postgres |
| `tests/test_billing_refund_integration.py` | Issue 57 |
| `tests/test_generate_clips_retry_integration.py` | Issue 46 |
| `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/MEMORY.md` | Auto-memory index |
