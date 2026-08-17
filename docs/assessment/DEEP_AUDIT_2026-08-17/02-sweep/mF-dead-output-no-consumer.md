# Modality F — Dead output: built, emitted, and consumed by nothing

**Swept:** 2026-08-17, Phase 2 of the deep standards audit. Tree at `main` / `1def133`.
**Interpreter:** `.venv/bin/python` throughout. Read-only pass — nothing in the tree was modified.

---

## What I swept, and how

Six measured passes, not assertions:

1. **Tables.** All 39 `__tablename__` classes in `models.py`. Counted `select(Model)` reads and
   constructor/`session.add` writes separately across the 16 production packages + root modules,
   excluding `tests/` and `alembic/`. Then hand-verified every table that came back with writes and
   zero reads.
2. **Columns.** AST-extracted all `mapped_column` declarations (models.py) and classified each
   reference across the production corpus as a *write* (`x.col = …` / `Model(col=…)`) or a *read*
   (`.col` in a non-assignment position, or inside `select(...)`). Reported the write-only set.
3. **API response fields.** AST-collected every class reachable from a `response_model=` declaration
   in `routers/` (101 models, 459 fields, transitive closure through nested annotations), then
   diffed the field names against the identifier token set of all of `frontend/src`.
4. **Routes.** AST-extracted all 120 route declarations with their router prefixes and probed
   `frontend/src` + `frontend/e2e` for each path's literal segments. 6 routes had no client hit;
   each was then hand-verified.
5. **Indexes.** All 50 `create_index` / `CREATE INDEX` sites in `alembic/versions/` plus the 4
   `sa.Index` in `models.py`, each matched against the query it was built for.
6. **Metrics / events / periodic work.** Traced every one of the 12 Prometheus instruments to the
   *process* that increments it and to the process that exports it; traced `log_event` /
   `record_event` writers to readers; walked the Celery Beat schedule for tasks whose product has no
   consumer.

**Honest yield.** The codebase is tighter than the brief's framing suggested: no orphan Python
modules, no orphan indexes, and only 3 fully-dead columns by raw reference count. The dead output
that *does* exist is concentrated in four places — **cost accounting**, **compliance capabilities**,
**version stamps**, and **worker-process telemetry** — and two of those are load-bearing enough to
be worth issues on their own. **Nine candidates below; F1, F2 and F3 are the ones I would defend
hardest.** I have deliberately not padded: several things that looked dead (crop-track,
proof-of-lift, originality guard, notifications, `ClipOutcome`, `AudienceActivity`) turned out to be
fully wired, and are named here only so nobody re-derives them.

---

## F1 — GDPR data export: three endpoints, a table, a Celery task, and a published Privacy Policy promise, with **zero UI**

**Severity: high.** `routers/export.py:46` (POST), `routers/export.py:92` (GET),
`worker/tasks.py:5133` (`generate_data_export`), `models.py:1488` (`DataExport`),
`alembic/versions/0027_data_exports.py`.

**What it claims.** `static/privacy.html:162`, live on the production site, tells every visitor:

> **Access & portability:** you may request a copy of your data at any time. AutoClip generates a
> machine-readable JSON export of your profile, channel DNA, videos and metrics, clips, feedback,
> outcomes, chat history, and billing records, with download links for your rendered clips. **The
> export is prepared on request and made available as a download from your account.**

`routers/export.py:1` is the matching implementation header: *"GDPR Art. 15/20 data export (Issue
249)."*

**Why it does not deliver that.** There is no affordance anywhere in the React app to request or
download an export. Not on Profile, not on Settings, not in any component. The sibling
right-to-erasure capability (`DELETE /auth/me`) *did* get a UI — the Profile "Danger zone", shipped
as Issue 158 and ticked off in `CLAUDE.md`'s pre-launch list — so the asymmetry is not a house
convention, it is an omission. The only way a creator can exercise Art. 20 portability today is to
hand-craft an authenticated POST.

Note also that the sweep independently flagged `ExportStatusOut.requested_at` and
`ExportStatusOut.completed_at` as fields no client reads — which is what a response model with no
client looks like from the other direction.

**Repro** (both commands return nothing; the third returns the promise):

```bash
grep -rn "me/export" --include="*.ts" --include="*.tsx" frontend/    # → 0 hits
grep -rni "export\|download.*data" frontend/src/pages/Profile.tsx \
     frontend/src/pages/Settings.tsx | grep -v "^.*:export "        # → 0 hits
grep -n "portability" static/privacy.html                            # → line 162, the promise
```

**Cost / believed capability.** The project believes it has shipped Art. 15/20 portability; a
regulator or a creator following the Privacy Policy would find no way to exercise it. Also burns a
table, a migration, an index, an R2 write path and a full-table-walk Celery task that no creator can
trigger.

---

## F2 — The `usage` cost ledger is written on every billed LLM call and read by nothing

**Severity: high.** `models.py:1304` (`Usage`), `billing/ledger.py:75` (`increment_usage`),
`billing/ledger.py:248-254` (the `record_llm_usage` tail).

**What it claims.** `tests/test_usage_coverage.py:1-16` is a repo-wide AST gate whose docstring
states:

> `record_llm_usage` (billing/ledger.py) is **the billing ledger write that deducts from the
> creator's token/minute pack**. If a task runs inference without calling it, creators get LLM calls
> that are unmetered — **a real revenue leak at scale**.

`models.py:1316-1319` adds: *"Cost estimate in USD persisted at write time so billing/metrics can
read USD without a price-book join at query time."*

**Why it does not verify that.**

- `record_llm_usage` does not touch `Creator.minutes_balance` or `MinuteDeduction`. Minutes are
  deducted from **video duration at ingest** (`worker/tasks.py:2212`), on a completely separate
  path. Nothing in the app has ever charged a creator from `usage`.
- The `usage` table has **zero readers**. `select(Usage)` appears nowhere; there is no `/usage`
  endpoint, no admin query, no raw SQL, no reconciliation job. `billing/spend_guard.py` — the thing
  that *does* consume cost — reads Redis µ$ counters and imports only `Creator` from `models`
  (`billing/spend_guard.py:56`).
- The other two consumers named at `billing/ledger.py:236-246` are `record_llm_cost` (a Prometheus
  counter — see F3) and `spend_guard.record_spend` (Redis). Only the Redis rail is live.
- Two of the table's five payload columns, `Usage.videos_processed` and `Usage.clips_generated`,
  are **never written either** — declared, migrated, and permanently 0.

**Repro:**

```bash
grep -rn "select(Usage\|FROM usage\|Usage\." --include="*.py" routers worker billing scripts *.py \
  | grep -v "billing/ledger.py"          # → 0 hits: no reader anywhere
grep -rnw "videos_processed\|clips_generated" --include="*.py" . | grep -v models.py   # → 0 hits
```

**Cost / believed capability.** Every billed LLM call opens a **second admin DB session** and runs
an `INSERT … ON CONFLICT DO UPDATE` inside a savepoint (`billing/ledger.py:113-114`) to write a row
nobody reads — a per-call latency and connection cost on the hot path of ~41 Celery tasks and every
LLM endpoint. `docs/GO_LIVE.md:83` asks *"Will we hear about cost blowouts?"* and answers **OPEN**;
the durable per-creator, per-month cost ledger that would answer it exists and is unread. The
project believes it has FinOps cost-per-unit accounting; it has a write-only table plus a Redis
counter that expires in 35 days.

---

## F3 — Six of twelve Prometheus metrics are emitted in a **different process** from the only `/metrics` exporter, so they can never be scraped — `METRICS_TOKEN` is irrelevant

**Severity: high.** `observability.py:33` (single default registry), `main.py:476-498` (the only
exporter), `docker-compose.prod.yml:2/35/56/82` (app, worker, render-worker, beat — four separate
containers).

**What it claims.** `docs/assessment/DEEP_AUDIT_2026-08-17/00-groundtruth/process-map.md:298-302`
lists 12 instrumented metrics and attributes their absence to a single cause: *"`/metrics`
auto-disables in production if `METRICS_TOKEN` is unset."* That framing implies setting
`METRICS_TOKEN` would restore them. It would not.

**Why it does not verify that.** `prometheus_client` keeps counters in a **per-process** registry.
`generate_latest()` is called only from the FastAPI `/metrics` route (`main.py:497` →
`observability.py:582`), which runs in the `app` container. There is no
`PROMETHEUS_MULTIPROC_DIR`, no `start_http_server`, no `make_asgi_app`, no pushgateway anywhere in
the tree. Therefore anything incremented in `worker` / `render-worker` / `beat` is written to a
registry that is never rendered:

| Metric | Incremented at | Process | Scrapeable? |
|---|---|---|---|
| `BEAT_LOCK_SKIPS_TOTAL` | `worker/tasks.py:228` | beat/worker | **No** |
| `RENDER_FAILURES_TOTAL` | `worker/tasks.py:954,973,979,1011` | render-worker | **No** |
| `R2_BYTES_STORED` / `R2_OBJECTS` | `worker/tasks.py:1406-1407` | beat | **No** |
| `CELERY_TASK_DURATION` / `CELERY_TASKS_TOTAL` | `observability.py:603,606` (celery signal) | worker | **No** |
| `LLM_TOKENS_TOTAL` / `LLM_COST_USD_TOTAL` | `observability.py:229-251` | **both** — but the majority of LLM calls (DNA build, clip scoring, video context, chapters, hooks, titles, improvement brief, thumbnail concepts) run in Celery | app-side sliver only |
| `HTTP_REQUEST_DURATION`, `DB_POOL_CHECKED_OUT`, `CELERY_QUEUE_DEPTH`, `REDIS_USED_MEMORY_BYTES` | app middleware / the `/metrics` handler itself | app | Yes |

**Repro:**

```bash
grep -rn "start_http_server\|make_asgi_app\|PROMETHEUS_MULTIPROC\|multiproc" --include="*.py" .   # → 0 hits
grep -rn "generate_latest" --include="*.py" .    # → exactly 2: the import and observability.py:582
grep -n "metrics" main.py                        # → the route, app process only
grep -n "^  [a-z]" docker-compose.prod.yml       # → app / worker / render-worker / beat, 4 processes
```

Practical repro: set `METRICS_TOKEN`, curl `/metrics`, and observe that `celery_tasks_total`,
`render_failures_total`, `beat_lock_skips_total`, `r2_bytes_stored` and `r2_objects` are absent no
matter how much work the workers do.

**Rider — a Beat task that pays money to feed two of them.** `worker/tasks.py:1385`
`collect_storage_gauges` (Issue 293, scheduled daily at `worker/schedule.py:107`) paginates the
**entire R2 bucket** across five prefixes (`worker/storage.py:117-133`, `list_objects_v2` — billable
Class A/B operations, unbounded in object count) for the sole purpose of calling `.set()` on
`R2_BYTES_STORED` / `R2_OBJECTS` in the beat process. The result is discarded on process exit. It is
never logged, never persisted, never compared to a threshold. This is the purest instance in the
sweep: **recurring paid work whose only output is a variable in a registry nobody renders.**

**Cost / believed capability.** The project believes it has COGS visibility, render-failure
tracking, Celery task-duration SLOs and beat-lock diagnostics. It has none of them, and fixing
`METRICS_TOKEN` will not change that — this needs a multiprocess registry or a worker-side exporter.

---

## F4 — `VideoContext.prompt_version` and `.model`: version stamps that nothing compares, so a prompt-version bump can never invalidate a stale row

**Severity: medium-high.** `models.py:563-564`, written at `worker/tasks.py:666-671`, idempotency
check at `worker/tasks.py:612` and `:663`.

**What it claims.** The columns exist so a row can be identified as generated under an older prompt
or model. `analysis/video_context.py:62` is already at `PROMPT_VERSION = 3` — it has been bumped
twice (v3 = Issue 463, "identity → user turn, DNA-only cached block 2"), and
`settings.ANTHROPIC_MODEL_VIDEO_CONTEXT` moved to Opus 5 on 2026-08-05.

**Why it does not verify that.** Nothing reads either column. The idempotency guard is
version-blind:

```python
# worker/tasks.py:612
if await session.get(VideoContext, video_uuid) is not None:
    logger.info("video_context already exists for video %s — skipping", video_id)
    return
```

Both consumers (`routers/clips.py:454`, `worker/tasks.py:779`) read only `.context_jsonb`. So every
video whose context was built under prompt v1 or v2, or under the pre-Opus-5 model, keeps that
output **forever** and continues feeding it into clip ranking (`clip_engine/ranking.py:288-299`) and
clip-metadata generation. The stamp that exists to detect exactly this is inert.

**Repro:**

```bash
grep -rn "prompt_version" --include="*.py" . | grep -v tests | grep -v alembic
# → models.py:564 (declaration) and worker/tasks.py:670 (write). No read.
```

**Cost / believed capability.** The project believes prompt versions can be rolled forward. They
cannot — a prompt improvement only reaches videos ingested after it ships, silently, with no way to
tell which rows are stale. Every prior audit's "the scorer got better" claim is diluted by an
unknown fraction of v1/v2 context rows.

**Same shape, smaller blast radius:** `settings.PRICE_BOOK_VERSION` (`config.py:199`) is documented
as *"a version mismatch between a stored cost_estimate and this stamp signals a rate-change event
(FinOps Foundation cost-per-unit standard)"* — but it is **stored nowhere**. There is no
`price_book_version` column on `usage` or anywhere else, so no mismatch can ever be computed. Its
only two references outside `config.py` are `tests/test_usage_ledger.py:139` (asserts the string is
non-empty) and `:153` (asserts it equals the literal `"2026-08-04"`) — a check that verifies a
constant equals itself.

---

## F5 — Clickwrap consent versions are recorded once at signup and never compared, so a policy bump re-prompts nobody

**Severity: medium.** `models.py:242-257` (`terms_accepted_at`, `terms_version`, `privacy_version`,
`minimum_age_confirmed_at`), written at `routers/auth.py:214-229`.

**What it claims.** `config.py:893-896`, verbatim:

> Bump `TOS_VERSION` or `PRIVACY_VERSION` (ISO-8601 date) whenever a material change is published to
> `/static/tos.html` or `/static/privacy.html`. **The recorded version string on each Creator row
> lets a future re-prompt path compare the stored version against the current one and gate the OAuth
> CTA.**

`routers/auth.py:211-212` repeats it: *"We store the version strings shown at acceptance so a future
re-prompt path can detect material ToS/Privacy changes by comparing stored vs current."*

**Why it does not verify that.** All four columns are write-only — the comparison path does not
exist, and the write itself sits inside the `if is_new:` branch, so it only ever fires at account
creation. This is not hypothetical: **`PRIVACY_VERSION` has already been bumped**, `2026-06-23` →
`2026-07-02`, for the GPC-recognition clause (Issue 302) and the backup-erasure ceiling disclosure
(Issue 254) — both material. Every creator who signed up before that date still carries
`privacy_version = '2026-06-23'` and has never been shown, let alone accepted, the current policy.

**Repro:**

```bash
grep -rn "terms_version\|privacy_version\|terms_accepted_at\|minimum_age_confirmed_at" \
  --include="*.py" --include="*.tsx" . | grep -v tests | grep -v alembic
# → models.py declarations + routers/auth.py:214-216,229 writes. Zero reads, zero comparisons.
```

**Cost / believed capability.** The project believes it has versioned clickwrap consent with a
re-prompt hook ready to go. It has a versioned *record* and no mechanism — the data is accumulating
for a consumer that was never built, and one material bump has already passed through unnoticed.

---

## F6 — `GET /billing/packs` is dead, and the price the customer sees is a hand-duplicated TS literal guarded by a test that checks nothing

**Severity: medium.** `routers/billing.py:116` (`list_packs`, `PackOut`),
`frontend/src/pages/Pricing.tsx:19-30`, `frontend/src/pages/Landing.test.tsx:37-42`.

**What it claims.** `billing/packs.py:1-3` — *"Add new packs here; the router and checkout session
pick them up automatically."* `frontend/src/pages/Landing.test.tsx:37` names itself **`renders real
pricing figures matching billing/packs.py`**.

**Why it does not verify that.** No client ever calls `/billing/packs`. `Pricing.tsx:19-30` carries
its own `PACKS` const — six packs, prices and per-minute rates retyped by hand — under the comment
*"keep in sync with billing/packs.py ALL_PACKS … TODO: drive from /billing/packs API to eliminate
DRY drift"*. `billing/packs.py:23-28` acknowledges the same duplication from the other side. And the
test that claims to enforce parity does this:

```tsx
it('renders real pricing figures matching billing/packs.py', () => {
  render(<Landing />)
  expect(screen.getByText('$18.00')).toBeInTheDocument()
  expect(screen.getByText('$70.00')).toBeInTheDocument()
  expect(screen.getByText('$400.00')).toBeInTheDocument()
})
```

Three hardcoded strings. It never reads `billing/packs.py`. Change `Pack("starter", …, 1800)` to
`2400` in Python and this test stays green while the marketing page quotes the old price and Stripe
charges the new one. `PackOut.price_usd` and `PackOut.per_minute_usd` — the two computed fields that
exist precisely to be the single source of pricing truth — were both flagged by the response-field
diff as reaching no client.

**Repro:**

```bash
grep -rn "billing/packs\|/packs" --include="*.ts" --include="*.tsx" frontend/src | grep -v "\.test\."
# → only the two "keep in sync" comments. No fetch.
```

**Cost / believed capability.** The project believes pricing has one source of truth with a test
pinning the frontend to it. It has two sources and a test that pins the frontend to itself. This is
a money-path drift trap with a green light over it.

---

## F7 — `GET /creators/me/thumbnail-patterns`: a hardened, rate-limited, billed vision endpoint with no caller

**Severity: medium.** `routers/thumbnails.py:167-182`.

**What it claims.** Everything about this endpoint asserts that it is a live, load-bearing, expensive
surface: three stacked slowapi limits (`10/hour` + `LLM_DAILY_LIMIT` + `BRIEF_DAILY_LIMIT`), both
`require_flag("llm_generation")` and `require_budget` dependencies, a `check_positive_balance` call,
a 24 h Redis cache, and a per-creator single-flight lock added as **SEV1 #3**
(`routers/thumbnails.py:103-149`). It is pinned by `tests/test_rate_limiting.py:110`,
`tests/test_creator_quota.py:33`, `tests/test_flags.py:416`, `tests/test_usage_coverage.py:176-178`
and ~8 tests in `tests/test_thumbnails.py`.

**Why it does not verify that.** No client calls it. The only frontend reference to anything
thumbnail-shaped is `ThumbnailConcepts.tsx:18`, which POSTs to `/thumbnail-concepts` — a *different*
endpoint whose Celery task (`worker/tasks.py:5944`) calls `analyze_thumbnail_patterns` internally.
The underlying vision analysis is alive; the read endpoint wrapping it is not.

**Repro:**

```bash
grep -rn "thumbnail-patterns" --include="*.ts" --include="*.tsx" frontend/   # → 0 hits
grep -rn "thumbnail" --include="*.tsx" frontend/src | grep -v test           # → thumbnail-concepts only
```

**Cost / believed capability.** Two SEV1-grade hardening cycles, a Redis lock protocol, and five
test files were spent making an unreachable endpoint safe. Not a live defect — a live *misallocation
signal*: the gates all point at a surface with no users, and none of them can tell you that.

---

## F8 — `demographics`: a paid YouTube Analytics report fetched for every creator, refreshed daily, purged on a schedule, and read by nothing

**Severity: medium.** `models.py:503` (`Demographics`), fetched at `youtube/analytics.py:215-226`
(`fetch_demographics`), upserted at `youtube/analytics.py:423-431`, purged at
`worker/tasks.py:4399`.

**What it claims.** `README.md:27` — *"it pulls your YouTube Analytics (retention curves,
**demographics**, activity windows) … then builds a versioned channel DNA profile"*.
`walkthrough.md:11` — *"We analyse your top and bottom performers, your retention curves, your
audience activity windows, and **your demographics**. We synthesise that into … your Creator DNA."*
`docs/SOT.md:25` and `docs/COMPLIANCE.md:97/156` both list it as a stored, scope-justified data
class.

**Why it does not deliver that.** `select(Demographics)` appears **nowhere** in production code.
`dna/builder.py` imports `AudienceActivity` (`dna/builder.py:17`, read at `:321`) and never
`Demographics`. `chat/tools.py` reads `AudienceActivity`, `RetentionCurve`, `VideoMetrics`,
`ClipOutcome` — not `Demographics`. No router serializes it. No frontend component displays it. The
only non-write references in the whole tree are the purge task and three integration test files.

**Repro:**

```bash
grep -rnw "Demographics" --include="*.py" routers worker dna chat knowledge upload_intel improvement clip_engine
# → youtube/analytics.py (writes) and worker/tasks.py:4399 (delete). No read.
```

**Cost / believed capability.** A `viewerPercentage` report request against the YouTube Analytics
API per creator per daily refresh — real quota against a scope
(`yt-analytics.readonly`) that `docs/COMPLIANCE.md:156` justifies to Google *by naming demographics
as a purpose*. The claim on the marketing surface, the walkthrough, the SOT and the compliance
register is that demographics feed the DNA. They feed nothing. **Under data-minimisation this is the
worst kind of dead output: PII-adjacent data collected under a stated purpose that does not exist.**

---

## F9 — The `event_logs` read surface, and two columns nothing ever writes

**Severity: low-medium.** `routers/logs.py:39` (`GET /api/logs/me`), `models.py:1386` (`EventLog`),
`event_log.py:93` (`record_event`).

**What it claims.** `routers/logs.py:1-7` — *"Read surface for the beta event log (Issue 151).
`/api/logs/me` returns the requesting creator's own recent events."*

**Why it does not deliver that.** No client calls `/api/logs/me`. The frontend writes to this rail
enthusiastically — `frontend/src/hooks/useActivityTelemetry.ts` POSTs every click, form submit and
route change to `/api/activity` (`frontend/src/lib/activity.ts:23`), and `routers/activity.py:96`
turns each one into a committed row on a dedicated engine — but nothing reads it back. The docstring
does name a fallback receiver (*"for beta, operators query the `event_logs` table directly"*), which
is why this is low-medium rather than high; the *endpoint* is nonetheless dead weight.

Two of the nine columns `EventLogItemOut` serializes are worse than unread — they are never
**written**. `status_code` and `duration_ms` exist on the model (`models.py`), on the migration
(`0025_event_logs.py`), in both `record_event` and `record_event_nowait` signatures
(`event_log.py:102-103, 150-151`), and in the response schema (`routers/logs.py:30-31`). All three
production call sites — `routers/activity.py:96`, `billing/spend_guard.py:176`, `flags.py:152` —
pass neither. They are permanently NULL.

**Repro:**

```bash
grep -rn "logs/me" --include="*.ts" --include="*.tsx" frontend/    # → 0 hits
grep -rn "record_event(" --include="*.py" . | grep -v tests | grep -v "^./event_log.py"
# → 3 call sites; none passes status_code or duration_ms
```

**Cost / believed capability.** A DB row and a commit per UI click, on an unauthenticated
IP-rate-limited route, into a 90-day-retained table (`purge_stale_event_logs`, daily) whose only
in-app reader is an endpoint nobody calls. The believed capability is per-creator beta telemetry the
creator (or support) can look at; the actual capability is a `psql` session.

---

## Also swept, and genuinely alive — do not re-derive

`ClipOutcome` (read by `preference/train.py:130`, `preference/lift.py`, `routers/insights.py:536`,
`chat/tools.py:377`) · `AudienceActivity` (4 readers incl. `dna/builder.py:321`) · crop-track
(`frontend/src/hooks/useCropTrack.ts:20`) · proof-of-lift
(`frontend/src/components/insights/ProofOfLift.tsx`) · originality guard · notifications (list +
dismiss + preferences all wired to `ActivityPanel.tsx`) · saved insights · all 50 alembic indexes
matched to a query (`ix_creator_insight_creator_video` → `routers/insights.py:870-874`;
`ix_clip_outcomes_poll_candidates` → `poll_clip_outcomes`; `ix_creators_refresh_order` → the beat
fan-out) · every module under `analysis/`, `knowledge/`, `improvement/`, `upload_intel/` is imported
from a live path.

---

## Off-class

Not dead output — ordinary defects noticed in passing, filed here so they are not lost.

1. **`frontend/src/components/insights/PerformerPanel.tsx:69` reports success over a failed write.**
   ```tsx
   await api(`/creators/me/insights/save/${analysis.id}`, { method: 'POST' }).catch(() => {})
   setSaved(true)
   ```
   Every error is swallowed and the UI unconditionally renders "Saved". This is the same shape as
   `docs/OFF_COURSE_BUGS.md:22` (`YourCall.tsx:124`, a failure string rendered in success green) —
   an honesty inversion the project has already paid for once. Compounding it: `POST
   /insights/save/{id}` is a **toggle** (`routers/insights.py:1021`, `insight.is_saved = not
   insight.is_saved`) and the response's `is_saved` is discarded, so a double-click silently
   *unsaves* while the button still says "Saved".

2. **`worker/tasks.py:1385` `collect_storage_gauges` swallows every exception per prefix** and, when
   the sweep fails, *"the gauge keeps its last value"* — a stale reading that is
   indistinguishable from a fresh one. Moot today because of F3, but it becomes a live wrong-data
   hazard the moment the metrics rail is fixed.

3. **`expire_trials` (`worker/schedule.py:62-69`) is a daily task whose entire product is a log
   line**, self-documented as *"Watchdog only … so we can see funnel drop-off."* With zero alert
   rules in the repo (`process-map.md:328`) and no log-based monitoring, nothing sees it. Honest and
   intentional, so not filed above — but it is a signal with no receiver by construction.
