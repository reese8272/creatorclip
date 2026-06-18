# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a
> source of truth — those live in `docs/` (start at [`docs/README.md`](docs/README.md)).
> Updated at the end of every session.

**Last updated:** 2026-06-17 (React adoption + beta logging + insight sort + **Issue 152 Pro chatbot**)
**Branch:** `main` — HEAD `75845cb`
**Working tree:** ⚠️ **DIRTY — this session's work (incl. Issue 152) is entirely uncommitted.** Nothing committed or pushed yet. Issue 152 added: `chat/`, `routers/chat.py`, `tests/test_chat*.py`, `alembic/versions/0026_chat.py`, `frontend/src/pages/Chat.tsx`, plus edits to `models.py`/`config.py`/`main.py`/`worker/tasks.py`/`worker/anthropic_stream.py`/`.env.example`/`frontend` (App/Nav/taskStream/vite) + docs.
**Prod:** live & healthy — `https://autoclip.studio/health` → `{"status":"ok",...}`. **Prod does NOT yet have any of this session's work.**
**CI:** green at HEAD `75845cb` (this session's changes have not hit CI).

---

## CURRENT FOCUS

**This session built four things — all verified locally, none committed yet:** (1) adopted
**React + TypeScript** for the frontend and ported the **profile page** as the pilot, (2) wired the
**Docker/CI build** for the SPA, (3) shipped **Issue 149** (insight sort) and **Issue 151** (beta
logging to a DB), and (4) **built Issue 152 — the Pro chatbot** (streaming assistant scoped to the
creator's own channel; full issue-workflow CHECK→APPROVE→BUILD→REVIEW; `docs/DECISIONS.md`
2026-06-17). The immediate job is now to **land all of it**.

**→ NEXT ACTION:**

1. **Commit + ship the session work** (it's on `main` uncommitted — do NOT commit to `main` directly;
   the model is `feature → staging → main`, and a push to `main` auto-deploys):
   ```bash
   git checkout -b feature/react-adoption-logging
   git add frontend/ .dockerignore alembic/versions/0025_event_logs.py event_log.py routers/logs.py \
           tests/test_event_log.py tests/test_event_log_integration.py tests/test_spa_serving.py
   git add .env.example .github/workflows/ci.yml .gitignore Dockerfile config.py main.py models.py \
           routers/activity.py static/insights.html tests/test_static.py docs/
   git status   # confirm frontend/node_modules + frontend/dist are NOT staged (both gitignored)
   git commit   # message below
   git push -u origin feature/react-adoption-logging
   gh pr create --base staging   # PR into staging, NOT main
   ```
   - Watch the new **`Frontend (lint, test, build)`** CI job + the **`Docker build`** smoke job — both
     are new this session and have never run in CI. The integration job will apply migration **0025**
     and run the CI-authoritative `event_logs` tests (`tests/test_event_log_integration.py`).
   - Consider splitting into two PRs if you want cleaner history (frontend/React+Docker vs.
     logging+sort) — they're independent. One branch is fine too; they're all this session.
2. **Issue 152 (Pro chatbot) is BUILT** ✅ — verify it lands cleanly in CI. Watch for: **migration
   0026** (`chat_conversations` + `chat_messages`, RLS on the conversation table) applying after 0025
   on the integration job, and the **CI-authoritative isolation test**
   `tests/test_chat_isolation_integration.py` (per-creator tool scoping — needs real Postgres). The
   gate is **active-creator + 25/day quota, no per-message minute deduction in v1** (DECISIONS
   2026-06-17). Add `CHAT_*` env vars (see `.env.example`) to the prod `.env` on deploy. Next product
   step (separate issue): revisit per-message credit metering after ~30 days of real token logs.
3. **Visual-QA the React profile page** (deferred — needs the running backend + a seeded DNA). To see
   it locally: start FastAPI on `:8000`, `npm --prefix frontend run dev`, open
   `http://localhost:5173/app/profile` (dev server proxies the API).

---

## WHAT WORKS NOW (built + verified this session — don't re-investigate)

- **React SPA stack stood up** in `frontend/`: Vite 8 + React 19 + TypeScript + Tailwind v4 +
  hand-written shadcn-style primitives. Served by FastAPI under **`/app/*`** (hashed assets via a
  StaticFiles mount; `/app/{path}` falls back to the SPA shell for client routing; legacy `static/`
  pages untouched). The Issue-99 dark design tokens are mapped into the Tailwind `@theme`.
- **Profile page ported** (`frontend/src/pages/Profile.tsx` + `components/profile/*`): DNA brief now
  renders as **real structured HTML** via a `.textContent`-safe parser (fixes the "wall of asterisks"),
  the internal `v3 · active` badge is replaced by a plain **provenance badge**, plus identity / intake
  mode / API-keys sections. Verified: `npm run build` clean, `eslint` 0, `vitest` 6/6.
- **Docker/CI wired for the SPA:** Dockerfile gained a `node:22` `frontend-build` stage → copies
  `dist` into the runtime image at `/app/frontend/dist`; new `.dockerignore` (was none — `COPY . .`
  had been baking `.venv`/`node_modules`/`.env` into the image); new `frontend` CI job. `npm ci` clean.
  **Not run as a real `docker build` (no Docker in this WSL env)** — CI's docker-build job is authoritative.
- **Issue 149 (insight sort) DONE:** Sort dropdown on the Top/Underperformers panels (default score
  high→low; +low→high, +Title A–Z). Fixed an inline **stored-XSS** (performer title/kind were
  unescaped in `innerHTML`) → now `escapeHtml`-wrapped, pinned in `test_static.py`.
- **Issue 151 (beta logging to DB) DONE:** new `event_logs` table (migration **0025**) + `event_log.py`
  sink (isolated engine on `LOGS_DATABASE_URL`, **boundary PII/token redaction**, best-effort writes).
  `/api/activity` now persists UI events (+ keeps `app.log`); a new `http_request` middleware logs
  every backend request; `GET /api/logs/me` returns a creator's own rows (app-level isolation).
- **Verification:** `ruff` + `mypy` clean on all touched Python; **full unit suite 986 passed** (also
  fixed a latent bug — the `/app` HTML routes were failing `test_response_models`, now
  `include_in_schema=False`). Redaction unit-tested; logging integration tests are **CI-only**.

## THE ARC THAT LED HERE

1. User asked 7 things about the app (barren UI, ugly profile, insight sort, profile nav, a Pro
   chatbot, log-everything-to-a-DB, and "what did the ToS say"). The ToS answer: downloading YouTube
   bytes via yt-dlp is barred **even for own content** (`COMPLIANCE.md` §5 / Issue 139).
2. Diagnosed the barren/ugly look as raw-markdown rendering + sparse layout. Researched **React vs
   vanilla** → adopted **React + TypeScript** (DECISIONS 2026-06-17); profile = pilot page.
3. Wired the SPA into FastAPI + Docker/CI.
4. Built **#3 insight sort** and the **#6 logging-to-DB** system; filed **Issue 150** (continuous OBS
   capture as the ToS-clean clip source) and **Issue 152** (Pro chatbot, next).

## KEY COORDINATES & FACTS

| Thing | Value |
|-------|-------|
| New frontend | `frontend/` — Vite+React+TS+Tailwind v4. Build: `npm --prefix frontend run build` → `frontend/dist/` (gitignored). Dev: `npm --prefix frontend run dev` (proxies API to `:8000`). |
| SPA serving | FastAPI serves `dist/` under **`/app/*`** (see `main.py`, guarded — no-op if `dist` absent). Profile lives at `/app/profile`. |
| New backend files | `event_log.py` (sink), `routers/logs.py` (`/api/logs/me`), `alembic/versions/0025_event_logs.py` |
| New config | `LOGS_DATABASE_URL` (defaults to `DATABASE_URL`), `EVENT_LOG_DB_ENABLED` (default true) — in `.env.example` |
| Migration to apply | **0025** (`event_logs`) — applied by CI integration + on deploy; NOT applied locally |
| Prod domain / VM | `https://autoclip.studio` · DO droplet `147.182.136.107`, dir `/opt/autoclip` |
| Docker image | `ghcr.io/reese8272/creatorclip:latest` |
| Deploy pipeline | push to `main` → `docker-publish.yml` (self-hosted runner) → `deploy.yml` (auto-deploys prod) |
| Branches | `main` (live) + `staging` (pre-prod) — both at `75845cb` |
| Open issues from this session | **150** (OBS live capture, planned), **151** (logging, done — needs commit), **152** (Pro chatbot, **NEXT**) |
| Repo plan | private, free tier (no branch protection without Pro) |
| Docs index | `docs/README.md` |

## CONSTRAINTS & GOTCHAS

- **Working tree is dirty on `main`.** First move is a feature branch — do not commit to `main`.
- **Pushing/merging to `main` triggers a production deploy** (docker-publish → deploy). Go via
  `feature → staging → main`. `staging → main`: fast-forward / `--merge`, **never `--rebase`**.
- **Migration 0025 must run on deploy.** The deploy applies `alembic upgrade head`; if the SPA serves
  but `/api/logs/me` 500s or activity writes silently fail, check the migration ran.
- **The SPA won't serve in prod until the image is rebuilt** with the new `frontend-build` Dockerfile
  stage (this session added it; the current prod image predates it). `/app/*` is a guarded no-op
  without `frontend/dist`.
- **No Docker in this WSL env** and **no local Postgres** (no sudo). The `docker build`, Alembic
  migrations, and all `*_integration.py` tests are **CI-authoritative**. Redis *is* up locally; the
  unit suite runs: `.venv/bin/python -m pytest -m "not integration" -q` (986 pass).
- **Frontend `node_modules` (181M) + `dist` are gitignored** — confirm they're not staged before commit.
- **CI ruff is pinned to `0.15.15`** — match it locally (`.venv` has it) or `ruff format` diverges.
- **`event_logs` has no RLS by design** (telemetry; mirrors `audit_log`). Per-creator read isolation is
  enforced in `/api/logs/me` at the app layer, NOT by the database. Redaction (`event_log._redact`) is
  the load-bearing PII/token guard — keep it.
- **Cloudflare Bot Fight Mode 403s GitHub-hosted IPs** (external GH probes of prod fail even when
  healthy). **`StarletteDeprecationWarning`** on every test run is harmless noise.

## OPEN FOLLOW-UPS (pre-existing, still optional / external)

1. **Enable Cloudflare Health Checks** — `docs/DEPLOYMENT.md` → "Production health monitoring". *User.*
2. **GitHub Pro → apply branch protection** — ready ruleset in `docs/BRANCHING.md`. *User/account.*
3. **Issue 148 deep CSS dedup** — intentionally deferred (no visible benefit, JS-coupled).

## POINTERS

- **Docs index:** [`docs/README.md`](docs/README.md)
- **State:** [`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md) · **Decisions:** [`docs/DECISIONS.md`](docs/DECISIONS.md) (React adoption + logging entries, 2026-06-17)
- **Issues / backlog:** [`docs/issues.md`](docs/issues.md) — **149** (done), **150** (OBS), **151** (logging, done), **152** (chatbot, next)
- **Compliance:** [`docs/COMPLIANCE.md`](docs/COMPLIANCE.md) (ToS §5; event-log data class) · **Branching:** [`docs/BRANCHING.md`](docs/BRANCHING.md) · **Deploy:** [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)
- **Off-course bugs:** [`docs/OFF_COURSE_BUGS.md`](docs/OFF_COURSE_BUGS.md)
- **Memory:** `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/`
