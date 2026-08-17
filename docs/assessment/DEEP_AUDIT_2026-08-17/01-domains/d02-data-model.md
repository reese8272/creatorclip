# d02 — Data model & Postgres

**Domain:** data model, Postgres schema, migrations, pgvector, retention.
**Auditor pass:** 2026-08-17, deep standards audit. Read-only.
**Scope judged against:** ≤100-user private beta on one DigitalOcean VM (DECISIONS 2026-06-26).

---

## Verdict

The migration discipline in this repo is genuinely top-decile — better than most funded teams
I would expect to review. Individual migrations (0057, 0059, 0062) are self-documenting,
offline-mode-aware, idempotent, expand-only, and reason explicitly about the rolling-restart
overlap window. The schema itself is sane: 39 tables, deliberate denormalisation with a named
house pattern, correct enum handling, and RLS as a structural tenancy property.

Where it is weak is **the boundary between the models and the database**: 20 of 28 production
indexes exist only in migrations and are invisible to `Base.metadata`, and there is no
`alembic check` gate, so nothing in CI notices when the two diverge. And there are two live
instances of the project's own #1 failure class in this domain: a pgvector HNSW index (plus a
green integration test attesting to it) that serves a query which **does not exist anywhere in
the codebase**, and a `clip_impressions` table written on every clip-list request that **no
production code reads**. Both are cheap to fix and neither is an outage — but both are green
signals over things that do nothing.

---

## What the current (2026) standard is, with sources

### 1. JSONB vs. relational columns in Postgres 16

The 2026 consensus is a **hybrid**: stable, frequently-filtered, frequently-updated fields become
typed columns; variable/rarely-queried payloads stay JSONB.

- **Promotion trigger:** "the more often a key participates in filtering or reporting, the more
  likely it should be promoted into the relational model", and "promote frequently *updated*
  keys because the full-document rewrite cost of `jsonb_set()` will hurt at scale"
  ([sqlpad](https://sqlpad.io/tutorial/postgresql-jsonb-vs-columns-performance-guide/),
  [Heap](https://www.heap.io/blog/when-to-avoid-jsonb-in-a-postgresql-schema)).
- **Postgres cannot partially update a JSONB datum** — changing one key rewrites the whole
  document and generates TOAST churn + WAL
  ([Snowflake/Postgres TOAST guide](https://www.snowflake.com/en/blog/engineering/postgres-jsonb-columns-and-toast/)).
- **The 2 KiB cliff is the number that matters:** query performance on JSONB values degrades
  **2–10×** once the value exceeds ~2 KiB, because the datum is TOASTed out of line
  ([Evan Jones, *Postgres large JSON value query performance*](https://www.evanjones.ca/postgres-large-json-performance.html)).
  The corollary is the actionable rule: **do not select a large JSONB column you are not going
  to use** — "you can select hundreds of rows but NOT the very large text column."
- **Detoast is not shared within a query**: `SELECT big->'a', big->'b'` detoasts the datum twice
  ([pgsql-hackers thread](https://www.postgresql.org/message-id/87ttoyihgm.fsf%40163.com)).

### 2. JSONB payload versioning

There is no single standard, but the two recognised patterns are (a) an in-payload `version` /
`schemaVersion` discriminator plus **read-time upcasters** that transform old payloads to the
current shape, and (b) expand→dual-write→parity-check→flip→drop for the payload itself, i.e. the
same expand/contract discipline applied inside the document
([JSON Schema Versioning & Evolution Guide 2026](https://www.zerodatatools.com/blog/json-schema-versioning-guide/),
[Zero-Downtime PostgreSQL JSONB Migration](https://medium.com/@shinyjai2011/zero-downtime-postgresql-jsonb-migration-a-practical-guide-for-scalable-schema-evolution-9f74124ef4a1)).
The load-bearing half is the **upcaster**: a version discriminator with no upcast path and no
backfill is a latent silent-wrong-answer generator, not a versioning strategy.

### 3. Index ownership and schema drift

Alembic's own documentation is explicit that autogenerate **does** compare indexes and named
unique constraints, that it "is not intended to be perfect", and that the mechanism for keeping
models and DB in sync in CI is **`alembic check`** (added in Alembic 1.9), which runs the
autogenerate comparison and **exits non-zero if any operation would be emitted**
([Alembic autogenerate docs](https://alembic.sqlalchemy.org/en/latest/autogenerate.html)).
This is now the default recommendation in CI writeups and has been adopted by projects as large
as Airflow ([apache/airflow#48998](https://github.com/apache/airflow/issues/48998),
[Practical checks when working with alembic migrations](https://ldirer.com/blog/posts/practical-checks-alembic-migrations)).
The standard is therefore *not* "indexes must live in models.py" — it is **"whatever the DB has,
`Base.metadata` must also have, and CI must prove it."**

### 4. Migration practice

Expand → migrate → contract across separate deploys is the settled 2026 pattern; the invariant is
that **the application version running concurrently with the schema must tolerate it**
([2026 zero-downtime guide](https://dev.to/young_gao/database-migrations-in-production-zero-downtime-schema-changes-5fng),
[Harness](https://www.harness.io/blog/zero-downtime-database-migrations-safe-schema-changes),
[Wellhausen, *Expand and Contract*](https://www.tim-wellhausen.de/papers/ExpandAndContract/ExpandAndContract.html)).
For NOT NULL specifically, the complete PG12+ recipe is **four** steps — `ADD CONSTRAINT … CHECK
(col IS NOT NULL) NOT VALID` → `VALIDATE CONSTRAINT` → `ALTER COLUMN … SET NOT NULL` (which
skips the table scan because a valid CHECK proves it) → `DROP CONSTRAINT`
([PostgreSQL commit "Allow ALTER TABLE .. SET NOT NULL to skip provably unnecessary scans"](https://www.postgresql.org/message-id/E1h43Ru-0005An-1w%40gemulon.postgresql.org),
[Haki Benita](https://hakibenita.com/postgresql-unconventional-optimizations)).

### 5. pgvector + RLS / filtered ANN

The real hazard with an approximate index plus a filter is **not a leak — it is silent recall
collapse**. With HNSW, the index returns a fixed candidate set (`hnsw.ef_search`, default 40) and
the filter is applied afterwards, so a selective filter can return far fewer rows than the
`LIMIT` asked for — pgvector's own docs use the example "if a condition matches 10% of rows … 4
rows will match on average." **pgvector 0.8.0 (Oct 2024) fixed this with iterative index scans**:
`hnsw.iterative_scan = strict_order | relaxed_order`, bounded by `hnsw.max_scan_tuples` (20,000)
and `hnsw.scan_mem_multiplier`
([pgvector docs](https://access.crunchydata.com/documentation/pgvector/latest/pdf/pgvector.pdf),
[Nile: pgvector 0.8.0](https://www.thenile.dev/blog/pgvector-080),
[dbi-services pgvector for DBAs, updated March 2026](https://www.dbi-services.com/blog/pgvector-a-guide-for-dba-part-2-indexes-update-march-2026/),
[ClickHouse: scaling vector search in Postgres](https://clickhouse.com/resources/engineering/scale-vector-search-postgres)).
The 2026 default advice for a tenant-filtered corpus of this size (thousands of vectors) is
simpler still: **do not use an ANN index at all** — exact scan is faster and exact below roughly
10⁴–10⁵ vectors.

---

## Findings

### F1 — The pgvector HNSW index serves a query that does not exist, and a green integration test says otherwise

**Severity: medium · Confidence: high · Verdict: over-engineered (and a vacuous-green instance)**

`alembic/versions/0006_vector_and_fk_indexes.py:40-42` builds
`ix_dna_embeddings_hnsw ON dna_embeddings USING hnsw (embedding vector_cosine_ops) WITH (m=16,
ef_construction=200)`, documented as matching "the `<=>` cosine query".
`tests/test_vector_index_integration.py:35-39` asserts the index exists, with the comment
"Must be an HNSW index over the cosine op class (matches the `<=>` query)."

**There is no `<=>` query.** A repo-wide grep for every pgvector operator and helper
(`<=>`, `<->`, `<#>`, `cosine_distance`, `l2_distance`, `max_inner_product`) over all non-test,
non-alembic Python returns **zero hits**. The only production reader of `dna_embeddings` is
`routers/insights.py:666-671`, which selects rows by exact predicate
(`creator_id`, `kind`, `ref_jsonb->>'clip_id' IN (…)`), pulls whole 1024-dim vectors into Python,
and computes cosine similarity in `knowledge/originality.py`. The only `<=>` in the tree is
`tests/test_db.py:57`, and it is `… WHERE id = :id` — a point lookup that cannot use an HNSW
index either.

**Failure scenario (cost, not correctness):** every `embed_clip_excerpts` / `embed_patterns` /
`embed_brief` insert (`dna/embeddings.py:101,137,195`) pays an HNSW graph insertion at
`ef_construction=200` — ~200 distance computations over 4 KB float4 vectors plus index page
writes and WAL — on the same VM that is running ffmpeg and MediaPipe. Every row inserted, forever,
for zero read benefit. Meanwhile the integration test reports GREEN on infrastructure with no
consumer, which is precisely the shape `docs/AUDIT_BRIEF.md:191-193` describes.

**This also moots the deferral in Issue 56.** `docs/DECISIONS.md:9535-9539` records: *"pgvector
ANN index queries on `dna_embeddings`: RLS policies are evaluated post-index-scan, so cross-tenant
embeddings could briefly appear in ANN candidates before filtering. For current scale … this is
correctness-and-performance neutral; revisit at scale."* **Answering the brief's question 5
directly: at 100 users it is neither a correctness nor a performance problem — because there is
no ANN scan at all, at any scale.** The recorded position is also subtly mis-stated: the real
risk of post-index-scan filtering is not exposure (the LIMIT applies after the RLS qual, so you
never *see* another tenant's row) — it is **recall collapse**, returning 1 row when you asked for
10. That failure mode is invisible in tests and would look like "the originality check found
nothing similar."

**Recommendation:** drop the index, delete the assertion, and record a one-line decision: *"we
compute similarity exactly, in Python, over ≤N per-creator vectors; when an ANN query is written,
the index comes back together with `hnsw.iterative_scan` set."* Cheaper and more honest than
carrying a deferral for a risk that cannot fire.

---

### F2 — `list_clips` selects a ~15–20 KB TOASTed JSONB column for up to 100 rows in order to compute one boolean

**Severity: medium · Confidence: high · Verdict: deviation-unjustified**

`routers/clips.py:824-830` issues `select(Clip)` — the full ORM entity, no `load_only`, no
`defer` — for up to 101 rows. `_clip_response` (`routers/clips.py:334`) uses exactly one thing
from the widest column on the table:

```python
"has_crop_track": clip.reframe_track_jsonb is not None,
```

`models.py:862-870` documents the size itself: *"the list surface only carries the boolean
`has_crop_track` (a track is ~15–20 KB)"* — the intent was explicitly to keep the track off the
list surface, and the query defeats it. The size is structural, not incidental:
`clip_engine/reframe.py:1034-1075` emits one keyframe per sample at
`REFRAME_SAMPLE_FPS = 5.0` (`config.py:374`), so a 90 s clip (the Issue-427 clamp) carries ~450
`{"t":…,"x":…}` entries plus `cuts`, `shots`, `speakers`, `region`.

**Failure scenario:** a creator opens the Review page for a video with 12 auto-rendered clips.
Postgres detoasts and ships ~180 KB of JSONB (≈1.5–2 MB at the 100-clip cap), psycopg parses all
of it into Python dicts, SQLAlchemy hydrates it into ORM instances — and every byte is discarded
after an `is not None`. Per the measured 2–10× penalty on >2 KiB JSONB values, this is the single
most expensive thing in a request that is otherwise a small indexed range scan, and it is on the
hottest read path in the product. Two identical instances of the same class:
`clips.signals_jsonb` is loaded whole to read two string keys, and `dna/builder.py:153-156`
(`_load_hook_texts`) loads every `Transcript` entity in full — `segments_jsonb` is the largest
payload in the system — to extract one opening sentence per video.

**Recommendation (one line, not a schema change):** add
`.options(load_only(...))` naming the ~20 scalar columns `_clip_response` actually uses, and
replace the boolean with a projected expression
(`(Clip.reframe_track_jsonb.is_not(None)).label("has_crop_track")`). Same for
`_load_hook_texts` (`select(Transcript.video_id, Transcript.segments_jsonb['segments'][0])`).

---

### F3 — 20 of 28 production indexes are invisible to `Base.metadata`, and nothing in CI compares the two

**Severity: medium · Confidence: high · Verdict: gap**

`alembic/env.py:18` sets `target_metadata = Base.metadata`, so `alembic revision --autogenerate`
is fully wired — and there is **no `include_object` filter**. But of the 28 named indexes created
across `alembic/versions/*.py`, **20 do not exist in `Base.metadata` at all**, including
`ix_clips_video_id`, `ix_clips_creator_id`, `ix_videos_creator_id`, `ix_clip_feedback_creator_id`
and `ix_dna_embeddings_hnsw`. (Verified by loading `models.py` under `.venv/bin/python` and
diffing `Base.metadata` index names against the migration set.) Conversely, 9 indexes declared in
`models.py` — e.g. `ix_creator_insight_creator_video` (`models.py:1188`),
`uq_summaries_active` (`models.py:1537`) — were created by `op.create_table(...)`/model-driven
DDL and never appear as a standalone `create_index` call, so the two sets barely overlap.

**Failure scenario A (the drift itself):** a session runs
`alembic revision --autogenerate -m "add X"`. Alembic emits `op.add_column` for X **plus 20
`op.drop_index` operations**. The reviewer sees a 60-line migration where they expected 4 and
either hand-prunes it (the repo's saving grace so far) or misses one. `migration-lint` is
**advisory, not required** (`docs/BRANCHING.md:100-127` / process-map §1), and its
downgrade round-trip compares upgrade→downgrade→upgrade, which a *deliberate* drop passes
cleanly. Result: `list_clips`' `WHERE video_id = …` becomes a seq scan on `clips`, and
`DELETE FROM creators` (right-to-erasure) seq-scans every child table.

**Failure scenario B (the missing gate, and the more likely one):** a column is added to
`models.py` with no migration. `migration-lint` is skipped entirely — `dorny/paths-filter` only
fires it when `alembic/versions/*.py` changes. The default unit lane mocks the DB at the session
boundary, so it passes. The integration lane runs `alembic upgrade head` then 191 tests over 39
tables — if none of them touches that column, it passes too. In production `alembic upgrade head`
is a legitimate no-op, `docker compose up -d` swaps the image, and every request touching that
model returns 500 `column clips.foo does not exist`. This repo has already lived through
"`alembic upgrade head` exited 0 and changed nothing" (ISSUES_LOG.md:556) and added a
`current == head` assertion afterwards; that assertion cannot catch this variant, because head
genuinely *is* head.

**The standard fix is one CI step.** `alembic check` (Alembic ≥1.9; the repo pins 1.14.0) runs
the autogenerate comparison and exits non-zero if any operation would be rendered. It closes
scenario B completely and forces scenario A to be resolved once — because **it will fail today**
until those 20 indexes are declared in `__table_args__`. That is the correct order of work, not
an obstacle to it.

---

### F4 — The repo states the unindexed-FK rule in its own code and then does not apply it to three tables

**Severity: low · Confidence: high · Verdict: gap**

`models.py:958-963` writes the rule out in full:

> *"Postgres does not auto-index FK columns, and an unindexed FK makes the parent DELETE
> seq-scan this table. … `creator_id` needs its own so right-to-erasure (DELETE FROM creators,
> which cascades here) stays cheap."*

Three tables carrying `creator_id` have **no index or unique constraint leading with it**, in
metadata or in any migration: `clip_impressions` (`models.py:1092-1104` — no index of any kind
beyond the PK), `minute_deductions` (`models.py:1282-1298`), `video_feedback`.

**Failure scenario:** `DELETE /auth/me` (right-to-erasure) triggers the `ON DELETE CASCADE` on
`clip_impressions.creator_id`. With no index, Postgres sequentially scans the whole table for
every account deletion. `clip_impressions` is the one that grows without bound (see F5), so the
erasure path gets monotonically slower over the life of the beta while the other two stay small.
Independently, the `tenant_isolation` RLS policy on `clip_impressions` (migration 0037) adds
`creator_id = current_setting('app.creator_id')::uuid` to every query on the table, and with no
index that qual can only be satisfied by a seq scan.

`minute_deductions` and `video_feedback` are low-risk in practice (every production query on
`minute_deductions` goes through the `UNIQUE(video_id)` index — `billing/ledger.py:351`,
`billing/refund.py:62`, `worker/tasks.py:401`), so this is one real index and two cheap ones.

---

### F5 — `clip_impressions` is written on every clip-list GET and read by nothing

**Severity: medium · Confidence: high · Verdict: over-engineered**

`routers/clips.py:857-877` inserts one `ClipImpression` row per ranked clip **and commits**,
inside a `GET`. A repo-wide grep for `ClipImpression` / `clip_impressions` outside `models.py`,
`routers/clips.py` and `tests/` returns **nothing** — not `preference/`, not `preference/efficacy.py`,
not `scripts/eval_efficacy.py`, not `worker/`, not `knowledge/`. The table has no reader, no
index, and no purge task (the three purge beat tasks are `purge_stale_source_media`,
`purge_stale_youtube_analytics`, `purge_stale_event_logs` — `worker/tasks.py:1273,1318,1373`).

The recorded position is `docs/DECISIONS.md:2252-2260` (Issue 202): *"cheap insurance for IPS
eval … cannot be reconstructed retroactively … a rolling-purge (à la event_logs 90-day) can be
added later if volume warrants."* **I agree with collecting it and disagree with the shape.**
Arguing against the recorded position on its own terms:

1. *"Cheap"* is asserted, not measured. The write rate is keyed to **page views, not clips
   produced** — one row per ranked clip per `list_clips` call. Twelve clips per video and a
   handful of Review visits per video per creator puts a 100-user beta into the millions of rows
   per year. Every one of those is a `COMMIT` on a read path (so `list_clips` can never be served
   by a read replica, and the request's session is committed mid-handler).
2. *"If volume warrants"* has no trigger, no owner and no metric — and the purge it anticipates
   would itself need a `shown_at` index the table does not have.
3. The data is being collected in a shape that **cannot be queried by the analysis it exists
   for**: IPS evaluation needs `(creator_id, clip_id)` and `(shown_at)`; there is neither.

**Failure scenario:** eighteen months in, someone finally writes the IPS harness the table was
built for. The first exploratory query — `SELECT … WHERE creator_id = ? AND shown_at > ?` over a
multi-million-row unindexed, RLS-forced table on a shared 2-vCPU droplet — seq-scans, and the
work stalls on a migration that must build indexes on the largest table in the database. The
insurance policy did not pay out.

**Recommendation:** either (a) add `Index("ix_clip_impressions_creator_shown", "creator_id",
"shown_at")` and a 90-day purge alongside the existing event-log sweep, matching the pattern the
decision itself invoked, or (b) drop the table until the harness that consumes it exists and log
impressions to `event_logs`, which already has retention, indexes and a purge. Do not leave it in
the current shape.

---

### F6 — Six JSONB payloads carry a `version` discriminator; none has an upcaster, and the readers degrade to a *plausible wrong answer*

**Severity: medium · Confidence: high · Verdict: gap**

Versioned payloads exist and are read-guarded: `clip_edit_documents.doc`
(`clip_engine/edits.py:218`), `clips.pending/effective_geometry_jsonb`
(`GEOMETRY_DOC_VERSION`, `clip_engine/edits.py:306,329`), `clips.reframe_track_jsonb`
(`TRACK_JSON_VERSION`, `clip_engine/reframe.py:1058`), `signals.timeline_jsonb`
(`ingestion/signals.py:119`), `videos.peaks_uri` envelope (`ingestion/peaks.py:170`),
`videos.overlay_spans_jsonb` (`OVERLAY_SPANS_VERSION`). Unversioned but structured:
`clips.signals_jsonb`, `clips.style_preset`, `creator_dna.patterns_jsonb`,
`transcripts.segments_jsonb`, `demographics.payload_jsonb`,
`preference_models.feature_schema_jsonb`, `dna_embeddings.ref_jsonb`, `event_logs.extra`.
There is no recorded policy for any of it, and **no upcast function anywhere in the tree**.

The two guards behave in opposite ways, which is the tell that this is convention rather than
policy: `ClipEditDocument`'s reader **rejects** a newer version explicitly
(`unsupported_version`, `clip_engine/edits.py:79`) — correct. `parse_geometry` **returns `None`**
on a version mismatch (`clip_engine/edits.py:329-331`), documented as "readers then fall back to
the source-window numbers (the pre-Issue-470 behavior)."

**Failure scenario:** someone adds a field to the geometry document and bumps
`GEOMETRY_DOC_VERSION` to 2, with no backfill (which is exactly the precedent set by 0059's
deliberate "NO BACKFILL"). Every clip trimmed before that deploy still holds a v1 document.
`parse_geometry` now returns `None` for all of them, so `_clip_response`
(`routers/clips.py:317`) reports `has_baked_edits: false` and `_clip_duration_s` falls back to
the **source-window** duration. The creator's Review page now states a duration for a trimmed
clip that does not match the video they will download, and claims the clip has no baked edits
when it does — silently, for every historical row, with every test green. That is the precise
regression Issue 470 was filed to fix, re-introduced by a version bump, in a product whose
stated identity is honesty. The `style_preset` column already demonstrates the unversioned
variant of the same drift: `models.py:838-841` notes that keys from removed styles "persist on
old rows and are ignored" (the Issue-442 phantom `background`), with no cleanup pass.

**Recommendation:** one paragraph in `docs/DECISIONS.md` establishing (a) that a version bump
requires either a read-time upcaster or a backfill migration — never a silent fallback — and
(b) which of the two mismatch behaviours (`reject` vs `degrade`) is the house rule. The bar is
low; nothing here needs a schema registry.

---

### F7 — `docs/MIGRATIONS.md` Rule 2's NOT NULL recipe stops one step short of setting NOT NULL

**Severity: low · Confidence: high · Verdict: deviation-unjustified**

`docs/MIGRATIONS.md:64-80` is titled *"New NOT NULL constraints — NOT VALID first, then
VALIDATE"* and shows exactly two phases: add `CHECK (col IS NOT NULL) NOT VALID`, then
`VALIDATE CONSTRAINT`. At the end of that recipe the column is **still nullable** — there is a
validated CHECK constraint and no `NOT NULL`. `alembic --autogenerate` and every SQLAlchemy
reader still see `nullable=True`; a bulk `COPY` or a direct `psql` insert path that bypasses the
constraint name is the only thing the CHECK catches, and the model type annotations remain
`… | None`.

The complete PG12+ recipe is four steps: `NOT VALID` → `VALIDATE` → `ALTER COLUMN … SET NOT NULL`
(which now skips the table scan precisely *because* a valid CHECK proves it) → `DROP CONSTRAINT`.
Steps 3 and 4 are the point of steps 1 and 2.

**Failure scenario:** an author follows Template C + D literally to make a column non-nullable,
ships both deploys, and the column is never non-nullable. Nothing fails; the invariant just
isn't there. A later reader of `models.py` (or an AI session doing a CHECK-phase pass) sees
`nullable=True`, concludes the invariant was never intended, and writes defensive `if x is None`
branches for a case that the schema was supposed to have eliminated.

**Context, not a re-file:** `docs/OFF_COURSE_BUGS.md:142` already logs that Rule 4's backfill
snippet (`UPDATE … LIMIT`) is MySQL, not PostgreSQL, and that the offline-mode guard every data
migration needs is undocumented — both still open. Worth adding one observation to that row:
**the only gate that would have caught the invalid template is `migration-lint`, which is
advisory and path-filtered**, so the doc could stay wrong indefinitely while CI stays green.
Migrations 0057 and 0062 both implement the correct CTE/`IN (SELECT … LIMIT)` form and the
`context.is_offline_mode()` branch — the code is right and the doc that governs it is wrong,
which is the inversion that matters.

---

### F8 — Do **not** split the `clips` table (judgement call, arguing against a plausible reading of the ground truth)

**Severity: n/a · Confidence: medium · Verdict: deviation-justified**

`architecture-map.md` B5/D2.6 observes that `clips` carries four lifecycles in one row and notes
that the excellent recorded rationale for keeping `clip_edit_documents` off `clips` (TOAST
detoast cost on list pages, `models.py:915-923`) "now applies to `reframe_track_jsonb`,
`signals_jsonb`, and the two geometry columns, and was not revisited." I want to register the
opposite conclusion on the remedy.

`clips` has ~25 columns. That is not a wide table by Postgres standards, and column count is not
what costs anything — **TOASTed column *fetches* are**, and only when they are actually fetched.
The `clip_edit_documents` decision was correct because that document is written on a
multi-second autosave cadence by the client (so an in-row JSONB would rewrite the whole `clips`
tuple on every keystroke batch, with CAS revisions on top). None of `reframe_track_jsonb`,
`signals_jsonb` or the geometry columns has that write profile: `reframe_track_jsonb` is written
once per render in the same transaction as `render_uri`, geometry once per confirm,
`signals_jsonb` once at persist. Splitting them into satellite tables at 100 users buys nothing
and costs joins, four more RLS policies to keep in sync, four more FK cascades on the erasure
path, and four more chances to get the Issue-468-class CAS transactions wrong — in a codebase
whose recorded history shows those transactions are where the SEV1s live.

**The measured problem (F2) is a `SELECT` list, not a schema.** Fix the projection; leave the
table. If the four-lifecycle observation deserves anything, it is a `DECISIONS.md` entry saying
*"`clips` is deliberately one row per clip across engine/verdict/render/delivery state; the cost
control is column projection on read paths, not normalisation"* — so the next refactor argues
against a recorded position instead of a vacuum.

---

### F9 — `docs/COMPLIANCE.md` is the real retention decision record, and nine tables are missing from it

**Severity: low · Confidence: high · Verdict: gap**

Correcting the ground truth first: `architecture-map.md` D2.8 states that *"nothing decided for:
rendered clips in R2, `transcripts.segments_jsonb`, `dna_embeddings`, `chat_messages`,
`clip_impressions`."* That is **not accurate**. `docs/COMPLIANCE.md:89-124` is a 28-row Data
Classes & Retention table that explicitly covers rendered clips (`:100` — until archive/erase/
account deletion, with the non-creator-scoped-key enumeration problem reasoned out), transcripts
(*"Until video deleted"*), chat conversations (`:113`), and clip impressions (`:112`, including
the "purge if volume warrants" caveat quoted in F5). The retention posture in this project is
considerably better documented than the map suggests, and it is the *right* file for it.

What is genuinely missing: **`dna_embeddings` has no row in that table** — despite being a
Voyage-derived semantic representation of transcript excerpts, i.e. a derivative of the source
media that the 72 h purge exists to remove, and despite the file's own opening rule (`:4`:
*"Update this file any time data classes … changes"*). Also absent: `video_context`, `summaries`,
`improvement_briefs`, `creator_insights`, `usage`, `minute_packs`/`minute_deductions`,
`preference_models`, `signals`.

**Failure scenario:** Google OAuth app verification (Issue 29, still open) asks what derived
representations of YouTube-origin content are retained and for how long. The answer for
`dna_embeddings` — semantic embeddings of spoken content from source video, retained
indefinitely until account deletion — is defensible and probably fine, but it is not written
down anywhere, so it gets improvised under review pressure by whoever is answering. The same file
already contains a careful, well-argued asymmetry analysis for poster frames and waveform peaks
(`:101-102`); embeddings deserve the same paragraph and do not have it.

Separately, and out of my scope to price: every retention row that says *"until account
deletion"* is a storage-cost posture, not a compliance one. `Issue 293` (R2 storage cost) is
flagged with no position. At 100 users this is genuinely fine; it is listed under "decisions this
domain needs" rather than as a finding.

---

## What is genuinely right here

Not padding — these are specific and I checked each one.

1. **The migrations are excellent.** `0059_clip_blended_score.py` is a nullable `ADD COLUMN` whose
   docstring explains why there is deliberately no backfill (the blend cannot be un-mixed), why
   no new RLS policy is needed (RLS is table-scoped), why no index (ordering is in-process), and
   which template it follows. `0062_delete_masking_skip_feedback.py` is a batched, idempotent,
   offline-mode-aware data repair with a termination proof in a comment and a `logger.warning`
   audit line that explains *why it is WARNING and not INFO* (alembic's NullHandler swallows INFO).
   I do not often see migrations this good.
2. **`alembic/env.py:47-58`** carries the full incident archaeology for why `lock_timeout` /
   `statement_timeout` must go through libpq `options` rather than a pre-transaction `SET` — the
   root cause of the silent-no-op-migration outage. The fix is structural and the reason it is
   structural is written down at the site.
3. **Postgres enum discipline is correct**, which is rare. `0032_clip_publication_schedule.py:49-54`
   uses `ALTER TYPE … ADD VALUE IF NOT EXISTS` outside a transaction with the constraint noted;
   `0057` chose a CHECK constraint over an enum *because* `ADD VALUE` cannot run in one.
   22 `sa.Enum` columns and no drift.
4. **`Clip.__table_args__` (`models.py:730-745`)** — a `DEFERRABLE INITIALLY DEFERRED`
   `UNIQUE(video_id, rank)` with a comment explaining that immediate checking would fire on
   `rerank_with_preference`'s transient rank permutation. That is a correct and non-obvious use of
   deferred constraints.
5. **`Clip.triage`'s `server_default` is load-bearing and says so** (`models.py:827-836`): without
   a database-side default, the previous image's `INSERT` during a rolling restart would violate
   NOT NULL and 500 clip generation for the whole window. That is the expand/contract invariant
   applied at the column level, correctly.
6. **RLS is a structural property, not a convention** — 27 `tenant_isolation` policies,
   `FORCE ROW LEVEL SECURITY`, the NULLIF hardening in 0045, and `db.py::tenant_session` taking
   `creator_id` as a *required argument* so a call site cannot forget it. `clip_edit_documents`
   denormalises `creator_id` specifically so the policy is a direct-column comparison, and the
   pattern has a name ("the house pattern"). This is above current standard.

---

## Decisions this domain needs but does not have

1. **Index ownership + a schema-drift gate.** One position ("every DB index is declared in
   `__table_args__`; `alembic check` is a required CI job") plus the one-time migration to make
   it true. This is the highest-leverage item in the domain.
2. **JSONB payload versioning policy** — version bump ⇒ upcaster or backfill, never a silent
   fallback; and one house rule for reject-vs-degrade on mismatch.
3. **When a JSONB key graduates to a column.** 36 JSONB columns and no stated trigger. The 2026
   answer is "when it is filtered on, or when it is updated independently of the rest of the
   document" — write it down once.
4. **`clips` stays one table** (F8) — record the position so the next refactor argues against it.
5. **Read-path column-projection rule.** "No ORM full-entity `select()` on a list endpoint for a
   table with a >2 KiB JSONB column." This is the general form of F2 and would be gate-able as a
   source-scanning test, which this repo already does well on the frontend (`src/test/sourceScan.ts`).
6. **`clip_impressions`: index + purge, or delete the table** until its consumer exists.
7. **pgvector: drop the unused index and state the exact-search position**, replacing the Issue-56
   "revisit at scale" deferral with "no ANN index until an ANN query exists; when one is written,
   `hnsw.iterative_scan` is mandatory."
8. **`dna_embeddings` retention row in `docs/COMPLIANCE.md`**, plus the eight other absent tables,
   and a storage-cost position for the "until account deletion" classes (Issue 293).
