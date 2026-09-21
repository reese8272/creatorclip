---
name: ui-check
description: Hands-off, terminal-only UI/UX verification. Runs the Playwright rung ladder via scripts/ui_audit.sh (mocked-network lane + seeded real-backend lane), then reads the machine-readable report and reviews the screenshots for UX judgment. Invoke when asked to "check the UI", "verify the frontend", "run the UI audit", or before/after frontend-touching changes. Prod lanes stay explicit flags — never run them implicitly.
---

# /ui-check — hands-off UI verification

You are verifying the CreatorClip/AutoClip UI with zero human clicking. The harness is
`scripts/ui_audit.sh` (Issue 542, Lane L33); this skill is the agent-facing wrapper (Issue 543).

## 1. Run the harness

```bash
scripts/ui_audit.sh                  # default: --rungs mocked,local
scripts/ui_audit.sh --rungs mocked   # no DB needed (network-mocked Vite lane only)
scripts/ui_audit.sh --prod           # ADDITIONALLY audits https://autoclip.studio — only when
                                     # the user explicitly asks for a prod check
```

What it does for you: env checks (node 22 via nvm, Redis, pg16 + `vector`, Playwright
chromium, `frontend/dist`), then for the `local` rung: migrates + seeds `creatorclip_e2e`
(`tests/perf/seed_staging.py --profile ui`), mints a `cc_session` storageState
(`scripts/mint_storage_state.py`), boots uvicorn serving the built SPA at
`http://localhost:8000/app/`, runs the Playwright lanes, and tears down. Exit code is
non-zero iff a required rung failed.

If it fails in the env-check phase it prints a NAMED remediation (e.g. "start pg16",
"npm --prefix frontend run build") — do that and re-run rather than debugging blind.

## 2. Read the report

Read `frontend/e2e/.audit/report.json` — top-level `{ok, rungs, counts}`, then per
route×viewport entries with `consoleErrors`, `pageErrors`, `failedRequests`,
`axeViolations`. Treat any console/page error and any failed request as a finding even
when the test itself passed.

## 3. Look at the screenshots (this is the point)

Screenshots are at `frontend/e2e/.audit/screens/<rung>/<route>-<viewport>.png` — flat,
enumerable paths. **Read them with the Read tool** (it renders PNGs) and judge as a
designer would: layout breakage, overflow/clipped text, empty states where the seed
should have produced data (the `ui` seed feeds every route — an empty screen is a bug,
not a fixture gap), contrast problems, mobile viewport collapse, spinners that never
resolved. Compare the `mocked` and `local` captures of the same route — divergence means
the real serializer shape and the mock have drifted.

## 4. Report findings per route

For each finding: route, viewport, rung, what's wrong, and the evidence (error text or
screenshot path). No findings → say so explicitly, listing what was covered (routes ×
viewports × rungs).

## Invariants (do not "fix" these)

- The local rung runs **no Celery worker**: async states must read as `queued`, never
  `done`. A spec asserting `done` there is the bug — do not add a worker to make it pass.
- The `ui` seed is ON CONFLICT-idempotent; re-running the harness must not need a DB drop.
- Never flip prod detection flags (`CAMERA_REGION_DETECT_ENABLED`,
  `OVERLAY_BAND_DETECT_ENABLED`) from this harness.
- Visual-regression baselines are CI-only (WSL2 font antialiasing false-positives) — the
  harness already excludes `@visual`; don't re-add it locally.
- `frontend/e2e/.auth/` holds a valid dev JWT and stays gitignored.
