# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-07-20 (end of the assessment-and-fix marathon session)
**Branch at close:** `main` @ `1ed2473` — working tree clean, 0 ahead/behind origin/main
**Prod:** deployed and healthy at this exact content (Deploy-to-production green 18:42 UTC; DB head **0046**)

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source of truth.

---

## CURRENT FOCUS

**The production-readiness verdict is now `YES for the v1 ≤100-user beta`** — see
`docs/assessment/REPORT.md` (snapshot `history/2026-07-20-postfix-REPORT.md`). This session ran a
full /assess (found 1 live BLOCKER-symptom + 4 SEV1s + ~37 SEV2s), fixed everything across three
parallel agent waves (PRs **#56, #57, #58**, all merged + deployed), cleared the live blocker by
SSH triage on the VM, and closed the YES gate with real Locust load evidence (50 users: 0 failures,
p99 180 ms; 300 users: clean saturation, limiter enforcing per-creator). Issues **356–361 are
CLOSED/DONE** with evidence in `docs/issues.md`.

### → NEXT ACTIONS

The code track is done. What remains is **operator work — `docs/GO_LIVE.md` Stage A** (unchanged
critical path #24→#25→#26→#28):

1. **#24** — verify prod env config (`ALLOWED_ORIGINS` locked, `/docs` disabled) on the VM:
   `ssh creatorclip-vm 'grep -E "ENV|ALLOWED_ORIGINS" /opt/autoclip/.env'` + `curl -si https://autoclip.studio/docs | head -1`.
2. **#25/#26** — external API provisioning check + Google OAuth consent screen/test users.
3. **#28** — beta go-live smoke with a real friend account (the 2026-06-30 sign-in failure was
   triaged as transient/non-recurring — if the friend hits it again, the decisive command is in
   Issue 356 / `docs/assessment/modules/_live_smoke_triage.md`).
4. Optional hygiene: commit Playwright visual baselines (#272) to make the visual job gating;
   ratchet the now-working per-module coverage floors in `run_layer0.py` (currently 0.0).

## WHAT WORKS NOW (don't re-investigate)

- **All 2026-07-20 findings fixed and verified in code by an independent re-assessment sweep**
  (15 modules, `docs/assessment/modules/*.md` all dated "post-fix"): spend-gate on
  `/clips/generate`, api_key RLS GUC, stale-render recovery (Redis marker + sweep + 409 override),
  runner split (live-proven by hosted PR runs), staging env-bleed overrides, CSP fonts (live-verified
  in the prod header), races backstopped by migration 0046, pause_turn consolidated into
  `worker/anthropic_stream.stream_until_final`, LGBM allowlist fix (real personalization-restoring
  bug), oauth scope replace-on-grant, deploy.sh full rollback port.
- **Load evidence recorded** in REPORT.md; staging stack + CSVs torn down per
  `tests/perf/README.md` (reproducible any time).
- **Deploy pipeline fail-safes observed working live**: the staging gate blocked a prod deploy when
  staging Postgres degraded under a load test, then passed on rerun.
- Layer 0 green: ruff/mypy 0, coverage 79.73 (floor 75.2), module-coverage gate operational for the
  first time (its coverage.xml-deletion bug is fixed).

## THE ARC THAT LED HERE

1. User asked for remaining bugs + production-testing readiness → full /assess @ `ca3305c` →
   CONDITIONAL (4 SEV1s, live sign-in screenshot triage, Issues 356–361 filed).
2. 8-batch parallel worktree workflow built the SEV1s + SEV2 leads → PR #56; 4-batch tail wave →
   PR #57 (both reviewed per-batch, merged conflict-aware, deployed).
3. SSH triage on the VM cleared the sign-in BLOCKER (transient, 2 weeks no recurrence) and found
   no stuck renders; CSP fonts confirmed broken → fixed in code defaults.
4. Fresh /assess re-run: 0 SEV1 anywhere, but caught 3 wave-introduced defects → fixed in PR #58.
5. Locust runs per `tests/perf/README.md` closed the load-evidence gate → **verdict YES-for-beta**
   (PR #59, docs).

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Prod | `autoclip.studio` — VM `creatorclip-vm` (147.182.136.107, `ssh creatorclip-vm` works, standing permission granted), `/opt/autoclip`, `docker compose -f docker-compose.prod.yml` |
| Staging | project `ccstage`, `docker-compose.staging.yml`, app on `:8001`, currently **torn down** (`down -v`) |
| Load-test runbook | `tests/perf/README.md` (seed fans out via sed-templating for >1 creator; CC_CREATOR_IDS) |
| DB head | `0046_race_unique_backstops` |
| Verdict + evidence | `docs/assessment/REPORT.md` + `history/2026-07-20-postfix-REPORT.md` |
| Session log | `docs/PROJECT_STATE.md` (three 2026-07-20 entries at top) |
| Cross-project issue log | `~/.claude/ISSUES_LOG.md` (new: ISSUE-2026-07-20-01, rollback-expires-ORM MissingGreenlet) |

## CONSTRAINTS & GOTCHAS

- **Any push/merge to `main` triggers Docker publish → staging gate → prod deploy.** Never run a
  staging load test with a merge in flight — the gate recycles `ccstage` mid-test (bit us once).
- **Local toolchain: `.venv/bin/*` ONLY** (bare `python3` is now brew 3.14 without pytest-cov/yaml).
  Layer-0: `PATH="$PWD/.venv/bin:$PATH" python3 .claude/skills/production-assessment/scripts/run_layer0.py`.
  Redis: `redis-server --daemonize yes --save '' --appendonly no`.
- **Piped exit codes lie** (`pytest | tail` → tail's 0 masked 4 failures): `set -o pipefail` or
  check `.pytest_cache/v/cache/lastfailed`.
- pip-audit locally = venv-drift noise (pillow/pip/pytest); CI's gate is authoritative.
- Visual-regression CI job is red by design until #272 baselines are committed (`continue-on-error`).
- CI runs on GitHub-hosted runners now (Issue 360); only deploy-track workflows use the VM runner.
- Staging deliberately still shares prod `TOKEN_ENCRYPTION_KEY` (data-bearing volume — documented
  residual in DECISIONS + compose comment).

---

## POINTERS

| Doc | Purpose |
|-----|---------|
| `docs/GO_LIVE.md` | THE plan now — Stage-A operator gates to beta |
| `docs/PROJECT_STATE.md` | Session log (2026-07-20 morning/later/evening entries) |
| `docs/issues.md` | Tracker — 345–361 all closed with evidence |
| `docs/assessment/REPORT.md` | Current verdict + full register + load numbers |
| `docs/assessment/modules/*.md` | Per-module post-fix findings incl. the ~60-item cleanup tail |
| `docs/DECISIONS.md` | 2026-07-20 entries: Redis staleness marker, deferrable unique, scope replace-on-grant, Opus-rate fallback, runner split, etc. |
| Memory dir | `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` |
