# CreatorClip — Project State

Updated after every issue closes.

---

## Current Status

**Active issue**: Phase 2 hardening — Batch 4 fully closed (Issues 38 W1, 52, 56). Three follow-up issues filed: 58 (transactional email infra), 59 (in-app notifications surface), 60 (RLS implementation), 61 (Issue 38 Wave 2).
**Last completed**: Issue 38 Wave 1 — Celery hot-path sync-in-async fixes via `asyncio.to_thread` + new `worker/storage.py` async wrappers.
**Blocked**: _(none)_

> **Closed Issue 38 Wave 1** (2026-05-28): Sync-in-async fixes for the Celery
> ingest pipeline. A full-codebase audit found 23 instances of sync external calls
> inside `async def` (class 1) or `await` while a DB session was open (class 2);
> Wave 1 closed all the class (1) findings in the Celery hot path (~14 of 23).
> Wave 2 is filed as Issue 61 — covers the AsyncAnthropic/AsyncVoyage SDK swap
> across `dna/brief.py` / `improvement/brief.py` / `clip_engine/scoring.py`, the
> router session-order refactor (`routers/auth.py` / `videos.py` / `clips.py` /
> `billing.py`), the `clip_engine/ranking.py` compute/persist split, and the
> 10-concurrent-improvement-brief load test.
>
> Wave 1 changes: new async wrappers in `worker/storage.py` (`aupload_file`,
> `adelete_file`, `adelete_prefix`, `alocal_path` — all dispatch to boto3 via
> `asyncio.to_thread`); the four Celery pipeline tasks
> (`_ingest_async` / `_transcribe_async` / `_signals_async` / `_render_clip_async`)
> now use the async wrappers + offload sync subprocess (ffmpeg / probe), librosa,
> WhisperX/Deepgram, and `render_clip_file` to threads; `_build_dna_async` wraps
> the sync Anthropic `generate_brief` call in `to_thread`; `dna/embeddings.py`
> gets a new `_aembed` async wrapper around the sync Voyage `_embed`;
> `_purge_stale_source_media_async` was restructured to release the session
> during the boto3 delete loop (select tuples → close → loop deletes via
> `adelete_file` → reopen session for a single UPDATE) — previously held one
> session across every R2 round-trip in the sweep.
>
> Test patches updated: `test_retention_tasks.py` for the new purge two-session
> + tuple shape and for `alocal_path`; `test_worker_pipeline.py` (Issue 52 file
> shipped earlier the same session) for `alocal_path`. Renamed worker tests
> still pass at 381 / 1 skipped / 54 deselected.

> **Closed Issue 52** (2026-05-28): Worker pipeline integration tests. The seven
> Celery async functions in `worker/tasks.py` (`_ingest_async`, `_transcribe_async`,
> `_signals_async`, `_render_clip_async`, `_generate_clips_async`, `_build_dna_async`,
> `_poll_clip_outcomes_async`) had no direct end-to-end coverage —
> `test_pipeline_trigger.py` only asserted registration / task chaining. New
> `tests/test_worker_pipeline.py` pins all 5 ACs against real Postgres with mocks at
> the storage (R2 / boto3) and external-SDK (YouTube Data API, ffmpeg) boundaries.
> Notable design: AC4 (per-creator median) seeds two creators with disjoint
> VideoMetrics — same fetched view count (100) yields opposite `performed_well`
> labels (A=False because 100 < 500 median, B=True because 100 ≥ 20 median) —
> a global-median computation would label both identically. AC5 (build_dna ValueError
> bypasses retry) calls `_build_dna_async` directly per the established
> `test_dna_build_idempotency.py` pattern; the task wrapper's `except ValueError:
> raise` is pinned by inspection because `build_dna.apply()` would call `asyncio.run`
> from inside the running pytest-asyncio loop (RuntimeError). No real fixture media
> files needed — `local_path` is mocked to yield a temp file, matching the existing
> `test_purge_integration.py` / `test_generate_clips_retry_integration.py` pattern.
> Test count: **381 passed, 1 skipped, 54 deselected** (+5 integration).

> **Closed Issue 56** (2026-05-28): Postgres Row-Level Security research-and-decide.
> Decision: **adopt RLS** as defense-in-depth underneath the existing
> application-layer always-filter for every tenant-owned table. Trigger context:
> the SEV-0 Issue 33 leak (a missed `creator_id` filter exposed cross-creator
> analytics to a Claude prompt) demonstrated that application-layer filtering is a
> linting problem disguised as a security property. RLS converts that into a
> structural guarantee: the database refuses to return cross-tenant rows even when
> application code forgets the WHERE. Implementation sketch pinned in
> `docs/DECISIONS.md`: 12 tables with direct `creator_id` columns get policies;
> two-role split (`creatorclip_app` no-BYPASSRLS + `creatorclip_migrate` BYPASSRLS;
> new `DATABASE_MIGRATION_URL`); `SET LOCAL app.creator_id` injected via
> SQLAlchemy `after_begin` event listener sourcing from FastAPI auth context;
> `FORCE ROW LEVEL SECURITY` on every covered table; mutation paths audit
> rowcount-zero-→-404. pgbouncer-future answer pinned: safe with transaction
> pooling, unsafe with statement pooling (we don't run pgbouncer today). Sources:
> Crunchy Data, pganalyze, Bytebase footguns writeup, SQLAlchemy 2.0 async docs
> + discussion #10469, Microsoft Azure multi-tenant guidance. **Implementation
> split to new Issue 60** — the Issue 56 spec was explicitly "research-and-decide",
> and the implementation is substantial enough (alembic migration + role split +
> middleware + mutation audit) to warrant its own focused PR. The decision
> ships now and Issue 60 inherits the carry-over ACs.

> **Closed Issue 57** (2026-05-28): Automatic refund on terminal ingest failure.
> Issue 34 made minute deduction per-video-idempotent, but a terminally-failing ingest
> still left the deduction in place. Policy decided (see DECISIONS): automatic refund,
> all terminal failure classes, surfaced via billing-history `MinutePack` row only
> (email + in-app banner split to new Issues 58 + 59 — both require infrastructure
> we don't have yet). New `billing/refund.py:refund_for_video` is idempotent on
> `pack_id="refund:<video_id>"`; new Celery base class `RefundOnFailureTask` in
> `worker/tasks.py` fires only when retries are exhausted, extracts `video_id` from
> `args[0]`, dispatches via `run_async`, and swallows internal exceptions so the
> task's original terminal failure stands. Applied to `ingest_video`,
> `transcribe_video`, `build_signals` (the three tasks where minutes can have been
> deducted by the time failure terminates). No alembic migration — `MinutePack`
> already supports the compensating-grant pattern. Disclosure language added to
> `docs/COMPLIANCE.md` as the canonical user-facing copy until pricing / ToS pages
> land in Phase 3.
> Test count: **381 passed, 1 skipped, 49 deselected** (+3 unit, +3 integration).

> **Closed Issue 46** (2026-05-28): Generate-clips retry safety + outcomes time-window
> bug. Two regressions in one issue. (1) `clip_engine/ranking.py:generate_and_rank_clips`
> unconditionally `DELETE FROM clips WHERE video_id = ...` before reinserting candidates;
> a late retry of `generate_clips` after `render_clip` had already completed wiped the
> `done` Clip rows, orphaning R2 objects and breaking the `ClipOutcome` FK chain. Fix:
> narrowed the DELETE WHERE to exclude `done` and `running` rows, and added an
> idempotency guard at the top of `_generate_clips_async` — if any `done` clip already
> exists for this video, log and return without re-extracting candidates. (2)
> `_poll_clip_outcomes_async`'s 7d arm had no upper bound on `Clip.created_at`, so every
> clip past its 7d checkpoint re-polled YouTube Data API every hour forever. Fix: added
> `Clip.created_at > now() - interval '30 days'` to the WHERE — after 30 days the
> `performed_well` label is stale enough that flipping it retroactively offers no
> preference-model signal. No migration needed. Predicate logic pinned via two unit
> tests in `tests/test_outcomes.py`; all three regressions pinned end-to-end against a
> real Postgres in `tests/test_generate_clips_retry_integration.py` (marker:
> `integration`).
> Test count: **375 passed, 1 skipped, 46 deselected** (+2 unit, +3 integration).

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
