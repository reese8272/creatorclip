# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-07-03 (end of the W3 wave — the final build wave)
**Branch at close:** `w3/round1` — PR to main pending (carries migration **0045**)
**Prod:** at W2 content (PR #46-48, DB head `0044`); W3 deploys when its PR merges (first migration through the staging gate)

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source of truth.

---

## CURRENT FOCUS

**W1, W2, and W3 are all code-complete — the build track of the roadmap is EMPTY.** W3 shipped:
the styled re-render fix (353), NULLIF-hardened RLS policies via migration 0045 (354), the
downgrade-reversibility CI check (296), the re-engagement sunset cap + lifecycle integration test
(246), and **docs/GO_LIVE.md** (303) — the canonical 41-gate go/no-go ledger.

### → NEXT ACTIONS

1. **Merge the W3 PR** from `w3/round1` once CI is green (Playwright/visual red as always). The
   deploy applies 0045 through the staging gate — expected to just work now.
2. **Work docs/GO_LIVE.md Stage A** — that IS the plan now: 13 hard-open gates (critical path
   #24→#25→#26→#28: prod env config check → external API provisioning check → OAuth consent
   screen + test users → beta go-live smoke with a friend) + 7 verification residuals + the
   standing operator checklist (key rotation, Cloudflare rule, Redis cron+drill, Better Stack,
   DO billing alert, Grafana rule, R2 lifecycle check, MAILING_ADDRESS + Gmail round-trip).
3. **Stage B (public launch, Issue 30)** stays gated on #29 OAuth verification + #261 load test —
   post-beta.

## KEY FACTS / GOTCHAS (delta from the W1 handoff)

- **Post-restart PATH trap:** `python3.12` now resolves to the polluted `~/.local` user-site
  (stale FastAPI-0.115-era packages) — its pip-audit output is NOISE and its mypy misses real
  errors. **Layer-0 must run as** `PATH="$PWD/.venv/bin:$PATH" .venv/bin/python
  .claude/skills/production-assessment/scripts/run_layer0.py`. The user-site needed
  `deepgram-sdk` installed (`--user --break-system-packages`) for Batch E's SDK-guard test.
- **Spend guard:** `billing/spend_guard.py`; trip/reset runbook in RUNBOOKS.md; counters are
  MICRODOLLARS in Redis; global trips flip `llm_generation` (manual reset only).
- **Ownership lookups:** use `routers/_owned.py::get_owned` (25 sites migrated); test stubs
  use `tests/_helpers.owned_lookup_result`/`stub_get_owned`, not `session.get` mocks.
- **Shutdown lifecycle:** long-lived clients register with `shared_resources.register_aclose`;
  lifespan calls `close_all()` (reverse order, error-isolated).
- **Recap flow:** POST `/videos/{id}/summaries` → selection in-request → `render_summary`
  task → `GET /summaries/{id}/download`. UI at `/app/video/:id/recap`.
- **Deploy pipeline:** prod job now `needs: deploy-staging` (exact sha- image, in-container
  alembic against the persistent ccstage DB); `skip_staging` workflow_dispatch break-glass;
  rollback works now (`${IMAGE_TAG:-latest}` + `:rollback` re-tag).
- The agent-worktree CWD-teleport quirk persists — `cd` to repo root before git operations.

---

## POINTERS

| Doc | Purpose |
|-----|---------|
| `docs/PROJECT_STATE.md` | Session log — W2 entry at top |
| `docs/issues.md` | Tracker — W2 statuses flipped with evidence; W3 next |
| `docs/DECISIONS.md` | 2026-07-02 entries: W1 + Squawk + rounds 2–3 + W2 scope/decisions |
| `docs/INCIDENT_RESPONSE.md` | NEW — severity ladder + runbook index |
| `docs/OFF_COURSE_BUGS.md` | 2 promotable SEV2s outstanding |
| Memory dir | `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` |
