# OFF_COURSE_BUGS — incidental defects found while doing something else

> A running log of bugs, fragilities, and surprises discovered **outside the scope of the
> task in flight**. The rule (see `CLAUDE.md` → *Off-Course Bugs*): when you hit one, **log
> it here in one line and keep going** — don't fix it inline unless it blocks the current
> task, and don't abandon the current task to chase it. Triage later: promote real defects
> into `docs/issues.md`, delete entries that turn out to be non-issues. This keeps the main
> pipeline on-course while ensuring nothing is silently brushed off.

## How to add an entry

One row per finding. Keep it short; link to the full issue once promoted. Severity uses
the same scale as the assessment rubric (BLOCKER / SEV1 / SEV2 / cleanup).

| Date | Found while | Bug / surprise | Severity | Status | Tracked / fixed in |
|------|-------------|----------------|----------|--------|--------------------|
| 2026-05-29 | Validating the `build_dna` idempotency fix (Issue 76) under a live Postgres | A mid-session **Redis death** surfaced as ~25 opaque `500`s across the TestClient suites (every limiter/health route) and a silent coverage drop — it looked exactly like a code regression, costing real time to rule out. Root cause: the slowapi limiter has no in-memory fallback (by design) and nothing asserted Redis was reachable before the suite ran. | SEV2 (test-infra legibility) | ✅ Fixed | conftest `pytest_configure` Redis fail-fast guard (`tests/conftest.py`) + `scripts/dev_session_setup.sh` + `.claude/` SessionStart hook |
| 2026-05-30 | Running Layer 0 gates for Issue 78a | `run_layer0.py`'s `_CANDIDATE_SOURCES` lists a `knowledge/` package that **does not exist** in the repo. mypy aborts with `can't read file 'knowledge'` before checking anything, so the gate reports `mypy=1` instead of the true repo count (30). The mypy gate is effectively a no-op — it would not catch a real regression. (ruff/coverage filter the source list with `REPO_ROOT/s exists()`, but the mypy invocation passes the raw list.) | SEV2 (gate blind-spot) | 🔲 Logged | this log — promote to issues.md (one-line fix: drop `knowledge` or filter `_sources()` for the mypy call too) |
