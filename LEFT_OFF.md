# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a source of
> truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-05-30 (Issue 78 salvage: 78a/b/c/d/g shipped to `main`)
**Branch:** `main` only (all feature branches merged + locally deleted).
**Working tree:** clean.
**Sync with `origin/main`:** **0 / 0** — local `main` == `origin/main` @ `a52833a`.
**`main` health (verified this session):** mypy **0** (plain + `run_layer0 --gates mypy`), ruff
**0** + format clean, **431 passed / 1 skipped** unit, **66 passed** integration; all 5 CI checks
were green on every merged PR (#9–#14).
**Production:** ⚠️ **UNVERIFIED this session.** Six squash-merges to `main` (#9–#14) + one new
alembic migration (**0009**) will have triggered the CD pipeline. Verifying the deploy +
migration is **NEXT ACTION #1**. This session ran in an ephemeral container with no prod access.

---

## 1. CURRENT FOCUS

**Issue 78 — re-implementing the net-new pieces salvaged from the closed PR #6** (tracked in
`docs/issues.md` under "Issue 78"). **Five of seven items shipped to `main` this session**, each
as its own small, CI-gated, squash-merged PR:

| Item | PR | What |
|------|----|------|
| 78a | #9  | per-(creator, version) preference-scorer cache (`preference/_scorer_cache.py`) |
| 78b | #10 | clip-scorer prompt caching — 1h TTL + stable-first ordering (`clip_engine/scoring.py`) |
| 78d | #11 | improvement-brief -> 202 + poll async Celery (new `ImprovementBrief` model, migration **0009**) |
| 78g | #12 | Google **Limited Use** disclosure in `static/privacy.html` (was an OAuth-verification blocker) |
| 78c | #13 + #14 | **mypy 30 -> 0** (pydantic.mypy plugin + real fixes + targeted SDK-stub ignores) |

> **78c needed a hotfix (#14) — read this.** #13 over-reached by also enabling
> `disallow_untyped_defs`, which surfaces ~18 PRE-EXISTING untyped-def signatures that were never
> in the 30-error backlog; it was also merged before its Types CI job finished (that job *failed*,
> briefly red-gating `main`). #14 reverted the ratchet flags (back to commented-out in
> `[tool.mypy]`) and fixed 2 misplaced `# type: ignore`. **mypy is now a true 0 under the
> committed gradual config.** The 30->0 deliverable stands; the ratchet is deferred (see NEXT #3).

### -> NEXT ACTION (in priority order)

1. **Verify the prod deploy + migration 0009 applied.** The #9–#14 merges should have triggered
   `deploy.yml`. Confirm:
   ```bash
   curl -fsS https://autoclip.studio/health     # {"status":"ok","postgres":"ok","redis":"ok"}
   ssh creatorclip-vm "cd /opt/autoclip && docker compose exec app .venv/bin/alembic current"
   # expect head = 0009_improvement_briefs (creates the improvement_briefs table)
   ```
   If alembic shows `0008`, the deploy didn't run `alembic upgrade` — check
   `docker compose logs --tail 100 app worker`.

2. **Delete 6 stale remote branches — REQUIRES YOU (the human).** The agent CANNOT: this
   environment's git proxy returns **HTTP 403 on branch-delete pushes**. All merged & safe to
   remove via the GitHub branches UI (or enable Settings -> "Automatically delete head branches"):
   `claude/issue-78a-scorer-cache`, `-78b-clip-scorer-caching`, `-78c-mypy-zero`,
   `-78c-ratchet-revert`, `-78d-improvement-brief-async`, `-78g-limited-use-disclosure`.
   Local is already clean (only `main`).

3. **Remaining Issue 78 items — all BLOCKED on a human input; do not start blind:**
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

4. **Re-run `/assess`** for a fresh diff of the remaining SEV-2/cleanup tail from **Issue 76**
   (it diffs against `docs/assessment/`, so each run is incremental).

### PROCESS LESSONS FROM THIS SESSION (internalize before the next one)

- **Never merge a PR before its CI reports a terminal `success` on the head commit.** #13 was
  merged mid-run and its Types job failed -> `main` went red. Wait for the green webhook.
- **Do NOT fan out parallel sub-agents on the same task/branch.** Two agents on 78d corrupted the
  working tree and pushed a broken partial commit. One worker per branch; work sequentially.
- **CI runs BOTH `ruff check .` AND `ruff format --check .`** — run both locally before pushing
  (a format-only miss red-gated an early 78d push).
- **After every `Edit`, confirm the anchor matched.** Several doc edits silently no-op'd on stale
  anchors this session and had to be re-applied.
- **The test DB accumulates leftover `creators` rows** from crashed runs; the analytics-fairness
  integration test (scans ALL creators) then false-fails. Run
  `psql -h localhost -U creatorclip -d creatorclip -c "DELETE FROM creators;"` before an
  integration run if it complains about a surprising creator count.

---

## 1b. SUPERSEDED (older context — kept for the trail, no longer the focus)

The block below was the 2026-05-29 production-assessment session (Issues 58–75). All merged and
on `main`; retained only as history. Skip to §2 for what currently works.

---

## 2. WHAT WORKS NOW (do not re-investigate)

- ✅ **Assessment Issues 58–72 closed + 73(partial)/74/75(partial)**. Per-issue rationale in
  `docs/DECISIONS.md` (2026-05-29 entries) and close log in `docs/PROJECT_STATE.md`.
- ✅ **Test suite green**: `401 passed, 1 skipped, 55 deselected`. All gates green on real CI
  in PR #3 (ruff, unit tests, docker build, **quality.yml** types/SAST/deps + coverage floor).
- ✅ **The `/assess` harness works end-to-end** (locally and on GitHub runners). Layer 0 =
  `run_layer0.py` deterministic gates; Layer 1 = parallel per-module subagents writing to
  `docs/assessment/modules/`; Layer 2 = `REPORT.md` verdict. Baselines in
  `docs/assessment/baselines.json` (ruff 0, mypy 30, coverage 69.54 floor, bandit 0/0,
  pip-audit 14) — ratchet down over time per `docs/assessment/README.md`.
- ✅ **Core product promise now actually ships** (Issues 59 + 60): clips render from
  `setup_start_s`; the personalization loop is wired (retrain task on feedback + reranker
  called in `generate_and_rank_clips` + maturity-gated blend).
- ✅ **Celery at-least-once safety** (Issues 61/62): `generate_and_rank_clips` is idempotent
  (skips if clips exist — never cascade-wipes feedback); `task_reject_on_worker_lost` +
  `soft(3000)<hard(3300)<visibility(3600)` invariant; `render_clip` skips when done.
- ✅ **Idempotent money/data writes** (63/64): `build_dna` keyed on Celery task_id; `grant_minutes`
  SAVEPOINT + IntegrityError; advisory-lock for preference version race (71).
- ✅ **Event loops are clean** (66/67/68): no sync LLM/upload/transcription/Voyage calls on the
  API or worker loops — all `asyncio.to_thread`; transcription has a `wait_for` job timeout.
- ✅ **YouTube HTTP** (72): one lazy per-process `youtube/_http.py` client w/ timeouts + 5xx
  backoff. **pgvector HNSW index** (65). **poll_clip_outcomes bounded** (70, `final` marker).
- ✅ **All prior Phase-1/Phase-2 work** still intact (see `docs/PROJECT_STATE.md`).

---

## 3. THE ARC THAT LED HERE

1. **Phases 1–2** closed in earlier sessions; beta live on `autoclip.studio`. (See the prior
   LEFT_OFF history in git if needed — that work is in `docs/PROJECT_STATE.md`.)
2. **2026-05-29 (this session)** — a standalone **production-readiness assessment**:
   - Built the `/assess` harness + standards/freshness layer + CI gates.
   - Ran the full assessment → verdict **PRODUCTION-READY: NO** (1 BLOCKER, 25 SEV-1, ...).
   - Tracked findings as **Issues 58–75**; fixed the BLOCKER + all SEV-1s + security SEV-2s,
     one issue/batch at a time (CHECK → BUILD → REVIEW, each committed green).
   - **Three assessment claims were corrected against the actual code** (documented in
     DECISIONS): the ffmpeg "GOP drift" (false positive — re-encode accurate-seeks by default);
     "missing" pgvector/FK indexes (two already existed); the prompt-caching "cost win"
     (the brief prompts are below Sonnet 4.6's 2048-token cache floor, so the split is
     correct-structure only). **Trust the code over the register.**
   - Opened PR #3, CI green, **merged to `main`**.

---

## 4. KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| **Public URL / health** | `https://autoclip.studio` · `/health` |
| **VM / SSH / deploy dir** | `147.182.136.107` (Ubuntu 24.04) · `ssh creatorclip-vm` · `/opt/autoclip/` |
| **R2 bucket / image** | `creatorclip-beta` · `ghcr.io/reese8272/creatorclip:latest` |
| **GitHub repo** | `github.com/reese8272/creatorclip` (private) — `main` is the only branch (after you delete the remote feature branch) |
| **Test runner** | `.venv/bin/python -m pytest -q` — **venv MUST be Python 3.12** (the codebase uses 3.12 syntax; a 3.11 interpreter can't even parse it). Needs a running **Redis** (slowapi limiter has no in-memory fallback). |
| **Lint runner** | `ruff check .` AND `ruff format --check .` — CI runs both. **CI ruff is 0.15.x**; `requirements-dev.txt` pins `ruff==0.15.15` to match (an older pin disagrees on formatting). |
| **Assessment gate** | `python3 .claude/skills/production-assessment/scripts/run_layer0.py` (add `--update-baseline` to recapture, `--require-fresh` for the freshness gate) |
| **Active issue** | _(none in flight)_ — remaining work is the **Issue 75** tracking list |
| **Last completed** | Issue 75(a) CVEs (14→0) + 75(f) observability (this session) |
| **Latest alembic revision** | `a7b8c9d0e1f2` — `0007_clip_outcome_final` (this session added 0005, 0006, 0007) |
| **Test count** | 410 passed, 1 skipped, 55 deselected |

---

## 5. CONSTRAINTS & GOTCHAS

- **Fresh-container setup (this assessment env):** the default `python3` was **3.11** and could
  not parse the 3.12 codebase. Do: `python3.12 -m venv .venv && . .venv/bin/activate &&
  pip install -U pip setuptools wheel && pip install -r requirements.txt -r requirements-dev.txt`,
  then `redis-server --daemonize yes --save "" --appendonly no`. `ffmpeg` may also be missing
  (`apt-get install -y ffmpeg`).
- **Cannot delete remote branches from the agent env** — git proxy returns **403 on
  delete-refspec pushes** (create/update is fine). Branch cleanup is a human action via the
  GitHub UI. (This is why the merged feature branch may still be on the remote.)
- **Coverage is a regression floor, not an absolute bar** (`baselines.json` = 69.54). DB-only
  code is integration-tested (not seen by the unit-coverage gate), so the floor moved down
  once (justified in DECISIONS) — that's expected, not a smell.
- **Deploy is gated on Docker publish, NOT on lint/CI** (existing) — a green CI is not a deploy
  gate. Pushing docs to `main` (like this LEFT_OFF update) still triggers a redeploy of
  identical code; harmless.
- **Issue 60 ↔ 71 coupling:** the preference reranker (60) relies on the hardening in 71
  (lock-guarded unpickler, advisory-lock version race, schema-drift → DNA fallback). Don't
  weaken `from_bytes`'s lock or `load_latest`'s schema check.
- **Issue 70's `final` marker** is set only on the 7d-checkpoint poll; the query excludes
  `final` + caps to clips created <10 days. Don't remove either or the quota drain returns.
- **Integration tests** (`@pytest.mark.integration`, 55 deselected) need a real Postgres with
  migrations applied; default `pytest -q` excludes them (`pytest.ini`).
- **Existing constraints still apply:** TestClient cookie jar is session-scoped (clear in
  teardown); SQLAlchemy async sessions can't cross event loops; Google OAuth app still in
  Testing mode (verification needed before public launch).
- **Two issue-numbering tracks exist.** The older Phase-2 remainder (38/46/52/56/57) overlaps
  the assessment: old **38** ≈ new 66/67/68 (done), old **46** ≈ new 61+70 (done). Genuinely
  still open: **56** (Postgres RLS — the assessment's structural tenant-isolation
  defense-in-depth) and **57** (refund on terminal ingest failure — needs a policy call).
  Reconcile these in `docs/issues.md` next session.

---

## 6. WHAT'S LEFT

**Assessment tail — Issue 75 (`docs/issues.md`), in rough priority:**

| Item | Why it matters |
|---|---|
| Staging **Locust run behind PgBouncer** | The only way to *verify* the BLOCKER fix (58) and axes A/B/E under load |
| ~~14 pip-audit CVEs~~ ✅ done | Patched; `pip_audit_vulns`→0. Residual: **starlette-1.x migration** to close PYSEC-2026-161 |
| YouTube **analytics-retention cadence** | ToS exposure (compliance) — needs the cadence figure, then a scheduled purge |
| Full **`response_model`** coverage | API hygiene across ~16 endpoints |
| ~~observability~~ ✅ done (75f); **mypy→0**, **Deepgram stream**, **clip-scorer caching**, **scorer LRU cache**, **brief 202/poll**, **OTel tracing** | Each enumerated under Issue 75 |
| ~37 SEV-2 + ~34 cleanup | In `docs/assessment/modules/*.md`; re-run `/assess` to triage as a diff |

**Older Phase-2 remainder to reconcile:** **56** (RLS, research/decide) and **57** (refund
policy — needs Phase-1 product call). 38/46/52 are effectively covered by the assessment work.

**Then Phase 3** = pre-public-launch gates (OAuth verification, ToS/Privacy pages, billing
tiers, eval adversarial expansion) — see `docs/PROJECT_STATE.md` and `CLAUDE.md`
"Pre-Public-Launch Requirements".

---

## 7. POINTERS

| Doc / path | Purpose |
|---|---|
| `docs/PROJECT_STATE.md` | Per-issue close log (this session's 58–75 entries at top) |
| `docs/issues.md` | Issue backlog incl. **Issues 58–75** with acceptance criteria |
| `docs/DECISIONS.md` | Architecture decisions — 2026-05-29 entries + the 3 assessment corrections |
| `docs/assessment/REPORT.md` + `modules/*.md` | The assessment verdict + per-module findings register |
| `docs/SKILL_FRESHNESS.md` | The evergreen-vs-perishable convention + `last_verified` ritual |
| `.claude/skills/production-assessment/` | The `/assess` harness (SKILL.md, rubric, scale-checklist, run_layer0.py) |
| `.claude/skills/best-practices/` | Process-first standards gate (Phase-1 CHECK) |
| `.github/workflows/quality.yml` | Ratcheted CI gates (types/coverage/SAST/CVEs) |
| `.github/workflows/freshness.yml` | Quarterly skill-staleness check |
| `tests/perf/` | Locust load-test scaffold (for the BLOCKER verification) |
| `alembic/versions/0005..0007` | This session's migrations (dna idempotency; vector/FK indexes; clip_outcome.final) |
| `CLAUDE.md` | Project rules + Check→Approve→Build→Review workflow |
| `docs/SOT.md`, `docs/COMPLIANCE.md`, `docs/SECRETS.md`, `docs/ACCESS.md`, `docs/DEPLOYMENT.md` | Architecture / compliance / secrets / access / deploy |
