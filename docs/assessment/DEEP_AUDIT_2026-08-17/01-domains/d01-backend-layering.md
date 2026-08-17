# D01 — Backend layering & code structure

**Auditor pass:** deep standards audit, 2026-08-17. Read-only. Built on the Phase-0 ground truth
(`00-groundtruth/*`); facts established there are not re-derived.

---

## Verdict

**The absence of a service layer is correct and I would not add one** — the officially maintained
FastAPI reference template does exactly what this repo does (route handlers running `select()`
against an injected session), and this repo's `_owned.py` / `_enqueue.py` / `_schemas.py` seams are
*better* than that template. The two structural bets with no recorded rationale (`worker/tasks.py`
as service layer, `config.py` as god-object) are **not** the top risks here; both are survivable at
this scale and I give a concrete, bounded answer on each below.

The real defects in this domain are three places where a **rule the project already wrote down was
applied in one layer and not the other**: an import-time route registration that means the entire
production SPA mount is exercised by zero tests in any CI lane (a new instance of the repo's named
#1 failure mode); two HTTP handlers that hold a pooled DB connection across a multi-minute Anthropic
call, which is precisely what recorded decision **Issue 82b** forbids *for routers*; and a
single-source origin helper (`clip_origin_s`, Issue 475) adopted at 1 of 14 call sites while its own
docstring claims "no surface can mis-measure clips."

---

## What the current standard actually is, with sources

### 1. FastAPI structure at this size — the standard is *contested*, and the "no service layer" side is the official one

| Reference | Status checked | What it actually does |
|---|---|---|
| [`fastapi/full-stack-fastapi-template`](https://github.com/fastapi/full-stack-fastapi-template) | **Actively maintained — last commit 2026-08-17T08:38Z** (verified via GitHub API today) | `backend/app/` = `api/routes/*.py`, `core/`, `crud.py`, `models.py`. **There is no `services/`.** `api/routes/items.py` runs `select(Item).where(Item.owner_id == current_user.id)` and `session.exec(...)` *inline in the handler*, including the ownership predicate. |
| [`zhanymkanov/fastapi-best-practices`](https://github.com/zhanymkanov/fastapi-best-practices) | The most-cited community structure guide | Recommends **domain-packaged** modules (`auth/router.py`, `auth/service.py`, `auth/schemas.py`), explicitly because type-based layout "didn't scale well for our monolith with many domains". Prescribes a `service.py` per domain. |
| [`Netflix/dispatch`](https://github.com/Netflix/dispatch) | **Archived 2025-09-03, read-only** | ~50 domain packages each with `service.py` + `views.py` + `models.py`. Frequently cited as *the* large-FastAPI reference; note that it is no longer maintained, so citing it as "current practice" is now a stretch. |
| YAGNI counter-literature ([service-layer criticism roundup, 2025–26](https://craftedstack.com/blog/python/design-patterns-repository-service-layer-specification/), [O'Reilly *Architecture Patterns with Python*, ch.6](https://www.oreilly.com/library/view/architecture-patterns-with/9781492052197/ch06.html)) | — | The consistent caveat: don't add an interface/layer you have exactly one implementation of. "For trivial APIs, the layered structure might be overengineering." |

**Read on the evidence:** there is no single 2026 standard. There is a *domain-packaging* standard
(the thing this repo actually has — `clip_engine/`, `dna/`, `knowledge/`, `preference/`, `billing/`)
and an *optional* `service.py` inside each package. The official template ships without it. A repo
with 24 routers and 98 endpoints sits below the scale at which either reference claims the layer
pays for itself.

### 2. Splitting a large Celery task module

Celery's own docs and the long-standing community answer
([celery/celery#2570](https://github.com/celery/celery/issues/2570),
[Tasks user guide](https://docs.celeryq.dev/en/stable/userguide/tasks.html),
[sneawo, split-tasks pattern](https://blog.sneawo.com/blog/2018/12/05/how-to-split-celery-tasks-file/)):
convert `tasks.py` into a `tasks/` package, import the submodules in `__init__.py` (or list them in
`include=`), and **use explicit `name=` on every task** so names are decoupled from module paths.
There is no size threshold in any Celery guidance — the naming discipline is the load-bearing part.

### 3. LLM calls in the request path

Current consensus for production LLM backends is unambiguous: inference does not belong inline in an
HTTP handler; the pattern is `POST → 202 + job_id → queue → worker → poll/SSE`
([LLM backend architecture, 2026](https://markaicode.com/architecture/llm-backend-architecture-best-practices/);
[FastAPI production failure modes under load](https://www.zestminds.com/blog/fastapi-production-issues-under-load/)).
The specific FastAPI hazard — holding a pooled DB session across a slow external call — is a named
antipattern: "release the connection before making async calls and acquire a new one if needed
afterward."

**This repo already agrees with all of that, in writing**, at `docs/DECISIONS.md:12284`
(Issue 82b — *"router session-order: release before external call, re-stamp
`session.info['creator_id']` on every reacquired session"*) and `:12749`.

### 4. pydantic-settings at 200+ settings

[Current pydantic-settings docs](https://pydantic.dev/docs/validation/latest/concepts/pydantic_settings/)
recommend nested `BaseModel` groups inside one `BaseSettings`, addressed via `env_nested_delimiter`
(`DATABASE__HOST`). The docs do **not** recommend multiple `BaseSettings` classes.

But the prior question is [12-Factor III](https://12factor.net/config): *"An app's config is
everything that is likely to vary between deploys… This definition of config does **not** include
internal application config"* — internal constants belong in code, not in the environment.

---

## Findings

### F1 — `main.py:227` registers the entire production SPA mount at import time, so no CI lane ever executes it — HIGH

`main.py:197`:

```python
_SPA_BUILT = _SPA_INDEX.is_file()      # frontend/dist/index.html
...
if _SPA_BUILT:                          # main.py:227
    app.mount("/app/assets", StaticFiles(directory=_SPA_DIST / "assets"), ...)
    @app.get("/app/{spa_path:path}")
    async def spa(spa_path: str = "") -> FileResponse:
        candidate = (_SPA_DIST / spa_path).resolve()   # main.py:~240, traversal guard
```

Route registration is branched on filesystem state evaluated at import. Where is that branch true?

| Environment | `frontend/dist` present? | `/app/*` routes registered? |
|---|---|---|
| `Unit tests (pytest)` — required check, `ci.yml:72-141` | **No** — no Node step in the job | **No** |
| `Integration`, `Coverage floor`, `eval` | No | No |
| `Frontend (lint/test/build)` — `ci.yml:457` builds it | **but it is a separate job AND is not a required check** | irrelevant, no pytest there |
| `Playwright (smoke + a11y)` — required | webServer = `npm run dev` (`frontend/playwright.config.ts:50`) — talks to Vite, not FastAPI | **No** |
| `Docker build (smoke test)` — required | image builds SPA (`Dockerfile:71,95`) but `push: false`, container is never run | **No** |
| **Production** | Yes | **Yes — the only place** |

Consequence in `tests/test_static.py`: the four assertions that cover production behaviour
(`:39`, `:51`, `:59` `/app/chip/chip-book.png`, `:67` `/app/dashboard`) are guarded by
`@pytest.mark.skipif(not _SPA_BUILT)` and are **skipped on every CI run**. The branch that *does*
run is `test_root_authenticated_returns_404_without_spa_bundle` — an assertion that production is
explicitly not in. Deploy smoke is `/health` ×5 + `llm_harness.py --flow core`
(`deploy.yml:122,136,345`); neither touches `/app`.

**Failure scenario.** Edit the traversal guard or the `spa_path` fall-through in `main.py`'s `spa()`
handler — e.g. change `candidate.resolve()` containment or the `/app/assets` mount order. All 8
required checks go green (the Python assertions skip themselves; Playwright is talking to Vite;
Docker only builds). The image deploys. Every signed-in creator's `/app/dashboard` now 500s or
serves the wrong bytes, and the post-deploy smoke passes because `/health` is fine — so the
auto-rollback in `deploy.yml` never fires. First execution of the changed code is production, with
no rollback trigger.

This is the AUDIT_BRIEF §6 shape exactly, and it is not in `AUDIT_KNOWN_ISSUES.md`. The
security-relevant path-traversal guard on user-controlled `spa_path` has **zero automated
coverage anywhere in the repo**.

*Remedy (cheap):* one `render_env`-style CI step that runs `npm run build` before a small
`spa`-marked pytest lane, or a `conftest` fixture that writes a stub `frontend/dist/index.html` and
re-imports `main`. Then delete the `skipif(not _SPA_BUILT)` guards. Judgement call on which; the
finding is not.

---

### F2 — Two HTTP handlers hold a pooled DB connection across a multi-minute Anthropic call, contradicting recorded decision Issue 82b — HIGH

`docs/DECISIONS.md:12284` names this exact rule and names the layer: *"82b (**router** session-order:
release before external call…) is the user-visible half and ships first."* `:12749` reports closing
*"the last three open-session-across-LLM holds."* Two router-layer holds remain:

**(a) `routers/insights.py:824` `analyze_performer`.** `session: AsyncSession = Depends(get_session)`
(`db.py:191`, `async with AsyncSessionLocal()` for the whole request). The handler runs
`get_owned(...)`, a `VideoMetrics` select, a `CreatorDna` select and a `CreatorInsight` cache select
— so the connection is checked out and a transaction is open — then at `:911` awaits
`_ANTHROPIC.messages.create(...)` on a client configured `timeout=httpx.Timeout(120.0)`,
`max_retries=2` (`:764`). Worst case ≈ 360 s holding an idle-in-transaction connection.

**(b) `routers/thumbnails.py:182` `get_thumbnail_patterns`.** Same shape, worse payload: after
`check_positive_balance(creator.id, session)` and two more queries, it calls
`_compute_patterns_single_flight(...)` (`:273`), which — on the waiter path — `asyncio.sleep`s and
then runs a **Claude multimodal vision call over up to 10 images** (`knowledge/thumbnails.py:40`,
also 120 s × 3). This is the largest per-call LLM cost in the app (~$0.055, per
`OFF_COURSE_BUGS` `:115`/`:117`), and neither of these two files appears in
`tests/test_llm_conformance.py::_LLM_CALL_SITE_FILES` (`:240-247` lists `routers/insights.py` but
not `routers/thumbnails.py`).

Pool: `db.py:42-43` → `pool_size=15, max_overflow=5` = **20 connections per uvicorn worker**, 2
workers (`docker-compose.prod.yml:14`).

**Failure scenario.** Anthropic degrades (elevated latency, not errors — the common case; no
circuit breaker exists, per architecture-map D3.14). Requests to `/insights/analyze-performer`
(20/hour/creator) and `/creators/me/thumbnail-patterns` (10/hour/creator) each pin one connection
for up to 6 minutes. Twenty such requests landing on the same uvicorn worker — ~7 creators
retrying over a 6-minute window is enough — exhausts that worker's pool. Every *other* endpoint
routed to that worker then blocks on SQLAlchemy's `pool_timeout` (30 s default, unset here) and
returns 500. The Cloudflare `/health` check may still pass, because `/health` on the *other* worker
is fine. An LLM slowdown becomes a full API outage for half of traffic, with no signal.

The correct pattern is 20 lines away in the same repo: `worker/tasks.py:5334-5340` documents it
verbatim — *"the read phase closes its tenant session BEFORE the ~120 s Claude + web_search call, so
no pooled connection is held across it."* And 19 other endpoints already use the
`routers/_enqueue.py` 202+SSE seam.

*Secondary, same handler:* `routers/insights.py:942-955` puts `record_llm_usage` inside the outer
`except Exception → 503`. If the ledger write fails after a successful LLM call, the creator gets a
503, nothing is persisted, and a retry pays for a second call. Low cost at Haiku/256 tokens; noted
because it is the one LLM call in the app outside the idempotent-task envelope.

---

### F3 — `worker/tasks.py`: the answer is "split three seams, but that is *not* the fix" — MEDIUM (recorded-decision gap)

This is the owner's headline question, so here is a direct answer rather than a size complaint.

**Is 7,179 lines a problem? Partly, and less than it looks.** The mechanics of splitting are
already paid for:

- **39 of 40 task decorators pin `name="worker.tasks.<x>"` explicitly** (verified by AST-ish scan).
  Task identity is therefore decoupled from module path — `worker/celery_app.py:84`'s
  `task_routes`, `worker/schedule.py`'s beat entries, and any in-flight Redis message all survive a
  module move untouched. This is exactly what Celery's docs prescribe, and it was done. **The one
  exception is `distill_style_prefs` (`worker/tasks.py:1574`)** — no `name=`, so its auto-derived
  name *would* change on a move, orphaning in-flight messages enqueued from
  `routers/video_review.py:125`. That one line must be fixed in a separate, earlier deploy.
- The file already carries **241 function-local imports** — it has been fighting its own weight for
  a while.

**Concrete seams, measured by line range:**

| Proposed module | Lines | Tasks | Coupling back to the pipeline core |
|---|---|---|---|
| `worker/sweeps/` — purges, backfills, analytics refresh, catalog sync, outcome polling, trials, lifecycle, Stripe reconcile | **~1,480** | 14 (all beat) | `_try_advisory_lock` / `_rollback_then_unlock` / `_keyset_batches` / `AdminSessionLocal` only |
| `worker/llm_features/` — analysis, titles, thumbnails, hooks, chapters, improvement brief | **~1,150** | 6 | `_spend_guard_blocked` only; each is `job_id → knowledge/* → persist` |
| `worker/render/` — `_ClipRenderPlan`, `_load_clip_render_plan`, encode/upload/poster/peaks, clean, edit, summary render | **~990** | 6 (own `render` queue already) | `_creator_id_for_clip`, `_set_clip_render_status` |
| **remains in `tasks.py`** — ingest/transcribe/signals/video-context/clip-metadata/generate-clips + DNA/preference + publish + chat + notifications + export | ~2,900 | 14 | shares `_set_status`, `_tenant_id_or_raise`, `_humanize_failure`, `_creator_id_for_video` — genuinely one chain |

**Migration cost, honestly:** the code move is a morning. The bill is the test suite —
**280 `worker.tasks.<symbol>` patch targets across 85 test files, 60 distinct symbols**. Re-export
shims do **not** help: `unittest.mock.patch("worker.tasks.X")` rebinds `tasks`' global, while the
moved function resolves `X` from *its own* module globals. Every patch target for a moved symbol
must be rewritten, and the split must land as one PR (a half-split leaves two homes for the same
concern). Realistic: **1–2 days, mostly mechanical, one full-suite run, near-zero runtime risk.**

**Now the part that matters more.** Ask what the size actually cost. Per `snag-taxonomy.md` §C, the
defects logged against this file are: format drift (`:56`), mislabeled log fields (`:98`), unbilled
LLM calls, redelivery double-spend (open), advisory-lock leak (`:61`). **Not one of those is a
size defect.** Every one is a *missing cross-cutting concern in a single task body*. Splitting into
four files leaves all five classes exactly as likely.

The measurable structural fact is the repetition: across ~60 `_async` bodies the same envelope is
hand-rolled — `tenant_session` **55×**, `AdminSessionLocal` **47×**, `progress.aemit` **116×**,
`log_event` **38×**, `_try_advisory_lock` **18×**, `_spend_guard_blocked` **10×**. Each new task
re-derives which session factory to use, whether to take a lock, whether to check the spend guard,
and which progress events to emit — with nothing enforcing the answer. `_generate_improvement_brief_async`
(`worker/tasks.py:5319-5400`) is a fair sample: ~80 lines before the first line of business logic,
all of it envelope.

**My recommendation, in priority order:**

1. **Extract the envelope first** — one `@task_body` async context manager owning: session-factory
   choice, advisory lock with guaranteed release, spend-guard check, status transition, `aemit`
   start/error/done, and `log_event`. Pin it with a test that asserts every `_*_async` in the
   package goes through it. This repo already has that exact test shape twice
   (`tests/test_usage_coverage.py`, `tests/test_worker_invariants.py`) and it is the pattern that
   actually killed a defect class before (`record_llm_usage`).
2. **Then** the split becomes cosmetic and cheap — do `worker/sweeps/` alone if you only do one; it
   is the least-coupled 1,480 lines and it is where every unattended defect lives.
3. Fix `distill_style_prefs`'s missing `name=` regardless of whether you ever split.

**What I would not do:** introduce a `services/` package. It would move the same 60 `_async` bodies
one directory sideways and add an import hop, with zero effect on the five defect classes above.

**The gap this finding is really about:** architecture-map C#2 is right that this bet has *zero*
recorded rationale. Whichever way you go, the one-paragraph entry is overdue — the next session will
otherwise re-open the question from scratch, which is `snag-taxonomy` Class 11.

---

### F4 — `tests/test_llm_conformance.py` is a hand-maintained allowlist, and a module already slipped past it with **no timeout at all** — MEDIUM

`tests/test_llm_conformance.py:34-47` defines `_LLM_MODULES` as a literal list of 13 module paths;
`:240-247` defines `_LLM_CALL_SITE_FILES` as a literal list of 6. A new LLM module is **unguarded by
default**. Four modules construct an Anthropic client and are in neither list:

- **`preference/style_distill.py:31` — `_ANTHROPIC = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)`.
  No `timeout`. No `max_retries`.** The Anthropic SDK default (verified against the pinned
  `anthropic==0.105.2` in `.venv`) is `Timeout(connect=5.0, read=600, write=600, pool=600)` with
  `max_retries=2`.
- `analysis/video_context.py:55` and `knowledge/clip_metadata.py:47` — correctly configured, but
  unpinned by any test, so they can drift silently.
- `routers/insights.py:764` — see F2.

**Failure scenario.** Anthropic hangs a `style_distill` request. `distill_style_prefs`
(`worker/tasks.py:1574`, enqueued from `routers/review.py:319` on creator feedback) blocks for
600 s, retries twice → **~30 minutes on one worker slot**, well inside
`CELERY_SOFT_TIME_LIMIT_S = 3000` (`config.py:753`) so nothing kills it. The default queue runs
`--concurrency=4` (`docker-compose.prod.yml:46`). Four creators submitting review feedback during
the same incident consume all four slots: **ingest, transcribe, generate-clips, DNA build, chat, and
every beat sweep stop dispatching for half an hour**, with no error and no alert. The observable
symptom is "uploads are stuck", and the cause is a missing keyword argument in an unrelated module.

This is the AUDIT_BRIEF §6 pattern in test-gate form: a green conformance suite over a registry that
never enumerated the offender. The repo already knows the fix — `frontend/src/test/sourceScan.ts`
and `tests/test_model_config.py` **discover** their targets by scanning source instead of listing
them. Make `_LLM_MODULES` a `Path.rglob` for `AsyncAnthropic(` and both the missing timeout and the
two unpinned modules fail immediately.

---

### F5 — `clip_engine.edits.clip_origin_s` — extracted as "the ONE origin rule", adopted at 1 of 14 call sites — MEDIUM

`clip_engine/edits.py:360-371` defines the canonical rule, and its docstring claims the property:

> *"The ONE origin rule (Issue 475) — shared by `playable_duration_s`, the Proof-of-Lift contrast
> and the originality fingerprint, **so no surface can mis-measure clips** whose setup point differs
> from `start_s`."*

`docs/DECISIONS.md:13014` records the same, narrowly: *"the origin helper (`clip_origin_s`) adopted
by lift + the originality fingerprint."* So the recorded position is that two consumers adopted it.
In practice the expression `setup_start_s if … is not None else start_s` is still written inline at
**13 other sites**:

```
routers/clips.py:546, 1168, 1563, 2276, 2388, 2495, 2725
worker/tasks.py:783, 2457 (_render_start_for), 2556, 2657, 2867
chat/tools.py:498
```

`routers/insights.py:29` is the only importer. `worker/tasks.py:2457` is a *named function*
(`_render_start_for`) that is a byte-for-byte duplicate of the helper, with its own 8-line docstring
citing Issue 59.

**Failure scenario.** The origin rule is one clamp away from non-trivial — e.g. a future
`max(0.0, …)` for clips whose setup lead runs before t=0, or accounting for a leading trim in the
edit document. Whoever makes that change edits `clip_engine/edits.py` (it is the documented single
source and the docstring says so) and ships. The rendered bytes (`worker/tasks.py:2457`) and the
clip-relative transcript/trim windows served to the editor (`routers/clips.py:1168, 1563`) keep the
old semantics. Result: the waveform, the trim handles and the rendered video disagree about second
0 — reproducing the exact class Issue 475 was filed to close, and one the eval harness cannot see
because `tests/eval/scenarios/*.yaml` assert *candidate geometry*, not render origin.

Judgement call on urgency; not on the fact. Cheap fix: adopt the helper at all 14 sites and add a
source-scanning test banning the inline expression outside `edits.py` — the repo already ships four
tests of exactly that shape in `frontend/src/test/sourceScan.ts`.

---

### F6 — 67 clip-engine algorithm constants are env-settable `Settings` fields that **no deployment sets** — LOW/MEDIUM, over-engineered

`config.py` is 1,208 lines / **214 settings** on one `Settings(BaseSettings)` with
`model_config = SettingsConfigDict(env_file=".env", extra="ignore")` (`config.py:35`). Between
`config.py:311` and `:660` sit **67 settings** across 12 clip-engine sections (sentence snapping,
filler removal, reframe planner, speaker-cut planner, virtual tripod, caption placement, camera
region, overlay bands, append-mode, video-context, shortlist, stream recap).

Measured: **66 of the 67 are documented in `.env.example`**, and **0 of the 67 are set in
`docker-compose.yml`, `docker-compose.prod.yml`, `docker-compose.staging.yml`, `render.yaml`, or
`.github/workflows/deploy.yml`.** They are knobs nothing turns.

Per [12-Factor III](https://12factor.net/config), config is what *varies between deploys*; internal
application constants are explicitly excluded. These do not vary between deploys, and they cannot
be changed without a restart anyway (they are read through the module-level `settings` singleton).

Two concrete costs:

1. **`extra="ignore"` makes every misspelling silent.** Rename or typo `OVERLAY_BAND_MIN_HEIGHT_FRAC`
   in the hand-edited `/opt/autoclip/.env` (which `deploy.yml:180-182` explicitly does *not* sync)
   and pydantic drops it without a word; the algorithm runs on its default while the operator
   believes it is tuned. The repo has this exact failure three times already — `STORAGE_BACKEND`
   left at `local` (`ISSUES_LOG.md:542`), `BACKUP_R2_BUCKET` never set (`OFF_COURSE_BUGS:26`),
   `METRICS_TOKEN` never set (`:128`) — and there is still no `.env.example` ↔ `config.py` parity
   test (process-map §7).
2. **`config.py` is the #2 most-churned code file in the repo (99 commits)** precisely because every
   clip-engine issue adds a section to it. That churn is Class-4's epicenter per the taxonomy.

**What I would *not* recommend:** migrating to nested pydantic models with `env_nested_delimiter`,
which is what the pydantic docs and every blog will tell you. It renames all 214 env vars
(`REFRAME_X` → `REFRAME__X`), and this project's single worst failure class is config drift on a
hand-edited VM `.env`. The migration's failure mode is the exact bug you already have, at scale.

**What I would recommend:** delete the 67 from `Settings` entirely and put each next to its
algorithm (`clip_engine/reframe.py`, `overlay_bands.py`, `camera_region.py`, `sentence_snap.py`,
`filler.py`). That deletes ~66 `.env.example` rows, shrinks `config.py` by ~350 lines, and removes
the silent-typo surface for the constants most likely to be fiddled with. It leaves `Settings` at
~147 genuinely per-deploy values — a size at which one flat class is unremarkable.

---

## What is genuinely right here

1. **No service layer is the correct call, and the seams that exist are better than the reference.**
   `routers/_owned.py` (47 lines) collapses fetch+ownership into one query returning 404 for both
   missing and foreign — the official FastAPI template writes that predicate inline in every
   handler. `routers/_enqueue.py` (75 lines) is the single enqueue+SSE-ownership seam behind 19
   endpoints, with the fail-open Redis posture documented in the module docstring. That is targeted
   extraction where duplication was measured — the KISS-correct answer, not the ceremony one.

2. **`db.py:200` `tenant_session(creator_id)` — required-argument RLS.** A call site *structurally
   cannot* forget the GUC. 55 uses in `tasks.py` alone. Ground truth already calls this the best
   piece of architecture in the repo and I agree; it is better than what most funded teams ship.

3. **Explicit Celery task names on 39/40 tasks.** Nobody does this until it bites them. It is the
   single reason the tasks.py split is a 1–2 day job instead of a migration project. Whoever added
   it bought an option the repo has not yet exercised.

4. **`clip_engine/` internal layering is genuinely clean.** `candidates.py` → `sentence_snap.py` →
   `merge.py` → `scoring.py` → `ranking.py` → `render.py`, with the impure surface concentrated in
   `render.py`/`reframe.py` and everything else pure and testable. 8,365 lines with a real
   dependency direction.

5. **Structural tests as an architectural mechanism.** `tests/test_worker_invariants.py` pinning the
   `AdminSessionLocal` allowlist, `tests/test_usage_coverage.py` pinning that every LLM task bills,
   `tests/test_model_config.py` banning model literals, `tests/test_ci_config.py` gating the CI YAML.
   This is a *better* structural-integrity mechanism than a layer boundary, because it fails loudly.
   F4 and F5 are both arguments to use *more* of it, not less.

6. **Incident-archaeology comments.** `worker/tasks.py:5334-5340`, `db.py:31-35`,
   `routers/thumbnails.py:167-177`, `main.py:188-210` each explain *why* the shape is the shape,
   naming the incident. Some of my findings above were only findable because those comments told me
   what the intended rule was.

---

## Decisions this domain needs but does not have

1. **"We have no service layer, and here is why."** One paragraph. State that routers own their
   queries under `_owned.py`, that domain packages are the layer, and that the official FastAPI
   template does the same. Mark it as reviewed-and-kept, so the next agent argues against a position
   rather than a vacuum. (architecture-map D1.2 — still open.)
2. **The `worker/tasks.py` position** — either "one file, deliberately, until the envelope is
   extracted" or the three-seam split in F3. Either is defensible; the vacuum is not.
   (architecture-map D1.1.)
3. **A task-body envelope contract.** Which session factory, when a lock, when the spend guard,
   which progress events, which `log_event`. Currently re-derived 60×. This is the same shape as the
   already-successful `record_llm_usage` choke point, and its absence explains the actual defect
   history in that file better than its line count does. (Related to architecture-map D4.18 —
   "idempotency as a *stated* pattern".)
4. **A rule for what belongs in `Settings`.** Proposal: *only values that differ between local /
   staging / production*. Everything else is a module constant. Would have prevented F6 and roughly
   a third of `config.py`'s 99 commits. (architecture-map D1.3.)
5. **Test-registry policy: discovered, never listed.** Any structural gate whose scope is a Python
   literal list (`_LLM_MODULES`, `_LLM_CALL_SITE_FILES`, `_CANDIDATE_SOURCES` in `run_layer0.py`,
   `_TENANT_TABLES` per `OFF_COURSE_BUGS:25`) is a vacuous-green generator by construction. The repo
   has already been bitten by this exact shape three times. Make "scan, don't list" a house rule.
6. **Where LLM calls are allowed to live.** Issue 82b decided the *session-order* half but not the
   *placement* half. A one-line rule — "no LLM call in a request handler; use `_enqueue.py`" — plus
   a source-scan test over `routers/` would close F2 permanently and is ~10 lines of test.
7. **Which environments must register the full route table.** F1 exists because route registration
   is conditional on a build artifact and no gate notices. Either the SPA mount is always registered
   (serving a stub in dev) or one CI lane must build the bundle. This needs an explicit call.

---

## Answer to AUDIT_BRIEF §8 Q3 — "what would you delete?"

Naming paths, in descending confidence:

- **67 clip-engine settings from `config.py:311-660`** + their 66 `.env.example` rows → module
  constants (F6). ~350 lines of `config.py`, ~120 of `.env.example`.
- **`worker/tasks.py:2448-2457` `_render_start_for`** and the 12 other inline origin expressions →
  `clip_engine.edits.clip_origin_s` (F5). Small, but it is duplicated *logic*, which is the only
  duplication that hurts.
- **`render.yaml` (219 lines).** It is a live-looking blueprint for a host the app has never run on
  (architecture-map B4 #3), and it encodes `VERBOSE_LOGGING=true` + `VERBOSE_LOGGING_ALLOW_PROD=true`
  — i.e. if anyone ever *does* deploy it, raw prompts/transcripts/PII go to the log stream. Deleting
  it removes a hazard, not just a file.
- **`tests/test_static.py` (1,861 lines, 80 tests).** Not wholesale — but it is the largest test file
  in the repo, it tests a static surface that is now 4 HTML pages, and its most important assertions
  never run (F1). Rebuild it at ~200 lines with the SPA lane from F1, delete the rest.
- **`deploy/charts/` (532 lines) and the PgBouncer-shaped constants it justifies.** `db.py:35`'s
  `prepare_threshold=None` disables psycopg3 server-side prepared statements for a PgBouncer that is
  not in `docker-compose.prod.yml`; `_POOL_SIZE=15` is sized against a "25-conn sidecar" that does
  not exist. Flagged rather than filed — `AUDIT_BRIEF §9` puts the K8s track out of scope, and
  architecture-map B4 #4 already names it. But it is live config paying a real cost for a descoped
  deployment.

**On the 134k/56k line count itself:** 56k source for 98 endpoints, a 3,200-line computer-vision
reframe pipeline, a Celery worker, an LLM layer with 17 call sites and a billing ledger is *not*
bloated — it is roughly what this product is. The 78k of tests against 56k of source (1.39:1) is
also within normal. I would not go looking for lines to delete; I would go looking for the ~5% that
is duplicated logic (F5) and the ~1% that is configuration surface nobody uses (F6).
