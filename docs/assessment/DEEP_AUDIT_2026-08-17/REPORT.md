# Deep Standards & Process Audit — AutoClip / CreatorClip

**Date:** 2026-08-17 · **Tree:** `main` @ `1def133` · **Method:** 56 agents in three phases, read-only.
**Question asked:** not "is this production ready" (that is `/assess`) and not "how does it look to an
outsider" (that is `docs/AUDIT_BRIEF.md`), but **"are we building the right thing, the right way, with
the right process, measured against current industry standard?"**

**Trigger, in the owner's words:** *"I notice the numerous times we have hit a baby snag here and
there and it is simply one after the other after the other."*

---

## The answer, in one paragraph

**The architecture is not the problem, and neither is the stack.** Three independent domains
concluded the biggest structural bets are correct and should be left alone — the missing service
layer, the single VM, and `worker/tasks.py`'s line count. What is wrong is narrower and more
specific: **this project diagnoses better than it defends.** It finds the right root cause almost
every time, writes the correct fix into the row that reported the problem, and then does not build
it. Knowledge is stored as *prose that must be read and remembered* rather than *mechanism that
cannot be forgotten*. Nine recurring gotchas each have a correct structural fix already written
down; none of the nine was built; the classes recurred. Roughly **30 maintainer-hours of engineering
controls, starting with a ten-minute change to one npm script**, converts the four classes that
generate the most lost sessions from "remembered" to "impossible."

**But it only explains about 40% of the pain, and the report says so.** The completeness critic
argued this down and was right: ~56 of 138 logged snags (Classes 1, 4, 6 — vacuous green, config
drift, doc drift) are mechanism-addressable. The rest — third-party SDK surprises, clip-engine
domain correctness, most test-infra churn — are not preventable by any internal gate. Class 3
(vendor defaults differing from assumed defaults) escapes to production **every single time** and no
lint, registry, or structural test can catch it. Those need a *habit*, not a mechanism: one real
transaction against each live integration, on a schedule. The taxonomy already said this — incident
#1's earlier-catch was "a single real $1 purchase in the first week"; incident #3's was "one real
end-to-end upload, once."

---

## Read this before you read anything else

**1. Coverage was not uniform. Absence of findings is not a clean bill of health.**
The backend deploy/gate/probe spine was worked hard (`main.py`/`config.py`/`db.py`/`models.py` drew
331 mentions across 20 of 21 reports). But **67% of the frontend (129 files, 14,383 lines) was never
opened**, and `ingestion/transcribe.py` — the live Deepgram/AssemblyAI/WhisperX switch, 408 lines —
got **zero mentions in zero reports**, despite Class 3 being the most expensive failure class and
despite that file's own comments documenting two Class-3 incidents that already happened inside it.
Also unexamined: four `knowledge/clip_*` LLM feature modules (1,106 lines of creator-facing output),
`upload_intel/`, `improvement/`, `analysis/brief.py`, and 59 of 62 migration files. Those are exactly
the two classes that escape to production most reliably (Class 3 SDK surprise, Class 5 honesty
inversion). *Mitigating: the critic separately verified that **no production module in this repo is
entirely untested** — the blind spots are audit blind spots, not coverage blind spots.*

**2. Discount every severity that was not adversarially verified — by about one full level.**
This is the audit's weakest dimension, measured: **of the 21 findings that went through verification,
21 were downgraded and 0 were upgraded.** All 21 were filed HIGH; 18 came back medium and 3 came
back low. The 69 unverified findings still carry their originally-filed severity, which is
systematically hot. Re-rate before scheduling.

**3. `has_repro: true` on an unverified finding means "a repro was written", not "it was run and
matched."** The critic caught one shipped repro block whose stated output does not match what its own
script produces. For the 21 verified findings, the verifier's `repro_detail` quotes real terminal
output and is trustworthy.

---

## Verification ledger

An audit about hollow verification must not be hollow itself.

| | Count |
|---|---|
| Findings produced | **141** (86 standards + 55 sweep) |
| Adversarially verified | **72** |
| — CONFIRMED (a skeptic tried to kill it and failed) | **8** |
| — CORRECTED (core real, scope/severity wrong — use the corrected statement) | **57** |
| — REFUTED (excluded entirely) | **7** |
| Never contested — treat as leads | **69** |
| `file:line` citations extracted from all 18 reports | **909** |
| — resolve | **906** (3 range-*end* overshoots; **0** point at a non-existent file) |
| — hand-checked for content match (the 23 heaviest) | **23/23 support the claim; 0 findings pulled** |
| External sources spot-checked | **11/11** real, current, and supporting |

**Caveat on the ratio.** Phase 2's first verification round corrected 12 of 12 and refuted 0. That is
suspicious, so the critic picked the five it judged weakest and attacked them independently: four
survived intact, one survived with a false repro. The self-filtering explanation holds — the sweepers
were required to produce a repro, which filtered junk before verification.

**Seven findings were killed** and must not be re-filed. They are listed in
`SYNTHESIS_technical.md` §3 "Excluded". Two were consequential: *"`render.yaml` is armed to
auto-deploy a second production copy with PII logging on"* (it is inert without a linked Blueprint)
and *"`ci_local.sh` prints 'Local CI passed' after skipping every gate"* (refuted on three grounds).

---

## The five things to change first

Ordered by value per maintainer-hour, not severity.

**1. `"test:ci": "vitest run --reporter=verbose"` — ten minutes.**
The single most important line in this audit. The same cold-run vitest flake has been logged three
times (`OFF_COURSE_BUGS:133`, `:147`, `:157`), and on all three occasions **the failing test's name
was never captured**, each time because nobody remembered to pass the flag beforehand. This is the
canonical unfixable-by-documentation shape: the advice must be applied *before* the failure is
observable, so it can only ever be a default. Three sessions lost; ten minutes to retire the class.

**2. `run_layer0.py` must check `proc.returncode` — two hours.**
The most important gate finding in the audit, and it inverts the obvious fix. Four of eight Layer-0
gates infer their result purely from stdout and never inspect the exit code, so **a tool that runs
and fails scores `{"status":"ok","value":0}`** — a perfect score against the strict baseline of 0.
`--require`, the project's own hardening primitive built for exactly this class (Issue 479), only
escalates a gate whose status is `skipped`, so it **cannot see this failure mode at all**. Adding
`--require` to `static-gates` — the fix the process map recommends — would not have helped. Most
plausible live consequence: pip-audit reporting "0 vulnerabilities" during a PyPI/OSV outage, on a
required check. *(ruff is independently guarded by the separate required lint job.)*

**3. Personalization is a measured no-op across its entire ramp — 4–6 hours.**
`LGBMClassifier` is fitted with `min_child_samples=20` untouched, making it a **constant predictor
for label counts 21–40** (measured: 200/200 degenerate at n=38–39, 0/200 at n≥42). The blend weight
ramps 0→cap across exactly n=20→40, so the whole ramp lies inside the dead zone; `blended_score`
becomes a monotone transform of `score` and the persisted order is byte-identical to DNA-only —
while `GET /videos/{id}/clips` returns `personalization={active:true}`. Corollary: the
LogisticRegression branch is unreachable at serve time by construction. This is both a product
defect and an honesty defect against the north star. **And the gate written four days ago to guard
this certifies the false property** — its fixture is `rows_per_class: 20`, the single 40-row shape
where LightGBM can split (verified: 92 trees on the fixture; move one label and it collapses to 1
tree, spread 0.000000).

**4. The rate limiter fails CLOSED with an unhandled 500, not open — two hours.**
`slowapi` is constructed with neither `swallow_errors` nor `in_memory_fallback`, so any Redis error
re-raises and every rate-limited route — including `GET /auth/me` at 120/min, called by `AuthGate` on
every page load — returns 500 while the body never runs. Reproduced against a dead Redis. A Redis
*stall* is sufficient. **Three documents state the opposite posture**, including `DECISIONS.md:2633`
and the module's own docstring, and the module carries a 99% coverage floor with **zero** tests for
Redis-unavailable behaviour. Either fix the code or fix all three documents — but the record and the
code currently disagree about a load-bearing availability decision.

**5. There are no backups of any kind — two hours plus operator steps.**
`BACKUP_R2_BUCKET` has never been set, and it gates *both* the pre-migration dump and the nightly
cron (`backup_pg.sh` hard-dies on it). RPO is total loss of the billing ledgers, `preference_models`
(the trained taste — irreplaceable), `creator_dna`, `clip_outcomes` and the consent records.
`docs/RUNBOOKS.md:648` still reads `measured RTO recorded here: ________`. The *design* is textbook;
it is 0% armed. This is a known owner-blocked gate rather than new signal, but nothing in the audit
outranks it on consequence.

---

## Three things that are right — do not let a future session "improve" them

**1. The absence of a service layer is correct.** The officially maintained FastAPI full-stack
template does exactly the same thing (verified: last commit 2026-08-17T08:38Z, no `services/`), and
this repo's targeted seams are *better* than that template's — `routers/_owned.py` collapses
fetch+ownership into one query returning 404 for both missing and foreign, where the reference
writes that predicate inline in every handler. **Do not add a service layer.**

**2. `db.py::tenant_session(creator_id)` is the best piece of architecture in the repo.** It takes
the tenant id as a *required argument*, so a call site structurally cannot forget the RLS GUC, and
the `AdminSessionLocal` allowlist is pinned by a test. Three domains independently rated the tenancy
model at or above standard.

**3. `worker/tasks.py` at 7,179 lines is not the problem.** The defects came from **envelope
duplication** across ~60 `_async` bodies, not the line count. 39 of 40 tasks pin explicit names,
which makes a split a 1–2 day job whenever you want one — so it is a choice, not a debt. If you do
nothing else here, write the one-paragraph decision saying so, so the next session argues against a
recorded position instead of a vacuum.

Also earning their keep and named in `SYNTHESIS_technical.md` §6: the billing ledger's
SAVEPOINT+UNIQUE idempotency, `tests/test_usage_coverage.py`'s bidirectional AST sweep (the model the
other registries should copy), prompt-injection defense, `tests/test_scoring_goldens.py` replaying
real recorded Anthropic bodies, the eval harness's anti-hollowing guards, and the data-bearing
staging gate.

---

## What was found that nobody was looking for

Beyond the five above, the sweep surfaced a class the audit did not set out to find: **capabilities
that exist, cost money, and reach nobody.** A GDPR data export with three endpoints, a table, a Celery
task, a published Privacy Policy promise — and **zero UI**. A paid YouTube `demographics` report
fetched per creator and refreshed daily, read by nothing, while README, walkthrough, SOT and
COMPLIANCE all state it feeds the DNA (verified: all three named consumers read `AudienceActivity`
only). A hardened, thrice-rate-limited, billed vision endpoint with no caller. A pgvector HNSW index
serving a query that has never existed, with a green integration test attesting to it. These are the
same bug seen from the other end — the system *looks* like it has a capability because there is code,
tests, and a decision entry, and the capability reaches no one.

---

## Documents

| File | What it is |
|---|---|
| `SYNTHESIS_process.md` | **Start here.** The mechanism map (11 classes → the buildable artifact that retires each), the prose→mechanism rule, the CLAUDE.md and DECISIONS.md restructures, and the 90-day plan |
| `SYNTHESIS_technical.md` | Architecture and stack verdicts, 39 ranked technical defects in three tiers with fix-hour estimates, what to delete vs. wire up, module risk ranking |
| `DECISIONS_DRAFTS.md` | 22 decisions drafted in house format. **PROPOSED, NOT ADOPTED** — each needs your Phase-2 approval before it moves to `docs/DECISIONS.md` |
| `DOC_RECONCILIATION.md` | Doc-vs-code divergences with exact replacement text, plus the audit's own citation audit |
| `COMPLETENESS_CRITIC.md` | What this audit missed, where it may be wrong, and what to trust |
| `00-groundtruth/` | The shared factual base: snag taxonomy, process map, architecture map + full DECISIONS index |
| `01-domains/` (12) · `02-sweep/` (6) | The primary reports, ~11,000 lines |
| `_findings_phase*.json` | Machine-readable findings with per-finding verification status |

**Filed work:** `docs/issues.md` Lane **L30**, Issues **#498–#527**.

---

## One correction to this audit's own ground truth

`00-groundtruth/snag-taxonomy.md` originally cited a "9-day silent production outage" as evidence for
the monitoring gap. **That framing is wrong and has been retracted in place.** `docs/GO_LIVE.md:82`
records that the Jul 28→29 ~31h downtime was an **intentional owner poweroff to save cost**, and that
the "gap PROVEN in production" wording was already retracted on 2026-07-31. What remains true:
`health-check.yml`'s schedule silently died on 2026-06-17 and nobody noticed for six weeks. The
monitoring gap is real; the outage that "proved" it was not. Caught by two independent verifiers.
