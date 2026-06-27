# LEFT_OFF — backend observability (Issue 326) built; live app is the VM, not Render

> **Read this first.** Living "where we are right now" handoff for a fresh session with zero memory.
> Source-of-truth docs live in `docs/`; this file orients and points to them — it is NOT a source of truth.

**Last updated:** 2026-06-27
**Checked out:** `main` @ `d83da76` — **pushed to `origin/main`**; `staging` fast-forwarded to match (both in line). Issue 326 (code + VM activation wiring) is committed AND pushed.
**Working tree:** clean. Screenshot churn removed this session; `.gitignore` now has a working `Screenshot *.png` glob so it won't recur.
**CI:** all green. **Last prod deploy:** pushing `d83da76` triggers a VM deploy (Docker publish → "Deploy to production"). The OTel code remains a **strict no-op** until the SaaS secrets are set, so this deploy changes prod behavior **not at all** yet.

> ⚠️ **Pushing `main` auto-deploys to the VM.** Already done for `d83da76` — safe because OTel/Sentry stay dormant until `OTEL_EXPORTER_OTLP_ENDPOINT` / `SENTRY_DSN` GitHub secrets exist (still unset). The **next** push that matters is after those secrets are set — that one actually arms the exporters.

---

## CURRENT FOCUS

**Give the live backend full observability ("see every detail") — Issue 326.** Research + design + code + **VM activation wiring are all done, committed, and pushed.** All that remains is a **user/external** step: create the SaaS accounts, set 3 GitHub secrets, then verify it lights up live.

### ✅ DONE this session (code-side complete)
- **Issue 326 code** (`e837979`) — `init_otel()`/instrumentation in `observability.py`, wired into `main.py` + `worker/celery_app.py`, no-op until env set; 42/42 obs tests green.
- **VM activation wiring** (`d83da76`) — `.github/workflows/deploy.yml` guarded secret-sync now propagates `SENTRY_DSN`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS` to the VM `.env` (skipped while unset → no drift, no premature activation), and stamps `IMAGE_SHA` for release attribution. No `docker-compose.prod.yml` change (all services `env_file: .env`). 4 non-secret OTEL_* tunables keep `config.py` defaults. Logged in `docs/DECISIONS.md` (2026-06-27).
- **`scripts/clip_pipeline_state.py`** committed (read-only DB diagnostic, sibling to `scripts/r2_inspect.py`).
- **Pushed `main` → `origin/main`; `staging` fast-forwarded to match.**

### → NEXT ACTION (pick up here — all EXTERNAL/USER)
1. **Create the SaaS accounts + set creds** (see `docs/RENDER_DEPLOY.md` §11 — applies conceptually to the VM): Sentry project → `SENTRY_DSN`; Grafana Cloud stack → OTLP endpoint + Basic-auth token → `OTEL_EXPORTER_OTLP_ENDPOINT` + `OTEL_EXPORTER_OTLP_HEADERS`. Set all 3 as **GitHub Actions secrets** (the deploy secret-sync picks them up automatically).
2. **Trigger a deploy** (push any commit to `main`, or re-run the "Deploy to production" workflow) so the secrets sync into the VM `.env` and the exporters arm.
3. **Verify live:** reproduce one upload→clip flow, confirm an HTTP→Celery trace appears in Grafana Cloud Tempo and exceptions in Sentry. Then check off Issue 326's external ACs (`docs/issues.md`) + flip its `docs/PROJECT_STATE.md` row to ✅ Done.

### Lower-priority follow-ups (raised this session, not yet done)
- **Render DB cleanup:** remove the temporary **IP allowlist entry** added to Render `creatorclip-db` during debugging (that DB is empty/unused).
- **Re-run a failed-transcription video** to confirm the Deepgram fix holds (3 of 4 test uploads failed transcription *before* the fix; the latest succeeded — strongly indicates resolved).
- **~20 YouTube-linked videos stuck at `ingest_status=pending`** — confirm that's "linked but never queued" (expected per Issue 317) and not a stuck queue.

---

## WHAT WORKS NOW (don't re-investigate)

- **Upload→clips pipeline is live-verified end-to-end on the VM.** Latest upload (`01fa0601…`) ran ingest→transcribe→signals→`generate_clips` and produced **6 clip candidates**. The earlier transcribe failures were the now-fixed Deepgram bugs (`8db5c3c`, `705cb56`).
- **"Why are there no `clips/` objects in R2?" — ANSWERED, not a bug.** `render_clip` is **user-triggered** (enqueued from `routers/clips.py:419`, the set-style+render endpoint), never auto-chained after `generate_clips`. Clips rest at `render_status=pending` until a user clicks render. R2 `clips/` is empty simply because no one has rendered yet. To produce a rendered 9:16 clip in R2, hit render on one of the 6 candidates in the app.
- **R2 (`autoclip-studio`) is healthy:** 8 objects (4 `source/`, 4 `audio/`), ~172 MB. Inspect with `python3.12 scripts/r2_inspect.py` (needs `R2_*` in local `.env`).
- **Issue 326 code (committed `e837979`):** `init_otel()`/`instrument_fastapi_app()` in `observability.py` (lazy imports, strict no-op when endpoint unset, idempotent), wired into `main.py` + `worker/celery_app.py`; auto-instruments Celery/SQLAlchemy/Redis/httpx/botocore + Anthropic (OpenLLMetry, content capture forced OFF for PII). 42/42 observability tests pass; no-op import verified. Deps pinned in `requirements.txt`.
- **LLM lane (#318–325) shipped + deployed.** Prod is self-sufficient for AI (keys synced from GH secrets on deploy).

---

## THE ARC THAT LED HERE (how this session unfolded)

1. **User asked to "check the R2 and other storage logs to understand the app as it's working."** Inspected the `autoclip-studio` R2 bucket (wrote `scripts/r2_inspect.py`): found only `source/` + `audio/` objects, **no `clips/`** — surfacing the question "why are no clips being produced?"
2. **User escalated:** "check worker logs to see why clips aren't rendering, and make sure you have **complete visibility** on the backend — logging, tracing, third-party API capture, etc."
3. **Audited existing observability:** strong foundation (structured JSON logs + request-id correlation HTTP→Celery, prometheus-client golden signals, Sentry SDK wired). Gaps: `SENTRY_DSN` unset (no error capture), Render logs ephemeral/unsearchable, nothing scrapes `/metrics`, **no distributed tracing at all**, third-party calls only partially metered.
4. **User: "do this the most robust, production-standard way — research it, issue-workflow it."** Ran `best-practices` (Phase-1 CHECK gate) + an industry-standards research pass (2026-current, sourced).
5. **Decided (user-approved):** keep the foundation, layer **OpenTelemetry → managed Grafana Cloud** (unified logs+metrics+traces) + **Sentry SaaS** (errors). Filed **Issue 326** in `docs/issues.md` (L08 Observability lane), recorded the decision in `docs/DECISIONS.md` (2026-06-26 — reverses the 2026-05-29 beta-OTel deferral; Grafana Cloud managed chosen over self-hosted Loki-on-GKE #240).
6. **Built it** (python-senior-engineer agent + my review): committed `e837979`. Included an off-course fix — `response_class=Response` on three 204 routes (`routers/{activity,auth,chat}.py`) that tripped a FastAPI assertion blocking the local test suite (logged in `docs/OFF_COURSE_BUGS.md`).
7. **Went to answer the original "why no clips" against the prod DB.** User provided the Render `creatorclip-db` external URL → **it was empty (0 tables, PG18, no pgvector, no `alembic_version`)**. This exposed the big finding: **the live app does NOT run on Render.**
8. **Traced the real topology (`docs/DEPLOYMENT.md` + `docker-compose.prod.yml`):** autoclip.studio runs on a **single VM via docker-compose behind a Cloudflare tunnel**, with its own `postgres` container holding the real data. Render is a **half-finished future beta host** that was never cut over (no `creatorclip-web` deploy ever migrated the DB).
9. **User granted SSH to the VM** (`creatorclip-vm`) → queried the live DB directly: confirmed the pipeline works (6 clips generated, `render_status=pending`), confirmed render is user-triggered, and that the 3 transcription failures were the earlier fixed bugs. **Mystery resolved.**
10. **Now:** Issue 326's code targets `render.yaml` (not the live host), so the remaining work is extending it to the VM + creating the SaaS accounts — see NEXT ACTION.

---

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Live prod host | **VM** — SSH alias `creatorclip-vm`, dir `/opt/autoclip`, `docker-compose.prod.yml`, served via Cloudflare tunnel (`cloudflared` svc) |
| Live DB | VM `postgres` container — `pgvector/pgvector:pg16`, db `creatorclip`, user `creatorclip` (inspect: `ssh creatorclip-vm 'cd /opt/autoclip && docker compose -f docker-compose.prod.yml exec -T postgres psql -U creatorclip -d creatorclip'`) |
| Live logs | `ssh creatorclip-vm 'cd /opt/autoclip && docker compose -f docker-compose.prod.yml logs -f app worker'` (ephemeral — the reason Issue 326 exists) |
| Image | `ghcr.io/reese8272/creatorclip:latest` |
| R2 bucket | `autoclip-studio` (creds by name: `R2_ACCOUNT_ID` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` / `R2_BUCKET`) |
| Render (NOT live) | blueprint `render.yaml`; `creatorclip-db` = empty PG18 instance `dpg-d8vkcuu8bjmc738cvm6g-a` — unmigrated, unused |
| Branch / HEAD | `main` @ `e837979`, 1 ahead of `origin/main` (unpushed) |
| Test creator (this session) | `eb9af967-5d2f-4063-a05e-9f4f070ce840` |
| Active issue | **#326** (`docs/issues.md`, L08 Observability) — code-complete, Verify: external |
| Secrets (names only) | `SENTRY_DSN`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS`, `R2_*`, `DATABASE_URL`, AI keys — live in the VM `.env` + GH Actions secrets; **never in this repo** |

---

## CONSTRAINTS & GOTCHAS

- **Render ≠ live.** Do not query/trust the Render `creatorclip-db` for prod data — it's empty. The live data is on the VM. (Memory: `project_live_deployment_topology`.)
- **Pushing `main` deploys to the VM.** Verify intent before `git push`. `e837979` is no-op-safe, but any future OTel env wiring will activate exporters on deploy.
- **OTel/Sentry are no-ops without env.** `init_otel` returns immediately and imports nothing when `OTEL_EXPORTER_OTLP_ENDPOINT` is empty; Sentry no-ops on empty `SENTRY_DSN`. Dev/CI stay offline-clean by design.
- **PII boundary:** LLM span content is forced OFF (`TRACELOOP_TRACE_CONTENT=false` in `init_otel`). Don't re-enable.
- **`render_clip` is user-triggered** — a `pending` clip is normal, not a failure. Don't "fix" it.
- **Secrets are write-only on GH / live only in the VM `.env`** — reference by name; never echo values.
- **Local box has no Docker**; tests run via `python3.12` / `.venv` (see memory `local_dev_test_env`).
- **`httpx2` vs `httpx`:** the app's TestClient uses `httpx2`; outbound clients (YouTube/Deepgram/Anthropic) use stock `httpx`, so `HTTPXClientInstrumentor` binds them correctly.

---

## POINTERS

- `docs/issues.md` — Issue **#326** brief (L08 Observability lane) + full roadmap
- `docs/DECISIONS.md` — 2026-06-26 entry: observability stack decision + sources
- `docs/RENDER_DEPLOY.md` §11 — observability setup steps (Sentry + Grafana Cloud)
- `docs/DEPLOYMENT.md` — VM/single-host deploy mechanics; `docs/SOT.md`, `docs/PROJECT_STATE.md` — architecture + progress
- `docs/OFF_COURSE_BUGS.md` — the 204-route fix logged this session
- `CLAUDE.md` — project rules (Read Order, issue workflow, standards)
- Memory: `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` — esp. `project_live_deployment_topology.md` (this session's key finding)
- Session helpers: `scripts/r2_inspect.py` (committed), `scripts/clip_pipeline_state.py` (untracked)
