# D10 — Test strategy and gate design

**Domain researcher pass, 2026-08-17.** Read-only. Ground truth taken as given from
`00-groundtruth/{snag-taxonomy,process-map,architecture-map}.md`; `docs/AUDIT_BRIEF.md` §5/§9 and
`docs/AUDIT_KNOWN_ISSUES.md` read before filing.

---

## Verdict

The suite is not too small, not badly written, and not short on assertions — I measured that
directly (18 of 3,102 test functions have no assertion of any kind, and ~15 of those are legitimate
"must not raise" tests). **The problem is that the gates are aimed at the wrong risk class.** Every
required check verifies code the maintainer wrote against fixtures the maintainer wrote; the three
multi-week outages all lived at the seam between this code and a system nobody in this repo
controls (Google's `fields=` projection, Stripe's transport + webhook registration, the coverage
tool's own exit semantics). That seam has exactly one real guard today, and it was retrofitted
after the incident.

And the single gate that exists to measure whether tests *assert* rather than merely *execute* —
mutation testing — **has never executed a single mutant in its entire life, while reporting
`success` on all eight weekly runs I sampled.** That is instance #5 of the vacuous-green class the
brief asked for, and it is inside this domain.

---

## What the current standard is, with sources

**1. Contract testing does not apply to providers you do not own — schema/projection validation
does.** The 2026 consensus is a hybrid: consumer-driven contracts (Pact) *only* between services
you can run verification against, and OpenAPI/schema-based validation for third parties, because
"only the consumer side is testable — the provider is a third party and cannot run Pact
verification," and "if a provider verification needs a live third-party system to pass, the contract
boundary is probably drawn too wide."
([Total Shift Left, 2026](https://totalshiftleft.ai/blog/what-is-api-contract-testing);
[astaQC, 2026](https://www.astaqc.com/software-testing-blog/contract-testing-microservices-api-validation-2026))
Pact is the wrong tool for `youtube/`, `billing/`, `ingestion/`. Do not adopt it here.

**2. The mechanism for third-party seams is record-and-replay against the *real* request shape.**
VCR-family tooling exists precisely so that "the response will contain the same headers and body you
get from a real request," with CI pinned to `--vcr-record=none` and periodic re-record to detect
drift ([vcrpy 8.0 docs](https://vcrpy.readthedocs.io/en/latest/usage.html);
[pytest-recording via Simon Willison](https://til.simonwillison.net/pytest/pytest-recording-vcr)).
The complementary layer is response-schema conformance, which "catches the slow drift where the code
returns a field the schema forgot to document"
([Schemathesis](https://schemathesis.readthedocs.io/), [schemathesis.io](https://schemathesis.io/)).

**3. Mutation testing at scale is diff-scoped and surfaced in review, never a full-repo per-PR
gate.** Google runs mutants over 1,000+ projects for 24,000 developers by mutating *only changed
lines*, suppressing uncovered and "arid" lines, and showing ~1 mutant per diff to the reviewer;
alive mutants a reviewer judges irrelevant are simply ignored, not failed
([Petrović & Ivanković, *State of Mutation Testing at Google*](https://research.google/pubs/state-of-mutation-testing-at-google/)).
The tooling analogue is Stryker's incremental mode (since 6.2), which reuses ~94% of prior results
and brings per-PR runs into the 1–5 minute range
([Stryker incremental docs](https://stryker-mutator.io/docs/stryker-js/incremental/)).

**4. Coverage: diff coverage on changed lines is the gate; the global number is a report.** Google's
own guidance is to use coverage-diff reports for new code, exclude generated/config/test-utility
code, and treat 70–85% as the practical band, with "human judgment of where you can risk leaving the
code untested" mattering more than any single figure
([Google Testing Blog, *Code Coverage Best Practices*](https://testing.googleblog.com/2020/08/code-coverage-best-practices.html)).
Current practice layers a *tiered* target — payment, auth, data integrity at 90%+; UI and admin
tooling at 60–70% — rather than one org-wide number
([diff_cover](https://github.com/Bachmann1234/diff_cover) default `--fail-under` in the 70–80 band).

**5. Assertion-presence linting is a real, standard rule.** Sonar S2699 ("Tests should include
assertions") exists because "a test case without assertions ensures only that no exceptions are
thrown," with documented false-positive modes around helper-file assertions
([SonarSource S2699 discussions](https://community.sonarsource.com/t/sonarqube-and-test-assertions-s2699/24540)).

**6. Where unit tests structurally cannot help, the field uses synthetic monitoring of critical user
journeys, defined as code and reviewed in PRs.** Checkly's "Monitoring as Code" model — Playwright
journeys living in git, deployed through CI, run against production and against preview
environments as a deploy gate — is the 2026 mainstream shape
([Checkly + Playwright guide](https://qaskills.sh/blog/checkly-playwright-synthetic-monitoring-guide);
[Synthetic monitoring with Playwright, 2026](https://qaskills.sh/blog/synthetic-monitoring-playwright-guide)).

**7. Context for the volume of AI-written tests.** DORA 2025 names a "verification tax": AI
"increases the rate of code generation faster than review and deployment infrastructure can absorb
it," and increased AI adoption correlates with increased delivery *instability* even as individual
effectiveness rises ([DORA 2025](https://dora.dev/dora-report-2025/);
[Google Cloud announcement](https://cloud.google.com/blog/products/ai-machine-learning/announcing-the-2025-dora-report)).
A 1.4:1 test:source ratio produced by AI assistance is not evidence of rigour by itself; what
matters is whether the tests are aimed at the seams.

---

## Answers to the six questions

### Q1 — What the field uses where unit tests structurally cannot help, in order of value *here*

Ranked by expected value at this scale, not by prestige:

| Rank | Mechanism | Applies here? | Why |
|---|---|---|---|
| 1 | **Real-projection / recorded-response replay** (VCR-family, or the hand-rolled equivalent) | **Yes — already invented in this repo, applied to 1 of ~6 external seams** | Caught outage #2 retroactively. Cheapest, most repeatable, runs in the default lane. |
| 2 | **Synthetic journeys against production, as code** | **Yes — already 80% built and wired to nothing** | `frontend/playwright.config.prod.ts` + `e2e/prod/` exist, run against real prod with real auth, and are manual-only (`process-map.md:194-195`). This is the only mechanism that would have caught outages #1 and #2 *while they were happening*. |
| 3 | **Registered-endpoint ↔ route-table reconciliation** | **Yes — nothing does it** | The Stripe webhook was registered at `/webhooks/stripe` while the app served `/billing/webhook`, 404 for its entire life. ~15 lines in `doctor.py`. |
| 4 | **"Test the test" / gate-integrity invariants** | **Yes, and it is the project's own best idea** | `tests/test_ci_config.py` (22 tests over the CI YAML), `tests/test_eval_transparency.py`, `tests/test_llm_conformance.py`, `tests/test_usage_coverage.py`. Under-generalized — see F1, F2. |
| 5 | **Mutation testing as a gate** | **No — see Q5** | Diff-scoped mutation is the standard, mutmut has no incremental mode, and the current run is dead anyway. |
| 6 | **Consumer-driven contract testing (Pact)** | **No** | There is no second service you own. Adopting Pact here is pure ceremony. |
| 7 | **Assertion-density linting** | **No — measured, the problem does not exist** | 18/3,102 (0.6%), ~15 legitimate. See "what is right". |

### Q2 — The catalog-sync mechanism, named

The standard name is **response-projection (partial-response) conformance**: never let a fixture be
hand-authored in a shape the real request cannot produce; apply the *actual* request's projection to
the fixture before the parser sees it, and pin the projection simulator itself.

**This repo already built exactly that**, and it is the single best piece of test engineering in the
tree: `tests/test_data_api.py:44-140` implements Google's `fields=` grammar (`a,b`, `a(b,c)`, `a/b`),
pins the simulator against Google's own documented example
(`test_fields_simulator_matches_googles_documented_semantics`, `:214-240`), then feeds
`_FIELDS_PLAYLIST_ITEMS` and `_FIELDS_VIDEOS_CONTENT_DETAILS` through it before parsing
(`:161-203`). The comment block at `youtube/data_api.py:42-56` records why.

**What it looks like generalized** — the seam list and its current state:

| Seam | Real-shape guard today |
|---|---|
| YouTube Data API (`fields=` projection) | ✅ `tests/test_data_api.py` projection simulator |
| Anthropic messages API | ✅ `tests/test_scoring_goldens.py` — real recorded bodies replayed through `anthropic.types.Message`, incl. a real `stop_reason="max_tokens"` golden, sha256-pinned to `_OUTPUT_SCHEMA` |
| Deepgram nova-3 | ✅ nightly `transcription_live` re-transcribes the LibriSpeech fixture against the live API and asserts word timings (`llm-e2e-nightly.yml:93-121`) |
| **Stripe** | ❌ nothing replays a real Stripe object through `stripe.*` model classes; `doctor.py:400-417` proves reachability, not parseability |
| **R2 / boto3 presign** | ❌ nothing pins SigV4/`auto` against a recorded response (this class already bit — `ISSUES_LOG:505`) |
| **Voyage embeddings** | ❌ nothing |
| **Stripe webhook *delivery*** | ❌ nothing (see F5) |

### Q3 — Coverage philosophy and what to delete

The suite is **not** too big in the abstract; 78k lines against 56k is high but defensible for a
solo maintainer using the tests as executable documentation. It *is* misallocated. Concretely:

**Delete:**
- `tests/test_static.py` — **1,861 lines, the largest test file in the repo, 80 tests of which 34 are
  `@pytest.mark.skip`d against HTML pages deleted in Issue 226.** Keep only the handful covering the
  surviving `tos`/`privacy`/`accessibility` routes. (~1,400 lines out.) The skip *count* is already
  in `AUDIT_KNOWN_ISSUES.md` §C4; the new signal is that it is the biggest file in `tests/`.
- The 8 `tests/test_issue_*.py` files (2,605 lines). Named by *when* not *what*; nobody adding a
  feature will find `test_issue_113.py`. Merge each into the behaviour file it belongs to. Zero
  coverage change, real navigability gain.
- `tests/test_notifications.py:636` and `tests/test_notifications_triggers.py:652` — don't delete;
  re-mark `@pytest.mark.integration`. Their skip reason ("needs real Postgres") describes a lane
  that runs on every PR (already known, §C4).

**Do not delete:** the structural/AST guards, the eval scenarios, the goldens, the projection
simulator. Those are the 20% carrying the 80%.

**Coverage philosophy verdict:** the global 83% floor is the *weakest* of the three coverage gates
and should be demoted to a report. `diff_cover --fail-under=80` (`run_layer0.py:416-424`) is doing
the real work and matches current practice. The per-module tiering is right in principle and
misapplied in fact — floors exist on `clip_engine`/`preference`/`crypto`/`limiter`/`auth` (the code
that changes least) and on none of `routers/ worker/ billing/ youtube/ chat/ knowledge/ dna/
ingestion/` (the code that changes most). Already filed as §C3; my only addition is the *mechanism*
that makes it cheap — `run_layer0.py` already supports `--update-baseline`, so per-module floors can
be auto-ratcheted from measurement instead of hand-chosen, which is the reason they were never
added.

### Q4 — The 8 required checks ranked by signal-per-minute

Measured from three consecutive real runs (`gh run view` on 31908591494 / 31895047970 / 31894386424).

| # | Check | Median | Signal per minute | Call |
|---|---|---|---|---|
| 1 | `Lint (ruff)` | **11 s** | High — trivially cheap, catches import/bugbear classes | **Keep** |
| 2 | `eval/clip-quality` | 20 s | Very high *when it runs*; skipped on most PRs by paths-filter, and the same scenarios also run inside `Unit` | **Keep** |
| 3 | `Docker build (smoke)` | 40 s | Medium — catches dep-resolution/Dockerfile drift before publish (the 2026-05-28 starlette class) | **Keep** |
| 4 | `Types + SAST + deps` | 120 s | **Currently low** — mypy skips 4,093 lines, bandit 8,277 (Issue 497), and the job runs `run_layer0.py` **without `--require`**, so an unparseable tool output silently becomes `skipped` + "All runnable gates passed" (`process-map.md:71-77`) | **Keep, but fix `--require` first** |
| 5 | `Playwright (smoke + a11y)` | 138 s | Medium — backend fully mocked at `e2e/fixtures/mock-api.ts`, so it can only catch frontend-local regressions | Keep |
| 6 | `Coverage floor` | 189 s | **Low marginal** — re-executes the entire 3,234-test lane that `Unit` just executed; its unique signal is three threshold comparisons over an XML | **Merge into `Unit`** (F4) |
| 7 | `Unit tests (pytest)` | 203 s | The workhorse — but per the stage-of-escape table, CI catches <1 in 10 logged defects and 0 of the SEV1s | Keep |
| 8 | `Integration (postgres + redis)` | 222 s | **Highest severity-weighted signal in the set** — the only lane against real PG, and `tests/test_rls_isolation_integration.py` correctly `SET LOCAL ROLE creatorclip_app`s so RLS is actually exercised rather than owner-bypassed | Keep |

**Ceremony:** `Coverage floor` as a *separate job*. **Not ceremony but currently hollow:**
`Types + SAST + deps`.

**Promote to required (F3):** `Frontend (lint, test, build)`, `Migration lint (Squawk)`,
`Visual regression`. All three already run on every PR and are **25/25 green across the last 25 CI
runs** — promotion costs zero additional CI minutes and zero expected friction.

### Q5 — Is mutation testing worth making a gate?

**No — and the prior question is that it is not currently running at all (F1).**

The recorded position (`docs/DECISIONS.md:2676-2737`, Issue 273) — scope to the load-bearing core,
weekly cadence, never per-PR, report-not-gate — is *correct on cadence* and matches the standard's
warning against per-PR full-repo mutation. I am not arguing against it. Two things have changed
since it was written:

1. Current practice has moved past "cadence vs. gate" to **diff-scoped mutation surfaced in review**
   (Google: mutate changed lines only, suppress arid/uncovered, ~1 mutant per diff). mutmut has no
   incremental mode; that shape is not reachable with the current tool.
2. The recorded position's *own success criterion* ("target score >80%, survivors triaged into
   test-gap follow-ups") has never been evaluable, because the score has always been zero-of-zero.

**Recommendation: fix it, keep it non-gating, and add one assertion — the run must fail if fewer
than N mutants were actually checked.** That single line converts the weekly report from
unfalsifiable to falsifiable and is the whole value.

### Q6 — The smallest set of changes that would have caught the three outages

Costed in maintainer-hours. Ordered by value-per-hour.

| # | Change | Would have caught | Cost |
|---|---|---|---|
| 1 | **Wire `e2e/prod/` into a schedule.** `frontend/playwright.config.prod.ts` and the prod specs already exist and already authenticate against the live site. Add a `workflow_dispatch` + `cron` workflow that runs them every 30 min and fails loudly. | **#1 (catalog sync, 7 wks) and #2 (Stripe, 10 wks)** — both were user-visible dead journeys. Also closes `GO_LIVE.md`'s open "independent uptime monitoring" and the dead `health-check.yml` schedule. | **2–3 h** |
| 2 | **Make every workflow prove it did work.** Replace `mutmut run \|\| true` + `mutmut results \| tee` with a script that parses the result counts and exits non-zero if `checked == 0` (F1). Generalize: one test in `tests/test_ci_config.py` that greps all workflows for `\|\| true` and for `cmd \| tee` on a load-bearing step, and fails on any un-allowlisted instance. | **#3 (coverage gates dead 7 wks) and the live mutmut outage** — both are "exit 0 accepted as proof of work". | **2 h** |
| 3 | **Add `--require` to the `static-gates` invocation** (`ci.yml:419-435`), exactly as the coverage job was hardened in Issue 479. | **#3's remaining twin.** Today an unparseable mypy/bandit/pip-audit output silently becomes `skipped` + green. | **15 min** |
| 4 | **Reconcile external registrations against the route table** in `doctor.py`: `stripe.WebhookEndpoint.list()` → assert each URL's path resolves in `main.app.routes`; same idea for the OAuth redirect URI. | **The third layer of #2** (webhook 404 for its whole life). Nothing prevents a recurrence. | **1–2 h** |
| 5 | **Promote Frontend / Migration lint / Visual regression to required** (one `gh api` call, list already written in `docs/BRANCHING.md:104-124`), and add a test pinning that list against `ci.yml` job names + any job comment claiming "GATING" (F2). | Not one of the three, but it is the cheapest real gate expansion available and it closes the live `Visual regression` contradiction. | **1 h** |
| 6 | **Extend the projection/replay pattern to Stripe and R2** — one recorded `checkout.Session` and one recorded presign response, replayed through the SDK's own model classes the way `test_scoring_goldens.py` does for Anthropic. | The *next* one of this class. | **3–4 h** |

Total: **~10–13 hours** to close the class that has cost this project 24 weeks of dead subsystems.

---

## Findings

### F1 — SEV1 · The mutation gate has never executed a single mutant, and is structurally incapable of failing

**This is instance #5 of the vacuous-green class.**

`.github/workflows/mutation.yml:48` runs `mutmut run || true`. Line 52 runs
`mutmut results | tee mutmut_results.txt` — in a pipeline, bash reports the **exit status of the
last command (`tee`)**, so even a crashing `mutmut results` exits 0. Lines 53-58 wrap the second
invocation in a `{ … } >> "$GITHUB_STEP_SUMMARY"` group whose status is the trailing `echo`. **No
step in this workflow can fail.**

It has been failing since at least 2026-06-29:

```
run 32007314899 (2026-08-17), step "Run mutmut":
  mutmut.__main__.BadTestExecutionCommandsException: Failed to run pytest with args:
  [... 'tests/test_crypto.py', 'tests/test_preference.py', 'tests/test_scoring.py']
artifact mutmut-results.txt:  990 lines, 990 of them "not checked"
run 28359402567 (2026-06-29) artifact:  796 lines, 796 "not checked"
gh run list --workflow mutation.yml --limit 8  →  8 × "success"
```

**Root cause:** `pyproject.toml:139-160` `also_copy` omits `flags.py`, `ingestion/`, `chat/`,
`improvement/`, `media/`, `notify/`, `analysis/`, `shared_resources.py`, `verbose.py`. mutmut removes
the repo root from `sys.path` and runs from `./mutants/`, so `tests/conftest.py` → `import main` →
`routers/*` → `from flags import require_flag` (`routers/review.py:26`, `routers/clips.py:27`,
`routers/auth.py:18`, +7 more) raises `ModuleNotFoundError` inside the sandbox. Collection fails,
mutmut aborts at the stats phase, `|| true` swallows it, the artifact uploads 990 unchecked mutants,
and the run is green.

**Failure scenario.** Someone weakens `clip_engine/scoring.py`'s setup-vs-aftermath comparison so
that `_in_window` uses `<` instead of `<=` — the exact mutant (#86) the Issue-273 DECISIONS entry
cites as the proof the gate works (`DECISIONS.md:2735`). Every unit test still passes, line coverage
is unchanged, the weekly mutation job reports `success`, and nobody learns the tests never asserted
on it. The project's own stated reason for having this gate — "line coverage proves these lines RUN;
mutation testing proves the tests ASSERT on them" (`pyproject.toml:113-115`) — has been unmet since
the gate shipped.

**Note the contrast, which shows this is an oversight not a philosophy:**
`.github/workflows/llm-e2e-nightly.yml:88-91` and `:120-121` explicitly use `${PIPESTATUS[0]}` for
exactly this reason. The knowledge is in the repo; the invariant is not.

**Fix:** add `flags.py`, `ingestion`, `chat`, `improvement`, `media`, `notify`, `analysis`,
`shared_resources.py`, `verbose.py` to `also_copy`; replace `|| true` with a script that fails when
`checked == 0`; add a `tests/test_ci_config.py` assertion that no load-bearing workflow step uses
`|| true` or an unguarded pipe.

---

### F2 — SEV2 · Nothing machine-checks which checks are required; `Visual regression` believes it is gating and is not

`ci.yml:606-609` states in a code comment: *"GATING since 2026-07-29 (ready-pass W2)."* The applied
protection contexts (`docs/BRANCHING.md:104-124`, applied 2026-08-15) do not include it.
`tests/test_ci_config.py` has 22 tests about the CI/CD YAML — deploy runner, dump-before-alembic,
sha-pinning, eval commit-status target, render_env hardness — and **none about the required set**.

**Failure scenario.** A CSS or layout change shifts the login/pricing/empty-dashboard baselines past
`maxDiffPixelRatio: 0.01`. `Visual regression` goes red. The PR merges anyway, because it is
advisory. The next maintainer reads `ci.yml:606` and reasonably concludes visual regressions are
gated, and does not look at the screenshot diff. This is the same shape as the `render-env` marker
that "was registered but zero tests carried it" (Issue 478) — a control that documents itself as
active while being inert.

**Fix (~1 h):** a test that parses the `gh api` JSON block in `docs/BRANCHING.md` (already the
recorded source of truth) and asserts (a) every context matches a `name:` in `ci.yml`, and (b) every
`ci.yml` job whose comment contains `GATING` appears in that list.

---

### F3 — SEV2 · Three advisory jobs are 25/25 green and free to promote; the 92-file frontend suite gates nothing

Measured across the last 25 CI runs via `gh`: `Frontend (lint, test, build)` 25/25 success,
`Migration lint (Squawk)` 25/25, `Visual regression` 25/25. All three already execute on every PR
(`ci.yml:437-457`, `:255-417`, `:606-664`) — promotion to required is a one-line change to the
protection contexts and costs **zero additional CI minutes**.

**Failure scenario.** `OFF_COURSE_BUGS.md:22` is the template: `review/YourCall.tsx:124` hardcoded
`text-success` so the string `'Error — try again'` rendered in success green, and the panel closed
before awaiting the POST, silently discarding the creator's rating. A vitest test asserting the
error branch renders with the error token would catch that class — and today that test could go red
and the PR would still merge. Same for `Migration lint`: the whole expand→backfill→contract safety
apparatus (`.squawk.toml`, downgrade round-trip, `check_downgrades.py`) is advisory at the merge
gate, on a project where `docs/MIGRATIONS.md`'s own Rule-4 snippet turned out not to be valid
PostgreSQL (`:142`).

**Judgement call flagged:** the documented cold-first-run vitest flake (`:133`/`:147`/`:157`) is the
plausible reason frontend was left advisory. It has not recurred in CI in 25 runs — it is a local
cold-`node_modules` phenomenon. If it does recur, `@pytest.mark.quarantine`'s frontend analogue
(vitest `test.skip` with a tracked issue) is the documented answer, not leaving 92 files ungated.

---

### F4 — SEV3 · `Unit` and `Coverage floor` each execute the full 3,234-test lane; ~190 s of duplicated work per PR

`ci.yml:113-114` runs `pytest --tb=short -q`. `ci.yml:239-253` runs `run_layer0.py --gates
coverage,module_coverage,diff_cover`, whose `gate_coverage` runs the same lane under `pytest-cov`.
Measured: 197–209 s and 186–191 s respectively, on separate runners with separate Redis services and
separate dependency installs.

**Consequence** (not a correctness bug — a budget one): the project is spending ~3.5 runner-minutes
per PR re-running tests it just ran, on a solo-maintainer budget, while the frontend suite has **no
coverage measurement at all** (`process-map.md:183-185`) and the prod-journey specs run never.
Merging the two jobs — run `pytest --cov` once in `Unit`, publish `_coverage.xml`, and let the gate
job consume it — recovers that budget and drops the required-check count 8 → 7 with no signal loss.

**Verdict: over-engineered.** Flagged as a judgement call; the counter-argument is that separate
jobs fail independently and read more clearly. At three required checks' worth of runner time, I
don't think that survives.

---

### F5 — SEV2 · Nothing reconciles externally-registered endpoints with the app's route table

`scripts/doctor.py:400-417` was correctly hardened after the 10-week outage — it now probes Stripe
**through the app's own `_STRIPE` singleton** rather than a raw `httpx.get`, with the incident
recorded inline. That closes the *transport* layer. It does not close the *delivery* layer, which
was the second defect in the same incident: the webhook was registered in Stripe at
`/webhooks/stripe` while the app serves `/billing/webhook` (`routers/billing.py`, exercised by
`tests/test_billing.py:364` and 8 other call sites), so every event 404'd for the endpoint's entire
life. Grep for `WebhookEndpoint` across the repo returns **zero hits** — nothing anywhere compares
the registered URL to the served route.

**Failure scenario.** The maintainer rotates the webhook signing secret and re-creates the endpoint
in the Stripe dashboard, mistyping the path or picking the old one from history. Checkouts complete,
`payment_status` is `paid`, Stripe's dashboard shows delivery attempts, and minutes are never
granted. `doctor.py` reports `stripe auth ok`, all 8 required checks are green, the deploy smoke
passes, and the first signal is a customer complaint — exactly what happened the first time.

**Fix (~1–2 h):** in `doctor.py`, `stripe.WebhookEndpoint.list()` (a free read) → for each `url`,
assert `urlparse(url).path` resolves against `main.app.routes`; fail the preflight otherwise. Same
pattern for `OAUTH_REDIRECT_URI` vs the registered Google redirect URIs.

---

### F6 — SEV3 · The delete list: 4,000+ lines of tests aimed at code that no longer exists or at issue numbers

- `tests/test_static.py` — **1,861 lines, the single largest file in `tests/`**, 80 test functions,
  **34 `@pytest.mark.skip`** with reasons of the form *"Issue 226: static/index.html retired — React
  SPA is canonical."* The skip count is known (§C4); the size ranking is not. It sits at the top of
  every "what does this project test?" reading.
- 8 × `tests/test_issue_*.py`, **2,605 lines** (`test_issue_104`, `_105_worker_idempotency`, `_110`,
  `_113`, `_125`, `_126`, `_139`, `_88_filter_parity`). Named by when, not what.

**Failure scenario** (this is a maintainability finding, so the scenario is a maintenance one):
someone changing `/billing/webhook`'s rate limit greps `tests/test_billing*.py`, finds nothing about
the limiter decorator, and removes it — because that assertion lives in `tests/test_issue_110.py:44`.
The guard exists and is unfindable.

---

## What is genuinely right here

Stated specifically, because the domain has real craft in it and the verdict above is harsh.

1. **The projection simulator (`tests/test_data_api.py:44-240`) is better than what most teams
   ship.** It does not just add a regression test for the outage; it reimplements Google's
   partial-response grammar, **pins the simulator itself against Google's documented example**, and
   then applies the real `_FIELDS_*` constants to the fixtures. That is the correct three-layer
   shape, and the "pin the measuring instrument" step is the one almost everyone skips.

2. **`tests/test_scoring_goldens.py` is the right answer to LLM-boundary testing.** Real recorded
   Anthropic bodies, deserialized back through `anthropic.types.Message` so SDK model drift fails
   the test; a genuine `stop_reason="max_tokens"` golden recorded by re-issuing the request at
   `max_tokens≈200`; and a **sha256 pin of `_OUTPUT_SCHEMA`** so a golden can never green-stamp a
   contract it wasn't recorded against. I would copy this file into other projects.

3. **The anti-hollowing pattern is correct and worth generalizing verbatim.** `SCENARIO_FLOOR`
   (`tests/test_clip_engine.py:269`) + `test_eval_scenario_no_unapproved_skip_markers` (`:301`) +
   the second pin in a different file (`tests/test_eval_transparency.py:101-110`, so lowering the
   floor must touch two files and is visible in any diff). **Where to generalize it, concretely:**
   (a) a `MUTANT_FLOOR` on the mutation run (F1); (b) a `TENANT_TABLE_FLOOR` on `_TENANT_TABLES`,
   since the RLS-policy gap (`:46`) was "a table exists with no policy"; (c) a
   `LLM_CALL_SITE_FLOOR` on `_ANTHROPIC_CALL_SITES` in `tests/test_usage_coverage.py`, which is the
   same "the next unbilled site fails CI" idea and already has the registry; (d) a
   `REQUIRED_CHECK_FLOOR` (F2).

4. **The registry+AST-sweep family is the project's real innovation.**
   `tests/test_usage_coverage.py` fails CI on the *next* unbilled Anthropic call site;
   `tests/test_llm_conformance.py` enforces singleton/timeout/typed-exception/untrusted-content-policy
   per LLM module; `tests/test_ci_config.py` gates the CI YAML. These are exactly the "convert a root
   cause into a mechanism" move that `snag-taxonomy.md:334-336` says is missing — it isn't missing,
   it is applied per-incident rather than per-class.

5. **The RLS integration lane is done correctly, including the lesson from its own past failure.**
   `tests/test_rls_isolation_integration.py` explicitly `SET LOCAL ROLE creatorclip_app`s (`:308`,
   `:355`, `:397`, `:428`, …) so policies are evaluated under the non-bypass role rather than the
   container owner, and `test_rls_deny_by_default_unset_context` (`:405-444`) now iterates
   `_TENANT_TABLES` rather than the hardcoded 2-tuple that made `:25` vacuous. The fix stuck.

6. **Assertion quality is not the problem — I measured it.** AST sweep over all 3,102 collected test
   functions: **18 have no assertion of any kind (0.6%)**, and ~15 of those are legitimate "must not
   raise" tests for fail-open paths (`test_spend_guard.py:212,229`, `test_usage_ledger.py:214`,
   `test_observability.py:409`). The one genuinely hollow case,
   `tests/test_render.py:193 test_render_clip_file_calls_ffmpeg_with_crop`, says so in its own
   trailing comment and has a real sibling immediately below it. **Do not spend time on
   assertion-density linting here.** The tests assert; they assert against mocked boundaries and
   hand-authored fixtures. That is a different problem and it is what F1/F5/Q2 address.

7. **`llm-e2e-nightly.yml` gets the exit-status handling exactly right** (`${PIPESTATUS[0]}` at
   `:88-91` and `:120-121`), and the nightly `transcription_live` leg — re-transcribing a real
   LibriSpeech fixture against live Deepgram and asserting word timings to ±0.25 s — is precisely
   the mechanism F5 asks for, already built for one vendor. 10/10 green nightly runs.

---

## Decisions this domain needs but does not have

1. **What "required" means, and where that list lives as data.** Today it exists in three
   inconsistent places: GitHub's protection API (authoritative), `docs/BRANCHING.md` (a copy), and
   scattered `ci.yml` comments (wrong). Pick one source and test the others against it.

2. **A stated policy that a gate must be able to fail.** No document says "a workflow step that
   cannot return non-zero is not a gate." `|| true`, unguarded pipes, `continue-on-error: true`,
   `paths-filter` skips-as-success, and `run_layer0.py`'s skip-without-`--require` are five distinct
   mechanisms for the same outcome, each individually justified somewhere. There is no rule spanning
   them, which is why the class recurs under new names.

3. **Whether per-module coverage floors are hand-chosen or auto-ratcheted.** The hand-chosen model
   is why eight high-churn packages have none. `run_layer0.py --update-baseline` already exists;
   decide whether a floor is a *judgement* (current model, doesn't scale) or a *ratchet from
   measurement* (scales, and is what `docs/assessment/baselines.json` already does globally).

4. **The lifecycle of a skip.** `@pytest.mark.quarantine` has a documented lifecycle and a hard
   prohibition on rerun-as-gate (`pytest.ini:10-13`) — excellent. `@pytest.mark.skip` has none, and
   70 of them have accumulated. Either skips get an expiry/tracking convention like quarantine's, or
   they get deleted.

5. **Frontend coverage: measured or explicitly declined.** There is no `coverage` block in
   `frontend/vite.config.ts` and no decision entry explaining why. At 260 source files against 92
   test files, "we deliberately do not measure frontend coverage because Playwright + the structural
   contract tests carry it" is a perfectly defensible position — it just isn't written down, so
   nobody can tell it from an omission.

6. **Whether production is a test environment.** `frontend/playwright.config.prod.ts`,
   `e2e/prod/`, `scripts/live_smoke.py`, `scripts/drills.py`, `scripts/clip_audit.py` and
   `scripts/eval_efficacy.py` all exist and all run only when a human remembers. The recorded
   position on canaries is the flag-gated synthetic canary (`DECISIONS.md:2049`, Issue 341). The
   unmade decision is whether continuously-scheduled synthetic journeys against real prod are part
   of the test strategy or part of monitoring — they are the highest-value unbuilt mechanism in this
   domain, and they are currently neither.

---

*Sources: [Google Testing Blog — Code Coverage Best Practices](https://testing.googleblog.com/2020/08/code-coverage-best-practices.html) ·
[State of Mutation Testing at Google (Petrović & Ivanković)](https://research.google/pubs/state-of-mutation-testing-at-google/) ·
[Stryker incremental mode](https://stryker-mutator.io/docs/stryker-js/incremental/) ·
[vcrpy 8.0 usage](https://vcrpy.readthedocs.io/en/latest/usage.html) ·
[pytest-recording (Simon Willison)](https://til.simonwillison.net/pytest/pytest-recording-vcr) ·
[Schemathesis](https://schemathesis.readthedocs.io/) ·
[diff_cover](https://github.com/Bachmann1234/diff_cover) ·
[Sonar S2699](https://community.sonarsource.com/t/sonarqube-and-test-assertions-s2699/24540) ·
[Contract testing 2026 — third-party providers](https://totalshiftleft.ai/blog/what-is-api-contract-testing) ·
[Contract testing in 2026 (astaQC)](https://www.astaqc.com/software-testing-blog/contract-testing-microservices-api-validation-2026) ·
[Checkly + Playwright monitoring as code](https://qaskills.sh/blog/checkly-playwright-synthetic-monitoring-guide) ·
[Synthetic monitoring with Playwright 2026](https://qaskills.sh/blog/synthetic-monitoring-playwright-guide) ·
[DORA 2025](https://dora.dev/dora-report-2025/)*
