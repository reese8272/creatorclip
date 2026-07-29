# _root_infra — assessed 2026-07-20 (post-fix); delta re-assessed 2026-07-29

Slice: `main.py`, `config.py`, `db.py`, `auth.py`, `crypto.py`, `limiter.py`,
`models.py`, `api_key.py`, `event_log.py`, `observability.py`, `redact.py`,
`shared_resources.py`, `flags.py`, `verbose.py`, plus `alembic/` (env.py and
migrations 0040–0046). Re-assessment after the two fix waves merged this
morning (`git diff ca3305c..e92b93a`): every finding from the 2026-07-20
morning report re-verified at HEAD, and the wave diff (api_key.py, config.py,
main.py, models.py, new migration 0046) reviewed line-by-line for regressions.

## Findings

- [SEV2] (carry-forward, accepted-for-beta residual) limiter.py:129-133 —
  unchanged in the wave. slowapi 0.1.9 still calls sync `RedisStorage.hit()`
  on the event-loop thread for every rate-limited request; the shipped
  mitigation (socket_timeout=0.1, socket_connect_timeout=0.25, `limits==5.8.0`
  pinned) bounds worst-case blocking at ~100 ms/request rather than
  eliminating it. Remains a serialized Redis hop and a throughput ceiling;
  documented async-upgrade trigger unchanged (slowapi still has no awaited
  `hit()`). | fix: none for the ≤100-user beta; execute the documented
  `async+redis://` + `limits.aio` switch when slowapi awaits `hit()`.
  (needs-load-evidence for the ceiling.)

- [cleanup] (carry-forward) config.py:520 — `log_level_int` still does
  `import logging as _logging` inside the property although `logging` is
  imported at module top (config.py:1). | fix: use the module-level import.

- [cleanup] (carry-forward) main.py:409 — `from auth import
  creator_id_from_cookie` inside the `_log_request_events` middleware body
  executes a `sys.modules` lookup per request; `main.py` already imports from
  `auth` at module level, so there is no circular-import reason. | fix: hoist
  to the top-level import.

## Resolved since the 2026-07-20 morning report

- **SEV1 api_key.py GUC regression** — FIXED (Issue 358, commit 2435b27).
  `get_current_creator_via_api_key` now executes
  `SELECT set_config('app.creator_id', :cid, true)` unconditionally at
  api_key.py:141-144, placed AFTER the Issue-352 stamp branch and AFTER
  `session.info["creator_id"] = creator.id`. Verified on both paths:
  *no-stamp* — the key-hash SELECT auto-began the request transaction before
  `session.info` was set (so `after_begin` emitted nothing); the explicit
  `set_config` now lands on that same live transaction, so downstream tenant
  queries (`check_positive_balance` → minute_packs/usage) see the creator's
  rows under enforced RLS. *Stamp* — the `last_used_at` commit ends the
  bootstrap transaction; the `set_config` execute then auto-begins a fresh
  transaction in which the `after_begin` listener ALSO fires (info now set),
  so the GUC is set twice, harmlessly, and `is_local=true` matches the
  listener's commit-scoped semantics. Regression tests added:
  tests/test_api_key.py:230-277 (unit, no-stamp path asserts the GUC lands on
  the live transaction) and tests/test_api_keys_integration.py:410-484
  (real-RLS `-m integration`: two bearer requests inside the stamp window,
  asserting the second — no-stamp — request still sees tenant rows). The
  false-402 on back-to-back OBS `/clips/ingest` uploads is closed.
- **SEV2 Fernet key boot validation** — FIXED (Issue 358, commit 2435b27).
  `_fernet_key_format` field_validator (config.py:790-812) attempts
  `Fernet(v.encode())` on both `TOKEN_ENCRYPTION_KEY` and
  `TOKEN_ENCRYPTION_KEY_PREVIOUS` at Settings construction, with a clear
  generate-a-key message; None/empty `_PREVIOUS` passes through (optional
  rotation field), while an empty/garbage primary key now fails at boot
  instead of 500ing on the first OAuth encrypt. Tests at
  tests/test_crypto.py:39-71 cover malformed primary, malformed `_PREVIOUS`,
  and valid `_PREVIOUS` pass-through; a stale 28-byte dummy key in the mailer
  tests was caught by the new validator and corrected (commit ef02066) —
  evidence the gate bites.

(Everything in the morning report's "Resolved since 2026-07-01" section
remains fixed — event-log pool pinning + `record_event_nowait`, ENV Literal,
JWT min-length, `limits` pin, `__unmatched__` metric label, `_fernet()`
lru_cache — spot-re-verified unchanged at HEAD.)

## Wave-diff review (new code since ca3305c) — no new findings

- **Migration 0046 (`alembic/versions/0046_race_unique_backstops.py`) —
  online-safe and downgrade-symmetric.** Both unique indexes are built
  `CONCURRENTLY` inside `autocommit_block()` (same proven shape as
  0006/0010/0013; env.py's lock_timeout=5s / statement_timeout=120s guard
  both online and offline `--sql` paths). The clips constraint is then
  attached catalog-only via `ADD CONSTRAINT ... UNIQUE USING INDEX ...
  DEFERRABLE INITIALLY DEFERRED` — valid PG syntax, microsecond ACCESS
  EXCLUSIVE, no scan. DEFERRABLE is load-bearing and correct:
  `rerank_with_preference` permutes rank values via per-row UPDATEs, which an
  immediate check would abort on the transient swap; the deferred commit-time
  check still fails the loser of a cross-transaction double-insert (PG queues
  the potential conflict and rechecks after the winner commits). NULL ranks
  are distinct and never conflict, matching the nullable `rank` column.
  Dedupe-first steps make the builds safe on a dirtied table: clips keeps the
  earliest `created_at` per (video_id, rank) (feedback/outcomes attach to the
  canonical originals); summaries demotes older active duplicates to
  'failed' (UPDATE, not DELETE — auditable, and mid-render workers against a
  demoted id are not orphaned from a deleted row). The dedupe→build race
  window and the failed-CONCURRENTLY→INVALID-index recovery (`IF NOT EXISTS`
  would otherwise adopt an invalid index that `USING INDEX` then rejects) are
  both explicitly documented in the docstring with the operator action.
  Downgrade is symmetric: `DROP CONSTRAINT` removes the clips constraint and
  its index; `DROP INDEX CONCURRENTLY` removes the summaries partial index;
  dedupe irreversibility documented. DECISIONS entry present ("Issue 361
  (races batch): shape of the two unique backstops", 2026-07-20).
- **models.py:615-632, 834-848** — ORM metadata matches 0046 exactly:
  `UniqueConstraint("video_id", "rank", name="uq_clips_video_rank",
  deferrable=True, initially="DEFERRED")` on Clip and the partial unique
  `sa.Index("uq_summaries_active", "video_id", unique=True,
  postgresql_where=render_status IN ('pending','running'))` on Summary — so
  autogenerate stays quiet and fresh `create_all` schemas match prod. The
  index predicate correctly keeps pending→running transitions conflict-free
  and lets done/failed rows re-render. (The IntegrityError-catch consumers
  live in clip_engine/ranking.py and routers/clips.py — other modules' slices;
  their race tests exist: tests/test_ranking_persist_race.py,
  tests/test_summary_race_integration.py.)
- **main.py:294-299 CSP** — `style-src 'self' 'unsafe-inline'
  https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com`
  added to `_CSP_BASE`. Deliberate, DECISIONS-logged (2026-07-20 backend-misc
  batch): the SPA stylesheet @imports Google Fonts, and `'unsafe-inline'`
  covers the retained static pages' `<style>` blocks. script-src remains
  governed by `default-src 'self'` (no inline scripts), so the
  clickjacking/XSS posture is intact; style-src 'unsafe-inline' is the
  industry-common tradeoff and bounded to CSS injection. Not flagged.
- **config.py:141-146 + .env.example:32-33** — new
  `COST_PER_MTOK_IN_OPUS`/`_OUT_OPUS` present in `.env.example` with source
  comments (rubric 8 satisfied); floats with sane defaults.
- Unit lanes green locally: `tests/test_api_key.py` + `tests/test_crypto.py`
  — 36 passed.

## Delta re-assessment — 2026-07-29 (ready-pass, branch w3/ready-pass-closeout)

Delta reviewed: `git diff e92b93a..HEAD` restricted to the slice — only
`main.py`, `models.py`, `shared_resources.py`, and new migration 0047 changed.
`config.py`, `db.py`, `auth.py`, `crypto.py`, `limiter.py`, and `.env.example`
are byte-identical to the previously assessed state (no new config to check —
rubric 8 unchanged). **No new findings.**

- **main.py:92-122 lifespan health-Redis ownership — correct under both
  nested TestClients and the prod single lifespan.** The lifespan now owns the
  client it creates: acquire before `yield` (`from_url` is lazy, cannot raise
  a connection error there), stack-save `prev_health_redis`, and in `finally`
  restore the module global BEFORE awaiting `health_redis.aclose()`. Nesting
  is LIFO by `with`-semantics, so nested TestClients restore correctly: inner
  lifespan closes ITS client on ITS portal loop (connections are created
  lazily on that same loop, so the close is same-loop — no cross-loop
  "Event loop is closed" GC noise) and the outer client is restored intact.
  Prod single-lifespan: prev is None; restore-before-close means an in-flight
  `/health` probe during shutdown sees `_health_redis is None` and degrades
  (503 path via `_check_redis` → False, main.py:490-491) rather than pinging
  a closing client. `aclose` is wrapped so shutdown never raises;
  `shared_resources.close_all()` still runs after, unchanged in order. The
  registry's replace-and-never-clear semantics (which leaked earlier
  lifespans' instances) no longer apply — the "health_redis" registry entry
  is gone, and shared_resources.py's docstring now states the rule
  (import-time singletons only). Regression tests verify both properties:
  tests/test_shared_resources.py:89 (no registry entry) and :94-110
  (stack-restore of the previous client). 15/15 unit tests green
  (test_health.py + test_shared_resources.py).
- **Migration 0047 (`alembic/versions/0047_clip_applied_metadata.py`) —
  expand-only, lock-cheap, linear, downgrade-symmetric.** Two nullable
  `ADD COLUMN` (Text, no default) = catalog-only in PG16: no rewrite, no
  scan; each ALTER takes a microsecond ACCESS EXCLUSIVE bounded by env.py's
  `lock_timeout=5s` guard (present on both the online `connect_args` path,
  env.py:61, and the offline `--sql` path, env.py:35-36), so it is safe on
  the populated clips table even under concurrent traffic — worst case is a
  5s-timeout retry, never a stuck queue behind the lock. Linearity verified:
  0047 is the ONLY revision with `down_revision = "0046"` and nothing revises
  0047 — single head. Downgrade drops the two columns in reverse order
  (metadata-only `attisdropped` in PG); data loss on downgrade is inherent
  and acceptable for an expand-only column pair whose NULL state is the
  documented fallback. Old images ignore the columns during rolling restart —
  no contract phase needed, as the docstring states per docs/MIGRATIONS.md
  Template A.
- **models.py:670-676 — ORM/migration parity exact.** `applied_title` /
  `applied_description` are `Mapped[str | None]` on `sa.Text`,
  `nullable=True`, no server_default — matches 0047 precisely, so
  autogenerate stays quiet and fresh `create_all` schemas match prod. Length
  enforcement (title ≤100, description limits) correctly lives at the API
  boundary (routers/clips.py PATCH validators — other module's slice), so
  the worker never truncates an applied value.
- **tests/conftest.py:112-142 `pytest_sessionfinish` hook — test-infra, note
  only.** Disarms leftover async-Redis singletons' `_writer`/`_reader` so
  `AbstractConnection.__del__` is a no-op at interpreter exit; docstring is
  explicit that mid-run leaks still surface as
  PytestUnraisableExceptionWarning, so it masks only the unavoidable
  end-of-session GC noise, not real leaks. No production code affected.

Carry-forwards re-verified still present at HEAD: SEV2 limiter.py sync Redis
hop (accepted beta residual, mitigation pins intact at limiter.py:33-75);
cleanup config.py:520 inline `import logging`; cleanup main.py:419 inline
`from auth import creator_id_from_cookie`.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — lifespan now owns the health-Redis client per-lifespan (acquire/restore/aclose verified + regression-tested); pools/registries otherwise unchanged |
| 2 Concurrency & scale | 1 SEV2 (slowapi sync Redis hop, accepted beta residual); 0046 backstops and 0047 expand-only ADD COLUMN verified online-safe |
| 3 Security & compliance | ok — SEV1 api_key GUC and SEV2 Fernet boot validation both FIXED with tests; RLS posture whole again on the API-key surface; no new secrets/PII in the delta |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | n/a (no LLM calls in slice; Opus rates are config only) |
| 6 Cleanliness & typing | 2 cleanup (both carry-forward inline imports) |
| 7 Error handling / API | ok (401/402/403/503 codes correct; shutdown-window /health degrades cleanly) |
| 8 Config & paths | ok — no config/env additions in the delta; .env.example unchanged and current |

## Module verdict
clean — the 2026-07-29 ready-pass delta (per-lifespan health-Redis ownership,
Clip applied_title/applied_description, migration 0047) introduces no new
findings: the lifespan pattern is correct under nested TestClients and prod
single-lifespan with regression tests, 0047 is expand-only/lock-cheap/linear
with a symmetric downgrade, and ORM parity is exact; what remains is the
documented, accepted slowapi beta residual and two trivial inline-import
cleanups.
