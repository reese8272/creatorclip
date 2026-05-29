# CreatorClip — Design Decisions Log

Entries are added whenever an architectural decision is made, a library is chosen, or
implementation diverges from the PRD. Every entry must include what, why, source/evidence, and date.

---

## 2026-05-29 — Issue 75: improvement brief → 202 + poll (async Celery job)

### What changed
The improvement brief was a synchronous `GET /creators/me/improvement-brief` that
blocked up to 120s on an Anthropic + web_search call. Behind Cloudflare (~100s
proxy timeout) that 524s the user — a feature broken in production. Converted to
the standard async-job pattern:
- **`POST /creators/me/improvement-brief`** (202) — validates channel + has-data
  (immediate 400 if not), debounces (returns the in-flight status instead of
  enqueuing a duplicate — no double LLM spend), sets `pending`, enqueues a Celery
  task. Keeps the 10/hour limit (the expensive op).
- **`GET /creators/me/improvement-brief`** — non-blocking poll; returns
  `{"status": none|pending|running|done|failed}` (+ `brief` / `error`). 60/hour.
- **`worker.tasks.generate_improvement_brief`** — builds the analytics (moved out of
  the router) and runs the LLM call off the worker loop (`asyncio.to_thread`, Issue
  68), writing `done`/`failed`. No Celery retry (a failure is surfaced as `failed`
  for the user to re-trigger, not silently re-run → no surprise double spend).
- **`improvement/jobs.py`** — Redis-backed status keyed by `creator_id` (1h TTL).
- Frontend `insights.html` POSTs then polls every 3s to a 180s deadline.

### Why this shape
202 + poll is the canonical REST pattern for long jobs; the codebase already polls
(`/videos/{id}/status`). **Status in Redis, keyed by creator id** — no migration
(the brief is ephemeral/regenerable, not durable data; avoids deploy risk before
beta) and **isolation is by construction** (the key *is* the creator id, so a
creator can only ever read its own job — satisfies the strict per-creator rule
without a task→owner map). The Issue-33 SEV-0 scoping (analytics filtered to the
requesting creator) moved intact into the worker task; its isolation tests now
assert it there.

### Alternatives ruled out
- **Celery `AsyncResult` by task_id:** weaker isolation (`PENDING` is ambiguous;
  needs a task→creator map) — rejected for the creator-keyed Redis approach.
- **New DB table:** migration deploy-risk for ephemeral data — rejected.
- **SSE/WebSocket streaming:** overkill for a vanilla-JS frontend (KISS) — rejected
  for simple polling.
- **Raise the proxy timeout:** Cloudflare's ~100s isn't configurable off Enterprise,
  and a 120s request is bad practice regardless.

### Note
Debounce is best-effort (a rare concurrent double-POST could enqueue twice); bounded
by the 10/hour limit and harmless beyond a little extra spend — not worth a Redis
`SET NX` lock for beta.

### Verification
3 DB-free router unit tests (channel check, debounce-no-duplicate-enqueue, GET
passthrough) + integration tests repointed to the worker helper (SEV-0 scoping,
done/failed, offload, 202-pending-enqueue). Default suite **421 passed** (+3);
gates ruff 0 / mypy 30 / bandit 0,0 / pip_audit 0.

---

## 2026-05-29 — Tier-1: runnable PgBouncer load harness to verify the BLOCKER (Issue 58)

### What changed
`tests/perf/` grew from a bare locustfile into a one-command BLOCKER verifier:
- **`docker-compose.perf.yml`** — reproduces the prod topology the fix depends on:
  Postgres + **PgBouncer in `POOL_MODE=transaction`** + Redis + the app with its
  `DATABASE_URL` pointed at **pgbouncer:6432** (not Postgres). That transaction
  pooling is the exact condition that makes psycopg3 server-side prepared
  statements vanish across requests — which CI (direct-to-Postgres) can never see.
- **`seed.py`** — idempotent seed of a fixed creator (UUID `…0000ff`) with
  videos/metrics/retention/clips/confirmed-DNA/pgvector-embeddings/activity/balance,
  so the read endpoints return non-empty payloads (empty tables hide N+1 + ser cost).
- **`run.sh`** — builds the stack, migrates **direct to Postgres** (0006's
  `CREATE INDEX CONCURRENTLY` can't run under transaction pooling), seeds, runs
  Locust headless, then **greps the app logs for `prepared statement … does not
  exist` / `InvalidSqlStatementName`** and exits non-zero if found. That log
  signature is the pass/fail oracle for the `prepare_threshold=None` fix.

### Why this shape
The fix (`db.py connect_args={"prepare_threshold": None}`) is code-complete but was
unprovable without a real pooler. A self-contained compose stack makes the proof a
single command that anyone can run locally or on staging — no hand-assembled
infra, deterministic seed (no creator-id handoff), and a machine-checkable verdict
rather than "eyeball the latency chart". Locust tool choice unchanged (prior entry).

### Notes / constraints
- Migrations run against Postgres directly; only the app's runtime traffic goes
  through PgBouncer (mirrors how `CREATE INDEX CONCURRENTLY` must bypass the pooler).
- PgBouncer `AUTH_TYPE=plain` against a scram Postgres is fine — PgBouncer holds the
  plaintext and completes the server-side SCRAM handshake itself.
- Could not execute in the build sandbox (no container-registry egress); validated
  statically: `docker compose config` valid, `bash -n` clean, `seed.py` imports
  resolve against the real models, ruff clean. Run it on staging to capture numbers.

---

## 2026-05-29 — Tier-1 pre-beta launch readiness (legal pages + CORS lockdown + deploy verify)

### What changed
- **Clean legal routes** `main.py` `/privacy` → `static/privacy.html`, `/terms` →
  `static/tos.html` (`include_in_schema=False`). The pages existed but were only
  reachable at `/static/*.html` and were unrouted/undiscoverable.
- **Google API *Limited Use* affirmative disclosure** added to `privacy.html` (a
  dedicated section) — the exact statement Google requires for OAuth verification of
  YouTube scopes: *"CreatorClip's use of information received from Google APIs will
  adhere to the Google API Services User Data Policy, including the Limited Use
  requirements."* + the no-transfer / no-ads / no-human-reads specifics.
- **Homepage footer** (`index.html`) now links `/privacy` + `/terms` and restates the
  Limited Use line — Google requires the privacy policy be discoverable from the app
  home.
- **CORS production fail-fast** (`config.py` new `_lock_prod_cors` validator): when
  `ENV=production`, `ALLOWED_ORIGINS` must be non-empty, HTTPS, domain-locked — no
  `*`, no `localhost`/`127.0.0.1`, no `http://`. Boots-fail rather than shipping an
  open CORS policy. (`/docs` was already disabled outside development.)
- **`scripts/verify_deploy.sh`** — turnkey Tier-1.2 check: `/health` ok, `/privacy`
  + `/terms` + `/metrics` = 200, `/docs` = 404 in prod, and `alembic current` ==
  the expected head (`a7b8c9d0e1f2` / 0007) over SSH. Fully parameterized
  (DOMAIN/SSH_HOST/DEPLOY_DIR/EXPECTED_HEAD).

### Why
This is the buildable slice of the Tier-1 launch gate. A **closed beta does not need
full Google OAuth verification** — the consent screen's *Testing* mode allows up to
100 explicitly-added test users with unverified sensitive scopes — so verification
(1.3) is a *public-launch* gate, not a *beta* gate. What a beta does need: correct,
locked prod config; routed, discoverable legal pages with the mandatory Limited Use
disclosure (so the consent screen and login flow are legitimate); and a way to
confirm the deploy actually landed.

### Industry standard checked (live, 2026-05-29)
Google API Services User Data Policy — the affirmative Limited Use statement must
appear in the privacy policy *and* be linked from the homepage; the recommended
wording is the one used verbatim above. CORS with `allow_credentials=True` + a
wildcard origin is invalid per the Fetch spec, hence the hard prod guard.
Sources: Google API Services User Data Policy; YouTube API Services Developer Policies.

### Legal-text caveat
The privacy/ToS bodies remain a **draft pending legal review** (banner retained).
The text added is the Google-mandated compliance disclosure grounded in the actual
data practices (`docs/COMPLIANCE.md`), not legal advice; have counsel review before
public launch.

### Verification
Full suite **418 passed, 1 skipped, 55 deselected** (+8 DB-free tests: `/privacy`
+ `/terms` routes, Limited-Use disclosure present, homepage links present, and four
CORS-validator cases — reject localhost / `*` / `http://`, accept HTTPS domain).
Gates unchanged: ruff 0 / mypy 30 / bandit 0,0 / pip_audit 0. `verify_deploy.sh`
syntax-checked (`bash -n`).

---

## 2026-05-29 — Issue 75(f): Observability (correlation ids + structured logs + metrics)

### What changed
New `observability.py` wires three things, integrated in `main.py` (API) and
`worker/celery_app.py` (worker):
- **Correlation id** — a `request_id_ctx: ContextVar[str]`; a pure-ASGI
  `RequestIDMiddleware` (added last → outermost) reads an inbound
  `REQUEST_ID_HEADER` (default `X-Request-ID`) or mints a UUID4, binds it, and
  echoes it on the response. A `RequestIDLogFilter` injects `request_id` onto every
  log record.
- **Structured logs** — `JsonLogFormatter` emits one JSON object per line (incl.
  `request_id` + any `extra` fields); `configure_logging(json_logs=settings.LOG_JSON)`
  replaces `logging.basicConfig`, idempotent, text fallback for local dev.
- **Golden signals** — `prometheus-client==0.25.0`: `http_request_duration_seconds`
  (latency + traffic/errors via the `_count` by status, labelled by route template
  to bound cardinality) recorded in the same middleware; `celery_task_duration_seconds`
  + `celery_tasks_total` recorded via task signals; exposed at `/metrics`
  (gated by `METRICS_ENABLED`, `include_in_schema=False`).
- **Celery propagation** — `before_task_publish` stamps the id onto task headers;
  `task_prerun` binds it (+ starts the task timer); `task_postrun` records the task
  metrics and clears the id. Connected with `weak=False`.

### Why
Assessment axis G was the last ⚠️ blocking *operability*: no request id meant a
failed render couldn't be traced API→worker, and there were no golden signals for
p99 / error rate. This is a pre-deploy operational gate, not polish.

### Decisions / deviations
- **Hand-rolled correlation layer, not `asgi-correlation-id`.** Same documented
  pattern (ContextVar + ASGI middleware + echo header + logging filter + Celery
  signals) in ~60 lines we own, adding **zero** new dependency/CVE surface right
  after ratcheting pip-audit to 0. One new dep (prometheus-client, the canonical
  metrics client) is justified; a second for ~60 lines is not.
- **Prometheus metrics now; OpenTelemetry tracing deferred.** Full OTel needs a
  collector to operate; golden-signals-before-launch is the standard MVP and is
  k8s-native (our deploy target). Distributed tracing is a tracked follow-up.
- **`weak=False` on the Celery signal connects** — Celery connects receivers weakly
  by default, so module-level handlers held by no other ref would be GC'd and never
  fire (caught by a failing test before it shipped).
- **Pure-ASGI middleware, not `BaseHTTPMiddleware`** — avoids the known
  streaming/background-task pitfalls; reads `scope["route"]` in the `finally` (set by
  the inner router by then) so the latency label is the route template.

### Industry standard checked (live, 2026-05-29)
The de-facto pattern across `snok/asgi-correlation-id`, `django-structlog`'s Celery
integration, and current FastAPI observability guides: ContextVar + ASGI middleware
+ echo-header + logging filter, Celery signal propagation, Prometheus for metrics,
OTel for tracing as a later layer. Sources in the session research.

### Verification
Full suite **410 passed, 1 skipped, 55 deselected** (+9 DB-free observability tests:
id validation/mint, log-filter injection, JSON format, idempotent config, mint+echo
+ inbound-respect via TestClient, `/metrics` exposition, Celery publish→run→clear +
task-counter increment). Gates unchanged: ruff 0 / mypy 30 / bandit 0,0 / pip_audit 0.

---

## 2026-05-29 — Issue 75(a): pip-audit CVE remediation (14 → 0)

### What changed
Patched every CVE with a fix in our compatible range; pinned in `requirements.txt`:
- **cryptography** 43.0.3 → **46.0.7** — OpenSSL secadv (GHSA-79v4-65xg-pq4g), EC
  subgroup check (GHSA-r6ph-v2qm-q3c2), DNS name-constraint (PYSEC-2026-35), and the
  46.0.6-only PYSEC-2026-36 found after the first bump.
- **python-multipart** 0.0.20 → **0.0.27** — path-traversal + 2 DoS.
- **PyJWT** 2.9.0 → **2.12.0** — `crit`-header validation bypass (PYSEC-2026-120). The
  disputed PYSEC-2025-183 ("weak encryption") dropped off entirely: it was scoped to
  2.10.1 and 2.12.0 is outside its affected range.
- **lightgbm** 4.5.0 → **4.6.0** — RCE (PYSEC-2024-231).
- **python-dotenv** 1.0.1 → **1.2.2** — symlink-follow file overwrite.
- **starlette** 0.41.3 → **0.49.1** (the newest under FastAPI 0.120.x's `<0.50.0`
  pin) — multipart-blocks-the-loop (GHSA-2c2j-9gv5-cj73) + Range-header quadratic DoS
  (GHSA-7f5h-v6xp-fcq8). Required bumping **FastAPI** 0.115.4 → **0.120.4**, the
  smallest bump whose starlette pin admits 0.49.1.

The gate (`run_layer0.py:gate_pip_audit`) now passes a curated `--ignore-vuln`
allowlist (`PIP_AUDIT_IGNORES`); baseline `pip_audit_vulns` ratcheted **14 → 0**.

### Accepted-risk (2 residuals, in `PIP_AUDIT_IGNORES`)
- **pytest GHSA-6w46-j5rx-g56g** — local `/tmp/pytest-of-*` predictable-name
  priv/DoS. Fixed only in pytest 9, but `pytest-asyncio==0.24.0` caps `pytest<9`, so
  it's a test-stack cascade, not a runtime exposure (dev/CI only). Lift when the test
  stack is bumped as a unit.
- **starlette PYSEC-2026-161** — Host-header path injection, fixed only in starlette
  **1.0.1**, which needs FastAPI 0.136.x (the documented `on_startup/on_shutdown`
  1.x landmine). The advisory itself notes routing matches on the *actual* path; we
  also sit behind Cloudflare + locked `ALLOWED_ORIGINS`. Tracked as a starlette-1.x
  migration follow-up under Issue 75.

### Why these chosen versions / why not literal-0 without ignores
Going to starlette 1.x / FastAPI 0.136 to close the last starlette CVE is a
major-line jump with a documented breakage surface — out of scope for a CVE-patch
task. The standard posture for a `pip-audit` CI gate is patch-to-nearest-fix plus a
*justified* ignore-list for no-fix/disputed/major-line-only advisories, kept in
lockstep with this entry. Verified each fix version and the FastAPI↔starlette pin
coupling against live PyPI metadata, not memory.

### Verification
`pip check` clean; full suite **401 passed, 1 skipped, 55 deselected** on the bumped
deps (auth/crypto/upload/preference/lifespan all green); `run_layer0.py` reports
`pip_audit 0`, no other gate regression (ruff 0, mypy 30, bandit 0/0). PyJWT 2.12
emits an `InsecureKeyLengthWarning` only on a short-key test fixture — production
uses a full-length configured secret.

---

## 2026-05-29 — Batch 8 (Issues 73 + 74 + 75): input/memory/config hardening

### What changed
- **74:** `ingestion/audio.py` loads at `sr=16000` (≈3× less memory than the native
  rate); `ingestion/transcribe.py` caches the WhisperX model + align model
  (`lru_cache`) and makes the Deepgram client + AssemblyAI key module-level singletons.
- **73:** `routers/videos.py` validates `youtube_video_id` against `^[A-Za-z0-9_-]{11}$`
  (422) on `/link` and `/upload`, before it reaches a storage key.
- **75:** `config.py` fails fast in production when Stripe secrets are unset;
  `upload_intel/timing.py` skips out-of-range `day_of_week`/`hour` rows instead of
  `IndexError`→500.

### Why
Memory: the librosa full-rate decode was the dominant OOM vector under concurrency.
Security: an unvalidated `youtube_video_id` could reshape the R2 object key (`../`).
Robustness: a missing Stripe secret should fail at boot, not at first webhook; one
bad activity row shouldn't 500 the upload-intel endpoint.

### Scope decisions (honest deferrals, tracked in Issue 75)
- **Full `response_model` coverage (73)** is mechanical hygiene across ~16 endpoints
  with no security/correctness risk; rushing accurate schemas for every dict in one
  commit risks runtime 500s. Deferred to Issue 75. The *security* part (input
  validation) shipped here.
- **Deepgram file-stream (74)** deferred: the deepgram SDK isn't installed in this
  environment to verify the streaming API, and `sr=16000` already removes the main
  memory vector.
- **CVE triage, analytics-retention cadence, observability, mypy→0** are each
  research/infra efforts, not single commits — enumerated in Issue 75.

---

## 2026-05-29 — Issue 71 (Batch 7): Preference hardening

### What changed
- `preference/model.py`: the `from_bytes` joblib-global swap is now guarded by a
  module `threading.Lock`. `predict_score` validates feature count against
  `n_features_in_` and raises on mismatch (no more silent `0.5`).
- `preference/train.py`: `build_and_save` takes `pg_advisory_xact_lock(hashtext(creator_id))`
  before the `max(version)+1` read; `load_latest` returns `None` when the stored
  `feature_schema_jsonb` differs from `FEATURE_NAMES` (schema drift → DNA fallback).
- `clip_engine/ranking.py`: `rerank_with_preference` scores all clips first; if the
  scorer raises, it keeps the DNA ranking untouched (honest fallback).

### Why
The monkeypatch was not thread-safe — a concurrent load could restore the
unrestricted unpickler mid-load, defeating the RCE allowlist exactly when a
tampered blob is read. `max()+1` raced into a UNIQUE violation under concurrent
retrains. `predict_score`'s blanket `0.5` let a broken/drifted model silently move
rankings.

### Decision: lock over direct unpickler
The finding suggested instantiating `_RestrictedUnpickler` directly to avoid the
global swap. Verified empirically that joblib's `NumpyUnpickler.__init__` signature
is version-dependent (this joblib requires an `ensure_native_byte_order` arg), so
direct instantiation is brittle across upgrades. A module-level `threading.Lock`
around the existing swap is version-proof and fully thread-safe — the accepted
alternative in the finding. Serialization cost is negligible (loads are rare; a
per-(creator,version) scorer cache is the tracked optimization under Issue 75).

---

## 2026-05-29 — Issue 70 (Batch 6): Bound poll_clip_outcomes

### What changed
- Migration `0007`: `clip_outcomes.final BOOLEAN NOT NULL DEFAULT FALSE` + a partial
  index on `fetched_at WHERE final=false AND published_youtube_id IS NOT NULL`.
- `_poll_clip_outcomes_async`: query excludes `final IS TRUE` and caps candidates to
  `Clip.created_at >= now-10d`; the 7d-checkpoint poll sets `final=True`; commits per
  creator.

### Why
The `fetched_at < cutoff_7d` branch had no terminal guard, so every published clip
re-qualified for a quota-costing re-poll every 7 days forever — an unbounded drain
that would eventually starve the daily analytics refresh (axes E/F). One session was
also held across the whole N×M network loop.

### Decision
`final` terminal marker is the primary fix; the 10-day created-at cap is
defense-in-depth so the scan is bounded even before `final` propagates to legacy
rows (which self-finalize on their next 7d poll — no backfill needed). Per-creator
commit bounds the transaction/connection hold to one creator's network calls.

---

## 2026-05-29 — Issue 69 (Batch 5): Prompt-cache split + web_search extraction

### What changed
- `dna/brief.py` and `improvement/brief.py`: `system` is now two blocks — a static
  instruction block carrying `cache_control: ephemeral`, then a separate uncached
  block holding the per-creator corpus/analytics.
- `improvement/brief.py` returns `text_blocks[-1].text` (final answer after the
  last web_search `tool_use`), not `text_blocks[0]` (the preamble). `dna/brief.py`
  uses `[-1]` for consistency.
- Corrected misleading docstrings (the DNA brief does not share a cache with the
  clip scorer — separate prompts never share a cache entry).

### Why
The volatile data was interpolated into the cached block, so the prefix changed
every call (the assessment's "~0% hit"). The `improvement` extraction bug returned
the model's "let me search…" preamble instead of the synthesised brief.

### Finding: caching can't engage at this prompt size (the real correction)
Per the `/claude-api` skill, the **minimum cacheable prefix is 2048 tokens on
Sonnet 4.6** (4096 on Opus); below it the cache silently no-ops. Both static
instruction blocks are ~350-450 tokens — far below the floor — and both calls are
low-frequency (DNA build once per build; improvement 10/hour), so there is no
repeated-prefix-within-window to cache either. **The split is the correct
structure but is NOT a cost win for these two endpoints.** The acceptance
criterion "cache_read_input_tokens non-zero after warmup" was therefore replaced
with a structural assertion (volatile data is out of the cached block).

### Follow-up (Issue 75)
The genuine caching beneficiary is `clip_engine/scoring.py`: a large per-creator
prefix (DNA brief + the 11 principles) reused across all of a creator's videos in
a window. Splitting static/volatile + `cache_control` there, with a prefix above
the 2048-token floor, is where caching actually pays off.

### Standard checked
`/claude-api` prompt-caching: stable-prefix-first, breakpoint on the last stable
block, volatile after; minimum cacheable prefix 2048 (Sonnet 4.6) / 4096 (Opus);
web_search interleaves text/tool_use — take the final text block.

---

## 2026-05-29 — Issue 72 (Batch 4b): Shared YouTube HTTP client + 5xx backoff

### What changed
- New `youtube/_http.py`: lazy per-process singleton `httpx.AsyncClient`
  (`Timeout(15, connect=5)`) + `aclose()`. All three OAuth helpers, `_get_json`,
  and `_fetch_report` reuse it. `aclose()` wired into the API lifespan
  (`main.py`) and worker shutdown (`worker/celery_app.py`).
- `_get_json`/`_fetch_report` retry transient 5xx with jittered backoff.

### Why
Per-call `httpx.AsyncClient()` with no timeout on the token-refresh hot path could
hang a request/worker indefinitely if Google stalls; per-call construction also
defeats connection pooling under the analytics fan-out (axes B/E).

### Decisions / standard checked
- **Lazy singleton** (not import-time) so the connection pool binds to the loop
  that first uses it — the API app loop and the worker's post-fork singleton loop
  (Issue 39) are different; lazy avoids a cross-loop binding bug.
- httpx guidance: reuse one `AsyncClient` for pooling; always set timeouts. 5xx on
  idempotent GETs → backoff+retry (axis E).

### Test-isolation note
The existing `test_oauth_lifecycle` `_get_json`/`_fetch_report` tests mocked
`httpx.AsyncClient` directly; rebased them onto the new `youtube._http.client`
boundary. (The per-test event loop + a module singleton is the same class of
hazard as Issue 39 — but in tests every patch targets `_http.client`, and the one
test that builds the real client `aclose()`s it, so nothing leaks across loops.)

---

## 2026-05-29 — Issue 68 (Batch 4b): Worker-loop offload + transcription timeout

### What changed
- `dna/embeddings.py`: both `_embed` (Voyage) calls run via `await asyncio.to_thread`.
- `worker/tasks.py`: `generate_brief` and `extract_audio_events` offloaded via
  `asyncio.to_thread`; `transcribe_audio` via
  `asyncio.wait_for(asyncio.to_thread(...), timeout=settings.TRANSCRIPTION_TIMEOUT_S)`.
- `config.py`/`.env.example`: `TRANSCRIPTION_TIMEOUT_S` (default 300).

### Why
These sync calls ran on the worker's Issue-39 singleton event loop (bounded by
prefork concurrency today, fragile to any pool change), and transcription had no
upper bound — a hung provider stalled the worker indefinitely (axis E).

### Decision: wait_for as the job-level bound; SDK-native timeouts deferred
`wait_for(to_thread(...))` guarantees the *job* fails (→ Celery retry) after the
timeout and keeps the loop free. It cannot kill the worker thread, which lives
until the SDK call returns; the Deepgram/AssemblyAI SDKs aren't installed in this
environment to verify their native timeout params, so SDK-level timeouts are a
tracked follow-up (Issue 75). Voyage already self-bounds (`timeout=30`).

### Standard checked
FastAPI/asyncio: offload blocking/CPU work with `asyncio.to_thread`; never let a
sync SDK + retry-sleep run on the event loop. (Batch-0 load-testing research.)

---

## 2026-05-29 — Batch 4a (Issues 66 + 67): Blocking calls off the API event loop

### What changed
- `routers/improvement.py`: the 120s Anthropic+web_search brief now runs via
  `await asyncio.to_thread(generate_improvement_brief, ...)`.
- `routers/videos.py`: the R2/disk `upload_file` write now runs via
  `await asyncio.to_thread(...)`.
- `routers/auth.py`: `delete_account`'s `delete_prefix` purge now runs via
  `await asyncio.to_thread(...)`.

### Why
Each was a synchronous call inside an `async def` handler — on FastAPI's
single-threaded event loop, one in-flight call stalled every other concurrent
request on that worker (axis B; "p99 issues come from sync calls hidden in async
paths"). `asyncio.to_thread` moves the blocking work to a threadpool so the loop
stays responsive.

### Decision: to_thread now, Celery+poll later (Issue 66)
`to_thread` fully fixes the loop-blocking SEV-1. It does NOT shorten the request
(the brief can still take 120s, which may exceed a production LB/gateway timeout).
The robust UX is a Celery 202/poll job (like `build_dna`), but that needs result
storage + a poll endpoint + frontend work — tracked under Issue 75 rather than
ballooning this batch. The upload/delete offloads are unambiguously correct as
`to_thread` (the work must finish before responding; it just must not hold the loop).

### Standard checked
FastAPI guidance: never run blocking/CPU/sync-I/O directly in an `async def` path;
offload via `asyncio.to_thread` or a task queue. (Confirmed in the load-testing
research from Batch 0.)

### Testing
Integration tests (`tests/test_event_loop_offload_integration.py`) assert the
offloaded callable is recorded through an `asyncio.to_thread` shim — external
services mocked, DB real.

---

## 2026-05-29 — Batch 3 (Issue 65): pgvector HNSW + FK index

### What changed
- Migration `0006`: `ix_dna_embeddings_hnsw` (HNSW, `vector_cosine_ops`,
  `m=16, ef_construction=200`) on `dna_embeddings.embedding`, and
  `ix_clip_feedback_creator_id` on `clip_feedback.creator_id`. Both built
  `CREATE INDEX CONCURRENTLY` inside `op.get_context().autocommit_block()`.

### Why
The embedding similarity query (`<=>`, cosine) was an unindexed O(rows) scan;
`clip_feedback.creator_id` was an unindexed FK on the (now hot, post-Issue-60)
training + retrain-debounce path.

### Decisions / standard checked
- **HNSW over IVFFlat**: HNSW is the recommended default for <10M vectors with
  active writes; IVFFlat's k-means clustering is data-dependent and must NOT live
  in a migration. `m=16, ef_construction=200` is the documented better-recall
  starting point (defaults 16/64). Op class `vector_cosine_ops` matches the `<=>`
  query (voyage-3.5 vectors). Sources: pgvector index-selection guides
  (medium.com/@philmcc…), AWS pgvector indexing deep-dive.
- **CONCURRENTLY + autocommit_block**: `CREATE INDEX CONCURRENTLY` can't run in
  Alembic's default transaction; the autocommit block keeps the build online-safe.

### Scope correction (assessment was imprecise)
- `dna_embeddings.creator_id` already has a btree index (`ix_dna_embeddings_creator_id`,
  migration 0001).
- `preference_models.creator_id` is already covered by the `(creator_id, version)`
  unique-constraint index (leading column serves `WHERE creator_id ORDER BY version`).
- So no redundant `creator_id` indexes were added — only the HNSW index and the
  genuinely missing `clip_feedback.creator_id`.

---

## 2026-05-29 — Batch 2 (Issues 63 + 64): Idempotent unique-keyed writes

### What changed
- `billing/ledger.py`: `grant_minutes` is now self-idempotent — fast-path
  existence check (keyed grants) + `begin_nested()` SAVEPOINT + `flush()` +
  `IntegrityError` catch, mirroring `deduct_for_video`.
- `dna/profile.py`: `create_draft` accepts `build_job_id`; `confirm_draft` locks the
  creator's DNA rows `with_for_update()`, supersedes-before-promotes with an explicit
  `flush()`, and catches `IntegrityError`.
- `worker/tasks.py`: `build_dna` passes `self.request.id`; `_build_dna_async`
  early-returns before the paid LLM/Voyage calls when a draft for that job_id exists.
- Migration `0005`: `creator_dna.build_job_id` (nullable) + index, and partial unique
  index `uq_one_confirmed_dna_per_creator ON creator_dna(creator_id) WHERE status='confirmed'`.

### Why
At-least-once delivery + concurrent Stripe/worker delivery duplicated money records,
re-spent paid Anthropic/Voyage calls (duplicate DNA drafts), and could leave two
`confirmed` DNA rows. The idempotency key for DNA builds is the Celery `task_id`
(stable across redelivery, new per user re-request); for grants it is
`stripe_session_id` (UNIQUE).

### Standard / precedent
In-repo precedent `deduct_for_video` (UNIQUE + SAVEPOINT + IntegrityError); Celery
idempotency (Batch 1 research). Partial unique index is non-deferrable, hence the
ordered flush (supersede → flush → promote) so two 'confirmed' rows never coexist
even transiently.

### Coverage baseline moved: 69.97% → 69.54% (justified)
These three fixes are DB-mutating logic. The project rule (CLAUDE.md Testing Rules)
forbids mocking the DB, so they are covered by integration tests
(`test_dna_idempotency_integration.py`, `test_billing_grant_idempotency_integration.py`)
which run in the integration CI, not the unit-coverage gate. Their unit-invisible
lines lowered the unit-only floor. Per the README ratchet + Phase-4 rule, the floor
moves to current reality (69.54%) with this justification; it climbs back as
unit-coverable code lands in later batches.

---

## 2026-05-29 — Batch 1 (Issues 61 + 62): Worker at-least-once safety

### What changed
- `clip_engine/ranking.py`: `generate_and_rank_clips` is now idempotent — it
  early-returns the existing clips (rank order) when any exist for the video,
  instead of `delete(Clip)` + reinsert.
- `worker/celery_app.py`: added `task_reject_on_worker_lost=True`,
  `task_soft_time_limit=3000`, `task_time_limit=3300`, and
  `broker_transport_options={"visibility_timeout": 3600}`.
- `worker/tasks.py`: `_render_clip_async` early-returns when the clip is already
  `render_status==done` with a `render_uri`.

### Why
Celery delivers at-least-once. With `acks_late` and cascade-delete on
`Clip.feedback`/`Clip.outcome`, a redelivered `build_signals`→`generate_clips`
silently wiped a creator's feedback labels and outcomes (data loss; and post-Issue-60
the preference training signal). `acks_late` without `reject_on_worker_lost` also
dropped tasks whose worker was OOM-killed, and with no time limit a task exceeding
the broker visibility timeout was redelivered while still running (double execution).

### Decisions / standard checked
- Pair `task_acks_late` with `task_reject_on_worker_lost`; the **invariant
  soft < hard time_limit < visibility_timeout** ensures a task is killed before
  Redis redelivers a running copy. Assume tasks run twice → design idempotent.
  Sources: francoisvoron.com/blog/configure-celery-for-reliable-delivery;
  dev.to "Celery + Redis at Scale"; celery/celery#5935.
- **Idempotency strategy = skip-if-exists** (KISS) over "replace only pending
  zero-feedback clips" — there is no regenerate trigger today, and skip-if-exists
  can never wipe feedback. The finer-grained replacement is noted as a future
  enhancement if a re-generate feature is ever added.

### Caveat
`task_time_limit=3300` (55m) covers normal media jobs; a very long source on CPU
WhisperX could exceed it → use the hosted transcription backend or add a per-task
`time_limit` override. Documented here rather than guessed at a larger global ceiling.

---

## 2026-05-29 — Issue 60: Wire the personalization loop + maturity-gated blend

### What changed
- `clip_engine/ranking.py`: `generate_and_rank_clips` now calls
  `rerank_with_preference` after persisting (and re-commits the blended score/rank).
- `preference/model.py`: new `preference_weight(label_count)` — the rerank blend
  weight. `rerank_with_preference` uses `(1-w)*dna + w*pref` instead of fixed 50/50.
- `worker/tasks.py`: new idempotent, self-debouncing `retrain_preference(creator_id)`
  Celery task (no-op when no trainable feedback since the latest model version).
- `routers/review.py`: enqueues `retrain_preference` after each feedback write.
- `config.py`/`.env.example`: `PREFERENCE_WEIGHT_CAP` (default 0.5).
- `preference/train.py`: exposed `TRAINABLE_ACTIONS` (DRY for the debounce filter).

### Why
Two subagents independently found personalization was dead code — never trained,
never applied. This is half the North Star ("learns your style, adapts as you
evolve"). The flat 50/50 blend also gave a 2-label cold-start model equal authority
over ranking, violating the CLAUDE.md honesty rule ("below the threshold, ranking
falls back to DNA + signals").

### Decisions
- **Blend curve:** weight 0 below `PERSONALIZATION_THRESHOLD_LABELS` (honest DNA
  fallback), linear ramp to `PREFERENCE_WEIGHT_CAP` by 2× the threshold. This is the
  standard hybrid cold-start strategy — start content-based, grow personalization as
  the creator's own feedback accumulates. `label_count` already lives on
  `PreferenceScorer`, so no migration. Sources: hybrid/cold-start recommender
  practice — expressanalytics.com/blog/cold-start-problem; arxiv 1808.10664.
- **Retrain trigger:** enqueue-on-feedback (responsive, matches "adapts as you
  evolve") with an in-task new-labels guard, over a Beat-only cadence (laggy).
  Repeated clicks collapse to cheap no-ops.

### Scope boundaries (deferred, tracked)
- `build_and_save` version-race (`max()+1` → IntegrityError) and unpickler
  thread-safety → **Issue 71**; the retrain task catches `IntegrityError` as a
  minimal guard meanwhile.
- `from_bytes` runs sync on the worker loop (bounded by prefork) → **Issues 68/71**.

---

## 2026-05-29 — Issue 59: Render from setup_start_s + ffmpeg accurate-seek finding

### What changed
- `worker/tasks.py` renders from `setup_start_s` via a new pure helper
  `_render_start_for(clip)` (coalesces to `start_s` only when the nullable
  `setup_start_s` is unset), instead of the fixed peak−window `start_s`.
- `clip_engine/render.py` sets `-accurate_seek` explicitly before `-i`.
- Tests: DB-free unit guards for `_render_start_for` + the seek flag, plus an
  end-to-end integration test.

### Why
The render cut from `start_s` (fixed peak−75s) while scoring, the API response,
and the eval all key on `setup_start_s` — so the delivered Short did not actually
"clip the setup" (CLIPPING_PRINCIPLE #2), the product's core differentiator.

### Finding: the assessment's "inaccurate seek" SEV-2 was a false positive
`clip_engine.md` flagged `-ss` before `-i` as drifting up to one GOP. That is true
for **stream copy**, but this pipeline **re-encodes with libx264**, and ffmpeg
applies `accurate_seek` by default when encoding — so the existing cut was already
frame-accurate. We set `-accurate_seek` explicitly as a self-documenting guard (a
no-op today) so the cut stays accurate if anyone later switches to `-c copy`. We
did NOT restructure to output-seek (`-ss` after `-i`), which decodes from 0 and
could blow the render timeout for clips deep in a long source.
Source: ffmpeg seek semantics — `-noaccurate_seek` "only applies when encoding"
(github.com/mifi/lossless-cut/pull/13 discussion); accurate seek is the default
when transcoding.

### Note
`setup_start_s` is a nullable column; the coalesce keeps legacy/edge clips
rendering a valid range rather than passing `None` to ffmpeg.

---

## 2026-05-29 — Issue 58: psycopg3 prepared statements + pool sizing for PgBouncer

### What changed
- `db.py:_make_engine()` now passes `connect_args={"prepare_threshold": None}` to
  `create_async_engine`, disabling psycopg3 server-side prepared statements.
- Pool ceiling lowered from `pool_size=10, max_overflow=20` (30/pod) to
  `pool_size=15, max_overflow=5` (20/pod) to stay under the 25-conn PgBouncer
  sidecar; added `pool_recycle=1800`. Values are module constants
  (`_CONNECT_ARGS`, `_POOL_SIZE`, `_MAX_OVERFLOW`, `_POOL_RECYCLE_S`) for testability.
- `docs/DEPLOYMENT.md` records the connection-budget inequality.
- `tests/test_db_engine_config.py` introspects the engine to guard all three.

### Why
psycopg3 auto-prepares a statement after its 5th execution. PgBouncer in
transaction-pooling mode (the chosen production pooler, `DEPLOYMENT.md`) reuses
server connections across clients, so the prepared statement vanishes on the next
checkout → `prepared statement "_pg3_…" does not exist`. CI never catches this
because it connects to Postgres directly. The per-pod pool (30) also exceeded the
25-conn sidecar, causing checkout timeouts at p99 under load.

### Source / evidence
- psycopg3 docs: *"Unless a pooling middleware explicitly declares otherwise…
  disable prepared statements by setting `Connection.prepare_threshold` to `None`."*
  (psycopg.org/psycopg3 — prepared statements).
- SQLAlchemy issue #6467 / discussion #10246 (pooler + prepared-statement handling).

### Alternatives ruled out
- **Rely on PgBouncer ≥1.22 + psycopg ≥3.2 named-prepared support:** couples
  correctness to exact infra versions; a downgrade silently reintroduces the outage.
- **Session-mode pooling:** defeats pooling benefit at hundreds of clients.
- **Drop PgBouncer:** contradicts the documented 10k-scale K8s target.

### Verification status
Code complete + unit-tested. The green-under-load proof (no `prepared statement`
errors at target concurrency) is deferred to a `tests/perf/` Locust run behind a
real PgBouncer in staging — not reproducible in the CI/dev container.

---

## 2026-05-29 — Skill freshness convention + standards SSOT

### What changed
- Created a committed `best-practices` skill (`.claude/skills/best-practices/SKILL.md`)
  to replace the phantom `/best-practices` that CLAUDE.md mandated but did not exist
  on disk. It is process-first/evergreen: it operationalizes the One Rule
  (research current standard live, record in DECISIONS) rather than listing
  perishable "current best" facts.
- Added a freshness convention (`docs/SKILL_FRESHNESS.md`): every `SKILL.md`
  carries `last_verified: YYYY-MM-DD`; a 6th `freshness` gate in `run_layer0.py`
  flags any skill unverified for >90 days (warn-only by default, hard fail under
  `--require-fresh` for the scheduled re-verification job). Added freshness
  (warn-only) to the CI static-gates job.
- Hoisted the Anthropic model id + web_search tool version to `config.py`
  (`ANTHROPIC_MODEL`, `ANTHROPIC_WEB_SEARCH_TOOL`), referenced by all 4 call sites
  (`clip_engine/scoring.py`, `dna/brief.py`, `improvement/brief.py`) instead of
  three hardcoded duplicates; added both to `.env.example`. Closes the
  hardcoded-model-id SEV2 from the assessment.

### Why
A standards skill that bakes perishable facts (model ids, tool/lib versions,
"best library for X") goes stale silently and then gives confident wrong answers —
worse than no skill. The mitigation is to encode *process* (how to find the
current standard) as evergreen, fetch perishable facts live where possible
(pip-audit pulls current CVEs; web_search researches per decision), store the
must-store perishable facts in a single source (config/requirements), and make
staleness a visible CI signal via `last_verified` + the freshness gate. Full
rationale in `docs/SKILL_FRESHNESS.md`.

### Alternatives ruled out
- **Baking the current model id / library recommendations into the skill prose:**
  the exact rot this avoids; the improvement assessment caught `claude-sonnet-4-6`
  duplicated across three files.
- **A hard staleness gate that fails all PRs after 90 days:** would block unrelated
  work; warn-by-default + `--require-fresh` on the scheduled job is the cadence-
  correct posture.
- **Cloning the Claude API surface into a repo skill:** that surface moves fastest;
  delegate to the Anthropic-managed `/claude-api` skill that updates upstream.

---

## 2026-05-29 — Production-assessment harness + quality gates

### What changed
- Added a committed project skill `.claude/skills/production-assessment/`
  (`SKILL.md` + `rubric.md` + `scale-checklist.md` + `subagent-contract.md` +
  `report-template.md` + `scripts/run_layer0.py`) and a `/assess` slash command.
- Added four ratcheted CI gates in `.github/workflows/quality.yml`, all driven by
  the single `run_layer0.py` harness: **mypy** (types), **pytest-cov** (coverage
  floor), **bandit** (SAST), **pip-audit** (dependency CVEs).
- Added `requirements-dev.txt` (pinned), `[tool.mypy|coverage|bandit]` config in
  `pyproject.toml`, `docs/assessment/` register (baselines + per-module findings +
  report history), and a Locust load-test scaffold in `tests/perf/`.
- Un-ignored `.claude/skills/` and `.claude/commands/` in `.gitignore` (session
  state stays ignored; intentional skills/commands are now committed).
- Added one line to the CLAUDE.md Phase-4 checklist requiring the Layer-0 gates
  to be green before an issue closes.

### Why
Assessing a codebase aimed at hundreds of concurrent users needs to be (a)
exhaustive, (b) repeatable, and (c) bounded in context as the repo grows. A
single full-codebase Claude sweep satisfies none of these — it is
non-deterministic, unrepeatable, and its recall drops as the repo grows. The
governing split is **tools provide exhaustiveness; Claude provides judgment**:
deterministic gates run in CI with perfect recall at zero context cost, and the
model is reserved for per-module judgment via parallel subagents that write
findings to disk, so the orchestrator reads short summaries rather than source.
This keeps assessment context flat from 16k LOC upward.

### Tool choices (industry standard checked, 2026)
- **Type checker: mypy** over pyright/ty. `ty` only reached FastAPI in 3/2026 —
  too new for a load-bearing gate; pyright needs Node in CI. mypy is pip-native
  and mypyc-compiled builds are fast. Sources: pydevtools.com type-checker
  comparison; "Migrating from mypy to ty" (FastAPI).
- **SAST: bandit** — AST-based, Python-specific, ~88% issue recall, <5s scans.
  Semgrep (92%, semantic) noted as a future add; heavier and needs rule curation.
  Source: dev.to "Semgrep vs Bandit (2026)".
- **Dependency CVEs: pip-audit** over safety (safety now gates behind an account).
  Critical/high CVEs to be fixed within 7 days. Source: aikido.dev Python tools.
- **Coverage: pytest-cov as a self-baselining ratchet** (regression gate, not an
  absolute bar) so it doesn't red-wall 16k existing LOC. Mutation testing via
  **mutmut** (most-active tool; target score 75%→85%) is cadence-only because it
  is slow. Source: johal.in mutmut 2026; ieeexplore mutation-tool comparison.
- **Load testing: Locust** over k6 — Python-first, reuses the project's JWT/auth
  scheme and is maintained in-language. k6 documented as the alternative for
  >10k RPS/Grafana streaming. Source: dev.to "Best Load Testing Tools 2025".

### Ratchet posture
Gates are seeded permissively in `docs/assessment/baselines.json` and only fail
on regression; `run_layer0.py --update-baseline` captures current reality, then
the targets are tightened over time (bandit_high→0, pip_audit_vulns→0,
mypy_errors→0 then enable `disallow_untyped_defs`). Rationale and steps in
`docs/assessment/README.md`.

### Alternatives ruled out
- **One big Claude sweep**: non-deterministic, unrepeatable, recall degrades with
  size, context blows up — the exact failure mode this design avoids.
- **Strict gates from day one** (mypy --strict, 90% coverage): would block every
  PR against existing code; the ratchet reaches the same end state without a
  flag-day rewrite.
- **Folding the full assessment into every issue's Phase 4**: too heavy for
  day-to-day; instead only the cheap Layer-0 floor-check is per-issue, and the
  deep sweep is the milestone-cadence `/assess`.

---

## 2026-05-28 — Issue 47: Beat-job fairness via `last_analytics_refreshed_at`

### What changed
- Added `creators.last_analytics_refreshed_at: timestamptz NULL` (bundled with
  Issue 43 into alembic revision `d4e5f6a7b8c9`, file renamed to
  `0004_video_done_creator_refreshed.py`).
- Added B-tree index `ix_creators_refresh_order ON creators(last_analytics_refreshed_at, id)`
  to make the daily sweep cheap.
- `_refresh_youtube_analytics_async` now orders creators by
  `Creator.last_analytics_refreshed_at.asc().nulls_first(), Creator.id`.
- On successful per-creator refresh (after `sync_audience_data` returns,
  inside the same transaction as the analytics writes), set
  `creator.last_analytics_refreshed_at = datetime.now(UTC)` before
  `session.commit()`. On `QuotaExhaustedError` the existing
  `await session.rollback()` un-stamps the timestamp by design, so the
  starved creator stays at the front of the queue next cycle.

### Why
The previous loop iterated `select(Creator)` with no `ORDER BY`. On
`QuotaExhaustedError` the loop broke. Quota resets daily; next beat run
started the same scan in the same heap order. For e.g. 50 creators with
quota for ~30 per day, creators 31–50 starved forever — they would never
even have analytics fetched once. Classic FIFO-fairness bug.

The fix is a single nullable timestamp + an `ORDER BY` clause. NULLS FIRST
means newly-connected creators (never refreshed) jump the queue, which
matches user expectation: "I just connected my channel, I expect to see
data fast." Once they're refreshed they stamp and drop to the back; the
oldest stamp goes next.

### Alternatives ruled out
- **`ORDER BY RANDOM()`**: non-deterministic, hard to debug. Probabilistically
  still starves unlucky creators across consecutive runs (any randomized
  scan with a cutoff has a non-zero starvation tail).
- **Round-robin pointer in Redis**: extra distributed state; doesn't survive
  worker restart cleanly; loses the "newly connected creator jumps first"
  property.
- **Process all creators in parallel via Celery groups**: multiplexes the
  quota faster but does nothing for fairness — same starvation curve,
  compressed in time.
- **Per-creator quota allocation (1/N of total)**: punishes power users
  with many videos who legitimately need more quota; doesn't solve the
  "new creator never appears in the scan" failure mode.

### Tradeoffs
- **Partial-refresh starvation (acknowledged)**: if a creator's refresh
  partially succeeds (e.g. 5 of 12 videos processed) and then
  `sync_video_analytics` raises `QuotaExhaustedError`, we rollback the
  whole creator and don't stamp the timestamp. They retry first next run.
  A creator who *always* trips quota mid-refresh would never advance —
  but that's actually correct behavior (no partial credit). Out of scope
  for Issue 47.
- **Migration coupling**: bundled with Issue 43's `videos.ingest_done_at`
  into one alembic revision (`0004_video_done_creator_refreshed.py`) per
  LEFT_OFF's explicit suggestion. Pro: one alembic step at deploy. Con:
  reverting one change reverts both. Both are nullable-additive,
  low-blast-radius, so the coupling is acceptable.
- **No backfill**: existing creators have `last_analytics_refreshed_at IS
  NULL`, which by `NULLS FIRST` puts them at the front on day 1 (tied
  break by `id` — same as today's order). Self-bootstrapping fairness
  after the first daily sweep.
- **Index cost**: tiny B-tree on `(last_analytics_refreshed_at, id)`.
  Bounded by creator count.

### Source / evidence
- Read `_refresh_youtube_analytics_async` at `worker/tasks.py:532–572` and
  confirmed: `select(Creator)` with no `ORDER BY`; `break` on
  `QuotaExhaustedError`; per-creator commit inside the inner try.
- SQLAlchemy `.nulls_first()` documented at
  https://docs.sqlalchemy.org/en/20/core/sqlelement.html#sqlalchemy.sql.expression.nulls_first
- Canonical time-based fairness pattern: Crunchy Data's `SKIP LOCKED`
  job-queue writeups, Stripe's webhook re-delivery scheduler design, every
  CRM batch-syncer paginator.

### Files
- `alembic/versions/0004_video_done_creator_refreshed.py` — added
  `creators.last_analytics_refreshed_at` + `ix_creators_refresh_order`;
  broadened docstring + filename to reflect the bundle.
- `models.py` — `Creator.last_analytics_refreshed_at` Mapped column.
- `worker/tasks.py` — `ORDER BY` clause on the creator SELECT; stamp +
  commit on successful refresh.
- `tests/test_retention_tasks.py` — three new mock-level tests pinning
  the load-bearing contracts: ORDER BY whereclause inspection,
  stamp-on-success, no-stamp-on-quota-exhaustion.
- `tests/test_analytics_fairness_integration.py` — new `integration`-marked
  scenario: 5 creators × 2-budget × 3 cycles → no starvation; verifies
  both attempt sequence and DB timestamp stamping.

---

## 2026-05-28 — Issue 43: Source-media retention clock = ingest completion, not upload

### What changed
- Added `videos.ingest_done_at: timestamptz NULL` (alembic revision `d4e5f6a7b8c9`)
  + partial index `ix_videos_purge_candidates ON videos(ingest_done_at) WHERE
  ingest_done_at IS NOT NULL AND source_uri IS NOT NULL` to keep the hourly purge
  sweep cheap.
- Set `Video.ingest_done_at = datetime.now(UTC)` in `_signals_async` at the same
  point we flip `ingest_status` to `done`. Guarded by `if video.ingest_done_at
  is None:` so a retry of an already-completed task preserves the original
  completion stamp (Celery is at-least-once; without the guard, retries would
  silently extend the retention window).
- Changed `_purge_stale_source_media_async` filter from `Video.created_at <
  cutoff` to `Video.ingest_done_at.is_not(None) AND Video.ingest_done_at <
  cutoff`. Kept the `source_uri IS NOT NULL` predicate.
- Backfill (one-shot in the migration): every existing row with `ingest_status
  = 'done'` AND `ingest_done_at IS NULL` gets `ingest_done_at = created_at`. This
  preserves the pre-migration retention semantics for already-completed videos.

### Why
The previous filter `Video.created_at < cutoff` started the retention clock at
upload time. A video uploaded 30h ago but still mid-ingest (slow Whisper, retry
backoff, beat-cycle race) would have its `source_uri` nulled out from under the
pipeline; the next stage would crash trying to read the file. This is SEV-1
because under any concurrency / queue depth it shows up as flapping ingests
that "just sometimes fail" — exactly the kind of bug that's expensive to
diagnose post-launch.

The new filter gates on a soft-completion timestamp: ingest is "done with
the source" precisely when the signals-build commits successfully. That's the
right moment to start the YouTube ToS retention clock.

### Alternatives ruled out
- **Gate on `ingest_status = IngestStatus.done`**: works, but couples retention
  to a status enum that's also used for failure states. With the timestamp we
  can later say "retain failed videos longer for debugging" without a schema
  change.
- **Bigger retention window (e.g. 72h → 168h)**: pushes the problem out but
  doesn't fix it; a stuck pipeline still races on day 4.
- **Skip purge while a task is in-flight (Redis lock check)**: orthogonal
  mechanism, much more complex, doesn't help the case where a task crashed and
  left `source_uri` set without `ingest_done_at`.
- **Use a `Video.updated_at`**: don't have one, and `updated_at` would tick on
  retries/status flips/score writes — fuzzy semantics for a retention cutoff.

### Tradeoffs
- **Backfill semantics**: existing already-completed videos use `created_at` as
  a stand-in for `ingest_done_at`. Slightly off (the original completion was
  later than upload), but bounded by the ingest pipeline runtime (~minutes)
  and only matters at the edges of the cutoff. Net effect: a handful of
  already-completed videos get a few minutes of extra retention. Acceptable.
- **Failed-ingest rows**: `ingest_done_at` stays NULL for rows with
  `ingest_status = failed`. Those rows are NEVER purged by this sweep. Their
  source media is small (failed ingests = nothing rendered) and they're useful
  for debugging. If they pile up they can be cleaned via a separate retention
  job; out of scope for Issue 43.
- **Idempotency**: the `if video.ingest_done_at is None` guard is load-bearing.
  Without it, Celery's at-least-once redelivery could refresh the timestamp on
  retry, silently pushing the cutoff forward by hours/days.
- **Partial index cost**: adds one B-tree of (`ingest_done_at`) filtered to
  source-still-on-disk rows. Roughly O(videos with source_uri set). At our
  scale this is a few thousand rows max — negligible storage; meaningful
  speedup for the hourly Beat sweep.

### Source / evidence
- Read `_purge_stale_source_media_async` at `worker/tasks.py:491–525` and
  confirmed the bug: filter is `Video.created_at < cutoff`, not gated on
  status. Confirmed `IngestStatus.done` is set exactly once at line 254 inside
  `_signals_async`.
- SQLAlchemy partial index pattern:
  https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#partial-indexes
- Standard pattern across event/job systems: gate retention on a
  "soft-completion" timestamp (Stripe `processed_at`, S3 lifecycle
  `LastModified`, DLQ `last_completed_at`).

### Files
- `alembic/versions/0004_video_ingest_done_at.py` — schema + backfill +
  partial index.
- `models.py` — `ingest_done_at` Mapped column on `Video`.
- `worker/tasks.py` — `datetime` added to top-level import; `_signals_async`
  stamps `ingest_done_at` under the NULL guard; `_purge_stale_source_media_async`
  filter swapped.
- `tests/test_retention_tasks.py` — semantic-aligned existing tests
  (`created_at` → `ingest_done_at` on mocks); new `test_purge_filter_gates_on_ingest_done_at`
  inspects the SQL `whereclause` to pin the new predicate; new
  `test_signals_async_stamps_ingest_done_at_when_null` +
  `test_signals_async_preserves_ingest_done_at_on_retry` pin the idempotent
  write contract.
- `tests/test_purge_integration.py` — `@pytest.mark.integration` real-DB
  scenario: done-100h purged, in-progress-100h preserved, done-1h preserved.
- `docs/COMPLIANCE.md` — retention-clock row updated to reflect the new
  semantic for the YouTube ToS posture.

---

## 2026-05-28 — Issue 39: Celery event-loop strategy

### What changed
- Replaced per-task `asyncio.run(...)` with a per-worker-process singleton event loop
  installed by the `worker_process_init` Celery signal.
- Added `db.recreate_engine()` and `db.dispose_engine()` so the SQLAlchemy async engine
  + asyncpg pool can be rebound to the worker child's loop after fork, and cleanly
  disposed on `worker_process_shutdown`.
- Added `worker.celery_app.run_async(coro)` — used by every task in `worker/tasks.py`
  (11 sites) instead of `asyncio.run`. Falls back to `asyncio.run` when no worker loop
  is installed (unit-test invocation path).
- `worker/tasks.py` now does `import db` and uses `db.AsyncSessionLocal(...)` so that
  rebinding the module-global sessionmaker in `db.recreate_engine()` is visible to
  task bodies at call time (`from db import AsyncSessionLocal` would capture the
  stale reference).

### Why
Every Celery task used to call `asyncio.run(_some_async(...))`, which creates a fresh
event loop per task. The first task in a worker process would bind the engine's
asyncpg pool to its loop; subsequent tasks would receive a *different* loop and hit
the classic `Future attached to a different loop` errors plus pool churn (each loop
discarded, connections re-handshaked). Under concurrent load this was a SEV-1 because
it manifests as intermittent worker failures rather than a single reproducible bug.

The fix pins one loop per worker process for the worker's lifetime and binds the
engine to it once. This is the canonical FastAPI + Celery + async-SQLAlchemy pattern;
SQLAlchemy's own docs spell out that async engines must be created *after* fork
because the asyncpg connection pool cannot survive across processes.

### Alternatives ruled out
- **`celery-pool-asyncio` / `celery-aio-pool`**: third-party pool replacements. Smaller
  community, replace the entire pool model, and unnecessary — our concurrency model is
  per-process prefork and we don't need cooperative I/O multiplexing inside a task.
- **`asgiref.async_to_sync`**: caches a loop per thread but does not address the
  engine-binding-on-fork problem. Same bug class would resurface.
- **Lazy `get_engine()` inside every coroutine**: scatters the fix across every task
  body and makes the contract implicit; one init signal is far easier to audit.
- **`gevent` / `eventlet` worker pool**: would require monkey-patching the entire
  stack; out of scope.

### Tradeoffs
- Each worker child holds a long-lived loop + pool. Trivial memory cost vs. eliminating
  the pool-rebind cost on every task.
- Engine pool sizing budget is unchanged: `concurrency × (pool_size + max_overflow)`,
  currently `concurrency × 30`. If we raise Celery concurrency, we must size the
  Postgres `max_connections` accordingly. Not a regression — the pre-fix code had the
  same upper bound; it just churned the pool more.
- `worker_process_init` calls `db.recreate_engine()` after fork. We use
  `engine.sync_engine.dispose(close=False)` to abandon (not close) any inherited
  parent connections so we don't yank file descriptors out from under the parent.
  In practice the parent has no open connections at fork time (it only imports the
  modules), but this is the SQLAlchemy-blessed safe default.

### Source / evidence
- SQLAlchemy 2.0 docs — "Using asyncio with multiprocessing":
  https://docs.sqlalchemy.org/en/20/core/pooling.html#using-connection-pools-with-multiprocessing-or-os-fork
- Celery worker signals reference:
  https://docs.celeryq.dev/en/stable/userguide/signals.html#worker-process-init
- Prior incident pattern: `Future attached to a different loop` is the symptom called
  out in Issue 39's spec; verified the cause by reading `worker/tasks.py:49–135` and
  `db.py:8` before the fix.

### Files
- `db.py` — added `_make_engine`, `recreate_engine`, `dispose_engine`.
- `worker/celery_app.py` — singleton `_LOOP`, `run_async`, init/shutdown signal hooks.
- `worker/tasks.py` — 11 × `asyncio.run` → `run_async`, 16 × `AsyncSessionLocal` →
  `db.AsyncSessionLocal`.
- `tests/test_celery_event_loop.py` — pins loop-reuse, fallback, init/shutdown,
  engine-rebind invariants (5 tests).
- `tests/test_retention_tasks.py`, `tests/test_pipeline_trigger.py`,
  `tests/test_oauth_lifecycle.py` — updated patch targets from `worker.tasks.*` to
  `db.AsyncSessionLocal` / `worker.tasks.run_async` to match the new import surface.

---

## 2026-05-28 — Issue 37: External SDK Timeouts + Retry-with-Backoff

### Anthropic SDK (`anthropic==0.40.0`)

**What**: Replaced per-call `Anthropic(...)` / `AsyncAnthropic(...)` construction in `dna/brief.py`, `improvement/brief.py`, and `clip_engine/scoring.py` with module-level singletons (`_ANTHROPIC`) constructed once from `config.settings`. Configured `timeout=httpx.Timeout(60.0, connect=10.0)` and `max_retries=2`. For `improvement/brief.py`, the web_search call uses `_ANTHROPIC.with_options(timeout=120.0)` per-call because web_search tool agentic loops routinely exceed 60s.

**Why**: The Anthropic Python SDK docs (sdk.anthropic.com/python) recommend constructing the client once and reusing it. Per-call construction wastes connection pool setup on every invocation. The 60s read timeout covers standard Claude calls; 120s override on the web_search path is needed because the tool loop typically takes 30–90s per the Anthropic docs on `web_search_20250305`. connect_timeout of 10s is an industry-standard value for TLS handshakes. `max_retries=2` uses the SDK's built-in exponential backoff on transient 529/500 errors.

**Source**: Anthropic SDK docs — `httpx.Timeout`, `max_retries`, `with_options`; Anthropic web_search tool docs noting agentic loop latency.

### Stripe SDK (`stripe==11.4.0`)

**What**: Added `stripe.max_network_retries = 3` at module level in `billing/stripe_client.py` and promoted `StripeClient` to a module-level singleton `_STRIPE`.

**Why**: Stripe's official Python library docs state that `max_network_retries` enables automatic retry with exponential backoff on 429 and 5xx errors. The default is 0 (no retries). Setting 3 is the Stripe-recommended value for production. The default 80s socket timeout is appropriate for Checkout session creation and is not overridden.

**Source**: Stripe Python library docs — `stripe.max_network_retries`; Stripe best practices guide.

### Voyage AI (`voyageai==0.3.2`)

**What**: Added lazy-initialized module-level singleton `_VOYAGE` (via `_voyage()` accessor) in `dna/embeddings.py` with `timeout=30`. Wrapped embedding calls in a `_embed()` function decorated with `@tenacity.retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))`. Added `tenacity==9.1.4` to `requirements.txt`.

**Why**: The voyageai SDK does not support built-in retries. Tenacity is the Python community standard for retry-with-backoff (used by Google, LangChain, etc.). Exponential backoff with min=1s/max=10s is the standard pattern for rate-limit-friendly API retries. The singleton is lazy (not eager at import time) because voyageai.Client validates the API key at construction, which would fail in test environments without `VOYAGE_API_KEY` set.

**Source**: Tenacity docs (tenacity.readthedocs.io); voyageai Python client source (`voyageai/_base.py`).

### boto3 / Cloudflare R2 (`boto3==1.35.54`)

**What**: Replaced per-call `boto3.client(...)` with a lazy module-level singleton `_R2` (via `_r2()` accessor) in `worker/storage.py`. Configured `botocore.config.Config(retries={"mode": "adaptive", "max_attempts": 5}, connect_timeout=10, read_timeout=60)`.

**Why**: boto3 docs recommend reusing the client to share the connection pool. Adaptive retry mode (botocore docs) uses a token bucket to avoid retry storms on throttling; `max_attempts=5` is the botocore recommended value for production S3 workloads. `connect_timeout=10` / `read_timeout=60` match AWS SDK best practices. The singleton is lazy because boto3 validates the endpoint URL at construction, which fails if `R2_ACCOUNT_ID` is empty (test environment).

**Source**: botocore Config docs; AWS SDK best practices guide for S3 retry configuration.

### Deepgram / WhisperX (`ingestion/transcribe.py`)

**What**: No change made. WhisperX is local-only (no network timeout relevant). The Deepgram fallback path uses `deepgram-sdk` which is commented out of `requirements.txt` and unreachable in all environments. There is no httpx-based fallback path.

**Why**: Implementing a timeout on an unreachable code path would be dead code. Noted here to close the loop on the Issue 37 audit.

**Date**: 2026-05-28

---

## 2026-05-25 — Project Kickoff Decisions

### North Star Sentence

**What**: Settled on the north star: *"The only AI editor that truly knows your channel —
it learns your style from your own analytics, adapts as you evolve, and keeps you ahead of
the algorithm."*

**Why**: The product is broader than clipping — it's a full analyzer + advisor that adapts to
the creator's evolving style and keeps them informed about algorithm changes. The sentence must
communicate the personalization flywheel, not just the clip output.

**Source**: Creator (owner) input, 2026-05-25.

---

### Review UI: Single Player + Next

**What**: The review interface is a single-player + Next button, not a swipe-stack.

**Why**: Single-player makes precision trim handle interaction easier and more reliable.
Swipe-stack UX is faster for bulk review but sacrifices the trim-delta signal, which is the
strongest *timing* feedback. Trim handles are the visual centerpiece.

**Source**: Creator input, 2026-05-25.

---

### Pricing Model: Usage-Based Tiers (Research Pending)

**What**: Pricing is usage-based with tiered subscription floors, similar to Anthropic's own
model. A flat "low cap" monthly plan would frustrate prolific creators. A pure per-video model
adds friction.

**Why**: Creators' output volume varies enormously. A tiered usage model (e.g., base plan
includes N tokens/videos, then pay-as-you-go overage) aligns cost with value and doesn't
block high-output creators.

**Research needed**: Best practices for usage-based SaaS pricing + Stripe metered billing
implementation. Must be decided before public launch. Stripe + usage metering is the
industry-standard path.

**Source**: Creator input, 2026-05-25. Research not yet completed — see `docs/SOT.md` Known
Production Gaps.

---

### Production Deployment: GKE Autopilot + Helm + KEDA

**What**: GKE Autopilot is the production K8s platform. Helm charts in
`deploy/charts/creatorclip/`. KEDA ScaledObject autoscales Celery workers on Redis
queue depth. PgBouncer sidecar handles connection pooling. Cloud SQL for PostgreSQL 16
(pgvector enabled). GCP Secret Manager + External Secrets Operator for secrets.

**Why GKE Autopilot over EKS/DO**:
- No node management — Google provisions and upgrades nodes automatically
- Cloud SQL for PostgreSQL 16 has first-class pgvector support (vs. RDS which requires
  custom parameter groups and is slower to enable extensions)
- GCP Secret Manager + Workload Identity = cleanest managed-secrets story without extra agents
- Spot node pools for transcription workers available when we add WhisperX
- Familiarity: same provider as Cloudflare Tunnel integration already in dev

**KEDA vs HPA-only**: HPA on CPU is insufficient for Celery — a backlogged queue does
not spike CPU until workers are already overwhelmed. KEDA's `redis-listLength` trigger
scales on actual work queued, providing proactive scaling.

**PgBouncer sidecar vs RDS Proxy**: Sidecar eliminates the network hop to a separate
pooler, is free, and transaction mode allows up to 25 upstream connections per pod
(→ 750 at 30 pods, well within Cloud SQL's 1,000 limit).

**Source**: Compared providers on pgvector support, managed node overhead, secrets
integration, and community KEDA+Celery patterns. 2026-05-26.

---

---

### OAuth HTTP Calls: httpx Instead of google-auth-oauthlib

**What**: The OAuth token exchange, token refresh, userinfo, and YouTube Channels calls are
implemented directly with `httpx.AsyncClient` rather than using `google-auth-oauthlib` /
`google-api-python-client`.

**Why**: `google-auth-oauthlib` is synchronous — using it in an async FastAPI handler requires
`asyncio.run_in_executor()` boilerplate. The OAuth endpoints are simple POST/GET calls that
`httpx` handles natively in 3–4 lines each. Fewer dependencies, fully async, and easier to
test (patch the `_call_*` helpers rather than monkey-patching Google internals).

**Source**: httpx docs; FastAPI async best practices. Confirmed: no Google library provides
a first-party async implementation as of 2026-05.

---

### Numeric Thresholds Set as Defaults

**What**: The following defaults were set based on the kickstart document's suggested values:

| Variable | Default | Rationale |
|----------|---------|-----------|
| `CLIPS_PER_VIDEO_DEFAULT` | 8 | Enough candidates to cover diverse moments without overwhelming review |
| `MIN_VIDEOS_FOR_DNA` | 10 | Minimum for meaningful top/bottom performer analysis |
| `MIN_SHORTS_FOR_DNA` | 5 | Minimum for Shorts-specific pattern extraction |
| `PERSONALIZATION_THRESHOLD_LABELS` | 20 | Minimum feedback volume for reranker to produce meaningful signal |

All are environment-configurable and can be tuned once real usage data exists.

**Source**: Kickstart document defaults; no external research needed (tunable post-launch).

---

### Postgres Docker Image: pgvector/pgvector:pg16

**What**: Using `pgvector/pgvector:pg16` in docker-compose instead of `postgres:16` + manual
extension install.

**Why**: The official pgvector Docker image pre-installs the extension, eliminating the
`CREATE EXTENSION` step that frequently trips up fresh setups. Same underlying Postgres 16;
no functional difference.

**Source**: pgvector GitHub README recommendation, standard practice.

---

### Transcription Backend: Deepgram as MVP Default

**What**: `TRANSCRIPTION_BACKEND` defaults to `"deepgram"` (hosted API). WhisperX remains
available via `TRANSCRIPTION_BACKEND=whisperx` for self-hosted GPU deployments. The
`DEEPGRAM_API_KEY` field is already in Settings (optional, empty default).

**Why**: No GPU infrastructure exists for the MVP. Deepgram's Nova-3 model provides
word-level timestamps, speaker diarization, and competitive accuracy without the operational
overhead of managing a GPU box or container. WhisperX is preserved as a config-selectable
path for production cost optimisation once volume justifies the GPU spend.

**Source**: Resolves the "Transcription compute" open research item. Decision: hosted API
for MVP, self-hosted as a future cost lever. 2026-05-25.

---

### asyncio.run() in Celery Tasks

**What**: Celery task functions (`ingest_video`, `transcribe_video`, `build_signals`) use
`asyncio.run()` to call async SQLAlchemy helpers. Each task creates a fresh event loop
per invocation.

**Why**: Celery workers are process-based and synchronous by default. The project's
SQLAlchemy setup is async-only (`create_async_engine`). The alternatives — a parallel sync
engine or `nest_asyncio` — add more complexity. `asyncio.run()` is the documented SQLAlchemy
approach for non-async call sites, and Celery workers run in their own processes so there is
no event-loop conflict.

**Source**: SQLAlchemy async docs "Using Asyncio" section; Celery docs recommend keeping
task functions synchronous. 2026-05-25.

---

## 2026-05-26 — Billing: Minute Packs (replaces subscription tiers)

**What**: Billing model is pre-paid minute packs, not subscriptions. `Creator.plan_tier` and
`Creator.subscription_status` replaced with `Creator.minutes_balance` (int) and a
`minute_packs` ledger table. Stripe Checkout in one-time payment mode — no subscriptions,
no Billing Meters. Five purchasable packs (Starter 200 min → Studio 5,000 min) with
programmatically-verified volume discounts. 60-minute free trial granted on first login.
Minutes deducted atomically at ingest via `UPDATE … WHERE minutes_balance >= X RETURNING`.

**Why**: Subscriptions require monthly commitment — a poor fit for creators who post
episodically. Minute packs let creators pay for exactly what they use and never expire,
which is a better conversion funnel ("try 60 free minutes, buy more when you need them").
One-time Stripe Checkout is also significantly simpler to implement than subscriptions
(no Customer Portal, no dunning, no invoice lifecycle).

**Source**: Product decision, 2026-05-26. Feature branch `claude/zealous-wozniak-5KVb7`
merged into main.

---

## 2026-05-26 — Beta deployment: VM + Docker Compose, not Kubernetes

**What**: BETA_DEPLOYMENT phase (Issues 23–28) runs on a single cloud VM (DigitalOcean
Droplet, 4 vCPU / 8 GB RAM) with Docker Compose + Cloudflare Tunnel, not Kubernetes.
This is a scoped exception to the "Docker Compose = dev only" stance in `docs/SOT.md`.

**Why**: Kubernetes is right for 10k+ scale but adds unnecessary operational complexity
for a close-friends beta with < 10 users. The existing CI/CD pipeline (`deploy.yml`)
already handles image build, SSH deploy, and DB migration — no K8s tooling needed for
beta. `docs/SOT.md` still targets GKE Autopilot for production (Issue 22 Helm charts
are ready); this is a scoped beta exception only.

**Source**: Practical deployment gap analysis, 2026-05-26. Production deployment phase
(Issues 29–30) retains the Kubernetes target.

---

## 2026-05-26 — Clip engine: extend end_s for early-peak candidates

**What changed**: `clip_engine/candidates.py` — `end_s` now computed as
`min(duration_s, max(peak_s + POST_PEAK_S, setup_start_s + MIN_CLIP_S))` instead of
`min(duration_s, peak_s + POST_PEAK_S)`.

**Why**: Adversarial eval fixture `peak_very_early` surfaced a bug: when a retention spike
occurs near t=0 (e.g. 12s), the setup-to-post-peak window is only ~27s, below `MIN_CLIP_S`
(30s). The candidate was silently discarded. The fix extends `end_s` just enough to meet the
minimum, so early-video hooks are never dropped.

**Source**: `tests/eval/scenarios/peak_very_early.yaml` — engine returned 0 candidates.
Debug confirmed `end_s - setup_start_s = 27.5 < 30.0`. 2026-05-26.

---

## 2026-05-27 — Issue 31: Operability kit (secrets registry, preflight doctor, deploy hardening, auto-heal)

### Secrets storage: plain gitignored `.env` + registry (not SOPS+age)

**What**: Secrets are kept in gitignored `.env` files (local + VM `/opt/autoclip/.env`, chmod 600),
documented in a single registry at `docs/SECRETS.md`. SOPS+age (encrypted-in-git) was considered
and deferred.

**Why**: For a <10-user close-friends beta on a single VM, plain `.env` with strict file
permissions is the industry-accepted baseline and matches the existing setup with zero new
tooling. SOPS+age adds a keypair to manage and deploy-step changes — robustness we don't need
until multi-operator or compliance requirements appear. Logged as the explicit upgrade path.

**Source**: Web research on single-VM Docker Compose secret management (GitGuardian; Docker docs;
cmmx.de SOPS/age guide), 2026-05-27. Owner chose plain `.env` + registry.

### Pre-existing bug fixed: `routers/clips.py` imported deleted `billing.tiers`

**What changed**: `routers/clips.py` imported `require_render` from `billing.tiers`, a module the
minute-packs rewrite (commit `41016e6`) deleted. The render endpoint now uses
`Depends(get_current_creator)` + `await check_positive_balance(...)`, matching the minute-packs
guard already used in `routers/videos.py`.

**Why**: The stale import meant `import main` raised `ModuleNotFoundError` — the app could not
start at all, the full test suite could not collect, and any container built from `main` would
crash on boot (a likely real cause of "deploy fails / times out"). Minutes are deducted at ingest
(`worker/tasks.py`), so a render needs only a positive-balance guard, not a second deduction.

**Source**: Discovered while running `pytest` during Issue 31 Phase 3. The breaking commit was the
unpushed local `main` commit; this fix lands on top before any push. 2026-05-27.

### Image build: amd64 only

**What**: `docker-publish.yml` builds `linux/amd64` only (was `linux/amd64,linux/arm64`).

**Why**: The DigitalOcean droplet is x86_64. The arm64 build was pure wasted CI time — roughly
doubling image build duration for an architecture nothing runs. Contributed to slow deploys.

**Source**: Deploy-time analysis, 2026-05-27. If an arm64 host is ever added, restore the matrix.

### Cloudflared in Compose + no host port + auto-heal (beta VM)

**What**: `docker-compose.prod.yml` now (a) runs `cloudflared` as a service, (b) removes the app's
`ports: 80:8000` host mapping, (c) drops the dev `--reload` from the app command, (d) adds
liveness `healthcheck`s to `app` and `worker`, and (e) adds a `willfarrell/autoheal` sidecar that
restarts containers labelled `autoheal=true` when their healthcheck goes unhealthy. The tunnel's
public-hostname ingress must target `app:8000` (Compose DNS), documented in `docs/ACCESS.md`.

**Why**: Docker has no native restart-on-unhealthy (confirmed 2026); `autoheal` + per-service
healthchecks is the standard Compose pattern. Routing inbound traffic only through the tunnel
satisfies Issue 23's "no open inbound ports" acceptance and removes the `localhost:80` vs
`app:8000` ambiguity that breaks tunnels. App healthcheck is liveness-only so a transient Postgres
blip doesn't trigger an app restart loop.

**Source**: Web research on Docker Compose auto-healing (willfarrell/autoheal; oneuptime 2026
guides), 2026-05-27.

## 2026-05-28 — Issue 44: Auth boundary hardening

### `get_current_creator`: catch ValueError/KeyError alongside PyJWTError

**What changed**: `auth.py` — `uuid.UUID(payload["sub"])` moved inside the existing
`try/except`, with `(ValueError, KeyError)` added to the caught exception types. A malformed
`sub` (non-UUID string, missing key) now returns 401 "Invalid or expired session" instead of
propagating as a 500.

**Why**: The call was outside the `try` block, so any `ValueError` from `uuid.UUID()` or
`KeyError` from a missing `sub` key fell through to the global exception handler and surfaced
as a 500 with a stack trace in development mode. Per defence-in-depth, any invalid token
payload should yield 401 — not leak error details.

**Source**: Code review of `auth.py:43`; Python `uuid.UUID` docs confirm `ValueError` on
malformed input. 2026-05-28.

---

### `DELETE /me`: add 5/hour rate limit

**What changed**: `routers/auth.py` — `@limiter.limit("5/hour")` added to the
`delete_account` handler. `request: Request` added to handler signature (required by
slowapi for key extraction).

**Why**: The right-to-erasure endpoint had no rate limit. An attacker with a stolen session
could spam it; even accidental repeated clicks should be bounded. 5/hour is generous for
legitimate use (account deletion is a one-time action) and tight enough to prevent abuse.
The existing `limiter` from Issue 18 already uses `_creator_key` (JWT sub → creator UUID),
which gives correct per-creator isolation.

**Source**: slowapi docs on `@limiter.limit`; Issue 18 pattern in `routers/videos.py`.
2026-05-28.

---

### `crypto.py`: MultiFernet + typed TokenDecryptError

**What changed**: `crypto.py` — `_fernet()` now returns `MultiFernet([primary])` when no
previous key is configured, and `MultiFernet([primary, previous])` when
`TOKEN_ENCRYPTION_KEY_PREVIOUS` is set. `decrypt()` catches `cryptography.fernet.InvalidToken`
and re-raises as the new typed `TokenDecryptError`. `config.py` adds
`TOKEN_ENCRYPTION_KEY_PREVIOUS: str | None = None`. `.env.example` documents the rotation
workflow.

**Why MultiFernet over Fernet**: `MultiFernet.encrypt()` always uses the first (primary) key;
`MultiFernet.decrypt()` tries keys in order. This enables zero-downtime key rotation: set
`TOKEN_ENCRYPTION_KEY_PREVIOUS = old key`, run `scripts/rotate_token_key.py` to re-encrypt
all rows under the new primary, then clear `TOKEN_ENCRYPTION_KEY_PREVIOUS`. During the window
between setting the new primary and completing re-encryption, both old and new tokens are
readable. A single-key `MultiFernet([primary])` is functionally identical to `Fernet(primary)`
so there is no behaviour change when no previous key is configured.

**Why TokenDecryptError**: callers (`routers/auth.py`, `youtube/oauth.py`) were inconsistently
handling raw `cryptography.fernet.InvalidToken` — some caught it, some didn't. A project-level
typed exception makes the contract explicit and prevents internal cryptography exceptions from
leaking through unhandled.

**Source**: `cryptography` library docs on `MultiFernet`; Python exception-hierarchy best
practices. Confirmed: `MultiFernet` ships in the same `cryptography` package already pinned
in `requirements.txt`. 2026-05-28.

---

### Preflight doctor as the deploy gate

**What**: New `scripts/doctor.py` validates presence + format + live reachability of every secret
and prints a **redacted** status table (length + last-4 only). `config.py` keeps its fail-fast on
*missing* required vars; the doctor adds *validity* and *connectivity*. `deploy.yml` runs
`python scripts/doctor.py` after image pull and **before** migrations/cutover, so a bad secret
fails the deploy early with safe, visible output rather than a silent crash.

**Why**: The owner's core pain was being unable to see *why* a deploy failed without exposing
secrets. A redacted doctor is the standard "preflight/doctor" answer; pydantic-settings only
covers presence.

**Source**: Web research on pydantic-settings validation patterns, 2026-05-27.

---

## 2026-05-28 — Issue 32: Pin `starlette` explicitly to defend against transitive shadowing

### What changed
`requirements.txt` now pins `starlette==0.41.3` directly, in addition to the existing
`fastapi==0.115.4` pin. Previously starlette was an unpinned transitive dep.

### Why
On 2026-05-28 the test suite failed to collect with
`TypeError: Router.__init__() got an unexpected keyword argument 'on_startup'`.
Root cause: the installed environment had drifted to `starlette==1.1.0`, the published
upstream **on the same day** (starlette 1.2.0 was released earlier in the day; 1.1.0 was
2026-05-23). `starlette` graduated from ZeroVer to 1.0 on 2026-03-22, with the package
moving from `encode/starlette` to `Kludex/starlette` on PyPI (Marcelo Trylesinski now
primary maintainer; Tom Christie co-maintainer). The 1.x line **removed**
`on_startup`/`on_shutdown` from `Router.__init__`, which FastAPI 0.115.x still forwards.

FastAPI 0.115.4 declares `starlette>=0.40.0,<0.42.0` in its `Requires-Dist`, so the broken
install can only happen on an env where pip ran without that constraint applied (drift via
an unrelated `pip install` that didn't reference the requirements file). The explicit pin
on starlette closes that drift path.

### Why not pip-tools / uv lockfile right now
The 2026 industry-standard answer for production Python dep management is `uv` with
`uv.lock` (cross-platform, auto-maintained, 10–100× faster than pip-tools), or `pip-tools`
(`requirements.in` → compiled `requirements.txt`) as the lower-friction alternative. Both
would prevent this category of bug structurally. We're deferring the tooling migration:
a hotfix for an SEV-0 collection failure shouldn't carry a CI/Dockerfile/dev-workflow
overhaul with it. **Re-evaluate when production K8s deployment lands (Issue 30)** — at
that point the operational case for a lockfile is unambiguous.

Until then, the rule is **explicit `==` pinning of every runtime-affecting transitive dep
in `requirements.txt`** as the minimum bar.

### Source / evidence
- `python3.12 -m pip show fastapi` reports `Requires-Dist: starlette<0.42.0,>=0.40.0`
- FastAPI 0.115.4 `pyproject.toml` on GitHub confirms the same constraint
- PyPI `starlette` project page (2026-05-28): latest 1.2.0, source repo
  `https://github.com/Kludex/starlette`, maintainers Marcelo Trylesinski + Tom Christie
- Industry references on 2026 dependency-management practice: Astral `uv` docs;
  Real Python "uv vs pip"; Cuttlesoft "Python Dependency Management in 2026";
  pydevtools handbook on pip-tools

### Verification
With `starlette==0.41.3` pinned and `pip install -r requirements.txt` re-run in a clean
venv, `pytest -q` runs the full suite to **313 passed, 7 deselected** (the 7 are
integration-marked tests excluded by `pytest.ini`'s `-m "not integration"`).

---

## 2026-05-28 — Issue 34: Per-video idempotency for minute deduction (SAVEPOINT + UNIQUE)

### What changed
A new `minute_deductions` ledger table (migration `0003_minute_deductions.py`,
model `MinuteDeduction`) is added with **`UNIQUE(video_id)`** as the idempotency key.
`billing.ledger.deduct_minutes(creator_id, duration_s, session)` is replaced by
`deduct_for_video(video_id, creator_id, duration_s, session)`, and `worker/tasks._ingest_async`
calls the new function with `video.id` + `video.creator_id`.

The new function:
1. Fast-checks for an existing deduction row (skip without opening a savepoint if found).
2. Opens `session.begin_nested()` (SAVEPOINT) wrapping two writes:
   - INSERT into `minute_deductions` + `session.flush()` to surface UNIQUE conflicts now.
   - `UPDATE creators SET minutes_balance = minutes_balance - n WHERE id = :cid AND minutes_balance >= n RETURNING`.
3. On `IntegrityError` (concurrent retry won the race) → roll back savepoint, return 0.
4. On insufficient balance → raise `HTTPException(402)` inside the savepoint, which auto-rolls back the INSERT.

### Why
Celery is configured with `task_acks_late=True` in `worker/celery_app.py`, which makes
delivery at-least-once: if a worker crashes after the deduction commits but before
acking the message, the broker redelivers and the task runs again. The previous
`deduct_minutes` had no per-video key — each retry just re-decremented the balance,
charging the creator 2–4× for a single video. The `UNIQUE(video_id)` constraint moves
the idempotency guarantee from "the application remembers" to "the database refuses",
which is the only durable place for a money primitive.

### Why a ledger table instead of `Video.minutes_charged_at`
`MinutePack` (existing) ledgers **grants in**. `MinuteDeduction` (new) ledgers **costs
out**. `Creator.minutes_balance` is the running total of both. This is the symmetric
design used by every customer-facing billing system (Stripe usage records, AWS billing,
Adyen). It also lets us answer "show my usage history for the last 30 days" with one
indexed query — `Video.minutes_charged_at` would have lost that audit trail.

### Why SAVEPOINT (`session.begin_nested`)
Two writes (deduction record + balance decrement) must succeed atomically. SAVEPOINT
makes them an undo unit *inside* the caller's larger transaction — the caller can
continue doing other work in the same transaction even when our two writes roll back.
This is the SQLAlchemy-2.0-async idiomatic pattern for "atomic sub-operation within
a larger flow."

### Industry standard checked
- **Stripe Idempotency-Key pattern** — store key + result on first call; replay returns
  stored result. The `MinuteDeduction.video_id UNIQUE` is the same pattern with
  `video_id` as the natural opaque key.
- **AWS "Designing Idempotent APIs"** — same model: client supplies an idempotency token,
  server uses a unique constraint to short-circuit duplicates.
- **Celery docs** explicitly state task idempotency is the caller's responsibility;
  `task_acks_late=True` + worker crashes make duplicates a *normal* occurrence, not an
  edge case.
- **Postgres UNIQUE + SAVEPOINT** vs. application-level locking — UNIQUE is the
  database's natural primitive when a key exists. We use both: UNIQUE for the
  idempotency guarantee, SAVEPOINT for atomicity between the two writes.

### Refund-on-permanent-failure deferred
If `_ingest_async` eventually exhausts all Celery retries after the deduction lands,
the creator paid for a permanently-failed ingest. That refund policy is a product
decision (refund threshold? automatic vs. support-initiated?) and is filed as
**Issue 57** in `docs/issues.md`. Today's exposure is small — ingest failures are
observable in logs and support can manually refund via `grant_minutes`.

### Verification
- `pytest -q`: **311 passed, 13 deselected** (was 313/9 — net -2 mocked deduct_minutes
  unit tests, +4 real-DB integration tests in `tests/test_billing_idempotency.py`).
- Integration tests assert: (a) sequential retry is idempotent, (b) two concurrent
  coroutines for the same video_id charge exactly once, (c) insufficient balance leaves
  zero ledger rows, (d) deduction record carries minutes + duration + timestamp.

### Source / evidence
- Stripe Idempotency docs; AWS Best Practices "Designing Idempotent APIs"
- SQLAlchemy 2.0 async docs: "Using SAVEPOINT with begin_nested"
- Celery docs: at-least-once delivery + `task_acks_late`
- Existing project precedent: `MinutePack` grants ledger (Issue 21)

---

## 2026-05-28 — Issue 42: ffmpeg/subprocess timeout formula

### What changed
Every `subprocess.run` call in `clip_engine/render.py` now has an explicit `timeout=`:

- `_run(cmd, label, timeout_s=120.0)` — optional float arg, passed directly to
  `subprocess.run(timeout=timeout_s)`; catches `subprocess.TimeoutExpired` and re-raises
  as `RuntimeError(f"ffmpeg {label} timed out after {timeout_s}s")`.
- `_frame_dimensions` — direct `subprocess.run(..., timeout=30)` hardcoded; ffprobe
  reads only container headers and should return in milliseconds on a healthy file.
- `_extract_keyframe` — threads `timeout_s: float = 120.0` through to `_run` so callers
  can pass the same budget as the render.
- `render_clip_file` — computes `render_timeout_s = max(120.0, duration * 4)` and passes
  it to both `_extract_keyframe` and the final render `_run` call.

### Timeout formula: `max(120, clip_duration_s * 4)`

**Why 4×**: libx264 `fast` preset on 1080p encodes at approximately real-time speed on
modern consumer hardware (i7/Ryzen with AVX2). 4× gives 3 full "real-time equivalents" of
headroom above the encode itself, covering disk I/O, container muxing, startup overhead,
and moderate system load. A 30s clip → 120s ceiling (floor kicks in). A 60s clip → 240s.
A 90s clip → 360s.

**Why floor at 120s**: Very short clips (< 30s) would get absurdly tight budgets with 4×
alone (e.g. a 10s clip would get only 40s). 120s is ample for any short ffmpeg invocation
regardless of clip length and matches the existing `LLM_TIMEOUT_SECONDS` default, making
it the project's "standard slow-operation timeout".

**Why ffprobe = 30s hardcoded**: ffprobe reads only the container header — it finishes in
milliseconds on any non-corrupt file. 30s is already 2–3 orders of magnitude more generous
than needed; threading the render timeout through would be misleading (the ffprobe call is
not proportional to clip length).

### What the error surfaces to
`_run` raises `RuntimeError` on timeout. The Celery render task's existing error handler
catches `RuntimeError` and sets `clip.render_status = failed`. No new error handling path
was needed.

### Source / evidence
- Python docs: `subprocess.run(..., timeout=N)` raises `subprocess.TimeoutExpired` after N
  seconds, which also sends `SIGKILL` to the child process.
- ffmpeg wiki on encode speed: "fast" preset encodes near 1× real-time for 1080p H.264 on
  modern x86 CPUs.
- Project precedent: `LLM_TIMEOUT_SECONDS` defaults to 120s in `config.py`.

---

## 2026-05-28 — Issue 41: Replace pickle with joblib + restricted unpickler allowlist

### What changed
`preference/model.py` — `to_bytes` / `from_bytes` now use **joblib** for serialisation
instead of raw `pickle`.  A new `_RestrictedUnpickler` class (subclass of
`joblib.numpy_pickle.NumpyUnpickler`) overrides `find_class` to enforce an explicit
allowlist of permitted `(module, name)` pairs.  `from_bytes` temporarily patches
`joblib.numpy_pickle.NumpyUnpickler` with `_RestrictedUnpickler` for the duration of
the `joblib.load` call, then restores the original.

No schema change — `preference_models.weights_blob` remains `bytes`.

### Why joblib over raw pickle
joblib is sklearn's officially documented serialisation format:
> "joblib.dump / joblib.load — use this for sklearn estimators as it handles
> large numpy arrays more efficiently than pickle" — scikit-learn User Guide §Model
> persistence.

It is already a transitive dependency (`scikit-learn → joblib`), so no new package
is needed.  Blobs written by `joblib.dump` are forward-compatible across
minor sklearn/joblib versions; raw pickle blobs are not.

### Why the allowlist is the load-bearing defence
joblib uses pickle internally — `joblib.load` without the restricted unpickler is
functionally identical to `pickle.loads` from a security standpoint.  The allowlist
closes the RCE surface by ensuring that `find_class` rejects any module or class
that is not in the pre-approved set, **before** any `__reduce__` / `__setstate__`
output is invoked.

### Allowlist derivation
The full `(module, name)` set was determined empirically by running a subclass of
`pickle.Unpickler` against real `joblib.dump` outputs for both `LogisticRegression`
and `LGBMClassifier`:

| Entry | Reason |
|-------|--------|
| `preference.model.PreferenceScorer` | The wrapper class itself |
| `sklearn.linear_model._logistic.LogisticRegression` | Cold-start model |
| `lightgbm.sklearn.LGBMClassifier` | Warm-start model |
| `lightgbm.basic.Booster` | LightGBM's internal tree model |
| `joblib.numpy_pickle.NumpyArrayWrapper` | joblib emits this for every ndarray |
| `numpy.ndarray` | Model weight arrays |
| `numpy.dtype` | Array dtypes |
| `numpy._core.multiarray.scalar` | Scalar numpy values |
| `collections.defaultdict` | LightGBM's internal param dict |
| `collections.OrderedDict` | LightGBM's internal param dict |

### Alternatives ruled out
- **HMAC envelope around raw pickle**: defers the attack surface instead of closing it.
  The blob still becomes RCE if the HMAC key leaks.  HMAC-only is the "if pickle truly
  cannot be removed" fallback the issue specified — joblib + allowlist is strictly
  stronger.
- **LightGBM native `.txt` format + sklearn JSON**: requires separate serialisation
  paths per model type, custom re-assembly of the `PreferenceScorer` wrapper, and
  additional validation of the sklearn JSON format.  More code surface for the same
  security property.

### Thread-safety note
The temporary `_jnp.NumpyUnpickler` patch is not thread-safe if two `from_bytes`
calls execute concurrently in the same process.  Celery workers are single-threaded
per-task (one task per process with the `prefork` pool), so this is safe in the
current architecture.  If the project ever switches to a threaded Celery pool or
calls `from_bytes` from async code, replace the patch with a thread lock.

### Verification
- `tests/test_preference.py` — 4 new tests:
  - `test_scorer_round_trips_joblib`: legitimate scorer survives to_bytes → from_bytes
    with identical `predict_score` output
  - `test_scorer_round_trips_preserves_label_count`: `label_count` attribute preserved
  - `test_tampered_blob_is_rejected`: joblib blob with `os.system` `__reduce__` raises
    `pickle.UnpicklingError("class not allowed: posix.system")`
  - `test_tampered_blob_arbitrary_global_rejected`: joblib blob with `subprocess.Popen`
    gadget raises `pickle.UnpicklingError("class not allowed: subprocess.Popen")`

### Source / evidence
- scikit-learn User Guide "Model persistence": https://scikit-learn.org/stable/model_persistence.html
- Python docs `pickle.Unpickler.find_class`: https://docs.python.org/3/library/pickle.html#pickle.Unpickler.find_class
- Python HOWTO "Restricting globals" pattern for safe unpickling
- joblib source: `joblib.numpy_pickle.NumpyUnpickler`, `_unpickle` (joblib 1.5.3)
## 2026-05-28 — Issue 35: Idempotent DNA build (SEV-0)

### Single-transaction commit for draft + embeddings + onboarding state

**What changed**: `dna/profile.create_draft`, `dna/embeddings.embed_patterns`, and
`dna/embeddings.embed_brief` each gained a keyword-only `commit: bool = True` parameter.
`worker/tasks._build_dna_async` now calls all three helpers with `commit=False` and issues
a single `await session.commit()` at the end of the function, after all three `session.add()`
chains are staged.

**Why**: The original code committed inside `create_draft` before calling the Voyage API for
embeddings. If the Voyage call raised (network error, quota exhaustion, etc.), Celery retried
the whole task. On retry, `create_draft` queried `max(version)` — which now returned the orphan
draft row — and inserted a new row at version+1. The root cause is a partial commit that left a
permanent row before the unit of work was complete.

The fix makes the database write atomic: if the Voyage call or any subsequent write fails, the
`AsyncSessionLocal` context manager's `__aexit__` calls `session.rollback()`, and no draft row
exists for the next retry to bump the version against.

**Alternatives ruled out**: Deleting the orphan on retry detection (fragile — requires detecting
partial state; race-prone). Using a SAVEPOINT to wrap the embeddings (overkill — the entire
`_build_dna_async` function is one logical unit of work; a single outer transaction is the
idiomatic choice).

**Backward compatibility**: `commit=True` is the default on all three helpers, so all existing
callers (`confirm_draft`, `routers/creators.py`, any future standalone call) continue to commit
immediately without code changes.

**Source**: Standard SQLAlchemy async unit-of-work pattern (defer commit to the outermost
caller that owns the transaction boundary). 2026-05-28.
## 2026-05-28 — Issue 40: Streaming upload — chunk size and RSS assertion bound

### Chunk size: 1 MB

**What**: `upload_video` reads `UploadFile` in 1 MB chunks into a `NamedTemporaryFile`, keeping
only the current chunk in memory at any one time.

**Why 1 MB**: Standard FastAPI / ASGI streaming guidance (Starlette issue #1746; python-multipart
docs) recommends chunk sizes between 512 KB and 4 MB. 1 MB is the midpoint — syscall overhead
is negligible (≤ 500 iterations for a 500 MB file), while the per-request heap ceiling is 1 MB
of upload data regardless of file size. Smaller chunks add syscall noise; larger chunks make the
heap ceiling proportionally higher. No project-specific tuning data exists at this stage, so the
industry midpoint was chosen.

**Source**: Starlette streaming docs; python-multipart FAQ; ASGI file-upload best practices.
2026-05-28.

### RSS delta assertion bound: 20 MB for a 100 MB rejected upload

**What**: `test_rss_delta_bounded_for_rejected_upload` asserts that `ru_maxrss` grows by no more
than 20 MB when a 100 MB upload is rejected.

**Why 20 MB**: With 1 MB chunks, only the current chunk (≤ 1 MB) should be live at any moment.
However, the Python runtime, test framework, OS buffer cache, and Starlette request internals
introduce measurement noise. The 20 MB ceiling is 20× the chunk size — tight enough to catch a
regression to bulk-read (which would show a ~100 MB delta) while loose enough to absorb normal
runtime overhead. This is a conservative bound; in practice the delta observed is 1–3 MB.

**Source**: `resource.getrusage` documentation (Linux: kilobytes, macOS: bytes); empirical
observation during implementation. 2026-05-28.

---

## 2026-05-28 — Issue 36: OAuth token lifecycle hardening (SEV-1)

### Revoke the refresh token, not the access token

**What**: `DELETE /auth/me` now POSTs the decrypted **refresh_token** to
`https://oauth2.googleapis.com/revoke`. A `400` with body `{"error": "invalid_token"}` or
`{"error": "token_revoked"}` is treated as success; other 4xx is logged but does not abort
account deletion.

**Why**: Revoking only the access token leaves the refresh token usable until the user
manually visits `myaccount.google.com/permissions` — an incomplete right-to-erasure and a
YouTube ToS gap. Google's OAuth 2.0 docs explicitly state revoking a refresh token
invalidates every access token derived from it, so one call suffices.

**Source**: Google OAuth 2.0 — Revoking a Token
(`developers.google.com/identity/protocols/oauth2/web-server#tokenrevoke`); OAuth 2.0
RFC 6749 §2.3.1.

### Discard the token row on `invalid_grant`

**What**: `youtube/oauth.py::get_valid_access_token` now deletes the `YoutubeToken` row +
commits when `refresh_access_token` returns `400 {"error": "invalid_grant"}`. Other 4xx
during refresh leaves the row in place (could be transient client misconfig).

**Why**: Per RFC 6749 §5.2, `invalid_grant` is a permanent error — the user has revoked
consent, the grant expired (6 mo unused), or a password reset with reauth invalidated it.
Re-attempting the refresh hourly was wasted quota and noisy logs. Deleting the row makes
the next call surface the existing "No OAuth tokens found — please reconnect" 401.

**Source**: OAuth 2.0 RFC 6749 §5.2; Google identity docs on refresh-token expiration.

### Classify 403 errors by `error.errors[].reason`

**What**: New `youtube/errors.py` defines `YouTubeAuthError(reason, status_code)` plus
`PERMANENT_403_REASONS` (authError, forbidden, accountClosed, accountSuspended,
accountDelegationForbidden, channelClosed, channelSuspended) and `TRANSIENT_403_REASONS`
(quotaExceeded, rateLimitExceeded, userRateLimitExceeded). `_get_json` in
`youtube/data_api.py` and `_fetch_report` in `youtube/analytics.py` now share a
`_classify_error()` helper: transient reasons + 429 still retry with exponential backoff;
permanent reasons + 401 raise `YouTubeAuthError` immediately, no retries.
`worker/tasks.py::_refresh_youtube_analytics_async` catches `YouTubeAuthError`, deletes
the creator's `YoutubeToken` row, commits, and continues to the next creator.

**Why**: Previously every 403 triggered four backoff retries — 7+ seconds of blocking and
four wasted quota hits per beat tick per revoked creator. Over time the daily beat loop
would consume a meaningful slice of the channel quota on creators who had revoked access.
The reason-based branching mirrors how `google-api-python-client` exposes
`HttpError.error_details` and how official YouTube samples branch on `reason`.

**"Mark creator disconnected" via token-row absence**: Rather than add a new
`OnboardingState.disconnected` enum value (which would require an Alembic migration), we
delete the `YoutubeToken` row. The existing `get_valid_access_token` already raises
`HTTPException(401, "No OAuth tokens found — please reconnect")`, and the beat loop's
prefix `try: get_valid_access_token ... except: continue` block then silently skips that
creator. A future issue can add a UI-visible `disconnected` state if the product needs it.

**Source**: YouTube Data API v3 — Errors reference
(`developers.google.com/youtube/v3/docs/errors`); Google APIs error model
(`developers.google.com/identity/protocols/oauth2/openid-connect#errors`); existing
worker skip-on-exception pattern in `worker/tasks.py:_refresh_youtube_analytics_async`.

---

## 2026-05-28 — Issue 45: Concurrent token refresh lock + Redis pool singleton (SEV-2)

### Per-creator Redis advisory lock in `get_valid_access_token`

**What changed**: `youtube/oauth.py::get_valid_access_token` now wraps the Google refresh
call with a per-creator Redis advisory lock (`SET refresh-lock:{creator_id} <uuid> NX EX 10`).

- **Lock acquired**: proceed with the existing refresh + DB commit, then release via a Lua
  compare-and-delete script that only deletes the key if the value still matches our token.
  This prevents a worker whose TTL expired mid-flight from deleting another worker's lock.
- **Lock not acquired**: poll up to 3 times with 200 ms sleeps, re-reading the
  `YoutubeToken` row each time. If the row's `expires_at` is now in the future by > 5 min,
  return its decrypted access token. If still expired after all retries, raise
  `HTTPException(503, "Token refresh in progress; please retry")`.

**Why SET NX EX over Redlock**: SET NX + a reasonable TTL (10s) is the canonical
single-node Redis distributed-lock pattern, documented in the official Redis SETNX page and
in "The Redlock algorithm" article. Redlock (multi-node quorum) is appropriate when Redis
itself is clustered; this project runs a single Redis instance so SET NX is correct and
significantly simpler. The Lua compare-and-delete (KEYS[1] == ARGV[1] → DEL) is the
canonical safe-release idiom from the Redis docs to prevent accidental release of another
client's lock if our TTL expires.

**Why 10s TTL**: One Google token-refresh round-trip completes in < 1s under normal
conditions. 10s gives 10× headroom before the lock auto-expires, covering network hiccups
and slow Google responses while still protecting against a worker crash leaving the lock
indefinitely. A shorter TTL risks expiring mid-refresh; a longer TTL extends the worst-case
stall for waiting workers.

**Why 200ms / 3-retry poll**: Total worst-case wait is 600ms — acceptable for an interactive
`/clips` request. Three retries avoids an infinite loop while giving the lock holder enough
time to complete the Google round-trip and DB commit.

**Source**: Redis SETNX docs (`redis.io/commands/setnx`); Redis "Distributed Locks with
Redis" article (`redis.io/docs/manual/patterns/distributed-locks`). 2026-05-28.

---

### Module-level Redis singleton in `youtube/_redis.py`

**What changed**: `youtube/quota.py` previously called `aioredis.from_url(...)` on every
`consume()` and `remaining()` call, creating a new connection-pool per call. A new helper
module `youtube/_redis.py` exposes `get_redis_client()` which initialises a single
`redis.asyncio.Redis` instance at first call and reuses it on all subsequent calls.
Both `youtube/quota.py` and `youtube/oauth.py` import from this module.

**Why singleton over per-call `from_url`**: `redis-py` 4.2+ creates an internal
`ConnectionPool` per `Redis` instance. Per-call `from_url` creates a new pool every time,
leaking connections and adding latency. The singleton pattern ensures one pool is shared
across the process — the standard recommendation in the redis-py docs and the pattern used
by every production redis-py deployment.

**Why a separate `_redis.py` module**: `oauth.py` and `quota.py` are separate concerns but
both need Redis. Putting the singleton in either one and importing from the other creates a
circular dependency risk. A dedicated `_redis.py` (underscore = package-internal) is the
clean DRY solution.

**Source**: redis-py docs "Connection Pools" section; PEP 8 on module naming conventions
for package-internal helpers. 2026-05-28.
