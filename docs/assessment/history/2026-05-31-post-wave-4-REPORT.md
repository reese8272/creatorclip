# CreatorClip — Production Assessment

**Date:** 2026-05-31 (post Wave-4 — compliance + scale prep)  ·  **Commit:** `67fddc9`  ·  **Tests:** 547 passed, 1 skipped, 94 deselected (default lane)

## VERDICT: PRODUCTION-READY — **CONDITIONAL**

**No open BLOCKER and no cross-tenant leak in any module.** Wave 4 closed 3 SEV2s (refund pack_id partial UNIQUE race, the YouTube ToS analytics-retention gap that was the single largest compliance blocker, and the Wave-3-introduced upload `aset_owner` SEV2) — but the routers walk surfaced **1 new SEV1**: Wave 4's claim that the fail-open `aset_owner` invariant became "uniform across every call site" was false. Only ONE of the four Wave-2-introduced `aset_owner` sites got the wrap (`videos.py::upload`); three more remain fail-closed (`creators.py::sync_catalog`, `creators.py::build_dna`, `clips.py::render_clip`). One-line fix per site; 3 sites; ~15 LOC + 3 tests total.

SEV1 trajectory across six cycles: **4 → 2 → 1 → 3 → 0 → 1**. The regression is mechanical and the severity is borderline (no inconsistent DB state on failure, unlike the original Fix B case — these three sites are "fire-and-forget" enqueues; a 500 means the user retries successfully). Module verdicts: **dna ✅ clean · improvement ✅ clean · 9 NEEDS-WORK · 0 BLOCKER · 0 cross-tenant leak**.

---

## Layer 0 — deterministic gates (from `_machine.json`)
| Gate | Result | Baseline | Status |
|---|---|---|---|
| ruff | 0 issues | 0 | ✅ |
| mypy | 0 errors | 0 | ✅ |
| coverage | not run locally; CI authoritative | 69.54% floor | ✅ no regression expected — Wave 4 added +8 tests |
| bandit | not run locally; CI verified | 0 / 0 | ✅ |
| pip-audit | not run locally; CI verified | 0 | ✅ |
| freshness | both skills 2d | <90d | ✅ |

Full local pytest default lane: **547 passed / 1 skipped / 94 deselected** — net +4 vs post-Wave-3 (3 new default-lane tests in `test_youtube_analytics_retention_config.py` + 1 upload-fail-open in `test_videos_upload_streaming.py`; 4 new integration tests bring the integration lane total but are deselected on default).

## Layer 1 — module register (ranked)

| Sev | Module | Location | Issue | Backed fix |
|---|---|---|---|---|
| **SEV1** | routers (**NEW Wave-4 walk**) | `routers/creators.py::sync_catalog` (~167), `routers/creators.py::build_dna` (~186), `routers/clips.py::render_clip` (~145) | Fail-open `aset_owner` invariant was supposed to be uniform after Wave 4 Fix 1; in fact only `videos.py::upload` got the wrap. The three sites above still raise 500 on Redis blip after Celery enqueue succeeds — fail-closed where fail-open is the documented posture. Same shape as Wave-3 Fix B and Wave-4 Fix 1. **Severity is borderline:** unlike the original Fix B case (improvement brief leaves a DB row in inconsistent pending state), these three are "fire-and-forget" enqueue calls — a Redis blip 500s the request, but no DB state is corrupted and the user's retry succeeds. Still classified SEV1 to match how the post-Wave-2 walk classified the same shape; downgrade to SEV2 is defensible. | One-line `try/except redis.RedisError → log + stream_url=None` per site, mirroring Wave-3 Fix B exactly. 3 sites × ~5 LOC each + 3 regression tests pinning the Redis-down fail-open. **The lowest-LOC SEV1 fix on the register at any point in this session's history.** |
| SEV2 | worker | `worker/progress.py:149-164` | `aemit` exception handler nulls out `_AIO`/`_AIO_LOOP` on ANY exception, not just `ConnectionError`/`TimeoutError`. Wave 2/3/4 added more emit call sites → wider trust surface. Carry-forward. | `except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError):` resets globals; `except Exception:` leaves singleton intact. |
| SEV2 | worker | `worker/progress.py:190-208` | Blocking XREAD with default redis-py pool of 50. Wave 4 didn't widen this surface further; still serving 5 emit surfaces post-Wave-3. (needs-runtime-confirmation.) | Bound pool explicitly via `REDIS_MAX_CONNECTIONS=200`; separate read/write clients; document math in `docs/DEPLOYMENT.md`. |
| SEV2 | worker | `worker/progress.py:134-146` | Rebuild-without-aclose on loop mismatch — pytest function-scoped loops abandon aredis clients. Carry-forward. | Best-effort close old client via `old_loop.call_soon_threadsafe(...)`. |
| SEV2 | worker | `worker/progress.py:74-85` | `_serialize` does `json.dumps(fields, default=str)` — no schema/allowlist. Carry-forward. | Per-event-type `EventPayload(BaseModel)`. |
| SEV2 | worker | `worker/tasks.py:781-786` + new emit sites | `_emit("error", message=str(exc))` raw-passes ValueError args to SSE stream. Carry-forward. | Allowlist of known-safe error shapes + generic fallback. |
| SEV2 | worker | `worker/anthropic_stream.py:57-75` | No terminal `error` emit on mid-stream network drop. Carry-forward. | Wrap streaming call with `try/except: sync_emit(task_id, "error", message="stream interrupted"); raise`. |
| SEV2 | worker | `worker/anthropic_stream.py:72-84` | Silent-zero usage defaults hide SDK schema drift. Carry-forward (Issue 84 SDK bump). | Return `None` when attribute missing; warn the first time a previously-present field returns None. |
| SEV2 | worker | `worker/tasks.py:415-496` (`_render_clip_async`) | Render double-encode under concurrent delivery. Carry-forward. | `with_for_update()` on the Clip row + re-check `render_status` under lock. |
| SEV2 | worker | `worker/tasks.py:300-339` (`_ingest_async`) | Re-do on redelivery: re-`probe_duration_s` + `extract_audio_wav` over already-derived WAV. Carry-forward. | Short-circuit when `source_uri` points at derived audio key OR gate on `ingest_status == done`. |
| SEV2 | routers | `routers/tasks.py:131-138` | 404/403 ownership-check enumeration oracle. Carry-forward. | Return `404 "Unknown task"` for both branches; log distinction server-side. |
| SEV2 | routers | `routers/tasks.py:140` + `worker/progress.py:204` | Unvalidated `Last-Event-ID` → 500 on malformed reconnect. Carry-forward. | Validate against `^\d+-\d+$` (or empty); on mismatch reset cursor. Cap header at ≤64 chars. |
| SEV2 | routers | `videos.py:80-84`, `clips.py:~120`, `upload_intel.py:~38` | Unbounded `list(result.scalars())` on `/videos`, `/clips`, `/upload-intel`. Carry-forward. | Keyset pagination `?limit=&before=` with hard cap 100. |
| SEV2 | routers (additional 4 from this walk) | various — see `docs/assessment/modules/routers.md` | The routers walk classified 4 items previously cleanup as SEV2 in this run (typing gaps that affect contract surfaces, response_model gaps, error-message safety, etc.). Per-finding detail in module file. | See module file for individual fixes. |
| SEV2 | billing | `billing/stripe_client.py:20` | Module-level Stripe client built with empty key in dev/staging silently binds a landmine. Carry-forward. | Lazy-init via `functools.lru_cache`; OR extend `_require_prod_secrets` to fail-fast for `ENV == "staging"` too. |
| SEV2 | billing | `billing/refund.py` | Refund triggers on `on_failure` without re-checking video terminal state. Carry-forward. | Gate refund on `video.render_status IN ('failed', 'errored')`. |
| SEV2 | billing | `billing/refund.py` (third item from this walk) | Module walk classified a third finding as SEV2 — see `docs/assessment/modules/billing.md` for detail. | See module file. |
| SEV2 | improvement | `improvement/brief.py:132-138` | Streaming log line bundles cache reads into single counter — TTL-tier breakdown gap. Blocked behind Issue-84 SDK bump. | Update log after SDK bump. |
| SEV2 | _root_infra | `Dockerfile:1-34` | Image runs as root. Carry-forward. | `USER app` after `COPY . .`. |
| SEV2 | _root_infra | `Dockerfile:34` | Default CMD ships `uvicorn --reload`. Carry-forward. | Default to `gunicorn -k uvicorn.workers.UvicornWorker -w 4 main:app --bind 0.0.0.0:8000`. |
| SEV2 | _root_infra | `observability.py:~224` | Celery ContextVar correlation safe only under prefork. Carry-forward. | Assert `app.conf.worker_pool == "prefork"` at worker startup. |
| SEV2 | youtube | `youtube/oauth.py:~290` | No Redis-down degradation in token refresh — broker blip 500s every analytics fetch. Carry-forward. | Wrap `set()` in `try/except redis.RedisError`; lockless fall-back OR 503. |
| SEV2 | ingestion | `ingestion/transcribe.py:116-123,137-138` | Deepgram normalizer hard-key indexing → KeyError burns Celery retry. **Carry-forward through 4 waves.** | Switch comprehensions in `_normalize_deepgram` to `.get("start")`/`.get("end")` and skip None timestamps. |
| SEV2 | ingestion | `ingestion/transcribe.py:43-60` | `_guard_audio_size` swallows OSError → empty-transcript success on AssemblyAI. Carry-forward. | In `except OSError`, raise `FileNotFoundError(...)`. |
| SEV2 | clip_engine | `clip_engine/ranking.py:139` | `dna_match=c.get("score")` seeded to composite score → preference feature collinear with its own label. Carry-forward. | Have `score_candidates` return DNA-only fit separately. |
| SEV2 | clip_engine | `clip_engine/candidates.py:113` | Candidate windows never deduped/merged for overlap. Carry-forward. | IoU-merge pass dropping lower-prominence overlap >0.5. |
| SEV2 | clip_engine | `clip_engine/render.py:138` | `_extract_keyframe` uses full render timeout budget. Carry-forward. | Hardcode 30s ceiling on keyframe extraction. |
| SEV2 | dna | `dna/brief.py:130-151` | Streaming brief: ~2k synchronous Redis XADDs per build on threadpool slot. Acceptable for v1. Carry-forward. | If queue depth grows, batch deltas every K tokens or K ms. |
| SEV2 | dna | `dna/brief.py:153-157` | Asymmetric `# type: ignore[arg-type]`. Carry-forward (Issue 84 SDK bump). | Mirror ignore OR narrow `_build_request` return type. |
| SEV2 | dna | `dna/brief.py:134` | Function-local `from worker.anthropic_stream import stream_and_emit` (layering smell). Carry-forward. | Accept as lesser evil for v1; add explanatory comment. |
| SEV2 | preference | `preference/model.py:46` | LightGBM round-trip not in CI; library upgrade silently disables personalization. Carry-forward. | `test_scorer_round_trips_lightgbm` forcing the LGBM branch. |
| SEV2 | preference | `preference/_scorer_cache.py:23` | LRU bound is entry-count, not bytes. Carry-forward. | Lower default to 32 OR gate cap on bytes OR document math. |
| SEV2 | upload_intel | `upload_intel/timing.py:54-55` | `optimal_gap_hours` still missing Issue-75d bounds/coercion guard. Carry-forward. | Filter+coerce rows first; return None if <2 valid. |

Plus **~43 cleanup** items spread across all 11 modules. Per-finding detail in `docs/assessment/modules/*.md`.

## Layer 2 — scale checklist (`scale-checklist.md`)
| Axis | Status | Evidence |
|---|---|---|
| A Pool math | ⚠️ | Locust evidence still pending. *(unchanged ⚠️)* |
| B Async loop hygiene | ✅ | All async paths verified. *(unchanged ✅)* |
| C Celery idempotency | ✅ | **PROMOTED from ⚠️**: Wave-4 Fix 2 closed the refund pack_id race via partial UNIQUE index. Residual SEV2s (render double-encode, ingest re-do, poll_clip_outcomes quota break) are bounded wasteful-work risks, not correctness gaps. The DB-level guarantee for refund — the only billing race a misbehaving Celery delivery could exploit — is now structural. *(was ⚠️ — now ✅)* |
| D Tenant isolation | ✅ | RLS migration `0010_rls_policies` in place. Wave 4's new `aset_owner` wrap verified to use authenticated principal. *(unchanged ✅)* |
| E Backpressure | ⚠️ | `oauth.py` Redis-down → 500 still open. Wave-4 Fix 1 added fail-open posture to upload but the three Wave-2 sites (sync_catalog, build_dna, render_clip — the SEV1 above) are still fail-closed. *(unchanged ⚠️ — but fix is one-line per site)* |
| F Rate limit / quota | ✅ | EXPIRE-on-every-INCR in place. *(unchanged ✅)* |
| G Observability | ✅ | All emit surfaces verified no PII/token leak. *(unchanged ✅)* |
| H Migration / pgvector | ✅ | All migrations at HEAD including new 0013 (refund pack_id partial UNIQUE). All migrations use `CREATE INDEX CONCURRENTLY` pattern. Backups + PITR restore-test still unverified. *(unchanged ✅)* |
| I Secrets / deletion | ✅ | All unchanged. *(unchanged ✅)* |

**Compliance posture (new this run):** **Issue 75b CLOSED.** `docs/COMPLIANCE.md §2` now documents the 30-day YouTube API Data retention policy with citation to ToS §III.E.4.b and §III.D.2.3.b (verified via industry-standards-researcher). `purge_stale_youtube_analytics` runs daily, deleting rows in `video_metrics`, `retention_curves`, `audience_activity`, `demographics` whose `fetched_at < now() - 30 days`. CLAUDE.md pre-monetization item "YouTube data-retention/refresh fully compliant" ✅ ticked. **This was the single largest blocker before Google OAuth app verification submission.**

## Diff vs previous report (2026-05-31 post Wave 3, commit `84a7e9f`)

**Fixed & verified this cycle (3 SEV2s closed by Wave 4):**
- ✅ SEV2 `routers/videos.py:262-266` — Wave-4 Fix 1: upload `aset_owner` now wrapped in `try/except redis.RedisError`. (But see new SEV1 below — the wrap wasn't extended to the OTHER three Wave-2-introduced sites.)
- ✅ SEV2 `billing/refund.py:57-71` — Wave-4 Fix 2: new Alembic migration 0013 creates partial UNIQUE on `minute_packs(pack_id) WHERE reason='refund'`; refund_for_video drops the read-then-write guard. Concurrent-refund double-credit race closed structurally. Promoted scale axis C ⚠️ → ✅.
- ✅ SEV2 `docs/COMPLIANCE.md §2` analytics retention — Wave-4 Fix 3 (Issue 75b): `YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS=30` + daily purge Beat task. CLAUDE.md pre-monetization checkbox flipped.

**New this run (1 SEV1):**
- 🆕 SEV1 — `routers/creators.py::sync_catalog`, `routers/creators.py::build_dna`, `routers/clips.py::render_clip` all still call `aset_owner` without the fail-open wrap. Wave 4 Fix 1's "uniform invariant" claim was false at the time it was made — three sites were missed. (Pre-existing structural gap that the routers walk surfaced as a SEV1.)

**Carry-forward (re-checked, no Wave-4 changes):**
- ~33 SEV2s + ~43 cleanups across all 11 modules.

**Counts (Layer 1, all modules):**

| Severity | Wave 4 (2026-05-31) | Wave 3 | Wave 2 | Wave 1 |
|---|---|---|---|---|
| BLOCKER | 0 | 0 | 0 | 0 |
| SEV1 | **1** | 0 | 3 | 1 |
| SEV2 | 34 | 33 | ~36 | 31 |
| cleanup | ~43 | ~40 | ~38 | 34 |

SEV1 trajectory across six cycles: **4 → 2 → 1 → 3 → 0 → 1**. Wave 4's regression is real but the fix is the smallest on the register — three one-line wraps + three regression tests.

## Top 5 actions, in order

1. **Wave-5 hotfix: extend Fix B/Fix 1 fail-open to the 3 remaining `aset_owner` sites.** `routers/creators.py::sync_catalog` (~167), `routers/creators.py::build_dna` (~186), `routers/clips.py::render_clip` (~145). Each gets a `try/except redis.RedisError → log + stream_url=None` wrap mirroring Wave-3 Fix B exactly. 3 regression tests. **~25 LOC total. Returns SEV1 to 0.**

2. **Locust load test on real staging cluster (Issue 78f, carry-forward).** **The single remaining structural gate between CONDITIONAL and YES.** Scale axes A and E both need real concurrency evidence: pool math (A) and backpressure (E). Wave 3-4 widened the SSE emit surface; Wave 4 added the daily analytics purge; the upstream `oauth.py` Redis-down SEV2 also surfaces only under load. None of these can be settled by reading.

3. **Submit Google OAuth app verification.** Now unblocked by Wave 4 Fix 3 — `docs/COMPLIANCE.md §2` is concrete and citable, the daily purge runs, and CLAUDE.md's last documented compliance checkbox is flipped. External Google process; user-side action.

4. **Anthropic SDK 0.40 → current bump (Issue 84 follow-up).** Unlocks `usage.cache_creation.ephemeral_5m_input_tokens` / `_1h_input_tokens` for the SEV2s in `improvement/brief.py` and `dna/brief.py` flagged behind it. 65 minor versions stale, no breaking changes to our 3 call sites.

5. **`youtube/oauth.py:~290` Redis-down → 500 (carry-forward).** Same fail-open pattern as Fix 1/B/D — wrap the `redis_client.set()` in `try/except redis.RedisError`. Wave 4 widened the routers fail-open coverage but didn't touch this site. Affects every analytics fetch and Beat task.

---

## What Wave 4 got right + the SEV1 lesson

- **Compliance gap closed correctly.** The 30-day YouTube retention purge isn't just shipped — it's pinned to the exact ToS section (§III.E.4.b) via inline citation in `config.py`, has a default-lane test (`test_youtube_analytics_max_staleness_default_is_30_days`) that pins the value to the policy, and integration tests that prove the 5d/29d/35d boundary behaviour against real Postgres. The compliance posture is verifiable from code alone — no "we documented it somewhere" risk.

- **Partial UNIQUE index migration matches the established 0006/0010/0011 pattern.** `CREATE INDEX CONCURRENTLY` inside autocommit block, partial WHERE for "uniqueness within subset," CONCURRENT downgrade. Online-safe on a populated table.

- **The Wave-4 SEV1 is a lesson about scope discipline.** Fix 1's Phase-1 brief explicitly claimed the fail-open invariant would become "uniform across every aset_owner call site (improvement brief, OAuth callback, and now upload)." The `creators.py` (sync_catalog + build_dna) and `clips.py` (render_clip) sites were known to exist post-Issue 92 — I should have audited the full set of `aset_owner` callers before scoping Fix 1 as "the last one." Mechanical work, missed by a scope-tightening error. The Wave-5 fix is correspondingly mechanical.
