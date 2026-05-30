# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a source of
> truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-05-30 (reconciliation merge pushed; deploy green; RLS activation pending)
**Branch:** `main` only (6 feature branches deleted from origin; PR #15 closed).
**Working tree:** clean.
**Sync with `origin/main`:** **0 / 0** — pushed.
**Production:** ✅ **DEPLOYED.** GH Actions deploy job `26691893870` succeeded at 18:43 UTC; alembic upgrade ran
through head `0010_rls_policies`; health endpoint returned `{"status":"ok","postgres":"ok","redis":"ok"}`.
**RLS posture:** ⚠️ **migration applied but NOT enforced yet.** The app is still connecting as SUPERUSER
`creatorclip` which bypasses RLS by default. The roles `creatorclip_app` and `creatorclip_migrate` exist
(created by 0010 with LOGIN-only) but have no passwords and no BYPASSRLS attribute. Activation is a separate
one-time step — see **NEXT ACTION #1**.

---

## 0. RECONCILE NOTE (this session's first action — read before anything else)

Two timelines existed concurrently between 2026-05-28 and 2026-05-30:

- **Local `main`** shipped Issues 38 W1, 46, 52, 56, 57, 60 (RLS) into 6 unpushed commits.
- **Origin `main`** shipped Issues 58–78c, the beta launch, and the AutoClip rebrand (PRs #5, #8–#14).

Both used the issue number "60" for entirely different work (local = RLS implementation,
origin = personalization loop). Local-side numbers were re-issued to a free range before the merge:

| Local was | Now | Status |
|---|---|---|
| Issue 60 (RLS, **shipped**) | **Issue 79** | ✅ Done — see DECISIONS 2026-05-28 |
| Issue 58 (transactional email placeholder) | **Issue 80** | 🔲 Not started |
| Issue 59 (in-app notifications placeholder) | **Issue 81** | 🔲 Not started |
| Issue 61 (Issue 38 Wave 2 placeholder) | **Issue 82** | 🔲 Not started |

Alembic migration `0005_rls_policies` was renamed to `0010_rls_policies` and re-chained to
`down_revision = "0009_improvement_briefs"` so origin's 0005–0009 migrations stay linear.

Six remote feature branches (already squash-merged into origin/main as PRs #9–#14) and the duplicate
open PR #15 will be cleaned up after the push — see **NEXT ACTION #2 + #3**.

---

## 1. CURRENT FOCUS

**Issue 78 — re-implementing the net-new pieces salvaged from the closed PR #6**, plus the
local-main hardening work (Issues 79, 56, 57, 52, 46, 38 W1) just merged in:

| Item | PR / Commit | What |
|------|----|------|
| 78a | #9  | per-(creator, version) preference-scorer cache (`preference/_scorer_cache.py`) |
| 78b | #10 | clip-scorer prompt caching — 1h TTL + stable-first ordering (`clip_engine/scoring.py`) |
| 78d | #11 | improvement-brief → 202 + poll async Celery (new `ImprovementBrief` model, migration **0009**) |
| 78g | #12 | Google **Limited Use** disclosure in `static/privacy.html` (was an OAuth-verification blocker) |
| 78c | #13 + #14 | **mypy 30 → 0** (pydantic.mypy plugin + real fixes + targeted SDK-stub ignores) |
| 79  | 7e120d3 | Postgres RLS on 12 tenant-owned tables, role split, `AdminSessionLocal`, runbook (migration **0010**) |
| 56  | 877eb43 | RLS decide-and-document (decision-only; implementation = Issue 79) |
| 57  | 1855035 | Automatic refund on terminal ingest failure (3 ingest tasks) |
| 52  | 7ec3c1c | Worker pipeline integration tests (all 7 async fns, 5 ACs) |
| 46  | 1a8c635 | Generate-clips retry safety + outcomes time-window bound |
| 38 W1 | 2c53959 | Celery hot-path sync-in-async fixes (async storage wrappers, thread-offload) |

> **78c needed a hotfix (#14) — read this.** #13 over-reached by also enabling
> `disallow_untyped_defs`, which surfaces ~18 PRE-EXISTING untyped-def signatures that were never
> in the 30-error backlog; it was also merged before its Types CI job finished (that job *failed*,
> briefly red-gating `main`). #14 reverted the ratchet flags (back to commented-out in
> `[tool.mypy]`) and fixed 2 misplaced `# type: ignore`. **mypy is now a true 0 under the
> committed gradual config.** The 30→0 deliverable stands; the ratchet is deferred (see NEXT #4).

### → NEXT ACTION (in priority order)

1. **Activate RLS enforcement** via the new `.github/workflows/activate-rls.yml` workflow
   (`workflow_dispatch` only). Sequence:
   1. Add two repo Secrets at **Settings → Secrets and variables → Actions**:
      - `POSTGRES_APP_PASSWORD` — generate with `openssl rand -hex 24`
      - `POSTGRES_MIGRATE_PASSWORD` — generate with `openssl rand -hex 24`
   2. Run the **Activate RLS (Issue 79)** workflow with `dry_run=true` — prints the SQL
      and .env edits it would apply, without touching anything. Verify the plan.
   3. Re-run with `dry_run=false` to apply: sets role passwords, grants BYPASSRLS to
      `creatorclip_migrate`, transfers public-schema table ownership, rewrites
      `/opt/autoclip/.env` (DATABASE_URL → `creatorclip_app`; new DATABASE_MIGRATION_URL →
      `creatorclip_migrate`), restarts the compose services, and verifies that
      `creatorclip_app` sees 0 rows from `videos` without an `app.creator_id` GUC.
   4. Rollback if anything goes wrong: SSH to the VM, restore the timestamped
      `/opt/autoclip/.env.backup-YYYYMMDD-HHMMSS` and `docker compose up -d`.

2. ~~Delete 6 stale remote branches + close duplicate PR #15.~~ ✅ Done this session.

3. ~~Verify deploy + migration 0010.~~ ✅ Deploy job `26691893870` green; alembic at
   `0010_rls_policies`; `/health` returning ok.

4. **Remaining Issue 78 items — all BLOCKED on a human input; do not start blind:**
   - **78e — YouTube analytics-retention purge** (`docs/issues.md`). Needs (a) the **confirmed
     YouTube ToS data-staleness figure** (`docs/COMPLIANCE.md` §2 still says "TBD") and (b) your
     **sign-off to actually delete creator analytics**. Will add a Beat purge task to
     `worker/tasks.py` + touch models. Bring a Phase-1 CHECK before writing deletion code.
   - **78f — PgBouncer load-test harness** to prove the Issue-58 pool fix under load. Authorable,
     but the load-proof needs a **real staging cluster** (scaffold in `tests/perf/` / `deploy/`).
   - **Enable the `disallow_untyped_defs` ratchet** (deferred from 78c). First annotate the ~20
     pre-existing untyped-def signatures (8 in `worker/tasks.py`, 4 in `ingestion/transcribe.py`,
     + `youtube/analytics.py`, `worker/storage.py`, `models.py:542`, `dna/embeddings.py`,
     `limiter.py:15`, `main.py:38`), THEN uncomment the two flags in `[tool.mypy]`.

5. **Three local-only placeholder issues remain (renumbered):**
   - **Issue 82 (was 61, Issue 38 Wave 2)** — AsyncAnthropic / AsyncVoyage migration; router
     session-order refactor; pool starvation load test. Closes the remaining ~9 of 23 findings
     from the Issue 38 audit. (Note: substantial overlap with origin's Issue 68 — review first.)
   - **Issue 80 (was 58)** — transactional email infrastructure. First consumer: refund email
     (Issue 57 carry-over).
   - **Issue 81 (was 59)** — in-app notifications surface. First consumer: refund banner
     (Issue 57 carry-over).

6. **Re-run `/assess`** for a fresh diff of the remaining SEV-2/cleanup tail from **Issue 76**
   (it diffs against `docs/assessment/`, so each run is incremental).

### PROCESS LESSONS (carry forward)

- **Never merge a PR before its CI reports a terminal `success` on the head commit.**
- **Do NOT fan out parallel sub-agents on the same task/branch.** One worker per branch; sequential.
- **CI runs BOTH `ruff check .` AND `ruff format --check .`** — run both locally before pushing.
- **After every `Edit`, confirm the anchor matched.** Several doc edits silently no-op'd this session.
- **Parallel branches → same issue numbers → collisions.** If you fork work for more than ~12
  hours, sanity-check the next-free issue number on `origin/main` before assigning one locally.
- **The test DB accumulates leftover `creators` rows** from crashed runs; the analytics-fairness
  integration test (scans ALL creators) then false-fails. Run
  `psql -h localhost -U creatorclip -d creatorclip -c "DELETE FROM creators;"` before an
  integration run if it complains about a surprising creator count.

---

## 2. WHAT WORKS NOW (do not re-investigate)

- ✅ **Assessment Issues 58–72 + 73(p)/74/75(p) + 78a/b/c/d/g closed** (origin) AND **Issues 38 W1,
  46, 52, 56, 79 closed** (local-main). Per-issue rationale in `docs/DECISIONS.md`; close log in
  `docs/PROJECT_STATE.md`.
- ✅ **Postgres RLS on 12 tenant-owned tables** (Issue 79): `creatorclip_app` (no BYPASSRLS) +
  `creatorclip_migrate` (BYPASSRLS) role split; `after_begin` listener emits `SET LOCAL
  app.creator_id` from `session.info["creator_id"]`; worker tasks use `AdminSessionLocal`;
  one-time runbook in `docs/DEPLOYMENT.md`.
- ✅ **Auto-refund on terminal ingest failure** (Issue 57): `RefundOnFailureTask` base class on
  the 3 ingest-chain tasks; refund idempotent on `pack_id="refund:<video_id>"`.
- ✅ **Worker pipeline integration tests** (Issue 52): all 7 async functions + 5 ACs pinned.
- ✅ **Celery hot-path async correctness** (Issue 38 W1): `worker/storage.py` async wrappers
  (`aupload_file`, `adelete_file`, `adelete_prefix`, `alocal_path`); all sync calls in
  `_ingest_async` / `_transcribe_async` / `_signals_async` / `_render_clip_async` /
  `_build_dna_async` / `_purge_stale_source_media_async` are now thread-offloaded; `dna/embeddings.py`
  has `_aembed`.
- ✅ **Core product promise** (Issues 59 + 60 from origin): clips render from `setup_start_s`;
  personalization loop wired with maturity-gated blend.
- ✅ **Celery at-least-once safety** (Issues 61/62 from origin): `generate_and_rank_clips`
  idempotent (skips if clips exist — never cascade-wipes feedback); `task_reject_on_worker_lost` +
  visibility-timeout invariant; `render_clip` skips when done.
- ✅ **Idempotent money/data writes** (Issues 63/64/71 from origin): `build_dna` keyed on
  Celery task_id with advisory lock; `grant_minutes` SAVEPOINT + IntegrityError; advisory-lock
  for preference version race.
- ✅ **Event loops are clean** (Issues 66/67/68): no sync LLM/upload/transcription/Voyage calls
  on the API or worker loops; transcription has a `wait_for` job timeout.
- ✅ **YouTube HTTP** (Issue 72): one lazy per-process `youtube/_http.py` client w/ timeouts +
  5xx backoff. **pgvector HNSW index** (65). **poll_clip_outcomes bounded** (70 + `final` marker).
- ✅ **Generate-clips retry safety + outcomes time-window bound** (Issue 46, local) — note the
  poll-outcomes change was reconciled to origin's tighter 10-day + `final` bound (supersedes
  local's 30-day floor).
- ✅ **The `/assess` harness works end-to-end** (locally and on GitHub runners). Layer 0 =
  `run_layer0.py`; Layer 1 = parallel per-module subagents; Layer 2 = `REPORT.md` verdict.
- ✅ **All prior Phase-1/Phase-2 work** still intact (see `docs/PROJECT_STATE.md`).

---

## 3. THE ARC THAT LED HERE

1. **Phases 1–2** closed in earlier sessions; beta live on `autoclip.studio`.
2. **2026-05-29** — production-readiness assessment session: built the `/assess` harness +
   standards/freshness layer + CI gates; ran the full assessment → tracked findings as **Issues
   58–75**; closed the BLOCKER + all SEV-1s + security SEV-2s. Merged as PR #3.
3. **2026-05-30 (origin track)** — salvaged the net-new pieces from closed PR #6 as **Issue 78**;
   shipped 78a–d + g as PRs #9–#14 (with the #14 hotfix for the 78c over-reach). Merged.
4. **2026-05-28 → 2026-05-30 (local-main track, parallel)** — six commits hardening the
   Phase-2 carry-over: Issues 38 W1 (Celery async), 46 (retry safety), 52 (worker integration
   tests), 56 (RLS decide), 57 (auto-refund), Issue 79 (RLS implementation, was 60 locally).
5. **2026-05-30 (this session)** — **reconciled the two timelines into one `main`**: renumbered
   local Issues 60/58/59/61 → 79/80/81/82; renamed alembic 0005_rls_policies → 0010_rls_policies;
   merged origin/main into local main; resolved 7 file conflicts (docs/DECISIONS, PROJECT_STATE,
   issues, LEFT_OFF; clip_engine/ranking, db, dna/embeddings, worker/tasks).

---

## 4. KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| **Public URL / health** | `https://autoclip.studio` · `/health` |
| **VM / SSH / deploy dir** | `147.182.136.107` (Ubuntu 24.04) · `ssh creatorclip-vm` · `/opt/autoclip/` |
| **R2 bucket / image** | `creatorclip-beta` · `ghcr.io/reese8272/creatorclip:latest` |
| **GitHub repo** | `github.com/reese8272/creatorclip` (private) — `main` will be the only branch after cleanup |
| **Test runner** | `.venv/bin/python -m pytest -q` — **venv MUST be Python 3.12**. Needs a running **Redis**. |
| **Lint runner** | `ruff check .` AND `ruff format --check .` — CI runs both. `requirements-dev.txt` pins `ruff==0.15.15`. |
| **Assessment gate** | `python3 .claude/skills/production-assessment/scripts/run_layer0.py` |
| **Active issue** | _(none in flight)_ — clean up branches/PR, then pick from 78e/f/ratchet/80/81/82/Phase 3 |
| **Last completed** | Reconcile merge (this session) — Issues 79, 56, 57, 52, 46, 38 W1 + origin all on `main` |
| **Latest alembic revision** | `0010_rls_policies` (chains after `0009_improvement_briefs`) |
| **Test count (post-merge, verified)** | **439 passed, 1 skipped, 79 deselected** (`.venv/bin/python -m pytest -q`, this session). Integration tests deselected because Postgres isn't running locally; CI will run them. |
| **Safety tag for the pre-merge local main** | `safety/pre-reconcile-2026-05-30` (kept for rollback) |

---

## 5. CONSTRAINTS & GOTCHAS

- **RLS roles `creatorclip_app` / `creatorclip_migrate`** must be created with the right
  attributes before migration 0010 runs — see `docs/DEPLOYMENT.md`. The migration creates
  the roles idempotently but does NOT `ALTER ROLE ... BYPASSRLS` (that requires SUPERUSER).
- **Alembic migration chain after the merge:** `0001 → 0002 → 0003 → 0004 → 0005_dna_idempotency
  → 0006_vector_and_fk_indexes → 0007_clip_outcome_final → 0008_dna_build_job_unique →
  0009_improvement_briefs → 0010_rls_policies`. RLS lands LAST.
- **Issue 60 ↔ 71 coupling:** the preference reranker (origin Issue 60) relies on the hardening
  in 71 (lock-guarded unpickler, advisory-lock version race, schema-drift → DNA fallback).
- **Issue 70's `final` marker** + 10-day created-at cap on `_poll_clip_outcomes_async` — both
  must stay (the merge already supersedes local Issue 46's looser 30-day floor with these).
- **clip_engine/ranking.py is idempotent** (origin Issue 61): if any clip exists for the video,
  the function returns existing clips unchanged. Local Issue 46's selective DELETE block was
  dropped in the merge — origin's stricter guarantee makes it unreachable.
- **worker/tasks.py uses `db.AdminSessionLocal()` everywhere** (Issue 79) — keep this when
  writing new tasks, otherwise RLS will gate cross-tenant sweeps.
- **The `_build_dna_async` advisory lock + idempotency check** (origin Issue 76) was kept on
  top of the `AdminSessionLocal` switch — don't remove either.
- **Coverage is a regression floor, not an absolute bar.** Don't tighten without justification.
- **Deploy is gated on Docker publish, NOT on lint/CI.**
- **Two issue-numbering tracks USED to exist.** They're reconciled now; future work must use
  numbers that are free on `origin/main`. Check `docs/issues.md` before assigning.
- **TestClient cookie jar is session-scoped** (clear in teardown after OAuth callbacks).
- **SQLAlchemy 2.0 async sessions cannot cross event loops** (Issue 39's `db.recreate_engine`
  re-binds module globals on Celery `worker_process_init`).
- **Google OAuth app still in Testing mode.** Verification required before public launch.

---

## 6. WHAT'S LEFT

**Assessment tail (origin track, Issue 75/76 follow-ups):**

| Item | Why it matters |
|---|---|
| Staging **Locust run behind PgBouncer** (78f) | Verifies the BLOCKER fix under load |
| YouTube **analytics-retention cadence** (78e) | ToS compliance — needs cadence + delete sign-off |
| **`disallow_untyped_defs` ratchet** (deferred from 78c) | Annotate ~20 untyped defs first |
| ~37 SEV-2 + ~34 cleanup | In `docs/assessment/modules/*.md`; re-run `/assess` to triage |

**Local-track placeholders (renumbered, all "not started"):**

| Issue | Was | Title |
|---|---|---|
| 80 | 58 | Transactional email infrastructure (unblocks Issue 57's refund email) |
| 81 | 59 | In-app notifications surface (unblocks Issue 57's refund banner) |
| 82 | 61 | Issue 38 Wave 2 — AsyncAnthropic + router session-order + load test (overlaps origin 68; review before starting) |

**Then Phase 3** = pre-public-launch gates (OAuth verification, ToS/Privacy pages, billing
tiers, eval adversarial expansion) — see `docs/PROJECT_STATE.md` and `CLAUDE.md`.

---

## 7. POINTERS

| Doc / path | Purpose |
|---|---|
| `docs/PROJECT_STATE.md` | Per-issue close log (chronological; 2026-05-30 entries top, then 2026-05-29, then 2026-05-28) |
| `docs/issues.md` | Issue backlog — Issues 1–82 (60 = origin personalization; 79 = local RLS; 78e/f open) |
| `docs/DECISIONS.md` | Architecture decisions — 2026-05-30 → 2026-05-29 → 2026-05-28 (chronological) |
| `docs/DEPLOYMENT.md` | RLS one-time setup runbook (Issue 79) |
| `docs/assessment/REPORT.md` + `modules/*.md` | Assessment verdict + per-module findings register |
| `.claude/skills/production-assessment/` | The `/assess` harness |
| `.claude/skills/best-practices/` | Process-first standards gate (Phase-1 CHECK) |
| `.github/workflows/quality.yml` | Ratcheted CI gates (types/coverage/SAST/CVEs) |
| `tests/perf/` | Locust load-test scaffold |
| `alembic/versions/0005–0010` | Migration chain (0005 dna idempotency → ... → 0010 RLS policies) |
| `CLAUDE.md` | Project rules + Check→Approve→Build→Review workflow |
| `docs/SOT.md`, `docs/COMPLIANCE.md`, `docs/SECRETS.md`, `docs/ACCESS.md` | Architecture / compliance / secrets / access |
