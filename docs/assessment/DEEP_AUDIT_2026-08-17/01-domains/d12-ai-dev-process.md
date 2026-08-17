# D12 — The AI-assisted development process itself

**Domain owner:** process/meta researcher, Deep Standards Audit 2026-08-17
**Scope:** how work is planned, remembered, verified and closed — not the code it produces.
**Read first:** `00-groundtruth/snag-taxonomy.md` §B, §E (this report builds on them, does not re-derive them).

---

## Verdict

The diagnostic half of this process is genuinely above industry standard — the "wrong hypotheses
ruled out" discipline, the adversarial verifier pass, and the self-retracting decision log are
things most funded teams do not do. The **corrective** half is missing: root causes are converted
into *prose that must be remembered* rather than *mechanism that cannot be forgotten*, and the
repo's own Claude Code automation surface (hooks) is **completely unused locally** — the only hook
`exit 0`s unless `CLAUDE_CODE_REMOTE=true`. That single gap explains the "baby snag after baby
snag" sensation better than any other fact in the corpus: nine documented gotchas each have a
written-down structural fix, and **none of the nine was built**.

The four-phase issue loop is not the problem and should not be blamed for the churn. The problem is
that Phase 4 has no teeth, so defects fall through to a later paid audit — which is exactly why
`fix:` (225) outnumbers `feat:` (207).

---

## What the current standard is, with sources

### 1. CLAUDE.md is advisory; hooks are deterministic. Anthropic says this explicitly.

From Anthropic's own current Claude Code best-practices page:

> "Keep it concise. For each line, ask: *'Would removing this cause Claude to make mistakes?'* If
> not, cut it. **Bloated CLAUDE.md files cause Claude to ignore your actual instructions!**"

> "**Use hooks for actions that must happen every time with zero exceptions.** Hooks run scripts
> automatically at specific points in Claude's workflow. **Unlike CLAUDE.md instructions which are
> advisory, hooks are deterministic and guarantee the action happens.**"

> Failure pattern — *The over-specified CLAUDE.md*: "If your CLAUDE.md is too long, Claude ignores
> half of it because important rules get lost in the noise. **Fix**: Ruthlessly prune. If Claude
> already does something correctly without the instruction, delete it **or convert it to a hook**."

The ✅/❌ table on that page explicitly puts **"Information that changes frequently"** and
**"File-by-file descriptions of the codebase"** in the ❌ column. `CLAUDE.md:31-42` (a read-order
mandate over four files that change daily) and `CLAUDE.md:47-56` (a file-structure list) are both in
the ❌ column, and both have already misfired (findings F2, F3).

The community consensus in 2026 collapses to the same one-liner: *"CLAUDE.md for context, skills for
procedures, hooks for automation"* — and *"Skills can be skipped; **Hooks block**."*
([explainx](https://explainx.ai/blog/skills-vs-hooks-vs-prompts-when-to-use-each-2026),
[rikuq](https://rikuq.com/blog/tools/claude-code-hooks-vs-skills-when-to-use/),
[AyyazTech](https://ayyaztech.com/blog/claude-code-3-customization-pillars-skills-hooks-claudemd))

### 2. Verification-first is the named cure for the exact failure mode this repo has.

> "Claude stops when the work looks done. Without a check it can run, 'looks done' is the only
> signal available, and you become the verification loop… **Have Claude show evidence rather than
> asserting success.**"

And the escalation ladder Anthropic documents — prompt-level check → `/goal` condition → **Stop hook
that blocks the turn from ending until the check passes** → **adversarial verification subagent in a
fresh context**. This repo already independently invented rung 4 (the 2026-08-12 audit's verifiers)
and has *nothing* on rungs 1–3.

### 3. Prose is the weakest control there is. This is measurable, not aesthetic.

Occupational-safety's **hierarchy of controls** (elimination > substitution > engineering >
administrative > PPE) is the standard framing SRE postmortem practice borrows for action-item
quality ([OSHA](https://www.osha.gov/sites/default/files/Hierarchy_of_Controls_02.01.23_form_508_2.pdf),
[incident.io](https://incident.io/blog/sre-incident-postmortem-best-practices)). Administrative
controls — checklists, runbooks, "remember to" — sit second-from-bottom because *they require a
human to recall them at the moment of exposure*. Documentation-based action items are the least
effective class at preventing recurrence; engineering controls are the effective class. Teams that
close postmortem action items inside 30 days measurably reduce recurrence over the next two
quarters.

**Every one of the nine recurring gotchas in `docs/OFF_COURSE_BUGS.md` was closed with an
administrative control** ("if it recurs, run with `--reporter=verbose`"), and every one recurred.

### 4. ADRs: one file per decision, immutable, with a Status field.

[MADR](https://adr.github.io/madr/) and the 2026 practitioner guides converge:
ADRs live in-repo, are numbered sequentially and **never renumbered**, are **never edited after
acceptance** — to change a decision you write a *new* ADR that supersedes the old one — and carry a
Status from `{Proposed, Accepted, Deprecated, Superseded, Rejected}`, where superseded entries read
`Superseded by ADR-NNNN` so the chain is traversable forward
([codercops](https://blog.codercops.com/blog/architecture-decision-records-2026),
[Catio](https://www.catio.tech/blog/architecture-decision-record)).
`docs/DECISIONS.md` is a 13,018-line append-only journal with **zero `Status:` fields** and
supersession encoded in heading parentheticals. It fails all three properties.

### 5. AI-assisted development has a known, measured rework problem — and this repo has it.

GitClear's 2026 data: two-week code churn roughly doubled from ~3.3% to 5.7–7.1% with AI adoption
and rose another 15% into 2026; refactored code collapsed from ~21% to 3.8%; copy-paste rose to
15.7% of new code. ~66% of developers report AI output is *"almost correct"* — close enough to
merge, broken enough to require rework
([LeadDev](https://leaddev.com/ai/code-maintainability-plummets-in-the-ai-coding-era),
[axify](https://axify.io/blog/code-churn)).
This repo's `fix:` 225 vs `feat:` 207 (41% fix-commits in July) is **exactly the published
signature**. It is not a sign of a uniquely broken process; it is the baseline hazard of the method,
and it is what engineering controls exist to counteract.

### 6. Progressive disclosure has replaced monolithic docs as the memory strategy.

2026 agent-memory surveys describe the shift from monolithic documentation toward **skills as
packaged procedures with progressive disclosure** — a short always-visible description, full body
loaded only on demand — and name **forgetting as "the most underrated operation": entries that are
wrong, stale, or never relevant accumulate quietly and add noise to every future retrieval**
([Externalization in LLM Agents](https://arxiv.org/html/2604.08224v1),
[mem0 State of Agent Memory 2026](https://mem0.ai/blog/state-of-ai-agent-memory-2026)).
`docs/` here has an append path and no forget path.

### 7. Zero-bug policy is the standard answer to a write-only bug log.

> "If a bug is worth fixing, fix it now; if it's not worth fixing, **close it**."
> ([Ministry of Testing](https://www.ministryoftesting.com/articles/zero-bug-policy-the-myths-and-the-reality),
> [Scrum.org](https://www.scrum.org/resources/blog/zero-bug-policy-fast-way-paying-back-technical-debt))

The point is not zero bugs. The point is that **"open" is not a valid resting state** — a row is
either fixed or explicitly declared not-worth-fixing and deleted. `docs/OFF_COURSE_BUGS.md` has 52
rows in the invalid resting state, some for two months.

---

## THE MECHANISM QUESTION — the core deliverable

### The Conversion Rule

> **Convert prose to mechanism when ANY of these four is true. The trigger is recurrence or timing,
> never severity.**
>
> 1. **It has been logged twice.** Second occurrence of a *class* is the conversion trigger. Not
>    third, not "if it recurs again". (`:133` → `:147` → `:157` is three; `:37` → `:135` is two.)
> 2. **The knowledge must be applied BEFORE the failure is observable.** Any advice of the form
>    "next time, pass `--flag` on the first run" is *structurally unfixable by documentation* — you
>    only know you needed it after it's too late to add it. This class must become a default.
> 3. **The prose is an input to a machine.** If any script, gate or config mirrors a list that lives
>    in a doc, that doc is a schema and needs a parity test. (`CLAUDE.md` Project Structure →
>    `run_layer0.py::_CANDIDATE_SOURCES` → Issue 497.)
> 4. **A script can check it in under a second.** Then it is a test, not a checklist item.
>
> **Corollary (hierarchy of controls, applied):** every recommendation in `OFF_COURSE_BUGS.md`
> ending in "remember to…", "consider…", or "if it recurs…" is a PPE-level control and *will* fail.
> Grep for those three phrases; each hit is a conversion candidate.

### Applying it — the nine open gotchas, and the mechanism for each

| # | Gotcha | Current control | Mechanism that makes it **impossible** | Cost |
|---|---|---|---|---|
| 1 | **Wrong interpreter** (`:156`, `DECISIONS:12884`) — system python3 → mypy "ok 0", 77 phantom CVEs, 4 phantom test failures, *filed as real defects* | Prose in `LEFT_OFF.md:199`. **`CLAUDE.md:120` actively instructs the wrong one** | (a) fix `CLAUDE.md:120` to `.venv/bin/python`; (b) 3 lines at the top of `run_layer0.py` and `tests/conftest.py`: `if Path(sys.prefix).resolve() != REPO/".venv": sys.exit("refusing to run outside .venv")`; (c) a `PreToolUse` hook on `Bash` that blocks bare `python3 `/`pytest `/`mypy `/`pip-audit ` | 30 min |
| 2 | **Node 22 vs 24 vs 26 jsdom** (`:37`, `:135`) | `.nvmrc` ×2 that **disagree** — `/.nvmrc`=`22.17.1`, `frontend/.nvmrc`=`22`; `ci_local.sh:49` reads only the root one. `engines` still absent (row `:37` says "engines pin open") | `frontend/package.json` `"engines": {"node": ">=22 <23"}` + `frontend/.npmrc` `engine-strict=true` → npm **refuses to install**; delete one `.nvmrc`; a CI step asserting `node -v` major == `.nvmrc` | 15 min |
| 3 | **vitest cold-run flake, name never captured** (`:133`, `:147`, `:157`) — three occurrences, three sessions burned, still unknown | Prose advice, twice, in the rows themselves | `frontend/package.json`: `"test": "vitest run --reporter=verbose --reporter=json --outputFile=.vitest-report.json"`. The name is captured **whether or not anyone remembered**. This is a 5-minute, 1-line fix that has now cost three sessions | **5 min** |
| 4 | **pytest ordering flake, seed never captured** (`:157`) | Prose advice in the row | `ci_local.sh`: generate `SEED=$RANDOM`, pass `-p randomly --randomly-seed=$SEED`, echo it in the summary; on failure `cat .pytest_cache/v/cache/lastfailed`. Also fix the `failed: 0` vs `LOCAL CI FAILED` disagreement at `ci_local.sh:157`/`:160` — one of them is lying | 20 min |
| 5 | **Doc `file:line` citations drift** (`:159`; `CLAUDE.md:236` cites `clip_engine/window.py`, real location `clip_engine/candidates.py:22`) | Nothing | `tests/test_doc_citations.py`: regex every `` `path.ext[:N]` `` in `CLAUDE.md` + `docs/*.md` (excluding `docs/archive/`), assert the path exists and, when `:N` is given, that the file has ≥ N lines. ~40 lines of test | 45 min |
| 6 | **`CLAUDE.md` module list is a machine input and is stale** → bandit never scanned 8,277 lines (Issue 497) | Nothing; already an AC on Issue 497 and still unbuilt | `test_layer0_sources_cover_tree`: every top-level dir with `__init__.py` and every root `*.py` ∈ `_sources()`, **and** ∈ the `CLAUDE.md` Project Structure list | 30 min |
| 7 | **A skipped gate counts as a pass** (`run_layer0.py:576-605`; hardened in the coverage job by Issue 479, **not** in `static-gates` or `ci_local.sh`) | Partial — one job of three | Pass `--require ruff,mypy,bandit,pip_audit` everywhere `run_layer0.py` is invoked. Then `test_ci_config.py` asserts every invocation in every workflow carries `--require` | 20 min |
| 8 | **Issue-number collision** — `docs/issues.md:4550` says next free is **498**, `LEFT_OFF.md:252` says **497** | Two files claim authority | Delete the number from `LEFT_OFF.md`; a test asserting the string `Next free issue number` occurs exactly once in the tree | 10 min |
| 9 | **Structural gate false-positived "for the second time"** (`:29`) | Prose | Every source-scanning gate gets one fixture asserting it *fires* (a deliberately-bad sample) alongside the assertion that it passes on the real tree. The project already does this for the eval harness; generalize it | 30 min |

**Total: about 3.5 hours of work to permanently retire nine recurring time sinks.** Items 3 and 8
are five- and ten-minute fixes that have collectively cost four sessions.

### The missing capability, stated plainly

`.claude/` in this repo contains **one hook, and it is a no-op on the developer's machine**
(`.claude/hooks/session-start.sh:12` — `[ "${CLAUDE_CODE_REMOTE:-}" != "true" ] && exit 0`). There
is no `PreToolUse`, no `PostToolUse`, no `Stop` hook. **Every single rule in this project is
advisory, and the audit corpus shows every single one has been violated at least once.** Three hooks
would close most of the taxonomy:

- **`PreToolUse` on `Bash`** — block bare `python3`/`pytest`/`mypy` (finding F1); block `git push --no-verify` unless an env override is set.
- **`PostToolUse` on `Edit|Write`** matching `config.py` — run the `.env.example` parity check.
- **`Stop`** — run `scripts/ci_local.sh --fast` and refuse to end the turn while red. This is
  Anthropic's documented "deterministic gate" rung, and it directly attacks the "merged while red"
  process failure at `:152`.

---

## Findings

### F1 — `CLAUDE.md:120` instructs the exact interpreter that produces phantom results — HIGH

`CLAUDE.md:120`, inside the Phase 4 gate every issue must clear:

```
- [ ] `python3 .claude/skills/production-assessment/scripts/run_layer0.py` passes
```

`grep -n "venv" CLAUDE.md` returns **nothing**. The rule lives only in `LEFT_OFF.md:199` —
*"⚠️ RUN TESTS FROM `.venv`, NOT SYSTEM PYTHON. This has now burned two consecutive sessions… This
bit again on 2026-08-15."*

**Failure scenario (already occurred, twice):** a fresh session follows the Phase 4 checklist
literally. System python3 carries fastapi 0.115.4 against the pinned 0.137.1. mypy aborts and
reports a vacuous `ok 0`; pip-audit invents ~77 phantom CVEs; four tests fail spuriously. Per
`OFF_COURSE_BUGS:156` those phantoms **were filed as real defect reports and later retracted**. The
governing rules file is the proximate cause and the corrective knowledge is in a file the read-order
does not even list.

**Verdict: deviation-unjustified.** Confidence high.

---

### F2 — The Read Order mandates ~23,000 lines before writing any code; it cannot be and is not followed — HIGH

`CLAUDE.md:31-42` requires six files be read *"before writing a single line of code"*, every
session. Actual sizes: `SOT.md` 705 + `PROJECT_STATE.md` 4,440 + `issues.md` 4,550 +
`DECISIONS.md` 13,018 + `COMPLIANCE.md` + `CLIPPING_PRINCIPLES.md` ≈ **23,000+ lines**. The audit
brief itself concedes nobody reads `DECISIONS.md` whole; this audit's own instructions say *"never
read it whole."*

Anthropic's guidance puts *"information that changes frequently"* in the ❌ column for CLAUDE.md,
and warns that bloated instruction files cause the real rules to be ignored.

**Failure scenario:** an unsatisfiable mandate is silently dropped, and once one CLAUDE.md rule is
routinely ignored the file's authority degrades uniformly — which is precisely why `:120`'s wrong
interpreter, `:55`'s dead `static/` claim and `:236`'s wrong citation all survived. The agent then
works from memory, which is the *one* failure the One Rule exists to prevent. This is the mechanism
behind the "we keep hitting baby snags" complaint at the context layer.

**The fix is not "read less."** It is progressive disclosure: CLAUDE.md keeps the ~40 lines that are
always true (honesty constraint, security invariants, the DO-NOT list, the exact commands), and the
conditional bodies move to skills that load on demand — `/decisions <topic>` (greps DECISIONS.md and
returns matches), `/state` (returns the current lane + next issue number), `/compliance`.

**Verdict: deviation-unjustified.** Confidence high. *Judgement call on the exact target size; not
on the fact that 23,000 lines is unreadable.*

---

### F3 — Stale prose in `CLAUDE.md` is a machine input and has already seeded a real defect — HIGH

Three verified errors in the governing file:

- `CLAUDE.md:51-53` — the canonical module list omits `billing/`, `chat/`, `analysis/`, `notify/`, `media/`.
- `CLAUDE.md:55` — *"Frontend assets in `static/`"*. The static app was retired in Issue 226; `CLAUDE.md:227` in the same file says React under `/app/*`. The file contradicts itself.
- `CLAUDE.md:236` — cites `WINDOW_S = 75.0, clip_engine/window.py`. It is at `clip_engine/candidates.py:22`; `clip_engine/window.py` does not exist.

`run_layer0.py::_CANDIDATE_SOURCES` **mirrors the `:51-53` list**. Issue 497 (`docs/issues.md:4498`)
states it outright: *"`CLAUDE.md` → Project Structure… also predates them… the stale list is the
root cause of defect (2)."* Consequence: mypy has never type-checked 4,093 lines, bandit has never
scanned 8,277 — including `crypto.py`, `auth.py`, `redact.py`, `api_key.py`, `flags.py` — while
`baselines.json` reported `bandit_high: 0 / bandit_medium: 0`.

**Failure scenario:** the next package added to the tree (as `chat/` was — where two of the July
SEV2s landed) falls outside both gates, silently, and the dashboard stays green. Nothing detects it.

**Verdict: deviation-unjustified.** Confidence high.

---

### F4 — The correct structural fix is identified and then not built. Nine times. — HIGH

This is the audit's central finding, and it is verifiable in three lines of shell:

| Row | The row's own recommendation | Status today |
|---|---|---|
| `:147` (08-12) | *"Make it structural: set `reporter: ['verbose']` … so the name is captured whether or not anyone remembered"* | `frontend/package.json` → `"test": "vitest run"`. **Not done.** `:157` is the third recurrence |
| `:37` (08-03) | *"Consider a `package.json` `engines` field so a wrong local Node fails loudly"* — status column literally reads **"engines pin open"** | `engines: None`. **Not done.** And the two `.nvmrc` files now disagree (`22.17.1` vs `22`), so the half-fix has itself drifted |
| `:157` (08-15) | *"`ci_local.sh` should print the `--randomly-seed=N` it used and preserve `lastfailed`"* | `grep -n "randomly\|lastfailed" scripts/ci_local.sh` → **no hits**. Not done |
| Issue 497 AC | *"**Structural guard** — a test asserting `_sources()` covers every top-level package… **This is the load-bearing AC**: without it the list drifts again"* | Unbuilt (deliberately deferred pending external review — legitimate, but the pattern holds) |

**Failure scenario:** the fourth vitest recurrence lands in a future session. The test name is
*still* not captured, because the capture flag still has to be remembered in advance. Another
session pays ~1 hour. The row gets a fourth entry recommending the same fix.

The generalization: **this project's postmortems produce administrative controls where engineering
controls were correctly identified.** The diagnosis is right every time. The control class is wrong
every time.

**Verdict: deviation-unjustified.** Confidence high.

---

### F5 — `OFF_COURSE_BUGS.md` is a write-only ledger with no closing pressure — HIGH

138 rows, **52 open (38%)**, 10 ever promoted to `docs/issues.md`. `:70` (cached-token
under-billing, money path) open since 2026-06-24. `:26` (`BACKUP_R2_BUCKET` unset — every prod
migration has run with no safety dump) open since 08-04. `:42` — *Playwright CI jobs have failed on
every merged PR since 2026-07-02*, meaning a whole CI job class carries zero signal, open for six
weeks.

**Failure scenario:** the log is the owner's felt experience of the project. It grows every session
and never shrinks; "snag after snag after snag" is a literal description of reading a
monotonically-increasing file. Meanwhile real defects (`:26`, `:42`) age past the point where
anyone remembers the context needed to fix them cheaply.

**The smallest mechanism that works — and the project already invented it.** `tests/test_clip_engine.py`
has `SCENARIO_FLOOR`, a *ratcheted* count with the ratchet history in a comment above the constant.
That is a working closing-pressure device sitting in this repo. Apply the same shape:

```python
# tests/test_off_course_budget.py
OPEN_ROW_CAP = 52   # ratchet DOWN only; history below.  2026-08-17: 52 (initial)
def test_open_off_course_rows_within_budget():  ...
def test_sev1_sev2_rows_carry_an_issue_number_or_a_closed_date(): ...
```

The cap starts at today's number (so it never blocks work retroactively) and only moves down.
Closing a row costs nothing when the answer is *"not worth fixing"* — under the zero-bug rule that
is a legal close. **Do not** add a dashboard or a cron; the ratchet is 30 lines and runs in the
existing unit lane.

**Verdict: gap.** Confidence high.

---

### F6 — `DECISIONS.md` fails all three ADR properties, and the failure has already caused decision reversals — HIGH

`docs/DECISIONS.md`: 13,018 lines, 259 `##` entries, **zero `Status:` fields**, supersession encoded
in heading parentheticals (`## 2026-08-12 (latest, superseding the 453 entry above)` at `:277`), and
only 24 `supersed*` mentions against 15+ known reversals plus 101 reversal-language hits.

**Failure scenario, concrete and already realized twice:**
1. An agent follows `AUDIT_BRIEF.md:150`'s own advice — *"a quick `grep -i "<topic>" docs/DECISIONS.md`"* — for "Render". It hits `:2541` **"Beta hosting: managed PaaS (Render), not the self-managed VM"**, an Accepted-looking entry with no status marker. That decision was reversed on 06-27, and the reversal is **buried inside an observability entry** (`:2395`). `render.yaml` (219 lines, containing `VERBOSE_LOGGING_ALLOW_PROD=true`) still sits at the repo root looking live. The agent configures or trusts the wrong host.
2. The prompt-cache floor: 2048 → "1024, not 2048 as previously documented" → Issue 138 "corrected" back to 2048 (`:7729`) → Issue 315 declares 1024 and marks all prior refs *"SUPERSEDED and historically incorrect"* (`:2837`). **Four states for one number**, because there was no place to record a status on the entry itself.

**Migration path that loses nothing** (~2 hours, then incremental):

1. **Freeze, don't move.** Add a header to `docs/DECISIONS.md`: *"FROZEN 2026-08-17 — historical record, no new entries. Index: `docs/decisions/INDEX.md`."* Do not renumber, reorder or delete a line — every existing `DECISIONS.md:NNNN` citation across `issues.md` and `PROJECT_STATE.md` keeps resolving.
2. **The index already exists.** `00-groundtruth/architecture-map.md` §A is a categorized 259-row table of `line — date — heading`. Commit it as `docs/decisions/INDEX.md` and add two columns: **Status** and **Superseded-by**. This is a copy-paste, not a rewrite.
3. **Mark the known dead ones today.** `:2541` and `:2563` → `SUPERSEDED by 2026-06-27 (:2395)`. `:7729` → `SUPERSEDED by :2837`. `:223` → `SUPERSEDED by :277`. Four edits close the four most confusing trails in the file.
4. **New decisions only** go to `docs/decisions/NNNN-slug.md` in MADR-lite form: `Status / Date / Context / Decision / Consequences / Supersedes / Superseded-by / Sources`. Never edit an accepted one; supersede it.
5. **Back-port ~22 files, not 259** — the load-bearing bets in `architecture-map.md` §C. Everything else stays an index row pointing into the frozen file.
6. **One test:** every ADR has a Status in the allowed set, and every `Superseded-by` resolves to an existing ADR.

**Verdict: deviation-unjustified.** Confidence high.

---

### F7 — Phase 4 is a ~30-item human checklist at the exact point where administrative controls are weakest — MEDIUM

`CLAUDE.md:117-161`. One item is automated (`run_layer0.py`); ~29 are human recall, including
load-bearing ones: *"All new config in `.env.example` with description"*, *"No PII or token in any
log line"*, *"Per-creator isolation enforced on every query"*, *"All paths absolute"*.

**Failure scenario:** `grep -rn "env.example" tests/` returns **one** hit, in
`tests/test_beat_ha.py` — nothing asserts `.env.example` ↔ `config.py` parity. 213 settings vs 212
documented entries is coincidence. `CELERY_SOFT_TIME_LIMIT_S` and `YOUTUBE_PUBLISH_PRIVACY` shipped
undocumented (ground truth §7). A checklist item that has never once failed is not a gate; it is a
ritual.

At least eight of the 30 are mechanically checkable today: `.env.example` parity, absolute paths
(ruff rule), no TODO/debug (ruff), every new function typed (mypy — already gating), no virality
promise (structural test — **already exists**), scores cite a named principle (already exists),
setup-start eval (already exists), docs updated (the citation test from F5).

**Recommendation:** adopt the rule *"a Phase 4 item that a script can check must be a script, or be
deleted."* Then Phase 4 becomes ~6 genuine judgement questions plus one adversarial-review subagent
pass on the diff — which is Anthropic's documented pattern and which this project has already
proven works (the 08-12 verifiers refuted 2 findings and corrected 10).

**Verdict: deviation-unjustified.** Confidence high.

---

### F8 — Zero local Claude Code automation: the entire hook surface is unused — HIGH

`.claude/` in-repo is 13 files: 2 skills, 2 commands, 2 settings files, **1 hook that `exit 0`s
locally**. `.claude/settings.json` registers only `SessionStart`, and
`.claude/hooks/session-start.sh:12` short-circuits unless `CLAUDE_CODE_REMOTE=true`. No
`PreToolUse`, no `PostToolUse`, no `Stop`, no `.claude/workflows/`. (The memory index claims an
`issue-wave.js` harness exists; it does not — ground truth §4.)

**Failure scenario:** this is the enabling condition for F1, F4, and `:152` ("W4 was merged while
red — process failure noted"). With no `Stop` hook there is nothing that can refuse to end a turn on
a red suite; with no `PreToolUse` hook there is nothing that can refuse a bare `python3`. Anthropic's
current guidance is unambiguous — *"hooks are deterministic and guarantee the action happens"*, use
them *"for actions that must happen every time with zero exceptions"* — and this project has a
documented list of exactly such actions that it enforces with prose instead.

**Verdict: gap.** Confidence high. **This is the highest-leverage unclaimed capability in the
repo** — three hook scripts, roughly an afternoon, attacking four of the eleven failure classes.

---

### F9 — Three files claim authority over project state and they disagree — MEDIUM

`docs/issues.md:4550` → *"Next free issue number: **498**"*. `LEFT_OFF.md:252` → *"Next free issue
number: **497**"*. `docs/issues.md:6` header → *"Active lane: L26"* while L27, L28 and L29 have all
completed (memory index; `PROJECT_STATE.md` documents 489–495). `PROJECT_STATE.md` (4,440 lines,
210 commits) and `issues.md` (4,550 lines, 241 commits) both encode issue status; `LEFT_OFF.md`
encodes a third copy plus the gotchas that belong in mechanism.

**Failure scenario:** a session files a new issue as 497, colliding with the existing Issue 497
(`docs/issues.md:4498`, the bandit/mypy gap). Two different defects then share a number in a tracker
whose header already advertises the wrong active lane. `:123` records the tracker having tracker
bugs already (Issues 194/195 missing `Status` lines, so automated counts read a neighbour's text).

**Fix:** one authority per fact. `issues.md` owns issue status and the next free number;
`PROJECT_STATE.md` becomes an append-only narrative changelog making **no status claims**;
`LEFT_OFF.md` is a session handoff and cites, never restates. Enforce with the F5-style test:
`Next free issue number` occurs exactly once in the tree.

**Verdict: gap.** Confidence high.

---

### F10 — The four-phase loop is not the churn source, but per-issue APPROVE at ~6 issues/day is theatre — MEDIUM (judgement call)

497 issues in 82 days is ~6/day. `CLAUDE.md:104-107` requires Phase 2 to *"wait for explicit
confirmation"* on each. At that rate approval is a rubber stamp, and the project has already
routed around it — parallel build waves are recorded in the memory index as standard practice, and
`DECISIONS.md` has wave-scope entries (`:12227`, `:12354`, `:12409`) approving 5–6 issues at once.
**The written process and the practiced process have diverged, and the practiced one is better.**

The churn does not come from the gate structure. It comes from Phase 4 having no teeth: findings
arrive in bursts from deliberate audits (16% of the corpus, 27 issues in a single day) while
steady-state CI contributes ~9%. That is a delivery model of *"build fast, then pay for a deep
audit"*, and it is the direct cause of `fix:` > `feat:`.

**Opinion, stated as an opinion:**
- **Keep Phase 1 CHECK unchanged.** It is the single best asset in the process — it is why
  `DECISIONS.md` entries carry live sources and ruled-out alternatives, which is above the norm for
  a funded team, let alone a solo one. 2026 spec-driven-development practice converges on the same
  point: *"speed without a spec produces confident, wrong code"*, and *"if sixteen agents
  misunderstand the spec you get sixteen messes"*
  ([dev.to](https://dev.to/krlz/spec-driven-development-in-2026-what-it-is-the-tooling-and-how-teams-actually-use-it-2fk2)).
- **Move APPROVE from per-issue to per-wave.** Approve a lane plan of 5–8 briefs once. This matches
  what already happens and removes a gate that cannot be honest at 6/day.
- **Rebuild Phase 4 as machine + adversarial subagent**, per F7.

**Verdict: over-engineered** (the ceremony exceeds what it can deliver at this cadence).
Confidence medium — this is a judgement call about process ergonomics, not a defect.

---

## What is genuinely right here

Be clear: the *thinking* in this process is better than the industry norm. Specifically —

1. **`~/.claude/ISSUES_LOG.md`'s "Wrong hypotheses (ruled out — don't repeat these)" section.**
   36 entries, every one carrying it. Almost no postmortem practice records the *refuted* branches;
   they record the winning one, which is exactly the information that does not prevent the next
   investigation. This is the highest-value artifact in the whole corpus and it should be protected
   from any pruning this audit recommends.
2. **Adversarial verification before it was documented practice.** The 2026-08-12 audit ran verifier
   subagents chartered to kill each finding: 2 refuted outright, 10 materially corrected
   (`CLIPPING_INTEGRITY_2026-08-12.md:97-99`). Anthropic now documents this as the "adversarial
   review step" — *"a fresh model tries to refute the result, so the agent doing the work isn't the
   one grading it."* This project got there independently.
3. **`DECISIONS.md:12884` retracts a prior diagnosis rather than overwriting it.** Preserving a
   wrong diagnosis alongside its correction is rare and correct.
4. **`tests/test_ci_config.py` — 22 tests asserting properties of the CI/CD YAML itself.** This is
   the project *already doing* prose→mechanism conversion at a level most teams never reach: the
   deploy runs self-hosted, the pre-migration dump precedes alembic, staging is sha-pinned never
   `:latest`, the eval commit status targets PR head sha. When this repo builds a mechanism, it
   builds a good one. The gap is frequency, not capability.
5. **The eval anti-hollowing ratchet** (`SCENARIO_FLOOR` + the skip-marker regex scan). A working
   closing-pressure device with its ratchet history in a comment. It is the template for F5.
6. **`docs/AUDIT_BRIEF.md` §4 and §5** — telling an external reviewer up front that `grep TODO` will
   mislead them, and listing seven things that look like smells and are decisions. That is an
   unusually honest and genuinely effective orientation artifact.

And on the strategy question: **docs-as-continuity is the right strategy for a solo AI-assisted
project.** Do not abandon it. What has inverted is the *state-tracking* subset — `PROJECT_STATE.md`
(4,440 lines), `issues.md` (4,550) and `LEFT_OFF.md` (259) triple-source the same facts and now
disagree (F9), and they are the three highest-churn files in the repo. The durable subset
(`DECISIONS`, `COMPLIANCE`, `CLIPPING_PRINCIPLES`, `RUNBOOKS`) is earning its keep and just needs
the ADR shape (F6) so it stays retrievable. The correct summary is: **the strategy is right; the
execution needs a forget path.** 2026 agent-memory research names this directly — *"forgetting is
the most underrated operation; entries that are wrong, stale or never relevant accumulate quietly
and add noise to every future retrieval."*

---

## Decisions this domain needs but does not have

1. **What belongs in `CLAUDE.md` vs a skill vs a hook vs a test.** No entry in `DECISIONS.md`
   addresses the project's own agent-instruction architecture, which is why everything defaulted
   into `CLAUDE.md`.
2. **A conversion policy: when prose must become mechanism.** Propose the four-condition rule above,
   with "logged twice" as the hard trigger.
3. **A `docs/` size budget and pruning cadence.** There is an append path and no forget path. What
   is the quarterly ritual that deletes?
4. **The ADR format itself.** `DECISIONS.md`'s shape was never decided — it accreted. A one-paragraph
   entry choosing MADR-lite, one file per decision, with a Status field, would settle it.
5. **The promote-or-close rule for `OFF_COURSE_BUGS.md`.** How long may a row stay open, and what is
   the legal way to close one without fixing it?
6. **Whether Phase 2 approval is per-issue or per-wave.** Practice has already answered; the written
   process has not been updated.
7. **A Phase 4 admissibility rule** — "a checklist item a script can check must be a script." This
   would have converted eight items already.
8. **Single-authority rule for project state.** Which file owns issue status, and which files may
   only cite it.
9. **The flake policy's teeth.** `docs/BRANCHING.md` has a Flake Policy; nothing enforces that a
   flake's *identity* is captured, which is why three flakes are unidentified.
