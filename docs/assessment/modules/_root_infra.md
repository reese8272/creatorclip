# _root_infra — assessed 2026-07-20

Slice: `main.py`, `config.py`, `db.py`, `auth.py`, `crypto.py`, `limiter.py`,
`models.py`, `api_key.py`, `event_log.py`, `observability.py`, `redact.py`,
`shared_resources.py`, `flags.py`, `verbose.py`, plus `alembic/` (env.py and
migrations 0040–0045). Prior findings (2026-07-01) re-verified line-by-line
against current code; `git diff f70a857..HEAD` scrutiny applied to every
changed slice file.

## Findings

- [SEV1] api_key.py:105-136 — `get_current_creator_via_api_key` never received
  the Issue-344 GUC fix that auth.py:157 has, and the Issue-352 `last_used_at`
  throttle (`should_stamp_last_used`, 5-min interval) removed the per-request
  `commit()` that previously masked it. Sequence on the no-stamp path (every
  API-key request within 5 min of the last stamp): the `key_hash` SELECT
  auto-begins the request transaction BEFORE `session.info["creator_id"]` is
  set, so `after_begin` emits no `app.creator_id` GUC; no commit intervenes;
  the endpoint's queries then run in that SAME transaction with the GUC unset.
  Under enforced RLS (prod app role — dev single-role bypasses as table owner,
  so unit tests can't catch it) every tenant-table read denies to zero rows:
  `check_positive_balance` (routers/clips.py:948 → billing/ledger.py:403) sees
  zero `minute_packs`/`usage` rows and 402s "No minutes remaining" for a fully
  funded creator, and any tenant INSERT before the first commit hits WITH CHECK.
  Net symptom: OBS `/clips/ingest` works for the first upload in each 5-min
  window and falsely 402s back-to-back uploads. Fails closed (no leak), but a
  prod-only intermittent correctness defect on the whole API-key surface. |
  fix: mirror auth.py — in `get_current_creator_via_api_key`, immediately after
  `session.info["creator_id"] = creator.id`, unconditionally
  `await session.execute(text("SELECT set_config('app.creator_id', :cid, true)"), {"cid": str(creator.id)})`;
  add an integration test (real RLS, `-m integration`) that calls the API-key
  dependency twice inside the stamp interval and asserts the second request
  still sees the creator's minute packs.

- [SEV2] (carry-forward) config.py:83,92 + crypto.py:27-29 —
  `TOKEN_ENCRYPTION_KEY` / `TOKEN_ENCRYPTION_KEY_PREVIOUS` format still not
  validated at boot. `_fernet()` (now `lru_cache`d) is still lazy, so a
  malformed key surfaces as a 500 on the first OAuth encrypt/decrypt in
  production instead of a boot failure, and a bad `_PREVIOUS` silently breaks a
  live rotation window. `scripts/doctor.py:86` does validate, but it is an
  opt-in tool, not the boot path — CLAUDE.md mandates fail-fast via
  pydantic-settings. | fix: `field_validator("TOKEN_ENCRYPTION_KEY",
  "TOKEN_ENCRYPTION_KEY_PREVIOUS")` attempting `Fernet(v.encode())` (skip
  None/empty for `_PREVIOUS`), raising a clear message at load.

- [SEV2] (carry-forward, accepted-for-beta residual) limiter.py:129-133 —
  slowapi 0.1.9 still calls sync `RedisStorage.hit()` on the event-loop thread
  for every rate-limited request; the shipped mitigation (socket_timeout=0.1,
  socket_connect_timeout=0.25, now with `limits==5.8.0` pinned) bounds
  worst-case blocking at ~100 ms/request rather than eliminating it. Remains a
  serialized Redis hop and a throughput ceiling; documented async-upgrade
  trigger unchanged (slowapi still has no awaited `hit()`). | fix: none for the
  ≤100-user beta; execute the documented `async+redis://` + `limits.aio`
  switch when slowapi awaits `hit()`. (needs-load-evidence for the ceiling.)

- [cleanup] (carry-forward) config.py:513 — `log_level_int` still does
  `import logging as _logging` inside the property although `logging` is
  imported at module top (config.py:1). | fix: use the module-level import.

- [cleanup] main.py:401 — `from auth import creator_id_from_cookie` inside the
  `_log_request_events` middleware body executes a `sys.modules` lookup per
  request; `main.py` already imports from `auth` at module level (line 26), so
  there is no circular-import reason. | fix: hoist to the top-level import.

## Resolved since 2026-07-01

- **SEV1 uncounted 15-conn event-log pool** — FIXED. Pool pinned to
  `pool_size=2, max_overflow=3` (event_log.py:84-85, Issue 347), and the
  request-path write moved off the hot path entirely via `record_event_nowait`
  (fire-and-forget, bounded at `_MAX_PENDING=20` in-flight, drops beyond the
  cap; cross-loop task pruning + drain-on-shutdown in `dispose()`), Issue 352.
- **SEV2 main.py inline `await record_event` per request** — FIXED. The
  http_request middleware now calls `record_event_nowait` (main.py:404); a slow
  logs DB can neither add latency nor starve connections.
- **SEV2 ENV free-string gate** — FIXED. `ENV:
  Literal["development","staging","production"]` (config.py:420); a deploy typo
  now fails at boot.
- **SEV2 JWT secret min-length** — FIXED. `_jwt_secret_min_length` validator
  enforces ≥32 bytes for HS256 (config.py:766-781).
- **SEV2 unpinned `limits` transitive dep** — FIXED. `limits==5.8.0` pinned
  with an explanatory comment (requirements.txt:15-20).
- **SEV2 observability raw-path 404 metric label** — FIXED. Unmatched routes
  now label as the constant `"__unmatched__"` (observability.py:560-561);
  cardinality bounded.
- **cleanup MultiFernet rebuilt per decrypt()** — FIXED (beyond the prior
  "not worth caching" note): `_fernet()` is `@lru_cache(maxsize=1)`
  (crypto.py:15), with a documented `cache_clear()` contract for tests.

## Verified-correct (no finding)

- **Migrations 0040–0045 (online safety + correctness):** env.py applies
  `lock_timeout=5s` / `statement_timeout=120s` via libpq `options` at connect
  (online) and explicit `SET` statements in offline `--sql` mode — both prod
  paths guarded. 0045's `ALTER POLICY` rewrite is catalog-only (no heap scan),
  and its table inventory is complete: 21 direct-column + 6 child-subquery
  policies = all 27 `tenant_isolation` policies, matching 0010/0026/0027/0029/
  0030/0031/0037/0038/0041 + 0040/0044. The `NULLIF(current_setting(...), '')`
  hardening correctly degrades the reused-pooled-connection empty-string GUC to
  a clean zero-row deny instead of a 22P02 500. 0041 declares enums with
  dialect `postgresql.ENUM(create_type=False)` + explicit `checkfirst` create
  (the fix for the 2026-07-02 duplicate-CREATE TYPE deploy abort) and indexes
  `summaries.creator_id` / `summaries.video_id`. 0043 `feature_flags` is
  correctly RLS-exempt (global ops table, no tenant data). Downgrades real
  everywhere; 0014 exception documented in `alembic/DOWNGRADE_EXCEPTIONS`.
- **flags.py fail-open kill switches:** TTL-cached DB read → env default →
  hard-ON; any DB failure fails OPEN with a warn-once; `set_flag` audits on
  both telemetry rails; `require_flag` returns a stable-code 503 with no
  internal detail. No RLS needed (no tenant data).
- **Pool math (db.py):** app 15+5, admin 2+2, event-log 2+3 — all engines
  carry `pool_pre_ping`, `pool_recycle=1800`, `prepare_threshold=None`
  (PgBouncer-safe). `recreate_engine` swap is build-then-swap-then-dispose
  under a non-blocking lock; `tenant_session` reads the factory at call time so
  post-fork rebinds are honoured.
- **Per-tenant isolation (auth path):** auth.py sets the GUC on the live
  bootstrap transaction (Issue 344) AND via the `after_begin` listener for
  later transactions; `creators`/`creator_api_keys`/`event_logs`/`feature_flags`
  are deliberately RLS-exempt. The session-cookie path is correct — only the
  API-key path regressed (SEV1 above).
- **Log/PII hygiene:** redact.py recurses to depth 8 with a conservative
  wholesale-redact beyond it; JsonLogFormatter + `_sentry_before_send` scrub as
  structural backstops (`send_default_pii=False` unconditional); verbose sink
  remains triple-gated (`VERBOSE_LOGGING` + prod requires
  `VERBOSE_LOGGING_ALLOW_PROD`) with `propagate=False`; OTel Anthropic
  instrumentation forces `TRACELOOP_TRACE_CONTENT=false`. limiter logs only
  the exception class. No token/PII in any `logger.*` call in the slice.
- **/health and /metrics:** probes reuse the pooled engine + module-level Redis
  singleton (no per-probe pools); /metrics token-gated with
  `secrets.compare_digest`, auto-disabled in prod when the token is unset.
- **shared_resources registry:** reverse-order, error-isolated, re-registration
  replaces + survives repeated TestClient lifespans; matches its documented
  semantics.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — prior SEV1 pool fixed; registries/disposals verified |
| 2 Concurrency & scale | 1 SEV2 (slowapi sync Redis hop, accepted beta residual) |
| 3 Security & compliance | 1 SEV1 (api_key GUC regression, fails closed) + 1 SEV2 (Fernet boot validation) |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | n/a (no LLM calls; verbose/observability only shape usage dicts) |
| 6 Cleanliness & typing | 2 cleanup |
| 7 Error handling / API | ok (401/402/403/503 codes correct; no stack traces or DB errors to client) |
| 8 Config & paths | ok — new OTEL/FLAG/SPEND/VERBOSE/LOGS config all present in .env.example; validators strong |

## Module verdict
NEEDS-WORK — the 2026-07-01 SEV1 (event-log pool) and all four config/dep SEV2s
are fixed, and the 0040–0045 RLS migrations are correct and online-safe; but a
new SEV1 regression on the API-key auth path (missing Issue-344 GUC set after
the Issue-352 last-used throttle removed the masking commit) intermittently
breaks `/clips/ingest` under enforced RLS in production, plus the carry-forward
Fernet boot-validation gap.
