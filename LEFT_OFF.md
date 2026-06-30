# LEFT_OFF — live-testing push: repaired the bit-rotted integration lane (#344) + L21 #330 cut-list edges

> **Read this first.** Living "where we are right now" handoff for a fresh session with zero memory.
> Source-of-truth docs live in `docs/`; this file orients and points to them — it is NOT a source of truth.

> **Directive this session (user):** *NO MOCKS — live testing only* for everything we can control/reach
> (real Postgres/Redis/ffmpeg here; Anthropic/Voyage/Stripe/YouTube need the VM's keys, so those stay
> live-capable + flag-gated, never silently mocked). "Fix the integration lane first, then the L21 edges."

**Last updated:** 2026-06-30
**Branch / HEAD:** `claude/issue-workflows-test-coverage-59e15u` @ **`a3fc965`** — pushed to origin,
**2 commits ahead of `main`** (`337f52d` #344, `a3fc965` #330). **NOT merged to `main`, NOT deployed.**
**Working tree:** clean.
**CI / deploy:** not yet PR'd. CI already runs `pytest -m integration` on every PR (job exists in
`.github/workflows/ci.yml`) — it was sitting red/unenforced, which is how the lane rotted; it is now green.

> **Note:** prior session's #341/#342/#343 work (smoke harness, LLM fence fix, prod RLS role split) is on
> `main` and DEPLOYED — unchanged by this branch. RLS tenant isolation remains enforced in prod
> (`creatorclip_app`, no BYPASSRLS). This branch is purely test-fidelity + 2 edge-case product fixes.

---

## CURRENT FOCUS — this session (DONE on the branch; awaiting PR)

The integration (`-m integration`) lane had **bit-rotted to 21/134 failing** — CI's integration job sat
red/unenforced and nothing ran it locally. Provisioned the live services this box was missing (PG16 role
+ db + pgvector + `alembic upgrade head`, Redis, ffmpeg) and drove it green.

- **Issue 344 — integration-lane repair (21→0):** conformed stale LLM mock stubs to the real
  `(text, usage)` contract; seeded the minutes gate + Issue-206 `payment_status` webhook guard the
  fixtures predated; **1 real compliance fix** — `AuditLog.before/after_jsonb` stored the JSONB `'null'`
  literal not SQL NULL (`JSONB(none_as_null=True)`, no DDL), so the never-purged `creator.deleted` audit
  again satisfies Issue 247's "no PII payload" invariant; rewrote the per-creator-median test around
  Issue 201's ≥3 comparable-Shorts baseline. **Systemic:** autouse per-integration-test DB isolation
  (TRUNCATE domain tables + clear leaked session-level advisory locks) so the lane is order-independent.
  **134 integration passed** (deterministic + random seeds 1/42).
- **Issue 330 (L21) — captions/filler/edits cut-list edges:** 2 confirmed defects fixed — `captions._to_ms`
  crashed the whole render on a NaN/inf word timestamp; `edits.validate_user_cuts` leaked a bare
  `ValueError` (500 vs typed 422). Plus logged silent skip-paths + defense-in-depth guards.
  `tests/test_cutlist_edges.py` (31 cases, real pysubs2 ASS write+reparse, no mocks).

**Gates green:** unit lane **1805 passed / 0 failed**; integration **134 passed**; clip eval 18/18; ruff +
mypy clean on all touched files.

### → NEXT ACTION (pick one — nothing is blocked)

1. **Continue Lane L21 — recommended next is #340** (security/auth/crypto/billing/RLS — flagged
   highest-priority, has explicit attack tests that don't exist yet: JWT `alg:none`, RLS missing-context
   isolation). The RLS half is now runnable here against the live Postgres + the new per-test isolation
   fixture. Alternative: **#333** (LLM robustness — parse/injection/errors/cache, pure `local`).
2. **Open a PR** for this branch (#344 + #330) and let CI's now-meaningful integration gate run it.
3. **Product follow-up logged in `OFF_COURSE_BUGS.md` (2026-06-30):** the Beat-task `pg_advisory_lock`
   release can be skipped if the `finally` rollback raises on a dead-loop connection → a leaked lock makes
   later polls skip until pool recycle. Test lane is protected; triage the product fix
   (`pg_advisory_xact_lock` or unlock-on-fresh-connection + a skip metric).

### Remaining L21 (after #330)
- `local`-runnable: **329** (logic), **333**, **340** (unit half).
- `integration`/`render-env`: **334, 335, 336, 337, 339**, **340** (RLS), **329** (real ffmpeg).
- See `docs/issues_edge_case_hardening.md`. DONE so far: 327, 328, 330, 331, 332, 338.

### Outstanding on `main` (prior sessions — unchanged, still pending external setup)

The observability/DR/"moat" work is deployed but still needs its one-sitting live-host/account pass:
- **Observability (#326):** create Sentry + Grafana Cloud; set `SENTRY_DSN` / `OTEL_EXPORTER_OTLP_*` GH
  secrets → redeploy arms the exporters (no-op until then). Verify a trace + an exception.
- **Disaster Recovery (#255–258):** escrow keys (2 legs) **first**; create the separate
  `creatorclip-backups` R2 bucket + `BACKUP_*` secrets + nightly cron + **restore drill**; R2 Object
  Lock (Compliance ≥14d). Runbook: `docs/RUNBOOKS.md` → Disaster Recovery.
- **The moat (#198/#200/#201/#202):** run `scripts/eval_efficacy.py` on real PG; half-life sweep;
  `performed_well` bias before/after; `clip_impressions` RLS integration test.
- **Live smoke harness (#341):** run the full harness against the VM
  (`RUN_LIVE_SMOKE=1 python3.12 scripts/live_smoke.py --target prod --seed`, then `--teardown`).

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
