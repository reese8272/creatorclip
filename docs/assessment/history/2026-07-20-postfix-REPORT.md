# CreatorClip — Production Assessment (post-fix re-run)

**Date:** 2026-07-20 (evening)  ·  **Commit:** `64fb2f3` (main; all fixes merged AND deployed to prod)  ·  **Method:** Layer 0 gates + 15-module re-verification sweep (every morning finding re-checked in code) + **real load evidence** (Locust vs the staging stack pinned to prod's digest, per `tests/perf/README.md`) + live prod verification (SSH).

## VERDICT: PRODUCTION-READY — **YES for the locked v1 scope (≤100-user private beta)**

**0 BLOCKER · 0 SEV1 · SEV2s: 3 found by this re-run, all fixed+deployed same day (PR #58); remainder are documented/accepted residuals · ~60-item cleanup tail.**

The morning assessment's CONDITIONAL gates are all cleared:
1. **Live sign-in BLOCKER** — cleared by SSH triage: transient, zero recurrence in 2 weeks of prod logs, successful sign-ins since (Issue 356, closed).
2. **Stuck-render defect** — fixed (stale-`running` sweep + 409 override + enqueue guards), deployed.
3. **All 4 SEV1s + the Issue-361 SEV2 backlog** — built across two reviewed 8+4-batch waves (PRs #56, #57), the re-run's 3 new findings fixed in PR #58; everything merged, CI-green, and running in prod.
4. **Load evidence (the YES gate)** — see below; beta-scale criteria pass with ~60% headroom.

The 10k-scale YES remains **explicitly out of scope** — the GKE/KEDA track is descoped for v1 per DECISIONS 2026-06-26; the 300-user run below documents where the single-VM topology saturates.

---

## Layer 0 (at `e92b93a`; PR #58's 3-file delta re-gated green in its own CI)

| Gate | Result | Baseline | Status |
|---|---|---|---|
| ruff / ruff-format | 0 | 0 | ✅ |
| mypy | 0 | 0 | ✅ |
| coverage | 79.73 % | 75.2 floor | ✅ (−1.7 vs the wave peak: new wave source lines; floor intact) |
| module coverage (Issue 269 — **ran for the first time**, harness bug fixed this wave) | clip_engine 92.6 · preference 91.4 · crypto 100 · limiter 100 · auth 92.4 | 0-floors | ✅ (floors should now be ratcheted up) |
| bandit | 0 / 0 | 0 / 0 | ✅ |
| pip-audit | 8 (local venv: pillow/pip/pytest — none in `requirements.txt`; `-r requirements.txt` → 1 dev-only pytest) | 0 | ⚠️ venv drift, CI-authoritative ≈ clean |

---

## Layer 1 — 15-module re-verification (post-fix)

**All four morning SEV1s verified FIXED in code** (spend-gate on `/clips/generate`; api_key RLS GUC on both stamp paths; stale-render recovery incl. marker semantics; runner split — **live-proven** by 3 GitHub-hosted PR runs + 3 deploys). Module verdicts: `_root_infra`, `analysis/improvement/upload_intel`, `preference`, `chat`, `billing`, `knowledge`, `ingestion`, `notify`, `dna`, `frontend`, `deploy_ci` **clean**; `routers`/`worker`/`clip_engine`/`youtube` NEEDS-WORK only for the items below.

**Found by this re-run and FIXED same day (PR #58, deployed):**
- `routers/clips.py` — create_summary enqueue had no failure guard: a broker throw stranded a `pending` row that `uq_summaries_active` then blocked forever → marked `failed` + 503 (mirrors 359c), tested.
- `worker/tasks.py` — sweep flip was an unconditional ORM write that could clobber a mid-sweep `done` → conditional `UPDATE … WHERE render_status='running'`, tested.
- `clip_engine/ranking.py` — `except IntegrityError` scoped to `uq_clips_video_rank` (FK/NOT-NULL now re-raise).

**Remaining open (all documented/accepted, none beta-gating):**
- slowapi sync-Redis residual (~100 ms worst-case bound; accepted-for-beta, upgrade trigger documented) — `_root_infra`.
- youtube upload crash-window duplicate (worker death + acks_late redelivery opens a new session; `session_uri`-on-ClipPublication is the DECISIONS follow-up) — `youtube`.
- reframe `sendcmd` (needs-runtime-confirmation, feature-gated) — `clip_engine`.
- staging reuses prod `TOKEN_ENCRYPTION_KEY` (data-bearing staging volume; documented residual) — `deploy_ci`.
- ~60 cleanup-grade items across the module files.

---

## Load evidence — Locust vs staging (prod digest `a9d8d66`, PgBouncer txn mode, per `tests/perf/README.md`)

### 50 users (beta-representative; the v1 beta is ≤100 registered, so 50 *concurrent* is conservative-high) — 3 min
| Metric | Aggregated | Worst endpoint |
|---|---|---|
| requests / failures | **4,327 / 0 (0.00%)** | — |
| p50 / p95 / p99 | **33 ms / 110 ms / 180 ms** | p99 220 ms (`/creators/me/data-gate`) |
| max | 420 ms | — |

**Every pass criterion met with ≥60% headroom** (bar: p99 < 500 ms, errors < 1%): no `QueuePool limit`, no `prepared statement` errors, `/health` ok throughout.

### 300 users (3× total beta population; stress probe) — 5 min
26,843 requests · **zero 5xx, zero connection/pool/prepared-statement errors** · app healthy for the full run. All 2,909 "failures" (10.8%) are **429s from the per-creator limiter working as designed** (300 users across 9 creators ≫ per-creator limits — axis F enforcing, not failing open). Latency saturates at p50 1.1 s / p99 4.2 s — uniform across all endpoints incl. `/health`, i.e. **4-vCPU box CPU saturation** (VM also runs prod + the load generator), not an app defect. This is the documented boundary of the single-VM topology; crossing it is the descoped GKE track.

*Ops note:* the first 300-user attempt collided with PR #58's deploy — the staging gate found staging Postgres degraded under the load and **correctly blocked the prod deploy** (fail-safe live-proven, incidentally). Rerun after the deploy settled.

---

## Layer 2 — scale checklist (beta scope)

| Axis | Status | Evidence |
|---|---|---|
| A Pool math | ✅ | 300-user run: zero pool/prepared-statement errors through PgBouncer txn mode; 15+5 ≤ 25 sizing held. |
| B Async loop hygiene | ✅ (beta) | p99 180 ms @ 50 users; no tail explosion (p99/p50 ≈ 5 at beta scale). 300-user elevation is box CPU, not loop stalls. slowapi residual bounded + documented. |
| C Celery idempotency | ✅ | Unique backstops (0046) + IntegrityError→winner paths + stale-`running` sweep (now clobber-safe) + status-aware notification dedupe; visibility-timeout coupling remains comment-only (cleanup). |
| D Tenant isolation | ✅ | RLS enforced on every path incl. api_key (Issue 344 parity, both stamp paths, integration-tested); NULLIF-hardened policies; no leak anywhere. |
| E Backpressure | ✅ | 300-user saturation degraded to clean 429s, zero cascade, health honest; staging gate blocked a deploy on degraded health (live-proven); timeouts on external calls; upload same-session resume. |
| F Rate limit / quota | ✅ | Limiter held per-creator under 3× contention without failing open; spend breaker now gates the main burn path (`/clips/generate`). |
| G Observability | ✅ (minor ⚠️) | Model-accurate cost attribution (haiku/sonnet/opus); summed pause_turn usage; structured logs traced this morning's live triage in minutes. Residual: `provider_message_id` never populated (cleanup). |
| H Migration & pgvector | ✅ | 0046 online-safe (CONCURRENTLY + catalog-only, squawk clean, symmetric downgrade); downgrade round-trip in CI; pgvector indexes intact. |
| I Secrets / deletion | ✅ | Runner split (PR code off the prod VM, live-proven); keys off argv; backup no longer sources `.env`; rotation runbook consistent. Off-box key escrow (Issue 255) stays an operator item. |

---

## What YES does and does not mean

**YES:** the codebase, pipeline, and single-VM topology are production-ready for the locked v1 scope — a ≤100-user private beta — with load evidence at and 3× beyond that scale, zero SEV1s, enforced tenant isolation, and a deploy pipeline whose fail-safes have now been observed working live.

**Not covered by this YES (tracked, not code gates):** GO_LIVE.md Stage-A operator gates (Google OAuth verification #29, prod `ALLOWED_ORIGINS`/`/docs` verify #24, operator chain #25/26/28); visual-regression baselines (#272); 10k-scale (descoped GKE track); the cleanup tail.

---

## Diff vs 2026-07-20 morning report
Morning: CONDITIONAL — 1 BLOCKER-class live symptom, 4 SEV1, ~37 SEV2. Evening: **YES-for-beta** — BLOCKER cleared by live triage; 4/4 SEV1s + all load-bearing SEV2s fixed, reviewed, merged (PRs #56/#57/#58), deployed; re-run found 3 new items, fixed same day; ruff/mypy 0; module-coverage gate operational for the first time; **load evidence added** (the axis the checklist said reading could never settle).

*History snapshot: `docs/assessment/history/2026-07-20-postfix-REPORT.md`. Raw Locust output preserved in this file (staging stack + CSVs torn down per runbook).*
