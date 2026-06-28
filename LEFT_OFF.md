# LEFT_OFF — observability + DR + the "moat" eval batch shipped; all that remains is the staging/external verification pass

> **Read this first.** Living "where we are right now" handoff for a fresh session with zero memory.
> Source-of-truth docs live in `docs/`; this file orients and points to them — it is NOT a source of truth.

**Last updated:** 2026-06-28
**Checked out:** `main` @ `47a1f44` — **pushed to `origin/main`** (in sync, 0/0); `staging` fast-forwarded to match. Everything below is committed, pushed, AND deployed to the VM.
**Working tree:** clean. (Dev screenshots are now gitignored via a `Screenshot *.png` glob.)
**CI / deploy:** all green. The last push deployed to the VM successfully; **migration 0037 reached head on the live DB** (`alembic_version=0037`, `clip_impressions` table live with RLS enabled), app `/health` = `{"status":"ok", postgres/redis/storage ok}`.

> ⚠️ **Pushing `main` auto-deploys to the VM** (Docker publish → "Deploy to production", self-hosted runner). Nothing is pending to push right now.

---

## CURRENT FOCUS

**There is NO pending code work.** This session took five tracks from idea → code → tests → committed → pushed → deployed. **Everything remaining is a single staging/external verification pass on the live host** (create SaaS accounts, set secrets, run the live/real-data checks). The code for all of it is live and dormant-safe.

### ✅ SHIPPED this session (all on `origin/main`, all deployed)

1. **Observability on the VM — Issue 326** (`e837979` + `d83da76`). OTel + Sentry code (`observability.py` → `main.py`/`worker/celery_app.py`, auto-instruments Celery/SQLAlchemy/Redis/httpx/botocore + Anthropic, LLM content forced OFF for PII). VM activation wired into `deploy.yml`'s guarded secret-sync (`SENTRY_DSN`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS` + `IMAGE_SHA`). **Strict no-op until the 3 secrets exist** (they don't yet). 42/42 obs tests.
2. **Tracker reconciliation** (`b8838af`). Closed stale duplicate OPEN entries (#226/229/230/232 were already done; #241 superseded by #326); annotated #326. Open-issue count is now trustworthy.
3. **Disaster-Recovery batch — Issues 255–258** (`ab7a675`). `scripts/backup_pg.sh` (streamed pg_dump→gzip→openssl AES-256→separate R2 bucket; secret-hygiene-hardened, no client deletes), `BACKUP_*` config, gated pre-migration dump in **both** deploy paths (#257), full DR runbook (escrow/restore-drill/4 failure modes), two-leg key-escrow doc (#255), R2 Object-Lock Compliance-mode decision (#258). 11 local tests.
4. **The "moat" eval batch — Issues 198–202** (`d7eeab1`, `a51b3b3`, `47a1f44`):
   - **#198** efficacy harness — `tests/eval/metrics.py` (pure NDCG@5/MAP@5/MRR/Kendall-τ/chrono-split/paired-bootstrap) + `tests/eval/efficacy.py` (3 rankings: random / generic-signal / DNA+preference) + `scripts/eval_efficacy.py`. 16 local tests.
   - **#199 ✅ FULLY DONE** — 8 adversarial geometry fixtures + ranking-aware fixture + aggregate 100%-pass-rate gate (`SCENARIO_FLOOR=14`); "eval harness hardened" pre-launch gate reconciled CLAUDE.md ↔ PROJECT_STATE.
   - **#200** recency half-life parameterized (`DECAY_HALF_LIFE_DAYS`, default 30).
   - **#201** `performed_well` baseline fixed to comparable **Shorts** (was full-video median → flipped nearly every Short to False); 3× recency multiplier kept over hard dominance.
   - **#202** `clip_impressions` log (model + migration 0037 + RLS + best-effort write in `list_clips`) — LIVE on prod.

### → NEXT ACTION — the staging/external verification pass (no code; needs live-host/account access)

Do these in one sitting on the VM / in the SaaS consoles. Grouped by area:

**A. Observability (#326) — light it up**
1. Create a **Sentry** project → `SENTRY_DSN`; a **Grafana Cloud** stack → OTLP endpoint + Basic-auth token → `OTEL_EXPORTER_OTLP_ENDPOINT` + `OTEL_EXPORTER_OTLP_HEADERS`. (See `docs/RENDER_DEPLOY.md` §11 — applies to the VM.)
2. Set all 3 as **GitHub Actions secrets** → push any commit (or re-run "Deploy to production") so the secret-sync writes them to the VM `.env` and the exporters arm.
3. Verify: run one upload→clip flow; confirm an HTTP→Celery trace in Grafana Cloud Tempo + an exception in Sentry. Then close #326's external ACs.

**B. Disaster Recovery (#255–258) — arm the safety net** (runbook: `docs/RUNBOOKS.md` → Disaster Recovery)
1. **#255 (do FIRST):** escrow `TOKEN_ENCRYPTION_KEY` + `JWT_SECRET_KEY` + a `/opt/autoclip/.env` snapshot to **two** legs (1Password + GCP Secret Manager). A restore is useless without the key.
2. **#256:** create the **separate** `creatorclip-backups` R2 bucket; set `BACKUP_R2_BUCKET` + `BACKUP_ENCRYPTION_KEY` in VM `.env` + GH secrets; install `awscli` on the VM; add the nightly cron; **run the restore drill** (the load-bearing AC).
3. **#258:** apply R2 **Object Lock (Compliance mode, ≥14d)** on the backup bucket + lifecycle rules (daily/weekly/predeploy + source/ + clips/).
4. Once #256 backups exist, the **#257 pre-migration dump gate stops skipping** and starts protecting every deploy automatically.

**C. The moat (#198/#200/#201/#202) — run on real data** (all need Postgres; `alembic upgrade head` already brought 0037)
1. Run `python3 scripts/eval_efficacy.py` on the VM → the pooled/per-creator NDCG table (#198).
2. Half-life sweep {15,30,60,90} via the harness; change `DECAY_HALF_LIFE_DAYS` default ONLY if a value clears the incumbent's CI (#200).
3. Measure `performed_well` label-bias before/after the Shorts-baseline fix (#201).
4. Run the `clip_impressions` RLS integration test against real PG (#202); the per-retrain standing-report emission is deferred to here + Issue 265's ratchet.

### Lower-priority follow-ups (raised earlier, still open)
- **Render DB cleanup:** remove the temporary IP-allowlist entry on the (empty, unused) Render `creatorclip-db`.
- **Re-run a failed-transcription video** to confirm the Deepgram fix holds.
- **~20 YouTube-linked videos at `ingest_status=pending`** — confirm "linked but never queued" (expected per Issue 317), not a stuck queue.

---

## WHAT WORKS NOW (don't re-investigate)

- **Upload→clips pipeline is live-verified E2E on the VM** (ingest→transcribe→signals→`generate_clips` → 6 candidates). Transcribe failures were the now-fixed Deepgram bugs (`8db5c3c`, `705cb56`).
- **`render_clip` is user-triggered** — a clip at `render_status=pending` is NORMAL, not a failure. R2 `clips/` is empty only because nobody has clicked render. Don't "fix" it.
- **R2 (`autoclip-studio`) is healthy** — inspect with `python3.12 scripts/r2_inspect.py`; pipeline state with `python3.12 scripts/clip_pipeline_state.py` (both read-only; need `R2_*`/`DATABASE_URL` in local `.env`).
- **The whole session's code is deployed and dormant-safe:** OTel/Sentry no-op until their secrets exist; the pre-migration dump gate skips until backups are configured; the `performed_well` poll defers when <3 comparable Shorts; the impression write can't break the read path. Migration 0037 is at head with RLS live.
- **Tests:** full unit lane green (~1670); ruff + mypy clean. DB-backed parts of the DR + moat batches are written with `integration`-marked tests that run on staging (no Docker/PG on the local box).

---

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Live prod host | **VM** — SSH alias `creatorclip-vm`, dir `/opt/autoclip`, `docker-compose.prod.yml`, Cloudflare tunnel (`cloudflared`) |
| Live DB | VM `postgres` container — `pgvector/pgvector:pg16`, db/user `creatorclip`. Now at `alembic_version=0037`. Inspect: `ssh creatorclip-vm 'cd /opt/autoclip && docker compose -f docker-compose.prod.yml exec -T postgres psql -U creatorclip -d creatorclip'` |
| Live logs | `ssh creatorclip-vm 'cd /opt/autoclip && docker compose -f docker-compose.prod.yml logs -f app worker'` (ephemeral — the reason #326 exists) |
| Deploy pipeline | push `main` → "Docker publish" → "Deploy to production" (self-hosted runner on the VM). Watch: `gh run list` / `gh run watch`. `scripts/deploy.sh` is the manual mirror. |
| Image | `ghcr.io/reese8272/creatorclip:latest` |
| R2 (media) | `autoclip-studio` (`R2_*` creds). **Backups (#256) go to a SEPARATE `creatorclip-backups` bucket — not yet created.** |
| Render (NOT live) | blueprint `render.yaml`; `creatorclip-db` empty/unused. **Do not trust it for prod data.** |
| Branch / HEAD | `main` @ `47a1f44`, in sync with `origin/main`; `staging` in sync |
| Secrets (names only) | live: `R2_*`, `DATABASE_URL`, AI keys (synced on deploy). **Not yet set:** `SENTRY_DSN`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS`, `BACKUP_R2_BUCKET`, `BACKUP_ENCRYPTION_KEY`. Reference by name; never echo values. |

---

## CONSTRAINTS & GOTCHAS

- **Render ≠ live.** The live data is on the VM; the Render DB is empty. (Memory: `project_live_deployment_topology`.)
- **Pushing `main` deploys to the VM.** Verify intent before `git push`. All current code is dormant-safe, but setting the observability/backup secrets + next deploy ARMS those subsystems.
- **OTel/Sentry no-op without env** (`OTEL_EXPORTER_OTLP_ENDPOINT` / `SENTRY_DSN`). **PII boundary:** LLM span content forced OFF (`TRACELOOP_TRACE_CONTENT=false`) — don't re-enable.
- **`scripts/backup_pg.sh` secret hygiene** (#256): DB creds stay container-side, passphrase via `openssl -pass env:` (never argv), refuses to run if backup bucket == media bucket, never deletes (retention via R2 lifecycle). **The encryption key must NEVER be stored inside the backup it protects** (circular dependency).
- **R2 Object Lock must be Compliance mode**, not Governance (Governance is admin-overridable). R2 has no GA versioning — Object Lock is the only delete-protection.
- **`performed_well` is Shorts-vs-Shorts now** (#201) — judged against the median of the creator's published Shorts, deferred below 3 comparable Shorts. Don't revert it to the full-video median.
- **Local box has no Docker/Postgres** — tests run via `python3.12` / `.venv`; DB-backed tests are `integration`-marked and run on staging. (Memory: `local_dev_test_env`.)
- **Secrets are write-only on GH / live only in the VM `.env`** — reference by name.

---

## POINTERS

- `docs/issues.md` — per-issue briefs + statuses (#198–202, #255–258, #326) + full roadmap
- `docs/DECISIONS.md` — 2026-06-26/27 entries: observability stack, DR batch, eval methodology, performed_well baseline, impression log
- `docs/RUNBOOKS.md` → **Disaster Recovery** — escrow setup + restore drill + 4 failure modes (the #255–258 operator checklist)
- `docs/RENDER_DEPLOY.md` §11 — Sentry + Grafana Cloud setup steps (apply to the VM)
- `docs/COMPLIANCE.md` — data-classes table (now incl. backups + clip_impressions)
- `docs/PROJECT_STATE.md` — progress rows for everything shipped this session
- `CLAUDE.md` — project rules (Read Order, issue workflow, standards)
- Memory: `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` — esp. `project_live_deployment_topology.md`
- Session helpers (committed): `scripts/r2_inspect.py`, `scripts/clip_pipeline_state.py`, `scripts/backup_pg.sh`, `scripts/eval_efficacy.py`
