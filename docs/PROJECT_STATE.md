# CreatorClip — Project State

Updated after every issue closes.

---

## Current Status

**Active issue**: Phase 2.6 — Production-assessment fixes. 58 code-complete (staging Locust verify pending); 59–72 ✅ done; 73 partial (input validation done), 74 ✅ done, 75 tracking (item (a) CVEs now done; (c)/(d) done). **Assessment-driven SEV-0/SEV-1 work is complete.** Remaining: Issue 75 tracked follow-ups (analytics-retention compliance, full response_models, observability, mypy→0, starlette-1.x migration) + the staging Locust run for 58.
**Last completed**: Issue 75 — improvement brief converted to 202 + poll (Celery job + Redis status), killing the 120s synchronous request / Cloudflare 524.
**Blocked**: _(none)_ — remaining Tier-1 items (run the perf harness, prod deploy verify, OAuth verification) need a Docker host / prod access / Google Console, not code.

> **Closed Issue 75 — improvement-brief 202/poll** (2026-05-29): the brief was a synchronous GET
> blocking ~120s on Anthropic+web_search → Cloudflare 524 in prod. Now: POST enqueues a Celery task
> (debounced; 400 fast-fail on no channel/data) → 202; GET polls Redis-backed status
> (improvement/jobs.py, keyed by creator_id → isolation by construction); worker task builds analytics
> + runs the LLM off-loop (asyncio.to_thread), writes done/failed (no retry → no surprise double LLM
> spend). Frontend insights.html POST-then-poll (3s, 180s deadline). The Issue-33 SEV-0 scoping moved
> intact into the worker task; isolation tests repointed there. No migration (ephemeral data → Redis).
> +3 DB-free router tests; default suite 421 passed; gates ruff 0 / mypy 30 / bandit 0,0 / pip_audit 0.
> DECISIONS 2026-05-29.

> **Tier-1 PgBouncer load harness** (2026-05-29): turned tests/perf/ into a one-command BLOCKER
> verifier — docker-compose.perf.yml (Postgres + PgBouncer POOL_MODE=transaction + Redis + app with
> DATABASE_URL→pgbouncer:6432), seed.py (idempotent fixed-UUID creator with videos/clips/DNA/pgvector/
> activity/balance), run.sh (build → migrate direct-to-Postgres → seed → Locust headless → grep app
> logs for `prepared statement … does not exist`, exit non-zero on regression). Proves the
> prepare_threshold=None fix (Issue 58) under real transaction pooling — the one failure mode CI
> can't see. Validated statically (compose config valid, bash -n, seed imports resolve, ruff clean);
> not run in-sandbox (no registry egress) — run on staging/a Docker host. DECISIONS 2026-05-29.

> **Tier-1 pre-beta launch readiness** (2026-05-29): routed the existing legal pages at clean
> URLs (main.py /privacy → privacy.html, /terms → tos.html); added the Google-mandated Limited Use
> affirmative disclosure to privacy.html + a homepage footer linking both pages (Google requires the
> privacy policy be discoverable from home — prerequisite for OAuth verification). Added a CORS
> production fail-fast (config.py _lock_prod_cors: ENV=production rejects empty/`*`/localhost/http
> ALLOWED_ORIGINS). Added scripts/verify_deploy.sh — turnkey check of /health, /privacy, /terms,
> /metrics, /docs=404, and alembic current == head (a7b8c9d0e1f2) over SSH; fully parameterized.
> Key reframe: a closed beta needs NO full OAuth verification — Google's Testing mode allows ≤100
> test users with unverified sensitive scopes; verification is a public-launch gate. Legal text
> stays draft-pending-review (banner kept). +8 DB-free tests; **418 passed, 1 skipped, 55 deselected**;
> gates ruff 0 / mypy 30 / bandit 0,0 / pip_audit 0. Detail in DECISIONS (2026-05-29).

> **Closed Issue 75(f) — observability** (2026-05-29): new observability.py — a pure-ASGI
> RequestIDMiddleware (reads/mints X-Request-ID into a ContextVar, echoes it on the response;
> added outermost in main.py); JSON structured logs via JsonLogFormatter + RequestIDLogFilter
> (request_id on every line; configure_logging replaces basicConfig, idempotent, text fallback
> for dev); Prometheus golden signals (http_request_duration_seconds labelled by route template;
> celery_task_duration_seconds + celery_tasks_total) at /metrics gated by METRICS_ENABLED. The
> correlation id propagates API→Celery via before_task_publish/task_prerun/task_postrun signals
> (weak=False — Celery connects weakly by default). Added prometheus-client==0.25.0 (single CVE-clean
> dep; the correlation layer is hand-rolled to add zero new surface). Config: LOG_JSON,
> REQUEST_ID_HEADER, METRICS_ENABLED (+ .env.example). Deferred: OpenTelemetry distributed tracing.
> +9 DB-free tests; **410 passed, 1 skipped, 55 deselected**; gates ruff 0 / mypy 30 / bandit 0,0 /
> pip_audit 0. Rationale + sources in DECISIONS (2026-05-29).

> **Closed Issue 75(a) — pip-audit CVE remediation** (2026-05-29): 14 known vulns → 0.
> Patched 6 packages in requirements.txt: cryptography 43.0.3→46.0.7, python-multipart
> 0.0.20→0.0.27, PyJWT 2.9.0→2.12.0, lightgbm 4.5.0→4.6.0, python-dotenv 1.0.1→1.2.2,
> starlette 0.41.3→0.49.1 (forced FastAPI 0.115.4→0.120.4, smallest bump whose pin admits
> starlette 0.49.1). The disputed PyJWT PYSEC-2025-183 dropped off (2.12.0 out of its
> affected range). 2 residuals accepted-risk in run_layer0.py's PIP_AUDIT_IGNORES allowlist:
> pytest GHSA-6w46-j5rx-g56g (dev-only; pytest-asyncio caps pytest<9 — a test-stack cascade)
> and starlette PYSEC-2026-161 (Host header, fixable only on the starlette-1.x line / FastAPI
> 0.136.x). baselines.json pip_audit_vulns ratcheted 14→0. Verification: pip check clean;
> **401 passed, 1 skipped, 55 deselected** on bumped deps; run_layer0 gates ruff 0 / mypy 30 /
> bandit 0/0 / pip_audit 0. Justification + version evidence in DECISIONS (2026-05-29).
> Follow-up: starlette-1.x migration to close PYSEC-2026-161 (tracked in issues.md).

> **Closed Batch 8 / Issues 73(partial) + 74 + 75(partial)** (2026-05-29): Memory: librosa
> loads at sr=16000 (~3x less RAM) + WhisperX/SDK-client singletons. Security: youtube_video_id
> validated (^[A-Za-z0-9_-]{11}$ -> 422) before reaching a storage key. Robustness: Stripe
> prod fail-fast config validator; upload_intel skips out-of-range rows instead of 500.
> Deferred to Issue 75 tracking (with rationale in DECISIONS): full response_model coverage,
> Deepgram file-stream, 14 CVEs, analytics-retention cadence, observability, mypy->0, clip-scorer
> caching, scorer cache, brief 202/poll. DB-free unit tests for all four hardening items; updated
> 3 upload-streaming tests to valid 11-char IDs. Test count: **401 passed, 1 skipped, 55 deselected**
> (+4). Gates: ruff 0, mypy 30, bandit 0/0, coverage 70.45%.

> **Closed Issue 71** (2026-05-29, Batch 7): from_bytes monkeypatched a joblib global
> (not thread-safe -> RCE allowlist defeatable under concurrent loads); build_and_save
> max()+1 raced to IntegrityError; predict_score swallowed errors into 0.5. Fix: module
> threading.Lock around the swap (direct unpickler rejected -- joblib signature is
> version-fragile, see DECISIONS); pg_advisory_xact_lock(hashtext(creator_id)) for the
> version assignment; predict_score validates n_features_in_ and raises; load_latest
> returns None on feature-schema drift; rerank scores-then-mutates and falls back to DNA
> on scorer error. DB-free unit tests + fixed an existing mock-session test for the extra
> advisory execute. Test count: **397 passed, 1 skipped, 55 deselected** (+2). Gates: ruff 0, mypy 30, bandit 0/0, coverage 70.47%.

> **Closed Issue 70** (2026-05-29, Batch 6): poll_clip_outcomes re-polled every published
> clip every 7 days forever (no terminal guard) -> unbounded YouTube-quota drain. Added
> `clip_outcomes.final` (migration 0007) + partial index; the 7d checkpoint sets final and
> the query excludes final rows + caps candidates to clips created within 10 days; commit
> per creator. Integration test: 7d poll marks final, finalized outcome skipped. Test count:
> **395 passed, 1 skipped, 55 deselected** (+1 integration). Gates: ruff 0, mypy 30, bandit 0/0, coverage 70.38%.

> **Closed Issue 69** (2026-05-29, Batch 5): Both briefs interpolated per-creator
> data into the cached system block (prefix changed every call); improvement returned
> the web_search preamble instead of the answer. Split system into static-cached +
> volatile-uncached blocks; return `text_blocks[-1]`. `/claude-api` finding: Sonnet
> 4.6's min cacheable prefix is 2048 tokens and these static prefixes are ~400 — so
> caching can't engage for these low-frequency calls regardless; the split is
> correct-structure, and the real caching win (clip scorer's reused per-creator
> prefix) is tracked under Issue 75. DB-free unit tests for the split + final-block
> extraction; updated the existing 1-block test to the 2-block contract. Test count:
> **395 passed, 1 skipped, 54 deselected** (+4). Gates: ruff 0, mypy 30, bandit 0/0,
> coverage 70.47%.

> **Closed Issue 72** (2026-05-29, Batch 4b): Per-call `httpx.AsyncClient()` with no
> timeout on the token-refresh hot path; client built inside the retry loop in
> data_api/analytics. New `youtube/_http.py` lazy per-process singleton
> (`Timeout(15, connect=5)`) + `aclose()` reused everywhere and closed on API/worker
> shutdown; 5xx now backs off + retries. Rebased the oauth-lifecycle tests onto the
> `_http.client` boundary (they'd mocked the old per-call httpx). Test count: **392
> passed, 1 skipped, 54 deselected** (+2). Gates: ruff 0, mypy 30, bandit 0/0,
> coverage 70.49%.

> **Closed Issue 68** (2026-05-29, Batch 4b): Sync `generate_brief`, Voyage `_embed`
> (tenacity sleeping on the loop), `transcribe_audio`, and `extract_audio_events` ran
> on the worker's singleton loop with no transcription upper bound. All offloaded via
> `asyncio.to_thread`; transcription wrapped in `asyncio.wait_for(..., timeout=
> TRANSCRIPTION_TIMEOUT_S=300)` for a job-level bound. SDK-native timeouts deferred to
> Issue 75 (SDKs not installed to verify). DB-free unit test for the Voyage offload;
> existing pipeline tests confirm behavior-preservation. Test count: **390 passed, 1
> skipped, 54 deselected** (+2). Gates: ruff 0, mypy 30, bandit 0/0, coverage 70.32%.

> **Closed Batch 4a / Issues 66 + 67** (2026-05-29): Three synchronous calls ran on
> the API event loop (120s improvement brief, large-file upload, account-deletion
> purge), stalling every concurrent request on the worker (axis B). All three moved
> to `await asyncio.to_thread(...)`. The brief's 120s request duration (vs LB timeout)
> is tracked for a Celery 202/poll follow-up under Issue 75. Integration tests assert
> each call is offloaded. Test count: **388 passed, 1 skipped, 54 deselected** (+2
> integration). Gates: ruff 0, mypy 30, bandit 0/0, coverage 69.57%.

> **Closed Batch 3 / Issue 65** (2026-05-29): pgvector HNSW (`vector_cosine_ops`,
> m=16/ef_construction=200) on `dna_embeddings.embedding` matching the `<=>` query,
> plus `ix_clip_feedback_creator_id`; both `CREATE INDEX CONCURRENTLY` in an
> alembic autocommit_block (migration 0006). Reading the schema corrected two
> assessment items already covered (dna_embeddings.creator_id btree from 0001;
> preference_models.creator_id via the (creator_id,version) unique index) — no
> redundant indexes added. Integration test introspects `pg_indexes`. Migration-only,
> so the unit-coverage floor holds. Test count: **388 passed, 1 skipped, 52 deselected**
> (+2 integration). Gates: ruff 0, mypy 30, bandit 0/0, coverage 69.54%.

> **Closed Batch 2 / Issues 63 + 64** (2026-05-29): Idempotent unique-keyed writes.
> 63: `build_dna` stamps the Celery `task_id` as `creator_dna.build_job_id` and
> `_build_dna_async` early-returns before the paid LLM/Voyage calls on redelivery;
> `confirm_draft` locks `with_for_update()` + partial unique index
> `uq_one_confirmed_dna_per_creator` (ordered flush, non-deferrable). 64:
> `grant_minutes` now mirrors `deduct_for_video` (fast-path + SAVEPOINT +
> IntegrityError) so duplicate Stripe deliveries credit once. Migration `0005`.
> Integration tests for both. **Coverage floor moved 69.97→69.54%** (justified:
> DB-only idempotency code is integration-tested, not visible to the unit-coverage
> gate — see DECISIONS). Test count: **388 passed, 1 skipped, 50 deselected** (+3
> integration; updated 1 mocked unit test). Gates: ruff 0, mypy 30, bandit 0/0,
> coverage 69.54%.

> **Closed Batch 1 / Issues 61 + 62** (2026-05-29): Celery is at-least-once. A
> redelivered `build_signals`→`generate_clips` wiped feedback/outcomes via
> cascade-delete (data loss; corrupted the Issue-60 training signal), `acks_late`
> without `reject_on_worker_lost` dropped OOM-killed jobs, and no time limit meant a
> long task redelivered while still running. Fix: `generate_and_rank_clips`
> early-returns existing clips (idempotent, never cascade-wipes); added
> `task_reject_on_worker_lost` + the `soft(3000)<hard(3300)<visibility(3600)`
> invariant; `_render_clip_async` skips when already done. DB-free config-invariant
> test + integration tests (feedback survives re-gen; render skips when done).
> Test count: **388 passed, 1 skipped, 47 deselected** (+3 unit, +2 integration).
> Gates: ruff 0, mypy 30, bandit 0/0, coverage 70.02%.

> **Closed Issue 60** (2026-05-29): Personalization was dead code — `build_and_save`
> had no caller and `rerank_with_preference` was never invoked, so ranking was
> DNA-only (the North-Star "learns your style" loop never ran). Fix: idempotent,
> self-debouncing `retrain_preference` Celery task enqueued from the feedback
> endpoint; `rerank_with_preference` now called at the end of `generate_and_rank_clips`;
> flat 50/50 blend replaced with `preference_weight(label_count)` — 0 below
> PERSONALIZATION_THRESHOLD_LABELS (honest DNA fallback), ramping to
> PREFERENCE_WEIGHT_CAP by 2× the threshold (hybrid cold-start standard). Version-race
> + unpickler thread-safety deferred to Issue 71 (retrain catches IntegrityError
> meanwhile). DB-free unit tests (weight curve + rerank gating) + integration test
> (trains v1 then self-debounces). Test count: **385 passed, 1 skipped, 45 deselected**
> (+6 unit, +1 integration). Gates: ruff 0, mypy 30, bandit 0/0, coverage 70.18%.

> **Closed Issue 59** (2026-05-29): The render cut from `clip.start_s` (fixed
> peak−75s) while scoring/API/eval all key on `setup_start_s` → delivered Shorts
> didn't clip the setup. Fix: render via `_render_start_for(clip)` (pure helper,
> coalesces to `start_s` only when nullable `setup_start_s` is unset); set
> `-accurate_seek` explicitly. The assessment's "GOP drift" SEV-2 was a false
> positive — re-encode pipelines accurate-seek by default (DECISIONS). DB-free unit
> guards + an integration test that the persisted setup_start_s reaches the render.
> Test count: **379 passed, 1 skipped, 44 deselected** (+3 unit, +1 integration).
> Gates: ruff 0, mypy 30, bandit 0/0, coverage 70.06%.

> **Production assessment run** (2026-05-29): `/assess` across all 11 modules →
> verdict **PRODUCTION-READY = NO**. 1 BLOCKER, 25 SEV-1, 39 SEV-2, 34 cleanup;
> no cross-tenant leak, bandit 0/0. Findings tracked as Issues 58–75; full register
> in `docs/assessment/`. Also shipped the repeatable harness (`/assess` skill +
> ratcheted CI gates in `quality.yml` + baselines), the `best-practices` skill +
> freshness convention (`docs/SKILL_FRESHNESS.md`), and SSOT model-id config.

> **Closed Issue 58** (2026-05-29): psycopg3 prepared statements are incompatible
> with PgBouncer transaction-pooling mode (the production pooler) → would throw
> `prepared statement "_pg3_…" does not exist`; CI never caught it (direct
> Postgres). Fix: `connect_args={"prepare_threshold": None}`; pool ceiling cut
> 30→20/pod to stay under the 25-conn sidecar; `pool_recycle=1800`. Connection-
> budget inequality recorded in DEPLOYMENT.md; engine config guarded by
> `tests/test_db_engine_config.py`. Load-proof behind real PgBouncer deferred to
> staging Locust. Test count: **376 passed, 1 skipped** (+3). Gates: ruff 0, mypy 30,
> bandit 0/0, coverage 70.03%.

> **Closed Issue 47** (2026-05-28): Beat-job fairness on quota exhaustion. Old refresh
> task did `select(Creator)` with no ORDER BY and `break` on `QuotaExhaustedError` —
> next day's run started the same scan in the same heap order, so creators past the
> daily cutoff index never refreshed (SEV-2 starvation). Fix: added nullable
> `creators.last_analytics_refreshed_at` + `ix_creators_refresh_order` index;
> `ORDER BY last_analytics_refreshed_at NULLS FIRST, id` so newly-connected creators
> jump the queue and yesterday's starved creators go first today. Stamp set inside
> the successful inner try (commits with analytics writes); rollback on
> `QuotaExhaustedError` un-stamps by design, keeping the starved creator at the
> front. No backfill — NULL = "never refreshed" puts existing rows at the head on
> day 1, self-bootstrapping. Bundled into alembic `0004_video_done_creator_refreshed`
> per LEFT_OFF's explicit suggestion (one deploy step for both Issue 43 + 47 schema).
> Filter contract pinned via select-statement inspection (`order_by` clauses); stamp
> + no-stamp idempotency pinned via two unit tests; real-DB 5×3-cycle scenario in
> `tests/test_analytics_fairness_integration.py` (marker: `integration`).
> Test count: **373 passed, 1 skipped, 43 deselected** (+3 unit, +1 integration).

> **Closed Issue 43** (2026-05-28): Source-media purge correctness. Old filter was
> `Video.created_at < cutoff` — a stuck/in-progress ingest of an old upload would have
> its `source_uri` nulled mid-pipeline (SEV-1). Fix: added `videos.ingest_done_at`
> (nullable timestamptz) stamped exactly once in `_signals_async` under a
> `if video.ingest_done_at is None:` guard (Celery is at-least-once — retries must NOT
> refresh the stamp); swapped the purge filter to gate on
> `ingest_done_at IS NOT NULL AND ingest_done_at < cutoff`. Migration backfills
> existing `done` rows with `created_at` so already-completed videos keep their
> pre-migration retention window. Added partial index
> `ix_videos_purge_candidates ON videos(ingest_done_at) WHERE
> ingest_done_at IS NOT NULL AND source_uri IS NOT NULL` for cheap hourly sweeps.
> Filter contract pinned via SQL-whereclause inspection test;
> stamp idempotency pinned via two unit tests; real-DB three-row scenario in
> `tests/test_purge_integration.py` (marker: `integration`). `docs/COMPLIANCE.md`
> retention-clock row updated.
> Test count: **370 passed, 1 skipped, 42 deselected** (+3 unit, +1 integration).

> **Closed Issue 39** (2026-05-28 — Batch 3 kickoff): Celery event-loop strategy.
> Every task previously called `asyncio.run(...)`, creating a fresh loop per
> invocation and rebinding the SQLAlchemy async engine pool to whichever loop
> touched it first — the textbook cause of "Future attached to a different loop"
> + pool churn under concurrency. Fix: per-worker singleton `asyncio` loop installed
> by the `worker_process_init` Celery signal, and the engine rebound to that loop
> via new `db.recreate_engine()` (uses `engine.sync_engine.dispose(close=False)`
> to abandon inherited parent connections without yanking parent FDs). All 11 task
> bodies in `worker/tasks.py` now route through `worker.celery_app.run_async(coro)`.
> Switched `worker/tasks.py` from `from db import AsyncSessionLocal` to `import db`
> + `db.AsyncSessionLocal(...)` so the rebound sessionmaker is picked up at call time.
> Test count: **367 passed, 1 skipped, 41 deselected** (+5 new event-loop tests).
> Adjusted patch targets in `test_retention_tasks.py` / `test_pipeline_trigger.py` /
> `test_oauth_lifecycle.py` to match the new import surface.

> **Closed Batch 2** (2026-05-28 PM): Three TEST-ONLY issues via parallel agents.
>
> - **Issue 49**: 4 integration tests for the billing money paths (concurrent deduct
>   race, webhook idempotency same session_id, unknown pack_id, missing metadata).
>   Finding: webhook returns 200 `{"status": "ignored"}` for anomalies, NOT 4xx — this
>   is the correct Stripe pattern (2xx prevents retry storms; anomalies logged internally).
>   Tests document and assert the actual behavior.
> - **Issue 51**: 4 new tests appended to `tests/test_oauth_lifecycle.py` (now 15 total):
>   refresh-path success, callback caplog no-plaintext, authorization URL exact scopes
>   (no `youtube.upload`), `prompt=consent` + `access_type=offline` round-trip.
> - **Issue 55**: 9 surgical load-bearing tests across 8 existing files + 1 adversarial
>   YAML scenario (`loud_aftermath.yaml`).
>
> One merge-flow defect caught during Batch 2: Issue 51's new
> `test_callback_logs_no_token_plaintext` drives the full callback success path, which
> sets a `cc_session` JWT cookie on the session-scoped TestClient cookie jar — leaking
> auth into subsequent tests and causing `test_static::test_list_videos_requires_auth`
> to hit real Postgres. Fix: clear `client.cookies` in the finally block and `pop` only
> the dependency override this test set instead of `.clear()` (the project convention).
>
> Test count: **362 passed, 1 skipped, 41 deselected** (was 349; +13 unit / +4 integration).

> **Closed Batch 1** (2026-05-28 PM): Six issues landed via parallel agents in
> isolated worktrees, merged serially into main with full suite green after each merge.
>
> - **Issue 37** (SEV-1, SDK timeouts): module-level singletons for Anthropic / Stripe /
>   Voyage / boto3 with timeout + retry config. Anthropic 60s/2-retry, 120s override for
>   improvement_brief web_search path. Stripe `max_network_retries=3`. Voyage `timeout=30`
>   wrapped in tenacity (3 attempts, exp backoff). boto3 adaptive retry, max_attempts=5,
>   connect/read 10/60. Added `tenacity==9.1.4` to requirements.
> - **Issue 45** (SEV-2, refresh race + Redis pool): per-creator `SET NX EX 10` lock around
>   the Google refresh branch with canonical Lua compare-and-delete release. Module-level
>   `redis.asyncio.Redis` singleton in new `youtube/_redis.py` shared by oauth + quota.
> - **Issue 48** (TESTS): 14 new integration tests covering every protected route — zero
>   SEV-0 isolation findings (all routes correctly enforce per-creator filtering).
> - **Issue 50** (TESTS): 4 integration tests verifying cascade across all 17 dependent
>   tables; no missed FK cascades.
> - **Issue 53** (TESTS): renamed misnomered `test_compliance.py` → `test_retention_tasks.py`;
>   new `test_compliance_no_virality.py` with 3 structural scans (OpenAPI bodies, static
>   assets, schema descriptions). Codebase clean — no forbidden phrases.
> - **Issue 54** (TESTS): 3 integration tests for `scripts/rotate_token_key.py` —
>   happy-path full re-encrypt, corrupt-row rollback, caplog no-plaintext.
>
> Test count: **349 passed, 1 skipped, 37 deselected** (was 335 + 16 deselected;
> +14 unit / +21 integration). See `docs/DECISIONS.md` 2026-05-28 entries for Issues 37, 45.

> **Closed Issue 36** (2026-05-28): Three lifecycle gaps closed in one commit.
> (a) `DELETE /auth/me` now revokes the **refresh** token at
> `oauth2.googleapis.com/revoke` and tolerates 400 `invalid_token` / `token_revoked` as
> success — completes the right-to-erasure path. (b) `get_valid_access_token` now deletes
> the `YoutubeToken` row + commits on Google `invalid_grant` (RFC 6749 §5.2 permanent
> error), so subsequent refresh attempts immediately surface the existing
> "No OAuth tokens found — please reconnect" 401 instead of looping. (c) New
> `youtube/errors.py` (`YouTubeAuthError` + `PERMANENT_403_REASONS` / `TRANSIENT_403_REASONS`
> sets); `_get_json` and `_fetch_report` share a `_classify_error()` helper that retries
> transient 403/429 with exponential backoff and raises `YouTubeAuthError` on permanent
> 401 / 403 reasons (authError, forbidden, accountClosed, accountSuspended, channelClosed,
> ...). `worker/tasks.py::_refresh_youtube_analytics_async` catches `YouTubeAuthError`,
> deletes the offending `YoutubeToken` row, commits, and continues — eliminates the
> hourly-wasted-quota loop against revoked creators. "Mark creator disconnected" is
> represented as token-row absence (no `OnboardingState` enum change, no migration).
> 9 new tests in `tests/test_oauth_lifecycle.py`. Test count: **335 passed, 1 skipped,
> 16 deselected** (was 326; +9 new). See `docs/DECISIONS.md` 2026-05-28 Issue 36 entry.

> **Closed Issue 41**: `preference/model.py:35–40` used `pickle.dumps(self)` / `pickle.loads(data)`
> for `PreferenceScorer.to_bytes` / `from_bytes`.  Any future write to `preference_models.weights_blob`
> (SQL injection, admin import, a bug) would become RCE in the worker process on the next ranking pass.
> Replaced with **joblib** (sklearn's documented serialiser; already a transitive dep) backed by
> `_RestrictedUnpickler` — a subclass of `joblib.numpy_pickle.NumpyUnpickler` that overrides
> `find_class` with a hardcoded allowlist of 10 `(module, name)` pairs.  `from_bytes` temporarily
> patches `joblib.numpy_pickle.NumpyUnpickler` with the restricted class for the duration of the
> `joblib.load` call, then restores the original (no global state left behind).  No schema change —
> `weights_blob` column stays `bytes`.  4 new tests in `tests/test_preference.py`: round-trip
> (predictions identical), label_count preserved, `os.system` gadget rejected, `subprocess.Popen`
> gadget rejected.  Test count delta: +3 net (renamed 1 existing test, added 4, kept all others green).
> See `docs/DECISIONS.md` 2026-05-28 Issue 41 entry.
>
> **Closed Issue 42**: `clip_engine/render.py` had three `subprocess.run` calls with no
> `timeout=`. A stalled or corrupt source video would block the Celery worker indefinitely.
> Fixed: `_run` now accepts `timeout_s: float = 120.0` and catches `subprocess.TimeoutExpired`,
> re-raising as `RuntimeError(f"ffmpeg {label} timed out after {timeout_s}s")`. `_frame_dimensions`
> hardcodes `timeout=30` directly (ffprobe reads only the container header). `render_clip_file`
> computes `render_timeout_s = max(120.0, duration * 4)` and passes it to both the keyframe
> extraction and the final render `_run` call. 3 new tests in `tests/test_render.py` assert
> each timeout path raises the correct `RuntimeError` without any real sleeping (all using
> `subprocess.TimeoutExpired` side-effects). Test count: 311 passed + 3 new = 314 expected
> (test env currently broken by a langsmith/pydantic-core version conflict introduced between
> sessions — see environment note below). See `docs/DECISIONS.md` 2026-05-28 Issue 42 entry.
>
> **ENVIRONMENT NOTE (2026-05-28)**: `python3.12 -m pytest -q` now fails at plugin-loading
> time with `SystemError: pydantic-core 2.27.2 incompatible with pydantic requiring 2.46.4`.
> Cause: langsmith installed a newer pydantic (2.46.4) into the uv-managed Python at
> `~/.local/share/uv/python/cpython-3.12.7/` while the user site at `~/.local/lib/python3.12/`
> still has pydantic-core 2.27.2. The fix is: `python3.12 -m pip install --user --break-system-packages
> "pydantic-core>=2.46.4"` OR use the project venv at `.venv/bin/pytest`. This is an environment
> issue, not a code issue.
>
> **2026-05-28 session note**: Ran a full project audit before resuming work. Discovered 24
> hardening + coverage findings (4 SEV-0, 12 SEV-1, 3 SEV-2, 8 test-coverage), filed as
> Issues 32–55 in `docs/issues.md` under **Phase 2: Hardening & Test Coverage**.
> **Closed Issue 32**: `starlette` had drifted to 1.1.0 (a major-version upstream released
> 2026-05-23 under the new `Kludex/starlette` maintainership) and `pytest` could not even
> collect — the previously-claimed "313 tests pass" was stale. Pinned `starlette==0.41.3`
> explicitly in `requirements.txt` (inside FastAPI 0.115.x's `<0.42.0,>=0.40.0` constraint),
> re-installed via a project venv, and confirmed **313 passed, 7 deselected** (the 7 are
> integration-marked). See `docs/DECISIONS.md` 2026-05-28 entry.
> **Closed Issue 33**: `routers/improvement.py` was sending other creators' analytics
> averages to Claude for every requesting creator (`select(VideoMetrics).limit(50)` with no
> `creator_id` filter — SEV-0 isolation leak). Fixed via the always-filter idiom already
> used elsewhere (`.join(Video).where(Video.creator_id == creator.id)`) plus an
> `ORDER BY fetched_at DESC` for determinism, plus a zero-data 400 short-circuit so
> brand-new creators don't get a hallucinated brief. New integration test
> `tests/test_improvement_isolation.py` seeds two creators with disjoint metrics and asserts
> only the requesting creator's data reaches the LLM. Filed **Issue 56** (Postgres RLS
> evaluation) as defense-in-depth follow-up. See `docs/COMPLIANCE.md` "Findings & Fixes
> Log" 2026-05-28 entry.
> **Closed Issue 34**: `worker/tasks.py:189` called `deduct_minutes` with no per-video
> idempotency key. With Celery's `task_acks_late=True`, a worker-crash-between-commit-and-ack
> would re-deliver the ingest task and re-decrement the balance (up to 4× per video).
> Replaced with a new `MinuteDeduction` ledger table (symmetric to `MinutePack` grants),
> `UNIQUE(video_id)` as the idempotency key, and `deduct_for_video` using SAVEPOINT
> (`session.begin_nested`) to atomically INSERT the ledger row + decrement balance. New
> migration `0003_minute_deductions.py`. 4 real-Postgres integration tests in
> `tests/test_billing_idempotency.py` cover sequential retry, two-coroutine concurrent
> race, 402-leaves-ledger-clean, and audit fields. Test count: **311 passed, 13
> deselected** (net 0 — removed 2 mocked unit tests, added 4 integration tests). Filed
> **Issue 57** (refund-on-terminal-failure) as product follow-up. See `docs/DECISIONS.md`
> 2026-05-28 Issue 34 entry.
>
> **2026-05-28 session note (Issue 40)**: Replaced `await file.read(max_bytes + 1)` bulk-read
> (SEV-1: up to 500 MB into heap per request) with a 1 MB streaming chunk loop. Temp file is
> always unlinked on the 413 rejection path via `except HTTPException`. 3 new tests in
> `tests/test_videos_upload_streaming.py`: 413 on oversize, tempfile cleanup verified, RSS delta
> asserted < 20 MB for a 100 MB rejected upload. Test count: **314 passed** (net +3).
> See `docs/DECISIONS.md` 2026-05-28 Issue 40 entry.

> **2026-05-28 session note**: Completed Issue 44 (auth boundary hardening). Three security
> fixes: (1) `auth.py` `get_current_creator` now catches `ValueError`/`KeyError` alongside
> `PyJWTError` so a malformed JWT `sub` returns 401 instead of 500; (2) `DELETE /auth/me` rate-
> limited to 5/hour via the existing slowapi limiter; (3) `crypto.py` rewritten to use
> `MultiFernet` for zero-downtime key rotation + typed `TokenDecryptError`. Added
> `TOKEN_ENCRYPTION_KEY_PREVIOUS` optional setting. Test count delta: +8 tests (2 in
> `test_auth.py`, 6 in `test_crypto.py` replacing 1 old test). All existing tests updated for
> the new rate-limit requirement on `DELETE /me`.

> **2026-05-27 session note**: Built the operability kit (Issue 31). Found and fixed a
> **blocking pre-existing bug** — `routers/clips.py` imported the deleted `billing.tiers`, so
> `import main` failed and the app could not start (likely a real cause of failed/timed-out
> deploys). Fixed to the minute-packs `check_positive_balance` guard. Full suite now `313 passed`.
> Note: CI lint (`ruff check .`) has ~11 pre-existing violations unrelated to this work — flagged,
> not swept in. The local unprovisioned `.env` is missing most required vars (dev only).

> **2026-05-28 session note**: Fixed SEV-0 Issue 35 — idempotent DNA build. `create_draft`,
> `embed_patterns`, `embed_brief` all gained `commit=False` path; `_build_dna_async` now
> issues a single atomic commit. 3 integration tests added in `tests/test_dna_build_idempotency.py`
> (marked `integration`; excluded from default `pytest -q` run per pytest.ini). Non-integration
> suite count unchanged at `313 passed`.

---

## Issue Progress

| # | Title | Phase | Status | Notes |
|---|-------|-------|--------|-------|
| 1 | Repo scaffold + Docker Compose + health endpoint | Core | ✅ Done | All acceptance criteria met; tests pass |
| 2 | Postgres schema + Alembic + pgvector | Core | ✅ Done | All tables, enums, pgvector; alembic upgrade head verified against live DB |
| 3 | Google/YouTube OAuth + creator session | Core | ✅ Done | OAuth flow, JWT session, token refresh, get_current_creator |
| 4 | YouTube data fetch — metrics, retention, activity | Core | ✅ Done | data_api.py, analytics.py, routers/creators.py; Deepgram default logged |
| 5 | Ingestion pipeline — source + transcript + signals | Core | ✅ Done | Celery chain; Deepgram/WhisperX/AssemblyAI; audio events; unified timeline |
| 6 | Creator DNA builder + brief (Research Mode) | Core | ✅ Done | dna/builder+brief+profile+embeddings; build_dna task; /creators/me/dna endpoints; 99 tests pass |
| 7 | Clip engine — candidates with backward setup-finding | Core | ✅ Done | window.py, candidates.py; 20 tests + 2 eval YAML fixtures pass |
| 8 | Clip scoring + DNA-weighted ranking | Core | ✅ Done | scoring.py, ranking.py, routers/clips.py; 18 tests pass |
| 9 | Render — 9:16 cut + active-speaker reframe | Core | ✅ Done | render.py (ffmpeg+OpenCV), render_clip task, /clips/{id}/render endpoint; 10 tests pass |
| 10 | Review UI + feedback capture | Core | ✅ Done | routers/review.py, static/review.html+onboarding.html+profile.html; HTMX; 7 tests pass |
| 11 | Preference model — recency-decayed reranker | Core | ✅ Done | decay.py, features.py, model.py, train.py; rerank_with_preference; 19 tests pass |
| 12 | Upload intelligence + improvement brief | Core | ✅ Done | timing.py, brief.py (Claude+web_search), routers; 13 tests pass |
| 13 | Clip outcomes loop (strongest signal) | Core | ✅ Done | poll_clip_outcomes Beat task (48h+7d), performed_well, get_video_stats; 13 tests pass |
| 14 | Dashboard + static pages scaffold | Core | ✅ Done | index.html, insights.html, tos.html, privacy.html; StaticFiles mount + GET /; 12 tests pass |
| 15 | Connected user flow + auth guard | Core | ✅ Done | auth.js guard + auth:ready event; nav on all pages; review/profile/onboarding wired; 18 tests pass |
| 16 | Auto-trigger clip generation + status polling | Core | ✅ Done | generate_clips task; build_signals chains it; setInterval polling; /videos/{id}/status; 7 tests pass |
| 17 | Source media purge + YouTube analytics refresh | Core | ✅ Done | purge_stale_source_media + refresh_youtube_analytics Beat tasks; datetime fix; 13 tests pass |
| 18 | Per-creator rate limiting | Core | ✅ Done | slowapi + Redis; creator_id key from JWT; 10/h LLM, 20/h render, 120/min rest; 11 tests pass |
| 19 | Account deletion (right-to-erasure) | Core | ✅ Done | DELETE /creators/me; OAuth revoke; storage purge; cascade delete; audit log; 6 tests pass |
| 20 | YouTube API quota hardening | Core | ✅ Done | youtube/quota.py; atomic Lua consume; backoff in data_api; Beat refresh stops gracefully; 8 tests pass |
| 21 | Stripe billing — minute packs | Core | ✅ Done | billing/packs.py + ledger.py; atomic deduct_minutes; 60-min free trial on signup; pricing.html; 12 tests pass |
| 22 | Production Kubernetes deployment | Core | ✅ Done | Helm charts in deploy/; KEDA ScaledObject; PgBouncer sidecar; GKE Autopilot decision; deploy/README.md |
| 23 | VM provisioning + Cloudflare DNS + HTTPS | BETA | ✅ Done | DigitalOcean Droplet at `147.182.136.107` + Cloudflare Tunnel `autoclip-prod` + docker-compose.prod.yml; live at `autoclip.studio` |
| 24 | Production environment configuration | BETA | 🔲 Not started | .env secrets, ALLOWED_ORIGINS, GitHub Actions secrets |
| 25 | External API services provisioning | BETA | 🔲 Not started | Anthropic, Voyage, Deepgram, Cloudflare R2 |
| 26 | Google OAuth consent screen + beta test users | BETA | 🔲 Not started | External status, add friends as test users |
| 27 | YouTube API quota check + backoff verification | BETA | 🔲 Not started | Confirm quota limits; request increase if needed |
| 28 | Beta go-live smoke test + friend onboarding | BETA | 🔲 Not started | Full E2E on live deployment; invite 2-3 friends |
| 29 | Google OAuth app verification | PROD | 🔲 Not started | Submit for Google review; ~1–4 weeks external |
| 30 | Production hardening + public go-live | PROD | 🔲 Not started | Load test; all gates green; v1.0.0 tag |
| 31 | Operability kit — secrets registry, preflight doctor, deploy hardening, auto-heal | BETA | ✅ Done | docs/SECRETS.md + docs/ACCESS.md; scripts/doctor.py (14 tests); cloudflared+autoheal+healthchecks; amd64-only build; fixed blocking billing.tiers import; 313 tests pass |
| 32 | Restore test suite — starlette pin | HARDENING | ✅ Done | Pinned `starlette==0.41.3` (FastAPI 0.115.x range); test suite returns to 313 passed; DECISIONS.md entry on transitive-dep pinning |
| 33 | Cross-creator data leak in improvement brief | HARDENING | ✅ Done | Always-filter `Video.creator_id` added; ORDER BY recency; zero-data 400 short-circuit; new integration test; COMPLIANCE.md Findings & Fixes log; spawned Issue 56 (RLS evaluation) as defense-in-depth |
| 34 | Idempotent minute deduction on Celery retry | HARDENING | ✅ Done | New `MinuteDeduction` ledger with `UNIQUE(video_id)` idempotency key; `deduct_for_video` SAVEPOINT-atomic; 4 real-Postgres integration tests (sequential, concurrent race, 402-clean, audit fields); migration 0003; spawned Issue 57 (refund policy) |
| 41 | Replace pickle in preference model (RCE surface) | HARDENING | ✅ Done | joblib + `_RestrictedUnpickler` allowlist (10 entries); `to_bytes`/`from_bytes` rewritten; 4 new tests (round-trip + 2 rejection tests); no schema change |
| 42 | ffmpeg/subprocess timeouts | HARDENING | ✅ Done | `_run` accepts `timeout_s=120.0`; `_frame_dimensions` hardcodes `timeout=30`; `render_clip_file` computes `max(120, duration*4)`; 3 new timeout tests; DECISIONS.md entry |
| 35 | Idempotent DNA build (SEV-0) | HARDENING | ✅ Done | Single-transaction commit in `_build_dna_async`; `commit=False` param on `create_draft`, `embed_patterns`, `embed_brief`; 3 integration tests; 313 non-integration tests pass |
| 40 | Streaming upload + DoS guard | HARDENING | ✅ Done | 1 MB streaming chunk loop in upload_video; 413 + tempfile unlink on oversize; RSS delta test; 3 new tests in test_videos_upload_streaming.py; 314 tests pass |
| 44 | Auth boundary hardening — malformed sub 401, DELETE /me rate limit, MultiFernet rotation | SEC | ✅ Done | auth.py ValueError/KeyError catch; routers/auth.py 5/hour on DELETE /me; crypto.py MultiFernet + TokenDecryptError; +8 tests |

---

## Open Research Items

- [x] **Pricing model**: Minute packs + Stripe Checkout one-time payments. Issue 21.
- [x] **Production deployment**: GKE Autopilot + Helm + KEDA + PgBouncer. Issue 22.
- [x] **Transcription compute**: Deepgram (hosted) for MVP; WhisperX selectable via config. Resolved 2026-05-25.
- [ ] **YouTube API quota**: Confirm daily quota limits from Google Cloud Console for the project. Issue 27.
- [ ] **Retention curve availability window**: Verify how far back retention curves are available for the target channel.
- [ ] **TOKEN_ENCRYPTION_KEY rotation runbook**: Required before public launch.

---

## Pre-Public-Launch Gates (all must be green before opening to outside creators)

- [x] Lock `ALLOWED_ORIGINS` to production domain; disable `/docs` — env-driven: `docs_url` conditional on `ENV=="development"`; `ALLOWED_ORIGINS` from `.env`
- [x] Per-creator rate limiting + usage quotas before each LLM/render job — Issue 18 (slowapi, 10/h LLM, 20/h render, 120/min rest)
- [x] YouTube data-retention/refresh fully compliant (see `docs/COMPLIANCE.md`) — Issue 17 (Beat purge + analytics refresh)
- [x] `TOKEN_ENCRYPTION_KEY` rotation runbook written — see `docs/RUNBOOKS.md`
- [x] Terms of Service + Privacy Policy pages live — Issue 14 (`/static/tos.html`, `/static/privacy.html`)
- [ ] Google OAuth app verification completed for requested scopes — external Google process (Issue 29)
- [x] Account-deletion endpoint (right-to-erasure: token revocation + media purge) — Issue 19
- [x] Billing wired — Issue 21 (minute packs, atomic balance, 60-min free trial, Stripe Checkout)
- [x] Eval harness hardened with adversarial/edge cases — 3 new fixtures; fixed early-peak MIN_CLIP_S bug
