# OFF_COURSE_BUGS — incidental defects found while doing something else

> A running log of bugs, fragilities, and surprises discovered **outside the scope of the
> task in flight**. The rule (see `CLAUDE.md` → *Off-Course Bugs*): when you hit one, **log
> it here in one line and keep going** — don't fix it inline unless it blocks the current
> task, and don't abandon the current task to chase it. Triage later: promote real defects
> into `docs/issues.md`, delete entries that turn out to be non-issues. This keeps the main
> pipeline on-course while ensuring nothing is silently brushed off.

> **2026-06-22 — Log slimmed.** All resolved/withdrawn rows (2026-05-29 → 2026-06-19) were
> moved to `docs/archive/off_course_bugs_snapshot_2026-06-22.md` (full history preserved).
> The three still-open rows below were promoted into the rebuilt `docs/issues.md` backlog and
> remain here until their issue closes.

## How to add an entry

One row per finding. Keep it short; link to the full issue once promoted. Severity uses
the same scale as the assessment rubric (BLOCKER / SEV1 / SEV2 / cleanup).

| Date | Found while | Bug / surprise | Severity | Status | Tracked / fixed in |
|------|-------------|----------------|----------|--------|--------------------|
| 2026-06-17 | Running the suite after the Issue 143 starlette 1.3.1 bump | `fastapi.testclient`/`starlette.testclient` now emits `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead.` on every TestClient construction. Tests pass, but it's noise and a future migration signal (httpx → httpx2 for the test client). | cleanup (test-infra noise) | 📋 Open | Promoted to backlog (QA/release-eng cluster). Re-evaluate when bumping the test stack; likely a `httpx2` dependency swap. Not blocking. |
| 2026-06-18 | Porting the dashboard to React (Issue 85c) | The dashboard fetches clip counts with one `GET /videos/{id}/clips` request **per done video** (N+1) — both the vanilla page and the React port do this (port parallelises via `useQueries`). For a creator with many processed videos this is N round-trips on every dashboard load. | SEV3 (perf, bounded by done-video count; not user-blocking) | 📋 Open | Promoted: folded into the per-video clips-map issue (batched `GET /videos/clips/counts` endpoint replaces the N+1). Backend change. |
| 2026-06-19 | Issue 164 live-site paid-flow run (`npm run test:prod:flows`) | **Video-analysis + title-optimizer flows timed out at 60s on the real account** (chat flow passed). Could be genuinely slow LLM generation (a UX gap — long spinner) or a real latency issue; one timeout isn't conclusive. Not chased further to avoid extra paid runs. | SEV3 (needs investigation) | 📋 Open | Promoted: raise the flow-test timeout and/or assert on response headers (200) rather than rendered output; if endpoints really exceed ~60s, treat as a perf issue. |
