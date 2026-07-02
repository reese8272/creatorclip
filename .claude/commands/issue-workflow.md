<!-- SYNCED COPY - do not edit here.
     Canonical file: ~/.claude/commands/issue-workflow.md
     After editing the canonical file, rerun: ~/.claude/scripts/sync-issue-workflow.sh -->

# /issue-workflow

Run the full issue workflow for the issue number or title passed as the argument.

This is a **process**, not a checklist to rush. The spine is still
CHECK → APPROVE → BUILD → REVIEW, but it is now wrapped with two things the old
version skipped: we ground ourselves in project context *first*, and we make
sure **you** (not just me) actually understand the code, the problem, and the
approach *before* any brief is written. Standards are never recalled from
memory — they are looked up live against current documentation every time.

**Standing rule for every phase — ask, with a recommendation.** Whenever I am
unclear about anything you want — scope, priorities, an approach tradeoff, what
"done" means — I ALWAYS stop and ask rather than guess. The ask must contain:
(a) exactly what is unclear and why it matters, (b) the realistic options with
their tradeoffs, and (c) **my recommended option with backed reasons** — the
evidence, links, or project constraints that make it the best fit. Never a bare
menu of choices: you get a recommendation you can accept or override.

---

## Phase 0 — CONTEXT & GROUNDING

Before reading the issue closely, load the project's working memory. Read
whichever of these exist:

- `CLAUDE.md` (root and any nested) — coding principles, production standards
- `docs/SOT.md` — source of truth: stack, schema, file structure, API surface
- `docs/PROJECT_STATE.md` — current status, session log, what's done
- `docs/issues.md` — the issue itself + acceptance criteria
- `docs/DECISIONS.md` — prior deviations and why
- Any architecture/requirements doc (`docs/ARD.md`, `docs/PRD.md`, `docs/SAAS_ROADMAP.md`, etc.)

**If any of the core understanding files are missing** (`CLAUDE.md`,
`docs/SOT.md`, `docs/PROJECT_STATE.md`, `docs/issues.md`, `docs/DECISIONS.md`):
a project should never be without them. Create a **minimal stub** now — a
header, a one-line purpose, and a `<!-- populated during issue close-out -->`
marker — so there is somewhere to write context into. Note which ones you
stubbed; they get fully populated in Phase 5. Do not block the workflow on this,
but do not silently skip it either.

Output a 3–5 line **grounding summary**: what this project is, where this issue
sits in it, and which files it will likely touch. This is the shared map for the
rest of the session.

---

## Phase 1 — UNDERSTAND (adaptive: grill or teach)

**Goal: bring you to a mid-level software-engineering understanding of the code
in scope, the problem, and the candidate approach — before any brief exists.**
We do not blindly accept an issue and start building. We build shared
understanding first.

This phase is **adaptive**. Open by probing what you already know, then branch:

- **If you understand the area** → *grill* (Socratic, à la `/grill-me`). Surface
  gaps, assumptions, and fuzzy thinking. Push back on vague answers: "what does
  that mean specifically?", "give me an example", "what breaks that?"
- **If you don't** → *teach* (à la the adaptive-code-teacher). Explain the
  relevant concepts in the context of *this* code — read the actual files and
  walk them, don't lecture in the abstract. Then check the concept landed before
  moving on. (You may dispatch the `adaptive-code-teacher` agent for a deep file
  walk-through if the area is large.)
- Most issues are a mix — teach the unfamiliar parts, grill the familiar ones.

**Cover these areas, one topic at a time** (skip any clearly irrelevant):

1. **Goal** — what problem are we actually solving, and who benefits?
2. **Design** — the high-level approach, and why it over the alternatives
3. **Data** — what goes in, what comes out, the shape of each
4. **Edge cases** — bad input, timeouts, empty state, concurrency
5. **Dependencies** — what this touches and what it could break
6. **Testing** — how we'll know it works

Ask ONE question at a time and wait for the response. **Do not write
implementation code in this phase.**

**Exit criterion (HARD GATE):** do not proceed to Phase 2 until you can
articulate, in your own words, (a) the problem, (b) the shape of the data and
the code being changed, and (c) why this approach. If you can't yet, we stay
here. End the phase with a one-line readout: *clear / still fuzzy on X*.

---

## Phase 2 — CHECK (research with evidence, then brief)

Now — and only now — we settle the approach. Two hard rules govern this phase:

**Rule 1 — live lookup, never memory.** When picking or deciding any standard,
framework, library, pattern, or version, look it up live against current
documentation or an accredited source — training has a cutoff and these facts
move. Use `web_search` / `WebFetch`, the `best-practices` skill, and the
relevant SDK docs for any external-service decision (e.g. the `/claude-api`
skill for Anthropic). The value is the *process* (how we reliably reach a
current, correct choice every time), not a remembered snapshot.

**Rule 2 — no source, no approach (HARD GATE).** An approach only qualifies for
the brief if it is backed by **concrete documentation with a literal link** —
official docs, release notes, a maintainer's guide, or an accredited
engineering source — that I actually fetched and read, not a link recalled from
memory. If the search doesn't surface support for a candidate approach, **it
does not become the approach.** Keep searching until satisfied: vary the search
terms, go to the official docs directly, check the project's GitHub
issues/discussions, check release notes for deprecations. If after an exhaustive
search nothing authoritative supports *any* candidate, do not silently pick one
— bring the user what was searched, what was found, and a recommended path
(per the standing rule above), and let them decide.

For each non-trivial choice, capture: the current de-facto standard, the
maintained options, known failure modes, recent deprecations — **and the literal
URL where each fact was found**.

Then present the brief in this exact format:

> **Issue N — [title]**
> **Problem:** [the problem, as we now jointly understand it]
> **Approach:** [specific pattern, library, or architecture we'll use]
> **Why for this project:** [1–2 sentences tying it to our stack / constraints / PRD]
> **Documentation:** [literal URL(s) backing the approach — each link labeled
> with the specific claim it supports and verified fetched this session]
> **Alternatives ruled out:** [what we considered and why it lost — with links
> where a source drove the ruling-out]
> **Good to go?**

A brief with an empty or hand-waved **Documentation** field is invalid — go
back and search more.

**Stop here. Do not write implementation code until the user confirms.**

---

## Phase 3 — APPROVE

Wait for explicit confirmation. "Just go" or "yes" counts as approval. If the
approach changes during discussion, note the deviation — it belongs in
`docs/DECISIONS.md` (created in Phase 5 if not already present).

---

## Phase 4 — BUILD (the most thorough phase — quality gates run AS we build)

This is the deepest phase of the workflow. Quality is enforced **during**
construction, not discovered afterward — Phase 5 is a final judge, not a
bug hunt, because the bugs get caught and fixed *here*, fast.

**Build discipline**

- Follow all Coding Principles and Production Standards in `CLAUDE.md`.
- **Write tests alongside the code — not after.** Prefer regression tests: a
  test that locks in the behavior so it can't silently break later. For a bug
  fix, write the failing test first.

**The in-build quality gate**

After **every meaningful unit of work** (a function, a module, roughly a
commit-sized chunk — never more), stop and assess the code just written on all
eight dimensions, scoring each **0–10** (0 = dogshit, 10 = perfect):

1. **Syntax errors** — does it parse/compile at all?
2. **Bugs** — does it do the wrong thing on real input?
3. **Logical errors** — off-by-ones, inverted conditions, wrong boundary
   handling, unreachable branches
4. **Code smells** — duplication, dead code, god functions, magic values,
   misleading names
5. **Compliance violations** — against `CLAUDE.md` coding principles,
   production standards, and this project's own conventions
6. **Performance bottlenecks** — N+1 queries, quadratic loops on unbounded
   input, per-request client construction, blocking calls in hot paths
7. **Indentation/formatting inconsistencies** — mixed styles, drift from the
   project formatter
8. **Security vulnerabilities** — injection, secrets in code, unvalidated
   boundary input, unsafe deserialization, path traversal

**GATE: no dimension may score below 8/10.** Any dimension < 8 → stop, fix it,
rescore, and only then write more code. Do not bank a known sub-8 for later.

Score with **real tools first, judgment second**: run whatever the project has
at each gate — formatter/linter (`ruff`, `eslint`, `gofmt`…), type checker
(`mypy`, `tsc`…), the relevant tests (`pytest -k …`, `npm test -- …`) — and
read actual output. Self-review covers only what tooling can't (logic, smells,
compliance, security reasoning).

**Keep a running scorecard** (dimension → score per unit of work). It travels
to Phase 5 as the evidence base for the final verdict.

Before moving to Phase 5: run the **full** test suite (the project's test
command — `pytest`, `npm test`, `go test`, `cargo test`, etc.). Any failure is
fixed here, not reported forward.

---

## Phase 5 — FINAL JUDGE & CLOSE-OUT

This phase is **not** a "find where the build went wrong" pass — Phase 4's
gates already caught and fixed problems at the moment they appeared. Phase 5 is
the final judgment on the finished work, the cleanup, and the handoff.

**1. Judge the work**

- Review the Phase 4 scorecard end-to-end: every dimension ≥ 8/10 on every unit
  of work, with the final state of any fixed item rescored.
- Run the full test suite one final time on the finished state.
- Spot-check the single riskiest change (the one with the lowest initial score
  or the most churn) against its acceptance criteria.
- If anything here lands below the bar → back to Phase 4; this phase does not
  patch code.

**2. Cleanup sweep**

- [ ] No `TODO`, commented-out blocks, or leftover debug statements
- [ ] Temp files and test scratch artifacts removed
- [ ] Nothing new that belongs in `.gitignore` is left unignored
- [ ] All new config values are in the committed env example template with a description
- [ ] No dead code left behind by the change

**3. Docs close-out — update, and CREATE IF MISSING**

A project must always have its core understanding files. If any of these does
not exist, create it now (Phase 0 left a stub; populate it fully here):

- [ ] `docs/SOT.md` updated if stack, schema, or file structure changed
- [ ] `docs/DECISIONS.md` updated if implementation diverged from the issue or PRD
- [ ] `docs/PROJECT_STATE.md` updated with status and a session-log entry
- [ ] `CLAUDE.md` updated if this issue resolved a standing item
- [ ] `docs/issues.md` — all acceptance criteria checked off

**4. Handoff — run `/close-out`**

Finish by running the `/close-out` command to write or refresh `LEFT_OFF.md`
at the repo root, so a brand-new session can resume from this exact point.
Include `LEFT_OFF.md` in the same commit as the doc/state updates above.

**5. Final readout**

- One-line verdict: **DONE**, **NEEDS WORK**, or **BLOCKED** — backed by the
  scorecard, not vibes.
- If anything was deferred or descoped, say so explicitly — never report a
  partial result as complete.
