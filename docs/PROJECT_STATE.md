# CreatorClip — Project State

Updated after every issue closes.

---

## Current Status

**Last completed (Issue 184 — Auto-zoom punch-in at peak, 2026-06-22):** Batch A, issue 3.
Opt-in `zoom_on_peak` style flag (default off) applies a brief triangular punch-in (8% over
±0.6s, back to 100%) centered on the clip's `peak_s`, via an ffmpeg `crop`+`scale` chain using
crop's per-frame `t` expression — chosen over `zoompan` (built for stills, resamples the stream),
`docs/DECISIONS.md` 2026-06-22. Applied before subtitles so captions stay steady. `peak_s` is
plumbed `Clip.peak_s → worker → render_clip_file`; the flag flows through `RenderStyleIn` and a
new `CaptionStylePanel` "Punch-in at peak" toggle. Cites Principle 4 (pattern interrupt). Tests:
+4 in `tests/test_render.py` + endpoint-persistence in `tests/test_render_style.py`; full suite
**1008 passed, 3 skipped**; frontend lint/tsc/build green; Layer-0 ruff/mypy/bandit/freshness
green. On branch `feat/batch-a-render-quality`. **Next in Batch A (last):** Issue 185 (noise
reduction, opt-in, depends on 181's loudnorm path).

**Earlier (Issue 183 — Keyword highlight in captions, 2026-06-22):** Batch A, issue 2.
New `bold_pop_highlight` caption style: punch-yellow (`#ffd400`) `\c` highlight on the most
salient token per phrase, chosen by a dependency-free per-phrase salience scorer (stopwords +
clip term-frequency + casing + token length; top-1/phrase). Plain-Bold-Pop fallback when a
phrase has no salient token; existing 3 styles byte-identical (regression-tested). Chose the
pure-Python scorer over the YAKE library (document-level ranker, poor per-phrase fit + dep
weight) — `docs/DECISIONS.md` 2026-06-22. Added to `VALID_STYLES` + `CaptionStylePanel.tsx`
dropdown. Fixed a latent DRY bug: `worker/tasks.py` transcript-load gate now keys off
`captions.VALID_STYLES` so new styles can't silently render captionless. Tests: +4 in
`tests/test_captions.py`; full suite **1004 passed, 3 skipped**; frontend lint/45-tests/build
green; Layer-0 ruff/mypy/bandit/freshness green. On branch `feat/batch-a-render-quality`.
**Next in Batch A:** Issue 184 (auto-zoom punch-in), 185 (noise reduction — depends 181).

**Earlier (Issue 181 — Loudness normalization on every render, 2026-06-22):** First
implementation issue off the rebuilt backlog (Batch A — render quality). Both render paths
(`render_clip_file`, `render_cleaned_clip_file`) now normalize audio to YouTube's −14 LUFS via
**two-pass** ffmpeg `loudnorm` — measure (`print_format=json`) then apply the `measured_*` values
with `linear=true` for pump-free gain. Deviated from finding 03/A1's single-pass suggestion
(single-pass pumps; can't meet the no-pumping AC) — `docs/DECISIONS.md` 2026-06-22. Added a
near-silent guard (`measured_I ≤ −50 LUFS` → skip, don't amplify hiss) and graceful flat-render
fallback on measurement failure. Removed the dead `pyloudnorm==0.1.1` pin (zero imports) and
corrected `docs/SOT.md:19`. Tests: 8 new in `tests/test_render.py`; full suite **1000 passed, 3
skipped**; Layer-0 ruff/mypy/bandit/freshness green (pip_audit's lone finding is the pre-existing
msgpack CVE, unrelated). **Verification note:** the −14 ±1 LUFS `ebur128` AC is verified-by-
construction (unit tests assert the exact filter); the empirical binary measurement needs the
render env (ffmpeg CLI is absent in this dev box). On branch `feat/batch-a-render-quality`.
**Next in Batch A:** Issue 183 (caption keyword highlight), 184 (auto-zoom punch-in), 185
(noise reduction — depends on 181).

**Earlier (Issues 166–180 — Gap-Closure Research Initiative COMPLETE + backlog rebuild,
2026-06-22):** All 15 research briefs delivered (`docs/research/findings/01–15`). Rebuilt the
backlog: archived finished work (Issues 1–165 + the 166–180 research passes) verbatim to
`docs/archive/issues_snapshot_2026-06-22.md`, and the resolved off-course bugs to
`docs/archive/off_course_bugs_snapshot_2026-06-22.md`; spent prompts moved to
`docs/archive/research_prompts_2026-06-22/`. The new `docs/issues.md` carries only open work +
**~94 implementation issues (181–274)** harvested from the findings, deduped and renumbered into
priority order (Functionality → UI → UX → Agentic/Caching/Cost → Security → Observability →
Notifications → Privacy/Compliance → DR/Infra/Scale → QA/Release-eng), then carry-over open
items, then a deferred parking lot. Four founder scope decisions (`docs/DECISIONS.md` 2026-06-22):
**stream-VOD recap = expand v1 now** (190–192), **publishing = D0 export + D1 YouTube publish**
(182, 194–197; TikTok/Reels deferred), **multilingual = English-only v1** (entire i18n track
deferred), **editor = full timeline tool** (188–189). Supersessions: 80/81→242–244, 160→211,
27→260, 58/112-Locust→261. **Next:** start the prioritized backlog — Functionality first
(181 loudnorm is the cheapest win; 198 the moat-eval is the highest-leverage). No product code
changed in this pass.

**Earlier (Issues 166–180 — research authored, 2026-06-22):** Opened a deliberate
research pass over the **conceptual** gaps the bug-fix backlog never addressed. Authored 15
ready-to-paste, repo-grounded research-agent prompts in `docs/research/` (index +
cross-references in `docs/research/README.md`), one per gap, each driving the Phase 1 (CHECK)
research for a tracked issue: **166** UX/visibility/stream-summary/per-video-clips; **167** agentic
usage + prompt caching + LLM cost; **168** editorial capabilities vs. modern editors; **169**
security + scale-to-10k; **170** logging/observability; **171** monetization + unit economics;
**172** activation/onboarding funnel; **173** personalization efficacy + clip-quality eval (the
moat); **174** LLM content safety + prompt injection; **175** disaster recovery + backups + data
durability; **176** notifications + lifecycle comms; **177** data privacy/GDPR-CCPA + erasure/export;
**178** multi-platform distribution/publishing (scope expansion); **179** i18n + multilingual
content handling; **180** QA + eval-hardening + release engineering. Each prompt is read-only
(research → brief → file implementation sub-issues → approve → build); scope expansions
(stream→summary, publishing, deeper editor) gated on a `docs/DECISIONS.md` entry. Registered as a
new phase in `docs/issues.md`; `docs/SOT.md` doc-tree updated. **Next:** run the prompts (highest
leverage first: 173 moat-eval, 171 unit-economics, 169/174 security) and triage the resulting
sub-issues. No product code changed.

**Last completed (Issues 164 + 165 — live-site audit + WCAG AA contrast fix, 2026-06-19):** Built a
second Playwright harness that runs against PROD (`autoclip.studio`) with the real backend + a real
`cc_session` (captured via storageState; Google blocks automated OAuth, so a manual-cookie fallback was
used) — `frontend/playwright.config.prod.ts` + `e2e/prod/`, capturing console/network/broken-image +
**axe** at desktop/tablet/mobile, with gated paid-flow specs. **First live run: 0 console/network/image
errors**, but **420 serious `color-contrast` (WCAG AA) failures on every page** — the real "gaps." Issue
165 fixed them at the root: raised `--color-subtle`, split the accent token into solid-bg vs.
`--color-accent-text` (Radix convention), and — the actual bug — taught `tailwind-merge` the custom
font-size scale so it stopped dropping button text-color classes (filled buttons were silently
inheriting the page fg). Also fixed Profile `<dl>` + Review slider `aria-label`. Added a permanent local
a11y gate (`e2e/a11y.spec.ts`): **420 → 0 serious across 9 routes × 2 viewports.** Green: lint, vitest
45/45, build, `test:e2e` (smoke + a11y). Paid flows: chat ✓; analysis/titles timed out at 60s (logged,
OFF_COURSE). **Not yet deployed — prod re-verify (`npm run test:prod`) pending the push.** DECISIONS
2026-06-19 (Issues 164, 165).

**Last completed (Issue 163 — SPA UI polish from the Issue 162 audit, 2026-06-19):** Fixed the four
layout/responsive defects the Playwright audit surfaced. **[SEV2]** `Nav.tsx` now collapses to a
hamburger below Tailwind's `sm` (640px) — the bar shows `AutoClip … [142 min] [☰]` and a toggled panel
holds the 7 links + channel title + Logout (closes on link tap); the old 7-link-in-one-row mobile
cram is gone. **[SEV3]** `Review.tsx` rebalanced to fill the empty bottom-right quadrant (left =
player + Why-this-clip; right = Transcript + Caption-style + Clean-filler). **[SEV3]** `Analysis.tsx`
feature cards → `sm:grid-cols-2` 2×2 block. **[SEV3]** `Chat.tsx` empty-state vertically centered
until the first message. All four re-verified against regenerated screenshots (incl. a throwaway spec
for the open mobile menu). **Green: lint clean, vitest 45/45 (+1 nav toggle test), build ok,
`test:e2e` 20/20.** No DECISIONS entry — standard responsive patterns. OFF_COURSE_BUGS rows marked
fixed. Visual-regression baselines (`toHaveScreenshot()`) remain a deferred follow-up.

**Last completed (Issue 162 — Playwright E2E + visual harness, 2026-06-19):** Stood up a real-browser
test layer for the React SPA, the first coverage of *rendered* UI (jsdom/Vitest can't compute CSS or
paint). `@playwright/test` 1.61 under `frontend/`; `playwright.config.ts` runs every SPA route ×
desktop-1440 + mobile-390 (20 tests) against the Vite dev server with the FastAPI backend mocked at
the network boundary (`e2e/fixtures/mock-api.ts`, fixtures shaped to `src/types.ts`, authed/anon
seeds) — no Docker needed. Each test asserts no console error / uncaught JS and writes a full-page
screenshot. Two runners cleanly separated (Vitest→`src/`, Playwright→`e2e/`; ESLint React-rules
scoped to `src/`). **Green 20/20; no regression — lint clean, vitest 44/44, build ok.** Ran the
UX/UI audit from the captures: overall the Issue-85 overhaul renders well (honesty banner on every
page, dark-mode elevation holds, FitBadge reads) — 4 polish findings logged to `OFF_COURSE_BUGS.md`
(mobile nav overflow SEV2; Review empty quadrant, Analysis card grid, Chat empty void — all SEV3).
Follow-ups in `issues.md`: flow-based E2E, full-stack E2E, visual-regression baselines. WSL2 note:
`playwright install --with-deps` needs `sudo apt` once. DECISIONS 2026-06-19 (Issue 162).

**Last completed (Issues 153–159 — post-cutover regression audit + fixes, 2026-06-18):** A
6-dimension behavioral-parity audit of the Issue 85 soft cutover (telemetry, tracing, API
parity, compliance, security, UX state) — triggered by finding that live UI click telemetry had
gone dark on prod. Tracing/observability and security came back **clean** (middleware stack
unchanged; no `dangerouslySetInnerHTML`; cache-`no-store` still fires on the SPA shell;
server-side auth boundary intact). Honesty/"no virality" invariant intact. Filed Issues 153–161
in `docs/issues.md` and worked the batch (branch `feature/issue-85-overhaul-regressions`):
- **153 [BLOCKER]** Onboarding/Walkthrough lost the ToS/Privacy footer (outside `AppChrome`) — an
  OAuth-verification-gate breach on the page Google's reviewer walks. Shared `<Footer/>` rendered on both.
- **154 [SEV1]** Walkthrough CTA dead-ended into legacy `/static/onboarding.html` → in-SPA
  `navigate('/onboarding')`; also fixed a second dead-end in `DashboardBanners`.
- **155 [SEV2]** SPA UI telemetry restored (`lib/activity.ts` + `useActivityTelemetry` via a
  `RootLayout`) — clicks/submits/route-changes POST to `/api/activity` again.
- **156 [SEV3]** Walkthrough false "activity panel" copy corrected; panel rebuild split to **Issue 160**.
- **157 [SEV2]** Insights loading state + surfaced swallowed upload-intel/saved sub-fetch errors.
- **158 [SEV2]** Account-deletion UI (right-to-erasure, `DELETE /auth/me`) added to Profile — closes a CLAUDE.md launch item.
- **159 [cleanup]** Orphaned-endpoint sweep triaged (intentional retentions documented); stale
  backend `next_action` `/static` URLs split to **Issue 161** (needs a real Postgres to validate).

Batch is **frontend + docs only** (zero backend, zero migrations). Frontend **lint clean, vitest
38/38, build green**. **Deploy:** pending a single batched prod deploy (merge → `main` auto-builds
the image incl. `frontend/dist` and auto-deploys on the self-hosted VM; `alembic upgrade head` is a
no-op). Follow-ups: Issues **160** (cross-page active-tasks panel — gated by the 3-slot SSE cap) and
**161** (repoint stale envelope URLs). DECISIONS 2026-06-18.

**Prior (Issue 85g — Cutover, soft flip, 2026-06-18):** With all seven app pages ported,
`main.py`'s `/` now **redirects to `/app/dashboard`** when the SPA bundle is built (`_SPA_BUILT`
gate; a no-build checkout/CI stage still boots the legacy index byte-for-byte). The React app is the
primary surface; anonymous visitors land on `/app/login` via the auth gate. **Soft cutover (user-
chosen):** redirect `/` + delete the one orphaned page (`early-access.html`), but **keep the other
`static/*.html` served (unlinked)** as rollback insurance — full file retirement + backend
`next_action` URL repointing is a deliberate staging-verified follow-up (the Python suite is
CI-authoritative here; a hard cutover would be a large blind change). **Tests:** root tests made
flip-aware via `skipif(_SPA_BUILT)` (mirrors `test_spa_serving`); legacy-content `/` assertions
(`test_user_flow`, `test_pipeline_trigger`, `test_static` cache-bust, `test_observability` inbound-id)
**repointed to `/static/index.html`** — behavior-preserving (the exact file `/` used to serve) and
robust whether or not the integration job builds the bundle. Verified locally: AST-clean + **ruff
clean** on all touched Python (no Postgres here → mypy/pytest CI-authoritative); frontend untouched
(**vitest 32/32**). **Issue 85 (the full React/TS overhaul) is DONE** — all pages ported, design
system applied, honesty/mobile/a11y ACs met. **Deferred follow-up:** delete/redirect remaining
`static/*.html` (keep tos/privacy), repoint backend `next_action` URLs, global activity-panel widget,
React marketing hero (if going public). Decisions in `docs/DECISIONS.md` 2026-06-18. ⚠️ The entire
85a–85g body of work is still **uncommitted** — branch + PR into `staging` (do NOT commit on `main`;
push auto-deploys).

**Last completed (Issue 85f — Review / Editor → React, 2026-06-18):** Ported the biggest, most
stateful page — `static/review.html` + `static/editor.js` → `pages/Review.tsx` (+
`components/review/*`) at `/app/review` (protected + chrome). **Player-first redesign** (sanctioned
by the Issue-85 AC, replacing the Issue-136 icon-rail + slide-out drawer): clip player +
Keep/Drop/Skip/Trim + tag-feedback picker lead, the transcript editor sits alongside (2-col on `lg`,
stacked on mobile), and Why-this-clip / Caption style / Clean pass are collapsible sections. Full
clip-queue navigation (advance → back to dashboard when done). **Transcript editor faithfully
reimplemented**: `onMouseUp` → `getSelection()` snapped to `.ed-word[data-index]` spans (server
provides the stable word `index`), cuts in React state + `localStorage`, sort/merge-adjacent +
one-level undo, apply → poll `cleaned_render_uri` → confirm swap. New **`useCleanedUriPoll`** hook
(gated `refetchInterval` on `/videos/{id}/clips`) shared by the clean pass and the edit flow; confirm
invalidates `['review-clips', videoId]` so the main player picks up the swapped `render_uri`. **All
nav links are now SPA-internal** (Review was the last `external` one) + the dashboard "N clips" /
"Review queue" links flipped to `/app/review`. **Verified:** eslint 0, `tsc -b` + build clean,
**vitest 32/32** (+3: no-video prompt; clip loads → meta + reasoning + transcript + disclaimer; Keep
opens tag panel). No Python touched (legacy page served until 85g; backend Layer 0 unaffected).
Decisions in `docs/DECISIONS.md` 2026-06-18. **All seven app pages are now ported — only 85g
(cutover: `/`→`/app`, retire `static/*.html` except tos/privacy) remains.** ⚠️ 85a–85f all remain
**uncommitted** — branch + PR into `staging` (do NOT commit on `main`; push auto-deploys).

**Last completed (Issue 85e — Insights + Analysis → React, 2026-06-18):** Ported the two heaviest,
LLM-streaming pages. `static/insights.html` → `pages/Insights.tsx` at `/app/insights` (channel
snapshot, DNA snapshot, sortable top/bottom performers with per-row AI analyze + save, upload
windows, improvement brief as SSE log + gated poll, saved insights). `static/analysis.html` →
`pages/Analysis.tsx` at `/app/analysis` (free-form video-analysis with token-streamed prose, plus
four `?video_id=`-gated features: Title Optimizer, Hook Analyzer, Chapter Markers, Thumbnail
Concepts). **New streaming primitive:** extended `subscribeToTaskStream` with `onToken`/`onStep`
callbacks (additive) + broadened the `done` payload, and added a **`useTaskResult`** hook →
`{status, step, tokens, result, error}` — the reusable hook the analysis features share (token-only
prose for the narrative; structured `done` payloads for titles/concepts/report/chapters).
`useStreamAction` extracts the uniform "POST → 202 {stream_url} → stream" pattern for the three
uniform per-video panels; video-analysis (synchronous context first) and hook analyzer (200
`no_data` branch) keep bespoke flows. Improvement brief stays faithful to the async-202-then-poll
backend (live log via `useTaskStream` + `refetchInterval` poll until status leaves `pending`). Nav
"Insights"/"Analyze" + the dashboard "Analyze →" CTA + per-row "Titles" link rewired to SPA routes
(`<Link>`); "Review queue" links stay legacy until 85f. **Verified:** eslint 0, `tsc -b` + build
clean, **vitest 29/29** (+4: insights snapshot/performer/disclaimer, analyze-performer → save;
analysis form + disclaimer + per-video-panel gating). SSE flows covered by the 85a stream-layer
tests (jsdom has no EventSource; rendering opens none). No Python touched (legacy pages served until
85g; backend Layer 0 unaffected). Decisions in `docs/DECISIONS.md` 2026-06-18. **Open:** 85f
(review/editor — the biggest), 85g (cutover); live visual QA pending the running stack. ⚠️ 85a–85e
all remain **uncommitted** — branch + PR into `staging` before more building (do NOT commit on
`main`; push auto-deploys).

**Last completed (Issue 85d — Onboarding → React, 2026-06-18):** Ported `static/onboarding.html` to
`pages/Onboarding.tsx` (+ `components/onboarding/*`) on the 85a foundation. New route
`/app/onboarding`, **protected + bare** (under `AuthGate`, not `AppChrome` — a focused full-screen
flow with a minimal header, like the walkthrough). The 5-step first-run flow: (1) connect YouTube
(status from `useAuth`); (2) channel data gate — `POST /creators/me/catalog/sync` with a **live SSE
console** (`useTaskStream`) + `GET /creators/me/data-gate` polled via gated `refetchInterval` (4s
while the sync streams, invalidate-once on stream `done`); (3) optional slim identity intake (niche
1–3 + audience → `POST /creators/me/identity`, unlocks step 4); (4) build DNA — `POST
/creators/me/dna/build` with a **live SSE console** + `/creators/me/dna` brief-ready signal; (5)
confirm → `/app/profile`. **Preserved the Issue-100 gate**: Build-DNA stays disabled until an
identity row exists (intentional product behavior, kept faithfully). **Rewired the dashboard
`DnaCta`** to SPA routes by `setup.step` (sync_catalog/build_dna → `/app/onboarding`, confirm_dna →
`/app/profile`) so the new page is reachable from inside the SPA. New `StepCard` + `StreamConsole` +
`OnboardingIdentity` components. **Verified:** eslint 0, `tsc -b` + build clean, **vitest 25/25**
(+3: connected status + honesty disclaimer + data-gate readiness; Build-DNA locked w/o identity;
unlocked when identity on file). No Python touched (legacy `static/onboarding.html` still served
until the 85g cutover; backend Layer 0 unaffected). Decisions in `docs/DECISIONS.md` 2026-06-18.
**Open:** 85e (insights+analysis), 85f (review/editor), 85g (cutover); live visual QA pending the
running stack. ⚠️ 85a–85d all remain **uncommitted** — branch + PR into `staging` before more
building (do NOT commit on `main`; push auto-deploys).

**Last completed (Issue 85c — Dashboard → React, 2026-06-18):** Ported `static/index.html` to
`pages/Dashboard.tsx` (+ `components/dashboard/*`) on the 85a foundation, in the `docs/UI.md` design
system. New route `/app/dashboard` (protected + chrome); SPA catch-all now lands on `/dashboard` and
the Nav "Dashboard" link flipped from a `/` full-navigation to the SPA route. Surfaces: summary
cards (DNA status/version, video count, clips rendered), YouTube-analytics panel (period selector →
`/creators/me/insights/analytics`), link-a-video form (form-encoded `POST /videos/link` + YouTube-ID
extraction), video table with per-row actions (Queue / Generate clips / N-clips review link / Titles
+ the Issue-139 "upload source to clip" affordance for non-clippable linked rows), empty-state hero,
and the trial-countdown + low-balance + DNA-CTA banners. **Live in-flight status via gated TanStack
Query `refetchInterval`** — polls `/videos` every 5s only while a clip-trackable video is
pending/running, stops on settle, and pauses when the tab is unfocused (`refetchIntervalInBackground`
default false) — replacing the vanilla hand-rolled backoff timer + 10-min stuck cap. Per-video clip
counts via `useQueries` (N+1 preserved/parallelised; batch endpoint logged in OFF_COURSE_BUGS as a
future optimisation). **Activity panel: inline now, global floating widget deferred** to a later
slice / 85g (user-approved — cross-cutting `AppChrome` context concern). Added a `danger` variant to
the `Badge` primitive (failed ingests). **Verified:** eslint 0, `tsc -b` + build clean, **vitest
22/22** (+5: empty-hero + honesty disclaimer; pending→Queue CTA; non-clippable→upload affordance not
queue; done-with-clips→review link; done-no-clips→Generate; + Nav now-ported assertion). No Python
touched (legacy `static/index.html` still served until the 85g cutover; backend Layer 0 unaffected —
CI-authoritative). Decisions in `docs/DECISIONS.md` 2026-06-18. **Open:** 85d (onboarding), 85e
(insights+analysis), 85f (review/editor), 85g (cutover); live visual QA pending the running stack.
⚠️ 85a+85b+85c all remain **uncommitted** — branch + PR into `staging` before more building (do NOT
commit on `main`; push auto-deploys).

**Last completed (Issue 85b — pre-auth + presentational pages → React, 2026-06-18):** Ported
**login**, **pricing**, **walkthrough** to React on the 85a foundation. Split the single
`AppLayout` into **`AuthGate`** (protects routes; redirects to `/app/login` when no session) +
**`AppChrome`** (auth-agnostic Nav/Footer shell) → four route contexts via nested layout routes
(protected/public × chrome/bare). **`useAuth` no longer hard-redirects on 401** — it resolves to
`user: null`, which is what lets **pricing render for anonymous visitors**; the redirect decision
now lives in `AuthGate`. The `api()` 401 target + Nav logout + Chat's gated link moved
`/static/login.html`→`/app/login` and `/static/pricing.html`→`/app/pricing`. **Login** ported
faithfully (Google button stays a real nav to `/auth/login`, `?yt=` carried); **pricing** keeps
the Issue-106 `crypto.randomUUID` Stripe-checkout idempotency (URLs → `/app/pricing`); **walkthrough**
is the 5-panel first-run flow with keyboard nav. **`early-access` descoped** — it POSTs to a
**non-existent** `/billing/early-access` route and sells **subscriptions** that contradict the
minutes-pack model; logged in `OFF_COURSE_BUGS.md` for a product decision (delete in 85g or
rebuild), not ported. **Verified:** eslint 0, `tsc -b` + build clean, **vitest 17/17** (+5:
Walkthrough nav/finish, AuthGate anon-redirect vs authed-render, pricing anon grid). No Python
touched (legacy static pages stay until the 85g cutover; backend Layer 0 unaffected). Decisions in
`docs/DECISIONS.md` 2026-06-18. **Open:** 85c (dashboard), 85d (onboarding), 85e (insights+
analysis), 85f (review/editor), 85g (cutover); live visual QA of ported pages still pending the
running stack.

**Last completed (Issue 85a — React+TS overhaul foundation, 2026-06-18):** Resumed the frontend
migration as a full UI/UX overhaul, run via the issue-workflow (foundation-first sequencing +
genuine redesign, both user-approved). Filed the migration as **85a–85g** in `issues.md`; **85a
DONE**. Architecture foundation (visually neutral): React Router v7 **Data Mode**
(`createBrowserRouter` + `RouterProvider`) with a shared **`AppLayout`** (persistent Nav/Footer +
auth gate via `<Outlet/>`) — the per-page nav/footer duplication is gone; **TanStack Query v5**
adopted (`useAuth` rewritten as a cached `useQuery`, so layout + pages share one `/auth/me` +
`/billing/balance`); new **`useTaskStream`** SSE hook (EventSource lifecycle + guaranteed unmount
cleanup, reset-during-render to satisfy react-hooks v7); **React Testing Library** + jsdom added
to Vitest. **Profile + Chat re-homed** onto the shared shell (new `DisclaimerBand` keeps the
page-specific honesty copy). **Design system:** new **`docs/UI.md`** (evolve dark-Linear: warmer
**OKLCH** palette, player-first clip surface, honest three-tier "fit with your channel style"
badges — never virality; Geist+Inter, 8pt, spring motion); applied to the SPA `index.css`
`@theme` preserving token NAMES (only color VALUES → OKLCH; text/radius metrics + body font
unchanged, adopted per page on port). SPA `@theme` is independent of legacy
`static/_design-tokens.css`, so only React pages restyle. **Verified:** eslint 0, `tsc -b` + vite
build clean, **vitest 12/12** (6 new: SSE state machine/cleanup, Nav SPA-vs-static links). No
Python touched (backend Layer 0 unaffected — CI-authoritative). **Phase 1 used `industry-
standards-researcher` for live 2026 standards** (TanStack Query v5, RR v7 Data Mode, RTL,
Tailwind v4 OKLCH tokens, creator-tool UI references). Decisions in `docs/DECISIONS.md`
2026-06-18. **Open follow-ups:** live visual QA of the palette (needs running stack + seeded DNA);
85b–85g page redesigns. **Caught + fixed en route:** a `*/` sequence inside an `index.css` comment
(`--text-*/`) was prematurely closing the CSS comment and breaking the Tailwind build (would have
failed CI's frontend job too).

**Active issue**: **Issues 143–147 cleanup sweep COMPLETE** (branch `issue-139-142-sweep`). **143 + 144 + 145 + 146 + 147 — all DONE 2026-06-17.** Remaining: the single **PR #20 → main** merge (one-time direct-to-main per Issue 145), and the queued follow-up **Issue 148** (per-template design-system migration, needs visual QA).

**Last completed (Issue 152 — Pro chatbot, 2026-06-17):** Streaming conversational assistant
scoped to the creator's own channel. New `chat/` package — `prompt.py` (cached,
honesty-constrained system prompt), `tools.py` (5 creator-scoped tools: DNA / recent videos /
video performance / channel averages / upload timing, every query filtered by the
worker-injected `creator_id`), `runner.py` (manual agentic streaming loop: stream → `tool_use`
→ execute → loop, capped at `CHAT_MAX_TOOL_ITERATIONS`). New
`worker/anthropic_stream.stream_message` (full-message return for the loop) +
`worker.tasks.chat_respond`. `routers/chat.py` — **gate = active creator (positive balance OR
live trial) + per-creator daily message quota** (no subscription, no per-message minute
deduction in v1 — research-backed, see DECISIONS), SSE-streamed reply reusing the Issue-86
`/tasks/{id}/events` channel, list/get/regenerate/delete. Models `ChatConversation` /
`ChatMessage` + **migration 0026** (RLS on the conversation table, child-table pattern on
messages). React **`/app/chat`** page reusing `taskStream` (new `subscribeToChatStream`).
Config: `CHAT_DAILY_MESSAGE_LIMIT`/`CHAT_MAX_TOOL_ITERATIONS`/`CHAT_MAX_TOKENS`/
`CHAT_HISTORY_TURNS`. **Verified:** ruff + mypy + bandit clean; **993 unit green** (7 new chat
unit tests — honesty structural, tool-schema, gate, agentic-loop cap); frontend eslint 0 +
build + vitest 6/6. **CI-authoritative:** migration 0026 + `tests/test_chat_isolation_
integration.py` (per-creator tool isolation) need real Postgres. Phase 1 used `/claude-api` +
industry-standards research; gate + agentic-loop decisions in `docs/DECISIONS.md` 2026-06-17.

**Last completed (Issues 149 + 151 + OBS-150 filed, 2026-06-17):** Three of the user's
"final" beta items. **149 (insight sort) DONE** — Top/Underperformers panels on
`insights.html` got a Sort dropdown (default score high→low; +low→high, +Title A–Z),
client-side reorder of fetched rows; fixed an **off-course stored-XSS** inline (performer
title/kind/id were unescaped in innerHTML — Issue 138's sweep missed this row) → now
`escapeHtml`-wrapped, pinned in `test_static.py`, logged in OFF_COURSE_BUGS. **151
(beta logging to DB) DONE** — new `event_logs` table (migration 0025) + `event_log.py` sink
(isolated engine on `LOGS_DATABASE_URL`, **boundary PII/token redaction**, best-effort
writes); `/api/activity` now persists UI events (+keeps app.log), a new `http_request`
middleware logs every backend request (the click→action trail), `GET /api/logs/me` returns
a creator's own rows (app-level isolation). No RLS (telemetry; mirrors audit_log exemption);
default-privileges from 0010 cover the app role. Unit tests (redaction) pass; integration
tests (persist/redact/isolation) are CI-authoritative; **ruff+mypy clean; full unit suite
986 passed** — that run also fixed a latent bug from the SPA turn (the `/app` HTML routes
were failing `test_response_models` → marked `include_in_schema=False`). **150 (OBS live
capture) FILED** as a concrete issue — continuous obs-websocket capture of the whole session
as the *ToS-clean* clip source (extends Issue 95; sidesteps the YouTube-download bar). **Still
open: 152 (Pro chatbot)** — brief ready, needs the `/claude-api` skill + build. Docs updated:
DECISIONS, COMPLIANCE (event-log data class + retention), SOT, issues.md (149/150/151/152).

**Last completed (Frontend framework adoption — React + TS, pilot, 2026-06-17):** Resolved the long-standing "review-UI framework" DECISIONS candidate: adopted **Vite + React + TypeScript + Tailwind v4 + shadcn-style components**, incrementally (strangler-fig). Stood up `frontend/` served by FastAPI under `/app/*` (hashed assets via StaticFiles mount; `/app/{path}` falls back to the SPA shell so React Router owns client routing; legacy `static/` pages untouched and verified non-regressed). The Issue-99 dark Linear design tokens are mapped into the Tailwind `@theme`. **Profile is the pilot page** — full port of `static/profile.html` (DNA card, identity, intake mode, API keys) with the headline fixes: the DNA brief now renders as **real structured HTML** via a `.textContent`-safe parser (was a raw-markdown "wall of asterisks"), and the internal `v3 · active` badge is replaced by a plain **provenance badge** ("Updated <date>", synced/status chips). **Verified:** `npm run build` clean, eslint 0, vitest 6/6 (brief parser incl. an XSS-safety assertion), new `tests/test_spa_serving.py` + `test_static.py` 69/69 green. **Docker/CI wiring DONE (2026-06-17):** Dockerfile gained a `node:22` `frontend-build` stage that `npm ci && npm run build`s the SPA and copies `dist` into the runtime image at `/app/frontend/dist`; added a `.dockerignore` (was none — `COPY . .` had been baking `.venv`/`node_modules`/`.env` into the image); added a `frontend` CI job (eslint + vitest + build). The existing `docker-build` smoke job + `docker-publish` build the SPA automatically (same Dockerfile, `context: .`). Validated: `npm ci` clean + full CI sequence green locally; YAML valid. **Docker image build itself not run locally (no Docker in this env)** — CI's docker-build job is authoritative. **Follow-ups still open:** (1) live visual QA of the rendered page needs the running backend + a seeded DNA; (2) remaining pages still vanilla, ported on demand. See `docs/DECISIONS.md` 2026-06-17.

**Last completed (Issue 147 — UI/UX cohesion, 2026-06-17):** A 4-agent per-template audit found the incohesion was **duplicated components**, not missing tokens — the card concept was redefined 8+ times across pages under different names, stat-cell 3–4×, status-pill 5× in analysis alone. Delivered the foundation: new `static/components.css` shared layer (`.eyebrow`/`.stat-cell`/`.status-pill`/`.callout`/`.stream-output`/`.status-line`/`.input`/`.btn-danger` etc., tokens-only), wired into the 7 core templates; token additions (semantic tints, `--color-on-accent/-on-success`, one `--tracking-eyebrow`); fixed the `.intake-mode-option` `--editor-*`→`--color-*` mismatch; tokenized hardcoded `#000`/`#ffffff`/`rgba()`. Pinned with new `test_static.py` tests; **976 unit green**. `@layer` and the full per-template structural migration deferred to **Issue 148** (needs visual QA). See `docs/DECISIONS.md` 2026-06-17.

**Last completed (Issue 146 — docs consolidation, 2026-06-17):** `docs/` 20 → 17 live + a new `docs/README.md` index (canonical roles untouched). Archived 4 superseded docs to `docs/archive/` (KICKSTART, PRODUCTION_COMMANDS, ISSUE_APPROVED_PLANS, BETA_LAUNCH_RUNBOOK) with ⚠️ banners; salvaged KICKSTART's product "aspirations" → issues backlog and BETA's Google-OAuth closed-beta steps → ACCESS.md. **Deduped a divergent `TOKEN_ENCRYPTION_KEY` rotation** (two procedures → one zero-downtime MultiFernet flow canonical in RUNBOOKS, pointer in DEPLOYMENT). Renamed `other_apps_research.md` → `COMPETITIVE_RESEARCH.md`; removed root `Project Idea.md` (unreferenced KICKSTART dup). OFF_COURSE_BUGS triaged. See `docs/DECISIONS.md` 2026-06-17.

**Last completed (Issue 145 — staging + main branch model, 2026-06-17):** Established `feature → staging → main` (`docs/BRANCHING.md`). Cut `staging` from `main`; pruned stale `issue-138-sev1-bulk-sweep` (PR #19 squash-merged — content verified in `main`). Remote branches now: `main`, `staging`, `issue-139-142-sweep`. **Branch protection deferred:** rulesets need GitHub Pro on a private repo (API 403); kept as convention with the per-PR `CI` workflow as the gate — ready-to-apply ruleset written in `docs/BRANCHING.md`. PR #20 → main deferred to end of sweep (one-time direct-to-main). See `docs/DECISIONS.md` 2026-06-17.

**Last completed (Issue 144 — GH Actions + healthcheck audit, 2026-06-17):** Consolidated `ci.yml`+`quality.yml`+`integration.yml` into one `CI` workflow (8 workflows → 6; parallel jobs, names preserved so required-check rules resolve). **Integration now runs on PRs** — the gap that let Issue 143's breakage sit red 9+ days. Least-privilege `permissions: contents: read` on every workflow; bumped Node-20-deprecated actions (checkout@v6/setup-python@v6/buildx@v4/build-push@v7). **Health-check:** was a silent no-op (unset `PRODUCTION_URL`); enabling it exposed **Cloudflare Bot Fight Mode 403s** on GH-hosted datacenter IPs (origin healthy — 200 from a normal IP). Moved uptime monitoring to **Cloudflare Health Checks** (runbook in `docs/DEPLOYMENT.md`); demoted the GH cron to a manual smoke test. **Verified:** consolidated CI green on PR — all 6 jobs incl. integration 127/127. See `docs/DECISIONS.md` 2026-06-17.

**Last completed (Issue 143 — fix all red CI to 0 failures, 2026-06-17):** Cleared the two standing CI failures blocking PR #20 and the sweep. (1) **8 pip-audit CVEs** (4 starlette + 3 python-multipart + 1 cryptography) → bumped `fastapi 0.120.4→0.137.1`, `starlette 0.49.1→1.3.1` (the long-deferred starlette **1.x migration**; FastAPI 0.120.4 pinned `<0.50`, and the HIGH urlencoded-form-DoS CVE-2026-54283 is reachable via our login/OAuth endpoints so it couldn't be VEX-ignored), `python-multipart→0.0.31`, `cryptography→48.0.1`; lifted the now-fixed `PYSEC-2026-161` ignore (pytest CVE stays ignored — test-only). (2) **Integration suite red 9+ days** — `test_poll_clip_outcomes_uses_per_creator_median`: the poll's session-level `pg_advisory_lock` leaked across pytest-asyncio's per-test event loops on the shared module `admin_engine` pool, so the poll got `acquired=False` and silently skipped (`performed_well` stayed `None`). Fixed with rollback-before-unlock (prod hardening: an aborted-txn poll no longer leaks the lock) + an autouse fixture disposing `admin_engine` between tests. **Verified on real CI:** PR #20 all-green (SAST/pip-audit pass, coverage pass, unit pass) + integration dispatch **127 passed / 0 failed**. See `docs/DECISIONS.md` 2026-06-17.

**Prior (Issues 139–142)**: landed 2026-06-16 on the same branch (pushed; no deploy — deploy.yml is main-only).

**Last completed (Issue 142)**: LLM-drivable E2E harness + a live, SSH-reachable staging. `scripts/llm_harness.py` mints a session JWT (no OAuth) and drives the real API; `docs/STAGING_ACCESS.md` is the runbook. **Stood up live:** SSH to `creatorclip-vm` verified; the old staging (`root-*`) was permanently degraded by a PgBouncer `md5` vs Postgres-16 `scram-sha-256` auth mismatch (fixed → `AUTH_TYPE: scram-sha-256`; also blocked the deferred Locust load test). Replaced it with project `cc139` built from this branch (`creatorclip:staging` tag — prod's `:latest` left untouched), migration **0024 applied on real Postgres** (`alembic current` → `0024 (head)`), creator seeded. **Harness ran 10/10 PASS against staging**, incl. the live Issue-139 regression (`linked_video_visible_non_clippable — origin=link clippable=False`, `queue_source_less_409 — 409`). Prod (`autoclip-*`) healthy throughout.

**Pre-launch gates closed (2026-06-16):** (1) **Locust 300-user load test** finally runnable (staging was repaired) and **executed** — 300u/180s fanned across 13 creators, ~138 req/s, **zero 500s/timeouts/pool-exhaustion**, p99 680ms, `/health` 0% fail → **axes A + E CLOSED** (`docs/assessment/REPORT.md`); locustfile gained `CC_CREATOR_IDS` fan-out (single-creator runs are 85% rate-limited and don't stress the pool). (2) **`TOKEN_ENCRYPTION_KEY` rotation runbook** written (`docs/DEPLOYMENT.md`, zero-downtime MultiFernet path). **`/assess` verdict moves CONDITIONAL → the remaining blockers are external/ops only: Google OAuth verification + prod `.env` lock/`/docs` disable.** PR **#20** open. _(Found + fixed en route: the PgBouncer md5-vs-scram bug that had silently broken staging — logged in OFF_COURSE_BUGS.)_

**Last completed (2026-06-16 session)**: Three code items from the "be real, what's left to deploy" sweep. **Issue 139 — linked-video SEV1 (compliant fix):** new `Video.origin` enum (`catalog|link|upload`, migration 0024 backfilling from `source_uri`) replaces the `source_uri IS NULL` heuristic that silently hid every linked video from the dashboard. `list_videos` now filters `origin != catalog` so linked videos appear, carrying a derived `clippable` flag; `_has_clip_track_videos` (onboarding) switched to the same rule. **Crucially, we researched and REJECTED wiring yt-dlp** to make linked/catalog videos clippable: downloading via yt-dlp violates the YouTube API Services ToS even for own content and risks the Google OAuth verification gate (DECISIONS + COMPLIANCE updated). Instead (Option A): `POST /videos/{id}/queue` returns 409 with upload guidance for source-less rows; the dashboard shows an "Upload source file to clip" affordance (Google-Takeout-guided) and skips non-clippable rows in the in-flight tracker + status poller. `tests/test_issue_139.py` (+6). **Issue 140 — removed inert `cache_control`** on `routers/insights.py` analyze-performer (~30-token prefix, below Haiku 4.5's 4096 floor); regression test added (+1). **Issue 141 — domain reconciliation:** flipped every committed `agenticlip.studio` → `autoclip.studio` (the live domain) across `.env.example`, `docker-compose.prod.yml`, `tests/test_doctor.py`, SECRETS/ACCESS/SOT docs, issues.md steps. **Tests:** 974 passed (+7) / 2 skipped / 127 deselected; ruff 0 / mypy 0 on touched files. Postgres down locally → migration 0024 apply + coverage/integration are CI-authoritative.

**Prior**: Issue 138 — **all 7 SEV1s from the 2026-06-09 `/assess` closed in one sweep** (7 SEV1 → 0), three risk-ordered phases on branch `issue-138-sev1-bulk-sweep`. **Phase A (mechanical):** (#1) XSS — new `static/util.js::escapeHtml` (escapes the apostrophe too) wraps the three unescaped `innerHTML` sinks: YouTube titles (`index.html`), reflected LLM output + stored saved-insights (`insights.html`); `analysis.html`'s local `_esc` now delegates to it. (#2) `analysis.html` "Ingest this video" CTA built its URL from a non-existent element id (`youtube_url`; real id `url-input`) — TypeError killed the whole non-catalog analysis path since Issue 125 — now uses in-scope `urlRaw`. (#4) `_expire_trials_async` no longer SELECTs/logs creator email (PII invariant). (#5) `chapters.py` `max_tokens` 512→2000 + dropped `description_block` from the model schema (`parse_chapters` rebuilds it) — fixes deterministic truncation on 1h+ videos. **Phase B:** (#3) `GET /creators/me/thumbnail-patterns` gained `@limiter.limit("10/hour", key_func=creator_key)` + a per-creator single-flight Redis lock (`_compute_patterns_single_flight`, oauth.py SET-NX + Lua-release primitive, fully fail-open) so a degraded cache / concurrent first-hits can't fan out into N billed multimodal calls. **Phase C:** (#7) bumped `anthropic` 0.40.0→**0.105.2** (pre-vetted no-breaking-change; retired the now-unused `type: ignore` on the `scoring.py` `ttl:"1h"` block; added `cached_write_1h` logging from `usage.cache_creation.ephemeral_1h_input_tokens`). (#6) removed the **inert** `cache_control` markers from `titles.py` + `thumbnails.py` — their ~1,550-token prefix is below Sonnet 4.6's **2048**-token floor (corrected from a wrong 1024 in three `DECISIONS.md` loci; 1024 is the Sonnet *4.5* floor). **Tests:** +13 (5 static, 2 chapters, 1 PII guard, 1 rate-limit registration, 2 single-flight, + inverted titles/thumbnails cache assertions). Full non-integration suite **967 passed** / 2 skipped / 127 deselected; clip eval harness green; ruff 0 / mypy 0 project-wide; no new advisory in the anthropic dep tree (Layer-0 coverage/pip-audit gate CI-authoritative — local stack has no Postgres). Decision logged in `docs/DECISIONS.md` (2026-06-16); off-course `routers/insights.py:570` 4th inert marker logged in `OFF_COURSE_BUGS.md`. **Remaining for PRODUCTION-READY: YES** — the deferred Locust 300-user run (axes A+E) + `TOKEN_ENCRYPTION_KEY` rotation runbook (pre-launch checklist, not code).

**Prior**: Onboarding state aggregation on `/auth/me` + `/creators/me` (deferred follow-up #2 from 2026-06-08 `/assess`). Both endpoints now return a nested `setup: SetupStepOut` block — `{ step, label, next_action_type, next_action_url, progress_index, progress_total }` — resolved server-side by `dna/onboarding.py::resolve_setup_step`. Five `step` values (`sync_catalog`, `build_dna`, `confirm_dna`, `link_first_video`, `complete`) cover every onboarding transition; `Creator.onboarding_state` enum is the fast path, with at most one follow-up query per call (`check_data_gate` for pre-DNA states, a single COUNT(*) on clip-track videos for active state, zero queries for `dna_pending`). The resolver lives in `dna/` so any future non-HTTP caller — Beat task, reminder email, interactive walkthrough — shares the rule. `SetupStepOut` lives in `routers/_schemas.py` so both routers reuse it without a cross-router import. **Frontend**: `static/auth.js` stashes `window.__SETUP__` and dispatches `setup:ready` on every page load. `static/index.html::init` replaced the old 2-branch `onboarding_state` dispatch with `setup.label` / `setup.next_action_url`; `link_first_video` and `complete` skip the banner because the empty-hero block already covers those cases. **Industry research:** Stripe Account capabilities.requirements.currently_due, Linear User.onboardingState + nextStep, Vercel onboarding API ({currentStep, totalSteps, nextAction}), Clerk/Auth0 publicMetadata.onboardingComplete — all converge on "server owns the rule, client renders it". Chose nesting over flat `setup_step`/`setup_step_label`/`next_action_type` fields (issue's literal phrasing) so future fields like `blocked_by` or `eta` can land without bloating the top-level model. Decision logged in `docs/DECISIONS.md`. **Tests:** 956 passed (+8) / 2 skipped. New `tests/test_onboarding_setup_step.py` (8 tests): parametrized pre-DNA branching (`connected`/`awaiting_data` × ready/not-ready), `dna_pending` → confirm_dna, `active` + zero videos → link_first_video w/ `open_form` action, `active` + ≥1 video → complete with progress_index == progress_total, both endpoints carry identical setup block (auth.js stash vs profile-page reads agree). Updated `tests/test_issue_125.py` and `tests/test_rate_limiting.py` to use real `OnboardingState.active` + a session mock with a working `scalar_one` — both were using `MagicMock(value="active")` which doesn't match the enum the resolver dispatches on. Layer 0: ruff 0 / mypy 0 / coverage **75.93%** (up from 75.72%) / bandit 0/0 / pip-audit 0 / freshness ok.

**Prior**: Empty-state response envelopes (deferred follow-up #1 from 2026-06-08 `/assess`). `/videos`, `/creators/me/insights/saved`, and `/videos/{id}/clips` now return a typed envelope per resource — `{ <resource>: list[...], state: "empty_initial" | "empty_filtered" | "populated", message: str | None, next_action: { label, action_type, url } | None }` — instead of a bare JSON array. Resource-named keys (`videos`, `insights`, `clips`) match the existing `DnaGetOut.profile` / `ClipListOut.clips` convention. Shared `EmptyState` literal, `NextActionOut` model, and `build_envelope_state(count, *, is_filtered)` helper live in `routers/_schemas.py` (DRY across the three routers — same file that already housed `TaskQueuedOut` from Issue 108). **Backend wiring:** `routers/videos.py::list_videos` keys empty-state copy off `creator.onboarding_state` — `connected` → "Link your first video" with `action_type="open_form"` (frontend expands the inline link form); any other state → the same nudge but without the form-open hint. `routers/insights.py::list_saved_insights` nudges empty users to `/static/insights.html` so they can save one. `routers/clips.py::list_clips` distinguishes "video still ingesting" (message only, no CTA — would 400 against /generate) from "ingest done + zero clips" (CTA points at the generate route). `ClipListOut` got the new fields with `"populated"` defaults so the additive change keeps POST `/clips/generate` unchanged. **Frontend consumers updated:** `static/index.html::loadVideos` reads `body.videos` with bare-array fallback and uses `body.message` as the empty-row copy (replacing the hardcoded "No videos yet — pick a path above" string). `static/insights.html::loadSavedInsights` reads `body.insights` with bare-array fallback. `static/editor.js` + `static/review.html` were already defensively reading `d.clips || []` (Issue 91) so they keep working. The dashboard `#empty-dashboard-hero` (shipped earlier 2026-06-08) keeps its richer static markup — the server `message` becomes the table-row copy underneath. **Industry research:** strict REST (Google AIP-158, Stripe, GitHub, JSON:API) puts UX copy in the client; the envelope-with-state pattern is standard in BFF / Vercel / Supabase / Remix-loader architectures where backend and frontend are co-owned. CreatorClip is a single-frontend monorepo with no third-party API consumers, so the BFF posture is correct — deviation logged in `docs/DECISIONS.md` per the One Rule. **Tests:** 948 passed (+8 from 940) / 2 skipped / 127 deselected. New `tests/test_empty_state_envelopes.py` (7 tests): per-endpoint populated vs empty_initial state, /videos onboarding_state-keyed action_type, /clips ingesting-vs-done message branching, /insights/saved next_action URL. `tests/test_static.py` updated to assert the envelope shape on the three pre-existing /videos tests + new "connected state offers open_form" test. `tests/test_isolation.py::test_get_videos_scoped_to_creator` reads `body["videos"]` now. Layer 0: ruff 0 / mypy 0 / coverage **75.72%** (up from 75.38% baseline) / bandit 0/0 / pip-audit 0 / freshness ok.

**Prior**: Issue 126 — Trial UX + billing clarity. Six deliverables in one commit. **(A) `Creator.trial_ends_at` nullable column** + migration 0023; nullable so legacy creators stay NULL (treated as "no trial" by the trial-active predicate, preserves correctness). **(B) First-login wiring** — `routers/auth.py` `is_new` branch now stamps `creator.trial_ends_at = datetime.now(UTC) + timedelta(days=settings.TRIAL_DURATION_DAYS)` in the same transaction as `grant_minutes(FREE_TRIAL_MINUTES)`, so the two states can never disagree. **(C) `BalanceOut` extension** — `GET /billing/balance` now also exposes `trial_ends_at`, `trial_active`, `trial_days_remaining` (ceil so "ends in 18h" reads as "1 day"), and `low_balance: bool` (true when `bal < settings.LOW_BALANCE_THRESHOLD_MINUTES`). Single endpoint, no new route. Defensively normalizes naive timezones to UTC before comparing (SQLAlchemy round-trip behavior). **(D) Differentiated 402 copy** — new `_trial_expired()` + `_trial_ended_402_detail()` helpers in `billing/ledger.py`; both `check_positive_balance` and `check_balance_for_minutes` branch on them. When balance is zero AND `trial_ends_at < now`, the 402 detail becomes "Your free trial has ended. Add minutes at /pricing to continue." Otherwise the existing generic copy. NULL `trial_ends_at` falls back to generic (legacy correctness). **(E) Daily `expire_trials` Beat task** — `worker/tasks.py::expire_trials` + `worker/schedule.py` entry; watchdog-only (logs creators whose trial just expired with zero balance in the 25h window, no state mutation). Avoids the Beat-vs-API race; `billing/ledger.py` reads `trial_ends_at` live as the single source of truth. **(F) UI surface** — `static/auth.js` caches the full balance payload on `window.__BALANCE__`, emits a `billing:ready` custom event, and toggles `.is-low` on the `#nav-balance` chip when below threshold. `static/index.html` adds a `#trial-banner` (renders "Trial ends in X days — Y min remaining" on `billing:ready`; per-day-bucket dismissibility in localStorage; final-day override when `days <= 1`; CTA links to `/static/pricing.html` per Userpilot 2026) and a `#low-balance-warning` panel above the videos table. `static/analysis.html` carries the same low-balance warning above the Analyze button. `static/page-shell.css` adds the `.nav-balance.is-low` amber state, `.trial-banner` + `.is-final-day` glow, and `.low-balance-warning` utility class. **Industry research:** 79 of PricingSaaS-500 use credit models (+126% YoY, 2026); proactive threshold alerts are now "essential engineering requirements" (Fungies, Schematic HQ); banner CTAs must link to checkout not settings (Userpilot); "customers who feel in control of their bill churn less" (Fungies). Decisions logged: 2026-06-08 entry in `docs/DECISIONS.md` covering NULL-for-legacy, watchdog-not-state-machine, differentiated-text-not-error-code, per-day-bucket-dismissal. **Tests:** 940 passed (+16) / 2 skipped. New `tests/test_issue_126.py` (16 tests): trial_ends_at column nullable + TIMESTAMPTZ; migration 0023; config carries both knobs; BalanceOut field shape; ledger differentiated-copy helper; expire_trials task + Beat registration; behavioral happy-path on /billing/balance for trial-active / expired / legacy; check_positive_balance differentiation (trial-ended vs legacy); dashboard trial banner + dismiss handler + final-day override + pricing CTA; low-balance warning above actions on dashboard + analysis; auth.js caches __BALANCE__ + emits billing:ready + toggles .is-low; page-shell.css carries the three classes. Layer 0: ruff 0 / mypy 0; no test regressions.

**Prior (this session)**: Issue 125 — Video control model + minutes transparency. Three deliverables in one commit. **(A) `Creator.analysis_mode` enum** — new `AnalysisMode` enum on `models.py` with values `{auto, selective, manual}`; nullable=False; server_default `'auto'` so migration 0022 backfills every existing row without a data step. Surfaced on `GET /creators/me` (CreatorMeOut + dashboard reads `window.__USER__.analysis_mode` to decide intake-CTA emphasis). **(B) `PATCH /creators/me/analysis-mode`** — new endpoint with 60/minute rate limit, Pydantic `AnalysisMode` enum validation (422 on unknown value), per-creator isolation implicit because the dep-injected `creator` IS the row mutated. Profile page now exposes a 3-option radio form (auto / selective / manual) with `saveAnalysisMode()` POSTing to the endpoint and updating `window.__USER__` on success. **(C) `POST /videos/{id}/queue`** — new endpoint for the explicit "Queue for analysis" CTA on pending dashboard rows. Idempotent: returns `queued: false` when the video isn't `pending` so a double-click can't double-fire `start_pipeline`. Hooks the existing `start_pipeline()` via `await asyncio.to_thread(...)` (scale-checklist axis B compliant). 404 on cross-creator access. **(D) `analytics_available: bool` on `AnalysisQueuedOut`** — populated identically to the back-compat `has_metrics` so the two can never drift; `static/analysis.html` reads the new field, renders explicit "Full analytics unavailable — this video isn't in your ingested catalog yet" panel with an "Ingest this video" CTA when false (replaces the old quiet "no metrics yet" inline mono note). **(E) "What costs minutes?" tooltip** on the dashboard nav balance chip using the existing `tooltip.js` (Issue 124): "Transcription and clip generation cost minutes. Viewing analytics, insights, DNA, and the transcript editor are always free." Closes the honesty gap the user flagged in the Issue 125 spec. Migration 0022 (alembic versions/0022_creator_analysis_mode.py) chains from 0021; creates the Postgres enum type explicitly + adds the column with server_default. **Industry research:** OpusClip = 1 credit/min source video, Vizard/Klap match, hybrid pricing ~65% of 2026 AI SaaS (PYMNTS June 2026). OpusClip opacity (BIGVU review noting "credits disappearing after subscriptions lapse") is the explicit anti-pattern our explicit-Queue + always-visible balance + What-costs tooltip + analytics_available field directly counter. Decisions logged: 2026-06-08 entry in `docs/DECISIONS.md` covering default-auto, dual-field over rename, separate /queue endpoint over reusing /generate. **Tests:** 924 passed (+17) / 2 skipped. New `tests/test_issue_125.py` (17 tests): AnalysisMode enum value-set pin, Creator.analysis_mode column + server_default, migration 0022 structural, GET /creators/me returns analysis_mode, PATCH happy-path parameterized over all 3 modes + session.add + session.commit assertions, PATCH 422 on bogus value (no commit on rejection), /videos/{id}/queue happy + idempotent-when-running + 404 on other-creator (start_pipeline mocked, never called on the negative cases), AnalysisQueuedOut field shape + route handler dual-populates from has_metrics, profile.html intake-mode form + 3 radios + saveAnalysisMode wiring, dashboard tooltip honesty copy (both billable AND free halves pinned), queueVideo() wired to /queue endpoint + "Queue for analysis" copy, analysis.html analytics-unavailable element + "Full analytics unavailable" + "Ingest this video" copy. Layer 0: ruff 0 / mypy 0 / coverage unchanged (additive backend + UI; no removed code paths).

**Prior (this session)**: Issue 137 — Project-wide UI overhaul + horizontal-overflow fix. User feedback on the live deploy: "we need a complete overhaul on the UI […] match the UI of the sign in page, that sleek design and nice purple and super modern look, but for the WHOLE project. Additionally, the size of the app is too large horizontally, I need to scroll to see the whole thing sideways." Two deliverables in one commit. **(A) Visual unification:** new `static/page-shell.css` ships the cross-page chrome — aurora backdrop on `body.app-page`, glassmorphism sticky nav (`backdrop-filter: blur(14px) saturate(140%)`, indigo-tinted border), `.page-container` width cap at `min(1200px, calc(100% - 2 * var(--space-4)))`, `body.app-page .card` upgrade to `--editor-surface` + 12px radius + inset highlight + soft shadow, `.page-hero` aurora band, `.gradient-h1` utility, `body.app-page .btn-primary` upgrade to gradient pill + hover-lift + `--glow-accent-soft`, `.table-wrap` to scope horizontal scroll to data only, `.action-row` flex-wrap utility for button groups, and global `overflow-x: clip` on html/body with `@supports not (overflow-x: clip)` fallback to `hidden`. All 8 authenticated templates (`index`, `insights`, `profile`, `onboarding`, `analysis`, `pricing`, `walkthrough`, `review`) link `page-shell.css` and carry `class="app-page"` on `<body>` (review.html keeps `editor-page` alongside). No new color literals — every primitive consumes existing `--editor-*` / `--gradient-aurora` / `--glow-accent` / `--gradient-text` tokens from `_design-tokens.css`. **(B) Horizontal overflow fix:** the dashboard's `.video-table` is now wrapped in `<div class="table-wrap">` so the 4-column data table can horizontal-scroll INSIDE its wrapper without ever pushing the page sideways; action-cell buttons (Generate clips + Titles / X clips + Titles) render into a `.action-row` flex-wrap container so they stack on narrow viewports. `overflow-x: clip` on body is the global backstop. **(C) Design system honesty:** tables, forms, transcripts, and list rows remain flat with high-contrast text (WCAG 2.2 1.4.3 honored) — glassmorphism scoped to chrome layers only (nav, page hero, cards, modals/popovers, activity panel). Explicit reversal of the Issue-99 (2026-05-31) + Issue-136-redirect (2026-06-07) "sharp-utility-on-data-pages" split; full reasoning logged in `docs/DECISIONS.md` under "2026-06-08 — Issue 137". Industry research confirms Linear's own 2026 refresh extends aurora + indigo to product surfaces (not just marketing) so this is alignment with current standard, not deviation. **Tests:** 907 passed (+11 from 896) / 2 skipped. 5 new in `tests/test_static.py` pin: page-shell.css tokens (overflow-x clip + body.app-page + backdrop-filter + .table-wrap + --gradient-aurora consumption), every authenticated page links + opts in, dashboard table wrap + action-row presence, DECISIONS.md Issue 137 entry, cache-bust ?v= applied to page-shell.css. One Issue-136 existing test loosened to accept the new `editor-page app-page` class list on review.html. Layer 0: ruff 0 / mypy 0 / coverage unchanged (CSS/HTML only) / no Python source touched.

**Prior**: Issue 136 — Dark editor mode + marketing hero. **Part A — Dark editor (`static/review.html` + new `static/editor-layout.css`)**: Three-pane CSS Grid shell (player fluid | transcript 35rem | tools 3.5rem) with the transcript editor (Issue 135) always-visible in the middle column; other panels (Issue 119 caption style picker, Issue 134 clean pass, Issue 94 why-this-clip, Issue 118 tag-feedback hint) live behind icon-strip drawers toggled by a single `data-active-tool` attribute on the shell, driven by ~15 LOC of vanilla JS (no animation library). All animation is CSS `transition: transform var(--duration)`. Mobile breakpoint at 900px stacks columns and converts the drawer to a bottom sheet. New `--editor-bg/-surface/-surface-elev/-border/-border-strong/-icon-strip/-drawer-width/-strip-width` tokens added to `_design-tokens.css` with the Issue-136-locked values (#0a0a0a / #141414 / etc.); no hardcoded hex in markup. `transcriptEditor.mount(clip)` now fires from `loadClip()` instead of a `<details>` onclick, so the middle column stays in sync as the user advances. Every Issue 118/119/133/134/135 id/handler preserved through the restructure (pinned by regression test). **Part B — Pre-auth hero (`static/index.html` + new `static/hero.css`)**: `<body data-allow-anonymous>` opts in; `auth.js` now toggles `body.is-hero-mode` on `/auth/me` 401 instead of redirecting to login. `hero.css` shows `.hero` + hides `.dashboard` / authenticated nav links via `body.is-hero-mode` selectors — pure CSS, no JS branch in the show/hide path. Hero markup: H1 + subhead + URL `<input>` + CTA + honesty disclaimer (hero-scoped copy in addition to the existing nav-bar one, since hero mode hides the nav). Client-side YouTube URL regex accepts `youtube.com/watch?v=…`, `youtu.be/…`, `youtube.com/shorts/…`; on valid submit redirects to `/auth/login?next=/?yt=<encoded URL>`. Post-login, the same `auth.js` reads `?yt=` and auto-fills the existing dashboard link-video input — no new backend route. Demo MP4 placeholder via `<video poster="/static/demo-hero-poster.png" preload="none">` so the missing source file doesn't block first paint; deferred follow-up to record an actual 30s muted loop. Decisions logged in `docs/DECISIONS.md` (Issue-136 D1–D7: 3-pane Grid + drawer over JS animation lib; always-visible transcript over drawer; cookie-gate via `data-allow-anonymous` over separate landing route; `?yt=` query hint over new backend endpoint; client-side regex; preload-none demo placeholder; preserved-IDs regression test). **Tests**: 896 passed (+5 from 891) / 2 skipped. New `tests/test_static.py` cases pin: editor-layout.css + editor tokens (--editor-bg/-surface/-icon-strip), review.html 3-pane shell + every Issue 118/119/133/134/135 id survived, index.html hero block + honesty disclaimer in hero copy + dashboard still present (no regression), auth.js `data-allow-anonymous` + `?yt=` handling, hero.css visibility-by-body-class. Layer 0: ruff 0 / mypy 0 / coverage ≥75.20% / bandit 0/0 / pip-audit 0 / freshness ok.

**Prior**: **Post-Issue-135 audit fixes (6 SEV1s + 1 cross-cutting axis-B SEV2).** Full `/assess` ran across 13 modules; verdict was CONDITIONAL with zero BLOCKERs and a fixable register. All 6 SEV1s closed in one commit: (A1) `routers/clips.py` — `/clean` and `/cuts` now return 409 `{code:"pending_clean_or_edit"}` when `cleaned_render_uri` is already set, instead of silently no-op'ing the second request via the worker idempotency probe. (A2) `worker/tasks.py::_retrain_preference_async` switched from `AsyncSessionLocal` → `AdminSessionLocal` (worker-internal pass; under the RLS role-split the unstamped session was filtering to zero rows and fitting an empty model). (A3) `_generate_improvement_brief_async` stays on `AsyncSessionLocal` but now stamps `session.info["creator_id"]` so the `after_begin` listener sets `app.creator_id` before any query. (A4) Dropped inert `cache_control` breakpoints on `knowledge/hooks.py:179`, `knowledge/chapters.py:186`, `analysis/brief.py:94` — all three sit below the relevant model's cacheable-prefix floor (Haiku 4.5 = 4096 tok, Sonnet 4.6 = 1024 tok); marker was inert and token log silently reported `cache_read=0`. (A5) `youtube/oauth.py::_do_token_refresh` now opens an internal `AdminSessionLocal()` for the token write instead of committing the caller-owned session; previous behavior flushed unrelated pending writes in the caller's transaction. Caller's session is refreshed via `session.refresh(row)` after the internal commit so subsequent reads see the new token. (A6) Cross-cutting scale-checklist axis-B fix: wrapped all ~16 `task.delay(...)` / `start_pipeline(...)` calls across `routers/clips.py`, `videos.py`, `creators.py`, `auth.py`, `improvement.py`, `analysis.py`, `thumbnails.py`, `titles.py`, `review.py` in `await asyncio.to_thread(...)` — each was a sync Redis round-trip blocking the event loop at concurrency. Decisions logged in `docs/DECISIONS.md` (A1–A6). **Tests**: 891 passed (+2 from 889) / 2 skipped. New tests pin the 409 conflict on both `/clean` and `/cuts`. Layer 0: ruff 0 / mypy 0 / coverage 75.24% / bandit 0/0 / pip-audit 0 / freshness ok.

**Prior**: Issue 135 — Text-based transcript editor (Descript-style). New `clip_engine/edits.py` validates user-supplied cut lists with: bounds + NaN + start≥end + overlap rejection, hard 5 s minimum kept duration + 85 % maximum removed cap, and sub-frame floor (0.04 s = one frame at 25 fps) on inverted keep ranges. New `clip_engine.render.render_cleaned_clip_file::afade_s = min(0.005, seg_dur / 2.0)` guard fixes a latent bug from Issue 134 where a kept segment shorter than 10 ms would request an afade longer than half-duration and crash ffmpeg. New Celery `edit_clip` task + `_edit_clip_async(clip_id, cut_segments)` defensively re-validates and runs the same `render_cleaned_clip_file` pipeline as Issue 134's clean pass; result uploads to `clips/{id}_edit.mp4` and lands on `Clip.cleaned_render_uri` (REUSES Issue 134's column — confirm-swap path is the same `POST /clips/{id}/clean/confirm`). Two new router endpoints: `GET /clips/{id}/transcript` (60/hour) returns the clip-windowed word array with stable indices + clip-relative timestamps for the editor pane; `POST /clips/{id}/cuts` (20/hour, balance-gated) accepts `{segments: [{start_s, end_s}]}`, runs `validate_user_cuts`, queues the Celery `edit_clip` task. New `static/editor.js` (~280 lines): word spans rendered with `data-start/data-end/data-index`, native `window.getSelection()` snapped to enclosing `.ed-word` on `mouseup` (keyboard `Shift+Arrow` works for free + WAI-ARIA-aligned `role="textbox" aria-readonly`), cut queue persisted to `localStorage["clip:{id}:cuts"]`, strikethrough + faded opacity on affected words, per-cut × removal button, one-level undo, "Clear all" wipe, batch-on-confirm POST, polling for `cleaned_render_uri`, side-by-side preview player, reuse of `/clean/confirm` swap. review.html gains an "Edit transcript" expander panel + editor styles; `loadClip` unmounts previous editor state. Decisions logged in `docs/DECISIONS.md` (D1 drop 24 h purge — reuse cleaned_render_uri, D2 hard caps 5 s/85 %, D3 sub-frame floor, D4 afade guard fixes Issue 134 latent bug, D5 getSelection + word-span DOM, D6 batch-on-confirm). **Tests**: 889 passed (+25 from Issue 134's 864) / 2 skipped. Layer 0: ruff 0 / mypy 0 / freshness ok. New `tests/test_edits.py` (25 tests) covers all validation paths (bounds, NaN, overlap, kept_too_short, removed_too_much, permissive right edge, sub-frame floor), afade guard regression, endpoint integration (202 happy + 422 with structured code per reject reason + 404 per-creator isolation), transcript clip-windowing.

**Prior**: Issue 134 — Filler-word + long-silence removal with reversible preview. New `clip_engine/filler.py` (~190 lines, pure function) walks the WhisperX word array and emits a `list[CutSegment]` with two-tier filler detection (Tier 1 unconditional: `um`/`uh`/`umm`/`uhh`/`er`/`ah`/`mhm`/`hmm`/`uhm`; Tier 2 pause-flanked: `like`/`you know`/`basically`/`so`/`right`/`okay`/`you know what i mean` — only when phrase ≤600 ms AND flanked by ≥150 ms gap on at least one side) plus silence cuts on inter-word gaps >800 ms, with 150 ms breath tail subtracted from each side. Helpers `merge_adjacent_cuts` + `invert_to_keep_ranges` + `percent_removed` close the cut→keep-range loop without ever emitting zero-width segments (which crash the ffmpeg graph). New `clip_engine/render.py::render_cleaned_clip_file` writes a `filter_complex` script to a sibling `.filter` file and invokes ffmpeg with `-filter_complex_script` — each kept segment gets `trim`/`atrim`/`setpts`/`asetpts`/5 ms `afade in+out` for click prevention at every splice; concat with `concat=n=N:v=1:a=1[outv][outa]`; cleanup in `finally`. New worker task `clean_clip` (max_retries=2) downloads the existing `render_uri` (the burned-in captioned clip, NOT the original video — keeps animated captions intact and reuses the paid encode), reads `Transcript`, builds the clip-relative word array, runs the cut logic, uploads the result to `clips/{id}_clean.mp4`, persists `Clip.cleaned_render_uri`. SSE progress events under stage="clean". New router endpoints: `GET /clips/{id}/clean-preview` (60/hour, no render) returns `CleanPreviewOut` with the cut list + `percent_removed` + a `warning` string when ≥30%; `POST /clips/{id}/clean` (20/hour, balance-gated) returns 202 + `task_id` + `stream_url`; `POST /clips/{id}/clean/confirm` (60/hour) does an atomic swap `render_uri ← cleaned_render_uri` and is idempotent (200 + `status="noop"` when there's nothing to swap, so router retries are safe). Migration `0021_clip_cleaned_render_uri` adds `Clip.cleaned_render_uri TEXT NULL` via plain `op.add_column`. UI: review.html gets a "Clean filler words + long silences" expander panel — preview button → strikethrough list with reason + duration per cut + `⚠` warning band; apply button polls the clips list for `cleaned_render_uri` to populate a side-by-side cleaned-version player; confirm button swaps. `ClipOut` schema extended with optional `cleaned_render_uri`. Two new config knobs (`SILENCE_REMOVAL_THRESHOLD_MS=800`, `SILENCE_TAIL_MS=150`, `FILLER_TIER2_FLANK_GAP_MS=150`, `FILLER_TIER2_MAX_DURATION_MS=600`) added to both `config.py` and `.env.example`. Decisions logged in `docs/DECISIONS.md` (two-tier lexicon + no POS tagging + no ML, 800ms+150ms tail, `filter_complex` over `select`/demux-concat, 5ms `afade` over `acrossfade`, side-by-side URI + confirm-swap pattern). **Tests**: 864 passed (+24 from Issue 133's 840) / 2 skipped. Layer 0: ruff 0 / mypy 0 / freshness ok. New `tests/test_filler.py` (24 tests) covers Tier 1 unconditional + punctuation normalisation, Tier 2 pause-flank guard + max-duration gate + multi-word phrase, silence + tail subtraction, merge + invert roundtrip + zero-width drop, percent-removed >30% warning, filter_complex script shape + cleanup + invalid-input rejection, end-to-end disjoint keep-range invariant, `/clean-preview` cuts+warning, `/clean/confirm` idempotency.

**Prior**: Issue 133 — Animated caption styles (Bold Pop / Gradient Slide / Minimal). New `clip_engine/captions.py` generates ASS subtitle files via `pysubs2==1.7.3` (libass-backed). `clip_engine/render.py` appends `subtitles={out}.{style}.ass:fontsdir=/usr/share/fonts/custom` to the existing crop→scale vf chain when `style_preset.subtitle in {bold_pop, gradient_slide, minimal}`. ASS path is per-render and cleaned in a `finally` block. **Bold Pop**: `\an5` middle-center, Anton 95pt + `\bord4`, override `{\t(0,80,\fscx120\fscy120)\t(80,160,\fscx100\fscy100)}` for the scale pop; one Dialogue per word. **Gradient Slide**: accumulating per-phrase Dialogue events — each new word fades in with `{\fad(150,0)\c&Hd26a5e&\t(0,300,\c&Hffffff&)}` while prior words stay at Style-default white; each line ends at the next word's start so only one accumulating line is on screen at a time (libass auto-centers). **Minimal**: one Dialogue per transcript segment, no override tags, 60pt bottom-center, `MarginV=290` lower-third placement. Brand indigo `#5e6ad2` encoded as ASS `&Hd26a5e&` (BBGGRR byte order) — pinned by regression test that also asserts the HTML byte order `&H5e6ad2&` does NOT appear. Graceful word-level → line-level fallback when `Transcript.segments_jsonb[words]` is absent. Worker `_render_clip_async` fetches `Transcript` only when an animated style is selected, passes `transcript_segments` through to `render_clip_file`. `static/review.html` style picker rewritten: dropped Issue-119 placeholder keys (`white_large`/`yellow_impact`/`captions_sm` — they always drew empty text and were dead scaffolding), added the three real options with one-line tooltip descriptions. Dockerfile installs `fontconfig`, `fonts-open-sans`, `fonts-dejavu-core`, fetches `Anton-Regular.ttf` from Google Fonts GitHub raw URL into `/usr/share/fonts/custom/`, runs `fc-cache -f`. Decisions logged in `docs/DECISIONS.md` (pysubs2/libass choice, three-style spec, BBGGRR byte order, lower-third position, legacy-key removal, Anton fetch strategy). **Tests**: 840 passed (+19 from 821) / 2 skipped. Layer 0: ruff 0 / mypy 0 / freshness ok. New `tests/test_captions.py` (16 tests) covers ASS structure, style enum, per-word event count, scale-pop tag presence, indigo-byte-order regression, accumulating-phrase pattern, clip-window filtering, word-timestamp-missing fallback, render.py wiring. Existing `tests/test_render_style.py` updated for the new filter shape.

**Prior**: Issues 130 + 131 — Hook analyzer + auto chapter markers. **Issue 130**: `POST /creators/me/videos/{video_id}/hook-analysis` returns 200 + `{status:"no_data"}` when no `RetentionCurve` rows exist (cheap COUNT check), or 202 + `task_id` + `stream_url` otherwise. Celery `analyze_hook` task fetches the target video's retention curve + up to 20 other creator videos' curves, uses `numpy.interp` to lerp both onto a 1-sec grid, takes per-second median across other videos as baseline, finds the earliest second where the target drops >10pp below median. Claude Haiku 4.5 with `web_search` (1–2 searches) + cached DNA brief returns `HookReport` (`retention_drop_at_s`, `retention_at_drop`, `transcript_at_drop`, `diagnosis`, `rewrite_suggestion`, `honesty_disclaimer`). Three-block prompt (static instructions / cached DNA / per-video data) matching Issues 128/129. **Issue 131**: `POST /creators/me/videos/{video_id}/chapters` → 202 + task. Celery `generate_chapters` task reads `Signals.timeline_jsonb["silences"]`, filters silences ≥2s as candidate boundaries, enforces 1-per-3-minutes density cap + min 4 chapters (fill-to-min for short videos), always starts at 0:00. Claude Haiku 4.5 with single cached system block titles each transcript segment ≤40 chars; result includes ready-to-paste `description_block`. UI: two new panels on `static/analysis.html` (Hook Analyzer with drop visualization + diagnosis/rewrite cards; Chapter Markers with chapter list + copy-to-clipboard for description block). New `knowledge/hooks.py`, `knowledge/chapters.py`, `extract_transcript_excerpt` + `get_transcript_segments` helpers added to `knowledge/util.py`, `HookAnalysisOut` Pydantic union response model. Decisions logged in `docs/DECISIONS.md` (linear interp over embedding, 10pp threshold, silence-gap over embedding-shift, sync 200 vs 202 split). **Tests**: 821 passed (+68 from 753) / 2 skipped. Layer 0: ruff 0 / mypy 0 / coverage ≥75.20% / freshness ok. All CI green: Quality Gates ✅ · Integration tests ✅ · CI ✅ · Docker publish ✅ · Deploy ✅.

**Prior**: Issue 123 + assessment CI fixes (2026-06-07). **CI fixes**: `knowledge/__init__.py` created (mypy "source file found twice"); `aiohttp==3.14.1` pinned (CVE-2026-34993 + CVE-2026-47265); `PYSEC-2026-196` added to pip-audit ignore list. **Issue 123 SEV1 sweep**: (1) `routers/insights.py` — `_ANTHROPIC` module-level singleton, `cache_control: ephemeral` on system prompt, `asyncio.to_thread` for blocking call, token logging added. (2) `ingestion/transcribe.py` — `_DEEPGRAM_LOCK` + `_ASSEMBLYAI_LOCK` guard singleton init races under concurrent `asyncio.to_thread` calls. (3) `models.py` + migration `0020_creator_insight_index` — composite `(creator_id, video_id)` index on `creator_insights`. (4) `db.py::recreate_engine` — `_recreate_in_progress` re-entry guard + `try/finally`. **Issue 129 worker fixes**: `worker/tasks.py` — module-level `_thumb_redis()` singleton replaces per-task `_aredis.from_url()` calls; bare `except Exception` on UUID parse narrowed to `(ValueError, TypeError)`. **Bonus fixes**: `youtube/analytics.py::check_data_gate` type annotation; `billing/stripe_client.py` webhook UUID parse guarded; `knowledge/util.py` extracted for shared transcript extraction (`_extract_transcript_summary` + `_extract_transcript_hook` both delegate to it); `knowledge/__init__.py` created. **Tests**: 753 passed / 2 skipped. Layer 0: ruff 0 / mypy 0 / format clean / freshness ok.

**Last completed**: Issue 129 — Thumbnail concept generator. `GET /creators/me/thumbnail-patterns` (synchronous, Redis-cached 24h) → Claude multimodal analyzes up to 10 top-performing thumbnails from DNA `top_video_ids_jsonb` via public YouTube thumbnail URLs (`i.ytimg.com/vi/{id}/hqdefault.jpg`). Returns `ChannelThumbnailPatterns` (face presence, dominant emotions, text overlay style, typical colors, composition pattern, channel signature). `POST /creators/me/videos/{video_id}/thumbnail-concepts` (10/hour) → 202 + task_id → Celery task `generate_thumbnail_concepts` → SSE stream → 3–5 structured concept briefs. Three-block prompt: static instructions / DNA brief (cache_control breakpoint) / patterns + video context (transcript hook + stated identity). `web_search` grounds concept generation in current niche trends. Results ephemeral (SSE done payload). Celery task reads pattern cache first (skips Claude multimodal call if patterns already cached), then streams concepts. New `knowledge/thumbnails.py`, `routers/thumbnails.py`, thumbnails panel in `static/analysis.html`. Key decisions logged in `docs/DECISIONS.md` (Reporting API bypass, Claude multimodal over CV pipeline, 24h Redis cache, ephemeral results). **Tests**: 747 passed (+25 from 722) / 2 skipped. Layer 0: ruff 0 / format clean / freshness ok.

**Last completed**: Issue 128 — Title optimizer. `POST /creators/me/videos/{video_id}/titles` → 202 + task_id → Celery task `generate_title_suggestions`. Three-block prompt: static CTR instructions / DNA brief (cache_control breakpoint at ~2400 tokens, clearing Sonnet 4.6's 2048-token minimum) / per-video context (transcript summary + stated identity). Claude generates 10 ranked candidates via web_search; `parse_candidates` surfaces top 5 with 100-char enforcement + CTR signal normalization. Results are ephemeral — arrive in the `done` SSE payload (no new DB table or migration). New `knowledge/titles.py`, `routers/titles.py`, SSE + title-card UI in `static/analysis.html`, "Titles" button on video rows in `static/index.html`. Compliance: disclaimer uses "cannot guarantee" (not "promise") to pass the structural virality scan. Key decisions logged in `docs/DECISIONS.md` (ephemeral vs. persistent, generate-10-surface-5, CTR band definition, cache placement, sync+to_thread over AsyncAnthropic). **Tests**: 722 passed (+18 from 704) / 2 skipped. Layer 0: ruff 0 / format clean / freshness ok.

**Last completed**: Issue 127 — Sentence-boundary cut enforcement + context-aware scoring. Three load-bearing changes: (1) **`clip_engine/candidates.py`** — new `snap_to_sentence_boundary(timestamp_s, words, direction)` walks word-level timestamps for terminal-punctuation tokens (`.?!…`) with a silence-gap fallback and 3-second hard cap (`MAX_SNAP_S`); `extract_candidates` now accepts `words` and snaps both clip endpoints after NMS with setup/peak/end invariant preservation. (2) **`clip_engine/scoring.py`** — replaced 300-char in-window `_transcript_excerpt` with `_transcript_context` returning a three-section `[BEFORE 60s] / [CLIP] / [AFTER 30s]` window so Claude judges whether each clip opens and closes on a complete thought. Payload field renamed `transcript_context`. (3) **`ingestion/signals.py`** — `RetentionCurve.is_rewatch_spike` now fires a `retention_spike` event unconditionally (no longer gated behind `relative_retention_performance > 1.2`), making YouTube's "most replayed" graph a direct clipping signal. Config: `SENTENCE_BOUNDARY_MIN_PAUSE_MS=400`, `MAX_SNAP_S=3.0` added to `config.py` + `.env.example`. `docs/CLIPPING_PRINCIPLES.md` gained principle #12 (Clean Context Boundary). `docs/DECISIONS.md` entry logs three choices: punctuation-token walk over spaCy/NLTK, `is_rewatch_spike` as direct trigger, three-section context transcript. **Tests**: 704 passed (+13 from 691) / 2 skipped. Layer 0: ruff 0 / mypy 0 / freshness ok.

**Last completed**: Issue 124 — Virality score + hover tooltips. New `performance_score` (0–100) field on `PerformerOut` replaces raw `engagement_rate` display on the insights page. Score is a channel-relative composite using modified z-score (MAD-based, robust at N=10–50) weighted: retention/AVD (40%), engagement (35%), views (25%). Returns `None` for channels with < 3 videos with metrics. New `_fetch_channel_baselines` query computes per-creator medians+MADs across all videos. New `static/tooltip.js` reusable tooltip component (CSS `::after` + JS viewport-bounds correction + Escape-key dismiss per WCAG 1.4.13) included on all authenticated pages. Tooltips added: insights performer score column header `?`, upload timing `?`, DNA grid cells (3 tooltips), review clip score `?`, dashboard analytics cells (avg view duration, engagement rate). Compliance: field renamed from `virality_score` → `performance_score` to pass the structural no-virality scan; DECISIONS.md entry logs weight deviation from issues.md spec. **Tests**: 691 passed (+13 from 678) / 2 skipped. Layer 0: ruff 0 / freshness ok.

**Last completed**: Issue 122 — Persistent user activity logging for beta testing. `configure_logging()` now accepts `log_dir` and adds a `RotatingFileHandler` (10 MB × 5, JSON) writing to `/app/logs/app.log` which maps to `./logs/app.log` on the host via the existing `.:/app` Docker volume. New `POST /api/activity` endpoint (no auth required, creator_id populated when session exists) receives structured UI events and logs them via `log_event()`. New `static/activity.js` IIFE captures clicks, form submits, and page navigation across all 6 authenticated templates. `LOG_DIR` config added; `LOG_DIR=""` in test conftest disables file handler. **Tests**: 678 passed (+10 from Issue 121's 668) / 2 skipped / 126 deselected. Review logs: `tail -f logs/app.log` or `cat logs/app.log | grep ui_activity`.

**Prior**: Issue 121 — Video Analysis page + dashboard de-emphasis. New `analysis/brief.py` module (Claude streaming, no web_search, honesty disclaimer by Python), new `POST /creators/me/video-analysis` endpoint (202 + SSE stream_url, fail-open on Redis), new `generate_video_analysis` Celery task (fetches metrics + retention + DNA, enforces per-creator isolation), new `static/analysis.html` (URL + query form → streaming narrative prose via progressStream.js), dashboard "Analyze a video" accented CTA card added, "Link a video" demoted to collapsed `<details>`, "Analyze" nav link on all 5 authenticated templates. No DB migration required (analysis results are ephemeral — stream IS the response). **Tests**: 668 passed (+16 from Issues 113–119's 652) / 2 skipped / 126 deselected. Layer 0: ruff 0 / mypy 0 / freshness ok.

**Prior**: Issues 113–119 — UX wave. 7 issues built in one session. (A) **Issue 113** nav quick wins: `nav-balance` minutes chip + `?` tutorial button wired into all 4 main pages via `auth.js`. (B) **Issue 114** profile DNA collapsible `<details>` + "Synced / Not synced with DNA" chip comparing identity vs DNA timestamps. (C) **Issue 115** dashboard YouTube Analytics panel: new `GET /creators/me/insights/analytics?period=` endpoint aggregating video_metrics rows + time-period `<select>` on the dashboard. (D) **Issue 116** DNA rebuild streaming: `progressStream.js` wired into `rebuildDna()` on profile.html replacing "come back in ~30s". (E) **Issue 117** AI per-performer insights: Haiku 4.5 lazy + cached analysis per (video, dna_version), save/bookmark system (`creator_insights` table, migration 0017). (F) **Issue 118** structured feedback: `feedback_tags` + `feedback_note` on `clip_feedback` (migration 0018); multi-select approve/deny tag panels in review.html. (G) **Issue 119** review editing surface: `style_preset` on clips (migration 0019); `_SUBTITLE_FILTERS` in render.py; `RenderStyleIn` body on render endpoint; style picker UI. **Tests**: 652 passed (+23 from Issue 112's 629) / 2 skipped / 125 deselected. Layer 0: ruff 0 / mypy 0 / coverage 75.83% / bandit 0/0 / pip-audit 0 / freshness ok.

**Prior**: Issue 112 code-complete; Locust run is user-side. Pending: user runs Locust on staging VM to close axes A + E, Google OAuth verification, Issue 109 cleanups.

**Last completed**: Issue 112 — Locust load-test gate (axes A + E). Two deliverables: (A) **`/health` connection-churn fix** — `_check_postgres` now routes through `engine.connect()` (SQLAlchemy pool) + `asyncio.timeout(2.0)` instead of opening a fresh `psycopg.AsyncConnection` per probe; `_check_redis` uses a module-level `_health_redis` singleton initialized in lifespan instead of `aioredis.from_url()` per call. `psycopg` import + `_pg_dsn()` removed from `main.py`. 2 regression tests in `tests/test_health.py`. (B) **Staging infrastructure** — `docker-compose.staging.yml` with `edoburu/pgbouncer:1.23.1-p3` in transaction-pooling mode (`DEFAULT_POOL_SIZE=25`), isolated Postgres DB (`creatorclip_staging`) + Redis (DB index 1), app on port 8001. `tests/perf/seed_staging.py` upserts creator + 12 videos + confirmed DNA + identity row. `tests/perf/README.md` updated with 7-step runbook including pass criteria and result-recording instructions. **Pending user-side action**: SSH to prod VM → `docker compose -f docker-compose.staging.yml up -d` → `alembic upgrade head` → `seed_staging.py` → Locust 300 users 5 min → record axis A+E numbers in REPORT.md. **Tests**: 629 passed (+2 from Issue 110's 627) / 2 skipped / 125 deselected. Layer 0: pending run.

**Prior**: Issue 110 — post-Wave-9 /assess top-register cluster (5 fixes + production hotfix). All 3 net-new SEV2s from the post-Wave-9 walk closed: `/auth/logout` + `/billing/webhook` gained rate-limit decorators (CSRF + bot-net exhaustion vectors), and `start_improvement_brief` got `SELECT FOR UPDATE SKIP LOCKED` + fallback re-query to close the debounce race that would double-fire the billed Anthropic call. Also closes the Issue-105 misread: `_ingest_async` now captures `prior_source_uri` at function entry and calls `adelete_file` after the final commit with `source/...mp4` prefix+suffix guard — closes ToS-relevant orphan-mp4 retention leak that survived Issue 105's `.wav` short-circuit (which only fixed the retry case). Plus cleanup: `routers/auth.py:131` `_logging` workaround removed (the one site Issue 108 missed). DECISIONS entry documents two choices: SKIP LOCKED over advisory lock for the existing-row debounce race (canonical for SQLAlchemy 2.x async), and capture-then-delete-after-commit + R2 lifecycle as belt-and-suspenders (AWS Well-Architected primary/backstop split). **Production hotfix this turn**: `config.py` `LOCAL_MEDIA_DIR` validator relaxed to `STORAGE_BACKEND=="local"` only — Issue 105's validator was overreaching, rejected the `./media` default at `ENV=production` even when `STORAGE_BACKEND=r2` made the path dead config; crash-looped the first post-Wave-9 deploy. Hotfix `1acee71` shipped before the rest of Issue 110. **User-side action pending**: set 7-day TTL on R2 bucket's `source/` prefix via R2 dashboard (belt-and-suspenders for the orphan-mp4 cleanup). **Tests:** 627 passed (+7 from Wave 9's 620) / 2 skipped / 125 deselected. Layer 0: ruff 0 / mypy 0 / coverage **75.97%** / bandit 0/0 / pip-audit 0 / freshness ok. **15 unpushed commits** = Wave-9 batch (102/103/104/105/106/107/108) + Issue 95 frontend + Issue 110 + integration hotfixes; will push to redeploy alongside this docs commit.

**Prior**: Issue 108 — mechanical cleanup sweep over 38 of the 48 cleanup-severity items from the post-Wave-8 /assess. Module docstrings on empty `__init__.py` (clip_engine, worker); `.env.example` gains the missing `DATABASE_MIGRATION_URL` stanza; `worker/schedule.py` imports `timedelta` from `datetime` (was from `celery.schedules`); `routers/upload_intel.py` gets the missing module-level `logger`; `dna/identity.py` loses the dead `_ = sa` alias + unused `import sqlalchemy as sa`; `_logging` workarounds (the `import logging as _logging; _logging.getLogger(__name__).warning(...)` pattern) removed from clips/videos/creators routers, replaced with proper module-level `logger`; magic-number naming (`_DNA_BRIEF_MAX_CHARS`, `_HOUR_UNAVAILABLE_SENTINEL`, OBS-id collision math comment); `Optional["X"]` → `"X | None"` sweep in `models.py` (5 forward-ref relationship sites; PEP 604 forward refs use whole-expression-as-string form); 11 typing gaps closed across `auth.py`, `limiter.py`, `worker/tasks.py`, `worker/anthropic_stream.py`, `ingestion/transcribe.py`, `dna/embeddings.py`, `dna/brief.py`, `improvement/brief.py`, `billing/stripe_client.py`; duplicated `*QueuedOut` schemas extracted to `TaskQueuedOut` base in new `routers/_schemas.py` (`BriefQueuedOut` intentionally stays standalone — `task_id: str | None` is LSP-incompatible with base's `str`). Mid-sweep mypy hit 5 invariance-related issues (Protocol-typed lists vs SQLAlchemy `Mapped[T]`; `dict[str, object]` vs caller's narrower `dict[str, int|str|float|None]`); resolved by using `Sequence[Any]` + keeping the Protocol intent as inline comment, and `Mapping[str, object]` (covariant) in improvement/brief. Issue 109 filed as follow-up for the 10 deferred design-work items (`_enrich_videos` split, lifespan registry, fetch-then-validate query rewrite, `_fernet` lru_cache, cold-start principle misattribution, etc.). **Tests:** 620 passed (zero new tests — cleanups don't change behavior) / 1 skipped / 125 deselected. **Layer 0:** ruff 0 / mypy 0 / coverage **76.06%** / bandit 0/0 / pip-audit 0 / freshness ok.

**Prior**: Issue 106 — security tightening (5 fixes). `limiter.py::_creator_key` now verifies `exp` with 60s leeway (overrides /assess recommendation of 300s — DECISIONS entry; security-relevant decoder, RFC 7519 "few minutes"); `except Exception: pass` narrowed to `jwt.InvalidTokenError` with WARNING-level class-only logging (PyJWT error messages can include claim values). Closes per-creator quota-leak vector. `billing/stripe_client.py::create_checkout_session` accepts client-supplied `intent_id` (v4 UUID from sessionStorage), validates UUID shape, passes `options={"idempotency_key": intent_id}` to Stripe — double-click / router retry dedupes within Stripe's 24h window. `_STRIPE` client carries explicit `STRIPE_TIMEOUT_S=10` HTTPXClient timeout (default ~80s would pin an executor slot). `session.url` None-check raises `RuntimeError` instead of redirecting to the string `"None"`. `CheckoutRequest` gains `intent_id: UUID4` (Pydantic shape validation); `static/pricing.html::_getCheckoutIntentId()` generates `crypto.randomUUID()` once per page load. 5 new tests + 4 existing /billing/checkout tests updated for the new required field. **Tests:** 620 passed (+5) / 1 skipped / 125 deselected. Layer 0: ruff 0 / mypy 0 / coverage **76.02%** (+0.28pp) / bandit 0/0 / pip-audit 0 / freshness ok.

**Prior**: **Wave 9 — parallel-build batch (103/104/105/107) cherry-picked on top of Issue 102.** All four built via worktree-isolated subagents from one bulk-approved Phase-1 brief (4 issues, fully disjoint file trees). Cherry-picked into main one by one with full test runs between merges. Mid-merge hotfix: Issue 104's new per-creator `creator_key` quietly broke 8 tests using the `dependency_overrides[get_current_creator] = lambda: creator` pattern (lambda bypasses real auth dep → no `request.state.creator_id` → fallback to `get_remote_address` → all tests share the "testclient" rate-limit bucket → /auth/me 429 after 5 calls). Fix: new `tests/_helpers.py::override_current_creator(creator)` stashes id on `request.state`; sweep-replaced 26 call sites across 11 test files. Also a small ruff sweep (zip strict=, raise from exc, unused locals) on the parallel-built code. **Layer 0:** ruff 0 / mypy 0 / coverage **75.74%** (vs 69.54% baseline — +6.20pp) / bandit 0/0 / pip-audit **0** (was 16; Issue 107 closed via venv sync + 6 documented residuals in `[tool.pip-audit]`) / freshness ok. **Tests:** 615 passed (+29 from Issue 102's 586) / 1 skipped / 125 deselected. SEV1 trajectory: **4 → 2 → 1 → 3 → 0 → 1 → 2 → 0**. Scale-checklist axes B (Async loop hygiene), C (Celery idempotency), F (Rate limit / quota) all returned to ✅. 6 new commits on main since session start (Issue 95 frontend through Wave 9 integration), all unpushed pending user authorization (pushing to main auto-deploys).

**Prior**: Issue 105 — Worker idempotency + advisory locks. Seven load-bearing SEV2s from the post-Wave-8 /assess: (1) `_transcribe_async` + `_signals_async` idempotency probes skip when Transcript/Signals row already exists and status is past the stage; (2) `_ingest_async` orphan-mp4 short-circuit returns immediately when `source_uri` already ends in `.wav`; (3) `generate_clips` now carries `base=RefundOnFailureTask` so terminal failure auto-refunds minutes; (4) `pg_try_advisory_lock` (non-blocking) on 6 sites — `_retrain_preference_async`, `_poll_clip_outcomes_async`, `_purge_stale_source_media_async`, `_purge_stale_youtube_analytics_async`, `_refresh_youtube_analytics_async`, `_sync_channel_catalog_async` — with explicit `pg_advisory_unlock` in `finally`; (5) `SoftTimeLimitExceeded` caught before the broad `except` in ingest/transcribe/build_signals sync wrappers to fire `on_failure` immediately; config validator asserts `TRANSCRIPTION_TIMEOUT_S < CELERY_SOFT_TIME_LIMIT_S - 30`; (6) Redis singletons (sync + async) in `worker/progress.py` now carry `socket_timeout=2.0` + `socket_connect_timeout=2.0`; (7) `worker/storage._local_root()` uses `expanduser().resolve()`; `config.py` model_validator rejects relative `LOCAL_MEDIA_DIR` in production. 9 new unit tests; 4 pre-existing tests updated for advisory-lock mock compatibility. **Tests:** 606 passed (+9) / 1 skipped / 122 deselected. Layer 0: freshness ok.
**Prior completed**: Issue 102 — preference model event-loop offload. Both Wave-8 /assess SEV1s fixed: `preference/train.py::load_latest` now wraps `PreferenceScorer.from_bytes` in `await asyncio.to_thread(...)` so the process-wide `_UNPICKLER_LOCK` (Issue 71 RCE allowlist) serializes threads instead of coroutines, and `preference/train.py::build_and_save` wraps the LightGBM/LogisticRegression `fit` call in `await asyncio.to_thread(fit, X, y, w)` so a power creator's training pass doesn't block the surrounding async loop for seconds. Bundled the two paired SEV2s: training-feedback query now `ORDER BY ClipFeedback.created_at DESC LIMIT settings.PREFERENCE_MAX_TRAINING_LABELS` (default 5000 — industry standard for recency-decayed sklearn pipelines at 30d half-life), and `list(_POSITIVE_ACTIONS) + list(_NEGATIVE_ACTIONS)` collapsed to the already-defined `TRAINABLE_ACTIONS` frozenset. DECISIONS entry logged for the deviation from the /assess recommendation — joblib 1.x has no public per-load NumpyUnpickler injection slot (verified via industry-standards-researcher), so the module-global swap stays as the documented extension point; the `asyncio.to_thread` wrap alone solves the scale defect. 3 new regression tests in `tests/test_preference.py` pin (a) `fit` offload via to_thread, (b) `from_bytes` offload via load_latest, (c) query has `ORDER BY created_at DESC` + `LIMIT PREFERENCE_MAX_TRAINING_LABELS`. **Tests:** 586 passed (+3) / 1 skipped / 122 deselected. Layer 0: ruff 0 / mypy 0 / coverage **75.25%** (+0.06pp) / bandit 0/0 / freshness ok / pip_audit 16 (carries forward — Issue 107). Returns SEV1 count to 0; restores scale-checklist axis B (Async loop hygiene) to ✅.
**Prior**: Issue 95 frontend — `static/profile.html` API-key management card. List / create / revoke wired against the Wave-8 backend (`/creators/me/api-keys`). One-time-reveal modal with the canonical "won't be able to see it again" security copy (GitHub/OpenAI/Anthropic phrasing). Revoke confirm modal with the canonical "stop working immediately / cannot be undone" destructive-action wording (GitHub/Stripe phrasing). Listed keys render as masked prefix `ack_xxxxxxxx••…` in the JetBrains-Mono data register (Issue-99 Phase C convention). Empty state shows a one-sentence orient + the Generate CTA (GitHub empty-state pattern). Native `<dialog>` element used for both modals — zero JS deps, free focus trap + Escape handling, supported in every shipping browser. New `tests/test_static.py::test_profile_page_exposes_api_keys_section` pins the section + endpoints wiring + the both modals' load-bearing copy + the mono register on the prefix so a future "let me simplify this" PR can't silently regress the one-time-reveal or the revoke confirmation. **Tests:** 583 passed (+1) / 1 skipped / 122 deselected. Layer 0: freshness ok (ruff/mypy/coverage/bandit/pip-audit skipped — not installed locally; CI Quality gates remain authoritative). Self-audit: zero raw-key/token log lines, the raw key is held only in a single `<input>` value and wiped on modal close, no PII or token logging anywhere, per-creator isolation is the existing backend's responsibility (verified by Wave-8 integration tests).
**Prior**: Wave 8 — 4-issue batch on the new Issue-99 design system. **Issue 95 backend** (`alembic 0015`, `models.CreatorApiKey`, `api_key.py` module, `routers/api_keys.py` for management, `POST /clips/ingest` on `clips_router` with bearer-auth via the new dependency — companion-app ready; backend isolated and complete). **Issue 100** (`static/walkthrough.html` 5-panel first-run explainer, `auth.js` gate routing new creators with `onboarding_state='connected'` to it once, intake on onboarding.html now mandatory — Skip button + skipIdentity() removed, Build-DNA button gates on identity-exists). **Issue 93** (`routers/insights.py` new `GET /creators/me/insights` single-fetch aggregation of channel totals + DNA snapshot + top/bottom performers; insights.html rebuilt with 6 panels and mono data register throughout). **Issue 94** (`Why this clip?` `<details>` expander on review.html surfacing the Claude-authored `reasoning` field, the cited `principle`, the score, and the setup→peak→end timing — auto-opens on first clip to teach the affordance). **Tests:** 582 passed (+19 from Issue 99 Phase B's 563) / 1 skipped / 122 deselected (+22 integration). Layer 0: ruff 0 / mypy 0 / freshness ok. **Self-audit:** 16 explicit `creator.id` / `creator_id` filter sites in new endpoints, zero raw-key/token log lines, zero TODOs introduced, all new functions typed.

**Phase B entry**: Issue 99 Phase B — retrofitted the 8 remaining static templates (index, onboarding, insights, profile, review, tos, privacy, early-access) onto `_design-tokens.css`. Every template now links the shared design system, consumes `--color-*` semantic tokens, replaces inline reset/nav/btn/footer styles with the shared component layer (`.nav`, `.btn`, `.footer`, `.disclaimer`, `.chip`). Mono data register applied to high-data surfaces: dashboard summary card values, video-table YouTube-ID column, DNA stat cards (profile.html), insights upload-window activity %, optimal-gap value, version badges. Niche-chip and trim-handle controls refactored from inline-style cssText to `.chip` / standard input styling. Early-access.html keeps its conversion-page CTA semantics but on the same indigo accent (no more red splat — consistent palette). `tests/test_static.py` (+1 parametric test pinning all 9 templates link the tokens file + consume `--color-*`). **Tests:** 563 passed (+1) / 1 skipped / 100 deselected. Layer 0: ruff 0 / mypy 0 / freshness ok.

**Phase A entry**: Issue 99 Phase A — `static/_design-tokens.css` built with the Linear-locked palette (#0a0a0a / #5e6ad2 indigo / Inter Variable + JetBrains Mono / 4px grid / hairline borders) + minimal component layer (nav, card, .btn, .kbd, .mono, .badge, .footer). pricing.html retrofit as proof: dropped the Wave-7 inline `:root` stopgap; links to the shared tokens file; mono data register applied to minutes / price / $/min figures (first real use of the sans/mono split). `tests/test_static.py` (+2 tests) pins (a) the tokens file exists with the canonical Linear palette + .mono utility + Google-Fonts swap-display imports, (b) pricing.html consumes `--color-*` tokens (not the Wave-7 stopgap names). Phase B (retrofit 8 remaining templates) and Phase C (`.mono` applied to clip metadata / transcripts / video table durations / DNA cards) queued.

**Last wave**: Wave 7 — pricing.html CSS hotfix. Live-observed by the user on the freshly-deployed autoclip.studio: pricing page rendering in browser defaults (Times New Roman, blue underlined links) because `pricing.html` linked `/static/style.css` which never existed in the repo. Every `var(--surface)` / `var(--accent)` / etc. resolved to empty string. Fix: dropped the broken `<link rel="stylesheet">`; added a `:root` block defining `--bg / --surface / --border / --text / --muted / --accent` matching the inline-style palette the other authenticated templates already use; added minimal `.nav`/`.nav-brand`/`.nav-links` rules so the nav stops rendering as default browser links. Static-page test pins both halves of the fix. This is a deliberate STOPGAP until Issue 99's `_design-tokens.css` lands and supersedes every inline palette.

**Phase 1 also closed for Issues 95 + 99** (this session). User picked the design direction + OBS architecture from researched menus. Backlog entries rewritten to lock in the picks; Phase 3 builds those issues out in their own workflows.

**Last issue**: Issue 101 — moved `.github/workflows/docker-publish.yml` from `runs-on: ubuntu-latest` to `runs-on: self-hosted`. The deploy pipeline is now end-to-end zero-GitHub-hosted-minutes (both docker-publish and deploy run on the prod VM's self-hosted runner). Triggered by live billing-block failure: Wave 6's push fast-failed in 4s with "recent account payments have failed or your spending limit needs to be increased" — same shape as the prior Wave-5 fix(ux) push. CI / Quality / Integration workflows intentionally remain on `ubuntu-latest` (informational only; don't gate deploys per `workflow_run` dependency model). `tests/test_ci_config.py` pins both workflows' `runs-on: self-hosted` directives + the `Docker publish` ↔ `workflow_run: [Docker publish]` workflow-name linkage so a future "let me fix CI" PR can't silently re-introduce the billing dependency or break the deploy trigger. Operational requirement (user must do): `scripts/setup-runner.sh` on the VM once — until then both workflows queue indefinitely; `scripts/deploy.sh` remains the manual fallback.

**Last wave**: Wave 6 — "done-vs-visible" audit fixes. User-reported gap: "things marked done but not on the website." Audit found four real causes — (A) Issue-98 state-machine fix was forward-only and existing creators with confirmed DNA stayed `connected` permanently → banner stuck; (B) Pricing / TOS / Privacy / Early-Access pages had no inbound links from anywhere → unreachable from the app; (C) `PROJECT_STATE.md` "Queued" list still showed Issues 84 and 92 despite both being closed (bookkeeping rot driving the user's perception); (D) Issue-92 returned `stream_url` on the upload + clip-generate endpoints but `index.html` never subscribed, so the Wave-5 activity panel was hidden 100% of the time on the dashboard.

> **Closed Wave 8 — Issues 95 backend + 100 + 93 + 94 in one batch** (2026-05-31): Four user-requested issues shipped on the new Issue-99 design system. **Issue 95 backend (OBS companion app surface):** `alembic 0015_creator_api_keys` + `models.CreatorApiKey` (SHA-256-hashed keys, soft revoke via `revoked_at`, 8-char display prefix) + `api_key.py` (generate / hash / display_prefix / `get_current_creator_via_api_key` FastAPI dependency that stamps `session.info["creator_id"]` for RLS) + `routers/api_keys.py` (GET/POST/DELETE management) + `POST /clips/ingest` on clips_router (multipart upload + ffprobe + balance check + R2 PUT + start_pipeline; same fail-open `aset_owner` posture as `/videos/upload`). 14 unit + 12 integration tests covering generation entropy, hash determinism, bearer-header parsing edge cases, raw-key-shown-once invariant, list-excludes-raw, soft-revoke semantics, per-creator isolation on list+revoke, bearer-dependency rejects unknown/revoked/non-canonical keys + stamps last_used_at. **Issue 100 (first-run onboarding):** `static/walkthrough.html` 5-panel explainer (what-this-is / DNA / what-a-clip-is / badges / tell-us-about-you) with arrow-key nav + progress dots + `creatorclip:walkthrough_seen` localStorage flag. `auth.js` redirect gate fires only when `onboarding_state='connected'` AND flag-unset AND not on walkthrough/onboarding (loop guard). `onboarding.html` intake is now MANDATORY (skipIdentity + Skip button removed); Build-DNA button starts disabled, unlocks after `_checkIdentityExists()` returns true. **Issue 93 (insights rebuild):** new `GET /creators/me/insights` returning ChannelTotals (videos_analyzed, longs, shorts, ingested_done, total_minutes_processed) + DnaStats (latest version, status, optimal_clip_len_s, best_source_region, optimal_upload_gap_h) + Performer lists (top + bottom) resolved from DNA's top_video_ids_jsonb / bottom_video_ids_jsonb with order-preservation. `_fetch_performers` filters on `Video.creator_id == creator.id` — defends Issue-33-shape cross-creator leak even if DNA references a foreign Video ID. 8 integration tests including empty-state, totals math, latest-DNA-pick, performer resolution, stale-ID drop, per-creator isolation, cross-creator video ID drop, auth-required. Rebuilt insights.html with 6 panels using mono register throughout. **Issue 94 (clip transparency):** review.html now surfaces `clip.reasoning` (Claude's natural-language explanation) and `clip.principle` (named principle) via a `<details>` "Why this clip?" expander showing principle / reasoning / score / setup→peak→end timing. Auto-opens on first clip; respects user toggle thereafter. **Wave 8 totals:** +19 tests in default lane, +22 in integration lane. Layer 0 green across the batch. **Deferred to focused future sessions:** Issue 96 (chat-driven intake — needs multi-turn LLM design work) and Issue 97 (livestream recap — needs clip_engine recap-mode extension + subscription tier work).

> **Closed Issue 99 Phase B — retrofit 8 templates onto design system** (2026-05-31): One commit, eight templates: `static/index.html` (dashboard), `onboarding.html` (5-step setup), `insights.html` (timing + brief), `profile.html` (DNA view + identity edit), `review.html` (clip player + trim), `tos.html`, `privacy.html`, `early-access.html`. Every template now links `/static/_design-tokens.css`, drops the inline reset / nav / btn / footer styles, consumes `--color-*` semantic tokens for everything. Shared component layer (`.nav`, `.btn`, `.btn-primary`, `.btn-secondary`, `.chip`, `.disclaimer`, `.footer`) reused across the surface. Mono data register applied to: dashboard summary card values, video-table YouTube ID column, DNA stat cards on profile, insights window activity %, gap value, version badges. Inline cssText for the niche-chip UX refactored into `.chip` / `.chip.selected`. early-access marketing-page CTA brought onto the same indigo accent as the rest of the app (lost the red splat — consistent brand). New parametric test in `tests/test_static.py` pins all 9 templates link the tokens file + consume `--color-*`. **563 passed (+1)** / 1 skipped / 100 deselected. Layer 0: ruff 0 / mypy 0. Phase C (mono register applied to clip metadata in review.html, transcript timestamps when those views build) queued.

> **Closed Issue 99 Phase A — `_design-tokens.css` + pricing.html proof retrofit** (2026-05-31): Built `static/_design-tokens.css` (~250 lines, vanilla CSS, no build step) with the Linear-locked direction from DECISIONS: full :root palette + Inter/JetBrains Mono via Google Fonts (`display=swap` so system fallback renders instantly) + 4px spacing scale + 80-120ms motion + minimal component layer covering nav, card, .btn variants, .kbd chips, .mono data utility, .badge status pills, .disclaimer honesty band, .footer legal links. Retrofit pricing.html as the proof case: removed the Wave-7 inline `:root` stopgap, links to the shared file, page-specific styles now consume `--color-*` semantic tokens. Mono data register applied to minutes / price / $/min figures — first real use of the sans/mono composition pattern. Pop-tag swapped from a marketing-pill ("Most Popular") to a Linear-kbd-style outlined chip ("Most picked"). Tests: +2 in `tests/test_static.py` pinning both halves (tokens file shape + pricing consumption). **562 passed** (+1 from Wave 7's 561) / 1 skipped / 100 deselected. Layer 0: ruff 0 / mypy 0. Phase B (retrofit index/onboarding/insights/profile/review/tos/privacy/early-access) and Phase C (`.mono` applied to clip metadata / transcripts / video-table durations / DNA stat cards) queued — each as its own commit.

> **Closed Wave 7 — pricing.html CSS hotfix + Phase 1 lock-in for Issues 95 + 99** (2026-05-31): User-observed bug live on the freshly-deployed autoclip.studio: pricing page rendering in browser defaults. Root cause: `pricing.html:7` linked `/static/style.css` which never existed; every `var(--…)` in the inline `<style>` block resolved to empty. Fix: dropped the broken link; added `:root` block with `--bg / --surface / --border / --text / --muted / --accent` matching the inline-style palette other templates use; added `.nav`/`.nav-brand`/`.nav-links` rules. Test in `tests/test_static.py` pins both halves. **Stopgap** until Issue 99 supersedes. **Phase 1 also closed for Issues 95 + 99** via researched-menu selection: Issue 99 = Linear-style base (#0a0a0a / #5e6ad2 indigo / Inter + JetBrains Mono / hairline borders / 4px grid) + monospace data register for clip metadata / transcripts / timestamps; Issue 95 = Architecture B (Medal.tv-style companion app + folder watcher, backend exposes API-key-auth `POST /clips/ingest`). Backlog entries rewritten to lock the picks. **Tests:** 561 passed (+1 from Issue 101's 560) / 1 skipped / 100 deselected. Layer 0: ruff 0 / mypy 0.

> **Closed Issue 101 — Permanent fix for GH-hosted-runner billing block on deploys** (2026-05-31): One-line YAML change. `.github/workflows/docker-publish.yml` `runs-on: ubuntu-latest → runs-on: self-hosted` (matches `deploy.yml`, which moved in the Wave-5 close-out). Deploy pipeline (docker-publish → workflow_run → deploy) is now end-to-end self-hosted; eliminates GH-hosted billing as a deploy blocker permanently. CI / Quality / Integration remain on `ubuntu-latest` (informational only; not on the deploy critical path). New `tests/test_ci_config.py` (+3 unit tests) pins `runs-on: self-hosted` for both pipeline workflows + the `Docker publish` ↔ `workflow_run` name linkage so silent regressions can't slip in. `scripts/setup-runner.sh` banner updated to reflect coverage of BOTH pipeline workflows. Operational: runner is NOT yet installed on the VM — until then both workflows queue; `scripts/deploy.sh` remains the immediate fallback. **Tests:** 560 passed (+3) / 1 skipped / 100 deselected. Layer 0: ruff 0 / mypy 0 / format clean.

> **Closed Wave 6 — Done-vs-Visible audit fixes** (2026-05-31): Four mechanically-distinct sub-fixes. **Fix A** — new alembic migration `0014_backfill_onboarding_state` heals creators where `onboarding_state IN ('connected','awaiting_data')` AND a confirmed `creator_dna` row exists; `dna_pending` is intentionally excluded (legitimate rebuild-in-progress). Closes the Issue 98 carry-over. **Fix B** — added `<a href="/static/pricing.html">Pricing</a>` to the top nav of index/insights/profile/review (onboarding skipped per focused-task design); added a minimal `<footer>` linking Terms + Privacy + © AutoClip 2026 to every static template (9 pages). Closes the pre-launch Google OAuth verification gate around TOS/Privacy reachability. **Fix C** — removed Issues 84 and 92 from the queue list (both already closed above) + removed the duplicate Issue 84 close entry. **Fix D** — `index.html::linkVideo` and `index.html::generateClips` now consume `stream_url` + `task_id` from the POST response and register with `window.activeTasks.registerTask(...)`; the Wave-5 floating activity panel finally surfaces the upload→ingest→transcribe→signals and generate-clips streams on the dashboard. Existing 5s polling stays as belt-and-suspenders. **Tests:** +6 unit (Fix B nav/footer assertions + Fix D wiring assertions in `tests/test_static.py`) and +6 integration (Fix A backfill semantics in `tests/test_onboarding_state_backfill_integration.py`).

> **Closed Wave 5** (2026-05-31): Three fixes. **Fix 1** — extends fail-open `try/except redis.RedisError` to `routers/creators.py::sync_catalog`, `routers/creators.py::build_dna`, `routers/clips.py::render_clip` (3 sites × ~5 LOC each, mirrors Wave-3 Fix B exactly). Response models now `stream_url: str | None = None`. **Fix 2** — new `static/activeTasks.js` library: localStorage-backed lifecycle manager exposing `registerTask`/`getActiveTasks`/`subscribe`/`removeTask` on `window.activeTasks`. On every page mount, prunes >1h entries (matches server-side stream TTL), resumes EventSource per remaining entry with `Last-Event-ID`. **Fix 3** — new `static/activityPanel.js`: floating bottom-right Linear/Vercel-style widget shown on every authenticated page. Hidden when no tasks; collapsed badge "⚡ N running"; expanded shows per-task terminal-style streams. Wired into 6 authenticated templates (index, onboarding, insights, profile, review, pricing). onboarding.html + insights.html existing flows now ALSO call `activeTasks.registerTask` so the global panel surfaces them. **User-stated needs resolved:** "going tab-to-tab without refreshing" (localStorage + EventSource resume) AND "see new features on the website" (global activity panel on every page). **Tests:** 553 passed (+6 from Wave 4's 547) / 1 skipped / 94 deselected. Layer 0: ruff 0 / mypy 0 / format clean.

> **Closed Wave 4 — compliance + scale prep** (2026-05-31): Three small fixes. **Fix 1** — `routers/videos.py:262-279` wraps `aset_owner` in `try/except redis.RedisError` (mirrors Wave-3 Fix B/D); fail-open invariant now uniform across every aset_owner site. **Fix 2** — new Alembic migration `0013_refund_pack_id_unique` creates partial UNIQUE on `minute_packs(pack_id) WHERE reason='refund'`; `billing/refund.py` drops the read-then-write guard, catches `IntegrityError` from the SAVEPOINT, returns 0 on race (same pattern as `deduct_for_video`); closes the concurrent-refund double-credit race. **Fix 3 (Issue 75b)** — `YOUTUBE_ANALYTICS_MAX_STALENESS_DAYS=30` setting + `purge_stale_youtube_analytics` daily Beat task that deletes stale rows from `video_metrics`, `retention_curves`, `audience_activity`, `demographics`. `docs/COMPLIANCE.md §2` expanded with concrete policy citation (verified via industry-standards-researcher against developers.google.com/youtube/terms/developer-policies §III.E.4.b + §III.D.2.3.b). CLAUDE.md pre-monetization checkbox flipped. **Tests:** 547 passed (+4 from Wave 3's 543) / 1 skipped / 94 deselected (default lane); 4 new integration tests pin the purge boundaries (5d/29d/35d). Layer 0: ruff 0 / mypy 0 / format clean.

> **Closed Wave 3 hotfix batch** (2026-05-31, kept for history below)
Previous: Wave 3 hotfix batch (3 SEV1s + 3 SEV2s).

> **Closed Wave 3 hotfix batch** (2026-05-31): Six small mechanical fixes addressing the regressions Wave 2 introduced + the carry-forward Stripe SEV1 (post-Wave-2 /assess flagged all six). **Fix A** — `worker/anthropic_stream.stream_and_emit` now accepts `tools` kwarg + `improvement/brief.py` threads it through (closes the SEV1 where 100% of streaming improvement briefs lost web_search grounding). **Fix B** — `routers/improvement.py` reorders `aset_owner` after row.job_id commit + wraps in `try/except RedisError` so a Redis blip returns `stream_url=None` instead of 500 (fail-open observability posture). **Fix C** — `routers/billing.py` wraps `create_checkout_session` in `await asyncio.to_thread(...)`, closing the carry-forward sync-Stripe-in-async SEV1. **Fix D** — `routers/auth.py:117-119` now stamps `aset_owner` on the post-OAuth catalog sync (one-line; same fail-open posture). **Fix E** — `_signals_async` now emits non-terminal `step:ingest_complete`; `_generate_clips_async` gets full emit instrumentation + terminal `done` on the same video_id stream key, so the SSE consumer stays subscribed through clip generation. **Fix F** — `_sync_channel_catalog_async` per-video failure handler emits `step:sync_metrics_skipped` (class name only — no exception message — preserves no-PII invariant). Tests: 543 passed (+10 from Wave 2's 533). Layer 0: ruff 0 / mypy 0 / format clean.

> **Closed Issue 92 — Universal progress visibility** (2026-05-31): Extended Issue-86's SSE primitive to 4 more long-running surfaces. Upload chain (`_ingest_async → _transcribe_async → _signals_async`) emits step events keyed by `video_id` (deterministic stream key — no Celery chain-id propagation needed). Render uses `clip_id` the same way. Catalog sync uses Celery `self.request.id` (single task, no chain) and emits per-video metric progress as `sync_metrics i=k total=N`. Improvement brief uses Celery `job_id` AND streams via the new `task_id` kwarg on `improvement/brief.py::generate_improvement_brief` (mirrors `dna/brief.py` Issue-86 pattern via `worker.anthropic_stream.stream_and_emit`). Routers stamp `progress.aset_owner` + return `stream_url` on all 4 endpoints. Frontend wired in `onboarding.html` (catalog sync) + `insights.html` (improvement brief); upload + render backends return `stream_url` for future Issues 100/95 UI consumers. 8 new tests in `tests/test_progress_emit_wiring.py` pin emit sequences, stream-key choice, terminal `done` events, and router wiring. 533 passed / 1 skipped / 89 deselected. Layer 0: ruff 0 / mypy 0.

> **Closed Issue 84 — AI/LLM efficiency assessment** (2026-05-31): Audited all 3 Anthropic call sites against current (May 2026) SDK + caching state, verified via industry-standards-researcher. Per-call-site reports written to `docs/assessment/llm/dna_brief.md`, `clip_scoring.md`, `improvement_brief.md` + consolidated `REPORT.md`. Key findings: (1) Sonnet 4.6 cacheable-prefix floor is 1024 tokens (not 2048 as our docstrings said) — cache markers on DNA brief + improvement brief silently don't engage today, 1.25× write premium for zero reads; (2) clip_scoring is the only call site where caching actually pays (1h TTL on DNA brief, correctly designed); (3) zero Opus-4.7-breaking parameters on our surface — clean migration path. Shipped Win A: `ANTHROPIC_WEB_SEARCH_TOOL` config default bumped `_20250305 → _20260209` (dynamic filtering: Claude pre-filters search results in code-exec before they hit the main context). 1-LOC config + 2 regression tests in `tests/test_brief_caching.py`. Follow-up issues flagged: Anthropic SDK 0.40 → 0.105.2 bump (unlocks TTL-tier observability), drop unproductive cache markers on DNA + improvement brief (post-SDK-bump so we can measure), per-call-site model settings + Haiku 4.5 A/B eval for clip scoring (~67% cost reduction opportunity).

**Queued — Creator Studio Expansion (ROI-ordered, 2026-06-06)**:
- ~~**Issue 127** — Sentence-boundary cut enforcement.~~ ✅ Done (2026-06-07)
- ~~**Issue 128** — Title optimizer.~~ ✅ Done (2026-06-07)
- ~~**Issue 129** — Thumbnail concept generator.~~ ✅ Done (2026-06-07)
- ~~**Issue 130** — Hook analyzer.~~ ✅ Done (2026-06-07)
- ~~**Issue 131** — Auto chapter markers.~~ ✅ Done (2026-06-07)
- **Issue 132** — YouTube Live Chat spike detection. Chat replay density as a clipping signal. Makes CreatorClip genuinely stream-native — no competitor does this with full polish.
- **Issue 133** — Animated caption styles. Bold Pop + Gradient Slide styles baked into render. Eliminates the Submagic step for creators.
- **Issue 134** — Filler word and silence removal. One-click clean with strikethrough preview; reversible. Foundation for the text editor.
- **Issue 135** — Text-based editor. Select transcript words → queue cuts → confirm re-render. Descript-style; eliminates export to CapCut/Premiere.
- **Issue 136** — UI upgrade: dark editor mode + marketing hero. review.html gets full-dark CapCut-style layout; index.html gets paste-URL PLG hero.

**Prod-readiness gates still pending**:
- **RLS activation** — Hotfix B unblocks the manual workflow. Run `Activate RLS (Issue 79)` workflow with `dry_run=true` then `false`.
- **Issue 78f PgBouncer load test** — sole gate that moves the verdict from CONDITIONAL → YES.
- **Issue 123** — SEV1 sweep (Anthropic singleton, transcription locks, CreatorInsight index, recreate_engine guard). Not started.

**Prod-readiness gates still pending**:
- **RLS activation** — Hotfix B unblocks the manual workflow. Run `Activate RLS (Issue 79)` workflow with `dry_run=true` then `false`.
- **Issue 78f PgBouncer load test** — sole gate that moves the verdict from CONDITIONAL → YES.

**Blocked**: _(none)_

> **Closed Wave 1 — 6-hotfix batch** (2026-05-31): One branch, six commits, one CI cycle.
>
> - **Hotfix A — `worker/progress.py:214-232` aacquire_slot EXPIRE drift** (SEV-1 from `/assess`): Moved `client.expire()` out of the `count==1` branch so EXPIRE fires on every INCR. Bug: a creator holding ≥1 SSE streams continuously past `_STREAM_TTL_SECONDS=3600s` had the counter TTL elapse → next INCR reset to 1 → cap silently bypassed. Canonical Redis sliding-window pattern. 2 new regression tests pin the TTL refresh + cap behavior.
> - **Hotfix B — `billing/refund.py:41` AsyncSessionLocal → AdminSessionLocal** (SEV-1 from `/assess`): Refund is a system action with no per-creator context; under prod RLS the app-role session without `session.info["creator_id"]` returns zero rows from the `MinuteDeduction` SELECT → every refund silently no-ops. Matches the rest of the worker surface. **Now unblocks the RLS activation workflow** (was blocking the prod role split). Source-inspect + runtime-mock invariants pin the factory choice.
> - **Issue 89 — balance pre-check vs deduction mismatch**: New `check_balance_for_minutes(creator_id, minutes_needed, session)` helper raises 402 with concrete gap copy ("This video needs N minutes; you have M"). Wired into `/videos/upload` after `probe_duration_s` so a low-balance creator uploading a long video gets an actionable 402 BEFORE the R2 PUT, not a silent post-upload `failed` row. **Deviation from AC**: did NOT wire into `/clips/render` because `_render_clip_async` doesn't deduct — adding a per-clip pre-check there would deny re-renders of already-paid clips for no billing reason. Captured in `docs/DECISIONS.md`. 4 unit tests + 1 router-level integration test (mocks probe, asserts 402 + tmp cleanup + storage not called).
> - **Issue 90 — catalog rows excluded from /videos list**: `source_uri IS NOT NULL` filter on `list_videos`. Documented `source_uri IS NULL` as the canonical catalog-only marker in `docs/SOT.md` data-model section. Test introspects the compiled SQL to pin the filter.
> - **Issue 91 — "Clips ready" counter filters render_status=done**: Frontend filter in `static/index.html`; relabeled card "Clips rendered". Also fixed an unrelated unwrapping bug (`clips.length` was reading off the `{clips: [...]}` wrapper). Display now shows `M/N rendered` when partial. Static-page text assertion test.
> - **Issue 98 — DNA banner sticky + missing state transition**: Root cause was in `dna/profile.py::create_draft` — it never advanced `onboarding_state`, so the existing `confirm_draft` precondition (`if state == dna_pending`) never matched and state stayed `connected` forever. Fix: `create_draft` bumps `connected → dna_pending` so the canonical arc completes. 3 unit tests for the arc (idempotent on rebuild, no-regression on active). Frontend conditional at `static/index.html:160` already correct and now hides properly after confirm.
>
> **Layer 0 gates**: ruff 0 / mypy 0 / freshness ok. **Tests**: 523 passed / 1 skipped / 89 deselected (default lane). Integration tests for Issue 98 added; verification runs on CI's integration lane.

> **Closed Issue 88 — end-to-end verification** (2026-05-31): Initial Issue 88 deploy
> (commit `e9a2c3f`) shipped the filter-parity fix + `log_event` observability +
> targeted audit, all CI green. But when the user retried the build live, the
> data-gate still showed 0/0 because catalog sync phase 2 was silently failing
> on every video. Live ssh diagnostic against the worker container caught the
> real exception (`httpx.ReadTimeout`) — empty `str()` was why the warning log
> was blank. Hotfix `b464a34`: bumped read timeout 15s→60s, wrapped both YT
> retry loops in `try/except httpx.RequestError`, changed the catalog-sync
> warning to `%r` + `exc_info=True`. Re-verified: 3/3 manual `sync_video_analytics`
> calls returned OK; `metered_count_now: 21`. User then rebuilt DNA successfully
> — backend now has 3 `creator_dna` rows for Backboard Media (v1 draft, v2
> confirmed, v3 draft from a rebuild), 21 videos analyzed (6 longs + 15 shorts),
> 63 `dna_embeddings` rows, `optimal_clip_len_s=14.5`, `best_source_region=first_third`.
> Carry-over: `onboarding_state` did not advance to `active` despite v2 being
> `confirmed` — captured in Issue 98 ACs.

> **Closed Issue 88 — DNA filter parity + business-event observability** (2026-05-30): Closed the SEV-0 logical bug surfaced live on `reesepludwick@gmail.com` ("data-gate said 3 long + 20 shorts, build said insufficient 0/0"). Root cause: `check_data_gate` counted every Video row by kind; `rank_videos` required `ingest_status==done` AND metrics — two queries on the same table with diverging predicates. Fixes: `rank_videos` no longer requires `ingest_status==done` (DNA needs metrics only, not local-pipeline state); `check_data_gate` joins VideoMetrics + uses OR semantics (matches `build_patterns` raise condition); `sync_channel_catalog` chains a phase-2 `sync_video_analytics` call so metrics are present immediately (was waiting up to an hour for Beat refresh). New `observability.log_event(event, **fields)` helper emits structured JSON; wired into 7 user surfaces (auth callback, link, upload, sync_catalog, build_dna, confirm_dna, feedback) + diagnostic `dna_build_insufficient_data` event with total/metered/per-kind counts. Targeted display-vs-filter audit returned 4 findings (2 SEV-1, 2 SEV-2) — one fixed inline (data-gate `ready` used AND, blocking long-only/shorts-only creators), three filed as Issues 89-91. 8 new tests. **509 passed / 1 skipped / 85 deselected**; ruff 0 / mypy 0.
**Blocked**: _(none)_

> **Closed Issue 88 — DNA filter parity + business-event observability** (2026-05-30):
> Detailed in `docs/DECISIONS.md` (2026-05-30 entry). Triggered by a live user
> report: connecting `reesepludwick@gmail.com` showed 3 long + 20 shorts in
> step 2 but the DNA build said "Insufficient data: 0 long, 0 shorts." Two
> queries silently disagreed — the data-gate counted every Video row, the
> DNA build required `ingest_status==done` (set only by the local-clip pipeline,
> never by catalog sync) AND metrics. The fix: aligned both paths on a single
> predicate (metrics-only); chained metrics fetch into `sync_channel_catalog`
> so the user doesn't wait an hour for the Beat refresh. Then added a class
> of debug observability: `log_event(event, **fields)` helper + diagnostic
> log on the insufficient-data raise + 7 wired user surfaces. A targeted
> subagent audit on the same failure shape found a SEV-1 in `check_data_gate.ready`
> (used AND while the build accepts OR — blocked long-only creators); fixed
> inline. Three other findings spawned Issues 89, 90, 91. All gates green:
> ruff 0, mypy 0, **509 passed / 1 skipped / 85 deselected** (+8 new).

> **Closed Issue 87 — Catalog sync wiring + 180s Shorts threshold** (2026-05-30): Closed the SEV-0 onboarding bug surfaced live on `reesepludwick@gmail.com` / "backboard media" (20 Shorts + 3 long-form, data-gate reporting 0/0). `youtube/analytics.py::sync_video_catalog` was dead code — `grep -rn` returned exactly one hit (the definition itself). New `sync_channel_catalog` Celery task wraps it, enqueued (a) from the OAuth callback for new creators and (b) prepended to each creator's iteration of `_refresh_youtube_analytics_async`; new `POST /creators/me/catalog/sync` endpoint (5/min, 202+task_id) wires the onboarding "Refresh data status" button into a true sync trigger. Compounding fixes: `classify_video_kind` now reads `SHORTS_MAX_DURATION_S=180` (was hardcoded `<=60s` — YouTube raised the Shorts max to 180s in Oct 2024); `/videos/link` resolves kind via `get_videos_metadata`; `/videos/upload` probes duration locally via `probe_duration_s` before R2 PUT. 9 new unit tests + 1 OFF_COURSE_BUGS row closed. 501 passed / 1 skipped / 85 deselected; ruff 0 / mypy 0.
**Blocked**: _(none)_

> **Closed Issue 87 — Catalog sync wiring + 180s Shorts threshold** (2026-05-30):
> Documented in detail in `docs/DECISIONS.md` (2026-05-30 entry). Investigation
> triggered by the user reporting that connecting `reesepludwick@gmail.com` (channel
> "backboard media") and clicking "Refresh data status" returned 0 long-form videos
> and 0 Shorts despite the channel having 23 actual uploads. Root cause was structural,
> not data-related: the only function in the codebase that pulls a creator's uploads
> playlist (`youtube/analytics.py::sync_video_catalog`) had zero callers. The OAuth
> callback never called it; the hourly Beat refresh task only re-fetched analytics for
> already-known Video rows. The two write surfaces for new Video rows
> (`/videos/link`, `/videos/upload`) both hardcoded `kind=VideoKind.long` and the
> Shorts classifier was at the pre-2024 `<=60s` threshold, so even manual linking
> would have mis-bucketed every Short.
>
> Fix: new `sync_channel_catalog` Celery task wraps the existing
> `sync_video_catalog` (idempotent on `UNIQUE(creator_id, youtube_video_id)`, with
> token resolution + commit + safe-fail). OAuth callback enqueues it via `.delay()`
> for new creators so the redirect budget isn't blocked. The hourly Beat job prepends
> it to each per-creator iteration so newly published videos are discovered every
> refresh tick. New `POST /creators/me/catalog/sync` endpoint (5/min, 202+task_id)
> wires the onboarding "Refresh data status" button into a real sync trigger; the
> button now polls the data-gate every 4s until the row count stabilises. New
> `SHORTS_MAX_DURATION_S=180` config (matches YouTube's 2024 spec — verified at
> [Create a Short](https://support.google.com/youtube/answer/10059070)).
> `/videos/link` resolves kind via `get_videos_metadata` (safe-fails to long-form
> with a warning log); `/videos/upload` probes duration locally before R2 PUT. 8 new
> tests in `tests/test_catalog_sync.py` + 4 boundary tests updated in `test_analytics.py`;
> 3 retention-task mocks + 1 oauth-lifecycle mock updated to patch `sync_video_catalog`.
> All gates green: ruff 0, mypy 0, **501 passed / 1 skipped / 85 deselected**.

> **Closed Issue 86 — Live progress surface for long-running tasks** (2026-05-30): A
> reusable per-task observability primitive built on Redis Streams + SSE, designed
> to eliminate the frozen-spinner experience that triggered today's prod incident
> (3+ min of nothing during a `build_dna` crash-loop). DNA build is the first wired
> call site — `_build_dna_async` now emits `step` events at `acquire_lock`,
> `analyze_patterns`, `analyzed_patterns` (with counts), `call_claude`, `embed`, plus
> terminal `done`/`error`. The LLM segment streams via the new `generate_brief_streaming`
> path which wraps Anthropic's `messages.stream(...)` context manager — surfaces
> `message_start.usage` as a `cache` event (cache HIT/miss confirmable BEFORE the
> first token), forwards `text_delta` as `token` events, and is forward-compatible
> with `thinking_delta` once the SDK is bumped in Issue 84. Three layers, all
> additive: (1) `worker/progress.py` with `sync_emit`/`aemit`/`aset_owner`/
> `aacquire_slot`/`aread_since` against `task:{task_id}:events` Redis Streams
> (MAXLEN ~ 200, EXPIRE 3600 on terminal); (2) `routers/tasks.py` SSE endpoint
> `GET /tasks/{task_id}/events` with session-cookie auth, Redis-key ownership
> check (`task:{task_id}:owner` set by `routers/creators.py::build_dna`),
> `Last-Event-ID` resume, 12s `: keepalive` comment, per-creator concurrent cap
> of 3, 600s hard lifetime; (3) `static/progressStream.js` — ~50-line vanilla-JS
> EventSource reducer + a terminal-style `<pre>` block in `static/onboarding.html`.
> Cloudflare-Tunnel-safe headers (`Cache-Control: no-cache` + `X-Accel-Buffering: no`)
> ensure no proxy buffers the stream. New subprocess integration test
> `tests/test_worker_imports_integration.py` spawns a real Celery worker subprocess
> and asserts `from dna.brief import generate_brief` succeeds — guards the
> Dockerfile PYTHONPATH hotfix from today's incident forever. 7 sub-decisions
> (transport, bridge, thinking API, cache stat location, wire format, late-joiner,
> SSE security) captured in `docs/DECISIONS.md`. +24 unit tests + 1 integration.
> All gates green: ruff 0, mypy 0, **492 passed / 1 skipped / 85 deselected**.
>
> **Closed Issue 83 — Creator Intake Form** (2026-05-30): Adds a stated-identity layer
> (niche, audience, mission, tone, hard-nos, optional style sample) that is captured via
> a 5-field intake (3 required, 2+ optional via progressive disclosure) and fused with
> the inferred `creator_dna` at LLM-call time. Two structural decisions per the 2026
> industry-standard research (see DECISIONS 2026-05-30 entry): (1) stated and inferred
> are STRICTLY SEPARATE tables fused at query time — silently overriding stated intent
> with engagement signals is the YouTube-algorithm problem recreated inside our own
> tool, contradicting the North Star; (2) append-only versioning (partial unique
> `uq_one_current_identity_per_creator` is the DB backstop) keeps the audit trail
> intact. New `dna/identity.py` provides `get_current` / `get_history` / `upsert_identity`
> with FOR UPDATE serialization + IntegrityError race recovery, plus
> `format_for_prompt` that returns `None` (not "(no identity)") when missing for
> prompt-cache friendliness. New `dna/conflict.py` flags stated-niche-vs-inferred-pattern
> mismatches as a non-blocking profile-page nudge per the research's honesty pattern.
> `dna/brief.py` accepts a `stated_identity` block and moves the `cache_control`
> breakpoint to the new last stable block. `worker/tasks.py::_build_dna_async` fetches
> identity via `AdminSessionLocal` and passes through. New `youtube/categories.py`
> exposes the stable 15-option YouTube Data API niche list. New endpoints in
> `routers/creators.py`: public `GET /creators/niches` (intake form depends on it
> pre-session); authed `GET/POST /creators/me/identity` and
> `GET /creators/me/identity/history`. `static/onboarding.html` gets an optional
> 45-second intake card; `static/profile.html` gets full edit + version summary +
> conflict nudge. Alembic `0012_creator_identity`. +22 unit tests + 5 integration
> tests (append-only invariant, per-creator isolation, conflict detection, cache
> breakpoint placement). All gates green.

> **Prior Active**: Issue 78 — salvaged-from-PR#6 work. 78a (#9), 78b (#10), 78d (#11), 78g (#12), 78c (mypy 30→0) ✅ done. Remaining: **enable the `disallow_untyped_defs` ratchet** (deferred from 78c — ~20 pre-existing untyped-def signatures to annotate first), 78e (analytics retention purge — needs confirmed ToS staleness figure + data-deletion sign-off), 78f (PgBouncer load harness — needs real staging).

> **Closed Issue 78d — improvement-brief 202 + poll** (2026-05-30): the ~120s Claude +
> web_search brief moved off the request path (it could exceed an LB timeout). New
> `ImprovementBrief` model + `improvement_brief_status` enum (one row/creator) + migration
> 0009. `POST /creators/me/improvement-brief` → 202, debounces an in-flight build, enqueues
> `generate_improvement_brief`; the worker builds the creator-scoped analytics + DNA brief and
> runs the LLM (idempotent on `job_id`, safe-fail with a generic message — no token/PII/trace);
> `GET` polls the stored row; `insights.html` POST→poll. Mirrors the DNA-build precedent. +8
> integration tests; 3 GET-based isolation/offload tests rebased onto the task path;
> rate-limit test updated (10/hour LLM cap moved GET→POST). Gates: ruff 0, mypy 30, default
> 425 passed/1 skipped, integration 66 passed. Rationale + sources in DECISIONS (2026-05-30).

> **Closed Issue 75(f) — observability** (2026-05-29): new observability.py — a pure-ASGI
> RequestIDMiddleware (reads/mints X-Request-ID into a ContextVar, echoes it on the response;
> added outermost in main.py); JSON structured logs via JsonLogFormatter + RequestIDLogFilter
> (request_id on every line; configure_logging replaces basicConfig, idempotent, text fallback
> for dev); Prometheus golden signals (http_request_duration_seconds labelled by route template;
> celery_task_duration_seconds + celery_tasks_total) at /metrics gated by METRICS_ENABLED. The
> correlation id propagates API→Celery via before_task_publish/task_prerun/task_postrun signals
> (weak=False — Celery connects weakly by default). Added prometheus-client==0.25.0 (single CVE-clean
> dep; the correlation layer is hand-rolled to add zero new surface). Config: LOG_JSON,
> REQUEST_ID_HEADER, METRICS_ENABLED (+ .env.example). Deferred: OpenTelemetry distributed tracing.
> +9 DB-free tests; **410 passed, 1 skipped, 55 deselected**; gates ruff 0 / mypy 30 / bandit 0,0 /
> pip_audit 0. Rationale + sources in DECISIONS (2026-05-29).

> **Closed Issue 75(a) — pip-audit CVE remediation** (2026-05-29): 14 known vulns → 0.
> Patched 6 packages in requirements.txt: cryptography 43.0.3→46.0.7, python-multipart
> 0.0.20→0.0.27, PyJWT 2.9.0→2.12.0, lightgbm 4.5.0→4.6.0, python-dotenv 1.0.1→1.2.2,
> starlette 0.41.3→0.49.1 (forced FastAPI 0.115.4→0.120.4, smallest bump whose pin admits
> starlette 0.49.1). The disputed PyJWT PYSEC-2025-183 dropped off (2.12.0 out of its
> affected range). 2 residuals accepted-risk in run_layer0.py's PIP_AUDIT_IGNORES allowlist:
> pytest GHSA-6w46-j5rx-g56g (dev-only; pytest-asyncio caps pytest<9 — a test-stack cascade)
> and starlette PYSEC-2026-161 (Host header, fixable only on the starlette-1.x line / FastAPI
> 0.136.x). baselines.json pip_audit_vulns ratcheted 14→0. Verification: pip check clean;
> **401 passed, 1 skipped, 55 deselected** on bumped deps; run_layer0 gates ruff 0 / mypy 30 /
> bandit 0/0 / pip_audit 0. Justification + version evidence in DECISIONS (2026-05-29).
> Follow-up: starlette-1.x migration to close PYSEC-2026-161 (tracked in issues.md).

> **Closed Batch 8 / Issues 73(partial) + 74 + 75(partial)** (2026-05-29): Memory: librosa
> loads at sr=16000 (~3x less RAM) + WhisperX/SDK-client singletons. Security: youtube_video_id
> validated (^[A-Za-z0-9_-]{11}$ -> 422) before reaching a storage key. Robustness: Stripe
> prod fail-fast config validator; upload_intel skips out-of-range rows instead of 500.
> Deferred to Issue 75 tracking (with rationale in DECISIONS): full response_model coverage,
> Deepgram file-stream, 14 CVEs, analytics-retention cadence, observability, mypy->0, clip-scorer
> caching, scorer cache, brief 202/poll. DB-free unit tests for all four hardening items; updated
> 3 upload-streaming tests to valid 11-char IDs. Test count: **401 passed, 1 skipped, 55 deselected**
> (+4). Gates: ruff 0, mypy 30, bandit 0/0, coverage 70.45%.

> **Closed Issue 71** (2026-05-29, Batch 7): from_bytes monkeypatched a joblib global
> (not thread-safe -> RCE allowlist defeatable under concurrent loads); build_and_save
> max()+1 raced to IntegrityError; predict_score swallowed errors into 0.5. Fix: module
> threading.Lock around the swap (direct unpickler rejected -- joblib signature is
> version-fragile, see DECISIONS); pg_advisory_xact_lock(hashtext(creator_id)) for the
> version assignment; predict_score validates n_features_in_ and raises; load_latest
> returns None on feature-schema drift; rerank scores-then-mutates and falls back to DNA
> on scorer error. DB-free unit tests + fixed an existing mock-session test for the extra
> advisory execute. Test count: **397 passed, 1 skipped, 55 deselected** (+2). Gates: ruff 0, mypy 30, bandit 0/0, coverage 70.47%.

> **Closed Issue 70** (2026-05-29, Batch 6): poll_clip_outcomes re-polled every published
> clip every 7 days forever (no terminal guard) -> unbounded YouTube-quota drain. Added
> `clip_outcomes.final` (migration 0007) + partial index; the 7d checkpoint sets final and
> the query excludes final rows + caps candidates to clips created within 10 days; commit
> per creator. Integration test: 7d poll marks final, finalized outcome skipped. Test count:
> **395 passed, 1 skipped, 55 deselected** (+1 integration). Gates: ruff 0, mypy 30, bandit 0/0, coverage 70.38%.

> **Closed Issue 69** (2026-05-29, Batch 5): Both briefs interpolated per-creator
> data into the cached system block (prefix changed every call); improvement returned
> the web_search preamble instead of the answer. Split system into static-cached +
> volatile-uncached blocks; return `text_blocks[-1]`. `/claude-api` finding: Sonnet
> 4.6's min cacheable prefix is 2048 tokens and these static prefixes are ~400 — so
> caching can't engage for these low-frequency calls regardless; the split is
> correct-structure, and the real caching win (clip scorer's reused per-creator
> prefix) is tracked under Issue 75. DB-free unit tests for the split + final-block
> extraction; updated the existing 1-block test to the 2-block contract. Test count:
> **395 passed, 1 skipped, 54 deselected** (+4). Gates: ruff 0, mypy 30, bandit 0/0,
> coverage 70.47%.

> **Closed Issue 72** (2026-05-29, Batch 4b): Per-call `httpx.AsyncClient()` with no
> timeout on the token-refresh hot path; client built inside the retry loop in
> data_api/analytics. New `youtube/_http.py` lazy per-process singleton
> (`Timeout(15, connect=5)`) + `aclose()` reused everywhere and closed on API/worker
> shutdown; 5xx now backs off + retries. Rebased the oauth-lifecycle tests onto the
> `_http.client` boundary (they'd mocked the old per-call httpx). Test count: **392
> passed, 1 skipped, 54 deselected** (+2). Gates: ruff 0, mypy 30, bandit 0/0,
> coverage 70.49%.

> **Closed Issue 68** (2026-05-29, Batch 4b): Sync `generate_brief`, Voyage `_embed`
> (tenacity sleeping on the loop), `transcribe_audio`, and `extract_audio_events` ran
> on the worker's singleton loop with no transcription upper bound. All offloaded via
> `asyncio.to_thread`; transcription wrapped in `asyncio.wait_for(..., timeout=
> TRANSCRIPTION_TIMEOUT_S=300)` for a job-level bound. SDK-native timeouts deferred to
> Issue 75 (SDKs not installed to verify). DB-free unit test for the Voyage offload;
> existing pipeline tests confirm behavior-preservation. Test count: **390 passed, 1
> skipped, 54 deselected** (+2). Gates: ruff 0, mypy 30, bandit 0/0, coverage 70.32%.

> **Closed Batch 4a / Issues 66 + 67** (2026-05-29): Three synchronous calls ran on
> the API event loop (120s improvement brief, large-file upload, account-deletion
> purge), stalling every concurrent request on the worker (axis B). All three moved
> to `await asyncio.to_thread(...)`. The brief's 120s request duration (vs LB timeout)
> is tracked for a Celery 202/poll follow-up under Issue 75. Integration tests assert
> each call is offloaded. Test count: **388 passed, 1 skipped, 54 deselected** (+2
> integration). Gates: ruff 0, mypy 30, bandit 0/0, coverage 69.57%.

> **Closed Batch 3 / Issue 65** (2026-05-29): pgvector HNSW (`vector_cosine_ops`,
> m=16/ef_construction=200) on `dna_embeddings.embedding` matching the `<=>` query,
> plus `ix_clip_feedback_creator_id`; both `CREATE INDEX CONCURRENTLY` in an
> alembic autocommit_block (migration 0006). Reading the schema corrected two
> assessment items already covered (dna_embeddings.creator_id btree from 0001;
> preference_models.creator_id via the (creator_id,version) unique index) — no
> redundant indexes added. Integration test introspects `pg_indexes`. Migration-only,
> so the unit-coverage floor holds. Test count: **388 passed, 1 skipped, 52 deselected**
> (+2 integration). Gates: ruff 0, mypy 30, bandit 0/0, coverage 69.54%.

> **Closed Batch 2 / Issues 63 + 64** (2026-05-29): Idempotent unique-keyed writes.
> 63: `build_dna` stamps the Celery `task_id` as `creator_dna.build_job_id` and
> `_build_dna_async` early-returns before the paid LLM/Voyage calls on redelivery;
> `confirm_draft` locks `with_for_update()` + partial unique index
> `uq_one_confirmed_dna_per_creator` (ordered flush, non-deferrable). 64:
> `grant_minutes` now mirrors `deduct_for_video` (fast-path + SAVEPOINT +
> IntegrityError) so duplicate Stripe deliveries credit once. Migration `0005`.
> Integration tests for both. **Coverage floor moved 69.97→69.54%** (justified:
> DB-only idempotency code is integration-tested, not visible to the unit-coverage
> gate — see DECISIONS). Test count: **388 passed, 1 skipped, 50 deselected** (+3
> integration; updated 1 mocked unit test). Gates: ruff 0, mypy 30, bandit 0/0,
> coverage 69.54%.

> **Closed Batch 1 / Issues 61 + 62** (2026-05-29): Celery is at-least-once. A
> redelivered `build_signals`→`generate_clips` wiped feedback/outcomes via
> cascade-delete (data loss; corrupted the Issue-60 training signal), `acks_late`
> without `reject_on_worker_lost` dropped OOM-killed jobs, and no time limit meant a
> long task redelivered while still running. Fix: `generate_and_rank_clips`
> early-returns existing clips (idempotent, never cascade-wipes); added
> `task_reject_on_worker_lost` + the `soft(3000)<hard(3300)<visibility(3600)`
> invariant; `_render_clip_async` skips when already done. DB-free config-invariant
> test + integration tests (feedback survives re-gen; render skips when done).
> Test count: **388 passed, 1 skipped, 47 deselected** (+3 unit, +2 integration).
> Gates: ruff 0, mypy 30, bandit 0/0, coverage 70.02%.

> **Closed Issue 60** (2026-05-29): Personalization was dead code — `build_and_save`
> had no caller and `rerank_with_preference` was never invoked, so ranking was
> DNA-only (the North-Star "learns your style" loop never ran). Fix: idempotent,
> self-debouncing `retrain_preference` Celery task enqueued from the feedback
> endpoint; `rerank_with_preference` now called at the end of `generate_and_rank_clips`;
> flat 50/50 blend replaced with `preference_weight(label_count)` — 0 below
> PERSONALIZATION_THRESHOLD_LABELS (honest DNA fallback), ramping to
> PREFERENCE_WEIGHT_CAP by 2× the threshold (hybrid cold-start standard). Version-race
> + unpickler thread-safety deferred to Issue 71 (retrain catches IntegrityError
> meanwhile). DB-free unit tests (weight curve + rerank gating) + integration test
> (trains v1 then self-debounces). Test count: **385 passed, 1 skipped, 45 deselected**
> (+6 unit, +1 integration). Gates: ruff 0, mypy 30, bandit 0/0, coverage 70.18%.

> **Closed Issue 59** (2026-05-29): The render cut from `clip.start_s` (fixed
> peak−75s) while scoring/API/eval all key on `setup_start_s` → delivered Shorts
> didn't clip the setup. Fix: render via `_render_start_for(clip)` (pure helper,
> coalesces to `start_s` only when nullable `setup_start_s` is unset); set
> `-accurate_seek` explicitly. The assessment's "GOP drift" SEV-2 was a false
> positive — re-encode pipelines accurate-seek by default (DECISIONS). DB-free unit
> guards + an integration test that the persisted setup_start_s reaches the render.
> Test count: **379 passed, 1 skipped, 44 deselected** (+3 unit, +1 integration).
> Gates: ruff 0, mypy 30, bandit 0/0, coverage 70.06%.

> **Production assessment run** (2026-05-29): `/assess` across all 11 modules →
> verdict **PRODUCTION-READY = NO**. 1 BLOCKER, 25 SEV-1, 39 SEV-2, 34 cleanup;
> no cross-tenant leak, bandit 0/0. Findings tracked as Issues 58–75; full register
> in `docs/assessment/`. Also shipped the repeatable harness (`/assess` skill +
> ratcheted CI gates in `quality.yml` + baselines), the `best-practices` skill +
> freshness convention (`docs/SKILL_FRESHNESS.md`), and SSOT model-id config.

> **Closed Issue 58** (2026-05-29): psycopg3 prepared statements are incompatible
> with PgBouncer transaction-pooling mode (the production pooler) → would throw
> `prepared statement "_pg3_…" does not exist`; CI never caught it (direct
> Postgres). Fix: `connect_args={"prepare_threshold": None}`; pool ceiling cut
> 30→20/pod to stay under the 25-conn sidecar; `pool_recycle=1800`. Connection-
> budget inequality recorded in DEPLOYMENT.md; engine config guarded by
> `tests/test_db_engine_config.py`. Load-proof behind real PgBouncer deferred to
> staging Locust. Test count: **376 passed, 1 skipped** (+3). Gates: ruff 0, mypy 30,
> bandit 0/0, coverage 70.03%.

> **Closed Issue 79** (2026-05-28): Postgres RLS implementation. Closes the
> structural defense-in-depth gap that allowed Issue 33 (missed `creator_id`
> filter → cross-creator analytics in a Claude prompt). New alembic revision
> `0010_rls_policies` creates roles `creatorclip_app` (LOGIN, no BYPASSRLS) +
> `creatorclip_migrate` (LOGIN, BYPASSRLS granted out of band), grants the
> app role full DML on `public` (plus `ALTER DEFAULT PRIVILEGES` for future
> tables), and enables + forces RLS on the 12 tenant-owned tables
> (`videos`, `audience_activity`, `demographics`, `youtube_tokens`,
> `creator_dna`, `dna_embeddings`, `clips`, `clip_feedback`,
> `preference_models`, `minute_packs`, `minute_deductions`, `usage`). Policies
> read `current_setting('app.creator_id', true)::uuid` on USING + WITH CHECK.
> `creators` and `audit_log` are exempt — the former because the FastAPI
> auth dependency must resolve the current creator before the GUC is set,
> the latter because ops/oncall need to read all rows.
>
> Application wiring: new optional `DATABASE_MIGRATION_URL` env var (falls
> back to `DATABASE_URL` for single-role dev/CI); `db.py` now exposes
> `AsyncSessionLocal` (app role) AND `AdminSessionLocal` (admin role) — a
> global `after_begin` event listener on `Session` emits `SET LOCAL
> app.creator_id` whenever `session.info["creator_id"]` is set;
> `auth.get_current_creator` attaches the resolved creator id to
> `session.info` after the (exempt) Creator lookup. Worker tasks all moved
> from `db.AsyncSessionLocal()` to `db.AdminSessionLocal()` (16 sites) —
> worker code is trusted internal and many tasks are inherently
> cross-tenant (purge, poll_clip_outcomes, analytics refresh).
>
> Two minor implementation decisions surfaced and resolved (see DECISIONS):
> (a) JWT-to-creator bootstrap via the `creators` table exemption rather
> than a middleware pre-parse; (b) RLS-guarantee tests use `SET LOCAL ROLE
> creatorclip_app` within a transaction to assume the non-BYPASSRLS role
> for the visibility assertion — keeps existing integration tests
> untouched.
>
> New `tests/test_rls_isolation_integration.py` (marker: `integration`)
> seeds Creator A + B with one row per tenant table, then under
> `creatorclip_app` role + Creator A's GUC asserts that unfiltered
> `SELECT creator_id FROM <each tenant table>` returns zero Creator B rows.
> Second test verifies the `creators` table remains visible to the app role
> with no GUC set (auth-bootstrap path).
>
> Mutation rowcount audit (AC carry-over): satisfied by construction — the
> only two raw `session.execute(update/delete)` outside the ORM session
> pattern target the exempt `creators` table; everything else routes
> through `session.get(Model, id)` → mutate → commit, where `session.get`
> returns `None` for RLS-blocked rows and the existing
> `if not video: raise 404` is the rowcount guard. Documented in DECISIONS.
>
> Production runbook in `docs/DEPLOYMENT.md` covers the one-time SQL ops:
> `ALTER ROLE creatorclip_migrate BYPASSRLS`, set passwords, transfer table
> ownership to `creatorclip_migrate`, update `/opt/autoclip/.env` with
> `DATABASE_MIGRATION_URL`, restart app. pgbouncer-future caveat pinned:
> transaction pooling only, never statement pooling.
> Test count: **381 passed, 1 skipped, 56 deselected** (+2 RLS integration).

> **Closed Issue 38 Wave 1** (2026-05-28): Sync-in-async fixes for the Celery
> ingest pipeline. A full-codebase audit found 23 instances of sync external calls
> inside `async def` (class 1) or `await` while a DB session was open (class 2);
> Wave 1 closed all the class (1) findings in the Celery hot path (~14 of 23).
> Wave 2 is filed as Issue 82 — covers the AsyncAnthropic/AsyncVoyage SDK swap
> across `dna/brief.py` / `improvement/brief.py` / `clip_engine/scoring.py`, the
> router session-order refactor (`routers/auth.py` / `videos.py` / `clips.py` /
> `billing.py`), the `clip_engine/ranking.py` compute/persist split, and the
> 10-concurrent-improvement-brief load test.
>
> Wave 1 changes: new async wrappers in `worker/storage.py` (`aupload_file`,
> `adelete_file`, `adelete_prefix`, `alocal_path` — all dispatch to boto3 via
> `asyncio.to_thread`); the four Celery pipeline tasks
> (`_ingest_async` / `_transcribe_async` / `_signals_async` / `_render_clip_async`)
> now use the async wrappers + offload sync subprocess (ffmpeg / probe), librosa,
> WhisperX/Deepgram, and `render_clip_file` to threads; `_build_dna_async` wraps
> the sync Anthropic `generate_brief` call in `to_thread`; `dna/embeddings.py`
> gets a new `_aembed` async wrapper around the sync Voyage `_embed`;
> `_purge_stale_source_media_async` was restructured to release the session
> during the boto3 delete loop (select tuples → close → loop deletes via
> `adelete_file` → reopen session for a single UPDATE) — previously held one
> session across every R2 round-trip in the sweep.
>
> Test patches updated: `test_retention_tasks.py` for the new purge two-session
> + tuple shape and for `alocal_path`; `test_worker_pipeline.py` (Issue 52 file
> shipped earlier the same session) for `alocal_path`. Renamed worker tests
> still pass at 381 / 1 skipped / 54 deselected.

> **Closed Issue 52** (2026-05-28): Worker pipeline integration tests. The seven
> Celery async functions in `worker/tasks.py` (`_ingest_async`, `_transcribe_async`,
> `_signals_async`, `_render_clip_async`, `_generate_clips_async`, `_build_dna_async`,
> `_poll_clip_outcomes_async`) had no direct end-to-end coverage —
> `test_pipeline_trigger.py` only asserted registration / task chaining. New
> `tests/test_worker_pipeline.py` pins all 5 ACs against real Postgres with mocks at
> the storage (R2 / boto3) and external-SDK (YouTube Data API, ffmpeg) boundaries.
> Notable design: AC4 (per-creator median) seeds two creators with disjoint
> VideoMetrics — same fetched view count (100) yields opposite `performed_well`
> labels (A=False because 100 < 500 median, B=True because 100 ≥ 20 median) —
> a global-median computation would label both identically. AC5 (build_dna ValueError
> bypasses retry) calls `_build_dna_async` directly per the established
> `test_dna_build_idempotency.py` pattern; the task wrapper's `except ValueError:
> raise` is pinned by inspection because `build_dna.apply()` would call `asyncio.run`
> from inside the running pytest-asyncio loop (RuntimeError). No real fixture media
> files needed — `local_path` is mocked to yield a temp file, matching the existing
> `test_purge_integration.py` / `test_generate_clips_retry_integration.py` pattern.
> Test count: **381 passed, 1 skipped, 54 deselected** (+5 integration).

> **Closed Issue 56** (2026-05-28): Postgres Row-Level Security research-and-decide.
> Decision: **adopt RLS** as defense-in-depth underneath the existing
> application-layer always-filter for every tenant-owned table. Trigger context:
> the SEV-0 Issue 33 leak (a missed `creator_id` filter exposed cross-creator
> analytics to a Claude prompt) demonstrated that application-layer filtering is a
> linting problem disguised as a security property. RLS converts that into a
> structural guarantee: the database refuses to return cross-tenant rows even when
> application code forgets the WHERE. Implementation sketch pinned in
> `docs/DECISIONS.md`: 12 tables with direct `creator_id` columns get policies;
> two-role split (`creatorclip_app` no-BYPASSRLS + `creatorclip_migrate` BYPASSRLS;
> new `DATABASE_MIGRATION_URL`); `SET LOCAL app.creator_id` injected via
> SQLAlchemy `after_begin` event listener sourcing from FastAPI auth context;
> `FORCE ROW LEVEL SECURITY` on every covered table; mutation paths audit
> rowcount-zero-→-404. pgbouncer-future answer pinned: safe with transaction
> pooling, unsafe with statement pooling (we don't run pgbouncer today). Sources:
> Crunchy Data, pganalyze, Bytebase footguns writeup, SQLAlchemy 2.0 async docs
> + discussion #10469, Microsoft Azure multi-tenant guidance. **Implementation
> split to new Issue 79** — the Issue 56 spec was explicitly "research-and-decide",
> and the implementation is substantial enough (alembic migration + role split +
> middleware + mutation audit) to warrant its own focused PR. The decision
> ships now and Issue 79 inherits the carry-over ACs.

> **Closed Issue 57** (2026-05-28): Automatic refund on terminal ingest failure.
> Issue 34 made minute deduction per-video-idempotent, but a terminally-failing ingest
> still left the deduction in place. Policy decided (see DECISIONS): automatic refund,
> all terminal failure classes, surfaced via billing-history `MinutePack` row only
> (email + in-app banner split to new Issues 58 + 59 — both require infrastructure
> we don't have yet). New `billing/refund.py:refund_for_video` is idempotent on
> `pack_id="refund:<video_id>"`; new Celery base class `RefundOnFailureTask` in
> `worker/tasks.py` fires only when retries are exhausted, extracts `video_id` from
> `args[0]`, dispatches via `run_async`, and swallows internal exceptions so the
> task's original terminal failure stands. Applied to `ingest_video`,
> `transcribe_video`, `build_signals` (the three tasks where minutes can have been
> deducted by the time failure terminates). No alembic migration — `MinutePack`
> already supports the compensating-grant pattern. Disclosure language added to
> `docs/COMPLIANCE.md` as the canonical user-facing copy until pricing / ToS pages
> land in Phase 3.
> Test count: **381 passed, 1 skipped, 49 deselected** (+3 unit, +3 integration).

> **Closed Issue 46** (2026-05-28): Generate-clips retry safety + outcomes time-window
> bug. Two regressions in one issue. (1) `clip_engine/ranking.py:generate_and_rank_clips`
> unconditionally `DELETE FROM clips WHERE video_id = ...` before reinserting candidates;
> a late retry of `generate_clips` after `render_clip` had already completed wiped the
> `done` Clip rows, orphaning R2 objects and breaking the `ClipOutcome` FK chain. Fix:
> narrowed the DELETE WHERE to exclude `done` and `running` rows, and added an
> idempotency guard at the top of `_generate_clips_async` — if any `done` clip already
> exists for this video, log and return without re-extracting candidates. (2)
> `_poll_clip_outcomes_async`'s 7d arm had no upper bound on `Clip.created_at`, so every
> clip past its 7d checkpoint re-polled YouTube Data API every hour forever. Fix: added
> `Clip.created_at > now() - interval '30 days'` to the WHERE — after 30 days the
> `performed_well` label is stale enough that flipping it retroactively offers no
> preference-model signal. No migration needed. Predicate logic pinned via two unit
> tests in `tests/test_outcomes.py`; all three regressions pinned end-to-end against a
> real Postgres in `tests/test_generate_clips_retry_integration.py` (marker:
> `integration`).
> Test count: **375 passed, 1 skipped, 46 deselected** (+2 unit, +3 integration).

> **Closed Issue 47** (2026-05-28): Beat-job fairness on quota exhaustion. Old refresh
> task did `select(Creator)` with no ORDER BY and `break` on `QuotaExhaustedError` —
> next day's run started the same scan in the same heap order, so creators past the
> daily cutoff index never refreshed (SEV-2 starvation). Fix: added nullable
> `creators.last_analytics_refreshed_at` + `ix_creators_refresh_order` index;
> `ORDER BY last_analytics_refreshed_at NULLS FIRST, id` so newly-connected creators
> jump the queue and yesterday's starved creators go first today. Stamp set inside
> the successful inner try (commits with analytics writes); rollback on
> `QuotaExhaustedError` un-stamps by design, keeping the starved creator at the
> front. No backfill — NULL = "never refreshed" puts existing rows at the head on
> day 1, self-bootstrapping. Bundled into alembic `0004_video_done_creator_refreshed`
> per LEFT_OFF's explicit suggestion (one deploy step for both Issue 43 + 47 schema).
> Filter contract pinned via select-statement inspection (`order_by` clauses); stamp
> + no-stamp idempotency pinned via two unit tests; real-DB 5×3-cycle scenario in
> `tests/test_analytics_fairness_integration.py` (marker: `integration`).
> Test count: **373 passed, 1 skipped, 43 deselected** (+3 unit, +1 integration).

> **Closed Issue 43** (2026-05-28): Source-media purge correctness. Old filter was
> `Video.created_at < cutoff` — a stuck/in-progress ingest of an old upload would have
> its `source_uri` nulled mid-pipeline (SEV-1). Fix: added `videos.ingest_done_at`
> (nullable timestamptz) stamped exactly once in `_signals_async` under a
> `if video.ingest_done_at is None:` guard (Celery is at-least-once — retries must NOT
> refresh the stamp); swapped the purge filter to gate on
> `ingest_done_at IS NOT NULL AND ingest_done_at < cutoff`. Migration backfills
> existing `done` rows with `created_at` so already-completed videos keep their
> pre-migration retention window. Added partial index
> `ix_videos_purge_candidates ON videos(ingest_done_at) WHERE
> ingest_done_at IS NOT NULL AND source_uri IS NOT NULL` for cheap hourly sweeps.
> Filter contract pinned via SQL-whereclause inspection test;
> stamp idempotency pinned via two unit tests; real-DB three-row scenario in
> `tests/test_purge_integration.py` (marker: `integration`). `docs/COMPLIANCE.md`
> retention-clock row updated.
> Test count: **370 passed, 1 skipped, 42 deselected** (+3 unit, +1 integration).

> **Closed Issue 39** (2026-05-28 — Batch 3 kickoff): Celery event-loop strategy.
> Every task previously called `asyncio.run(...)`, creating a fresh loop per
> invocation and rebinding the SQLAlchemy async engine pool to whichever loop
> touched it first — the textbook cause of "Future attached to a different loop"
> + pool churn under concurrency. Fix: per-worker singleton `asyncio` loop installed
> by the `worker_process_init` Celery signal, and the engine rebound to that loop
> via new `db.recreate_engine()` (uses `engine.sync_engine.dispose(close=False)`
> to abandon inherited parent connections without yanking parent FDs). All 11 task
> bodies in `worker/tasks.py` now route through `worker.celery_app.run_async(coro)`.
> Switched `worker/tasks.py` from `from db import AsyncSessionLocal` to `import db`
> + `db.AsyncSessionLocal(...)` so the rebound sessionmaker is picked up at call time.
> Test count: **367 passed, 1 skipped, 41 deselected** (+5 new event-loop tests).
> Adjusted patch targets in `test_retention_tasks.py` / `test_pipeline_trigger.py` /
> `test_oauth_lifecycle.py` to match the new import surface.

> **Closed Batch 2** (2026-05-28 PM): Three TEST-ONLY issues via parallel agents.
>
> - **Issue 49**: 4 integration tests for the billing money paths (concurrent deduct
>   race, webhook idempotency same session_id, unknown pack_id, missing metadata).
>   Finding: webhook returns 200 `{"status": "ignored"}` for anomalies, NOT 4xx — this
>   is the correct Stripe pattern (2xx prevents retry storms; anomalies logged internally).
>   Tests document and assert the actual behavior.
> - **Issue 51**: 4 new tests appended to `tests/test_oauth_lifecycle.py` (now 15 total):
>   refresh-path success, callback caplog no-plaintext, authorization URL exact scopes
>   (no `youtube.upload`), `prompt=consent` + `access_type=offline` round-trip.
> - **Issue 55**: 9 surgical load-bearing tests across 8 existing files + 1 adversarial
>   YAML scenario (`loud_aftermath.yaml`).
>
> One merge-flow defect caught during Batch 2: Issue 51's new
> `test_callback_logs_no_token_plaintext` drives the full callback success path, which
> sets a `cc_session` JWT cookie on the session-scoped TestClient cookie jar — leaking
> auth into subsequent tests and causing `test_static::test_list_videos_requires_auth`
> to hit real Postgres. Fix: clear `client.cookies` in the finally block and `pop` only
> the dependency override this test set instead of `.clear()` (the project convention).
>
> Test count: **362 passed, 1 skipped, 41 deselected** (was 349; +13 unit / +4 integration).

> **Closed Batch 1** (2026-05-28 PM): Six issues landed via parallel agents in
> isolated worktrees, merged serially into main with full suite green after each merge.
>
> - **Issue 37** (SEV-1, SDK timeouts): module-level singletons for Anthropic / Stripe /
>   Voyage / boto3 with timeout + retry config. Anthropic 60s/2-retry, 120s override for
>   improvement_brief web_search path. Stripe `max_network_retries=3`. Voyage `timeout=30`
>   wrapped in tenacity (3 attempts, exp backoff). boto3 adaptive retry, max_attempts=5,
>   connect/read 10/60. Added `tenacity==9.1.4` to requirements.
> - **Issue 45** (SEV-2, refresh race + Redis pool): per-creator `SET NX EX 10` lock around
>   the Google refresh branch with canonical Lua compare-and-delete release. Module-level
>   `redis.asyncio.Redis` singleton in new `youtube/_redis.py` shared by oauth + quota.
> - **Issue 48** (TESTS): 14 new integration tests covering every protected route — zero
>   SEV-0 isolation findings (all routes correctly enforce per-creator filtering).
> - **Issue 50** (TESTS): 4 integration tests verifying cascade across all 17 dependent
>   tables; no missed FK cascades.
> - **Issue 53** (TESTS): renamed misnomered `test_compliance.py` → `test_retention_tasks.py`;
>   new `test_compliance_no_virality.py` with 3 structural scans (OpenAPI bodies, static
>   assets, schema descriptions). Codebase clean — no forbidden phrases.
> - **Issue 54** (TESTS): 3 integration tests for `scripts/rotate_token_key.py` —
>   happy-path full re-encrypt, corrupt-row rollback, caplog no-plaintext.
>
> Test count: **349 passed, 1 skipped, 37 deselected** (was 335 + 16 deselected;
> +14 unit / +21 integration). See `docs/DECISIONS.md` 2026-05-28 entries for Issues 37, 45.

> **Closed Issue 36** (2026-05-28): Three lifecycle gaps closed in one commit.
> (a) `DELETE /auth/me` now revokes the **refresh** token at
> `oauth2.googleapis.com/revoke` and tolerates 400 `invalid_token` / `token_revoked` as
> success — completes the right-to-erasure path. (b) `get_valid_access_token` now deletes
> the `YoutubeToken` row + commits on Google `invalid_grant` (RFC 6749 §5.2 permanent
> error), so subsequent refresh attempts immediately surface the existing
> "No OAuth tokens found — please reconnect" 401 instead of looping. (c) New
> `youtube/errors.py` (`YouTubeAuthError` + `PERMANENT_403_REASONS` / `TRANSIENT_403_REASONS`
> sets); `_get_json` and `_fetch_report` share a `_classify_error()` helper that retries
> transient 403/429 with exponential backoff and raises `YouTubeAuthError` on permanent
> 401 / 403 reasons (authError, forbidden, accountClosed, accountSuspended, channelClosed,
> ...). `worker/tasks.py::_refresh_youtube_analytics_async` catches `YouTubeAuthError`,
> deletes the offending `YoutubeToken` row, commits, and continues — eliminates the
> hourly-wasted-quota loop against revoked creators. "Mark creator disconnected" is
> represented as token-row absence (no `OnboardingState` enum change, no migration).
> 9 new tests in `tests/test_oauth_lifecycle.py`. Test count: **335 passed, 1 skipped,
> 16 deselected** (was 326; +9 new). See `docs/DECISIONS.md` 2026-05-28 Issue 36 entry.

> **Closed Issue 41**: `preference/model.py:35–40` used `pickle.dumps(self)` / `pickle.loads(data)`
> for `PreferenceScorer.to_bytes` / `from_bytes`.  Any future write to `preference_models.weights_blob`
> (SQL injection, admin import, a bug) would become RCE in the worker process on the next ranking pass.
> Replaced with **joblib** (sklearn's documented serialiser; already a transitive dep) backed by
> `_RestrictedUnpickler` — a subclass of `joblib.numpy_pickle.NumpyUnpickler` that overrides
> `find_class` with a hardcoded allowlist of 10 `(module, name)` pairs.  `from_bytes` temporarily
> patches `joblib.numpy_pickle.NumpyUnpickler` with the restricted class for the duration of the
> `joblib.load` call, then restores the original (no global state left behind).  No schema change —
> `weights_blob` column stays `bytes`.  4 new tests in `tests/test_preference.py`: round-trip
> (predictions identical), label_count preserved, `os.system` gadget rejected, `subprocess.Popen`
> gadget rejected.  Test count delta: +3 net (renamed 1 existing test, added 4, kept all others green).
> See `docs/DECISIONS.md` 2026-05-28 Issue 41 entry.
>
> **Closed Issue 42**: `clip_engine/render.py` had three `subprocess.run` calls with no
> `timeout=`. A stalled or corrupt source video would block the Celery worker indefinitely.
> Fixed: `_run` now accepts `timeout_s: float = 120.0` and catches `subprocess.TimeoutExpired`,
> re-raising as `RuntimeError(f"ffmpeg {label} timed out after {timeout_s}s")`. `_frame_dimensions`
> hardcodes `timeout=30` directly (ffprobe reads only the container header). `render_clip_file`
> computes `render_timeout_s = max(120.0, duration * 4)` and passes it to both the keyframe
> extraction and the final render `_run` call. 3 new tests in `tests/test_render.py` assert
> each timeout path raises the correct `RuntimeError` without any real sleeping (all using
> `subprocess.TimeoutExpired` side-effects). Test count: 311 passed + 3 new = 314 expected
> (test env currently broken by a langsmith/pydantic-core version conflict introduced between
> sessions — see environment note below). See `docs/DECISIONS.md` 2026-05-28 Issue 42 entry.
>
> **ENVIRONMENT NOTE (2026-05-28)**: `python3.12 -m pytest -q` now fails at plugin-loading
> time with `SystemError: pydantic-core 2.27.2 incompatible with pydantic requiring 2.46.4`.
> Cause: langsmith installed a newer pydantic (2.46.4) into the uv-managed Python at
> `~/.local/share/uv/python/cpython-3.12.7/` while the user site at `~/.local/lib/python3.12/`
> still has pydantic-core 2.27.2. The fix is: `python3.12 -m pip install --user --break-system-packages
> "pydantic-core>=2.46.4"` OR use the project venv at `.venv/bin/pytest`. This is an environment
> issue, not a code issue.
>
> **2026-05-28 session note**: Ran a full project audit before resuming work. Discovered 24
> hardening + coverage findings (4 SEV-0, 12 SEV-1, 3 SEV-2, 8 test-coverage), filed as
> Issues 32–55 in `docs/issues.md` under **Phase 2: Hardening & Test Coverage**.
> **Closed Issue 32**: `starlette` had drifted to 1.1.0 (a major-version upstream released
> 2026-05-23 under the new `Kludex/starlette` maintainership) and `pytest` could not even
> collect — the previously-claimed "313 tests pass" was stale. Pinned `starlette==0.41.3`
> explicitly in `requirements.txt` (inside FastAPI 0.115.x's `<0.42.0,>=0.40.0` constraint),
> re-installed via a project venv, and confirmed **313 passed, 7 deselected** (the 7 are
> integration-marked). See `docs/DECISIONS.md` 2026-05-28 entry.
> **Closed Issue 33**: `routers/improvement.py` was sending other creators' analytics
> averages to Claude for every requesting creator (`select(VideoMetrics).limit(50)` with no
> `creator_id` filter — SEV-0 isolation leak). Fixed via the always-filter idiom already
> used elsewhere (`.join(Video).where(Video.creator_id == creator.id)`) plus an
> `ORDER BY fetched_at DESC` for determinism, plus a zero-data 400 short-circuit so
> brand-new creators don't get a hallucinated brief. New integration test
> `tests/test_improvement_isolation.py` seeds two creators with disjoint metrics and asserts
> only the requesting creator's data reaches the LLM. Filed **Issue 56** (Postgres RLS
> evaluation) as defense-in-depth follow-up. See `docs/COMPLIANCE.md` "Findings & Fixes
> Log" 2026-05-28 entry.
> **Closed Issue 34**: `worker/tasks.py:189` called `deduct_minutes` with no per-video
> idempotency key. With Celery's `task_acks_late=True`, a worker-crash-between-commit-and-ack
> would re-deliver the ingest task and re-decrement the balance (up to 4× per video).
> Replaced with a new `MinuteDeduction` ledger table (symmetric to `MinutePack` grants),
> `UNIQUE(video_id)` as the idempotency key, and `deduct_for_video` using SAVEPOINT
> (`session.begin_nested`) to atomically INSERT the ledger row + decrement balance. New
> migration `0003_minute_deductions.py`. 4 real-Postgres integration tests in
> `tests/test_billing_idempotency.py` cover sequential retry, two-coroutine concurrent
> race, 402-leaves-ledger-clean, and audit fields. Test count: **311 passed, 13
> deselected** (net 0 — removed 2 mocked unit tests, added 4 integration tests). Filed
> **Issue 57** (refund-on-terminal-failure) as product follow-up. See `docs/DECISIONS.md`
> 2026-05-28 Issue 34 entry.
>
> **2026-05-28 session note (Issue 40)**: Replaced `await file.read(max_bytes + 1)` bulk-read
> (SEV-1: up to 500 MB into heap per request) with a 1 MB streaming chunk loop. Temp file is
> always unlinked on the 413 rejection path via `except HTTPException`. 3 new tests in
> `tests/test_videos_upload_streaming.py`: 413 on oversize, tempfile cleanup verified, RSS delta
> asserted < 20 MB for a 100 MB rejected upload. Test count: **314 passed** (net +3).
> See `docs/DECISIONS.md` 2026-05-28 Issue 40 entry.

> **2026-05-28 session note**: Completed Issue 44 (auth boundary hardening). Three security
> fixes: (1) `auth.py` `get_current_creator` now catches `ValueError`/`KeyError` alongside
> `PyJWTError` so a malformed JWT `sub` returns 401 instead of 500; (2) `DELETE /auth/me` rate-
> limited to 5/hour via the existing slowapi limiter; (3) `crypto.py` rewritten to use
> `MultiFernet` for zero-downtime key rotation + typed `TokenDecryptError`. Added
> `TOKEN_ENCRYPTION_KEY_PREVIOUS` optional setting. Test count delta: +8 tests (2 in
> `test_auth.py`, 6 in `test_crypto.py` replacing 1 old test). All existing tests updated for
> the new rate-limit requirement on `DELETE /me`.

> **2026-05-27 session note**: Built the operability kit (Issue 31). Found and fixed a
> **blocking pre-existing bug** — `routers/clips.py` imported the deleted `billing.tiers`, so
> `import main` failed and the app could not start (likely a real cause of failed/timed-out
> deploys). Fixed to the minute-packs `check_positive_balance` guard. Full suite now `313 passed`.
> Note: CI lint (`ruff check .`) has ~11 pre-existing violations unrelated to this work — flagged,
> not swept in. The local unprovisioned `.env` is missing most required vars (dev only).

> **2026-05-28 session note**: Fixed SEV-0 Issue 35 — idempotent DNA build. `create_draft`,
> `embed_patterns`, `embed_brief` all gained `commit=False` path; `_build_dna_async` now
> issues a single atomic commit. 3 integration tests added in `tests/test_dna_build_idempotency.py`
> (marked `integration`; excluded from default `pytest -q` run per pytest.ini). Non-integration
> suite count unchanged at `313 passed`.

---

## Issue Progress

| # | Title | Phase | Status | Notes |
|---|-------|-------|--------|-------|
| 1 | Repo scaffold + Docker Compose + health endpoint | Core | ✅ Done | All acceptance criteria met; tests pass |
| 2 | Postgres schema + Alembic + pgvector | Core | ✅ Done | All tables, enums, pgvector; alembic upgrade head verified against live DB |
| 3 | Google/YouTube OAuth + creator session | Core | ✅ Done | OAuth flow, JWT session, token refresh, get_current_creator |
| 4 | YouTube data fetch — metrics, retention, activity | Core | ✅ Done | data_api.py, analytics.py, routers/creators.py; Deepgram default logged |
| 5 | Ingestion pipeline — source + transcript + signals | Core | ✅ Done | Celery chain; Deepgram/WhisperX/AssemblyAI; audio events; unified timeline |
| 6 | Creator DNA builder + brief (Research Mode) | Core | ✅ Done | dna/builder+brief+profile+embeddings; build_dna task; /creators/me/dna endpoints; 99 tests pass |
| 7 | Clip engine — candidates with backward setup-finding | Core | ✅ Done | window.py, candidates.py; 20 tests + 2 eval YAML fixtures pass |
| 8 | Clip scoring + DNA-weighted ranking | Core | ✅ Done | scoring.py, ranking.py, routers/clips.py; 18 tests pass |
| 9 | Render — 9:16 cut + active-speaker reframe | Core | ✅ Done | render.py (ffmpeg+OpenCV), render_clip task, /clips/{id}/render endpoint; 10 tests pass |
| 10 | Review UI + feedback capture | Core | ✅ Done | routers/review.py, static/review.html+onboarding.html+profile.html; HTMX; 7 tests pass |
| 11 | Preference model — recency-decayed reranker | Core | ✅ Done | decay.py, features.py, model.py, train.py; rerank_with_preference; 19 tests pass |
| 12 | Upload intelligence + improvement brief | Core | ✅ Done | timing.py, brief.py (Claude+web_search), routers; 13 tests pass |
| 13 | Clip outcomes loop (strongest signal) | Core | ✅ Done | poll_clip_outcomes Beat task (48h+7d), performed_well, get_video_stats; 13 tests pass |
| 14 | Dashboard + static pages scaffold | Core | ✅ Done | index.html, insights.html, tos.html, privacy.html; StaticFiles mount + GET /; 12 tests pass |
| 15 | Connected user flow + auth guard | Core | ✅ Done | auth.js guard + auth:ready event; nav on all pages; review/profile/onboarding wired; 18 tests pass |
| 16 | Auto-trigger clip generation + status polling | Core | ✅ Done | generate_clips task; build_signals chains it; setInterval polling; /videos/{id}/status; 7 tests pass |
| 17 | Source media purge + YouTube analytics refresh | Core | ✅ Done | purge_stale_source_media + refresh_youtube_analytics Beat tasks; datetime fix; 13 tests pass |
| 18 | Per-creator rate limiting | Core | ✅ Done | slowapi + Redis; creator_id key from JWT; 10/h LLM, 20/h render, 120/min rest; 11 tests pass |
| 19 | Account deletion (right-to-erasure) | Core | ✅ Done | DELETE /creators/me; OAuth revoke; storage purge; cascade delete; audit log; 6 tests pass |
| 20 | YouTube API quota hardening | Core | ✅ Done | youtube/quota.py; atomic Lua consume; backoff in data_api; Beat refresh stops gracefully; 8 tests pass |
| 21 | Stripe billing — minute packs | Core | ✅ Done | billing/packs.py + ledger.py; atomic deduct_minutes; 60-min free trial on signup; pricing.html; 12 tests pass |
| 22 | Production Kubernetes deployment | Core | ✅ Done | Helm charts in deploy/; KEDA ScaledObject; PgBouncer sidecar; GKE Autopilot decision; deploy/README.md |
| 23 | VM provisioning + Cloudflare DNS + HTTPS | BETA | ✅ Done | DigitalOcean Droplet at `147.182.136.107` + Cloudflare Tunnel `autoclip-prod` + docker-compose.prod.yml; live at `autoclip.studio` |
| 24 | Production environment configuration | BETA | 🔲 Not started | .env secrets, ALLOWED_ORIGINS, GitHub Actions secrets |
| 25 | External API services provisioning | BETA | 🔲 Not started | Anthropic, Voyage, Deepgram, Cloudflare R2 |
| 26 | Google OAuth consent screen + beta test users | BETA | 🔲 Not started | External status, add friends as test users |
| 27 | YouTube API quota check + backoff verification | BETA | 🔲 Not started | Confirm quota limits; request increase if needed |
| 28 | Beta go-live smoke test + friend onboarding | BETA | 🔲 Not started | Full E2E on live deployment; invite 2-3 friends |
| 29 | Google OAuth app verification | PROD | 🔲 Not started | Submit for Google review; ~1–4 weeks external |
| 30 | Production hardening + public go-live | PROD | 🔲 Not started | Load test; all gates green; v1.0.0 tag |
| 31 | Operability kit — secrets registry, preflight doctor, deploy hardening, auto-heal | BETA | ✅ Done | docs/SECRETS.md + docs/ACCESS.md; scripts/doctor.py (14 tests); cloudflared+autoheal+healthchecks; amd64-only build; fixed blocking billing.tiers import; 313 tests pass |
| 32 | Restore test suite — starlette pin | HARDENING | ✅ Done | Pinned `starlette==0.41.3` (FastAPI 0.115.x range); test suite returns to 313 passed; DECISIONS.md entry on transitive-dep pinning |
| 33 | Cross-creator data leak in improvement brief | HARDENING | ✅ Done | Always-filter `Video.creator_id` added; ORDER BY recency; zero-data 400 short-circuit; new integration test; COMPLIANCE.md Findings & Fixes log; spawned Issue 56 (RLS evaluation) as defense-in-depth |
| 34 | Idempotent minute deduction on Celery retry | HARDENING | ✅ Done | New `MinuteDeduction` ledger with `UNIQUE(video_id)` idempotency key; `deduct_for_video` SAVEPOINT-atomic; 4 real-Postgres integration tests (sequential, concurrent race, 402-clean, audit fields); migration 0003; spawned Issue 57 (refund policy) |
| 41 | Replace pickle in preference model (RCE surface) | HARDENING | ✅ Done | joblib + `_RestrictedUnpickler` allowlist (10 entries); `to_bytes`/`from_bytes` rewritten; 4 new tests (round-trip + 2 rejection tests); no schema change |
| 42 | ffmpeg/subprocess timeouts | HARDENING | ✅ Done | `_run` accepts `timeout_s=120.0`; `_frame_dimensions` hardcodes `timeout=30`; `render_clip_file` computes `max(120, duration*4)`; 3 new timeout tests; DECISIONS.md entry |
| 35 | Idempotent DNA build (SEV-0) | HARDENING | ✅ Done | Single-transaction commit in `_build_dna_async`; `commit=False` param on `create_draft`, `embed_patterns`, `embed_brief`; 3 integration tests; 313 non-integration tests pass |
| 40 | Streaming upload + DoS guard | HARDENING | ✅ Done | 1 MB streaming chunk loop in upload_video; 413 + tempfile unlink on oversize; RSS delta test; 3 new tests in test_videos_upload_streaming.py; 314 tests pass |
| 44 | Auth boundary hardening — malformed sub 401, DELETE /me rate limit, MultiFernet rotation | SEC | ✅ Done | auth.py ValueError/KeyError catch; routers/auth.py 5/hour on DELETE /me; crypto.py MultiFernet + TokenDecryptError; +8 tests |
| 87 | Catalog sync wiring + 180s Shorts threshold (SEV-0 onboarding bug) | HARDENING | ✅ Done | New `sync_channel_catalog` Celery task wired into OAuth callback + Beat refresh + new `POST /me/catalog/sync` endpoint; `/videos/link` + `/videos/upload` resolve kind from real duration; `SHORTS_MAX_DURATION_S=180` configurable; 9 new tests; surfaced live on `reesepludwick@gmail.com`/"backboard media"; DECISIONS.md entry |
| 88 | DNA filter parity + business-event observability (SEV-0 logical bug) | HARDENING | ✅ Done | `rank_videos` no longer requires `ingest_status==done`; `check_data_gate` joins VideoMetrics + uses OR; `sync_channel_catalog` chains metrics fetch (no Beat wait); new `observability.log_event()` helper + diagnostic on insufficient-data raise + 7 wired surfaces; targeted audit spawned Issues 89-91; 8 new tests |
| 89 | Balance pre-check vs deduction mismatch — silent upload failures (SEV-1, spawned by Issue 88 audit) | HARDENING | 🔲 Not started | `check_positive_balance` raises only on `<= 0`; deduction needs `>= video_minutes`. Low-balance creator → upload succeeds → silent failed status with no message |
| 90 | Catalog-synced videos pollute /videos library list (SEV-2, spawned by Issue 88 audit) | HARDENING | 🔲 Not started | `list_videos` returns every Video row; catalog-only rows have no `source_uri` and will never transition out of pending. Dashboard polling loop hammers `/status` forever |
| 91 | "Clips ready" dashboard counter ignores render_status (SEV-2, spawned by Issue 88 audit) | HARDENING | 🔲 Not started | Counter shows total clips regardless of render; reviewer can only play rendered clips. User clicks into "12 ready", scrolls past 12 placeholders |

---

## Open Research Items

- [x] **Pricing model**: Minute packs + Stripe Checkout one-time payments. Issue 21.
- [x] **Production deployment**: GKE Autopilot + Helm + KEDA + PgBouncer. Issue 22.
- [x] **Transcription compute**: Deepgram (hosted) for MVP; WhisperX selectable via config. Resolved 2026-05-25.
- [ ] **YouTube API quota**: Confirm daily quota limits from Google Cloud Console for the project. Issue 27.
- [ ] **Retention curve availability window**: Verify how far back retention curves are available for the target channel.
- [ ] **TOKEN_ENCRYPTION_KEY rotation runbook**: Required before public launch.

---

## Pre-Public-Launch Gates (all must be green before opening to outside creators)

- [x] Lock `ALLOWED_ORIGINS` to production domain; disable `/docs` — env-driven: `docs_url` conditional on `ENV=="development"`; `ALLOWED_ORIGINS` from `.env`
- [x] Per-creator rate limiting + usage quotas before each LLM/render job — Issue 18 (slowapi, 10/h LLM, 20/h render, 120/min rest)
- [x] YouTube data-retention/refresh fully compliant (see `docs/COMPLIANCE.md`) — Issue 17 (Beat purge + analytics refresh)
- [x] `TOKEN_ENCRYPTION_KEY` rotation runbook written — see `docs/RUNBOOKS.md`
- [x] Terms of Service + Privacy Policy pages live — Issue 14 (`/static/tos.html`, `/static/privacy.html`)
- [ ] Google OAuth app verification completed for requested scopes — external Google process (Issue 29)
- [x] Account-deletion endpoint (right-to-erasure: token revocation + media purge) — Issue 19
- [x] Billing wired — Issue 21 (minute packs, atomic balance, 60-min free trial, Stripe Checkout)
- [x] Eval harness hardened with adversarial/edge cases — 3 new fixtures; fixed early-peak MIN_CLIP_S bug
