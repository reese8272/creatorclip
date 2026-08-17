# DECISIONS — DRAFTS FOR APPROVAL

> ## ✅ SEVEN OF THESE WERE APPROVED AND ADOPTED ON 2026-08-17
>
> Drafts **#1, #13, #17, #19, #20 and #22** were reviewed with the owner and moved verbatim into
> `docs/DECISIONS.md` (dated 2026-08-17). **Do not re-apply them from this file** — `docs/DECISIONS.md`
> is now the source of truth for those six. They are left below for provenance only, each marked
> `✅ ADOPTED`.
>
> The remaining **16 drafts are still PROPOSED**. Approve each at its paired L30 issue's CHECK phase,
> not in bulk.

> ## ⚠️ PROPOSED. NOT YET ADOPTED. NOT PROJECT POSITIONS.
>
> Nothing in this file is a decision. These are **22 ready-to-paste drafts** of the architectural
> positions this project has never written down, produced by the deep standards audit of
> 2026-08-17. Per `CLAUDE.md`'s own workflow, a decision is not real until **Phase 2 — APPROVE**:
> the owner reads it and explicitly says yes.
>
> **Each entry requires individual approval.** Approve, amend, or reject them one at a time. Only
> then does an entry move into `docs/DECISIONS.md`, with the date changed from the drafting date
> (2026-08-17) to the approval date. Do not bulk-paste this file.

**Tags used below**

| Tag | Meaning |
|---|---|
| `[RATIFY]` | Records what the project already does, with reasoning. Nothing changes. A ratified status quo is exactly as valuable as a change — it means the next session argues against a position instead of re-litigating a vacuum. **6 of the 22 are this.** |
| `[CHANGE]` | Proposes a change to code, gates, or docs. |
| `[CORRECT]` | The recorded decision and the code disagree. The entry exists to fix the record, not to restate it. |
| `[DECIDE]` | The audit does not know. Options and a recommendation are given, plus what evidence would settle it. |

**Evidence confidence.** Findings behind these drafts were adversarially verified. Where a claim
was never contested by a skeptic it is marked **`[unverified]`** inline. Where a verifier *corrected*
the original claim, the corrected version is what appears here. Line counts marked
"re-verified 2026-08-17" were re-measured directly against the working tree while drafting.

---

## Contents

| # | Draft | Tag |
|---|---|---|
| 1 | [No service layer, and why](#1) | `[RATIFY]` |
| 2 | [`worker/tasks.py`: one file until the envelope is extracted](#2) | `[DECIDE]` |
| 3 | [The task-body envelope contract](#3) | `[CHANGE]` |
| 4 | [What belongs in `Settings` and what belongs beside its algorithm](#4) | `[CHANGE]` |
| 5 | [The frontend↔backend type contract is generated, not hand-written](#5) | `[CHANGE]` |
| 6 | [Scan, don't list — a gate's scope may never be a literal](#6) | `[CHANGE]` |
| 7 | [JSONB payload versioning and upcasting](#7) | `[CHANGE]` |
| 8 | [Index ownership and an `alembic check` gate](#8) | `[CHANGE]` |
| 9 | [Data retention beyond source media and event logs](#9) | `[CHANGE]` |
| 10 | [Model deprecation and upgrade policy](#10) | `[CHANGE]` |
| 11 | [Prompt versioning and prompt regression](#11) | `[CHANGE]` |
| 12 | [The LLM output-quality SLO, and who receives it](#12) | `[CHANGE]` |
| 13 | [LLM degradation posture — no circuit breaker at beta scale](#13) | `[RATIFY]` |
| 14 | [What "the clip engine is working" means numerically](#14) | `[CHANGE]` |
| 15 | [Backpressure, fairness, and queue-depth policy](#15) | `[CHANGE]` |
| 16 | [Idempotency as a named house pattern](#16) | `[CHANGE]` |
| 17 | [The single-VM failure domain: RTO 4 h, RPO 24 h](#17) | `[RATIFY]` |
| 18 | [The alerting position: one page, five digests](#18) | `[CHANGE]` |
| 19 | [Fail-open vs fail-closed — correcting the record](#19) | `[CORRECT]` |
| 20 | [One creator = one login; no team seam in v1](#20) | `[RATIFY]` |
| 21 | [Deprecation policy for parked artifacts](#21) | `[CHANGE]` |
| 22 | [Frontend performance budget and accessibility scope](#22) | `[RATIFY]` + `[CHANGE]` |

---

<a id="1"></a>
## 1. ✅ **ADOPTED 2026-08-17 — now in `docs/DECISIONS.md`** · `[RATIFY]` 2026-08-17 — There is no service layer, and there will not be one

**Decision — routers own their queries, the domain packages (`clip_engine/`, `dna/`, `knowledge/`, `preference/`, `billing/`) are the layer, and no `services/` package will be introduced at v1 scale.**

**Why this diverges / why now.** This is the single largest structural bet in the backend and it has
zero recorded rationale, so every session is free to re-open it. It should not be re-opened. The
officially maintained `fastapi/full-stack-fastapi-template` (last commit verified 2026-08-17) has
**no `services/`** — its route handlers run `select(...)` including the ownership predicate inline.
This repo is strictly ahead of that reference: `routers/_owned.py` (47 lines) collapses fetch +
ownership into one query that 404s for both missing and foreign rows, and `routers/_enqueue.py`
(75 lines) is a single enqueue + SSE-ownership seam behind 19 endpoints. That is targeted
extraction where duplication was measured, which is the KISS-correct answer.

**Alternatives ruled out.** (a) *Per-domain `service.py`* as `zhanymkanov/fastapi-best-practices`
prescribes — rejected: that guide's rationale is "many domains in a large monolith", and at 24
routers / 98 endpoints this repo is below the scale at which either reference claims the layer pays
for itself. (b) *`Netflix/dispatch`'s ~50 `service.py` packages* — rejected: archived read-only
since 2025-09-03, so citing it as current practice is a stretch. (c) *A repository layer* —
rejected on the standing YAGNI caveat: do not add an interface you have exactly one implementation of.

**Cost accepted.** Router handlers contain business logic, so a second consumer (a CLI, a second API
version) would require extraction at that time. Testing routes means testing through HTTP rather
than against a service object — already the repo's habit via `TestClient`.

**Source/evidence.** `routers/_owned.py`, `routers/_enqueue.py`; audit `01-domains/d01-backend-layering.md`
§"What the current standard actually is". <https://github.com/fastapi/full-stack-fastapi-template>,
<https://github.com/zhanymkanov/fastapi-best-practices>, <https://github.com/Netflix/dispatch>,
<https://www.oreilly.com/library/view/architecture-patterns-with/9781492052197/ch06.html>.

---

<a id="2"></a>
## 2. `[DECIDE]` 2026-08-17 — `worker/tasks.py` stays one file until the envelope is extracted

**Decision (recommended) — `worker/tasks.py` remains a single 7,179-line module for now. The size is explicitly NOT the problem; the envelope duplication is (draft 3). Once the envelope exists, extract `worker/sweeps/` only.**

**Why this diverges / why now.** The file is past every conventional threshold and the split is
mechanically obvious, which makes "we looked and chose not to" worth recording. The measured seams,
should the split ever happen: `worker/sweeps/` ≈1,480 lines / 14 beat tasks (couples only to
`_try_advisory_lock` / `_rollback_then_unlock` / `_keyset_batches` / `AdminSessionLocal`);
`worker/llm_features/` ≈1,150 / 6 tasks (couples only to `_spend_guard_blocked`);
`worker/render/` ≈990 / 6 tasks (already on its own queue); ≈2,900 remain. `[unverified]`
The decisive argument against doing it now: **none of the defects logged against this file is a
size defect** — format drift, mislabeled log fields, unbilled LLM calls, redelivery double-spend,
advisory-lock leak are each a missing cross-cutting concern in one task body, and all five stay
exactly as likely after a split. The bill is also real: 280 `worker.tasks.<symbol>` patch targets
across 85 test files, and re-export shims do not help because `mock.patch` rebinds the *old*
module's global. Prerequisite either way: `distill_style_prefs` (`worker/tasks.py:1574`) is the one
task of 40 with no explicit `name=`, so it must get one in an earlier, separate deploy or in-flight
messages orphan.

**Alternatives ruled out.** (a) *Split now* — defensible, ~1–2 days mostly mechanical, but buys
nothing against the actual defect history. (b) *A `services/` package* — moves the same 60 `_async`
bodies sideways and adds an import hop.

**Cost accepted.** A 7,179-line file is hostile to navigation and to an agent's context window, and
that cost is paid daily. If the envelope lands and the file still hurts, `worker/sweeps/` alone is
the least-coupled 1,480 lines and where every unattended defect lives.

**What would settle it.** Whether the next three defects in this file are envelope defects or
navigation defects. If two of three are "I could not find the code", split.

**Source/evidence.** `worker/tasks.py` (7,179 lines, re-verified 2026-08-17); `worker/tasks.py:1574`;
audit `01-domains/d01-backend-layering.md` F3. <https://github.com/celery/celery/issues/2570>,
<https://docs.celeryq.dev/en/stable/userguide/tasks.html> (no size threshold exists in Celery
guidance; explicit `name=` is the load-bearing discipline).

---

<a id="3"></a>
## 3. `[CHANGE]` 2026-08-17 — Every Celery task body goes through one envelope contract

**Decision — a single `@task_body` async context manager owns session-factory choice, advisory lock with guaranteed release, spend-guard check, status transition, `aemit` start/error/done, and `log_event`. A structural test asserts every `_*_async` in `worker/` goes through it.**

**Why this diverges / why now.** This is the finding that explains this file's defect history better
than its line count does. Re-verified by grep on 2026-08-17: across 39 `_async` bodies the same
envelope is hand-rolled — `tenant_session` ×55, `AdminSessionLocal` ×47, `aemit` ×140,
`log_event` ×38, `_try_advisory_lock` ×18, `_spend_guard_blocked` ×10. Each new task re-derives
which session factory to use, whether to take a lock, whether to check the spend guard, and which
progress events to emit, with nothing enforcing the answer.
`_generate_improvement_brief_async` (`worker/tasks.py:5319-5400`) spends ~80 lines on envelope
before its first line of business logic. The repo has already proved this exact mechanism works:
`record_llm_usage` as a single choke point plus `tests/test_usage_coverage.py` structurally forbids
an unbilled Anthropic call, and it killed that defect class.

**Alternatives ruled out.** (a) *A base `Task` class* — Celery's `bind=True` self is available but
the envelope is async and per-body, so a context manager composes better and is testable without a
broker. (b) *Documentation only* (a CLAUDE.md section) — rejected on this repo's own evidence: the
audit found nine gotchas whose correct structural fix was written down and never built. Prose that
must be applied before the failure is observable has to become mechanism.

**Cost accepted.** One more indirection between a task name and its logic, and a migration of ~39
bodies that will churn the test suite. Bodies with genuinely unusual lifecycles need an escape hatch,
and an escape hatch is a hole in the gate unless it is enumerated and justified in the test.

**Source/evidence.** `worker/tasks.py` (counts re-verified 2026-08-17); `tests/test_usage_coverage.py`,
`tests/test_worker_invariants.py` (the two existing tests of this shape);
audit `01-domains/d01-backend-layering.md` F3 `[unverified]`, `d03-async-celery.md` F6.

---

<a id="4"></a>
## 4. `[CHANGE]` 2026-08-17 — `Settings` holds only what varies between deploys

**Decision — a value belongs in `config.py::Settings` if and only if it differs between local, staging and production. Algorithm constants live as module constants next to their algorithm. A new `.env.example` ↔ `Settings` parity test is a required gate.**

**Why this diverges / why now.** `config.py` is 1,208 lines (re-verified 2026-08-17) and the #2
most-churned file in the repo (99 commits), because every clip-engine issue adds a section to it.
`config.py:311-660` holds 67 clip-engine fields; **none is set by any of the five committed
deployment configs** (`docker-compose{,.prod,.staging}.yml`, `render.yaml`, `deploy.yml`). Per
12-Factor III, config is what varies between deploys — internal application config is explicitly
excluded. The verifier narrowed the original claim and the narrowing matters: roughly **55** of the
67 are genuine internal constants, while ~8 are legitimately deploy-varying (seven feature toggles
plus `MEDIAPIPE_FACE_MODEL_PATH`), and several others are deliberate pre-calibration tunables with
"UNVALIDATED starting point" comments. So the rule is a rule, not a blanket deletion.

The cheap, high-value half the audit proved: **there is no `.env.example` ↔ `config.py` parity
test**, and eight `Settings` fields are undocumented today — `ANTHROPIC_MODEL_CLIP_CAPTIONS`,
`ANTHROPIC_MODEL_CLIP_EXPLAIN`, `ANTHROPIC_MODEL_CLIP_TITLES`, `CELERY_SOFT_TIME_LIMIT_S`,
`COST_CACHE_WRITE_MULTIPLIER`, `MAX_INGESTED_CHANNEL_TITLE_CHARS`, `REDBEAT_REDIS_URL`,
`YOUTUBE_PUBLISH_PRIVACY`.

**Alternatives ruled out.** (a) *Nested `BaseModel` groups with `env_nested_delimiter`*, which the
pydantic-settings docs recommend — rejected: it renames all 214 env vars (`REFRAME_X` →
`REFRAME__X`) on a hand-edited VM `.env`, and config drift on that file is this project's worst
failure class. The migration's failure mode is the bug you already have, at scale. (b)
*`extra="forbid"`* — not available: six `.env.example` keys are consumed outside `Settings`.

**Cost accepted.** ~55 constants stop being env-overridable without a code change and redeploy; in
practice nothing overrides them today. Migration is ~350 lines of `config.py` and ~120 `.env.example`
rows of churn, all mechanical.

**Source/evidence.** `config.py` (1,208 lines, re-verified 2026-08-17), `config.py:311-660`;
audit `01-domains/d01-backend-layering.md` F6 (CORRECTED — scope narrowed to ~55/67).
<https://12factor.net/config>, <https://pydantic.dev/docs/validation/latest/concepts/pydantic_settings/>.

---

<a id="5"></a>
## 5. `[CHANGE]` 2026-08-17 — The frontend type contract is generated from the OpenAPI document

**Decision — adopt `openapi-typescript` + `openapi-fetch`. `frontend/src/types.ts` is replaced by generated types for every endpoint with a `response_model`; hand-written types plus a runtime guard survive only for the ~5 LLM-shaped payloads the backend types as `list[dict[str, Any]]`. A CI step regenerates and `git diff --exit-code`s.**

**Why this diverges / why now.** Today `types.ts` (814 lines, 80 interfaces, re-verified) is
hand-written, `lib/api.ts:82` does `return (await resp.json()) as T` with no runtime check, and
`e2e/fixtures/mock-api.ts` imports its fixture types **from `../../src/types`**. So the Playwright
suite — a required check — asserts the frontend's own assumption at the network boundary. Rename a
backend response field and `mypy`, `tsc`, `vitest` and **Playwright all pass**, the image deploys,
and the player renders `<video src={undefined}>`. Measured contract state: 48 of 80 interfaces
field-exact, 15 interfaces drifted by 25 fields, 17 with no confident backend counterpart. Honest
qualifier from the verifier: **drift is currently one-directional** — zero TS fields the API does
not send — and the repo has an explicit additive-only API convention, so this is an unguarded gap
with no demonstrated instance, not a live defect. 98 of 119 endpoints already declare a
`response_model`, so the generation input is essentially finished.

**Alternatives ruled out.** (a) *`@hey-api/openapi-ts` or `orval`* — both generate an SDK/hooks
layer that duplicates the 89 existing `api<T>()` call sites and the TanStack Query wrappers already
written. (b) *Zod on all 89 call sites* — rejected: the server already validates via Pydantic
`response_model` on 84 of them; put runtime guards only where the backend has no schema, which is
exactly where `isCropTrack` / `isWaveformPeaks` already sit. (c) *Keep hand-writing and add a
comparison test* — that is strictly more work than generating.

**Cost accepted.** A build-time codegen dependency and a generated file in the tree; `openapi-fetch`
replaces ~40 lines of `lib/api.ts` (keeping `credentials:'include'` and redirect-on-401 as
middleware). Generated names will not always read as nicely as the hand-written ones.

**Source/evidence.** `frontend/src/types.ts`, `frontend/src/lib/api.ts:82`,
`frontend/e2e/fixtures/mock-api.ts:11-29`; audit `01-domains/d06-frontend.md` F1 (CORRECTED to
MEDIUM), F2. <https://openapi-ts.dev/openapi-fetch/>, <https://saschb2b.com/blog/typesafe-api-codegen-2026>,
<https://kubb.dev/docs/5.x/guide/comparison>.

---

<a id="6"></a>
## 6. `[CHANGE]` 2026-08-17 — Scan, don't list: a gate's scope may never be a literal

**Decision — any test or gate whose *scope* is a hand-written Python/TS literal is prohibited. Scope must be discovered by scanning source, the route table, `Base.metadata`, or `pg_policies`. A gate that cannot discover its own scope must assert bidirectional staleness ("everything I list still exists, and nothing exists that I do not list"). Additionally: the *assertion* must fail on a default value, not merely on presence.**

**Why this diverges / why now.** This is the load-bearing rule of the whole audit. A census of 101
module-level literals found **11 registries that define what gets checked; 10 had measurably
drifted**, two of them hiding live defects:

| Registry | listed | truth | live defect |
|---|---|---|---|
| `_LLM_ROUTES`/`_RENDER_ROUTES` (`tests/test_creator_quota.py:30,41`) | 9+4 | 17 LLM routes | **yes — 4 violate the invariant; `POST /creators/me/dna/build` has no daily cap and a 120/min burst** |
| `SUSPECT` (`design-tokens.contract.test.ts:68`) | 17 | open-ended | **yes — 2 tokens, 3 call sites** |
| `_TENANT_TABLES` (`test_rls_isolation_integration.py:265`) | 17 | 26 policied tables | latent |
| `_BILLED_LLM_ROUTES` (`test_flags.py:410`) | 10 | 17 | latent |
| `_LLM_MODULES` (`test_llm_conformance.py:34`) | 13 | 17 client modules | latent |
| `_TASK_MODEL_KEYS` (`test_model_config.py:15`) | 14 | 17 | latent |
| + 4 more, same shape | | | |

The second clause is not optional. `test_llm_conformance.py` asserts `timeout is not None`, which
the Anthropic SDK's 600 s default satisfies — so even a corrected registry would have passed
`preference/style_distill.py`. Scanning fixes scope; only a value assertion fixes vacuity. The repo
already owns both correct patterns: `tests/test_usage_coverage.py` (AST sweep, bidirectional
staleness) and `frontend/src/test/sourceScan.ts`.

**Alternatives ruled out.** (a) *Fix the lists* — the audit has now watched this shape fail three
times under three names; a list is a drift generator by construction. (b) *Lint rule only* — cannot
see semantic scope like "routes carrying `require_flag('llm_generation')`".

**Cost accepted.** Discovery tests are slower and harder to read than a literal, and a scan can
over-match, producing false failures on legitimately exempt code. Exemptions must then be explicit
and enumerated — which is fine, because an enumerated exemption is reviewable and a missing entry is not.

**Source/evidence.** `02-sweep/mB-tests-that-cannot-fail.md` §"literal-registry census";
`01-domains/d01-backend-layering.md` F4; `d04-llm-layer.md` §1 (CORRECTED — 13/17 and 14/17, plus
the vacuous-assertion half); `tests/test_usage_coverage.py:236`, `frontend/src/test/sourceScan.ts`.

---

<a id="7"></a>
## 7. `[CHANGE]` 2026-08-17 — JSONB version bumps require an upcaster or a backfill, never a silent fallback

**Decision — bumping any JSONB payload version requires either a read-time upcaster or a backfill migration. A version mismatch must be *rejected loudly*, never degraded to a plausible default. A JSONB key graduates to a real column when it is filtered on, or updated independently of the rest of the document.**

**Why this diverges / why now.** There are 36 JSONB columns and six versioned payloads
(`clip_edit_documents.doc`, `clips.pending/effective_geometry_jsonb`, `clips.reframe_track_jsonb`,
`signals.timeline_jsonb`, the `videos.peaks_uri` envelope, `videos.overlay_spans_jsonb`) — and **no
upcast function anywhere in the tree**. The two existing readers behave in opposite ways, which is
the tell that this is convention and not policy: `ClipEditDocument` rejects a newer version
(`clip_engine/edits.py:79`, correct), while `parse_geometry` returns `None`
(`clip_engine/edits.py:329-331`). Concrete failure: bump `GEOMETRY_DOC_VERSION` to 2 with no
backfill — the precedent set by migration 0059's deliberate "NO BACKFILL" — and every
pre-deploy clip's `parse_geometry` returns `None`, so `_clip_response` (`routers/clips.py:317`)
reports `has_baked_edits: false` and falls back to the *source-window* duration. The Review page
then states a duration that does not match the file the creator downloads. That is precisely the
regression Issue 470 was filed to fix, silently re-introduced by a version bump, in a product whose
stated identity is honesty. `[unverified]`

**Alternatives ruled out.** (a) *A schema registry* — over-engineered; nothing here needs one at 36
columns. (b) *Degrade-on-mismatch as the house rule* — rejected: a plausible wrong answer is worse
than an error in every one of these payloads, all of which drive creator-visible geometry. (c)
*Normalise the JSONB away* — the expand/contract cost is far higher than an upcaster.

**Cost accepted.** Every version bump now carries a mandatory second artifact (upcaster or backfill),
which slows small format changes. Rejecting loudly means an unrecognised payload surfaces as an
error to the creator rather than as a silently degraded view — that is the intended trade.

**Source/evidence.** `clip_engine/edits.py:79,218,306,329`, `clip_engine/reframe.py:1058`,
`ingestion/signals.py:119`, `ingestion/peaks.py:170`, `models.py:838-841`;
audit `01-domains/d02-data-model.md` F6 `[unverified]`.
<https://www.zerodatatools.com/blog/json-schema-versioning-guide/>,
<https://www.tim-wellhausen.de/papers/ExpandAndContract/ExpandAndContract.html>.

---

<a id="8"></a>
## 8. `[CHANGE]` 2026-08-17 — Every index is declared in `__table_args__`, and `alembic check` is a required CI job

**Decision — whatever the database has, `Base.metadata` must also have. `alembic check` becomes a required check. The one-time migration that declares the 20 missing indexes in `models.py` lands first.**

**Why this diverges / why now.** Of the 28 named indexes created across `alembic/versions/*.py`,
**20 do not exist in `Base.metadata` at all** — including `ix_clips_video_id`,
`ix_clips_creator_id`, `ix_videos_creator_id` and `ix_clip_feedback_creator_id`. Conversely 9
declared in `models.py` were created by `op.create_table` and never appear as standalone
`create_index` calls, so the two sets barely overlap. Two failure paths follow. (A) The next
`--autogenerate` emits the intended change **plus 20 `op.drop_index` operations**; the reviewer
hand-prunes (so far, successfully) or misses one, and `list_clips`' `WHERE video_id = …` becomes a
seq scan while `DELETE FROM creators` (right-to-erasure) seq-scans every child table. (B) The more
likely one: a column added to `models.py` with no migration. `migration-lint` is advisory *and*
`paths-filter`-gated on `alembic/versions/*.py`, so it never fires; the unit lane mocks the DB; the
integration lane's `alembic upgrade head` is a legitimate no-op; production returns 500
`column clips.foo does not exist`. The existing `current == head` assertion cannot catch this,
because head genuinely is head. `alembic check` (≥1.9; repo pins 1.14.0) closes (B) completely and
forces (A) to be resolved once — it will fail today, and that is the correct order of work.
`[unverified]`

**Alternatives ruled out.** (a) *Keep indexes in migrations and add `include_object` filters to
silence autogenerate* — hides the divergence instead of removing it. (b) *Make `migration-lint`
required without `alembic check`* — its downgrade round-trip passes a *deliberate* drop cleanly, so
it does not detect this class.

**Cost accepted.** One noisy prep migration plus ~20 `__table_args__` entries; every future index
must be written twice (model + migration) or generated. `alembic check` needs a live Postgres, so it
runs in the integration lane, adding ~1 job's latency to the required set.

**Source/evidence.** `alembic/versions/*.py` (28 indexes), `models.py`; `docs/BRANCHING.md:100-127`;
audit `01-domains/d02-data-model.md` F3 `[unverified]`.
<https://alembic.sqlalchemy.org/en/latest/autogenerate.html>,
<https://github.com/apache/airflow/issues/48998>.

---

<a id="9"></a>
## 9. `[CHANGE]` 2026-08-17 — Retention: nine missing table rows, the frozen DNA analytics copy, and a storage-cost position

**Decision — `docs/COMPLIANCE.md`'s Data Classes & Retention table is the retention decision record and must cover every table. Add the nine missing rows (starting with `dna_embeddings`), extend the 30-day YouTube purge to the analytics values frozen inside `creator_dna.patterns_jsonb`, and state a storage-cost position for the "until account deletion" classes.**

**Why this diverges / why now.** First, a ground-truth correction: the retention posture is
**better documented than the architecture map claimed** — `docs/COMPLIANCE.md:89-124` is a 28-row
table that already covers rendered clips, transcripts, chat conversations and clip impressions with
reasoning. What is genuinely missing is nine tables, most pointedly `dna_embeddings` — Voyage-derived
semantic representations of transcript excerpts, i.e. a derivative of the very source media the 72 h
purge exists to remove — plus `video_context`, `summaries`, `improvement_briefs`,
`creator_insights`, `usage`, `minute_packs`/`minute_deductions`, `preference_models`, `signals`.

Second, a real compliance edge: `worker/tasks.py:4321-4410` purges `video_metrics`,
`retention_curves`, `audience_activity` and `demographics` exactly as documented — but
`dna/builder.py:229-235,331-345` writes `views`, `engagement_rate`, `avg_view_duration_s` and
`retention_spike_times` verbatim into `patterns_jsonb`, and `dna/profile.py:5` states DNA profiles
are "never deleted". For an *active* creator this is fine — III.E.4.b permits indefinite retention
of Analytics data given 30-day re-verification. The gap is exactly the case the purge was built for:
a creator whose token is revoked. III.D.2.3 then requires deleting *all* API Data for that user, and
their view counts survive in `creator_dna` forever. `[unverified]`

**Alternatives ruled out.** (a) *Argue the frozen copy is aggregated/anonymised* — legitimate, and
cheaper than code, but it must be written in `COMPLIANCE.md`, not assumed. (b) *Delete the whole DNA
row on token revocation* — rejected as disproportionate; nulling the analytics-derived keys is enough.

**Cost accepted.** Every new table now requires a `COMPLIANCE.md` row before merge. The DNA purge
extension makes a revoked-then-reconnected creator rebuild part of their DNA.

**Source/evidence.** `docs/COMPLIANCE.md:89-124`, `worker/tasks.py:4321-4410`,
`dna/builder.py:229-235`, `dna/profile.py:5`; audit `d02-data-model.md` F9, `d07-security-tenancy.md`
F5 (both `[unverified]`). <https://developers.google.com/youtube/terms/developer-policies> §III.E.4.b, §III.D.2.3.

---

<a id="10"></a>
## 10. `[CHANGE]` 2026-08-17 — Model deprecation: an n+1 candidate lane, and a named owner for a swap

**Decision — a model swap is not a config edit. Any change to an `ANTHROPIC_MODEL_*` key requires: re-recorded goldens where they exist, a run of the live ordering lane parameterised over the candidate, and the measured delta recorded in `docs/DECISIONS.md`. `tests/test_llm_live_scoring.py` gains a `CANDIDATE_MODEL` parameterisation so the n+1 delta is known before a sunset clock starts.**

**Why this diverges / why now.** There are 20 pinned model IDs and **exactly one swap tripwire**:
`tests/test_scoring_goldens.py:113` breaks CI when `ANTHROPIC_MODEL_SCORING` changes until the
goldens are re-recorded. Nothing at all guards `ANTHROPIC_MODEL_VIDEO_CONTEXT` or
`ANTHROPIC_MODEL_CLIP_METADATA` — the other two Opus 5 calls, and with scoring the three most
expensive in the system. When Anthropic announces a retirement on a 60-day clock, the swap touches
11 Sonnet keys spanning titles, thumbnails, chat, intake, analysis, DNA brief and improvement; the
only way to learn whether title quality or DNA-brief grounding regressed is to ship it to the beta
and wait for a creator to notice. `[unverified]`

**Explicit correction to an earlier draft of this concern.** A companion finding claimed rolling
aliases could silently re-point and was **REFUTED** on the vendor docs: every Claude model ID is a
pinned snapshot, and from the 4.6 generation the dateless format is *also* a pinned snapshot, not an
evergreen pointer. `claude-opus-5` and `claude-sonnet-4-6` cannot re-point; only `claude-haiku-4-5`
is a pre-4.6 alias and it resolves to a single existing snapshot. So the risk is **scheduled
deprecation**, not silent drift, and Issue 318's bare-alias decision stands (though its stated
rationale — "routing resolves bare aliases to the latest stable version" — is now stale wording).

**Alternatives ruled out.** (a) *Date-pinned snapshots for the clip chain* — now a no-op, per the
above. (b) *Goldens for all 20 keys* — disproportionate; the ordering lane already exists and covers
judgement where it matters. (c) *Trust the nightly* — it is not a required check and nothing watches
it (draft 12).

**Cost accepted.** A model swap becomes a half-day with a live-API bill of a few dollars instead of
a one-line config edit, and someone must own it.

**Source/evidence.** `config.py` model registry, `tests/test_scoring_goldens.py:113`,
`tests/test_llm_live_scoring.py`, `.github/workflows/llm-e2e-nightly.yml`; audit `d04-llm-layer.md`
§4 `[unverified]` and the REFUTED `resolved-model-never-recorded` verifier note.
<https://platform.claude.com/docs/en/about-claude/models/overview>,
<https://tianpan.co/blog/2026-04-27-model-deprecation-treadmill-pre-sunset-discipline>.

---

<a id="11"></a>
## 11. `[CHANGE]` 2026-08-17 — Prompts are versioned where their output is persisted, and pinned by hash where it is judged

**Decision — every prompt whose output is persisted carries a `PROMPT_VERSION` stamped on the stored artifact and on any eval row. The scoring golden gains a sha256 of the prompt text alongside its existing `_OUTPUT_SCHEMA` hash, so editing the rubric fails CI.**

**Why this diverges / why now.** `PROMPT_VERSION` exists in **exactly one module**
(`analysis/video_context.py:62`, now at 3) and nowhere else. The scoring prompt has no version at
all, and the goldens pin `_OUTPUT_SCHEMA`'s sha256 but not the prompt text — a golden replays a
*recorded response body*, so editing the rubric inside `_SYSTEM_STATIC`, which is the actual
definition of the scorer's judgement, leaves every golden green. Current practice makes a
`prompt_version` the join key between CI and production scoring, on the grounds that without it "the
baseline is a hand-wave". The prompt-text hash is ~5 lines and converts the single most consequential
untracked edit in the LLM layer into a CI failure that forces a deliberate re-record. `[unverified]`

**Alternatives ruled out.** (a) *A prompt registry / external prompt-management service* —
disproportionate at 17 call sites and adds a runtime dependency to the thing it manages. (b)
*Version every prompt* — rejected: for transient outputs (chat turns, one-shot title suggestions)
there is no artifact to join a version to, so the stamp would be write-only. The rule is deliberately
"where the output is persisted". (c) *Golden sets per prompt* — that is draft 14's scope for the one
prompt where judgement is the product; the others do not earn a corpus at this scale.

**Cost accepted.** A prompt edit becomes a two-file change (prompt + hash) and a deliberate
re-record. Persisted artifacts grow one column/key. Prompts without persisted output remain
unversioned, so a regression there is still only visible behaviourally.

**Source/evidence.** `analysis/video_context.py:62`, `clip_engine/scoring.py` `_SYSTEM_STATIC`,
`tests/test_scoring_goldens.py`; audit `d04-llm-layer.md` §5 and `d05-clip-engine.md`
"Decisions this domain needs" `[unverified]`.
<https://futureagi.com/blog/prompt-regression-testing-2026/>,
<https://langfuse.com/resources/engineering/golden-dataset-evaluation>.

---

<a id="12"></a>
## 12. `[CHANGE]` 2026-08-17 — The scorer's quality signal gets a threshold, a trend store, and a receiver

**Decision — the nightly `SCORING-MARGIN` lines are appended to a committed JSONL and the job fails below a ratcheted floor, using the identical mechanism as `SCENARIO_FLOOR` in `tests/test_clip_engine.py`. That converts a printed number into both a trend and a gate without adding an alerting dependency.**

**Why this diverges / why now.** `llm-e2e-nightly.yml:123-140` prints one margin per ordering probe
to `$GITHUB_STEP_SUMMARY` **and nowhere else**. There is no threshold, no persisted series, and no
notification step — `grep slack|notify` across `.github/workflows/` finds only a placeholder comment.
So "is the scorer degrading?" is unanswerable across runs, which is exactly what a quality SLO is:
the standard shape is a score *distribution over time* with an alert when the mean drops, and this
has the numerator with no accumulator. The failure path is concrete: an edit to `_SYSTEM_STATIC`
moves the setup-vs-aftermath margin from comfortable to 2-of-3; the nightly still passes; three weeks
later it tips to 1-of-3 and goes red at 03:00 UTC into a workflow list nobody opens, having shipped
clips drifting toward aftermath windows for a month. The repo's own history is the evidence for the
"nobody opens it" premise: `health-check.yml`'s schedule died silently for six weeks, and
`OFF_COURSE_BUGS.md:42` records a job red on every merged PR since 2026-07-02. `[unverified]`

**Alternatives ruled out.** (a) *An LLM-drift monitoring platform* — a vendor dependency and a
subscription for one signal at beta scale. (b) *Email/Slack alert on nightly red* — depends on the
notification path this repo has twice demonstrated it does not read; the ratchet fails *loudly in the
run itself*, which is stronger. (c) *Move the lane pre-merge* — it costs live-API money on every PR;
revisit if the floor proves noisy.

**Cost accepted.** A committed JSONL grows monotonically and needs a pruning rule. A ratcheted floor
can false-alarm on best-of-3 noise, and the first few weeks will need the floor loosened before it
tightens — the same settling period `SCENARIO_FLOOR` went through.

**Source/evidence.** `.github/workflows/llm-e2e-nightly.yml:123-140`, `tests/test_clip_engine.py`
(`SCENARIO_FLOOR`), `docs/OFF_COURSE_BUGS.md:42`; audit `d04-llm-layer.md` §5, `d05-clip-engine.md`
F4 `[unverified]`. <https://galileo.ai/blog/best-llm-output-drift-monitoring-platforms>,
<https://galtea.ai/blog/automated-llm-evaluation-building-a-ci-cd-quality-gate-that-actually-runs>.

---

<a id="13"></a>
## 13. ✅ **ADOPTED 2026-08-17 — now in `docs/DECISIONS.md`** · `[RATIFY]` 2026-08-17 — No circuit breaker and no cross-model fallback at beta scale

**Decision — we are deliberately NOT building a circuit breaker or a cross-model fallback for Anthropic. The existing layers are sufficient at ≤100 users. The missing artifact is one runbook line, not a mechanism.**

**Why this diverges / why now.** Standard practice says retries → fallback chain → circuit breaker,
and the audit deliberately argued *against* it here. The layers already present: SDK
`max_retries=2` on 16 of 17 clients (429/408/5xx/connection, exponential backoff); Celery
`max_retries=2–3` at `default_retry_delay=60` on every LLM task, with `analyze_video_context`
deliberately at 0 and the reason written inline (`worker/tasks.py:518-526`); and the
`flags.llm_generation` **database** kill switch (30 s TTL, no deploy) plus the spend guard, both
gating every LLM-reaching route. A one-hour Anthropic outage therefore costs: each in-flight job
burns ~2 SDK retries then 2–3 Celery retries over ~3 minutes and fails cleanly, the queue drains
rather than backs up, and the owner flips the kill switch. Adding Redis-backed breaker state would
put a new dependency **inside the failure path of the thing meant to protect it** — the 2026
guidance's own caveat that every reliability layer must itself have a fallback — in a system where
Redis is already the single point of failure for the limiter and the spend guard (draft 19).
This finding was **CONFIRMED** by an adversarial verifier.

**Alternatives ruled out.** (a) *`pybreaker` or equivalent around the Anthropic clients* — 17 call
sites, new state, and it protects against a failure mode that already degrades acceptably. (b)
*Cross-model fallback (Opus → Sonnet on error)* — silently changes clip quality with no record of
which model served the response, which is the opposite of this product's honesty posture.

**Cost accepted.** During a provider outage, jobs fail with a retry message rather than degrading to
a cheaper model, and recovery is a manual kill-switch flip. That is the correct trade while one
person is the responder.

**Missing artifact to add.** One line in `docs/RUNBOOKS.md`: *"Anthropic degraded > 15 min →
`scripts/flags.py llm_generation off`; renders and triage continue; re-enable and the queue drains."*

**Source/evidence.** `worker/tasks.py:518-526`, `flags.py`, `billing/spend_guard.py`,
`routers/clips.py:401,492,641`; audit `d04-llm-layer.md` §6 (**CONFIRMED**).
<https://portkey.ai/blog/retries-fallbacks-and-circuit-breakers-in-llm-apps/>,
<https://zylos.ai/research/2026-02-20-graceful-degradation-ai-agent-systems/>.

---

<a id="14"></a>
## 14. `[CHANGE]` 2026-08-17 — What "the clip engine is working" means numerically

**Decision — build a 40-clip labelled corpus and report `HIT@1` and `nDCG@8` against it before any change to `clip_engine/scoring.py`'s prompt, `_OUTPUT_SCHEMA`, or `ANTHROPIC_MODEL_SCORING`. Reporting-only for one release cycle; after three runs establish variance, ratchet it into the `eval/clip-quality` commit status with a floor, exactly as the geometry scenarios were.**

**Why this diverges / why now.** 259 decision entries and none says what good looks like for the
product's core output. Today's behavioural eval is **n=2**: one hand-written synthetic transcript
with two hand-placed windows, graded by fixed orderings on material written to have an obvious
answer, running strictly post-deploy into a step summary. Against 2026 golden-set guidance (50–200
examples, ~100 before scores stabilise) that is a floor presented as a ceiling. Meanwhile the two
most valuable assets the project owns are unread: `ClipTriage` has produced one keep/dropped verdict
per clip since 2026-08-10 — a clean, deduped human label stream — and `clip_impressions` records
served rank. `grep keep_rate` across all Python and TypeScript returns **zero hits**. `[unverified]`

The concrete shape: 5 real ingested videos spanning the real distribution, top-8 candidates each,
stored under `tests/eval/corpus/` with a provenance README (the Issue-481 LibriSpeech fixture already
establishes this pattern). Labels: the maintainer, twice, two weeks apart, reporting **intra-rater
Cohen's κ** — ~90 minutes total, and it establishes the noise floor below which no engine change is
interpretable. If κ < 0.6 the rubric is the problem. `ndcg_at_k` and `reciprocal_rank` already exist
in `preference/efficacy.py`, as does paired-bootstrap machinery.

**Alternatives ruled out.** (a) *An LLM judge for the subjective axis* — the 2026 evidence that LLM
judges agree with each other and not with humans is strong, and you have a human. Keep the LLM where
it already works: fixed orderings on constructed adversarial pairs. (b) *A/B testing on live traffic*
— at ~100 users an A/B of two rankers never reaches power; **interleaving** is the documented answer
at 100× fewer users. (c) *Gate immediately* — a floor set before variance is measured is a
false-alarm generator.

**Cost accepted.** ~2 hours of labelling, ~40 rendered clips of storage, and ~$3–5 of Opus 5 tokens
per full re-score. Keep-rate from triage × deduped impressions is free but carries position and
presentation bias (ranks 9+ are never rendered), so it is a trend line, not a quality measure.

**Source/evidence.** `tests/test_llm_live_scoring.py`, `models.py:164-180` (ClipTriage),
`models.py:1079-1104` (ClipImpression), `preference/efficacy.py`; audit `d05-clip-engine.md` F4, F5
and §"The smallest credible clip-quality eval" `[unverified]`.
<https://arxiv.org/abs/2408.02901> (HIT@1 / mAP standard),
<https://netflixtechblog.com/using-interleaving-in-online-experiments-to-accelerate-algorithm-innovation-at-netflix-a04ee392ec55>,
<https://futureagi.com/blog/llm-eval-golden-set-design-2026/>.

---

<a id="15"></a>
## 15. `[CHANGE]` 2026-08-17 — Backpressure: an interactive render lane and a per-creator in-flight cap

**Decision — add a third queue `render:interactive` for endpoint-triggered single-clip renders, consumed by the same worker as `-Q render:interactive,render` so Celery drains interactive first; add a per-creator in-flight cap of one `render_video_clips` using the existing `_try_advisory_lock`; and tell the creator their queue position rather than spinning indefinitely.**

**Why this diverges / why now.** `grep backpressure|prefetch|priority` across all 13,018 lines of
`docs/DECISIONS.md` returns **zero hits**. `worker_prefetch_multiplier=1` is set correctly but
justified only as an `acks_late` companion. Auto-render enqueues **one** `render_video_clips(video_id,
[8 clip ids])` message — the download-once reasoning is sound — and combined with a
`--concurrency=1` render worker that message is indivisible and non-preemptible. Ten creators
uploading in the same hour (a plausible beta launch day) put ~80 serialised encodes on the queue; at
the *observed* 60–270 s per encode recorded in `worker/celery_app.py:66-68` ("four on-demand clicks
timing out together at ~266 s each on the 4-core VM"), that is **1.5–6 hours**. A creator who then
clicks "Render" on one clip lands behind all of it, with no interactive lane, no fairness, and no
admission control. The per-creator daily slowapi ceilings are cost control, not backpressure: they
cap *daily* jobs, not *in-flight* ones. `[unverified]`

**Alternatives ruled out.** (a) *KEDA autoscaling* — that was the answer and it is descoped with the
K8s track. (b) *`queue_order_strategy: 'priority'`* — works on the Redis transport but Redis has no
native priority and the caveats are documented; a second queue is simpler and explicit. (c) *Split
the 8-encode batch into 8 messages* — loses the download-once saving; if head-of-line blocking
persists, a batch that re-enqueues itself after K clips keeps the saving without the block.

**Cost accepted.** One more queue in compose and in `task_routes`, and a per-creator cap means a
single creator uploading two videos serialises their own work — acceptable and arguably correct.
Queue-position UI needs a depth read that no dashboard currently surfaces.

**Source/evidence.** `worker/celery_app.py:66-69,89`, `docker-compose.prod.yml` (render worker
`--concurrency=1 -Q render`), `limiter.py:136-175`, `docs/DECISIONS.md:2100`;
audit `d03-async-celery.md` F3 `[unverified]`.
<https://docs.celeryq.dev/en/stable/userguide/optimizing.html>,
<https://docs.celeryq.dev/en/stable/userguide/routing.html>.

---

<a id="16"></a>
## 16. `[CHANGE]` 2026-08-17 — Idempotency is a named house pattern, and the key is taken BEFORE the paid effect

**Decision — one entry names the pattern and one helper implements it: `worker/idempotency.py::once(key, ttl_s)`, a Redis `SET NX PX` lease yielding True to the single winner. Rule: use a Postgres advisory lock when the critical section is already inside a session; use the Redis lease when the effect is an external paid call with no session held. **The key is acquired before the effect; the persist-side UNIQUE is the backstop, not the guard.****

**Why this diverges / why now.** The primitives here are excellent and there are ten of them —
`_try_advisory_lock`/`_rollback_then_unlock` (`worker/tasks.py:168-229`), the Redis render-start
marker (`:117-165`), SAVEPOINT + `UNIQUE(video_id)` minute dedupe (`DECISIONS.md:10534`), and the
genuinely clever DEFERRABLE `uq_clips_video_rank` compare-and-set
(`clip_engine/ranking.py:394-438`). Nine or ten entries describe instances; **none states the rule**,
so every new task re-derives it and the same half keeps being missed:

> every guard makes the *persist* idempotent; none makes the *paid call* idempotent.

Concrete: `build_signals` commits and enqueues `generate_clips`, then is SIGKILLed before ack.
On redelivery `_signals_async` short-circuits correctly, then enqueues `generate_clips` a second
time. Both executions read `existing_clips == []` because the first is still inside a 30–120 s LLM
round-trip. **Two Opus 5 scoring calls are billed to the creator's ledger and one result is
discarded** — the deferred UNIQUE catches the duplicate *row*, not the duplicate *spend*. `[unverified]`

**Alternatives ruled out.** (a) *Celery task deduplication middleware* — opaque, broker-coupled, and
does not express the "before the paid effect" invariant. (b) *Rely on the existing UNIQUE constraints*
— that is exactly the assumption that leaves the double-spend open. (c) *A DB advisory lock
everywhere* — wrong tool when the critical section spans a multi-minute external call with no
session held; that is what leaks connections.

**Cost accepted.** A Redis lease is a soft guarantee: a crashed holder's key expires on TTL, so a
long-running paid call can be double-entered after TTL expiry. TTLs must be sized above the worst
observed call latency, and every new paid site must remember the wrapper — which is why draft 3's
envelope should own it rather than each author.

**Source/evidence.** `worker/tasks.py:117-165,168-229`, `clip_engine/ranking.py:394-438`,
`docs/DECISIONS.md:10534`, `docs/AUDIT_KNOWN_ISSUES.md:86`;
audit `d03-async-celery.md` F6 `[unverified]`.
<https://oneuptime.com/blog/post/2026-01-30-exactly-once-delivery/view>,
<https://docs.stripe.com/api/idempotent_requests>.

---

<a id="17"></a>
## 17. ✅ **ADOPTED 2026-08-17 — now in `docs/DECISIONS.md`** · `[RATIFY]` 2026-08-17 — The single VM is correct; RPO 24 h, RTO 4 h, drilled quarterly

**Decision — one DigitalOcean VM remains the production topology; it is not revisited at beta scale. The disaster-recovery targets are **RPO 24 hours, RTO 4 hours**, with a quarterly restore drill whose measured time is recorded in `docs/RUNBOOKS.md`. These targets are only real once backups are armed.**

**Why this diverges / why now.** `docs/RUNBOOKS.md:648` literally reads *"measured RTO recorded
here: ________"*, and `DECISIONS.md:2334` already chose a 24 h RPO — so half the position exists and
has never been paired with an RTO or a drill. The design behind it is genuinely good (logical
`pg_dump`, `openssl enc -pass env:` with the argv-leak reasoning, separate bucket, Object Lock
**Compliance** not Governance, retention by R2 lifecycle so a script bug cannot mass-delete, key
escrow on two independent legs). **None of it is on.** Walked today, losing the droplet gives: detect
= hours to days; provision ≈ 30 min; restore `/opt/autoclip/.env` = 5 min *or unrecoverable*; restore
Postgres = **∞, no dump exists**. Actual RTO ≈ 1 h to a schema-only stack; actual RPO = total loss of
the billing ledgers, `preference_models` (the trained taste — irreplaceable, it *is* the product),
`creator_dna`, `clip_outcomes` and the consent records. R2 media survives; the database that indexes
it does not.

The verifier's correction matters for scoping: this is a **known, owner-blocked gate**, already
stated in `GO_LIVE.md:59-61`, `LEFT_OFF.md:115` and `OFF_COURSE_BUGS.md:26` — not a new discovery.
`BACKUP_R2_BUCKET` and `BACKUP_ENCRYPTION_KEY` *are* documented in `.env.example:116-117`; the one
genuinely missing variable is **`BACKUP_HEALTHCHECK_URL`** (consumed by `scripts/backup_pg.sh:32`
and `scripts/backup_redis.sh:19,46,92`), which is the dead-man's switch and is absent from the
config SSOT.

**Alternatives ruled out.** (a) *Managed PaaS (Render)* — costs roughly 2× and fixes nothing that is
broken. (b) *Postgres PITR / a replica* — the documented anti-pattern of over-buying RPO; 24 h is
correct at this scale. (c) *A second droplet for HA* — doubles cost to remove a failure mode that a
4-hour RTO already tolerates.

**Cost accepted.** Up to 24 hours of creator data can be lost, and a full-day outage is possible if
the failure lands badly. That is an explicit, stated trade — not an accident.

**Source/evidence.** `docs/DECISIONS.md:2327-2390`, `docs/RUNBOOKS.md:572-670,648`,
`scripts/backup_pg.sh`, `.env.example:116-117`, `docs/OFF_COURSE_BUGS.md:26`;
audit `d08-deploy-observability.md` F7 (CORRECTED — known/owner-blocked; `BACKUP_HEALTHCHECK_URL` is
the one new item). <https://khimananda.com/blog/rpo-and-rto-explained>,
<https://oneuptime.com/blog/post/2026-02-06-rpo-rto-targets-observability-opentelemetry/view>.

---

<a id="18"></a>
## 18. `[CHANGE]` 2026-08-17 — Alerting: exactly one page, five digest signals

**Decision — exactly ONE alert may wake the responder: the public `https://autoclip.studio/health` failing 2 consecutive checks from a third-party monitor. Everything else is an email/digest, never a phone. If an alert has no specific action, it does not exist.**

**Why this diverges / why now.** The project built **twelve metrics and zero alerts**. There is no
entry anywhere defining what pages versus what waits; `docs/INCIDENT_RESPONSE.md` defines the ladder
for incidents *already known about*, and nothing defines how an incident becomes known. The single
page must probe the **public hostname** (covering the edge and the tunnel, not just the origin) from
a vendor that cannot die with the host, and must be verified against Bot Fight Mode before it is
trusted, with a documented mute step so an intentional poweroff does not page.

The five digest signals, chosen because **five of the six are emitted by code that already runs,
into a mailer that already exists** — this is *less* infrastructure than what has been built:
1. **Backup heartbeat missed** — `BACKUP_HEALTHCHECK_URL` → healthchecks.io free, 26 h grace.
   $0, one env var, guards the only irreplaceable asset.
2. **Deploy workflow failed** — already fires today, precisely because the rollback correctly still
   exits 1. Verify the notification setting; that is the whole task.
3. **Spend guard tripped** — emit through the existing `notify/` + Resend rail. Do not wait on a
   metrics pipeline.
4. **Pipeline stalled** — "oldest video non-terminal > 30 min" and "oldest unrendered auto-render
   clip > 60 min", a Beat sweep running plain SQL. This is the alert that would have caught *"0 of 18
   clips had ever rendered"*, and it needs no Prometheus at all.
5. **Droplet disk > 80%** — free in the DO console; pairs with the missing Docker log rotation.

**Alternatives ruled out.** (a) *Wait for the Prometheus/OTel rail* — those series are unreachable in
production and correcting that is a separate, larger job; the five signals above do not need it. (b)
*Page on anything else* — a solo responder who is paged for a non-actionable signal stops reading
pages, which is worse than no alerting. (c) *UptimeRobot free* — non-commercial only since Oct 2024,
and this product charges money.

**Cost accepted.** ~$0–9/month, and a genuine gap remains: anything not in these six signals is found
by a creator, not by the system. A single page also means a partial outage (one broken route) is
invisible until a creator reports it.

**Source/evidence.** `docs/INCIDENT_RESPONSE.md`, `scripts/backup_pg.sh:32`, `notify/`,
`docs/EDGE_SECURITY.md:80-86`, `docs/GO_LIVE.md:82` (Issue 282 deferral with named re-open criteria);
audit `d08-deploy-observability.md` §"Minimum credible alert set" and the verified D-4 finding
(**CORRECTED** — `beat` has no healthcheck and no `autoheal` label, so silent beat death stops the
ToS and GDPR purges with zero signal). <https://incident.io/blog/sre-alerting-best-practices>,
<https://opengov.com/article/a-monitoring-alerting-and-notification-blueprint-for-saas-applications/>.

---

<a id="19"></a>
## 19. ✅ **ADOPTED 2026-08-17 — now in `docs/DECISIONS.md`** · `[CORRECT]` 2026-08-17 — Fail-open vs fail-closed: the record is wrong and must be amended

**Decision — the rate limiter degrades to a LOCAL in-memory bucket (`Limiter(..., in_memory_fallback_enabled=True)`); the spend guard then fails CLOSED; and every Redis client carries bounded socket timeouts. `docs/DECISIONS.md:2633-2634`, `docs/AUDIT_BRIEF.md` §5 row 1, and `limiter.py`'s own docstring at lines 42-44 are amended, because all three describe behaviour the code does not have.**

**Why this diverges / why now.** This entry exists to correct the record, not to restate it. The
most-documented "deliberate decision" in the security domain — that the limiter fails open — is
**not what the code does**, and it was reproduced against a dead Redis port: `limiter.py:129-133`
constructs the limiter with neither `swallow_errors` nor `in_memory_fallback`, so slowapi 0.1.9
re-raises the Redis error (`extension.py:630-645`), there is no catch-all handler in `main.py`, and
every rate-limited route returns **HTTP 500 with the endpoint body never running**. That includes
`GET /auth/me` (called by `AuthGate` on every load) and the OAuth callback. A Redis *latency spike* —
not an outage; co-resident ffmpeg/MediaPipe is enough — is therefore a total sign-in outage.
`limiter.py` carries a **99% coverage floor**, the joint-tightest in the repo, and
`tests/test_rate_limiting.py` has **no test at all** for Redis-unavailable behaviour: 99% of lines,
0% of the property its 50-line docstring is about.

Two corrections the verifier insisted on, and they are load-bearing: **"fail-open is the 2026
consensus" is false** — Envoy defaults `failure_mode_deny=true` and the literature is explicitly
context-dependent; the defensible standard is *graceful degradation to a local bucket instead of a
500*. And the correct posture is **asymmetric by control type**: availability controls degrade,
money/authorization controls fail closed. Once the limiter genuinely degrades, the spend guard
becomes the only thing between a bug and an unbounded Anthropic bill, so it must fail closed. Related
and cheap: `youtube/_redis.py:33` is the only one of four Redis clients with no
`socket_timeout`/`socket_connect_timeout`, so against a wedged-but-connected Redis the documented
fail-open arm at `spend_guard.py:373-375` is never reached — a hang is not an exception.

**Alternatives ruled out.** (a) *Amend the docs to say "fails closed" and keep the 500s* — honest,
and rejected: a 500 on `/auth/me` is a worse outcome than a locally-enforced limit. (b) *A
Redis-backed circuit breaker* — see draft 13; slowapi already implements the `_storage_dead` latch
(`extension.py:634-640`), inert only because the flag is off.

**Cost accepted.** In-memory fallback at `--workers 2` means a 2× effective rate limit during a Redis
outage. A fail-closed spend guard means a Redis outage blocks LLM features entirely — a degraded
feature with honest copy already written (`spend_guard.py:98-102`), which is recoverable in a way an
unbounded bill is not.

**Source/evidence.** `limiter.py:129-133`, `youtube/_redis.py:33`, `billing/spend_guard.py:365-375`,
`docs/DECISIONS.md:2633-2634`, `tests/test_rate_limiting.py:278`, `run_layer0.py:333`;
audit `d07-security-tenancy.md` F1 (**CORRECTED** — standards basis restated), F3 (**CORRECTED** —
downgraded to medium, asyncio client so no event-loop block; the cost is unbounded request lifetime
holding a checked-out session). <https://github.com/firecrawl/firecrawl/issues/3728> (the same bug in
the wild), <https://nerdleveltech.com/fail-open-vs-fail-closed-hono-middleware-redis-tutorial>.

---

<a id="20"></a>
## 20. ✅ **ADOPTED 2026-08-17 — now in `docs/DECISIONS.md`** · `[RATIFY]` 2026-08-17 — One creator = one login; no team seam in v1, but take the `tid` claim now

**Decision — v1 is deliberately one-tenant-one-user: no editors, no VAs, no seats. To keep that reversible, `create_session_token` gains a distinct `tid` claim alongside `sub`, and every RLS GUC is read from `tid`. Today `tid == sub`, so it is a no-op.**

**Why this diverges / why now.** Every table keys on a single `creator_id` and all 27 RLS policies
are direct-column on it, with no entry acknowledging this as a deliberate bet — the largest
structural bet in the tenancy model with zero recorded position. The audit priced it, and the pricing
is the reason to ratify rather than build: **the RLS policies are the cheap part.** They are uniform
(`creator_id = NULLIF(current_setting('app.creator_id', true),'')::uuid`) and rewriting them to a
membership subquery is one mechanical migration over a generated list — a day's work — while the seam
itself (`db.py::tenant_session`, one GUC, one `after_begin` listener) does not change shape at all.

**The expensive part is that `creators` is simultaneously the tenant and the principal.** It carries
the login identity, the OAuth grant, the consent + COPPA attestation and the billing balance, *and*
it is the FK target of all 28 tenant tables. The JWT `sub` is that same id and is used
interchangeably for "who is acting" (`request.state.creator_id`, the audit `actor`) and "whose data
is this" (the RLS GUC, `routers/_owned.py`). Splitting them later touches auth, the JWT contract,
every issued cookie, the rate-limit key (one editor would exhaust the whole team's per-creator
quota), the spend-guard keys, the erasure path (does removing an editor erase the tenant?) and the
Art. 15 export scope. That is not a weekend. `[unverified]`

The ~10-line `tid` claim converts an *implicit* conflation into an *explicit* one before the first
paying cohort makes it expensive, and means the day a team seam is wanted, already-issued tokens and
every RLS policy are reading the right concept.

**Alternatives ruled out.** (a) *Build memberships now* — no beta user has asked, and it adds an
invite/permission surface to a product still proving its core loop. (b) *Do nothing at all* —
rejected: the `tid` half is nearly free and the conflation gets more expensive monotonically.

**Cost accepted.** A creator who wants an editor must share their login, which is a real product
limitation and a security smell to say out loud. If a beta creator asks for it, this decision is
re-opened rather than worked around.

**Source/evidence.** `db.py:200` (`tenant_session`), `auth.py:86`, `routers/_owned.py`,
`limiter.py:110-126`, the 27 RLS policies in `alembic/versions/0010_rls_policies.py`;
audit `d07-security-tenancy.md` §"Answers", Q4 `[unverified]`.
<https://ricofritzsche.me/mastering-postgresql-row-level-security-rls-for-rock-solid-multi-tenancy/>.

---

<a id="21"></a>
## 21. `[CHANGE]` 2026-08-17 — Parked artifacts leave the trunk and live as a git tag

**Decision — when a track is descoped, its artifacts are removed from the trunk and preserved as an annotated git tag (`parked/<name>`), with one line in `docs/DECISIONS.md` naming the tag. Applies now to `render.yaml`, `deploy/charts/` and the 12 root PNGs.**

**Why this diverges / why now.** There is an append path for artifacts and no forget path, and the
cost is not aesthetic: `render.yaml` (219 lines, re-verified) has **already misled the project once**
— Issue 326's brief was written against it and had to be re-scoped mid-issue when someone noticed the
live app has never run on Render (`DECISIONS.md:2395`). `deploy/charts/` is 532 lines of Helm that
has never touched a cluster, and it is still justifying live production config: `db.py:35`'s
`prepare_threshold=None` disables psycopg3 server-side prepared statements for a PgBouncer that is
not in `docker-compose.prod.yml`, and `_POOL_SIZE=15` is sized against a 25-connection sidecar that
does not exist. That is real config paying a real cost for a descoped deployment.

**Explicit correction to an earlier, harsher version of this finding, which was REFUTED.**
`render.yaml` is **not armed**: `autoDeployTrigger: commit` is Render's documented default for new
services and changes nothing — the file is inert unless a Blueprint is linked in the Render
dashboard, which is already logged as an owner check (`docs/AUDIT_KNOWN_ISSUES.md:182-186`) and whose
retention was deliberately decided at `DECISIONS.md:2395`. Also corrected: the 35 MB `.mp4` and
`dump.rdb` are **gitignored** (`.gitignore:41`, `:34`) and `{{pkgetc}}/` is untracked working-tree
cruft — none are in the repository. The real committed debt is `render.yaml` + `deploy/charts/` +
12 root PNGs (~1.0 MB). This is hygiene and decision-clarity, **not a security or deploy risk**, and
should be sized that way.

**Alternatives ruled out.** (a) *Keep them as documentation of the scale path* — that is what the tag
plus a DECISIONS line is for, at zero ambient cost. (b) *Move to `docs/archive/`* — still in the
trunk, still greppable, still misleadable-from. (c) *Delete outright* — the tag costs nothing and
removes the only real objection.

**Cost accepted.** Recovering a parked artifact becomes `git show parked/helm-chart:…` rather than
opening a file, which is a genuine friction if the K8s track resumes. The `db.py` PgBouncer constants
must be re-derived for the deployment that actually exists — that is separate work this decision
merely unblocks.

**Source/evidence.** `render.yaml` (219 lines, re-verified 2026-08-17), `deploy/charts/`,
`db.py:35,42,60`, `docs/DECISIONS.md:2395`, `docs/AUDIT_KNOWN_ISSUES.md:182-186`;
audit `d08-deploy-observability.md` F8 (**REFUTED** as a risk finding — the hygiene residual is what
survives), `d01-backend-layering.md` §"what would you delete".

---

<a id="22"></a>
## 22. ✅ **ADOPTED 2026-08-17 — now in `docs/DECISIONS.md`** · `[RATIFY]` + `[CHANGE]` 2026-08-17 — Frontend performance budget and accessibility scope

**Decision (perf, RATIFY) — the SPA ships as a single chunk with a budget of 300 KB gzipped; no code-splitting programme is started. Revisit if mobile becomes a real surface or the bundle crosses the budget.**
**Decision (a11y, CHANGE) — the target is WCAG 2.2 AA. `e2e/a11y.spec.ts` adds `'wcag22aa'` to `withTags`, and SC 2.5.8 on the timeline handles is either fixed or the Essential exception is claimed IN WRITING.**

**Why this diverges / why now.** Measured on the 2026-08-15 build: one
`assets/index-*.js` at 887,823 B raw / **261,004 B gzip**, one CSS file, zero `React.lazy`, zero
`manualChunks`. That is above the 2026 working budget of 150–200 KB gz — but the risk is genuinely
over-stated, and saying so is the point of ratifying: `/` is served by FastAPI as a static
`landing.html`, **not** the SPA, so anonymous marketing traffic never downloads the bundle. The app is
a repeat-visit authenticated tool behind Cloudflare with content-hashed assets; after first load it is
a 304. React Router v7 Data Mode makes route-level `lazy` a 3-line per-route change if it ever
matters, so deferring costs nothing. This finding was **CONFIRMED**: the code is fine, the vacuum was
the finding.

Accessibility is the opposite shape — the implementation is *ahead* of the record (cut edges are
`role="slider"` with `aria-valuetext`, 17 live regions, a sourced rationale for why the rail container
is `role="group"`), but the gate is one WCAG version behind. `e2e/a11y.spec.ts:44` tags
`['wcag2a','wcag2aa','wcag21a','wcag21aa']`; **`wcag22aa` is absent**, and axe-core's `target-size`
rule is tagged `wcag22aa` and off by default — so it has never executed against this app on any
route. `Timeline.tsx:263` styles each cut edge `w-[3px]`; SC 2.5.8 requires 24×24 CSS px or the
spacing exception, and Issue 134's filler/silence removal can put `start` and `end` handles a few
pixels apart, failing both. WCAG 2.2 has been a W3C Recommendation since Oct 2023 and EN 301 549
v4.1.1 (the EAA technical standard) aligns to it. `[unverified]`

**Alternatives ruled out.** (a) *A 200 KB gz budget now* — would mandate splitting work that buys
nothing for ≤100 desktop creators. (b) *Stay on WCAG 2.1* — defensible only if written down; silently
encoding 2.1 inside a `withTags` array is not a decision. (c) *Widen the handles to 24 px* — may be
wrong for frame-accurate trimming, which is exactly why the Essential exception is a legitimate answer.

**Cost accepted.** First authenticated load pays 261 KB gz to render a Google button. Enabling
`wcag22aa` will surface a triage backlog, and the gate still filters to `serious || critical`, so
moderate violations stay invisible.

**Source/evidence.** `frontend/dist/assets/index-DJ9gBY7A.js` (887,823 B / 261,004 B gz, build
2026-08-15), `frontend/src/App.tsx:66-73`, `frontend/e2e/a11y.spec.ts:44,50-52`,
`frontend/src/components/editor/Timeline.tsx:250-256,263`, `TimelineRail.tsx:70-71`;
audit `d06-frontend.md` F5 (**CONFIRMED**), F6 `[unverified]`.
<https://webperfclinic.com/article/javascript-bundle-optimization-complete-guide-shipping-less-code>,
<https://www.levelaccess.com/blog/wcag-2-2-aa-summary-and-checklist-for-website-owners/>,
<https://www.deque.com/blog/axe-core-4-5-first-wcag-2-2-support-and-more/>.

---

## Closing note for the approver

Six of these ratify what you already do (1, 13, 17, 20, 22-perf, and half of 2). That is the honest
finding of the audit's architecture pass: the decision discipline in this project is above industry
norm for a solo build — 259 dated entries with sources and logged deviations — but **the rigor was
applied to features and not to structure.** Every algorithm choice has a documented rationale; the
three largest structural facts about the codebase had none.

If you approve only one, approve **#6 (scan, don't list)**. It is the only entry that removes an
entire class of the "one baby snag after another" pattern rather than one instance of it, and it is
backed by the hardest evidence in the audit: 11 measured registries, 10 drifted, 2 hiding live
defects.
