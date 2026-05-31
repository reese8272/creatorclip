# LEFT_OFF — Session Handoff Contract

> **Read this first.** Living "where we are right now" file. Not a changelog, not a
> source of truth — those live in `docs/`. Updated at the end of every session.

**Last updated:** 2026-05-31 (Wave 8 closed — 4 issues shipped on top of Issue 99's design system; production current with `7714c7e`)
**Branch:** `main` — HEAD `7714c7e`. Only `main` exists.
**Sync with `origin/main`:** **0 ahead / 0 behind** — fully in sync.
**Working tree:** clean (3 untracked PNG screenshots — audit artifacts; intentionally not tracked).
**Production:** ✅ **Current.** Deploy run `26724155993` (33s) finished successfully; `autoclip.studio` serves Wave 8.
**Tests (local, default lane):** 582 passed / 1 skipped / 122 deselected (+8 unit, +22 integration vs start of session).

---

## CURRENT FOCUS

### No active issue — the deck is clear

This session closed 4 issues on top of the Issue 99 design system rollout. Production is current. The deploy pipeline is end-to-end self-hosted (Issue 101) and the runner is live on `147.182.136.107`. **The right move next session is to pick from the queue below** — not to fix anything (nothing's broken).

### → NEXT ACTION — choose ONE

1. **Issue 95 frontend** — backend is done; profile.html needs an API-key management card (list / create / revoke) using the new design system. ~1-2 hours. Smallest tractable next step that closes the last loop on a shipped feature.
2. **Issue 96** — multi-turn chat-driven intake. Needs a Phase-1 brief covering chat-UX patterns, prompt design, and whether to do multi-turn SSE vs single-call extract-then-confirm. ~1 day.
3. **Issue 97** — livestream recap mode. Needs a Phase-1 brief covering recap-length budget (3-10 min target), clip_engine extension, subscription-tier mechanics on Stripe. ~2 days.
4. **OFF_COURSE Issue 101 candidate** — linked videos disappear from dashboard (the `source_uri IS NOT NULL` filter excludes user-linked rows by accident, not just catalog rows). Real bug; file as Issue 102 and fix. ~1 hour.
5. **Companion app repo** — `creatorclip-obs-companion` (Go, fyne or wails). Out of this monorepo. Needs its own session.
6. **`/assess`** — a full multi-agent production-readiness sweep with fresh context. The skill recommends running it after major batches like Wave 8; it dispatches subagents and writes per-module findings.

If the user says "just keep going" — start with #1 (Issue 95 frontend) since it has the smallest scope and closes a loop already in motion.

---

## WHAT WORKS NOW (do not re-investigate)

- **Self-hosted runner deploy pipeline** — `docker-publish.yml` AND `deploy.yml` both `runs-on: self-hosted`. Zero GitHub-hosted minutes consumed by deploys. Runner = `autoclip-prod-vm` on `147.182.136.107`.
- **CI / Quality Gates / Integration tests** intentionally STAY on `ubuntu-latest` — they fail loudly on the billing block (expected per Issue 101). They are informational; they do NOT gate deploys (`workflow_run` depends only on Docker publish).
- **Linear-style design system** at `static/_design-tokens.css` — every static template links it and consumes `--color-*` semantic tokens.
- **Monospace data register** applied to dashboard counts, pricing values, DNA stats, insights numbers, clip metadata, trim handles. Future data views inherit the convention via `.mono` / explicit `var(--font-mono)`.
- **OBS companion-app backend** — `creator_api_keys` table, `api_key.py` module, `routers/api_keys.py` (GET/POST/DELETE), `POST /clips/ingest` with bearer auth. End-to-end isolated and tested. Companion app itself is OUT OF SCOPE for this repo.
- **Walkthrough gate** — first-run creators (`onboarding_state='connected'` + no `walkthrough_seen` localStorage flag + not on a setup surface) get routed to `/static/walkthrough.html`. After: intake on `/static/onboarding.html` is mandatory — Skip removed, Build-DNA disabled until identity exists.
- **Insights endpoint** — `GET /creators/me/insights` is the single-fetch aggregator. Cross-creator video resolution is defended at the SQL layer (`Video.creator_id == creator.id` filter).
- **Clip transparency** — `Why this clip?` expander on `/static/review.html` surfaces `clip.reasoning` + `clip.principle` + score + timing for every clip. Auto-opens once.
- **Backboard Media's DNA banner stuck-bug** — healed by migration `0014_backfill_onboarding_state` (Wave 6 Fix A).
- **Pricing page** — fully wired + visible. CSS landed.
- **All footers + TOS/Privacy linkage** — Google OAuth verification gate around legal reachability is satisfied (Wave 6 Fix B).

---

## WHAT'S NOT DONE YET (the real queue)

### Carrying over from the backlog

| Issue | Status | Size | Notes |
|---|---|---|---|
| 95 frontend | Backend done; UI missing | Small | Profile-page card for key management. Reuses `.chip`, `.card`, `.btn` from the design system. |
| 96 | Not started | Large | Multi-turn chat intake. Needs Phase-1 design (chat patterns + Claude prompt + UI). |
| 97 | Not started | Large | Livestream recap mode. Needs `clip_engine` recap extension + Stripe subscription tier. |
| 99 Phase C | Opportunistic | Small (per-surface) | `.mono` register applied to surfaces as they're built. No dedicated sweep planned. |
| 78f | Blocked on staging | Medium | PgBouncer load test under real Postgres + PgBouncer. Sole gate moving the `/assess` verdict from CONDITIONAL → YES. |
| RLS activation | Manual op | Small | Run `Activate RLS (Issue 79)` workflow with `dry_run=true` then `false`. Hotfix B (Wave 1) already unblocked it. |

### Off-course bugs already filed

- **Linked videos disappear from dashboard** (`OFF_COURSE_BUGS.md` 2026-05-31 row). SEV1. Needs a discriminator column or behavior change in `list_videos`. Candidate Issue 102.

### Pre-public-launch gates (CLAUDE.md "Pre-Public-Launch Requirements")

- [ ] Lock `ALLOWED_ORIGINS` to production domain; disable `/docs` — verify
- [ ] Per-creator rate limiting + usage quotas before each LLM/render job — partial; sweep audit
- [x] YouTube data-retention/refresh fully compliant — Wave-4 Fix 3
- [x] Terms of Service + Privacy Policy pages live AND linked — Wave-6 Fix B
- [ ] `TOKEN_ENCRYPTION_KEY` rotation runbook written
- [ ] Google OAuth app verification submitted (external; now unblocked from our side)
- [ ] Account-deletion endpoint end-to-end tested on prod
- [ ] Billing + plan-tier wired — minute packs live; subscription tier pending (Issue 97)
- [ ] Eval harness hardened with adversarial/edge cases

---

## WHAT ELSE COULD BE ADDED (ideation backlog — not committed)

Pulled from `docs/issues.md` "Phase 3 Backlog" plus things that came up across this session. Pick from this when the queue above is exhausted; each needs its own Phase-1 brief before building.

**Creator workflow**
- **A/B test mode** — publish two cuts of the same clip, automatically poll outcomes, surface a "which one your audience preferred" view to feed the preference reranker.
- **Email digests** — weekly "here's your clip queue + DNA evolution" summary. Re-engagement primitive.
- **In-app caption editor** — burned-in subtitles with font + position + style picker on the review surface.
- **In-app crop editor** — manual 9:16 reframe adjust when the active-speaker autoframe gets it wrong.
- **"State of your channel" deep dive** — one-shot AI-authored report. Bigger than the improvement brief; closer to a YouTube-channel-audit deliverable. Maybe a subscription-tier perk like recap (Issue 97).
- **Voice clone for B-roll narration** — out-of-domain but adjacent; deferred forever unless a creator explicitly asks.

**Distribution**
- **Auto-publish to YouTube Shorts** — new OAuth scope; needs creator consent flow. Closes the loop from "render" to "live."
- **Multi-platform export** — TikTok / Reels / X. Format adapters share most of the render pipeline.
- **Discord notifications** — "your clips are ready" webhook. Real-time alternative to email digest.

**Live capture**
- **Companion app v1** — Go binary watching OBS replay folder (Issue 95 Architecture B). Out of monorepo.
- **WHIP ingest fallback** — for paying creators who want sub-2s clip latency (Issue 95 Architecture D, only at scale).
- **Vision signals** — MediaPipe face-emotion detection for cam-on reaction detection. Phase 2 per SOT.

**Monetization**
- **Subscription tier** — recurring revenue alongside the one-time minute packs. Needed before livestream recap (Issue 97) since recap is positioned as a sub perk.
- **Affiliate program for streamers** — referral codes wired to Stripe Connect. Funnel from the companion-app install flow.

**Internal**
- **Issue 84 follow-up** — Anthropic SDK 0.40 → 0.105.2 bump unlocks TTL-tier observability; drop unproductive cache markers on DNA + improvement-brief paths.
- **Haiku 4.5 A/B for clip scoring** — per `docs/assessment/llm/clip_scoring.md`, ~67% cost reduction opportunity if quality holds.
- **Eval harness expansion** — adversarial scenarios in `tests/eval/scenarios/*.yaml`.
- **`/assess` re-run** — multi-agent production-readiness sweep after a few more issues land.

---

## THE ARC THAT LED HERE

1. User-reported "things done but not on the website" → **Wave 6** four-fix audit batch.
2. Deploy infrastructure billing-blocked → **Issue 101** moved docker-publish to self-hosted.
3. Permission issue on first deploy → `setup-runner.sh` chown fix (commit `6980086`).
4. User-reported pricing page rendering broken → **Wave 7** CSS hotfix + locked Issues 95/99 directions from researched menus.
5. Issue 99 design system rollout → **Phase A** (`_design-tokens.css` + pricing proof) then **Phase B** (8 templates retrofitted in one commit).
6. User said "work through all issues. issue-workflow the remaining issues we have with high volume of testing and assessment" → **Wave 8** four-issue batch (95 backend, 100, 93, 94). Issues 96 and 97 deferred to focused sessions per "quality over coverage" rule.
7. Self-audit confirmed: 16 explicit creator-isolation sites in new code, zero raw-key logging, zero TODOs, every new function typed.

---

## KEY COORDINATES & FACTS

| Item | Value |
|---|---|
| Public URL | `https://autoclip.studio` |
| Production VM | `147.182.136.107` |
| Container image | `ghcr.io/reese8272/creatorclip:latest` |
| Repo | `github.com/reese8272/creatorclip` (NOT `Youtube-Video-AI-Editor` — that name 404s; runner registration token is repo-scoped) |
| Self-hosted runner | `autoclip-prod-vm` (`self-hosted,linux,x64,prod`) — systemd service `actions.runner.reese8272-creatorclip.autoclip-prod-vm` |
| Last successful deploy | `26724155993` (Wave 8 close commit) |
| Alembic head | `0015_creator_api_keys` (Issue 95 backend) |
| `/assess` REPORT | `docs/assessment/REPORT.md` — STALE (post-Wave-4); re-run before publicly claiming readiness |
| Memory dir | `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` |
| Secret names (NEVER log values) | `STRIPE_SECRET_KEY`, `JWT_SECRET_KEY`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `GOOGLE_OAUTH_CLIENT_SECRET`, `TOKEN_ENCRYPTION_KEY`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `GHCR_TOKEN`, `DEEPGRAM_API_KEY` |
| API-key bearer header format | `Authorization: Bearer ack_<32 url-safe chars>` (companion-app upload to `/clips/ingest`) |

---

## CONSTRAINTS & GOTCHAS (next session: read before acting)

- **Pushing to `main` auto-deploys.** Self-hosted runner picks up Docker publish, then workflow_run triggers Deploy. No staging gate. Each push = a production cut.
- **CI / Quality / Integration on hosted runners still fast-fail.** That's intentional (Issue 101) — they're informational, not deploy-gating. If you want them green, EITHER fix GitHub billing OR move them to self-hosted (be careful about VM CPU pressure).
- **Runner is the single point of failure for deploys.** If the VM is down, deploys queue. Fallback: `./scripts/deploy.sh` with `GHCR_TOKEN` set. Documented in `scripts/deploy.sh`.
- **`/docs` is exempted from RLS lookups via `creators` table exemption** — auth dependency resolves Creator before the GUC is set. Don't touch this.
- **`AsyncMock(return_value=_FakeSession())` does NOT work for `AdminSessionLocal()` patching.** Use `MagicMock`. (Logged in `docs/OFF_COURSE_BUGS.md`.)
- **slowapi TestClient rate-limit collision trap.** Per-creator UUID cookies sidestep it. (Pattern documented in `OFF_COURSE_BUGS.md` 2026-05-31 row.)
- **YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS=30** is a ToS upper bound. Do NOT increase.
- **Pre-existing `Event loop is closed` warnings** in `tests/test_progress.py` are SEV2 carry-forward, not a regression.
- **Repo was renamed** `Youtube-Video-AI-Editor → creatorclip` — old name returns 404 from `gh api`; runner registration tokens are repo-scoped.
- **`docs/assessment/REPORT.md` is stale** (post-Wave-4). Anyone saying "we're production-ready" based on it is wrong by 5 issues + a design-system rollout. Re-run `/assess` before that claim.
- **OAuth tokens are Fernet-encrypted at rest.** Read via `decrypt()`; never log.
- **Per-creator isolation on every query.** Missing `WHERE creator_id = ...` is a BLOCKER. Wave 8's new endpoints add 16 explicit filter sites; preserve them.
- **Bearer-auth surface (`/clips/ingest`) and session-cookie surface are separate.** Don't conflate `get_current_creator` and `get_current_creator_via_api_key`. The session dependency reads cookies; the bearer dependency reads the Authorization header and stamps `last_used_at`.

---

## POINTERS

- `docs/SOT.md` — current stack, file structure, schema
- `docs/PROJECT_STATE.md` — every issue's status + session log (Wave 8 closed at the top)
- `docs/issues.md` — issue backlog (Issues 96 + 97 are the next big ones)
- `docs/DECISIONS.md` — deviation log (Issue 99 design direction, Issue 95 architecture, Issue 101 self-hosted runner — all 2026-05-31)
- `docs/COMPLIANCE.md` — YouTube ToS, retention, privacy posture
- `docs/CLIPPING_PRINCIPLES.md` — named principles the engine cites
- `docs/OFF_COURSE_BUGS.md` — incidental defects (linked-videos-disappear is current)
- `docs/assessment/REPORT.md` — last `/assess` verdict (STALE)
- `walkthrough.md` (repo root) — user-facing tutorial document explaining how AutoClip works
- `static/walkthrough.html` — in-app first-run 5-panel explainer (Issue 100)
- `CLAUDE.md` — project rules; the One Rule is non-negotiable
- Memory: `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/MEMORY.md`
