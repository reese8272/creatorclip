# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a source of
> truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-05-29 (Issue-75 hardening session — 10 commits)
**Branch:** `claude/busy-mendel-1r2oZ` — HEAD `9922a82`. **NOT merged to `main`.** This branch
holds *all* the merged assessment work **plus** this session's 10 commits. In a fresh container,
local `main` is just the initial commit — the real history lives on this branch.
**Working tree:** clean; everything pushed to `origin/claude/busy-mendel-1r2oZ`.
**Tests:** `431 passed, 1 skipped, 58 deselected` (default run). **Gates ALL at floor:**
ruff 0 · mypy **0** · bandit 0/0 · pip_audit **0**.
**Production:** ⚠️ **Running OLD code.** `deploy.yml` fires on push to **`main`**, and this
branch is not merged — so none of this session's 10 commits are live yet. **Merging → deploy →
verify is the beta gate (see NEXT ACTION).** This session ran in an ephemeral container, no prod access.

---

## 1. CURRENT FOCUS

The product is **functionally beta-ready**. This session cleared the bulk of the **Issue 75**
tail and every launch-config gate; what remains is *operational* (deploy/verify) + a smaller
SEV-2/cleanup tail best triaged by **re-running `/assess`** (the planned next action).

**This session shipped (10 commits, all pushed, each green: ruff 0 / mypy 0 / bandit 0,0 / pip_audit 0):**

| # | Commit | What |
|---|---|---|
| 1 | `78ee3d5` | **75(a)** pip-audit CVEs **14→0** (6 pkgs patched; 2 accepted-risk in `run_layer0.PIP_AUDIT_IGNORES`: pytest dev-cascade, starlette Host-header) |
| 2 | `7f72d10` | **75(f)** observability — `observability.py`: X-Request-ID ASGI mw + JSON logs + Prometheus `/metrics` + API→Celery propagation |
| 3 | `82d005c` | **Tier-1** legal routes `/privacy` `/terms` + Google Limited-Use disclosure + footer; CORS prod fail-fast; `scripts/verify_deploy.sh` |
| 4 | `3a72263` | **Tier-1** turnkey PgBouncer load harness (`tests/perf/run.sh` + `docker-compose.perf.yml`) to verify the BLOCKER (58) |
| 5 | `1eb6484` | **75** improvement-brief **202 + poll** (kills the 120s Cloudflare-524) — `improvement/jobs.py` Redis status, Celery task |
| 6 | `7ee5006` | **75/73** full `response_model` coverage — `routers/schemas.py` on every JSON endpoint |
| 7 | `8cddf32` | **75** `mypy_errors` **30→0** (pydantic mypy plugin + targeted fixes); baseline ratcheted to 0 |
| 8 | `9764ead` | **75/71** per-(creator,version) preference-scorer cache (`load_scorer_cached`) |
| 9 | `3ad8c23` | **75/69** clip-scorer prompt caching → **1h cache TTL** (verified via `/claude-api`) |
| 10 | `9922a82` | **75(b)** YouTube analytics retention purge — daily `purge_stale_analytics`, 30-day ToS |

### → NEXT ACTION

1. **Run `/assess`** (the reason for this fresh context). It diffs against `docs/assessment/`
   so the report is incremental — it will resurface the remaining **~37 SEV-2 + ~34 cleanup**
   tail now that the BLOCKER + all SEV-1s + the high-value SEV-2s are cleared. First set up the
   env (see §5) — Layer 0 needs the 3.12 venv + Redis; bandit/pip-audit must be on PATH.
   Update `docs/assessment/baselines.json` is already current (mypy 0, pip_audit 0).

2. **Beta-deploy gate (operational — needs the human / prod access; no more code required):**
   1. **Merge `claude/busy-mendel-1r2oZ` → `main`** → triggers `deploy.yml` (this is what makes
      the 10 commits live; nothing above is in prod yet).
   2. `./scripts/verify_deploy.sh` — checks `/health`, `/privacy`, `/terms`, `/metrics`,
      `/docs`=404, and `alembic current` == head (`a7b8c9d0e1f2`) over SSH.
   3. Confirm prod env vars: `ENV=production`, `ALLOWED_ORIGINS=https://agenticlip.studio`
      (the CORS guard fails boot otherwise), `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`,
      Deepgram/AssemblyAI, R2 creds, Google OAuth creds, Stripe (or `COMPED_EMAILS`).
   4. Add beta testers' Google emails in the OAuth consent screen (Testing mode, ≤100 — no full
      verification needed for a closed beta; that's a public-launch gate).
   5. *(Optional, load certainty)* `./tests/perf/run.sh` on a Docker host to prove the PgBouncer
      fix (58) — code-complete in `db.py`, harness ready, just unrun (no registry egress here).

3. **Delete stale remote feature branches — REQUIRES YOU (human).** This env's git proxy returns
   **403 on branch-delete pushes**. Use the GitHub UI / "Automatically delete head branches".

---

## 2. WHAT WORKS NOW (do not re-investigate)

- ✅ **All assessment SEV-0/SEV-1 fixed** (58–72, prior) + this session's SEV-2/quality tail.
  Per-item rationale in `docs/DECISIONS.md` (2026-05-29 entries), close log in `docs/PROJECT_STATE.md`.
- ✅ **Gates at floor**: ruff 0 / **mypy 0** / bandit 0,0 / **pip_audit 0**. `baselines.json`
  updated (mypy_errors 0, pip_audit_vulns 0, coverage floor 69.54). CI enforces no regression.
- ✅ **`/assess` harness** works end-to-end: Layer 0 = `run_layer0.py`; Layer 1 = per-module
  subagents → `docs/assessment/modules/`; Layer 2 = `REPORT.md`.
- ✅ **Core product ships** (59+60): clips render from `setup_start_s`; personalization loop wired.
- ✅ **Celery at-least-once safe** (61/62), **idempotent money/data** (63/64/71), **clean event
  loops** (66/67/68), **YouTube HTTP singleton + backoff** (72), **pgvector HNSW** (65),
  **bounded poll_clip_outcomes** (70).
- ✅ **This session's adds (all tested):** observability (`/metrics` + request-id), brief 202/poll
  (no more 524), `response_model` on every endpoint, the two scorer caches, 1h clip-scorer TTL,
  daily analytics-retention purge (30-day ToS), legal pages + Limited-Use + CORS prod lockdown.

---

## 3. KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| **Public URL / health** | `https://agenticlip.studio` · `/health` (SECRETS.md/ACCESS.md canonical; "autoclip" in old notes is stale) |
| **VM / SSH / deploy dir** | `147.182.136.107` (Ubuntu 24.04) · `ssh creatorclip-vm` · `/opt/autoclip/` |
| **R2 bucket / image** | `creatorclip-beta` · `ghcr.io/reese8272/creatorclip:latest` |
| **GitHub repo** | `github.com/reese8272/creatorclip` (private). Active dev branch: `claude/busy-mendel-1r2oZ` (HEAD `9922a82`, **not merged to main**) |
| **Deploy trigger** | `deploy.yml` on push to **`main`** — so the beta needs this branch merged first |
| **Test runner** | `.venv/bin/python -m pytest -q` — venv **MUST be Python 3.12** (3.12 syntax; 3.11 can't parse). Needs **Redis** running (slowapi limiter has no in-memory fallback). |
| **Lint runner** | `ruff check .` AND `ruff format --check .` (CI runs both). `requirements-dev.txt` pins `ruff==0.15.15`. |
| **Assessment gate** | `python3 .claude/skills/production-assessment/scripts/run_layer0.py` (bandit/pip-audit must be on PATH — put `.venv/bin` on PATH) |
| **Last completed** | Issue 75(b) analytics retention purge (`9922a82`) |
| **Latest alembic revision** | `a7b8c9d0e1f2` = `0007_clip_outcome_final` (no new migrations this session — 75b reuses existing tables) |
| **Test count** | 431 passed, 1 skipped, 58 deselected (default run) |
| **New config this session** | `LOG_JSON`, `REQUEST_ID_HEADER`, `METRICS_ENABLED`, `ANALYTICS_RETENTION_DAYS` (all in `.env.example`) |
| **New deps** | `prometheus-client==0.25.0`; bumped: cryptography 46.0.7, PyJWT 2.12.0, starlette 0.49.1, fastapi 0.120.4, python-multipart 0.0.27, lightgbm 4.6.0, python-dotenv 1.2.2 |

---

## 4. CONSTRAINTS & GOTCHAS

- **Fresh-container setup (do this first every session):** default `python3` is **3.11** and
  cannot parse the 3.12 codebase, and `.venv` is wiped when the container is recreated. Run:
  `python3.12 -m venv .venv && .venv/bin/pip install -U pip && .venv/bin/pip install -r
  requirements.txt -r requirements-dev.txt`, then `redis-server --daemonize yes --save "" --appendonly no`.
  `ffmpeg` may be missing (`apt-get install -y ffmpeg`). For `/assess` Layer 0, run with
  `PATH="$PWD/.venv/bin:$PATH"` so bandit/pip-audit/mypy resolve.
- **The container's Redis dies on session interruption** — if a broad sweep of tests suddenly
  fails with `Connection refused`/limiter 500s, just restart redis-server; it's not the code.
- **No Docker registry egress in this sandbox** — `docker compose` config validates but can't
  pull images, so `tests/perf/run.sh` and integration tests (real Postgres) only run on real
  infra / CI (`integration.yml`). Default `pytest -q` excludes `@pytest.mark.integration`.
- **mypy is ratcheted to 0** — any new type error fails CI. The pydantic mypy plugin is enabled
  (`pyproject.toml`). `disallow_untyped_defs` is NOT on yet (17 left, mostly Celery bound-task
  `self`) — tracked as a follow-up.
- **pip-audit ignore-list** (`run_layer0.PIP_AUDIT_IGNORES`): pytest GHSA-6w46-j5rx-g56g (dev
  cascade) + starlette PYSEC-2026-161 (needs starlette-1.x). Keep in lockstep with DECISIONS.
- **Coverage is a regression floor** (`baselines.json` 69.54), not an absolute bar — DB-only code
  is integration-tested, invisible to the unit-coverage gate.
- **Issue 60↔71 coupling:** don't weaken `from_bytes`'s lock or `load_latest`'s schema check.
- **Google OAuth still in Testing mode** — fine for ≤100 beta testers; full verification is a
  public-launch gate.
- **Two issue-numbering tracks:** still genuinely open from the old Phase-2 track — **56**
  (Postgres RLS, structural tenant isolation) and **57** (refund on terminal ingest failure —
  needs a product/policy call). Reconcile in `docs/issues.md`.

---

## 5. WHAT'S LEFT (Issue 75 tail — none beta-blocking)

| Item | Notes |
|---|---|
| **Run `/assess`** (next action) | Incremental diff of the remaining ~37 SEV-2 + ~34 cleanup in `docs/assessment/modules/*.md` |
| **starlette-1.x migration** (FastAPI→0.136.x) | Closes the last accepted-risk CVE (PYSEC-2026-161); major-line bump — mind the on_startup/on_shutdown landmine |
| **`disallow_untyped_defs`** | ~17 errors, mostly Celery bound-task `self` — needs a typed Task base/override decision |
| **Deepgram file-stream** | Needs the Deepgram SDK installed to verify the streaming API (74 deferral) |
| **OpenTelemetry tracing** | Distributed tracing on top of the Prometheus metrics (75f follow-up) |
| **Run the BLOCKER verification** | `./tests/perf/run.sh` on a Docker host — proves Issue 58 + captures p95/p99 |
| **Older Phase-2: 56 (RLS), 57 (refund policy)** | 56 = research/decide; 57 = needs a product call |

**Then Phase 3** = pre-public-launch gates (OAuth verification, ToS/Privacy legal review, billing
tiers, eval adversarial expansion) — see `CLAUDE.md` "Pre-Public-Launch Requirements".

---

## 6. POINTERS

| Doc / path | Purpose |
|---|---|
| `docs/PROJECT_STATE.md` | Per-issue close log (this session's 10 entries at top) |
| `docs/issues.md` | Issue backlog incl. **Issues 58–75** with acceptance criteria |
| `docs/DECISIONS.md` | Architecture decisions — all 2026-05-29 entries |
| `docs/assessment/REPORT.md` + `modules/*.md` | The assessment verdict + per-module findings register (re-run `/assess` to diff) |
| `docs/assessment/baselines.json` | Gate baselines (ruff 0, mypy 0, coverage 69.54, bandit 0/0, pip_audit 0) |
| `docs/COMPLIANCE.md` | YouTube ToS / data classes / retention (75b filled the cadence: 30 days) |
| `.claude/skills/production-assessment/` | The `/assess` harness (SKILL.md, rubric, scale-checklist, `run_layer0.py`) |
| `.claude/skills/best-practices/` | Process-first standards gate (Phase-1 CHECK) |
| `scripts/verify_deploy.sh` | Turnkey prod deploy/migration verification |
| `tests/perf/` | PgBouncer load harness — `run.sh` proves the BLOCKER (58) under transaction pooling |
| `observability.py`, `improvement/jobs.py`, `routers/schemas.py` | This session's new modules |
| `CLAUDE.md` | Project rules + Check→Approve→Build→Review workflow |
| `docs/SOT.md`, `docs/SECRETS.md`, `docs/ACCESS.md`, `docs/DEPLOYMENT.md` | Architecture / secrets / access / deploy |
