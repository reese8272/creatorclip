# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-07-30 (later — product/market review session; **Lane L24 filed, no code written**)
**Branch at close:** `main` @ `4ebb76b` — level with `origin/main` (0 ahead / 0 behind)
**Working tree:** **4 modified docs, UNCOMMITTED** — `docs/issues.md`, `docs/DECISIONS.md`, `docs/PROJECT_STATE.md`, `docs/COMPETITIVE_RESEARCH.md`
**Prod:** healthy — last deploy run `30561760945` succeeded 16:30 UTC; prod DB head **0049**; L23 surfaces live

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source of truth.

---

## CURRENT FOCUS

Two tracks are open. **A is the operator/launch path (unchanged and still the critical path). B is new
this session and is a decision, not a build.**

**A — Friend beta (blocked on operator actions, not code).**
**B — Lane L24 "Positioning & Moat Surfacing" (Issues 374–383) was just filed** from an owner-requested
product/market review. Nothing is built; **#382 (scope-freeze decision) gates the other nine issues.**

### → NEXT ACTIONS

1. **Commit this session's doc changes** (they are the entire deliverable — no code touched):
   `git add docs/ LEFT_OFF.md && git commit` on a `docs/` branch → PR → merge.
   ⚠️ Merging to `main` triggers the prod deploy pipeline; a docs-only merge is safe but still deploys.
2. **Make the #382 call** (5 minutes, owner-only, unblocks the lane): which of the breadth cluster —
   L23 advanced effects, #322/#323 (per-clip titles + thumbnail concepts), #324/#325 (agentic chat) —
   gets parked so #374/#375/#376 can be funded. Record in `docs/DECISIONS.md`; mark parked statuses in
   `docs/issues.md`. **Do not start #374 before this call.**
3. **Friend-beta path (still the launch critical path):** **#26** — owner adds the buddy's Gmail in
   Google Console → Audience → Test users, and confirms the 4 scopes match `youtube/oauth.py:46-51`.
   Then **#28** friend smoke. **#282** uptime monitor remains OPEN and beta-critical (a 31h silent
   outage on 2026-07-29 proved the gap).
4. **Still queued from last session** — live smoke of the L23 surfaces on prod (needs a real upload),
   and the pytest 8.3.3 → 9.0.3 dev-dep bump (PYSEC-2026-1845; until then Layer-0 `pip_audit` reads
   1 vuln vs the 0 baseline).

## WHAT WORKS NOW (don't re-investigate)

**L23 is shipped and deployed** — PR #65 → `main`, prod DB head 0049, all L23 endpoints live. The
2026-07-29 ready-pass gates are unchanged. Backend 2377 / frontend 309 green at L23 close.

**The L24 research is done and dated — do not re-run these searches.** Five live searches on
2026-07-30, all cited inline in the lane preamble and the DECISIONS entry:
- YouTube is rolling **Video Clips into Shorts + auto-suggestions for "clippable" moments** later in
  2026 → the clip-finding layer becomes free and native.
- YouTube's **16 July 2026 "inauthentic content"** policy (renamed from "repetitious content"):
  templated/mass-produced output is non-monetizable, three-strike ladder → an extinction risk for
  template-based clippers and the wedge behind #375.
- **OpusClip's own 2026 strategy post** concedes the bottleneck moved to "the decision of what
  deserves cutting" and that "measurement is where repurposing setups break down."
- **Style-learning is still unclaimed** field-wide (brand kits/templates only) — dated re-confirmation
  of the `COMPETITIVE_RESEARCH.md:104` thesis.

**Four backlog reconciliations were already worked out — don't re-derive them:**
- **#197 is DONE** — publish already creates `ClipOutcome` rows, so the outcome loop has input.
  #374 is **surfacing-only**, needs no new data collection. That's why it's the cheapest high-leverage item.
- **#132 (chat-spike) stays BLOCKED** — no replay endpoint; scrapers breach ToS §IV.A. **#381 is a
  distinct path** (`liveChatMessages.list` on the creator's own *live* broadcast), gated on a quota
  verdict that may legitimately sink it. Do not let #381 drift into a scraper.
- **#209 locked per-input-minute pricing.** #380 re-opens it for **evaluation only**, on three named
  new facts. The `MinuteDeduction` ledger stays untouched either way.
- **"No-auth demo mode" is a Phase-3 parking-lot item** — #376 promotes it, and flags that
  `YTDLP_ENABLED=False` is own-content-only, so an **arbitrary-URL demo is not ToS-clean** (house
  sample videos are the likely answer).

**The uncopyable asset, located and evidenced:** `poll_clip_outcomes` → `ClipOutcome.performed_well`
(`worker/tasks.py:1055`) → 3× training weight (`preference/decay.py:8`). It needs per-creator Analytics
OAuth, so no competitor can copy it — and it is surfaced to creators **nowhere**. That is Issue 374.

## THE ARC THAT LED HERE

1. **2026-07-29** — "100% ready" pass: core-loop UX shipped over two deploys; a **31-hour silent prod
   outage** was found and recovered; #24/#25 flipped GREEN with live evidence.
2. **2026-07-30 (earlier)** — Lane **L23** built and merged (PR #65): Review and Editor became
   standalone tools; style-preference distillation now feeds scoring + DNA briefs.
3. **2026-07-30 (this session)** — owner asked for an honest product/market read: *"how does this look,
   is it worth the hype, how do we make it genuinely desirable."* Reviewed the docs, the code, the four
   product screenshots, and ran live market research. Finding: **the engineering is strong and the moat
   is real, but the product sells the commodity layer — and that layer is about to be free.** Filed as
   Lane **L24**, plus a positioning decision in DECISIONS.

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Prod | `autoclip.studio` — droplet `147.182.136.107` (`ssh creatorclip-vm`), `/opt/autoclip`, prod compose file |
| Prod DB head | **0049** (0048 `video_feedback`, 0049 `creator_style_notes` applied via the deploy pipeline) |
| Branch / HEAD | `main` @ `4ebb76b`, level with `origin/main` |
| New lane | `docs/issues.md` → `## Lane L24 — Positioning & Moat Surfacing` (`L24_MOAT_POSITIONING`), **Issues 374–383** |
| Lane wave order | #382, #383, #374 (W0) · #375, #376 (W1) · #377, #378, #379, #380 (W2) · #381 (W3) |
| Gating issue | **#382** — owner scope-freeze decision; the lane is unfunded until it's made |
| Highest-leverage build | **#374** Proof of Lift — read-model over `ClipOutcome ⋈ ClipPublication ⋈ Clip` |
| Launch tracker | `docs/GO_LIVE.md` — Stage A: 15 GREEN · 6 CODE-GREEN · 11 OPEN; hard blockers **#26 → #28**, plus **#282** |
| Beta cap | Google OAuth app is **In production, External, 1 / 100 user cap** |
| Credentials | By name only: `TOKEN_ENCRYPTION_KEY`, `JWT_SECRET_KEY`, `.env` on the VM — off-box escrow still OPEN (#255) |

## CONSTRAINTS & GOTCHAS

- **This session wrote zero code.** All four modified files are docs. Don't look for a feature branch.
- **#382 gates L24.** Starting #374 before the scope call reintroduces exactly the breadth problem the
  lane was filed to fix.
- **#374's empty state IS the feature at launch** — beta creators will have ~zero published clips for
  weeks. Design the empty/small-N state first, not last, or the panel ships dead.
- **#375 and #378 make claims about a third party.** #375 speaks about YouTube's enforcement policy;
  #378 publishes metrics derived from YouTube Analytics. Both need the ToS/legal question answered in
  Phase-1 CHECK **before** any UI work — over-stating either is worse than not shipping it.
- **`docs/COMPETITIVE_RESEARCH.md` is now marked STALE** in its header (refresh = #383). Don't cite its
  pricing table or its "table stakes" section without re-verifying.
- **This box has no Docker/Postgres** — the unit lane mocks the DB by design; integration runs in CI.
- **Any merge to `main` = prod deploy.** Owner authorizes.
- Local toolchain: `.venv/bin/*` only; visual baselines only via the CI dispatch flow;
  `gh pr edit` GraphQL bug → use `gh api repos/.../pulls/N -X PATCH`. CI also runs
  `ruff format --check` (the local Layer-0 script does not) — run it before pushing.

## POINTERS

| Doc | Purpose |
|-----|---------|
| `docs/issues.md` | Work queue — **Lane L24 (374–383) is at the end of the file**; L23 (369–373) DONE |
| `docs/PROJECT_STATE.md` | Top entry = the L24 filing + findings; below it, L23 complete |
| `docs/DECISIONS.md` | **2026-07-30 positioning entry** (last in file) — all sources + the four reconciliations |
| `docs/GO_LIVE.md` | Canonical launch scorecard — Stage A/B gate status |
| `docs/COMPETITIVE_RESEARCH.md` | Strategic reference — **header now flags what's stale** (#383) |
| `docs/SOT.md` | Stack/schema/structure |
| `docs/COMPLIANCE.md` | YouTube ToS, data classes, retention |
| `docs/OFF_COURSE_BUGS.md` | 2026-07-30 pytest-advisory chore |
| Memory dir | `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` |
