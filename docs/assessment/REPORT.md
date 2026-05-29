# CreatorClip — Production Assessment

**Date:** 2026-05-29  ·  **Commit:** `ee59001` (branch `claude/codebase-quality-assessment-I0Tcg`)  ·  **LOC:** ~16k Python  ·  **Tests:** 373 passed, 1 skipped (per PROJECT_STATE)

## VERDICT: PRODUCTION-READY — **NO**

Not yet — but the gap is hardening and two product-correctness defects, **not**
foundations. The security fundamentals are genuinely solid: per-creator isolation
is complete on every query traced (no cross-tenant leak found in any module),
tokens always go through `decrypt()` and never hit a log line, bandit is clean
(0 high / 0 medium), and no virality promise exists anywhere. What blocks launch:
**1 infrastructure BLOCKER**, **two broken core differentiators**, a recurring
**Celery-idempotency** class that risks silent data loss/double-spend, pervasive
**blocking calls on async loops**, a **missing pgvector index**, and **14
dependency CVEs**. None require a rewrite; all are well-scoped fixes.

---

## Layer 0 — deterministic gates (from baselines.json, Python 3.12 + Redis)
| Gate | Result | Status |
|---|---|---|
| ruff | 0 issues | ✅ |
| bandit (SAST) | 0 high / 0 medium | ✅ |
| coverage | 69.97% (now the floor) | ⚠️ below the 80% target |
| mypy | 30 errors | ⚠️ ratchet to 0, then enable `disallow_untyped_defs` |
| pip-audit | **14 vulnerabilities** | ❌ triage; critical/high in 7 days |

---

## Launch-blocking items (must fix before public launch)

### 🔴 Infrastructure BLOCKER
1. **psycopg3 prepared statements break under PgBouncer transaction pooling**
   (`db.py:14-20`). Tests pass because CI hits Postgres directly; behind the
   planned transaction-mode pooler you'll get `prepared statement "_pg3_…" does
   not exist`. Fix: `connect_args={"prepare_threshold": None}`; verify with a
   Locust run behind PgBouncer. *(scale axis A)*

### 🟠 Broken core differentiators (the product doesn't do what it promises)
2. **Rendered clips don't clip the setup** (`clip_engine` → `worker/tasks.py:291`).
   The render cuts from `start_s` (the fixed peak−75s fallback), **not** the
   computed `setup_start_s`. Scoring, API, and the eval all key on `setup_start_s`,
   but the actual bytes don't — defeating CLIPPING_PRINCIPLE #2. Fix: render from
   `setup_start_s`; add an eval assertion on the *rendered* segment start.
3. **The personalization loop is not wired** — found independently by two agents:
   `preference/train.py:28 build_and_save` has **no caller** (model never trained)
   AND `clip_engine/ranking.py:26 rerank_with_preference` is **never invoked**
   (model never applied). The North-Star "learns your style, adapts as you evolve"
   is unshipped; ranking is DNA-only and silently no-ops the reranker forever.
   Fix: add a debounced/Beat `retrain_preference` Celery task and call the
   reranker in `generate_and_rank_clips`, gated on a trained model.

### 🟠 Celery idempotency under at-least-once delivery (data-loss / double-spend class)
4. **`generate_clips` destroys creator feedback + published-clip outcomes on
   redelivery** (`worker/tasks.py:76` → `ranking.py:88` delete+reinsert with
   `cascade=all,delete-orphan`). A redelivered `build_signals` silently wipes the
   training signal — BLOCKER-class data corruption. Fix: no-op when clips exist
   with feedback/outcomes; only replace `pending` clips.
5. **`acks_late=True` without `task_reject_on_worker_lost`** (`worker/celery_app.py:27`)
   — an OOM-killed media task (routine during ffmpeg/WhisperX) is silently dropped,
   not redelivered. Fix: add the flag (safe only *with* #4).
6. **`build_dna` version race** (`dna/profile.py:50` `max(version)+1`) and
   **`grant_minutes` TOCTOU** (`billing/ledger.py:39`) — redelivery/concurrency
   produce duplicate DNA drafts (double Anthropic+Voyage spend) and rely on a
   UNIQUE backstop that raises an uncaught 500. Fix: IntegrityError-guarded
   SAVEPOINT / advisory lock; mirror the proven `deduct_for_video` pattern.

### 🟠 Dependency CVEs
7. **14 pip-audit vulnerabilities.** Triage now; patch criticals/highs within 7
   days; then ratchet `pip_audit_vulns` baseline to 0.

---

## Cross-cutting patterns (the map-reduce payoff — no single-file review connects these)

- **Blocking calls on async loops** (axis B) — in 5 modules. Worst: the **120s
  Anthropic+web_search improvement brief runs on the API request loop**
  (`routers/improvement.py:65` → `improvement/brief.py`), collapsing p99 under
  concurrency. Also: sync large-file R2 upload on the API loop (`routers/videos.py:132`);
  sync Anthropic + Voyage on the worker loop (`dna/brief.py`, `dna/embeddings.py`);
  no-timeout transcription (`ingestion/transcribe.py`). The worker cases are
  bounded *today* only by the prefork pool — they become SEV1 the moment the pool
  changes. Fix pattern: `asyncio.to_thread(...)` or move to Celery.
- **Prompt caching is cosmetic** (axis: Anthropic SDK) — `dna/brief.py` and
  `improvement/brief.py` both interpolate per-creator data into the cached block →
  ~0% hit. Mandatory caching buys nothing. Fix: split static prefix (cached) from
  volatile payload (uncached).
- **Missing timeouts / backpressure** (axis E) — OAuth httpx calls
  (`youtube/oauth.py:84`), transcription, no 5xx retry, no Celery time limits, and
  an **unbounded `poll_clip_outcomes` quota drain** (`worker/tasks.py:401`, re-polls
  every outcome forever).
- **Missing pgvector index** (axis H) — consensus from `_root_infra` and `dna`:
  `dna_embeddings.embedding` has no HNSW/IVFFlat index → O(rows) similarity scans;
  `ClipFeedback.creator_id` / `PreferenceModel.creator_id` FKs unindexed too.

---

## Scale checklist verdict (scale-checklist.md)
| Axis | Status | Evidence |
|---|---|---|
| A Pool math | ❌ | BLOCKER (psycopg3/PgBouncer); pool ceiling 30 > sidecar 25; no `pool_recycle` *(needs load evidence)* |
| B Async loop hygiene | ❌ | 120s LLM on API loop; sync upload on API loop; sync LLM/Voyage on worker loop |
| C Celery idempotency | ❌ | feedback-wipe on redelivery; acks_late w/o reject_on_worker_lost; build_dna + grant races (deduct + ingest_done_at correctly idempotent) |
| D Tenant isolation | ⚠️ | **complete by inspection — no leak found** (good), but vigilant not structural; RLS still open (Issue 56) |
| E Backpressure | ❌ | missing timeouts; no 5xx backoff; unbounded outcome-poll quota drain; no task time limits |
| F Rate limit / quota | ⚠️ | limiter correctly per-creator on real Redis + spend gate present (good); not load-tested; quota drain via E |
| G Observability | ⚠️ | logs are PII-safe (good); but no request-id, redis client per-call, engine not disposed in lifespan, cache metric masked |
| H Migration / pgvector | ❌ | no HNSW index (consensus); unindexed FKs (migrations otherwise online-safe — Issues 43/47 good) |
| I Secrets / deletion | ⚠️ | crypto MultiFernet rotation + token decrypt + erasure endpoint correct (good); Stripe keys lack prod fail-fast; one near-miss exc-log in oauth |

---

## Tally
| Severity | Count |
|---|---|
| BLOCKER | 1 (+ 2 launch-blocking product gaps, 4 idempotency SEV1s elevated) |
| SEV1 | 25 |
| SEV2 | 39 |
| cleanup | 34 |

Per-module verdicts: every module = **NEEDS-WORK** (none clean, none with an
open cross-tenant BLOCKER). Full per-finding detail with backed fixes and
`file:line` in `docs/assessment/modules/*.md`.

What's genuinely strong and should not be touched: per-creator isolation
coverage, Fernet/MultiFernet key rotation, JWT-derived identity, the recency-decay
math (well-tested), the Issue 39/43/47 hardening (event-loop singleton, purge
gating, beat fairness), and the deduct/ingest idempotency patterns — reuse those
as the template for the fixes above.

---

## Top 10 actions, in order
1. Render from `setup_start_s` (#2) — restores the core promise; ~1 line + eval test.
2. Make `generate_clips` idempotent so redelivery can't wipe feedback (#4).
3. Move the 120s improvement brief off the API loop to Celery (#B, worst p99 risk).
4. Add the HNSW pgvector index + FK indexes via `CREATE INDEX CONCURRENTLY` (#H).
5. Wire the personalization loop: train task + call reranker (#3).
6. `prepare_threshold=None` for PgBouncer + reconcile pool math (#1, axis A).
7. Add timeouts to OAuth + transcription; 5xx backoff; Celery time limits (#E).
8. Fix prompt caching: split static/volatile blocks in both briefs.
9. Idempotent `grant_minutes` + `build_dna`; add `reject_on_worker_lost` (#5/#6).
10. Triage the 14 pip-audit CVEs; bound `poll_clip_outcomes`; then ratchet gates.

## Next-run instructions
- These items map cleanly to issues. Re-run `/assess` after each batch — the
  `history/` snapshot makes the next report a diff, not a re-read.
- Run `tests/perf/` (Locust) against staging behind PgBouncer to convert the
  axis-A/B/E `(needs load evidence)` marks into measured p99 numbers.
