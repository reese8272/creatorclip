# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-07-30 (late — all-waves execution run: **W0–W4 complete, 14 issues closed**)
**Branch at close:** `wave/l24-and-hygiene` @ `c58963c` — **26 commits ahead of `origin/main`, 0 behind**
**Working tree:** **clean** (nothing uncommitted, nothing untracked)
**Upstream:** **none — this branch has never been pushed.** Nothing from this session is on GitHub or in prod.
**Prod:** untouched this session. Last deploy is still run `30561760945` (2026-07-30 16:30 UTC, main); prod DB head **0049**.

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source of truth.

---

## CURRENT FOCUS

Executing **every code-closeable issue** in waves. Scope agreed at session start: *Buckets A+B* (everything
code can close), **per-wave batched approval**, **parallel subagent waves**. W0–W4 are done and merged to
`wave/l24-and-hygiene`. **W5 is the only build work left: Issues #355 and #380.**

### → NEXT ACTIONS

1. **Decide what happens to the 26 commits.** They are local-only. Nothing is on `origin`, nothing is deployed.
   ```bash
   git log --oneline origin/main..HEAD          # review all 26
   git push -u origin wave/l24-and-hygiene       # then open a PR
   ```
   ⚠️ **Merging to `main` triggers the prod deploy pipeline.** Owner authorizes — do not merge unprompted.
   This branch changes a live public surface (`/` now serves a landing page) and a live response header (CSP).

2. **Finish W5 (the last 2 buildable issues).** Both were deliberately sequenced last:
   - **#355 — first-run information architecture.** Nav vocabulary vs page names, three overlapping
     "ask the AI" surfaces, no single primary CTA, dead-end empty states. It restructures navigation
     **across everything W1–W4 added** (landing, Proof of Lift, Originality, Fingerprint, shortlist),
     which is exactly why it is last — doing it earlier meant doing it twice.
   - **#380 — `[DEC]` pricing re-evaluation.** **Already narrowed** by #383's live pricing check: two of its
     three premises are unsupported (see WHAT WORKS NOW). Remaining question is **free-trial structure only**.
     This is an owner decision; the evidence pack is assembled and sitting in `COMPETITIVE_RESEARCH.md`.

3. **Clean up 8 stale agent worktrees** (they still hold the merged lane branches):
   ```bash
   git worktree list                 # 8 under .claude/worktrees/agent-*
   git worktree prune                # after confirming every lane branch is merged
   ```

4. **Operator punch-list** (nothing here is code — see `docs/GO_LIVE.md`, which is canonical):
   - **#29 Google OAuth verification is now unblocked** — #376a shipped the homepage Google's review requires.
   - **#26** add the friend's Gmail as an OAuth test user → then **#28** friend smoke.
   - **#282** uptime monitor — still OPEN and beta-critical (a 31h silent outage on 2026-07-29 proved the gap).
   - **`MAILING_ADDRESS`** — unset, so **all lifecycle email is intentionally skipped** (see #246 below).

---

## WHAT WORKS NOW (don't re-investigate)

**Test + gate state, all measured in the real checkout at `c58963c`:**

| Gate | Value | Note |
|---|---|---|
| Backend pytest | **2480 passed, 64 skipped, 173 deselected** | was 2377/64 at session start → **+103 tests** |
| Frontend vitest | **337 passed** (51 files) | was 309 → **+28 tests** |
| ruff / mypy | **0 / 0** | |
| coverage | **83.57%** (baseline 83.00) | app code only — see the Layer-0 fix below |
| module floors | clip_engine 92.54 · preference 90.45 · crypto/limiter/auth 100.0 | **all 5 enforce now**; 2 of 5 previously enforced nothing |
| bandit | 0 high / 0 medium | |
| pip_audit | **0** | was reporting 96 phantom vulns — see below |
| **Layer 0 overall** | **ALL GREEN** | first fully-green run of the session |

**14 issues closed this session** — `#382 #383` (W0) · `#364 #365 #366 #367 #368` (W1) · `#374` (W2) ·
`#375 #376a #246` (W3) · `#377 #378 #379` (W4). Status lines in `docs/issues.md` are current.

### Verified findings — do NOT re-derive these

**Three L24 claims were fabricated or wrong, and are retracted (#383).** They were headed into creator-facing
copy via #375/#378:
1. **There is NO "three-strike ladder"** for YouTube's inauthentic-content policy. No primary source describes
   one; YouTube describes enforcement as ranging from limiting ad earnings to terminating monetization.
2. **The policy rename was 15 July 2025**, not 16 July 2026 (that was a *clarification* into three categories).
   It is ~13 months old — all "new policy"/urgency framing is wrong.
3. **The two OpusClip quotes do not exist** in `opus.pro/blog/short-form-video-strategy-2026` nor the adjacent
   post. **Never publish them.** Checkable substitutes are recorded in `COMPETITIVE_RESEARCH.md`.

**The YouTube-native threat is real but far narrower than the L24 filing implied.** Studio's Video Clips is
**16:9 only and cannot generate Shorts**; AI suggestions are **podcast-playlists, English, 10 countries**;
Shorts integration is **announced, not shipped**. The filing also conflated two opposite changes — the
*viewer-facing* Clips feature is being **discontinued**.

**Publishing pooled cross-creator metrics is ToS-prohibited (#378).** Developer Policies **III.E.2** bars
aggregating API Data across channels not under a common content owner *and* requires any permitted aggregate
stay "viewable only by that content owner"; **III.E.4.h** bars derived metrics. **Creator consent cannot cure
it** — it is a Google-to-us term. This does **not** constrain #374, which shows a creator their own data
(expressly permitted by **III.E.3.b**). Same clause constrains #379's shareable artifact.

**Pricing: two of #380's three premises are unsupported.** Live-checked 2026-07-30 — AutoClip's per-minute rate
**beats OpusClip at every tier** (9.0 ¢/min Starter vs ~10.0; 4.0 ¢/min Stream ≈ 2.5× cheaper), and
per-input-minute is **confirmed category-standard**. The one real gap: **60 free minutes once vs 60/month
recurring** at both Opus and Vizard — a trial-design question needing **no** `MinuteDeduction` change.

**The tracker overstated remaining work four times.** Verify before building:
- **#382**'s entire premise was stale — everything it proposed parking (#322–#325, L23 effects) had already shipped.
- **#366** claimed "~20 files"; the real scope was **10 sites / 8 files**.
- **#246** was listed as an open `M` build; it is **fully built and tested** — blocked only on an operator value.
- The **"99 done / 614 open"** figure was an acceptance-criteria **checkbox** miscount. Real: **205 headings,
  167 closed, 38 open** — of which only **2 (#355, #380) are buildable**; the other 36 are external, DESCOPED-BETA,
  or code-complete-pending-verification.

### Two Layer-0 gates were measuring the wrong thing (both fixed)

1. **`pip_audit` audited the wrong Python interpreter.** `_have()`/`_run()` resolved tools via `PATH`, where
   `pip-audit` is `~/.local/bin/pip-audit` (shebang `#!/usr/bin/python3`) — so it audited the **system Python
   (200 deps / 103 vulns)** instead of the venv (**171 deps / 1 vuln**, already in `PIP_AUDIT_IGNORES`).
   **The 2026-07-29 "venv staleness" diagnosis of this same symptom was wrong and is retracted.**
   Fixed: Python tools now run via `sys.executable -m <module>`. 96 → **0**.
2. **The #368 single-root `--cov .` switch silently widened the coverage denominator**, sweeping in operational
   tooling never previously measured (`llm_harness.py` 0%, `drills.py` 0%, `run_layer0.py` 22%). That — not
   feature code — is why the gate failed at 76.39. Fixed by adding `scripts/*` and `.claude/*` to
   `[tool.coverage.run] omit`. **True app coverage is 83.5%**, higher than any prior baseline.

---

## THE ARC THAT LED HERE

1. **2026-07-29** — "100% ready" pass; a **31-hour silent prod outage** found and recovered; #24/#25 flipped GREEN.
2. **2026-07-30 (early)** — Lane **L23** built and merged (PR #65): Review and Editor became standalone tools.
3. **2026-07-30 (mid)** — owner-requested product/market review filed Lane **L24** (Issues 374–383). Zero code.
4. **2026-07-30 (this session)** — owner asked to run **all remaining issues in waves, end to end**. Scoped to
   the code-closeable set, ran **W0–W4** (2 docs + 12 build issues), caught 3 fabricated market claims before
   they shipped, found and fixed 2 broken production-readiness gates, and closed 14 issues.

---

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Branch / HEAD | `wave/l24-and-hygiene` @ `c58963c` — **26 ahead of `origin/main`, unpushed** |
| Prod | `autoclip.studio` — droplet `147.182.136.107` (`ssh creatorclip-vm`), `/opt/autoclip`, prod compose file |
| Prod DB head | **0049** — **this branch adds no migrations** |
| Last CI/deploy | run `30561760945`, success, 2026-07-30 16:30 UTC (pre-dates this session) |
| Lane branches (merged, safe to prune) | `w1/issue-364-discard` `w1/issue-365-fonts` `w1/issue-366-test-hygiene` `w1/issue-367-redis-lifecycle` `w3/issue-375-originality` `w3/issue-376-landing` `w4/issue-377-shortlist` `w4/issue-379-fingerprint` |
| Stale worktrees | 8 under `.claude/worktrees/agent-*` — `git worktree prune` |
| New public surface | `GET /` → `static/landing.html` for anonymous visitors (authenticated flow unchanged) |
| New endpoints | `POST /clips/{id}/clean/discard` · `GET /creators/me/insights/lift` · `GET /creators/me/insights/originality` · `POST /creators/me/fingerprint/share` |
| New config keys | `ORIGINALITY_SIMILARITY_THRESHOLD` (0.92) · `ORIGINALITY_MIN_CLUSTER_SIZE` (4) · `ORIGINALITY_RECENT_CLIPS_WINDOW` (12) · `SHORTLIST_SIZE` (3) — all in `.env.example` |
| Beta cap | Google OAuth app **In production, External, 1 / 100 user cap** |
| Credentials | By name only: `TOKEN_ENCRYPTION_KEY`, `JWT_SECRET_KEY`, `MAILING_ADDRESS`, `.env` on the VM — off-box escrow still OPEN (#255) |
| Launch tracker | `docs/GO_LIVE.md` — canonical; now carries the CAN-SPAM `MAILING_ADDRESS` gate |

---

## CONSTRAINTS & GOTCHAS

- **Nothing from this session is pushed or deployed.** 26 local commits. Any merge to `main` = prod deploy.
- **This branch changes two live surfaces**: `/` now serves a public landing page, and the **CSP no longer
  allow-lists Google Fonts** (both font sources are self-hosted now). Worth a live smoke after deploy.
- **`ORIGINALITY_SIMILARITY_THRESHOLD=0.92` is UNVALIDATED.** No Voyage key, no pgvector, no real clip corpus
  here, so voyage-3.5's actual same-channel cosine distribution was never measured. It is deliberately high
  (few alarms) — **a silent advisory currently means "unmeasured", not "clean"**. Recalibration recipe is in
  `docs/OFF_COURSE_BUGS.md`.
- **`MAILING_ADDRESS` is unset and that is correct.** `config.py:752` makes `send_notification` **skip every
  lifecycle email** while it is empty — CAN-SPAM requires a valid physical postal address on commercial mail.
  **Do not "fix" this in code.** Whatever address is set becomes public in every lifecycle footer.
- **Agent-reported test counts from worktrees are unreliable.** Every agent this session over-reported skips by
  ~6. **Root cause found:** agent worktrees have no built `frontend/dist`, so SPA-dependent tests skip. Always
  re-run in the main checkout. Every number in this file was measured there.
- **Integration tests cannot even *collect* on this box** — a conftest guard aborts without Postgres. New
  integration tests (`test_lift_integration.py`, `test_originality_integration.py`) are **CI-verified only**;
  their enum/field names were checked against `models.py` programmatically instead.
- **This box has no Docker, Postgres, ffmpeg, or live API keys.** The unit lane mocks the DB by design.
- Local toolchain: `.venv/bin/*` only. **CI also runs `ruff format --check`**, which the local Layer-0 script
  does not — run it before pushing.
- **`docs/issues.md` #194/#195 have no `Status` line of their own**, so automated status scans walk into a
  neighbouring issue's block. Logged in `OFF_COURSE_BUGS.md`; the same gap hid #374's missing status this session.

---

## POINTERS

| Doc | Purpose |
|-----|---------|
| `docs/issues.md` | Work queue — **38 open, only #355 + #380 buildable** |
| `docs/PROJECT_STATE.md` | Progress log — top entries are this session's waves |
| `docs/DECISIONS.md` | **8 new entries today** — the #383 retractions, #382 scope freeze, #368 + Layer-0 gate fixes, #378 ToS block, #379 two-tier design, #246 CAN-SPAM, #376b descope |
| `docs/GO_LIVE.md` | Canonical launch scorecard — **#29 now unblocked by #376a** |
| `docs/COMPETITIVE_RESEARCH.md` | **Refreshed + corrections section** — read that section before citing anything |
| `docs/COMPLIANCE.md` | YouTube ToS, data classes, retention; #379's field-provenance audit |
| `docs/OFF_COURSE_BUGS.md` | 3 new rows today (originality threshold, CSP — now fixed, #194/#195 tracker gap) |
| `docs/SOT.md` | Stack / schema / structure |
| Memory dir | `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` |
