# CreatorClip — Production Assessment

**Date:** 2026-05-29 (re-run, post Issues 58–75)  ·  **Commit:** `f6c73ee`  ·  **LOC:** ~7.8k Python (non-test)  ·  **Tests:** 410 passed, 1 skipped, 55 deselected (per PROJECT_STATE / Layer 0 coverage run)

## VERDICT: PRODUCTION-READY — **CONDITIONAL**

No open BLOCKER, no cross-tenant leak in any module, and the entire prior launch-blocking
set (the psycopg3/PgBouncer BLOCKER, both broken core differentiators, the Celery
data-loss class, the blocking-loop class, the missing pgvector index, and all 14 CVEs) is
**fixed and verified by reading**. What keeps it CONDITIONAL rather than YES: scale axes
A/B/C/E/F still lack the Locust-behind-PgBouncer **load evidence** that only a real run can
produce, plus a thin tail of SEV1/SEV2 hardening — one concurrent-redelivery double-spend
(`build_dna`), two ingestion transcription gaps (whole-file buffering + no SDK request
timeout), the known-open `response_model` coverage, and an unauthenticated `/metrics`
surface introduced with the new observability layer.

---

## Layer 0 — deterministic gates (from _machine.json; Python 3.12 + Redis)
| Gate | Result | Baseline | Status |
|---|---|---|---|
| ruff | 0 issues | 0 | ✅ |
| mypy | 30 errors | 30 | ⚠️ at baseline; ratchet to 0 then enable `disallow_untyped_defs` (Issue 75e) |
| coverage | 70.59% | 69.54% floor | ✅ rose +1.05 (no regression) |
| bandit (SAST) | 0 high / 0 medium | 0 / 0 | ✅ |
| pip-audit | 0 vulns | 0 | ✅ (2 accepted-risk ignores documented in `run_layer0.py`/DECISIONS) |
| freshness | both skills 0d | <90d | ✅ |

All runnable gates passed. Coverage is the unit-test line rate; DB-only paths are covered by
the 55 deselected integration tests, not the unit gate — the floor is a regression guard,
not the 80% target.

## Layer 1 — module register (ranked)

| Sev | Module | Location | Issue | Backed fix |
|---|---|---|---|---|
| SEV1 | dna/worker | `worker/tasks.py:423-430` + `dna/profile.py:52-55` | `build_dna` idempotency re-check runs in its own closed session, not serialized vs the draft INSERT; `build_job_id` non-unique → two concurrent redeliveries both run paid Anthropic+Voyage before the version UNIQUE collides; the loser doesn't catch IntegrityError → retry. **Serial redelivery is safe; concurrent double-spends.** (worker agent flagged same root cause as SEV2.) | `pg_advisory_xact_lock(hashtextextended(creator_uuid,0))` in the **same** session that re-checks `build_job_id` and INSERTs (mirror `preference/train.py:88`); add partial UNIQUE on `creator_dna.build_job_id WHERE NOT NULL`; concurrent-redelivery test asserting 1 draft + 1 LLM call. |
| SEV1 | ingestion | `ingestion/transcribe.py:61-62` | Deepgram `payload={"buffer": f.read()}` buffers the whole source WAV into RAM (hundreds of MB/job × warm workers = OOM); `sr=16000` fix doesn't cover this path. Known-open Issue 75. | Stream the file handle / transcribe-by-URL; reject oversize WAV before read. |
| SEV1 | ingestion | `ingestion/transcribe.py:54-64,117-131` | No SDK-native request timeout on Deepgram/AssemblyAI; job-level `asyncio.wait_for` (Issue 68) can't cancel the spawned OS thread → a hung provider socket leaks a worker thread for the process lifetime. | Set connect+read timeout (~30–60s) on `DeepgramClient` / AssemblyAI HTTP layer so the blocking thread itself returns. (needs-runtime-confirmation on param names.) |
| SEV1 | routers | `routers/*` (18 endpoints) | No `response_model` on any route except billing → untyped OpenAPI + unvalidated, unfiltered responses (response-side leak risk). Known-open Issue 75. | Define `*Out` models (ClipOut/VideoOut/DnaOut/BriefOut/…) + `response_model=` per route; replace hand-built `_clip_response` dict. |
| SEV2 | clip_engine | `clip_engine/ranking.py:129` | `dna_match` is seeded to the composite score and never refined → preference model fed a duplicate of its own target as a "DNA-fit" feature (collinear). | Persist DNA-only fit distinct from `clip.score`, or rename to `seed_score`; unit test asserting dna_match ≠ composite. |
| SEV2 | clip_engine | `clip_engine/candidates.py:94-113` | Candidate windows never deduped/merged; two adjacent peaks can yield near-identical clips (vs principle #9). | Drop candidates whose `[setup_start_s,end_s]` overlap >50% IoU with a kept one; eval scenario for two close peaks. |
| SEV2 | clip_engine | `routers/clips.py:67` caller | `extract_candidates`/`compute_features` CPU (numpy + scipy find_peaks) runs on the FastAPI loop in the request handler. | Dispatch generation to Celery (202) or wrap CPU in `asyncio.to_thread`. |
| SEV2 | youtube | `youtube/oauth.py:303-313` | Lock-wait re-read hits the identity map (`expire_on_commit=False`) → stale token, waiter exhausts retries → spurious 503 under concurrent refresh. | `session.refresh(row)` / `populate_existing=True` in the loop; test the lock-holder-commits-in-separate-session path. |
| SEV2 | youtube | `youtube/quota.py:51` | Daily quota counter keyed by UTC date but Google resets at midnight **Pacific** → 7–8h window where our counter resets early and we hand out spent budget → hard 403. | Key by `ZoneInfo("America/Los_Angeles")` date; keep 25h TTL; test a UTC-instant-still-yesterday-PT. |
| SEV2 | youtube | `youtube/ingest.py:44-62` | `extract_audio_wav` `subprocess.run` has no `timeout=` (unlike `probe_duration_s`) → wedged ffmpeg ties a worker slot indefinitely. | Add bounded `timeout=` (∝ duration, floor ~600s); map `TimeoutExpired`→RuntimeError for clean Celery retry. |
| SEV2 | youtube | `youtube/analytics.py:51`,`data_api.py:93` | 429 backoff ignores `Retry-After` (COMPLIANCE §4). | `sleep(max(Retry-After, computed_delay))` on 429. |
| SEV2 | youtube | `docs/COMPLIANCE.md:21,47-50` | Analytics retention/refresh **cadence still TBD** (Issue 75b); refresh exists but no max-staleness purge → dead-grant metrics persist indefinitely. | Confirm Google staleness window, record it, add a purge sweep on `fetched_at` age. |
| SEV2 | worker | `worker/tasks.py:547-556` | `poll_clip_outcomes` doesn't `break` on quota exhaustion (unlike analytics refresh) → fires doomed YouTube calls (COMPLIANCE §4). Bounded by 10-day cap, but wasteful. | Catch `QuotaExhaustedError` and `break` (mirror tasks.py:698-704). |
| SEV2 | worker | `worker/tasks.py:357-394` | `_render_clip_async` guard protects serial-after-success but not concurrent: two workers both read `pending`, both encode+upload same key. No corruption, wasted double-encode. | `with_for_update()` on the Clip row + re-check status under lock before flipping to `running`. |
| SEV2 | worker | `worker/tasks.py:222-259` | `_ingest_async` not a clean no-op on redelivery: re-extracts/re-uploads the already-derived WAV. No corruption (billing idempotent), but re-does ffmpeg + R2. | Short-circuit when `source_uri` already points at the derived `audio/*.wav` key, or gate on `ingest_status==done`. |
| SEV2 | dna | `dna/builder.py:223-224,137-161` | `_enrich_video` does 3 serial round-trips × up to 20 videos = ~60 queries/build (N+1 on round-trips). | Batch into 3 `IN (...)` queries grouped in memory by video_id. |
| SEV2 | dna | `dna/builder.py:107-117` | `rank_videos` `select(Video,VideoMetrics)` has no LIMIT → unbounded fetch into worker memory for a power user. | `.order_by(published_at.desc()).limit(DNA_MAX_CANDIDATE_VIDEOS=500)`; add the setting. |
| SEV2 | dna | `dna/builder.py:201-202` | `kind` compared against bare string literals vs `VideoKind` enum value → a rename silently empties buckets and misreports "insufficient data". | Compare against `VideoKind.long.value` via import. |
| SEV2 | routers | `videos.py:40-55`,`clips.py:93-99`,`upload_intel.py:22-25` | Unbounded `list(scalars())` on list endpoints → whole set into memory + one response. | Keyset/offset pagination with a hard cap (100). |
| SEV2 | routers | `videos.py:62,93` | `link_video`/`upload_video` take raw `Form(...)` with no Pydantic request model (id regex-validated, so not a hole). | Wrap in a body model or record the multipart deviation in DECISIONS. |
| SEV2 | _root_infra | `main.py:102-107` | `/metrics` exposed unauthenticated when `METRICS_ENABLED=true` → leaks route templates, traffic, error rates, task names. New surface from Issue 75f. | Bind to an internal-only listener / gate behind token+network policy; document ingress must not route `/metrics` publicly. |
| SEV2 | _root_infra | `observability.py:189-211` | Correlation-id ContextVars are safe only under prefork (one task/process); `--pool=gevent/threads` would clobber across concurrent tasks. New with Issue 75f. | Assert/document the prefork assumption at startup, or key task start off `task.request`. |
| SEV2 | preference | `preference/train.py:116` + `clip_engine/ranking.py:39` | `load_latest` re-deserializes the joblib/LightGBM blob on **every** rerank; no per-(creator,version) cache (the one the brief expected). | Process-local LRU keyed on `(creator_id, version)`; version is the natural invalidation key. (Issue 75 item.) |
| SEV2 | billing | `billing/ledger.py:89-92` | Non-keyed grants (trial/manual, `stripe_session_id=None`) share the broad `except IntegrityError` no-op → a real FK fault is swallowed and mislabeled "race skip session=None"; a legitimate grant silently never credited. | `except IntegrityError: if stripe_session_id is None: raise`; test a non-keyed grant against a missing creator surfaces the error. |
| SEV2 | improvement | `improvement/brief.py:71` | 120s brief is now off-loop (Issue 66 ✅) but still a single 120s blocking request that can exceed an LB timeout and hold a `to_thread` pool thread; limiter caps per-creator not cross-creator concurrency. | Move to a Celery 202/poll job (as `build_dna`). Known-open Issue 75. |
| SEV2 | upload_intel | `upload_intel/timing.py:54-55` | `optimal_gap_hours` did NOT get the 75d bounds/coercion guard `best_upload_windows` got → reads raw `day_of_week`/`hour`; the two functions disagree on a valid row. | Filter+coerce rows first (mirror the windows guard); return None if <2 valid. |
| SEV2 | ingestion | `ingestion/transcribe.py:71-85,99-110` | Hosted-provider normalizers use hard-key indexing (`u["start"]`…) → a payload missing a timestamp raises opaque `KeyError` (burns a retry); WhisperX path already uses `.get`. | Switch hosted normalizers to `.get(..., default)`; skip items lacking timestamps. |

Plus **24 cleanup** items (typing gaps the mypy ratchet will catch, DRY extractions, magic-constant naming, the clip-scorer cache-prefix ordering optimization) — full per-finding detail with `file:line` in `docs/assessment/modules/*.md`.

Module verdicts: **all 11 = NEEDS-WORK** — none clean, **none with an open BLOCKER or cross-tenant leak**.

## Layer 2 — scale checklist (scale-checklist.md)
| Axis | Status | Evidence |
|---|---|---|
| A Pool math | ⚠️ | psycopg3 `prepare_threshold=None` (db.py:18), pool 15+5=20 < 25 sidecar budget, `pool_pre_ping`+`pool_recycle=1800` all verified — **but no Locust-behind-PgBouncer evidence yet** *(was ❌)* |
| B Async loop hygiene | ⚠️ | All sync LLM/upload/transcription/Voyage off the loop (`to_thread`, verified across routers/worker/dna/ingestion/improvement). Residual: in-request candidate CPU on the API loop (clip_engine SEV2) *(was ❌)* |
| C Celery idempotency | ⚠️ | acks_late+reject_on_worker_lost+soft<hard<visibility invariant; generate_clips no-op (no feedback wipe); deduct/grant UNIQUE+SAVEPOINT (concurrent test-backed). Residual: build_dna concurrent double-spend (SEV1), render/ingest concurrent double-work (SEV2) *(was ❌)* |
| D Tenant isolation | ⚠️ | **No leak found in any module** — every creator-scoped query filters `creator_id` from JWT (verified). Still vigilant not structural; RLS open (Issue 56) *(unchanged — good)* |
| E Backpressure | ⚠️ | httpx singleton timeouts + 5xx backoff + Celery time limits + poll bounded by `final` marker. Residual: ffmpeg no timeout, no SDK transcription timeout, 429 Retry-After ignored, poll no quota break *(was ❌)* |
| F Rate limit / quota | ⚠️ | limiter per-creator on real Redis + spend gate (verified). Not load-tested; quota TZ bug (UTC vs PT) *(unchanged)* |
| G Observability | ✅ | request-id ContextVar + ASGI middleware, JSON logs (no PII/token), Prometheus golden signals at `/metrics`, API→Celery correlation (Issue 75f, verified). Caveats: `/metrics` auth + ContextVar-prefork (both SEV2) *(was ⚠️)* |
| H Migration / pgvector | ✅ | HNSW index (0006) + FK indexes verified; `CREATE INDEX CONCURRENTLY` in autocommit block; partial unique on confirmed (0005); `final` marker (0007). Backups/PITR restore-test still unverified *(was ❌)* |
| I Secrets / deletion | ✅ | MultiFernet rotation + decrypt, JWT identity, account-deletion (revoke+purge), Stripe prod fail-fast (75c), `/docs` off in prod, ALLOWED_ORIGINS locked — all verified *(was ⚠️)* |

No axis is ❌. Per the rubric, that is **CONDITIONAL**: no BLOCKER, A–F awaiting load evidence
or carrying scheduled SEV-2 fixes, G–I green by inspection.

## Tally
| Severity | This run | Prev (2026-05-29 pre-hardening) |
|---|---|---|
| BLOCKER | **0** | 1 (+2 product gaps, 4 idempotency SEV1s) |
| SEV1 | 4 | 25 |
| SEV2 | 23 | 39 |
| cleanup | 24 | 34 |

## Diff vs previous report (2026-05-29 pre-hardening, commit `ee59001`)

**Fixed & verified (the entire prior launch-blocking set):**
- BLOCKER psycopg3/PgBouncer → `prepare_threshold=None` (Issue 58, db.py:18).
- Render now cuts from `setup_start_s` (Issue 59); personalization loop wired — retrain task + reranker invoked, maturity-gated (Issue 60).
- `generate_clips` idempotent no-op, no feedback/outcome cascade-wipe (Issue 61); acks_late+reject_on_worker_lost+timeout invariant (Issue 62).
- Blocking calls off both loops: improvement brief, R2 upload, dna brief/embeddings, transcription (Issues 66/67/68).
- Prompt-cache split in both briefs + honestly characterized as a no-op below Sonnet 4.6's 2048-token floor (Issue 69).
- pgvector HNSW + FK indexes (Issue 65); `poll_clip_outcomes` bounded by `final` marker + 10-day cap (Issue 70).
- YouTube shared HTTP client + timeouts + 5xx backoff (Issue 72); preference unpickler lock-guard + advisory-lock version race + schema-drift fallback (Issue 71).
- 14 pip-audit CVEs → 0 (Issue 75a); observability shipped (Issue 75f); Stripe prod fail-fast (75c); timing IndexError→500 in `best_upload_windows` (75d).
- Coverage 69.97% → 70.59%.

**New this run (not in the prior report):**
- clip_engine: `dna_match` seed handed to the preference model as a duplicate feature; no candidate overlap dedup; in-request candidate CPU on the loop.
- youtube: stale identity-map read under lock-contention (503s); UTC-vs-PT quota reset; missing ffmpeg timeout; 429 Retry-After unhonored.
- billing: non-keyed grant swallows a real IntegrityError; dna: N+1 enrichment, unbounded `rank_videos`, `kind` string-literal compare.
- upload_intel: `optimal_gap_hours` left out of the 75d guard (partial-fix gap).

**New surface from the hardening itself (not regressions of prior fixes):**
- `/metrics` exposed unauthenticated and the Celery correlation ContextVar's prefork-only safety — both introduced with Issue 75f observability.

**Carried / known-open (Issue 75 tracking list):** response_model coverage (SEV1), Deepgram whole-file buffer + SDK timeout (SEV1), build_dna concurrent idempotency (now SEV1 — serial case fixed, concurrent remains), improvement 202/poll (SEV2), analytics retention cadence (SEV2, 75b), per-(creator,version) scorer cache (SEV2), mypy→0 (cleanup gate).

**Regressed:** none.

## Top 5 actions, in order
1. **Serialize `build_dna`** — `pg_advisory_xact_lock` in the re-check+INSERT session + partial UNIQUE on `build_job_id`; stops concurrent-redelivery Anthropic+Voyage double-spend (the only SEV1 that costs money).
2. **Close the two ingestion SEV1s** — stream the Deepgram file (kill the OOM vector) and set SDK-level request timeouts on both hosted backends (stop the worker-thread leak the job `wait_for` can't).
3. **`response_model` coverage on the 18 endpoints** — define the `*Out` models; closes API hygiene + response-side leakage in one mechanical pass (Issue 75).
4. **Lock down `/metrics`** (internal listener or token) and sweep the youtube SEV2s — ffmpeg `timeout=`, PT-keyed quota, `populate_existing` on the oauth re-read, honor 429 `Retry-After`.
5. **Run `tests/perf/` (Locust) behind PgBouncer** at target concurrency — the one thing reading cannot settle; converts axes A/B/C/E ⚠️ → ✅ (or surfaces the real ceiling).

## Next-run instructions
- Findings map to the Issue 75 tracking list + a few new SEV2s; re-run `/assess` after each batch — the `history/` snapshot makes the next report a diff, not a re-read.
- The deterministic floor is green and ratcheted; the gap to **YES** is now load evidence + the SEV1 tail, not foundations.
