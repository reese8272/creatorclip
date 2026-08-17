# SYNTHESIS — Process: why the snags keep coming, and what stops them

**Deep Standards Audit, 2026-08-17.** Trunk `main` @ `1def133`. Read-only pass.
**Answers the question the owner actually asked:** *"I notice the numerous times we have hit a baby
snag here and there and it is simply one after the other after the other."*

---

## The diagnosis in one paragraph

The **diagnostic** half of this process is above industry standard. Every `~/.claude/ISSUES_LOG.md`
entry carries a *"Wrong hypotheses (ruled out — don't repeat these)"* section, which almost no
postmortem practice records. The 2026-08-12 clipping audit ran adversarial verifier subagents
chartered to kill each finding, and killed 2 outright while correcting 10. `docs/DECISIONS.md:12884`
retracts one of its own prior diagnoses rather than quietly overwriting it. This audit's own verifier
pass refuted 7 of 86 phase-1 findings and materially corrected 36 more. The **corrective** half is
what is missing: when a root cause is found, it is written down as **prose that must be read and
remembered**, not built as **mechanism that cannot be forgotten**. Four remediations recorded in
`docs/OFF_COURSE_BUGS.md`'s own remediation column — each one correct, each one written by the person
who would later need it — remain unbuilt on this tree, and the class they describe has recurred.
That is the whole of the "baby snag after baby snag" sensation: the snags are not being prevented,
they are being *documented*.

Two facts sharpen it:

- **The controls that exist are placed at the git boundary, not the work boundary.** `core.hooksPath`
  → `.githooks/pre-push` → `scripts/ci_local.sh --fast`, plus 8 required checks on `main` with
  `enforce_admins: true`. That placement is defensible and harness-independent, and it means nothing
  broken ships on a phantom result. The residual is the **in-session window**: a session can spend an
  hour producing, and *reporting*, wrong results before any gate runs. `.claude/` contains one hook
  and it `exit 0`s locally (`.claude/hooks/session-start.sh:12` — `[ "$CLAUDE_CODE_REMOTE" != "true" ]
  && exit 0`); there is no `PreToolUse`, no `PostToolUse`, no `Stop`. That window is exactly where
  `OFF_COURSE_BUGS:156` happened — system `python3` instead of `.venv` produced four phantom test
  failures, a phantom 77-CVE pip-audit and a vacuous `mypy ok 0`, **and those phantoms were filed as
  real defect reports before being retracted.**

- **The project's own hardening primitive cannot catch how its gates actually fail.** `--require`
  (Issue 479) escalates a gate whose status is `skipped`. But `run_layer0.py`'s mypy, bandit and
  pip-audit gates infer their result purely from stdout and never inspect `proc.returncode`, so a
  tool that runs and *fails* scores `{"status":"ok","value":0}` — a perfect result against the strict
  baseline of `0`. `--require` cannot see an `ok`. This is developed in §6; it is the single most
  important gate finding in the audit.

**Confidence key used throughout.** `CONFIRMED` / `CORRECTED` = adversarially verified; the corrected
statement is used, never the original claim. `[unverified]` = filed but never adversarially checked —
usable, lower confidence. `[verified here]` = something I checked myself during this synthesis.

---

## 1. The mechanism map — the core deliverable

Eleven recurring failure classes from `00-groundtruth/snag-taxonomy.md` §A. "Times it has bitten" is
the count of `docs/OFF_COURSE_BUGS.md` rows in that class (138 current rows + 24 archived). **Value
rank = expected reduction in future snags per maintainer-hour**, not raw severity — that is the metric
that matters to one person.

| # | Class | Times it has bitten | Currently prevented by | THE MECHANISM that makes it impossible | Effort | Value rank |
|---|---|---|---|---|---|---|
| 2 | **Test-infra defects & flakes** — the failing identity is never captured | ~35 rows (25%, largest) | Nothing. Prose advice inside the rows themselves, twice | `frontend/package.json`: `"test:ci": "vitest run --reporter=verbose --reporter=json --outputFile=.vitest-report.json"`, called from `ci.yml:456`. `scripts/ci_local.sh`: generate `SEED=$RANDOM`, pass `--randomly-seed=$SEED`, echo it in the summary, and `cat .pytest_cache/v/cache/lastfailed` on failure | **30 min** | **1** |
| 1 | **Vacuous green signal** — a check reports success without exercising what it names | ~26 rows (19%). The project's own named #1 | Deliberate audits only (by construction: no gate can catch a gate) | Three parts: (a) `run_layer0.py` gates assert `proc.returncode` — and bandit's JSON `errors[]` — returning `fail`, never `ok 0`; (b) a `tests/test_ci_config.py` assertion that no load-bearing workflow step uses `\|\| true` or an unguarded pipe outside a named allowlist; (c) generalise the eval harness's anti-hollowing pattern — every source-scanning gate ships a deliberately-bad fixture proving it *fires* | 5–6 h | **2** |
| 4 | **Config / environment drift** | ~18 rows. `config.py` (99 commits) and `.env.example` (87) are the #2/#3 most-churned code files | `scripts/doctor.py` at deploy time (22 of ~202 settings); one manual `CLAUDE.md` checklist item | (a) 3 lines at the top of `run_layer0.py` and `tests/conftest.py`: refuse to run when `sys.prefix` is not the repo `.venv`; a `PreToolUse` hook on `Bash` blocking bare `python3`/`pytest`/`mypy`/`pip-audit`; fix `CLAUDE.md:120`. (b) `tests/test_env_example_parity.py`: iterate `Settings.model_fields` against parsed `.env.example` keys. (c) `frontend/package.json` `"engines": {"node": ">=22 <23"}` + `frontend/.npmrc` `engine-strict=true` so npm **refuses to install** | 2 h | **3** |
| 9 | **Deploy / ops / monitoring blind spots** | ~8 rows, incl. prod down silently for days and a rollback that couldn't roll back | Cloudflare Health Check on `/health` (hand-configured, no config-as-code); deploy smoke = 4 GETs | (a) One alert rule — **the repo contains zero** — on deploy-run failure and on `/health` non-`ok`. (b) `alembic current == heads` in the **prod** job (staging and `scripts/deploy.sh` both have it; the prod job does not) plus a `test_ci_config.py` assertion covering **both** jobs — the existing meta-test is a whole-file substring check and structurally cannot see the gap. (c) healthcheck + `autoheal` label on `beat`, and one required `celery -A worker.celery_app inspect ping` smoke step. (d) `--full` on the deploy preflight | 6 h | **4** |
| 6 | **Doc drift, including docs-as-source-of-truth failure** | ~12 rows | Nothing | `tests/test_doc_citations.py`: regex every `` `path.ext` ``, `` `path.ext:N` `` and `NAME = value` claim in `CLAUDE.md` + `docs/*.md` (excluding `docs/archive/`); assert the path resolves, the file has ≥ N lines, **and the named symbol is defined in the cited file**. The symbol half is load-bearing — see §3 | 1.5 h | **5** |
| 3 | **Third-party SDK / API / platform surprise** | ~20 rows + 6 of 8 `ISSUES_LOG` entries. The most *expensive* class | One seam guarded, retrofitted after the incident (`tests/test_data_api.py`'s `fields=` projection simulator) | Extend the pattern the repo already invented to the unguarded seams: one recorded Stripe `checkout.Session` and one recorded R2 presign response, replayed **through the SDK's own model classes** the way `tests/test_scoring_goldens.py` does for Anthropic (sha256-pinned schema so a stale golden cannot green-stamp a changed contract). Plus `stripe.WebhookEndpoint.list()` → assert each `urlparse(url).path` resolves in `main.app.routes`, and the same for `OAUTH_REDIRECT_URI` | 6 h | **6** |
| 7 | **Tenancy / RLS backstop gaps** | ~10 rows — **five SEV1/SEV2 on one fault line in one day** | `tests/test_rls_isolation_integration.py`, correctly running as the non-bypass role — but driven by two hand-maintained literals | Replace `_TENANT_TABLES` / `_CHILD_TABLES` with a runtime query: enumerate `pg_policies` + `pg_class.relrowsecurity` and assert the sweep covers **every** policied table, and that every model carrying a `creator_id` column either has a policy or is on an explicit exemption list. Runs in the existing integration lane | 2 h | **7** |
| 8 | **Money-path leaks** | ~8 rows, incl. a 10-week total billing outage | `tests/test_usage_coverage.py` — a genuine repo-wide AST sweep with a bidirectional staleness check. **This is the model** | Apply that same shape to the three sibling registries still frozen as literals: `_LLM_ROUTES` (`tests/test_creator_quota.py:30`), `_BILLED_LLM_ROUTES` (`tests/test_flags.py:410`), `_LLM_RENDER_ROUTERS` (`tests/test_security_baselines.py:304`) — derive each from the live route table instead of listing it | 3 h | **8** |
| 11 | **Decision reversal / re-litigation** | ~15 explicit reversals; 101 reversal-language hits in `DECISIONS.md` | Nothing. Supersession is encoded in heading parentheticals | A `Status:` field per decision from `{Proposed, Accepted, Deprecated, Superseded, Rejected}` + a `Superseded-by` link, and one test asserting every ADR carries an allowed status and every `Superseded-by` resolves. Migration path in §4 | 2 h + incremental | **9** |
| 10 | **Clip-engine domain / algorithmic correctness** | 27 issues filed in one day (5 SEV1); none caught by any gate | The 32-scenario eval harness with `SCENARIO_FLOOR` + skip-marker scan — genuinely good, and it does not cover the LLM scorer | The nightly behavioural lane exists but reports **green with zero tests executed** when a secret is empty (`bool("")` is `False` → every test `skipif`-skips → pytest exits 0). Mirror the pattern `ci.yml:140` already uses for `render_env`: assert an *executed* count, e.g. `test "$(pytest -m llm_live --collect-only -q \| grep -c '::')" -ge N` plus an explicit `skipped == 0` assertion, and fail loudly on an empty-string secret | 2 h | **10** |
| 5 | **Honesty / UX inversion — the app tells the creator the opposite of the truth** | ~15 rows | Effectively nothing. The canonical row states it plainly: *"No gate could have caught this"* — the design token was valid, just wrong | Be honest: this class is the least mechanizable, and a mechanism that pretends otherwise would itself be a Class-1 defect. What is real: (a) promote `Frontend (lint, test, build)` to required so the 92 vitest files gate *at all* — today a test asserting the error branch renders in an error token could go red and the PR would still merge; (b) an **advisory** `claude-code-action` PR job scoped to the two questions gates cannot answer — *"does any assertion in this diff pass vacuously?"* and *"does any user-facing string's styling contradict its content?"*. Keep it non-blocking: an LLM reviewer as a *required* check would create a new gate that can pass by not looking | 3 h | **11** |

**Total: roughly 34 maintainer-hours to structurally retire the eleven classes.** Rows 2, 4 and 6 —
about four hours between them — cover the three classes that generate the most *sessions lost per
incident*, which is the felt cost the owner is describing.

### The seven worked mechanisms, as buildable artifacts

| Mechanism | Lives at | Replaces | Effort |
|---|---|---|---|
| **1. Node version pin** | `frontend/package.json` `engines` + `frontend/.npmrc` `engine-strict=true` + a CI step asserting `node -v` major == `.nvmrc` | Prose in `LEFT_OFF.md`. The row's own status column reads **"engines pin open"** | 15 min |
| **2. The venv refusal** | `run_layer0.py` + `tests/conftest.py` self-guard; `PreToolUse` hook on `Bash` | `CLAUDE.md:120`, which currently instructs the **wrong** interpreter — `grep -n venv CLAUDE.md` returns nothing `[verified here]` | 30 min |
| **3. Flake identity capture** | `frontend/package.json` `test:ci` with `--reporter=verbose`; `ci_local.sh` seed echo + `lastfailed` | Advice written into the rows themselves, twice, and followed zero times | **30 min** |
| **4. Citation resolution** | `tests/test_doc_citations.py` over `CLAUDE.md` + `docs/*.md` | Nothing | 1.5 h |
| **5. Scan, don't list** | `pg_policies` query for RLS; live route-table walk for the quota/spend registries; call-graph derivation for `RENDER_TASKS` | ~20 hand-maintained gate-scope literals, of which modality B measured **11 drifted** | 5 h total |
| **6. Exit codes cannot be swallowed** | `tests/test_ci_config.py` assertion over `.github/workflows/*` for `\|\| true` and unguarded pipes on load-bearing steps; `returncode`/`errors[]` checks in `run_layer0.py` | `--require`, which cannot see this failure mode at all | 3 h |
| **7. External registration reconciliation** | `scripts/doctor.py`: `stripe.WebhookEndpoint.list()` and `OAUTH_REDIRECT_URI` → `main.app.routes` | Nothing. **Nine externally-registered values; zero have any automated reconciliation** `[unverified inventory, modality E]` | 2 h |

**On mechanism 5 — the numbers, corrected.** Modality B censused 101 module-level literals in
`tests/` + `scripts/` and diffed the ~20 that define a *gate's scope* against a live scan: **eleven
had drifted.** Four of those were adversarially verified and all four survived with narrowed harm:

- **B1 `CORRECTED`** — `_LLM_ROUTES` lists 9; the live table has 17 routes behind
  `require_flag('llm_generation')`. Four violate the invariant the module docstring asserts:
  `chat.post_message` and `chat.regenerate` have a daily cap and **no burst limit**;
  `creators.identity_chat` has 40/hour and **no daily cap**; `creators.build_dna` has a **120/minute
  burst and no daily cap** on a route that enqueues a Sonnet DNA build. *Scope correction:* this is
  defence-in-depth, **not an open spend hole** — all four carry `Depends(require_budget)` and are
  re-checked task-side against `SPEND_CAP_CREATOR_DAILY_USD = 5.00`. The residual harm is Celery/CPU
  flooding, plus the fact that `CLAUDE.md`'s ✅ for Issue 228 rests on a test that can no longer
  detect a new uncapped LLM route.
- **B2 `CORRECTED`** — two undeclared Tailwind utilities (`bg-surface-raised`, `bg-accent-subtle`)
  are live on `main` and emit **zero CSS**, outside the gate's 17-name denylist. Live, cosmetic.
- **B3 `CORRECTED`** and **B4 `CORRECTED`** — 8 policied tenant tables and 7 LLM routes outside their
  respective literals. Both **latent**: no live defect today, but the construction cannot detect the
  next one. B3's verifier adds the sharper point — nothing in the repo reconciles *any* of these
  literals against `pg_policies`, so the sweep cannot detect a tenant table shipped with **no policy
  at all**, which is the exact `improvement_briefs`/`creator_insights` leak of migration 0038.

So: **two live, two latent, among the four verified.** The remaining seven drifts are `[unverified]`.

---

## 2. The general rule

Short enough to paste into `CLAUDE.md`:

> ### Prose → Mechanism
>
> Convert written knowledge into an enforced mechanism when **any** of these is true. The trigger is
> recurrence or timing, **never severity**.
>
> 1. **It has been logged twice.** Second occurrence of a *class* is the trigger — not third, not
>    "if it recurs again".
> 2. **The knowledge must be applied BEFORE the failure is observable.** Any advice of the form
>    *"next time, pass `--flag` on the first run"* is structurally unfixable by documentation: you
>    only learn you needed it after it is too late to add it. This class must become a **default**.
> 3. **The prose is a machine input.** If any script, gate or config mirrors a list that lives in a
>    doc, that doc is a schema. Add a parity test — or better, delete the list and **scan**.
> 4. **A script can check it in under a second.** Then it is a test, not a checklist item.
>
> **Corollary (hierarchy of controls).** Any remediation ending in *"remember to…"*, *"consider…"*
> or *"if it recurs…"* is an administrative control and will fail. `grep -i "remember to\|consider\|if
> it recurs" docs/OFF_COURSE_BUGS.md` — every hit is a conversion candidate.
>
> **Gate corollary.** A check that cannot fail is not a gate. Every gate must be able to prove it did
> work: assert a non-zero count of things actually checked, and never let "could not run" share an
> exit code with "ran and passed."

The grounding is not aesthetic. Occupational safety's **hierarchy of controls** — elimination >
substitution > engineering > administrative > PPE — is the framing SRE postmortem practice borrows to
judge action-item quality, and *administrative* controls (checklists, runbooks, "remember to") sit
second from the bottom precisely because they require a human to recall them at the moment of
exposure. Documentation-based action items are the least effective class at preventing recurrence.
Every one of this project's recurring gotchas was closed with an administrative control, and every
one recurred.

---

## 3. Is CLAUDE.md working?

**Partly — and the honest answer is narrower than "it is bloated."** At 298 lines `[verified here]`
`CLAUDE.md` is *not* oversized, and shrinking it for size would be the wrong move. Three specific
things are wrong.

**(a) The Read Order is unsatisfiable, so it is not the process.** `CLAUDE.md:31-42` mandates six
files *"before writing a single line of code"*, every session. Measured `[verified here]`:

```
docs/SOT.md                    705
docs/PROJECT_STATE.md        4,440
docs/issues.md               4,550
docs/DECISIONS.md           13,018
docs/COMPLIANCE.md             431
docs/CLIPPING_PRINCIPLES.md     42
                        = 23,186 lines
```

The audit brief concedes nobody reads `DECISIONS.md` whole; this audit's own instructions say *"never
read it whole."* The **actual** working method is targeted grep — documented by the project itself in
`docs/AUDIT_BRIEF.md` §4 — plus `LEFT_OFF.md` as a compressed handoff. Anthropic's own guidance points
at the same resolution: *"For domain knowledge or workflows that are only relevant sometimes, use
skills instead."* So the fix is to **replace "read these files in order" with "grep these files by
topic; here is the index"** — not to shrink the file.

*Do not over-read this.* The claim that an unsatisfiable mandate degrades the file's authority and
thereby caused the other errors was **REFUTED** in verification. The stale facts below have their own,
simpler cause: no test resolves a citation.

**(b) One instruction is wrong in a load-bearing way.** `CLAUDE.md:120`, inside the Phase-4 gate every
issue must clear:

```
- [ ] `python3 .claude/skills/production-assessment/scripts/run_layer0.py` passes
```

`grep -n "venv" CLAUDE.md` returns **nothing** `[verified here]`. The `.venv` rule lives only in
`LEFT_OFF.md:199` — a file the Read Order does not list — where it reads *"This has now burned two
consecutive sessions."* The Phase-4 checklist is the single most-followed instruction in the file, and
it names the interpreter that produces vacuous `mypy ok 0` and phantom CVEs. Severity is **MEDIUM**,
not high: `.githooks/pre-push` prefixes `PATH` with `.venv/bin` and branch protection means nothing
ships on a phantom result. The realized cost is retracted defect reports and ~2 sessions. Fix: one
word, plus the self-guard from mechanism 2.

**(c) Three stale facts, and the one that matters.** `CLAUDE.md:51-53`'s module list omits `billing/`,
`chat/`, `analysis/`, `notify/`. `CLAUDE.md:236` cites `WINDOW_S = 75.0, clip_engine/window.py`.

I checked this myself, and it is more interesting than the phase-1 report claimed. **`clip_engine/window.py`
does exist** `[verified here]` — contrary to d12's assertion that it does not — but `WINDOW_S` is at
`clip_engine/candidates.py:22`, and `window.py` does not contain it. **A citation test that only checks
"does the path exist" would pass on this citation.** The mechanism must resolve the *symbol*. That is
exactly what `OFF_COURSE_BUGS.md:158` already proposes verbatim, and what Issue 497's structural-guard
acceptance criterion should be widened to.

Two claims must **not** be repeated: the Project Structure list does *not* mirror
`run_layer0.py::_CANDIDATE_SOURCES` (that list already includes `billing`, which `CLAUDE.md` omits), so
the "stale prose seeded Issue 497" causal story is refuted; and `:55`'s `static/` claim is not dead —
`static/` is live (tos, privacy, accessibility, landing, tokens, fonts). The line is merely incomplete
for omitting `frontend/`.

### The restructure

Current practice for agent instruction files, from Anthropic's Claude Code guidance: *"Keep it
concise. For each line ask: would removing this cause Claude to make mistakes? If not, cut it. Bloated
CLAUDE.md files cause Claude to ignore your actual instructions."* · *"Use hooks for actions that must
happen every time with zero exceptions… unlike CLAUDE.md instructions which are advisory, hooks are
deterministic."* The 2026 community consensus collapses to one line: **CLAUDE.md for context, skills
for procedures, hooks for automation — skills can be skipped, hooks block.**

| Stays in `CLAUDE.md` | Becomes a **skill** (loaded on demand) | Becomes a **hook** (deterministic) | Becomes a **test** (cannot regress) |
|---|---|---|---|
| The One Rule (research current standard first) | `/decisions <topic>` — greps `DECISIONS.md`, returns matching entries + status | `PreToolUse` on `Bash`: reject bare `python3`/`pytest`/`mypy`/`pip-audit` outside `.venv/bin` | `.env.example` ↔ `config.py` parity |
| North Star + the Honesty Constraint | `/state` — current lane, next free issue number, in-flight issue | `PostToolUse` on `Edit\|Write` matching `config.py` → run the parity check | Doc citation resolution (`tests/test_doc_citations.py`) |
| The security invariants (tenancy, tokens, no PII in logs) | `/compliance` — ToS/retention/scopes on demand | *(optional)* `PreToolUse` on `Bash`: reject `git push --no-verify` absent an explicit env override | The no-virality structural test **(already exists)** |
| The exact commands, with the **right** interpreter | `/clipping-principles` | | Setup-start clip geometry eval **(already exists)** |
| The four-phase loop, restated to match practice (§7) | `production-assessment` (unchanged) | | `_sources()` covers every top-level package (Issue 497's own load-bearing AC, still unbuilt) |
| The DO-NOT list | `best-practices` (unchanged) | | Required-context list pinned against `ci.yml` job names |
| **"Grep these files by topic"** replacing the Read Order | | | |

**Size target: keep `CLAUDE.md` at 250–300 lines.** The target is not fewer lines — it is **zero
unverifiable claims and zero unsatisfiable mandates**. Every factual assertion in the file should be
covered by the citation test; anything the test cannot cover should be a link, not a fact.

**Highest-leverage unclaimed capability in the repo: the `PreToolUse` hook.** A few lines, and it
closes the one window the git-boundary controls structurally cannot reach.

---

## 4. The DECISIONS.md problem

**`CONFIRMED`** — the strongest verdict any process finding in this audit received.

Measured `[verified here]`: **13,018 lines · 259 `##` entries · 0 `Status:` fields · 24 `supersed*`
mentions** against 15+ known reversals and 101 reversal-language hits. Current ADR practice (MADR and
the 2026 practitioner guides) converges on three properties: one file per decision, numbered and never
renumbered; **never edited after acceptance** — to change a decision you write a *new* one that
supersedes it; and a `Status` from `{Proposed, Accepted, Deprecated, Superseded, Rejected}` where
superseded entries read `Superseded by ADR-NNNN` so the chain is traversable forward. `DECISIONS.md`
fails all three.

**The failure is already realized.** The clearest chain is the Sonnet prompt-cache floor: `:5412`
corrects 1024 → 2048; `:2837` (Issue 315) reverses back to 1024 and declares it *"supersedes ALL 2048
refs"*; and **unmarked 2048 claims still sit at `:4394`, `:6000` and `:6584`** for any topic-grep to
land on. Four states for one number, because there was no place to record a status on the entry
itself. The deployment trail is a subtler instance of the same defect: `:2541`/`:2563` choose Render as
the beta host and were **accepted but never executed** — `:2395` records that the live app does not run
on Render without rescinding the decision, and `render.yaml` (219 lines, carrying
`VERBOSE_LOGGING_ALLOW_PROD=true`) still sits at the repo root looking live. A status-less format cannot
express *"accepted, never implemented"* any more than it can express *"superseded"*.

### Migration path — loses nothing, does not need a weekend

1. **Freeze, don't move.** Add a header to `docs/DECISIONS.md`: *"FROZEN 2026-08-17 — historical
   record, no new entries. Index: `docs/decisions/INDEX.md`."* Do not renumber, reorder or delete a
   single line, so every existing `DECISIONS.md:NNNN` citation across `issues.md` and
   `PROJECT_STATE.md` keeps resolving. **(10 min)**
2. **The index already exists.** `00-groundtruth/architecture-map.md` §A is a categorised 259-row table
   of `line — date — heading`. Commit it as `docs/decisions/INDEX.md` and add two columns: **Status**
   and **Superseded-by**. This is a copy-paste, not a rewrite. **(20 min)**
3. **Mark the known-dead trails today.** `:5412` → `SUPERSEDED by :2837`. `:2541`/`:2563` →
   `ACCEPTED — NEVER IMPLEMENTED (see :2395)`. `:223` → `SUPERSEDED by :277`. `:12884` →
   `SUPERSEDES the prior-day diagnosis`. Five edits close the five most confusing trails in the file.
   **(30 min)**
4. **New decisions only** go to `docs/decisions/NNNN-slug.md` in MADR-lite form:
   `Status / Date / Context / Decision / Consequences / Supersedes / Superseded-by / Sources`. Never
   edit an accepted one — supersede it. **(the rule costs nothing; each new ADR costs the same as an
   entry does today)**
5. **Back-port ~22 files, not 259** — the load-bearing bets catalogued in `architecture-map.md` §C.
   Everything else stays an index row pointing into the frozen file. **(2 h, and it is interruptible)**
6. **One test.** Every ADR carries a `Status` in the allowed set; every `Superseded-by` resolves to an
   existing ADR; the index has a row per file. **(30 min)**

**~4 hours total, and only step 6 must be done in one sitting.** Nothing is deleted, nothing is
renumbered, and every existing line citation keeps working from day one.

---

## 5. Closing pressure for OFF_COURSE_BUGS.md

**`CORRECTED` — and the correction changes the fix.** The raw numbers are 138 rows, 52 marked
`📋 Open`, 10 ever promoted to `docs/issues.md`. But the verifier established that **the Open marker
is unreliable in both directions**: `:70` (cached-token under-billing, money path) is still marked
*"📋 Open — awaiting approval"* and was in fact **fixed** — `billing/ledger.py:148-153` prices cache
reads and cache creations, with a docstring at `:139` citing that exact row. So *"52 open"* cannot be
used as a backlog signal at all.

**The defect is status-column staleness, not backlog depth.** That is a better problem to have, and it
has a cheaper fix — but it also means the felt experience ("the snags are accumulating") is partly an
artifact of a file that only ever grows a column nobody reconciles.

Two rows *are* genuinely open and both are already disclosed in `docs/AUDIT_KNOWN_ISSUES.md` §B:
`:26` (`BACKUP_R2_BUCKET` unset — every prod migration to date has run with no safety dump; tracked as
Issue 256) and `:42` (Playwright/visual jobs fail-fast so 0 tests run — though the *reason* recorded in
that row is stale, since Issue 360 moved PR CI to `ubuntu-latest` and both now pass there). They are
unscheduled, not lost.

### The smallest mechanism — and the project already invented it

`tests/test_clip_engine.py` carries `SCENARIO_FLOOR`: a ratcheted count with its ratchet history in a
comment above the constant, pinned a second time in `tests/test_eval_transparency.py` so lowering it
must touch two files and is visible in any diff. That is a working closing-pressure device sitting in
this repo. Apply the same shape:

```python
# tests/test_off_course_budget.py   (~30 lines, runs in the existing unit lane)

# Ratchet DOWN only. History below; never raise this number without a line here.
#   2026-08-17: 52 (initial — set to today's count so it never blocks work retroactively)
OPEN_ROW_CAP = 52

def test_open_off_course_rows_within_budget():
    """Every session that adds a row must close one. 'Open' is not a resting state."""

def test_open_rows_carry_an_issue_number_or_a_closed_date():
    """Forces the status column to be reconciled rather than merely appended to.
       This is the assertion that catches :70 — fixed in code, still marked Open."""
```

Three properties make this the right size:

- **It does not derail work in flight.** The cap starts at today's number, so nothing goes red today.
- **Closing costs nothing when the answer is "not worth fixing."** Under a zero-bug policy — *"if a
  bug is worth fixing, fix it now; if it's not worth fixing, close it"* — deleting a row with a
  one-line reason is a legal close. The point is not zero bugs; the point is that **"open" is not a
  valid resting state**.
- **The second test is the one that matters**, because it attacks the verified defect (stale status)
  rather than the apparent one (depth).

**Do not** add a dashboard, a cron, or a triage ceremony. Thirty lines in the lane that already runs.

---

## 6. The gate changes, ranked

### The finding that outranks everything else: `--require` cannot catch how the gates actually fail

**A1 · `CORRECTED` · severity MEDIUM (down from HIGH), but highest structural importance.**

`run_layer0.py` infers each static gate's result **purely from stdout** and never inspects
`proc.returncode`:

```python
# :171  mypy — no returncode check, no unparseable branch at all
errors = sum(1 for ln in proc.stdout.splitlines() if ": error:" in ln)
return {"status": "ok", "value": errors, "metric": "mypy_errors", "compare": "max"}

# :161 / :229 / :276  ruff / bandit / pip-audit
issues  = len(json.loads(proc.stdout or "[]"))                  # empty stdout -> 0 issues
results = json.loads(proc.stdout or "{}").get("results", [])    #              -> 0 findings
data    = json.loads(proc.stdout or "{}")                       #              -> 0 vulns
```

A tool that runs and *fails* writes diagnostics to **stderr** and leaves stdout empty. That path yields
`{"status":"ok","value":0}` — the best possible score — which passes the strict `baselines.json` floor
of `0`. And `--require` (the project's own Issue-479 hardening primitive, built for exactly this class)
only escalates a gate whose status is **`skipped`**. A gate reporting `ok` is invisible to it.

Verified end-to-end by the verifier: `--gates mypy --require mypy` prints `mypy ok 0` and
`All runnable gates passed`, exit 0, **while mypy exited 2 having checked nothing.**

Two corrections to the original claim, both load-bearing:

1. **Three gates, not four.** `ruff` is independently guarded by the separate required `lint` job
   (`ci.yml:58-70`, bare `ruff check .` + `ruff format --check .`) and by `ci_local.sh:69-75`. Only
   **mypy, bandit and pip-audit** are genuinely unguarded.
2. **Bandit fails differently.** Its realistic failure (missing target dir) exits **0** and emits JSON
   with a populated `"errors"` array and empty `"results"` — so bandit is hollow via the **ignored
   `errors[]` list**, not the ignored returncode. A returncode-only fix would miss it.

The most plausible real-world trigger: a transient PyPI/OSV advisory-database outage turns the
dependency-vulnerability gate from red into a confident green **"0 vulnerabilities"** on a required
check.

**The fix, in order:** (a) every gate asserts its subprocess exited as expected **and** that bandit's
`errors[]` is empty, returning `fail` — never `ok`, never `skipped` — on an unexpected exit; (b) *then*
add `--require ruff,mypy,bandit,pip_audit` to the `static-gates` invocation and to `ci_local.sh:95`;
(c) a `test_ci_config.py` assertion that every `run_layer0.py` invocation in every workflow carries
`--require`. **`--require` is necessary and, as shown, not sufficient.** Effort: 2 h.

**Companion defect — A3 · `CORRECTED` · MEDIUM.** `gate_module_coverage` fails **open** on measurement
drift: when `_module_line_rate` returns `None` the loop `continue`s without recording a failure, so an
all-`None` result evaluates to `"ok"` and `--require module_coverage` is satisfied. Verified: a
`_coverage.xml` containing none of the floored modules yields
`rates {clip_engine: None, …}, failures [], status ok`, exit 0. *Important correction:* it is **not
vacuous today** — under the current single-root `--cov .`, coverage.py is source-based and emits
entries for files no test imported, so all five modules always resolve (verified against a real
coverage.xml from one trivial test file: all five floors correctly went red). The reachable mode is
**drift** — reverting to a multi-root `--cov` (the exact Issue-368 cause, pinned by no test), renaming
a floored package, or adding one to `[tool.coverage.run] omit`. Also, its anti-hollowing test
(`tests/test_layer0_module_coverage.py:86-95`,
`test_every_floored_module_is_resolvable_in_principle`) never parses a coverage report — it guards the
symptom Issue 368 already removed, not the mechanism. **Fix: `rate is None` must append to `failures`,
exactly as `rate < floor` does.** 30 min.

### Promote to required — three checks, **zero added PR latency**

Measured across four consecutive real CI runs. All twelve jobs start within 3 seconds of each other and
run in parallel; the critical path is `Integration tests` at 3m34s–4m29s.

| Job | Duration | Finishes before the gate closes by | Call |
|---|---|---|---|
| **Migration lint (Squawk)** | 20–25 s | ~3.5 min | **Require now, unconditionally** |
| **Visual regression** | 43–57 s | ~3 min | **Require now** |
| **Frontend (lint, test, build)** | 1m47–1m49s | ~2 min | **Require — after the vitest reporter fix** |

All three run unconditionally on every PR (no job-level `if:`, no `continue-on-error`), always report a
real conclusion, and were green on every sampled run. Promotion is one `gh api` call.

Corrections that must travel with this recommendation:

- **`Visual regression` is the sharpest case, not `Frontend`.** `ci.yml:605-609` states in a code
  comment *"GATING since 2026-07-29"* while branch protection has **never** required it. That is a
  live contradiction between two documents that both claim to describe the gate. Promote it or delete
  the comment; the current state is worse than either.
- **Do not credit Squawk with the silent-no-op-migration incident.** That was an `alembic/env.py`
  non-committing-transaction bug that neither Squawk nor the schema round-trip would catch. Argue
  `migration-lint` on its own merits: unsafe DDL locks and irreversible downgrades.
- **`npm run build` is already gated transitively** — `Dockerfile:68-71` runs `npm ci` + `npm run
  build` and *"Docker build (smoke test)"* **is** required. The genuinely unguarded surface is
  **eslint + the 92 vitest files**.
- **Do the flake fix first.** `frontend/package.json:10` is a bare `"test": "vitest run"`. Add
  `"test:ci": "vitest run --reporter=verbose"` and call it from `ci.yml:456` before promoting, so the
  fourth recurrence of the cold-run flake is self-documenting.
- Mitigating context: all three already show red in the PR checks UI and the maintainer reviews every
  PR. This is defence-in-depth against a tired merge, not an unguarded path.

### Add

| Change | Why | Effort |
|---|---|---|
| `returncode` + `errors[]` checks in `run_layer0.py`, then `--require` everywhere, then a `test_ci_config.py` pin | The A1 fix, in the only order that works | 2 h |
| `rate is None` → `failures` in `gate_module_coverage` | A3 | 30 min |
| A test pinning the required-context list against `ci.yml` job names **and** against any job comment containing `GATING` | Nothing machine-checks which checks are required `[unverified]` | 1 h |
| `alembic current == heads` in the **prod** deploy job + a meta-test covering **both** jobs | Staging and `scripts/deploy.sh` have it; prod does not. The existing meta-test is a whole-file substring check that passes on the staging strings and structurally cannot see the gap `CORRECTED, MEDIUM` | 45 min |
| `--full` on the deploy preflight + treat `SKIP`/"cannot verify" as `FAIL` for a dependency the config declares active | The preflight contacts **zero** third-party providers. *Corrected scope:* it is not vacuous — 8 presence/format sections plus live Postgres and Redis do run, and **R2 is live-probed every deploy** via `/health`'s `head_bucket` under the smoke gate. The real gap is **4 providers** (Anthropic, Voyage, Deepgram, Stripe), and specifically a *well-formed but invalid* credential `CORRECTED, MEDIUM` | 30 min |
| Nightly live lanes assert an **executed** count > 0 and fail loudly on an empty-string secret | `${{ secrets.X }}` expands to `""` on an unset secret; `bool("")` is `False`; every test skips; pytest exits 0. Both meta-tests written to prevent this pin **YAML strings**, not execution `CORRECTED` | 1.5 h |
| `mutmut`: add `shared_resources.py` + the whole first-party import closure to `also_copy`, and assert `checked > 0` | The gate has **never executed a single mutant** while reporting `success` on all eight sampled weekly runs. *Corrections:* the first missing module is `shared_resources.py`, not `flags.py` (the sandbox import chain dies at `conftest.py:37 → main.py:24 → event_log.py:38`), and `\|\| true` is a **deliberate documented choice** (`DECISIONS.md:2688-2695`) — keep it, and add the count assertion instead `CORRECTED, MEDIUM` | 1.5 h |
| Healthcheck + `autoheal` label on `beat`; one required `celery inspect ping` smoke step | `beat` has **no healthcheck and no autoheal label**, so nothing can distinguish a running beat from a dead one — and beat drives `purge_stale_youtube_analytics` (YouTube ToS §III.E.4.b) and `purge_stale_event_logs` (GDPR Art. 5(1)(e)). This is a compliance-drift path, not just a throughput path `CORRECTED, MEDIUM` | 1.5 h |
| One alert rule on deploy-run failure and `/health` non-`ok` | `grep alert` across `docs/dashboards/` returns **zero hits** — there are no alert rules anywhere in the repo | 1 h |
| `.github/dependabot.yml`, weekly **grouped** for pip + npm + actions | `pip-audit` is required at a zero baseline with no bot PR waiting; grouped mode is what makes it tolerable solo `[unverified, LOW]` | 10 min |

### Delete / do not do

- **Do NOT merge `Unit` and `Coverage floor`.** **REFUTED.** The repo is **public**, so Actions minutes
  are free and unmetered; `Coverage` finished *first* in two of three sampled runs so it is never on
  the critical path; the jobs are not duplicates (`Unit` additionally runs a `--collect-only` import
  smoke and the `render_env` lane with real ffmpeg + mediapipe); and consolidating **reintroduces the
  exact Issue-479 shared-artifact defect** that made `module_coverage` and `diff_cover` silent no-ops
  from 2026-06-23 to 2026-08-12.
- **Do NOT harden `ci_local.sh` into a fail-closed gate.** **REFUTED.** Layer 1 is non-authoritative by
  design; the contract is stated at `ci_local.sh:24` (*"skips are OK"*) with the rationale at `:9-14`;
  failing closed was tried and forced `--no-verify` on every push; and with 8 required contexts and
  `enforce_admins: true`, a vacuously-green pre-push hook cannot put one unverified line into `main`.
  *(The `--require` addition at `:95` still belongs there — but as part of the A1 fix, not as a
  fail-closed redesign.)*
- **Do NOT add a pre-commit hook.** Pre-push is the right boundary. This maintainer commits very
  frequently (967 commits in 82 days `[verified here]`); a pre-commit hook would make `--no-verify`
  muscle memory, which would also bypass the pre-push gate.
- **Do NOT add a required review, a merge queue, a deploy-approval gate, or a canary.** GitHub's own
  "prevent self-review" semantics say a lone approver is not a control; `strict: true` already buys the
  only property a merge queue provides; an approval button would have prevented none of the nine
  incidents in the corpus, while an alert would have caught at least three.
- **Keep `mutation` non-required and `Flake detection` on `continue-on-error`.** Both are correctly
  reasoned as-is.
- **Actually delete:** the 34 Issue-226 skips in `tests/test_static.py` and 8 skips across the
  issue-numbered files — **a few hundred lines, not 4,400** `CORRECTED`. **Rename, do not delete**, the
  eight `tests/test_issue_*.py` files: they hold **77 live tests** named for *when* they were written
  rather than *what* they protect. Highest-value item in this group: re-mark the two
  `"needs real Postgres"` skips (`tests/test_notifications.py:636`,
  `tests/test_notifications_triggers.py:652`) as `@pytest.mark.integration` so they actually run in a
  lane that already exists on every PR.

---

## 7. Is the four-phase issue loop right?

**Yes. It is not the churn source, and it should not be blamed for it.**

**Phase 1 CHECK is the single best asset in the process** and should not change. It is why
`DECISIONS.md` entries carry live sources and ruled-out alternatives — above the norm for a funded
team, let alone a solo one.

**Phase 2 APPROVE: the per-issue-ceremony finding was REFUTED. Do not resurrect it.** The verifier
established that practice has already moved to wave-scope batch approval since 2026-07-02
(`DECISIONS.md:12227`, `:12354`, `:12409`, each recording a multi-agent Phase-1 CHECK followed by a
single "good to go" over 5–6 issues) — and **the practice is better than the written rule.** The only
action is one line of doc drift: `CLAUDE.md:109-110` still describes Phase 2 as per-issue explicit
confirmation. Update the written process to match. The statistics attached to that finding were struck
in verification and must not be reused: *"~6 issues/day"* counts allocated issue numbers, not
approvals (`docs/issues.md` holds 113 briefs); the commit ratio is **228 `fix:` / 216 `feat:` over 967
commits** `[verified here]`, not 225/207; and the 16%/9%/41% stage-attribution figures could not be
reproduced from this repo.

**Phase 4 is the problem.** `CLAUDE.md:117-161` is a ~30-item checklist. The corrected picture is more
favourable than the original claim: several rows *are* backed by machinery elsewhere — the no-virality
structural test, the 32-scenario clip-geometry eval with its ratcheting `SCENARIO_FLOOR`, and
ruff/mypy/bandit/pip-audit/coverage inside Layer 0. **The genuinely unautomated block is
config / docs / resource-lifecycle / cleanliness.** The sharpest instance is *"All new config in
`.env.example` with description"*: `config.py` declares ~202 settings against ~200 documented keys, the
only test touching `.env.example` (`tests/test_beat_ha.py:111-116`) asserts a **single key**, and two
settings have already slipped — `CELERY_SOFT_TIME_LIMIT_S` (the source of truth for the
soft < hard < visibility-timeout invariant the config itself enforces) and `YOUTUBE_PUBLISH_PRIVACY`
(controls whether uploads land public). A checklist item that has never once failed is not a gate; it
is a ritual.

### What Phase 4 should become

Three blocks, replacing thirty lines of recall:

1. **The machine block.** One command. *Admissibility rule: a Phase-4 item a script can check must be
   a script, or be deleted.* That rule converts the `.env.example` parity row, the absolute-paths row,
   the no-TODO/debug row, and the docs-updated row immediately, and formally attaches the four items
   already backed by machinery.
2. **~6 genuine judgement questions** — the ones no script can answer. *Does this change the
   creator-visible contract? Is any new external registration involved? Does any new prose in this diff
   make a claim a test does not check? Is there a decision here that belongs in an ADR?* and the two
   security ones that are genuinely judgement, not pattern (per-creator isolation on new queries; PII
   reachable by a new log line).
3. **One adversarial-review subagent pass on the diff, in a fresh context.** This is the rung the
   project has *already proven works*: the 2026-08-12 verifiers refuted 2 findings and corrected 10;
   this audit's verifiers refuted 7 of 86 and corrected 36. Anthropic now documents this as the
   adversarial review step — *"a fresh model tries to refute the result, so the agent doing the work
   isn't the one grading it."* This project got there independently and uses it only for audits, never
   for issues.

Anthropic's documented escalation ladder is: prompt-level check → `/goal` condition → **Stop hook that
blocks the turn from ending until the check passes** → **adversarial verification subagent in a fresh
context**. This repo independently invented rung 4 and has nothing on rungs 1–3. Note one honest
caveat: a `Stop` hook running `ci_local.sh --fast` would *not* have prevented the "merged while red"
incident at `:152` (that was the integration lane, which `--fast` does not run). Build the `PreToolUse`
hook first; the `Stop` hook is optional.

---

## 8. The 90-day plan

Ordered by **value per maintainer-hour**, not by severity. One person, ~30 hours across 90 days.

### Week 1 — the six items that have already cost more sessions than they cost to fix (≈1.5 h)

Do these **first**, in this order, before anything else in this document.

| # | Action | Effort |
|---|---|---|
| 1 | `frontend/package.json` → `"test:ci": "vitest run --reporter=verbose"`, called from `ci.yml:456` | **10 min** |
| 2 | `CLAUDE.md:120` → `.venv/bin/python …` (or `scripts/ci_local.sh`) | 5 min |
| 3 | `frontend/package.json` `"engines": {"node": ">=22 <23"}` + `frontend/.npmrc` `engine-strict=true` | 15 min |
| 4 | `ci_local.sh`: echo the `--randomly-seed=N` it used; preserve `.pytest_cache/v/cache/lastfailed`; reconcile the `failed: 0` vs `LOCAL CI FAILED` disagreement at `:157`/`:160` | 20 min |
| 5 | Promote `Migration lint (Squawk)` + `Visual regression` to required (one `gh api` call); delete or honour the `"GATING since 2026-07-29"` comment at `ci.yml:605-609` | 15 min |
| 6 | Delete the `Next free issue number` line from `LEFT_OFF.md` so `docs/issues.md` is the sole authority | 5 min |

**Item 1 is the single most important line in this entire audit.** It is ten minutes. The class it
closes has now cost three sessions, each of which ended by writing down the same advice.

### Weeks 2–3 — gate integrity (≈7 h)

7. `run_layer0.py`: `returncode` + bandit `errors[]` checks → `fail`, never `ok 0`. **(2 h)**
8. Then `--require ruff,mypy,bandit,pip_audit` on `static-gates` and `ci_local.sh:95`, plus a
   `test_ci_config.py` assertion that every invocation carries it. **(30 min)**
9. `gate_module_coverage`: `rate is None` → `failures`. **(30 min)**
10. `test_ci_config.py`: no load-bearing workflow step may use `|| true` or an unguarded pipe outside a
    named allowlist; and the required-context list is pinned against `ci.yml` job names. **(2 h)**
11. `mutmut`: `also_copy` the first-party import closure starting with `shared_resources.py`; assert
    `checked > 0`. **(1.5 h)**
12. Nightly live lanes assert an executed count > 0; empty-string secret fails loudly. **(1.5 h)**

### Weeks 4–5 — scan, don't list (≈5 h)

13. RLS: derive the sweep from `pg_policies` + `relrowsecurity`; assert every `creator_id` table has a
    policy or an explicit exemption. **(2 h)**
14. Quota / spend / kill-switch registries: derive from the live route table instead of the three
    literals. Fix the four routes B1 surfaced while you are in there. **(2 h)**
15. `RENDER_TASKS`: derive the ffmpeg set from the call graph (or a task decorator) instead of a
    hand-copied duplicate. **(1 h)**

### Weeks 6–7 — the docs-as-schema block (≈3 h)

16. `tests/test_doc_citations.py` — path **and symbol** resolution. **(1.5 h)**
17. `tests/test_env_example_parity.py` — `Settings.model_fields` ↔ parsed `.env.example`. **(1 h)**
18. Issue 497's own load-bearing AC: `_sources()` covers every top-level package. **(30 min)**

### Weeks 8–9 — production truthfulness (≈5 h)

19. One alert rule: deploy-run failure and `/health` non-`ok`. **(1 h)**
20. `alembic current == heads` in the prod job + a both-jobs meta-test. **(45 min)**
21. `beat` healthcheck + `autoheal` label; required `celery inspect ping` smoke step. **(1.5 h)**
22. `--full` on the preflight; `SKIP` → `FAIL` for a declared-active dependency. **(30 min)**
23. `doctor.py`: `stripe.WebhookEndpoint.list()` and `OAUTH_REDIRECT_URI` reconciled against
    `main.app.routes`. **(1.5 h)**

### Weeks 10–12 — the process artifacts (≈8 h)

24. The `PreToolUse` hook on `Bash` (interpreter refusal). **(30 min)**
25. `tests/test_off_course_budget.py` — the ratchet + the status-reconciliation assertion. **(1 h)**
26. `DECISIONS.md` → ADR migration, steps 1–6 of §4. **(4 h, interruptible)**
27. `CLAUDE.md` restructure: Read Order → "grep by topic"; move the conditional bodies to skills;
    rewrite Phase 4 as machine block + 6 judgement questions + adversarial subagent; update Phase 2 to
    match wave-scope practice. **(2.5 h)**

### What is deliberately **not** in this plan

Merging `Unit` + `Coverage floor` (refuted). Hardening `ci_local.sh` to fail closed (refuted). A
pre-commit hook, a merge queue, a required human review, a deploy-approval gate, a canary, Pact
contract testing, assertion-density linting (measured: 18 of 3,102 test functions lack an assertion,
~15 of them legitimately). None of these would have prevented anything in the corpus, and several
would make the process worse.

---

## The one-sentence version

**This project diagnoses better than it defends: it finds the right root cause almost every time, and
then writes the fix down instead of building it — so the same shapes keep returning under new names.
Roughly thirty hours of engineering controls, starting with a ten-minute change to one npm script,
converts the four classes that generate the most lost sessions from "remembered" to "impossible."**
