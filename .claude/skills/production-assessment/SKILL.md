---
name: production-assessment
description: >-
  Run a full, repeatable production-readiness assessment of the CreatorClip
  codebase. Use when the user asks "is this production ready", wants a quality
  sweep, a coverage/test-gap audit, a security/scale review, or runs /assess.
  Splits exhaustiveness (deterministic tools) from judgment (parallel per-module
  subagents that write findings to disk), so context stays flat as the repo grows.
---

# Production Assessment

A three-layer, context-bounded, repeatable assessment. The governing principle:

> **Tools provide exhaustiveness. Claude provides judgment. Never ask Claude to
> be exhaustive.**

A whole-codebase sweep in one context is the wrong primitive — it is
non-deterministic, unrepeatable, and its recall *drops* as the repo grows. This
skill instead pushes everything mechanizable into a script (perfect recall, zero
tokens) and reserves the model for per-module judgment, dispatched as parallel
subagents that write to disk. The orchestrator reads only short findings files,
never the source — so context stays flat from 16k LOC to 160k.

---

## Inputs / outputs

- Reads: the repo, plus the previous `docs/assessment/REPORT.md` (for diffing).
- Writes:
  - `docs/assessment/_machine.json` — Layer 0 deterministic results
  - `docs/assessment/modules/<module>.md` — one findings file per subagent
  - `docs/assessment/REPORT.md` — ranked register + production-ready verdict
  - `docs/assessment/history/<date>-REPORT.md` — immutable snapshot of this run

---

## Procedure

Run the three layers in order. Do **not** skip Layer 0 — its JSON is the input
the verdict is built on.

### Layer 0 — deterministic floor (the script)

Run the harness. It executes ruff, mypy, pytest-cov, bandit, and pip-audit,
compares each against the committed baselines, and writes `_machine.json`:

```bash
python3 .claude/skills/production-assessment/scripts/run_layer0.py
```

Read `docs/assessment/_machine.json` (small) — **do not** read raw tool output.
Note any gate that regressed against `docs/assessment/baselines.json`, and the
ranked untested-code list from the coverage section.

To re-baseline after fixing or after the first run (captures current reality as
the new floor):

```bash
python3 .claude/skills/production-assessment/scripts/run_layer0.py --update-baseline
```

### Layer 1 — map-reduce judgment (parallel subagents)

For each module below, dispatch **one `Explore`/`general-purpose` subagent in
parallel** (all in a single message). Hand each subagent ONLY:
its slice + `rubric.md` + `subagent-contract.md`. Each subagent writes
`docs/assessment/modules/<module>.md` and returns to you only a 3-line summary
(see the contract). You never read the source yourself.

Modules (slice by existing boundaries):
`clip_engine/`, `dna/`, `preference/`, `youtube/`, `worker/`, `routers/`,
`ingestion/`, `billing/`, `upload_intel/`, `improvement/`, and
`_root_infra` (= `db.py`, `crypto.py`, `config.py`, `auth.py`, `limiter.py`,
`models.py`, `main.py`).

If the repo has grown, add a module per new top-level package — the pattern
scales by adding subagents, not by enlarging any context.

### Layer 2 — verdict

Read `_machine.json` + every `docs/assessment/modules/*.md` + `scale-checklist.md`.
Produce `docs/assessment/REPORT.md` using the template in `report-template.md`:

1. A single **PRODUCTION-READY: YES / CONDITIONAL / NO** verdict.
2. A ranked register (BLOCKER → SEV1 → SEV2 → cleanup), each row with
   `module | file:line | issue | backed fix`.
3. The `scale-checklist.md` axes, each marked ✅ / ⚠️ / ❌ with evidence.
4. A **diff vs the previous REPORT.md** — what's new, fixed, regressed.

Then copy the report to `docs/assessment/history/<YYYY-MM-DD>-REPORT.md`.

A finding is not done until it has a *backed* fix — a concrete design with a
source or a number (pool math, an index, a config value), never just a
complaint. Cite `scale-checklist.md` sections where relevant.

---

## Cadence (how this stays repeatable, not heroic)

- **Per commit / PR:** Layer 0 runs in CI (`.github/workflows/quality.yml`). Cheap.
- **Per PR diff:** `/code-review` + `/security-review` on the diff only.
- **Per milestone / pre-launch:** full `/assess` (all three layers) → REPORT.md.
- **Pre-launch + after infra change:** Locust run (`tests/perf/`) for real
  concurrency evidence (the one thing reading cannot produce).
