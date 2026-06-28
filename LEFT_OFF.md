# LEFT_OFF — pre-prod assessment + Lane L21 edge-case hardening (5 issues shipped on a feature branch)

> **Read this first.** Living "where we are right now" handoff for a fresh session with zero memory.
> Source-of-truth docs live in `docs/`; this file orients and points to them — it is NOT a source of truth.

**Last updated:** 2026-06-28
**Checked out:** `claude/llm-rendering-video-assessment-81dy8o` — **pushed to `origin/<that branch>`** (in sync). **NOT `main`. NOT merged. NOT deployed.**
**Working tree:** clean, all work committed + pushed.
**CI / deploy:** N/A for this branch — only `main` auto-deploys. No PR was opened (not requested). To ship this work: open a PR → merge to `main` (which then auto-deploys the VM).

> ⚠️ **This is a feature branch — pushing it does NOT deploy.** The prior session's `main`-side work
> (observability/DR/moat, below) is already on `main` + deployed; the staging/external verification pass
> for it is still outstanding and unchanged.

---

## CURRENT FOCUS — this session (feature branch)

Took the LLM/rendering/video pipeline through **assess → backlog → build** for the production push. Two
deliverables + five shipped hardening issues, all committed + pushed to the feature branch.

### ✅ SHIPPED this session (on `origin/claude/llm-rendering-video-assessment-81dy8o`)

1. **Assessment** — `docs/assessment/LLM_RENDER_VIDEO_ASSESSMENT.md`: logic + observability review of the
   LLM/render/video pipeline. Headline: structurally sound; gaps cluster in **silent failures** +
   **asymmetric observability** (verified-vs-reported tagged).
2. **Lane L21 edge-case test backlog** — `docs/issues_edge_case_hardening.md` (Issues **327–340**), a
   whole-project edge-case test catalog cross-referenced against existing `tests/*.py` so each item is a
   genuine gap; pointer added in `docs/issues.md`. Includes the systemic "inverted/OOB-timestamps pass
   silently" finding + a suspected-defect table.
3. **Five W0/`local` foundational issues built — each fixed a real latent defect:**
   - **#327** geometry validation at the signal boundary (`ingestion/signals.py` drops malformed events
     w/ WARNING+count; `window.py` `i1<=i0` guard) — `tests/test_geometry_validation.py`.
   - **#332** `observability.record_llm_metric` dual-shape adapter wired into the 10 LLM modules that were
     **invisible to the `llm_tokens_total` cost dashboard** — `tests/test_llm_metrics_coverage.py`.
   - **#331** `observability.warn_if_truncated` surfaces `stop_reason=="max_tokens"` (silent truncation);
     wired into the streaming wrapper + all non-streaming `.create()` JSON sites — `tests/test_llm_truncation.py`.
   - **#328** `ranking._safe_score` (NaN→−inf deterministic rank) + non-finite rerank guard +
     `candidates.py` DEBUG breadcrumb — `tests/test_clip_engine_edges.py`.
   - **#338** `predict_score` positive-class-column selection (single-class model no longer `IndexError`s);
     `clip_features` NaN→0.0; `config.py` fail-fast validators (`DECAY_HALF_LIFE_DAYS` etc.) —
     `tests/test_preference_edges.py`.

**Gates:** full unit lane **1735 passed** (+34 new), ruff + mypy clean on all touched files. One
pre-existing failure (`deepgram` SDK not installed in this venv) — env, not code; already in
`docs/OFF_COURSE_BUGS.md`.

### → NEXT ACTION (pick one)

1. **Merge this branch** — open a PR for `claude/llm-rendering-video-assessment-81dy8o` → `main`
   (merging auto-deploys the VM; the L21 changes are additive guards + tests, dormant-safe).
2. **Continue Lane L21** — remaining issues, all still OPEN in `docs/issues_edge_case_hardening.md`:
   - `local`-verifiable now: **#330** (captions/filler/edits), **#334** (ingestion; some render-env).
   - need the `integration` lane (real PG/Redis, unconfigured on this box): **#335** (youtube), **#336**
     (worker pipeline — carries 2 confirmable suspected defects: `generate_clips` RefundOnFailureTask +
     ingest WAV-integrity short-circuit), **#337** (observability/health), **#339** (router surface),
     **#340** (security/auth/billing — highest priority).

### Outstanding on `main` (prior session — unchanged, still pending)

The observability/DR/"moat" external verification pass is **already deployed** and still needs its
one-sitting live-host/account setup. Unchanged by this branch. Summary:
- **Observability (#326):** create Sentry + Grafana Cloud, set `SENTRY_DSN` / `OTEL_EXPORTER_OTLP_*` as GH
  secrets → redeploy arms the exporters (no-op until then). Verify a trace + an exception.
- **Disaster Recovery (#255–258):** escrow keys (2 legs) **first**; create the separate
  `creatorclip-backups` R2 bucket + `BACKUP_*` secrets + nightly cron + **restore drill**; R2 Object Lock
  (Compliance ≥14d). Runbook: `docs/RUNBOOKS.md` → Disaster Recovery.
- **The moat (#198/#200/#201/#202):** run `scripts/eval_efficacy.py` on real PG; half-life sweep;
  `performed_well` bias before/after; `clip_impressions` RLS integration test.

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
- **Tests:** full unit lane green (**1735**, incl. this branch's +34); ruff + mypy clean. DB-backed parts (DR + moat + L21 #335–340) are `integration`-marked and run on staging (no Docker/PG on the local box). 1 pre-existing `deepgram`-SDK env failure is logged in `docs/OFF_COURSE_BUGS.md`.

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
| Branch / HEAD | **`claude/llm-rendering-video-assessment-81dy8o`** (this session), pushed + in sync with origin. **NOT merged to `main`.** `main` is still @ `47a1f44` + deployed. |
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

- `docs/assessment/LLM_RENDER_VIDEO_ASSESSMENT.md` — this session's LLM/render/observability assessment
- `docs/issues_edge_case_hardening.md` — **Lane L21** edge-case test backlog (Issues 327–340; 327/328/331/332/338 DONE)
- `docs/issues.md` — per-issue briefs + statuses (#198–202, #255–258, #326) + full roadmap + L21 pointer
- `docs/DECISIONS.md` — 2026-06-26/27 entries: observability stack, DR batch, eval methodology, performed_well baseline, impression log
- `docs/RUNBOOKS.md` → **Disaster Recovery** — escrow setup + restore drill + 4 failure modes (the #255–258 operator checklist)
- `docs/RENDER_DEPLOY.md` §11 — Sentry + Grafana Cloud setup steps (apply to the VM)
- `docs/COMPLIANCE.md` — data-classes table (now incl. backups + clip_impressions)
- `docs/PROJECT_STATE.md` — progress rows for everything shipped this session
- `CLAUDE.md` — project rules (Read Order, issue workflow, standards)
- Memory: `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` — esp. `project_live_deployment_topology.md`
- Session helpers (committed): `scripts/r2_inspect.py`, `scripts/clip_pipeline_state.py`, `scripts/backup_pg.sh`, `scripts/eval_efficacy.py`
