# Ground truth — empirical taxonomy of this project's recurring problems

**Produced:** 2026-08-17, Phase 0 of the deep standards audit.
**Purpose:** a shared factual base so audit agents do not each re-derive (differently) what has
already gone wrong. **This is history, not a bug list.** Do not re-file anything here.

**Corpus:** 967 commits (2026-05-26 → 2026-08-15, 82 days) · 138 rows in `docs/OFF_COURSE_BUGS.md`
(+24 archived in `docs/archive/off_course_bugs_snapshot_2026-06-22.md` = 162 lifetime) · 497 issues
filed · 13,018 lines of `docs/DECISIONS.md` · 8 CreatorClip entries in `~/.claude/ISSUES_LOG.md` ·
2 prior audit reports.

**Headline shape:** `fix:` commits **225** vs `feat:` commits **207** — more fixes than features,
lifetime. July 2026 ran **41% fix commits (127/307)**. `docs:` = 154. The three highest-churn files
in the repo are *documentation* (`docs/issues.md` 241 commits, `docs/DECISIONS.md` 240,
`docs/PROJECT_STATE.md` 210) — the bookkeeping about the work churns harder than the work.

---

## A. Failure classes

Ranked by count. Percentages are of the 138 current `OFF_COURSE_BUGS.md` rows. Classes overlap
where a row has two causes.

### Class 1 — Vacuous green signal / verification theater (~26 rows, 19%)

**The project's own named #1** (`docs/AUDIT_BRIEF.md:177-203`, a 4-instance table). The sweep says
the real count is far higher — this is not a recurring bug, it is the house style of failure.

- `AUDIT_BRIEF.md:183-188` — YouTube catalog sync imported nothing **7 weeks** behind
  `HTTP 200 + "Synced N video(s)"`; Stripe checkout raised on every call **10 weeks** behind
  `doctor.py --full` saying "stripe auth ok"; per-module coverage floors + diff-cover **never ran in
  CI ~7 weeks** while printing *"All runnable gates passed"*; bandit **has never scanned 8,277
  lines** including `crypto.py`, `auth.py`, `main.py`, `config.py` — since inception.
- `OFF_COURSE_BUGS.md:148` — `scripts/doctor.py:405` probed Stripe with a raw `httpx.get`, not the
  app's own `_STRIPE` client. This is *why* `docs/GO_LIVE.md:71` cited "Stripe live-verified" over a
  10-week total outage. Row's own words: *"a pre-flight doctor that green-lights a subsystem it does
  not actually exercise is worse than no check."*
- `:154` — `drill_rate_limit` asserted `all(codes[:first_429] == 404)`; with the quota already
  spent, `first_429 == 0` and `all([]) == True`. The run **self-documented it**:
  `"rate-limit: 0 cheap 404 probes then 429 at request #1. PASS"`.
- `:25` — the RLS regression test written *for Issue 354* iterated a hardcoded `("clips","signals")`
  instead of `_TENANT_TABLES`, so a security gate passed vacuously on 2 of 17 tenant tables.
- `:134` — `test_every_documented_json_route_declares_response_model` iterates **zero routes** under
  FastAPI 0.137's deferred `_IncludedRouter`.
- `:83` — the Squawk migration-lint gate "had never actually run": `npm install -g squawk-cli` exit
  127 on the runner **and** an off-by-one rendering the wrong migration into suppressed stderr.
- `docs/assessment/CLIPPING_INTEGRITY_2026-08-12.md:53` (Issue 476, SEV1) — `score_candidates`, *the
  LLM call that decides which clips ship*, is evaluated **nowhere**; every test patches
  `_ANTHROPIC`. "A prompt/model change that systematically prefers aftermath windows would pass 100%
  of every gate."
- Also: `:31` (a11y gate passed because the fixture was two words long), `:34` (prototype-wide
  `getBoundingClientRect` spy makes every width assertion unconditionally true), Issue 477 (2 of 24
  "adversarial scenarios" assert nothing), Issue 478 (`render-env` marker registered, **zero tests
  carry it**), `ISSUE-2026-08-04-01` (Playwright "regenerated" baseline came back byte-identical,
  twice).

### Class 2 — Test-infrastructure defects & flakes, not product bugs (~35 rows, 25% — largest by count)

Highest volume, lowest value. Event-loop leaks, order dependence, unawaited coroutines, leaked
Redis/advisory locks, non-hermetic tests.

- `:89` — the unit suite had been **hitting the live R2 bucket** for weeks (`STORAGE_BACKEND=r2`
  unpinned in conftest); this masqueraded as an order-dependent `test_health` failure and burned a
  full isolation hunt on a poisoner test *that never existed*. Suite time 60–113 s → ~24 s after.
- `:60` — the integration lane had bit-rotted to **21/134 failing** because CI's integration job sat
  red/unenforced and nothing ran it locally.
- `:69` — ~10 backend unit tests were **silently red** because a conftest guard used substring
  matching (`"integration" in "not integration"`) and blocked collection.
- `:133` → `:147` → `:157` — the same cold-first-run vitest flake logged 2026-08-04, recurred
  2026-08-12, third sibling 2026-08-15. **The failing test name was never captured on any of the
  three occasions**, each time because nobody remembered `--reporter=verbose` beforehand.

### Class 3 — Third-party SDK / API / platform behavior surprise (~20 rows + 6 of 8 ISSUES_LOG entries)

The single most *expensive* class. Every one is a documented default that differs from the assumed
default.

- Google API `fields=` returns **only** named properties → `kind` absent → parser dropped 100% of
  items, 7 weeks (`ISSUES_LOG.md:710`).
- `stripe.HTTPXClient` CA trust store is empty (`capath` given a *file*) → **every** Stripe call
  raised, 10 weeks (commit `c138e93`, Issue 455).
- Cloudflare **OWASP Core Ruleset** blocked Stripe's webhook IPs at the edge; Bot Fight Mode was the
  intuitive-but-wrong suspect (`ISSUES_LOG.md:57`).
- pytest CLI `-m` **replaces** `pytest.ini` `addopts` `-m` rather than intersecting → the pre-push
  gate re-selected `render_env`/`llm_live` for every push, and the documented `--no-verify`
  workaround meant it was never investigated (`ISSUES_LOG.md:95`).
- Alembic + SQLAlchemy 2.0: a `SET` before `context.begin_transaction()` auto-begins a caller-owned
  transaction alembic never commits → **every prod deploy's migration was a silent no-op, exit 0**
  (`ISSUES_LOG.md:556`).
- boto3 presigned URLs default to SigV2 + `us-east-1`; R2 needs `s3v4` + `auto` (`:505`). Anthropic
  returns JSON in a ```` ```json ```` fence, breaking bare `json.loads` in 3 shipped features
  (`:48`). GitHub Actions `context.sha` on `pull_request` is the ephemeral merge commit, not PR HEAD
  (`:63`). Playwright's bare `--update-snapshots` is *changed-mode*. `h11` rejects a header with a
  trailing space → surfaced as `APIConnectionError`.

### Class 4 — Config / environment drift (~18 rows)

`config.py` (99 commits) and `.env.example` (87) are the #2 and #3 most-churned code files — that
churn *is* this class.

- Env vars wired but **never set**: `BACKUP_R2_BUCKET` → every prod migration to date ran with **no
  safety dump** (`:26`); `METRICS_TOKEN` → `/metrics` disabled in prod, so the SLO work has no
  scrape target (`:128`); `STORAGE_BACKEND` left at `local` on a two-container prod → uploads
  silently FAILED (`ISSUES_LOG.md:542`).
- Interpreter drift produces **confidently wrong verdicts in both directions**: `:156` — system
  python3.12 instead of `.venv` produced *four phantom test failures, a phantom 77-CVE pip-audit,
  and a vacuous `mypy ok 0`*, and those phantoms were **reported as real defects**. Same root cause
  as `DECISIONS.md:12884`, where the pip-audit gate had been auditing the user's system Python (103
  phantom vulns) — and the *previous* diagnosis of that symptom ("venv staleness") had to be
  formally retracted.
- Node 22 vs 24 vs 26 jsdom/localStorage: logged `:37` (08-03), bit again `:135` (08-05) — *"the
  gotcha lives only in LEFT_OFF prose, so every fresh session re-trips it."*

### Class 5 — Honesty/UX inversion: the app tells the creator the opposite of the truth (~15 rows)

Notable because the project's stated identity is honesty.

- `:22` — `review/YourCall.tsx:124` hardcoded `text-success`, so the failure string
  `'Error — try again'` rendered in **success green at 12px**; the panel closed before awaiting the
  POST; the rating was silently discarded. The row states plainly: *"No gate could have caught
  this"* — the token was valid, just wrong.
- `:40` — `LongFormEditor.tsx:131-138` drew a **fabricated waveform** (`20 + ((i*37) % 60)%`) under
  the label "Source timeline", over 22-minute sources.
- `:86`/`:137` — "Render with style" on a done clip is a worker no-op; `style_preset["background"]`
  is documented, accepted by the API, persisted, and **read by nothing**.
- Issue 472 (SEV1) — the always-visible Skip button after "Save trim" silently **erased the
  creator's keep label** from the training set while the pile still said `kept`.

### Class 6 — Doc drift, including docs-as-source-of-truth failure (~12 rows)

- `:142` — `docs/MIGRATIONS.md` Rule 4's copy-paste backfill snippet **is not valid PostgreSQL**
  (`UPDATE … LIMIT` is MySQL). Undetected because migration 0057 was the repo's first batched
  backfill — the documented rule would have produced a syntax error *inside a migration, at deploy
  time*.
- `:159` — three factual errors in docs handed to an external reviewer, including **`CLAUDE.md` —
  the governing rules file — citing `WINDOW_S = 75.0` as living in `clip_engine/window.py` when it
  is at `clip_engine/candidates.py:22`**. The stale `CLAUDE.md` Project Structure list *seeded Issue
  497* (the bandit/mypy hole). Row's diagnosis: *"docs cite code by path/line and nothing verifies
  the citation still resolves."*
- `:140` — 1,204 characters of stray keyboard input committed into `docs/issues.md` (bisected to
  `3c5655c`), riding along in every commit for 3 days.

### Class 7 — Tenancy/security backstop gaps (~10 rows), all in one cluster on 2026-06-30

`:47` (app role had `rolbypassrls=true` in prod — the entire RLS backstop inactive, SEV1) → `:46`
(2 tenant tables never got a policy) → `:45` (fixing the role split **broke ALL sign-ins**, SEV1) →
`:90` (policies 500 instead of denying on reused pooled connections) → `:25` (the test written to
guard that fix was vacuous). **Five defects on one fault line in one day.**

### Class 8 — Money-path leaks (~8 rows)

`:70` (cached tokens billed 0×), `:114`/`:116` (identity chat entirely unbilled — invisible to the
spend guard), `:115`/`:117` (thumbnail vision analysis unbilled, largest per-call leak ~$0.055),
`AUDIT_KNOWN_ISSUES.md` §D (the Stripe webhook grants `pack.minutes` without comparing
`amount_total` to `price_cents`; `async_payment_succeeded` referenced in a comment but unhandled).

### Class 9 — Deploy/ops/monitoring blind spots (~8 rows)

`:104` — prod was down (HTTP 530) and found only by manually curling, with `health-check.yml`'s
schedule dead since 2026-06-17 and the uptime monitor (#282) still open.
**⚠️ Corrected 2026-08-17 (this audit):** the "9-day silent outage" framing used in earlier drafts of
this file is **wrong and is retracted**. `docs/GO_LIVE.md:82` records that the Jul 28→29 ~31h
downtime was an **intentional owner poweroff to save cost**, not a silent failure, and that the
"gap PROVEN in production / beta-critical" wording was already retracted on 2026-07-31. What remains
true and is the real finding: `health-check.yml`'s schedule silently died on 2026-06-17 and nobody
noticed for six weeks, so the monitoring gap is real while the outage that "proved" it was not.
Do not cite a silent multi-day outage as evidence. `:91` — Issue 271's auto-rollback was a **no-op
image swap** (`docker-compose.prod.yml` hardcoded `:latest`, `${IMAGE_TAG}` never interpolated) — a
safety net that couldn't roll back.

### Class 10 — Domain/algorithmic correctness in the clip engine (27 issues filed in one day)

The 2026-08-12 audit filed Issues 456–482 (5 SEV1, 21 SEV2, 1 roll-up) — none caught by any gate.
Verdict on dimension 4: **"COMPROMISED — the green dashboard materially overstates what is
proven"** (`CLIPPING_INTEGRITY_2026-08-12.md:23`).

### Class 11 — Decision reversal / re-litigation (~15 explicit, 101 reversal-language hits in DECISIONS.md)

- The **Sonnet prompt-cache floor flipped three times**: 2048 → "1024, not 2048 as previously
  documented" → Issue 138 "corrected" it back to 2048 (`DECISIONS.md:7729`) → Issue 315 declares
  1024 and marks *all* prior refs **"SUPERSEDED and historically incorrect"** (`:2837`, `:2842`).
- Coverage baseline corrected 77.00 → 83.00 with an explicit retraction of the prior day's
  diagnosis (`:12884`).
- Others: `:3112` (Issue 204 reverses Issue 100), `:12634` (OAuth scope replace-on-grant reverses
  Issue 352 Batch D), `:12072` (reverses the beta-OTel deferral), `:395` (split-screen REVERSED),
  `:277` (Issue 455 supersedes Issue 453 *one day later*), `:4107`.

---

## B. Stage of escape — how far downstream problems get

Hand-classified from the "Found while" column of all 138 rows:

| Stage caught | Rows | % | Notes |
|---|---|---|---|
| **Design / CHECK research** (before code) | ~5 | 4% | Reading code before writing found `:100`, `:114-117` |
| **Build** (while implementing something *else*) | ~52 | 38% | The modal outcome. Issue 389 alone spawned 4 rows in one session |
| **Local test / gate run** | ~30 | 22% | Mostly test-infra, not product |
| **CI / PR watch** | ~13 | 9% | **The lowest-yield stage relative to its cost** |
| **Staging drills** | ~4 | 3% | And the drills themselves were the bug 4 times over (#105–#109) |
| **Deliberate post-hoc audit / sweep** | ~22 | 16% | High-yield: 27 issues from one audit |
| **Production — owner-reported or live-verified** | ~12 | 9% | Every incident costing >1 day is here |

**The key metric.** Roughly **1 in 11 logged defects escaped to production**, and CI caught fewer
than 1 in 10. But severity is inverted: **every SEV1/BLOCKER in the corpus was caught at production
or by a deliberate audit — none by CI.** The three "dead for weeks" outages escaped *all* stages
including production monitoring, and were found by a human reading code side-by-side.

| Class | Typically caught at | Escapes to prod? |
|---|---|---|
| 1. Vacuous green | Deliberate audit only (by construction — no gate can catch a gate) | **Yes — the 3 longest outages** |
| 2. Test infra | Local suite run | No |
| 3. SDK/API surprise | **Production** | **Yes, always** — 6 of 8 ISSUES_LOG entries |
| 4. Config/env drift | Build or local run; prod for the "never set" variants | Partially |
| 5. Honesty/UX inversion | Production (owner screenshots) or visual audit | **Yes** |
| 6. Doc drift | Only when someone reads the doc for a new purpose | n/a |
| 7. Tenancy/RLS | Live harness against the real DB role — `:47`: *"only shows up against the live role"* | **Yes** |
| 8. Money path | Deliberate cost-path sweep | **Yes** (10 weeks + unmeasured leaks) |
| 9. Deploy/monitoring | Production, by manual curl | **Yes** (monitoring gap real; see the retraction above — the outage that "proved" it was an intentional poweroff) |
| 10. Clip-engine domain | One 12-agent audit | Yes — shipped clips were wrong |

---

## C. Defect density and churn

| File | Commits | Size | Concentration of findings |
|---|---|---|---|
| `worker/tasks.py` | **131** | 7,179 lines, 41 task fns | Format drift `:56`, mislabeled log fields `:98`, unbilled calls, redelivery double-spend (open), advisory-lock leak `:61` |
| `config.py` | **99** | 1,208 lines | Class 4 epicenter |
| `.env.example` | **87** | 434 lines | Config-drift shadow; still drifted (`CELERY_SOFT_TIME_LIMIT_S`, `YOUTUBE_PUBLISH_PRIVACY` undocumented) |
| `routers/clips.py` | **67** | 2,893 lines, 27 endpoints | **Carries 3 of the 4 open SEV2s** — Issues 442, 468, 470, `:86`, `:143`, `:149` |
| `models.py` | 57 | — | 62 migrations; 0038/0040/0041/0044/0052/0057/0062 all repair earlier ones |
| `main.py` | 43 | — | CSP: media-src missing → prod player blank; then font origins left dead-allow-listed `:119` |
| `routers/auth.py` | 39 | — | OAuth callback 500s, RLS pre-auth writes, best-effort erasure |
| `clip_engine/scoring.py` / `render.py` / `ranking.py` | 31 / 30 / 24 | — | Issues 456–482 land here |

**No per-module coverage floor exists on `routers/`, `worker/`, `billing/`, `youtube/`, `chat/`,
`knowledge/`, `dna/`, `ingestion/`** (`AUDIT_KNOWN_ISSUES.md` §C3) — the highest-churn files have
the weakest gates. `chat/` — never type-checked by mypy at all — is exactly where two of the July
SEV2s landed.

---

## D. The five most expensive incidents, and what would have caught each earlier

**1. Stripe billing dead 10 weeks (2026-05-31 → 08-12), with two more failures behind it.**
`HTTPXClient(allow_sync_methods=False)` → every checkout raised. Fixed `b2a71ff` (#82), then
`c138e93` (#84, Issue 455 — drop HTTPXClient entirely, superseding #82 *one day later*), then
`d0390b6` (#88, tab-scoped replay broke the second buy), then the first real purchase credited
nothing → **webhook registered at `/webhooks/stripe` while the app serves `/billing/webhook`** (404
for its whole life) → **and** Cloudflare's OWASP ruleset blocking Stripe's IPs at the edge. Six-plus
PRs, ~4 days of active debugging, plus 10 weeks of zero revenue capability.
→ **Earlier catch:** *"treat 'provider says paid' and 'app granted entitlement' as two separate
assertions"*, plus a test that reads the app's own route table and pins the webhook path. **A single
real $1 purchase in the first week would have caught all three.**

**2. YouTube catalog sync imported nothing for 7 weeks.** `fields=` projection omitted `kind`; the
parser filtered on `kind`; `fetched += 1` counted loop iterations, so the log said `"Synced 4 new
video(s)"`.
→ **Earlier catch:** *apply the REAL projection to the fixture before feeding it to the parser.* The
existing fixture hand-wrote `"kind": "youtube#video"` — a shape the real request **cannot produce** —
and nothing referenced the fixture at all. Also: log raw-vs-kept counts at every `if not items:
return` boundary.

**3. Core loop non-functional — 0/18 clips had ever rendered (found 2026-06-30).** `ingest_video`
overwrote `video.source_uri` with the extracted WAV and deleted the mp4 seconds later; render then
downloaded audio-only (`:44`, migration `0039`). Existing clips **unrecoverable**.
→ **Earlier catch:** the row says it itself — *"Rungs 1–3 all passed because they seed a real video
as `source_uri`; only rung 4 exercised the real ingest→source=audio path."* **One real end-to-end
upload, once, at any point in the preceding month.**

**4. Every prod migration was a silent no-op; prod DB stuck 7 revisions behind shipped code
(2026-06-24).** `alembic/env.py` ran `SET lock_timeout` before `context.begin_transaction()`;
`alembic upgrade head` exited 0, printed nothing, changed nothing. Recovery required a `pg_dump`,
offline `--sql` generation, and a manual `psql` apply against production.
→ **Earlier catch:** the one-line assertion added *afterwards* — `scripts/deploy.sh` now asserts
`alembic current == head` after the upgrade step. Pure Class-1: exit 0 accepted as proof of work.

**5. The RLS cluster (2026-06-30) — five SEV1/SEV2 defects on one fault line in one day.** Prod's
app role had `rolbypassrls=true` (`:47`, SEV1). Activating the role split then **broke all sign-ins**
because the OAuth callback writes tenant tables pre-auth with no GUC set (`:45`, SEV1, complete prod
sign-in outage). Then `:46`, `:90` (Issue 354, 27 policies / 11 migrations), `:25`.
→ **Earlier catch:** running the integration lane **as the non-privileged role** from day one. The
role split was documented as pending in a `db.py` comment for weeks — a known-incomplete security
control with a green test suite over it.

**Runners-up:** the staging-drill chain — one vacuous drill took **five consecutive PRs (#105→#109)
in a single day** to make honest, each fix revealing the next defect; and the ~31-hour totally
silent prod outage (`:104`) where no monitoring fired because `health-check.yml` had stopped running
6 weeks earlier.

---

## E. Process evidence — where it visibly broke down

1. **CI could be bypassed by design, and was.** `.github/workflows/ci.yml:28-31` deliberately omits
   a `push` trigger, commenting that *"every change reaches main via a PR … and the local pre-push
   hook gates direct pushes."* Both halves were false: the pre-push hook **was not installed on this
   clone**, so direct commits to `main` ran **zero of ~12 jobs**. Consequence (`:141`, 2026-08-10):
   two gating jobs sat **red on `main` for days** and the whole Issues 438–441 batch reached main
   without CI. Branch protection had been explicitly deferred (`DECISIONS.md:5236`) and was only
   enforced on **2026-08-15** — commit `8c4d286`, **day 82 of 82**.

2. **Merging while red was tolerated.** `:152` (2026-08-13): *"SEV2 (red main; W4 was merged while
   red — **process failure noted**)"* — breaking a pattern `tests/test_billing_integration.py:201`
   already documents as forbidden.

3. **The off-course log is a write-only backlog.** 138 rows; **52 (38%) still Open**; only **10**
   ever promoted into `docs/issues.md`. Rows sit open for months: `:42` (Playwright CI jobs have
   failed on every merged PR since 2026-07-02, so those jobs *carry no signal at all*), `:26`
   (`BACKUP_R2_BUCKET` unset since 08-04), `:70` (cached-token under-billing, open since 06-24
   "awaiting approval — money path"). **The mechanism successfully prevents derailment but has no
   closing pressure** — which is precisely the "baby snag after baby snag" sensation: the snags
   aren't disappearing, they're accumulating in a file.

4. **Repeat-offense rate is the clearest process signal.** Three rows document the *same* uncaptured
   vitest flake (`:133`, `:147`, `:157`), each ending with the same advice that was already written
   down and not followed. The node-version gotcha bit twice (`:37`, `:135`) because the fix was left
   as prose in `LEFT_OFF.md` instead of `.nvmrc` + `engines`. `:29` notes a structural gate
   false-positived on a comment *"for the second time."* **The project repeatedly identifies the
   correct structural fix and then does not make it structural.**

5. **Findings arrive in bursts from audits, not from the pipeline.** 27 issues on 2026-08-12 from
   one audit; 8 SEV1/SEV2s on 07-29 from one assessment; 4 rows from one Issue-389 session.
   Steady-state CI contributes ~9%. The delivery model is effectively "build fast, then pay for a
   deep audit" — which is why fix commits outnumber feature commits.

6. **Issue numbering vs. reality.** 497 issues in 82 days (~6/day). `docs/issues.md` header still
   says "Active lane: L26" when L27/L28/L29 are complete; `LEFT_OFF.md` disagrees with `issues.md`
   about the next free number. The tracker itself has tracker bugs (`:123` — Issues 194/195 have no
   `Status` line, so any automated count silently reads a neighbouring issue's text).

7. **What is working, for contrast.** The diagnostic discipline is genuinely strong: every
   `ISSUES_LOG.md` entry carries an explicit **"Wrong hypotheses (ruled out — don't repeat these)"**
   section and a generalizable diagnostic rule; the 2026-08-12 audit ran adversarial verifiers whose
   charter was to kill each finding and **refuted 2 outright plus materially corrected 10**
   (`CLIPPING_INTEGRITY_2026-08-12.md:97-99`); `DECISIONS.md` records retractions of its own prior
   diagnoses (`:12884`) rather than quietly overwriting them.

**The synthesis that motivates this audit:** the failure is not in root-causing — it is that
**nothing converts a root cause into a mechanism that makes the class impossible**, so the same
shapes keep returning under new names.
