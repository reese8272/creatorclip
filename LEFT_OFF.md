# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-07-31 (W5 close-out — **the code-closeable backlog is empty**)
**Branch at close:** `wave/l24-and-hygiene` — **pushed**, PR **#67** open against `main`
**Working tree:** clean · **Worktrees:** none (all 8 removed) · **Stale branches:** 21 deleted

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source of truth.

---

## WHERE THIS LANDED

**Every issue in `docs/issues.md` that code can close is now closed.** W5 finished the last two
(#355, #380). What remains is external (Google review, operator clicks), DESCOPED-BETA (the K8s/scale
lanes), or code-complete-pending-live-verification. There is no buildable work left in the tracker.

**16 issues closed across W0–W5.** W0–W4 were a previous session; W5 is #355 + #380.

### → NEXT ACTIONS

1. **PR #67 → merge → prod.** Merging fires **Docker publish → Deploy to production**, which runs a
   **staging-parity gate first** (Issue 298: migration + critical-journey smoke on the persistent
   `ccstage` stack) before prod. **No migrations in this branch — prod DB head stays `0049`.**
2. **Live smoke — two live surfaces change:**
   - `GET /` serves `static/landing.html` to anonymous visitors; authenticated users still 302 to
     `/app/dashboard`. Verify **both** paths (the branch point is a best-effort JWT cookie decode,
     `main.py:211`).
   - **CSP no longer allow-lists Google Fonts** — `style-src 'self' 'unsafe-inline'`, `font-src
     'self'` (`main.py:330`). Confirm fonts render and the console shows no CSP violation.
   - Spot-check: `POST /clips/{id}/clean/discard` · `GET /creators/me/insights/lift` ·
     `GET /creators/me/insights/originality` · `POST /creators/me/fingerprint/share`.
3. **Operator punch-list** (no code can close these — `docs/GO_LIVE.md` is canonical):
   - **#29 Google OAuth verification** — unblocked since #376a shipped the homepage Google requires.
   - **#26** add the friend's Gmail as an OAuth test user → then **#28** friend smoke.
   - **#282** uptime monitor — still OPEN and beta-critical (a 31h silent outage on 2026-07-29 proved
     the gap).
   - **#255** off-box key escrow.
   - **`MAILING_ADDRESS`** is unset and that is CORRECT — `config.py:752` makes `send_notification`
     skip every lifecycle email while it is empty, because CAN-SPAM requires a real postal address on
     commercial mail. **Do not "fix" this in code.** Whatever is set becomes public in every footer.

---

## WHAT WORKS NOW (don't re-investigate)

| Gate | Value |
|---|---|
| Backend pytest | **2480 passed, 64 skipped, 173 deselected** |
| Frontend vitest | **354 passed** (55 files) — was 337 at W5 start |
| ruff / ruff format / mypy | 0 / clean / 0 |
| coverage | **83.51%** (baseline 83.00) |
| module floors | clip_engine 92.54 · preference 90.45 · crypto/limiter/auth 100.0 |
| bandit / pip-audit | 0 high, 0 medium / 0 |
| tsc / eslint / vite build | clean / 0 errors / clean |
| **Layer 0** | **ALL GREEN** |
| **CI (PR #67)** | 12/12 after the baseline regen |

### Verified findings — do NOT re-derive

**#380 is decided and closed: the free trial stays one-time 60 min / 7 days.** The comparison that
motivated the issue was never like-for-like — **Opus's and Vizard's "60 min/month" free tiers are
watermarked with 3-day storage** (`docs/COMPETITIVE_RESEARCH.md:63`), where our 60 trial minutes are
full-fidelity and permanently stored. Matching the number unrestricted = 720 min/yr, **3.6× the whole
Starter pack**, and a one-upload-a-month creator never converts. 2026 benchmarks: opt-in time-boxed
trials convert **8.9–25.2%** vs freemium **2–8%**; the category moved to trials (57%) over freemium
(26%) on AI-cost grounds. **COGS is NOT the argument** — a recurring grant costs ~$0.35–0.45 per
creator per month, under $45/mo at the full 100-user cap. The issue brief's risk (2) overstated this;
do not re-cite it. Runner-up **option (d), a watermarked recurring tier**, is deferred with an
explicit re-open trigger (signups open past the 100-user cap, or conversion drop-off at Starter).

**Three L24 claims were fabricated or wrong and are retracted (#383).** No YouTube "three-strike
ladder" exists; the policy rename was **15 July 2025**, not 2026 (so all urgency framing is wrong);
and the two OpusClip quotes **do not exist** — never publish them.

**The YouTube-native threat is real but narrower than the L24 filing implied.** Studio's Video Clips
is **16:9 only and cannot generate Shorts**; AI suggestions are podcast-playlists/English/10
countries; Shorts integration is announced, not shipped.

**Publishing pooled cross-creator metrics is ToS-prohibited (#378).** Developer Policies III.E.2 +
III.E.4.h; **creator consent cannot cure it** — it is a Google-to-us term. Does not constrain #374
(a creator seeing their own data, III.E.3.b).

---

## CONSTRAINTS & GOTCHAS

- **Visual baselines MUST be regenerated on the CI runner**, never locally — WSL2 font anti-aliasing
  differs from ubuntu-latest. Path: `gh workflow run ci.yml --ref <branch> -f update_snapshots=true`,
  then `gh run download <id> -n visual-baselines-<sha>`. Copy only the PNGs that actually changed and
  verify the rest are byte-identical (W5 did: only the 2 `empty-dashboard` files moved).
- **A failed visual-regression run uploads no diff artifact** — you cannot see what changed from the
  run alone. Logged in `OFF_COURSE_BUGS.md`; workaround is the regen-and-compare above.
- **The unit lane needs a live Redis** (the rate limiter has no in-memory fallback):
  `redis-server --daemonize yes --save '' --appendonly no`.
- **Integration tests cannot collect on this box** (a conftest guard aborts without Postgres) — they
  are CI-verified only. They passed on PR #67.
- **`ORIGINALITY_SIMILARITY_THRESHOLD=0.92` is UNVALIDATED** — no Voyage key/pgvector/clip corpus here,
  so the real same-channel cosine distribution was never measured. A silent advisory currently means
  "unmeasured", not "clean". Recipe in `docs/OFF_COURSE_BUGS.md`.
- **Agent-reported test counts from worktrees are unreliable** — worktrees have no built
  `frontend/dist`, so SPA-dependent tests skip. Every number above was measured in the main checkout.
- **CI also runs `ruff format --check`**, which the local Layer-0 script does not.
- **`docs/issues.md` #194/#195 have no `Status` line**, so automated status scans walk into a
  neighbouring issue's block and miscount. Logged.

---

## OPEN, LOGGED, NOT FIXED (from W5)

Three defects found outside #355's scope and deliberately left in `docs/OFF_COURSE_BUGS.md` rather
than fixed inline:

1. **Review-queue badge counts rendered clips, not shortlisted-unreviewed ones** — so it disagrees
   with the queue post-#377. Needs a new field on `/videos/clips/counts` (a backend change #355 was
   scoped to avoid). #355 fixed only the zero state.
2. **`App.tsx`'s `*` route silently redirects typos to `/dashboard`** — no 404, no index route.
3. **The visual-regression diff artifact never uploads** (above).

---

## POINTERS

| Doc | Purpose |
|-----|---------|
| `docs/issues.md` | Work queue — **no buildable issues remain** |
| `docs/GO_LIVE.md` | Canonical launch scorecard — **#29 unblocked by #376a** |
| `docs/PROJECT_STATE.md` | Progress log — top entry is W5 |
| `docs/DECISIONS.md` | Top entries: #380 free trial, #355's two structural calls |
| `docs/COMPETITIVE_RESEARCH.md` | **Read the corrections section before citing anything** |
| `docs/OFF_COURSE_BUGS.md` | Logged-not-fixed defects |
| `docs/COMPLIANCE.md` · `docs/SOT.md` | ToS/retention · stack, schema, structure |
| Memory dir | `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` |
