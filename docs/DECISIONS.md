# CreatorClip — Design Decisions Log

Entries are added whenever an architectural decision is made, a library is chosen, or
implementation diverges from the PRD. Every entry must include what, why, source/evidence, and date.

---

## 2026-05-28 — Issue 79: Postgres RLS implementation per Issue 56 decision

### What was built
Implements the Issue 56 adopt-now decision. New alembic revision
`0010_rls_policies` creates roles, grants, and policies:

- **Roles**: `creatorclip_app` (LOGIN, no BYPASSRLS — the application
  connects as this) and `creatorclip_migrate` (LOGIN, BYPASSRLS granted out
  of band — alembic and Celery worker tasks connect as this). Both are
  created idempotently inside `DO $$ ... $$` blocks.
- **Grants**: `creatorclip_app` gets `USAGE` on `schema public` and
  `SELECT, INSERT, UPDATE, DELETE` on all tables + `USAGE, SELECT` on all
  sequences. `ALTER DEFAULT PRIVILEGES` extends the same grants to future
  tables created in `public` so we don't lose access after the next
  migration.
- **Policies** on 12 tables (every table with a direct `creator_id`
  column): `videos`, `audience_activity`, `demographics`, `youtube_tokens`,
  `creator_dna`, `dna_embeddings`, `clips`, `clip_feedback`,
  `preference_models`, `minute_packs`, `minute_deductions`, `usage`. Each
  policy is `USING (creator_id = current_setting('app.creator_id',
  true)::uuid) WITH CHECK (...)`. Both `ENABLE` and `FORCE ROW LEVEL
  SECURITY` are applied so the table owner cannot bypass.

Application wiring (Issue 79 code changes):

- `config.py`: new optional `DATABASE_MIGRATION_URL` env var (falls back to
  `DATABASE_URL` for single-role dev/CI).
- `db.py`: two engines / sessionmakers — `engine` + `AsyncSessionLocal`
  (app role, used by FastAPI request path) and `admin_engine` +
  `AdminSessionLocal` (migration role, used by Celery worker tasks).
  Registers a global `after_begin` listener on the `Session` class that
  emits `SET LOCAL app.creator_id = :cid` from `session.info["creator_id"]`
  when present.
- `auth.py:get_current_creator`: after resolving the Creator from the JWT,
  attaches `creator.id` to `session.info["creator_id"]`. The bootstrap
  Creator lookup runs cleanly because the `creators` table is exempt from
  RLS.
- `worker/tasks.py`: every `db.AsyncSessionLocal()` site switched to
  `db.AdminSessionLocal()` (16 call sites). Worker tasks are trusted
  internal code that performs cross-tenant sweeps; the admin role bypass
  is the correct shape.
- `alembic/env.py`: uses `settings.database_migration_url`.

Tests:

- `tests/test_retention_tasks.py` and `tests/test_oauth_lifecycle.py`:
  patches of `db.AsyncSessionLocal` switched to `db.AdminSessionLocal`
  (only worker-task tests were affected).
- New `tests/test_rls_isolation_integration.py` (marker: `integration`):
  seeds Creator A + Creator B with one row per tenant table each, then
  opens a transaction, issues `SET LOCAL ROLE creatorclip_app` + `SET LOCAL
  app.creator_id = :A`, and asserts that an unfiltered `SELECT creator_id
  FROM <each tenant table>` returns zero rows owned by B. A second test
  asserts the `creators` table remains visible to the app role with no GUC
  set, validating the auth-bootstrap exemption.

Operations runbook in `docs/DEPLOYMENT.md` covers the one-time prod ops:
`ALTER ROLE creatorclip_migrate BYPASSRLS`, role passwords, table ownership
transfer to `creatorclip_migrate`, and the two-URL env update.

### Why
Implements the Issue 56 decision without re-deliberating. See that
DECISIONS entry for the rationale; this entry documents the chosen
implementation shape.

### Two minor decisions surfaced during implementation

**1. JWT-to-creator bootstrap via `creators` table exemption.** The auth
dependency must look up Creator by JWT `sub` before `app.creator_id` is set.
Option B from the CHECK brief (pre-parse JWT in middleware → request.state)
was ruled out as heavier than needed. Option A (rely on the existing
`creators`-table RLS exemption) works because the `creators` table has no
policy — the bootstrap SELECT runs without a gate, then `auth.py` attaches
the resolved id to `session.info` so every subsequent transaction in the
request emits SET LOCAL via the listener.

**2. Test fixture role strategy.** Existing integration tests use
`settings.DATABASE_URL` to create their own engines for setup/teardown.
Rather than touching ~15 test files, the strategy is: dev / CI Postgres
connects as a SUPERUSER (which bypasses RLS regardless of FORCE), and the
new RLS-guarantee tests use `SET LOCAL ROLE creatorclip_app` within a
transaction to assume the non-BYPASSRLS role for the visibility assertion.
This keeps existing tests untouched and makes the RLS guarantee
independently verifiable.

### Mutation rowcount audit (AC carry-over)

Issue 56's acceptance criteria included "every UPDATE/DELETE on tenant
tables checks rowcount and raises 404 on 0". The audit found:

- Routers: only two `session.execute(update/delete)` calls outside the
  ORM session pattern (`routers/billing.py:154` updating `creators`,
  `routers/auth.py:204` deleting `creator`). Both target the `creators`
  table, which is exempt from RLS — no rowcount-zero failure mode.
- All other router mutations go through ORM `session.get(Model, id)` →
  mutate → commit. Under RLS, `session.get` returns `None` for rows the
  current creator cannot see → the existing `if not video: raise 404`
  pattern is the rowcount guard.
- Worker tasks (the one bulk UPDATE in `_purge_stale_source_media_async`)
  run via `AdminSessionLocal` and bypass RLS — no failure mode there.

The audit AC is therefore satisfied by construction. If a future change
introduces a router-side bulk UPDATE/DELETE on tenant tables, the
rowcount-zero check must be added at the call site; this is documented
in the runbook.

### Alternatives ruled out (Issue 79-specific)
- **Drop FORCE RLS to make dev/CI Just Work**: would let the table owner
  bypass policies — defeats the purpose. The chosen role-assumption test
  strategy keeps FORCE on without needing to change CI.
- **Bypass-flag policy pattern** (`OR current_setting('app.bypass_rls',
  true) = 'on'`): rejected per Issue 56 — industry-standard is BYPASSRLS
  role, not in-policy bypass logic.
- **Worker tasks with per-creator `SET LOCAL`** (instead of admin role):
  would require restructuring every Celery task to scope to one creator.
  `purge_stale_source_media` and `poll_clip_outcomes` are inherently
  cross-tenant; the admin role + BYPASSRLS is the correct shape for those.
  Per-creator scoping in workers is a possible future hardening if we
  ever need to defend against compromised worker code.

### Tradeoffs
- **First-deploy ops burden**: the runbook requires SUPERUSER access to
  prod Postgres for one-time `ALTER ROLE BYPASSRLS` + ownership transfer.
  Documented but unavoidable.
- **Child tables not yet covered**: `video_metrics`, `retention_curves`,
  `transcripts`, `signals`, `clip_outcomes` reach tenant via FK to a
  policy-protected parent. Per Issue 56, this is acceptable for now; a
  raw `SELECT * FROM signals` in a future code path would bypass the
  parent policy. Flagged for future hardening.
- **Mutation rowcount audit**: the AC is satisfied by construction today
  but the codebase pattern (`session.get → mutate → commit`) is not
  enforced — a future bulk `session.execute(update(...))` on a tenant
  table would silently 0-row under RLS without raising 404. A static check
  could be added but is overkill for current surface.

### Source / evidence
Same sources as Issue 56's DECISIONS entry (Crunchy Data, pganalyze,
Bytebase footguns, SQLAlchemy 2.0 docs + discussion #10469, Microsoft
Azure multi-tenant guidance). Re-validated against the actual codebase:

- Read `auth.py:31-47` to confirm the bootstrap query shape and apply the
  exemption-based fix.
- Read `models.py` to enumerate every direct `creator_id` column (12,
  matches Issue 56's count exactly).
- Read every router for mutation patterns; confirmed two raw mutations on
  the exempt `creators` table.

### Files
- `alembic/versions/0010_rls_policies.py` — new migration.
- `config.py` — new `DATABASE_MIGRATION_URL` + `database_migration_url`
  property with fallback.
- `db.py` — admin engine/sessionmaker; `after_begin` listener.
- `auth.py:get_current_creator` — `session.info["creator_id"]` injection.
- `worker/tasks.py` — 16 `db.AsyncSessionLocal()` → `db.AdminSessionLocal()`
  replacements.
- `alembic/env.py` — uses migration URL.
- `tests/test_retention_tasks.py` — patches updated to
  `db.AdminSessionLocal`.
- `tests/test_oauth_lifecycle.py` — patch updated to
  `db.AdminSessionLocal`.
- `tests/test_rls_isolation_integration.py` — new file: 2 tests
  (cross-tenant leak block + creators-table exemption).
- `docs/DEPLOYMENT.md` — RLS one-time setup runbook.
- `docs/SECRETS.md` — `DATABASE_MIGRATION_URL` row added.

### Date
2026-05-28

---

## 2026-05-28 — Issue 56: Postgres Row-Level Security — adopt now

### What was decided
**Adopt Postgres RLS as the defense-in-depth layer underneath the existing
application-level always-filter for every tenant-owned table.** The
implementation lands in a separate issue (filed as **Issue 79**); this entry
closes the Issue 56 "research-and-decide" deliverable.

### Why
Application-layer filtering is the foundation but is a linting problem
disguised as a security property — it depends on every developer, every PR,
every query author, forever, never forgetting `WHERE creator_id = :id`. We
already had one SEV-0 leak (Issue 33) where the filter was missed and
cross-creator analytics flowed into a Claude prompt. RLS converts the
guarantee from "every query author must remember" into a structural property
of the database: the row never leaves Postgres for the wrong tenant, even
when application code forgets the WHERE.

We are about to enter Google OAuth verification (Phase 3) where auditable
multi-tenant isolation posture is load-bearing for approval; the right
time to pay the implementation cost is before public launch, not during a
post-launch incident.

### Implementation sketch (for Issue 79)

**Tables needing CREATE POLICY** — every table with a direct `creator_id`
column, 12 in total: `videos`, `audience_activity`, `demographics`,
`creator_dna`, `dna_embeddings`, `clips`, `clip_feedback`,
`preference_models`, `minute_packs`, `minute_deductions`, `usage`,
`youtube_tokens`. Child-only tables (`video_metrics`, `retention_curves`,
`transcripts`, `signals`, `clip_outcomes`) reach tenant via FK to a parent
that already has a policy; explicit policies on them are belt-and-suspenders
and can land in a follow-up if a query path ever bypasses the parent join.
`creators` and `audit_log` are explicitly exempt (self-identifying;
append-only ops log).

**Role split** — application connects as `creatorclip_app` (no `BYPASSRLS`,
not the table owner). Alembic migrations connect as `creatorclip_migrate`
with `ALTER ROLE creatorclip_migrate BYPASSRLS`. Adds a new
`DATABASE_MIGRATION_URL` env var alongside the existing `DATABASE_URL`.
Without this split the app role would bypass policies as the owner,
defeating the entire mechanism.

**`SET LOCAL app.creator_id` injection** — register an SQLAlchemy
`after_begin` event listener on the `Session` class that calls
`connection.execute(text("SET LOCAL app.creator_id = :id"), {"id": str(creator_id)})`
inside every transaction. Source the creator UUID from the existing FastAPI
auth dependency (`current_creator`). The `after_begin` hook fires
per-transaction, matching `SET LOCAL`'s transaction scope: when the
transaction commits or rolls back, the GUC disappears and the next
transaction on a recycled pool connection starts clean.

**`FORCE ROW LEVEL SECURITY`** — apply to every policy-covered table in
the migration. By default Postgres lets the table *owner* bypass RLS
regardless of policies; `FORCE` closes that gap.

**Issue 48 isolation test extension** — for every existing isolation test,
add a "with RLS active, an unfiltered `SELECT *` returns zero rows for
non-current creator" assertion. This converts the test suite from "the
application filtered correctly" into "the database refused to leak even
without the application filter" — exactly the property RLS is purchased to
provide.

### pgbouncer-future answer (pinned)
We do not run pgbouncer today. When we add it:
- **Transaction pooling**: SAFE. `SET LOCAL` is scoped to the transaction
  and cleared on commit, so the next request on a recycled connection
  starts clean.
- **Statement pooling**: UNSAFE. pgbouncer can hand off mid-transaction
  to a different connection, leaking the GUC across tenants.
- **Session pooling**: SAFE but loses most of pgbouncer's benefit.

Decision: when we add pgbouncer, configure transaction pooling only. This
is the industry-standard pairing for RLS-enabled stacks.

### Alternatives ruled out
- **Defer to production-scale**: would tolerate Issue-33-class regressions
  until launch. The Issue 33 leak motivated this issue. Deferring is not
  defensible given that history.
- **Decline (rely on application filter only)**: leaves the bug class
  structurally open. Even with the Issue 48 isolation test suite (which is
  excellent for what it tests), nothing prevents the next missed filter from
  shipping.
- **Connection `checkout` pool event for SET LOCAL**: fires too early —
  the tenant UUID is not yet in scope at pool-checkout time. Use
  `after_begin` per Crunchy Data + SQLAlchemy 2.0 guidance.
- **Per-tenant Postgres schema**: a tenant-per-schema approach is the
  alternative defense-in-depth pattern. It scales poorly past a few
  hundred tenants (`pg_class` bloat; introspection cost) and adds heavy
  migration complexity. Not the right shape for a B2C-leaning SaaS.

### Tradeoffs
- **Open question on child tables**: child-only tables (`video_metrics`,
  etc.) are reachable through parent tables that DO have policies, so
  application JOINs naturally filter them. The Issue 56 spec says "every
  table with a `creator_id` column" — honored literally; child tables get
  RLS in a future hardening if a query ever bypasses the parent join.
- **Silent UPDATE/DELETE failures**: with RLS, a mutation touching a row
  the current tenant doesn't own returns 0 rows affected with no error.
  Mutation paths must check rowcount and raise 404 rather than silently
  succeeding. Issue 79 implementation must audit every mutation path.
- **pgvector ANN index queries on `dna_embeddings`**: RLS policies are
  evaluated post-index-scan, so cross-tenant embeddings could briefly
  appear in ANN candidates before filtering. For current scale (closed
  beta, few hundred rows per creator) this is correctness-and-performance
  neutral; revisit at scale.
- **Migration role lockdown**: requires SSH access to the prod Postgres
  to grant `BYPASSRLS` to the migration role one time. Captured in
  `docs/DEPLOYMENT.md` for Issue 79.

### Source / evidence (RLS pattern + pgbouncer compatibility)
- Crunchy Data — Row Level Security for Tenants in Postgres:
  https://www.crunchydata.com/blog/row-level-security-for-tenants-in-postgres
- pganalyze — Using Postgres Row-Level Security in Ruby on Rails (pgbouncer
  transaction-mode compatibility):
  https://pganalyze.com/blog/postgres-row-level-security-ruby-rails
- Daniel Imfeld — PostgreSQL Row Level Security notes (pgbouncer
  statement-vs-transaction pooling):
  https://imfeld.dev/notes/postgresql_row_level_security
- Bytebase — Postgres RLS Footguns (FORCE RLS, owner bypass, silent
  failures): https://www.bytebase.com/blog/postgres-row-level-security-footguns/
- SQLAlchemy 2.0 Async I/O docs (sync_engine event listener pattern):
  https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
- SQLAlchemy Discussion #10469 (after_begin requires `connection.execute`,
  not `session.execute`, since 2.0.17):
  https://github.com/sqlalchemy/sqlalchemy/discussions/10469
- techbuddies.io — PostgreSQL RLS for Multi-Tenant SaaS:
  https://www.techbuddies.io/2026/02/04/how-to-implement-postgresql-row-level-security-for-multi-tenant-saas-2/
- Microsoft Azure Architecture — Postgres in Multi-Tenant Solutions:
  https://learn.microsoft.com/en-us/azure/architecture/guide/multitenant/service/postgresql
- Thenile — Shipping multi-tenant SaaS using Postgres RLS:
  https://www.thenile.dev/blog/multi-tenant-rls

### Files (this issue, decision-only)
- `docs/DECISIONS.md` — this entry.
- `docs/issues.md` — Issue 56 closed; new Issue 79 filed for the
  implementation.

### Date
2026-05-28

---

## 2026-05-28 — Issue 57: Automatic refund on terminal ingest failure

### What changed
- New module `billing/refund.py` with `refund_for_video(video_id)`. Looks up
  the `MinuteDeduction` for the video; if a refund `MinutePack`
  (`pack_id=f"refund:{video_id}"`) already exists, no-op; otherwise grant
  the same minute count back via `grant_minutes(reason="refund",
  pack_id=f"refund:{video_id}", price_cents=0)`.
- New Celery base class `RefundOnFailureTask` in `worker/tasks.py`. Its
  `on_failure` hook fires only when retries are exhausted; it extracts
  `video_id` from `args[0]`, refuses to crash the failure path on any
  internal exception, and dispatches `refund_for_video` via `run_async`.
- The three ingest-chain tasks — `ingest_video`, `transcribe_video`,
  `build_signals` — now use `base=RefundOnFailureTask`. `generate_clips`
  and `render_clip` do NOT — neither path deducts minutes, so refund is
  not applicable.
- `docs/COMPLIANCE.md` now includes a "Billing & Refund Policy" section
  with the disclosure language; this is the canonical user-facing
  disclosure until pricing / ToS pages land.

### Why
The product needed a policy. The choice between "automatic", "support-only",
and "hybrid (auto for our errors, manual for user-source errors)" was open;
the user delegated the call. The peer SaaS pay-per-use refund pattern is
unambiguous:

- **Stripe metered billing** auto-credits usage-record errors and surfaces
  them only in the customer portal billing history.
- **AWS service credits** auto-issue on SLA breach; visible in console,
  email is opt-in.
- **OpenAI compute charges** auto-refund on server-side API failures; usage
  dashboard surfaces them; per-call emails would create alert fatigue.
- **Twilio failed-message refunds** auto-credit, usage log only.

Convergent pattern: **automatic, immutable ledger entry, per-event email
only when material**. Honesty-constraint friendly ("you pay for what we
deliver"), low support burden, no abuse vector that isn't already bounded
by `max_retries=3` + per-video idempotency.

"All terminal failures" over "system errors only" because the classification
carve-out creates real edge cases (corrupt-but-decodable codec? DRM stripped
halfway?), demands a failure-reason taxonomy we don't have, and erodes trust
on the failure event itself. The abuse model — a user deliberately uploading
broken files to game the trial — costs us minutes that we'd refund anyway
(zero additional loss) plus compute we'd incur on retries (small dollar
amount; bounded by `max_retries`); the right knob for that is rate limiting
or per-creator quotas, not the refund policy.

### Alternatives ruled out
- **Support-initiated refunds**: high friction, doesn't match peer SaaS,
  creates a support queue we don't staff. Failure-mode UX would be: video
  shows "failed", balance reflects the deduction, creator has to find a
  support contact and email. Bad.
- **Hybrid policy (auto for system errors only)**: requires a
  `failure_reason` taxonomy plumbed through the three ingest tasks; demands
  a confidence call ("is this codec failure 'our fault' because we should
  support it, or 'their fault' because it's exotic?") that we can't make
  cleanly today. Revisit if/when we have meaningful corpus on real
  failures.
- **Refund minus a "we tried" overhead**: hard to communicate; erodes
  trust on the failure event; saves a trivial amount per failure relative
  to the support cost of explaining it.
- **`MinuteDeduction.refunded_at` column instead of compensating `MinutePack`
  row**: row mutation breaks the existing "immutable ledger" invariant.
  Both `MinuteDeduction` and `MinutePack` carry inline docstrings calling
  out immutability; the compensating-grant pattern preserves the
  event-sourcing audit trail; the schema already supports it (the `reason`
  column is a free-text label, and `pack_id` accepts arbitrary keys).
- **Per-video email + in-app banner notification (originally requested by
  the user)**: we have ZERO email infrastructure and ZERO notification
  surface. Bundling both into Issue 57 would explode a one-day refund-ledger
  PR into three separate systems. **Split out into Issues 58 (transactional
  email infrastructure) and 59 (in-app notifications surface)**, filed in
  `docs/issues.md`. Issue 57 ships with the immutable billing-history row
  as the only user-visible surface; the refund email and banner follow once
  the underlying infrastructure lands.

### Tradeoffs
- **Idempotency is read-then-write, not enforced by a UNIQUE constraint**.
  `MinutePack.pack_id` is not unique by itself. Two concurrent `on_failure`
  invocations for the same `video_id` could in principle race past the
  pre-check and both INSERT a refund row. This is not reachable in the
  current pipeline (the ingest chain is single-runner per video; Celery
  doesn't double-fire `on_failure` for one task instance), but if real
  concurrency emerges (e.g. a manual reprocessing endpoint) we should add
  a partial unique index `UNIQUE (pack_id) WHERE reason = 'refund'`
  via a future migration. Flagged in `billing/refund.py` module docstring.
- **`on_failure` swallows exceptions raised by the refund itself**. The
  worker's terminal failure must stand even if the refund crashes (e.g.
  transient DB outage at the precise moment the refund tries to write).
  Manual remediation via direct call to `refund_for_video(video_id)` is
  supported. A future hardening could add Celery retry semantics to the
  refund itself, but that adds complexity for a path that should already
  be rare.
- **Refund triggers on `failed` ingest only, not on Stripe purchase
  failures**: out of scope. Failed purchases never deduct minutes in the
  first place (the deduct happens on ingest, not on purchase).

### Source / evidence
- Read `MinutePack` / `MinuteDeduction` definitions at `models.py:434–480`
  — confirmed immutability docstrings, `reason` field shape, `pack_id` not
  unique, `stripe_session_id` unique-but-nullable.
- Read `billing/ledger.py:39–66` `grant_minutes` — confirmed it accepts
  arbitrary `reason` + `pack_id` kwargs and writes a `MinutePack` row +
  balance update in one session.
- Read the existing ingest chain at `worker/tasks.py:49–87` to confirm
  the failure path: `_set_status(failed)` + `self.retry(exc)`. The retry
  raises `MaxRetriesExceededError` on the final attempt; Celery's
  `on_failure` then fires exactly once.
- Celery `Task.on_failure` semantics: https://docs.celeryq.dev/en/stable/userguide/tasks.html#handlers
  ("Run by the worker when the task fails", fires only on final failure).
- Industry pattern confirmed against Stripe Billing credit balance docs,
  AWS Cost Anomaly Detection notification surfaces, OpenAI usage dashboard,
  Twilio Programmable Messaging usage logs.

### Files
- `billing/refund.py` — new (refund helper).
- `worker/tasks.py` — `RefundOnFailureTask` base; applied to `ingest_video`,
  `transcribe_video`, `build_signals`.
- `tests/test_billing_refund.py` — unit tests for `_refund_pack_id` and
  `RefundOnFailureTask.on_failure` dispatch/safety.
- `tests/test_billing_refund_integration.py` — three real-Postgres scenarios
  (deduct → refund net zero; idempotent on duplicate; pre-deduct failure is
  clean no-op).
- `docs/COMPLIANCE.md` — new "Billing & Refund Policy" section with
  user-facing disclosure language.
- `docs/issues.md` — Issue 57 closed; new Issues 58 + 59 filed as stubs.

### Date
2026-05-28

---

## 2026-05-28 — Issue 46: Generate-clips retry safety + outcomes 30-day floor

### What changed
- `clip_engine/ranking.py:generate_and_rank_clips` — the `DELETE FROM clips
  WHERE video_id = :vid` before reinsert is now narrowed to exclude `done` and
  `running` rows: `Clip.render_status.notin_([RenderStatus.done,
  RenderStatus.running])`. Pending and failed rows are still cleared.
- `worker/tasks.py:_generate_clips_async` — early-return idempotency guard:
  `select(Clip.id).where(Clip.video_id == video_uuid, Clip.render_status ==
  RenderStatus.done).limit(1)`; if a row is returned, log and return without
  invoking `generate_and_rank_clips`. The guard runs before the Signals lookup,
  so a retry on an already-rendered video no-ops even if Signals were never
  persisted.
- `worker/tasks.py:_poll_clip_outcomes_async` — added a 30-day floor on the
  Clip side of the join: `Clip.created_at > now - timedelta(days=30)`. Clips
  older than 30 days drop out of the polling set even when their `fetched_at`
  is past the 7-day arm.

### Why
Two distinct production hazards in one Celery task family:

1. **Late retry wipes rendered work**. `generate_clips` is configured with
   `max_retries=2, default_retry_delay=60`. If a retry fires after
   `render_clip` has already moved one or more rows to `done`, the previous
   unconditional `DELETE` would drop those rows, orphaning the rendered
   R2 objects and breaking the `ClipOutcome` FK chain (cascade delete on
   `clip_id`). The selective DELETE preserves anything in a terminal-success or
   in-flight render state; the idempotency guard short-circuits the whole task
   so the retry doesn't even re-extract candidates and re-rank them. Together
   they make `generate_clips` safe to retry at-least-once.
2. **Unbounded 7-day re-poll arm**. The WHERE was
   `or_(and_(performed_well.is_(None), fetched_at < cutoff_48h), fetched_at <
   cutoff_7d)`. The second arm has no upper bound on the clip's age — once a
   clip is past its 7-day checkpoint, every hourly run of
   `poll_clip_outcomes` would re-fetch its stats forever, burning YouTube Data
   API quota for a label flip that doesn't matter at that age. A 30-day floor
   matches the preference model's recency-decay horizon: a flip from
   `performed_well=False` to `True` for a 60-day-old clip would have a
   vanishing sample weight anyway.

### Alternatives ruled out
- **Make `generate_and_rank_clips` upsert-based on `(video_id, peak_s)`**:
  would eliminate the DELETE entirely but requires a new unique index +
  alembic migration, plus a way to delete stale candidates that no longer
  appear in the new ranking. Heavier than the acceptance criteria demand;
  the selective DELETE + idempotency guard hits the same correctness target
  with one-line changes and no schema work.
- **Bound the poll window by `ClipOutcome.published_at`** instead of
  `Clip.created_at`: `published_at` is nullable until the YouTube upload
  completes, so it would silently skip clips during the publish race window.
  `Clip.created_at` has a tz-aware default at row insert and is monotone.
- **30 vs 60 vs 90 days for the floor**: 30 days matches the recency-decay
  half-life used by `preference/decay.py:sample_weight`. A flip past one
  half-life contributes negligible weight to the next retrain.

### Tradeoffs
- **Selective DELETE keeps `running` rows around forever if render gets
  stuck**: acceptable. A separate Celery retry+timeout in `render_clip`
  (`max_retries=3, default_retry_delay=60`) drives `running` → `failed` on
  timeout/exception; the next `generate_clips` retry then sweeps the failed
  row out cleanly.
- **Idempotency guard is binary** (any `done` clip → skip entirely). For a
  video where rendering partially succeeded (some `done`, some `failed`),
  the retry will preserve all `done`/`running` rows but skip re-extracting
  candidates for the failed ones. Acceptable: the failed rows are still
  retried by `render_clip` itself; we don't re-rank a partially-rendered
  video.
- **30-day floor is not configurable**: hardcoded. If the recency-decay
  horizon changes (`preference/decay.py`) the two should stay aligned —
  flagged for future cleanup if either ever moves.

### Source / evidence
- Read `generate_and_rank_clips` at `clip_engine/ranking.py:65–119` —
  confirmed the unconditional DELETE on line 89 and the `session.commit()`
  follow-up on line 114.
- Read `generate_clips` Celery task at `worker/tasks.py:80–87` — confirmed
  `max_retries=2`, no idempotency check before `run_async`.
- Read `_poll_clip_outcomes_async` at `worker/tasks.py:376–460` — confirmed
  `cutoff_48h` is used in the `performed_well IS NULL` arm and is therefore
  self-bounding; the 7d arm is the unbounded one. (LEFT_OFF's framing of
  the 48h cutoff being the bug was slightly off; the actual bug is in the
  7d arm.)
- Celery retry-safety guidance: tasks must be safe under at-least-once
  redelivery, terminal-success rows must never be touched by a retry
  (https://docs.celeryq.dev/en/stable/userguide/tasks.html#avoid-launching-synchronous-subtasks).
- Standard sliding-window outcome polling pattern: bounded by both edges
  (Stripe webhook retry scheduler; Shopify Fulfillment polling docs).

### Files
- `clip_engine/ranking.py` — narrowed the DELETE WHERE (3 lines).
- `worker/tasks.py:_generate_clips_async` — early-return guard (12 lines).
- `worker/tasks.py:_poll_clip_outcomes_async` — 30-day floor added to the
  WHERE (3 lines including the `poll_floor` binding).
- `tests/test_outcomes.py` — two new predicate-level unit tests pinning
  the 30-day floor.
- `tests/test_generate_clips_retry_integration.py` — new `integration`-marked
  file with three scenarios: selective-DELETE preserves done+running and
  clears pending+failed; `_generate_clips_async` short-circuits when a done
  clip exists (even without Signals); `_poll_clip_outcomes_async` excludes
  clips >30 days old while polling fresh ones.

### Date
2026-05-28

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
