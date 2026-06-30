# LEFT_OFF — smoke harness (#341) → caught 2 prod issues → fixed (#342 fence crash, #343 RLS)

> **Read this first.** Living "where we are right now" handoff for a fresh session with zero memory.
> Source-of-truth docs live in `docs/`; this file orients and points to them — it is NOT a source of truth.

> **🔒 #343 (SEV1) FIXED + VERIFIED LIVE (2026-06-30):** prod RLS tenant isolation is now enforced — the
> app connects as `creatorclip_app` (no BYPASSRLS); `DATABASE_MIGRATION_URL` = superuser `creatorclip` for
> Alembic + worker sweeps. Verified: wrong-tenant→0 rows, real creator→their rows, app+worker healthy.
> Rollback = VM `.env.bak-pre-rls*` → `DATABASE_URL` back to `creatorclip` + recreate. **Deferred:** the
> optional full ownership transfer to a dedicated `creatorclip_migrate` (DECISIONS 2026-06-30).

**Last updated:** 2026-06-30
**Branch / HEAD:** `main` @ **`34b231f`** — **in sync with `origin/main`** (0 ahead / 0 behind).
**Working tree:** clean except untracked `notes_for_issues.txt` (the user's scratch notes — leave it).
**CI / deploy:** ✅ This work is **merged to `main` and DEPLOYED to the live VM** — `Docker publish`
(`28413024753`) + `Deploy to production` (`28413071745`) both **success** (2026-06-30).

---

## CURRENT FOCUS — this session (DONE & shipped)

Built a **live-in-isolation smoke-test harness** (Issue **341**, new Lane **L22 — Live Smoke**); on its
first live run it **caught a deployed bug** (LLM generators crashed on the real API's markdown-fenced
JSON); filed + fixed that as Issue **342** and deployed the repair. Both are on `main` and live.

### → NEXT ACTION (pick one — nothing is blocked)

1. **Run the full harness against the live VM** (the checks that can't run on the dev box — no creds /
   no ffmpeg here): on a box with the prod `.env` (or on the VM),
   `RUN_LIVE_SMOKE=1 python3.12 scripts/live_smoke.py --target prod --seed` then drop `--seed` for
   subsequent runs. `db / isolation(RLS) / pipeline / render / clean / r2` only run there;
   `title/caption/explain` (`--with-llm`) + `publish` (dry-run) are already green from the dev box.
   **Teardown after:** `--teardown` (purges the `__smoke_canary__` creator + its `smoke/<id>/` R2 prefix).
2. **Identify the next batch of smoke checks** (user's "identify more smoke testing"): account
   deletion/erasure, billing deduct/refund, real-staging publish path, SSE task stream.
3. **Optional follow-up on #342:** layer native structured outputs onto `scoring`/`chapters` too (they
   shipped via `extract_json_block` — the logged deviation; see `docs/DECISIONS.md` 2026-06-30).

### Outstanding on `main` (prior sessions — unchanged, still pending external setup)

The observability/DR/"moat" work is deployed but still needs its one-sitting live-host/account pass:
- **Observability (#326):** create Sentry + Grafana Cloud; set `SENTRY_DSN` / `OTEL_EXPORTER_OTLP_*` GH
  secrets → redeploy arms the exporters (no-op until then). Verify a trace + an exception.
- **Disaster Recovery (#255–258):** escrow keys (2 legs) **first**; create the separate
  `creatorclip-backups` R2 bucket + `BACKUP_*` secrets + nightly cron + **restore drill**; R2 Object
  Lock (Compliance ≥14d). Runbook: `docs/RUNBOOKS.md` → Disaster Recovery.
- **The moat (#198/#200/#201/#202):** run `scripts/eval_efficacy.py` on real PG; half-life sweep;
  `performed_well` bias before/after; `clip_impressions` RLS integration test.

### Lower-priority follow-ups (still open)
- **Lane L21 edge-case hardening:** Issues **329, 330, 333, 334** (`local`-runnable) + **335–340**
  (`integration`/render-env) still OPEN — `docs/issues_edge_case_hardening.md`.
- **Render DB cleanup:** remove the temporary IP-allowlist entry on the (empty) Render `creatorclip-db`.
- **~20 YouTube-linked videos at `ingest_status=pending`** — confirm "linked but never queued" (expected
  per Issue 317), not a stuck queue.

---

## WHAT WORKS NOW (don't re-investigate)

- **Issue 342 fix is LIVE-verified.** `claude-sonnet-4-6` wraps JSON in a ` ```json ` fence; the
  generators did a bare `json.loads` → `JSONDecodeError`, breaking the deployed Review per-clip features
  (322/323/325). Fixed two-track: **structured outputs** (`output_config.format`) on
  `clip_titles/clip_captions/clip_explain` (no web_search), **`extract_json_block`** on `hooks`
  (web_search → citations 400s structured outputs), `chapters`, `scoring`, `thumbnails`. Verified against
  the **real API** at the pinned `anthropic==0.105.2`: harness `--with-llm` → title 3/3, caption 3/3,
  explain 2/2 (was 0 passed / 1 fail). Regression locked: `tests/test_llm_fence_parsing.py` (6 tests).
- **Issue 341 harness is dormant-safe.** `scripts/live_smoke.py` exits 0 unless `RUN_LIVE_SMOKE=1`; no
  effect on prod until run manually. `tests/test_live_smoke.py` (13 offline tests) green.
- **Upload→clips pipeline is live-verified E2E on the VM** (ingest→transcribe→signals→generate_clips).
- **`render_clip` is user-triggered** — a clip at `render_status=pending` is NORMAL, not a failure.
- **Tests:** full unit lane **1778 passed, 0 failed** (+19 this session); ruff + mypy clean on all touched
  files. DB-backed parts are `integration`-marked and run on staging (no Docker/PG on the dev box).
  1 pre-existing `deepgram`-SDK env failure logged in `docs/OFF_COURSE_BUGS.md`.

---

## THE ARC THAT LED HERE

1. User asked for the current issues + a way to **test live in isolation** (upload/render? clip/save?
   caption? title?) — "or are they too interrelated to separate?"
2. Mapped the pipeline DAG: one indivisible upstream chain + a fan-out of independent leaf ops →
   **not** too interrelated; isolate the leaves with a persistent seeded **canary** fixture.
3. Ran the issue-workflow → built **Issue 341** (harness, Lane L22), flag-gated like `llm_e2e.py`.
4. First live `--with-llm` run **caught a real deployed bug** → the markdown-fence JSON crash.
5. Filed **Issue 342**, researched the fix via `/claude-api` (native structured outputs is GA on
   Sonnet 4.6, incompatible with citations), built the two-track fix, live-verified, regression-tested.
6. Committed both → merged to `main` → pushed → **deployed to the live VM** (all green).

---

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Live prod host | **VM** — SSH alias `creatorclip-vm`, dir `/opt/autoclip`, `docker-compose.prod.yml`, Cloudflare tunnel |
| Live DB | VM `postgres` container — `pgvector/pgvector:pg16`, db/user `creatorclip` (alembic at `0037`) |
| Deploy pipeline | push `main` → `Docker publish` → `Deploy to production` (self-hosted runner on the VM). Watch: `gh run list` / `gh run watch`. **Pushing `main` DEPLOYS to prod.** |
| Image | `ghcr.io/reese8272/creatorclip:latest` |
| R2 (media) | `autoclip-studio` (`R2_*` creds). Smoke canary writes are namespaced under `smoke/<creator_id>/`. |
| New this session | `scripts/live_smoke.py` (harness), `tests/test_live_smoke.py`, `tests/test_llm_fence_parsing.py` |
| Smoke canary | deterministic `uuid5` ids; creator `__smoke_canary__`, `google_sub=live-smoke-canary` (in `scripts/live_smoke.py`) |
| Local merged branch | `claude/live-smoke-and-structured-outputs` (fast-forwarded into `main`; local-only, safe to delete) |
| Anthropic SDK | pinned `anthropic==0.105.2` — `output_config.format` structured outputs **confirmed working** at this version |
| Secrets (names only) | live: `R2_*`, `DATABASE_URL`, `ANTHROPIC_API_KEY`, AI keys. **Not yet set:** `SENTRY_DSN`, `OTEL_EXPORTER_OTLP_*`, `BACKUP_*`. Reference by name; never echo values. |

---

## CONSTRAINTS & GOTCHAS

- **Pushing `main` auto-deploys the live VM.** Verify intent + green tests before `git push origin main`.
- **`scripts/live_smoke.py` needs the full prod `.env` + ffmpeg** for the db/r2/render/pipeline checks —
  the dev box has neither (only `ANTHROPIC_API_KEY`), so only the LLM + publish-dry-run checks run here.
  The dev box's `.env` uses `KEY = value` spacing — the harness/helpers tolerate it; raw `export $(grep …)`
  does **not**.
- **Structured outputs is incompatible with web_search citations** (400s) — that's why `hooks`/`titles`/
  `thumbnails` use `extract_json_block` instead. Don't "upgrade" those to `output_config.format`.
- **Render ≠ live.** The live data is on the VM; the Render DB is empty/unmigrated. (Memory: `project_live_deployment_topology`.)
- **Local box has no Docker/Postgres** — unit lane via `python3.12`/`.venv`; DB-backed tests are `integration`-marked.
- **Secrets are write-only on GH / live only in the VM `.env`** — reference by name.

---

## POINTERS

- `scripts/live_smoke.py` — the L22 harness (this session). Helpers: `scripts/llm_e2e.py` (LLM-only, #319),
  `scripts/r2_inspect.py`, `scripts/clip_pipeline_state.py` (read-only DB/R2 diagnostics).
- `docs/issues.md` — Lane **L22** (#341) + **L20** (#342 DONE) + full roadmap + the L21 pointer.
- `docs/issues_edge_case_hardening.md` — Lane **L21** edge-case backlog (327–340).
- `docs/DECISIONS.md` — 2026-06-30 entries: live-smoke canary (341); structured-outputs vs extraction (342).
- `docs/OFF_COURSE_BUGS.md` — the fence-crash row is now ✅ fixed in #342.
- `docs/PROJECT_STATE.md` — session rows for 341 + 342.
- `docs/SOT.md` — architecture; notes the new live-smoke lane.
- `CLAUDE.md` — project rules (Read Order, issue workflow, standards).
- Memory: `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` — esp. `project_live_deployment_topology.md`.
