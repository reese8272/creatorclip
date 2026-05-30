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
| 2026-05-30 | Running Layer 0 gates for Issue 78a | ~~`run_layer0.py`'s `_CANDIDATE_SOURCES` lists a `knowledge/` package that does not exist; mypy aborts and the gate mis-counts.~~ **WITHDRAWN 2026-05-30 (Issue 78c): misdiagnosis.** `gate_mypy()` calls `_sources()`, which *does* filter non-existent paths (`if (REPO_ROOT / s).exists()`), so `knowledge/` is dropped and the gate reports the true count (verified: `run_layer0.py --gates mypy` → 30, matching a manual count). The `mypy=1` I saw earlier came from running a *raw manual* `mypy` with the unfiltered `_CANDIDATE_SOURCES` list, not from the gate. No code bug; nothing to fix. | — | ✅ Withdrawn (non-issue) | n/a |
| 2026-05-30 | User triggered "Build Creator DNA" from onboarding after Issue 83 deploy | `build_dna` Celery task crashed 4× with `ModuleNotFoundError: No module named 'dna'` (task id `c3b02e43-689d…`, 19:48–19:51 UTC). Root cause: Celery is started via the `celery` script at `/root/.local/bin/celery`, so Python's `sys.path[0]` is the script dir, not the WORKDIR `/app`. Master inserts CWD before loading `worker.celery_app`, but the lazy `from dna.brief import generate_brief` at `worker/tasks.py:498` runs in a forked pool worker where the resolver still can't find `/app/dna/`. The same fragility silently shadowed every other lazy first-party import in `worker/tasks.py` — only `dna.*` blew up because none of its modules were transitively pulled in at celery boot, so `sys.modules` had nothing cached. | SEV1 (prod DNA build broken end-to-end) | ✅ Fixed | `Dockerfile` — `ENV PYTHONPATH=/app` so first-party packages are discoverable regardless of script entry point. Decision logged in `docs/DECISIONS.md`. |
