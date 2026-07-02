# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-07-02 (end of the W2 wave session)
**Branch at close:** `w2/round1` — PR to main pending (no migrations)
**Prod:** at W1 content (PR #45, DB head `0044`); W2 deploys when its PR merges

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source of truth.

---

## CURRENT FOCUS

**W1 and W2 are both code-complete.** W2 shipped today on `w2/round1`: recap UI (192 — the
Stream-VOD lane is user-reachable end to end), spend guard (290+291, approved $5/$50/$400
thresholds onto the llm_generation kill switch), one-click-unsubscribe compliance fix (245),
GPC (302), deletion-on-restore (254), the staging-parity deploy gate + the #271 rollback fix
(298), nova-3 price fix + R2 gauges + Deepgram-stays DEC (293), incident-response index (283),
cost-review runbook (292), the mypy strictness ratchet (78-R), and all 5 rescoped 109 cleanups
(incl. a measured 4.6–17.6× scoring-loop speedup). 310/78 were reconciled-closed as
already-shipped.

### → NEXT ACTIONS

1. **Open + merge the W2 PR** from `w2/round1` (all gates green locally: 2204 tests, venv
   Layer-0 clean, 240 vitest + tsc, eval 100%). ⚠️ The merge triggers the **first run of the
   new staging gate**: EITHER tear down the old `cc139` staging project on the VM first
   (`docker compose -p cc139 -f docker-compose.staging.yml down` — it holds port 8001) OR
   dispatch the deploy with `skip_staging=true` once, then do the teardown.
2. **Operator checklist** (accumulated, all documented): rotate the exposed Anthropic key;
   Cloudflare `/auth/*` rule (`docs/EDGE_SECURITY.md`); Redis backup cron + drill
   (`docs/RUNBOOKS.md`); Better Stack page (#282); #228 live 429 smoke; DO billing alert;
   Grafana cost rule + `docs/dashboards/llm-cost-panel.json` after #326 activation; R2
   lifecycle-numbers dashboard check (#254); spend-guard staging trip drill (#290).
3. **Promote from OFF_COURSE_BUGS:** styled re-render no-op (SEV2); NULLIF GUC policy
   hardening migration (SEV2); LLM E2E Nightly red; Playwright runner gap.
4. **Next wave: W3** — open items incl. 246 (lifecycle sequence), 300 (COPPA gate), 296
   (migration reversibility CI), 197 (published-clips outcome loop), 96-fold residuals; plus
   the W1/W2 staging-verify residuals once the staging gate is exercised.

---

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
