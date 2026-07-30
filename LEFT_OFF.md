# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-07-29 (end of the "100% ready" pass)
**Branch at close:** `w3/ready-pass-closeout` — PR #63 to main pending (**merge = Deploy 3**, carries the 8 assess-fixes + all W3 docs)
**Prod:** healthy at `a50b332` (= Deploys **1+2**, both through the staging gate 2026-07-29); DB head **0047**

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source of truth.

---

## CURRENT FOCUS

**The 2026-07-29 ready-pass is complete** — verdict **YES-for-beta** re-confirmed
(`docs/assessment/REPORT.md`, snapshot `history/2026-07-29-REPORT.md`). Two build waves shipped and
deployed same-day: **W1** (PR #61 = publish/schedule UI, applied titles→YouTube, trim→real
re-render, source-expired UX) and **W2** (PR #62 = flake elimination to literal zero noise, worker
hardening, self-hosted fonts, enqueue DRY, Issue-272 visual job now GATING, **approved billing
fixes**). The close-out /assess found 1 SEV1 + 7 SEV2s — **all fixed same day** (on the pending PR).

### → NEXT ACTIONS

1. **Merge PR #63** (`w3/ready-pass-closeout`) once CI is green — Deploy 3. Needs owner merge
   authorization (prod deploy).
2. **#26** — Google console (~5 min): confirm the 4 scopes match `youtube/oauth.py:46-51`; add each
   friend's Gmail under Audience → Test users. NOT skippable: unverified apps hard-block non-test-users.
3. **#282 uptime monitor** — now beta-critical: prod was down **~31h with zero alerts** (Jul 28
   11:59 UTC — owner-intentional: droplet disabled, re-enabled Jul 29; the monitoring gap is the
   lesson, not the shutdown). Better Stack free tier + `/health` monitor + status page (~30 min).
   Note: once friends are invited, the droplet must STAY UP — pause monitors for any intentional
   downtime instead of powering off silently.
4. **#28** — friend beta smoke + 48h window → invite. Also live-smoke the new surfaces: publish
   flow, trim re-render, source-expired card (owner's expired clip `b4c87d6f` is a natural canary),
   cleaned-preview playback (the s3:// fix).

## WHAT WORKS NOW (don't re-investigate)

- **Core loop is feature-complete**: upload→clips→review→apply-titles→trim/clean re-render→
  schedule→private publish→outcomes→retrain. Canonical map: **`docs/PIPELINE.md`** (NEW).
- **Gates #24/#25 GREEN with live evidence** (doctor 30/30, env locked, /docs 404, no key leaks,
  deploy proven twice). GO_LIVE Stage A: 15 GREEN · 6 CODE-GREEN · 11 OPEN.
- **Layer 0 fully green locally** (first time): ruff/mypy 0, coverage 83.51, pip-audit 0 (was venv
  drift — venv upgraded), module floors ratcheted (crypto/limiter 99, auth 91), visual job gating.
- **Billing now covers every LLM call site** (intake, thumbnail-patterns, chat tools were unbilled;
  1h cache-writes at the true 2×) — enforced by a repo-wide AST guard in `tests/test_usage_coverage.py`.
- Suites at close: backend **2338/0**, frontend **285/0**; zero event-loop noise (the poisoner was
  the compliance crawler's bare TestClient — root-caused, fixed, regression-locked).

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Prod | `autoclip.studio` — droplet 147.182.136.107 (`ssh creatorclip-vm`), `/opt/autoclip`, compose prod file |
| DB head | `0047_clip_applied_metadata` |
| Verdict + register | `docs/assessment/REPORT.md` (+ `history/2026-07-29-REPORT.md`) |
| Session log | `docs/PROJECT_STATE.md` (2026-07-29 entry at top) |
| Tracker | `docs/issues.md` — new Issues **363–368** filed; 362 residual + 272 closed; #282 elevated |
| Open register | 4 carried SEV2s (worker redelivery double-spend; routers ×3) + gated/accepted residuals — REPORT.md table |
| Wave harness gotcha | memory `worktree-wave-gotcha`: agent worktrees can vanish mid-build — commit early to lane branches |

## CONSTRAINTS & GOTCHAS

- **Any merge to main = Docker publish → staging gate → prod deploy.** Merges need owner authorization.
- Local toolchain: `.venv/bin/*` only (`python -m pip`, not bare pip — venv has no pip shim on PATH).
- Visual baselines regenerate ONLY via `gh workflow run ci.yml -f update_snapshots=true` →
  download artifact → commit (never from WSL2 — font rendering).
- `gh pr edit` dies on a GraphQL projectCards bug — use `gh api repos/.../pulls/N -X PATCH` instead.
- Piped exit codes lie: check `.pytest_cache/v/cache/lastfailed` or `set -o pipefail`.

## POINTERS

| Doc | Purpose |
|-----|---------|
| `docs/PIPELINE.md` | **NEW** — canonical upload→publish flow map (files/functions/error codes) |
| `docs/GO_LIVE.md` | Stage-A scorecard (updated 2026-07-29 with gate evidence) |
| `docs/assessment/REPORT.md` | Verdict + ranked register |
| `docs/DECISIONS.md` | 2026-07-29 W1+W2 sections (~18 entries) |
| `docs/OFF_COURSE_BUGS.md` | 2026-07-29 rows: outage, fixes, follow-ups |
| Memory dir | `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` |
