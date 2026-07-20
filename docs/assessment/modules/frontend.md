# frontend — assessed 2026-07-20

Slice: the React SPA under `frontend/src` (React 19, react-router-dom 7 Data Mode,
TanStack Query v5, Vite, Tailwind v4) **plus the residual `static/` pages** — this file
now folds in what was previously `static_frontend.md`, because the legacy vanilla app
was physically deleted in the assessed range (see Resolved section): `static/` today is
only `tos.html`, `privacy.html`, `accessibility.html`, `_design-tokens.css`.

Method: verified every 2026-07-01 finding against HEAD (ca3305c); scrutinized the
`f70a857..HEAD` diff (20 frontend + 13 static files — PR #54 Recap fix, Issue 352
Batch K, Issue 346 error boundary, static retirement); ran the requested Google-Fonts
malformed-URL hunt; swept query error/loading states, mutation handling, auth expiry,
XSS posture.

## Resolved since 2026-07-01

- [RESOLVED, was SEV1] No root `errorElement`/`onUncaughtError` — **FIXED (Issue 346)**.
  App.tsx:67 sets `errorElement: <RootError />` on the root `RootLayout` route with a
  branded Reload / Back-to-dashboard recovery UI (App.tsx:30-55); main.tsx:8-14 adds
  `onUncaughtError` + `onRecoverableError` to `createRoot`. A render throw no longer
  blanks the SPA.
- [RESOLVED, was SEV2] VideoTable `act()` raw fetch with no try/catch (stuck-busy
  latch) — **FIXED (Issue 352 Batch K)**. VideoTable.tsx:67-84 now derives busy/label
  from a TanStack `useMutation` over `api()`; `isError` resets the button to "Retry".
- [RESOLVED, was SEV2] ChannelBrowser `clip()` identical latch — **FIXED**.
  ChannelBrowser.tsx:28-66 wraps the (documented, intentional) multipart raw fetch in a
  mutation; non-ok throws; error → "Retry".
- [RESOLVED, was SEV2 needs-runtime-confirmation] ActivityPanel `useTaskSubscriptions`
  open-then-teardown of each task SSE — **FIXED (Issue 352 Batch K)**.
  ActivityPanel.tsx:61-128 keeps open connections in a `useRef` map and diffs it
  against the store instead of closure-scoped cleanup; the code comment explicitly
  cites the 2026-07-01 finding.
- [RESOLVED, was cleanup] Raw-fetch consolidation — substantially done. The JSON POSTs
  now route through `api()`; the four remaining raw fetches are each a documented
  exception (Editor.tsx:165 arrayBuffer waveform, lib/activity.ts:23 keepalive beacon,
  ChannelBrowser.tsx:32 FormData, BrandKitSection.tsx:42 optional 204-aware suggestion
  probe with its own catch).
- [RESOLVED by deletion] **The entire 2026-06-16 `static_frontend.md` open backlog is
  moot**: the legacy pages/JS it targeted (onboarding.html, profile.html, pricing.html,
  index.html, review.html, insights.html, analysis.html, early-access.html, auth.js,
  editor.js, activeTasks.js, activityPanel.js, progressStream.js, util.js, all page
  CSS) were removed in the assessed range (Issue 226 retirement — 2,355 deletions in
  `f70a857..HEAD`). That closes: the onboarding channel_title innerHTML XSS sink, the
  profile JS-in-attribute escape bug, both pricing auth bugs, the htmx-CDN-without-SRI
  supply-chain risk, the early-access contradictory pricing page, the camelCase
  registerTask bug, and all related cleanups. Remaining static pages re-checked below.

## Findings

- [SEV2] frontend/src/pages/Recap.tsx:51-55, 92-96 + components/recap/RecapPlayer.tsx:13-25
  — an in-flight recap render has **no poll fallback**: the summaries query has no
  `refetchInterval`, so when `stream_url` is null (the acknowledged Redis-blip
  fail-open, Recap.tsx:61-63) or when the page is opened while a render is already
  running (streamUrl state starts null), `renderInFlight` keeps `busy` latched, the
  button shows "Recap rendering…" and RecapPlayer spins "this takes a few minutes"
  **indefinitely until a manual reload** — the comment "the list poll/refresh picks up
  the result" describes a poll that does not exist (queryClient defaults:
  `refetchOnWindowFocus: false`). Compounding: an SSE cap rejection ("too many open
  streams") surfaces via useTaskStream as "Render failed — too many open streams"
  (Recap.tsx:191-193) even though the render is still queued — CaptionStylePanel
  special-cases exactly this (`SSE_CAP_MESSAGE`, CaptionStylePanel.tsx:22-24,63-68);
  Recap does not. | fix: add
  `refetchInterval: () => (latest is pending/running ? 4000 : false)` to the summaries
  query (the Review.tsx:121-128 idiom), and map the cap message to a
  "queued — progress unavailable" status instead of a failure.

- [SEV2] frontend/src/pages/Dashboard.tsx:154-160, pages/Review.tsx:114-158,
  pages/VideoClipsMap.tsx:140-154, pages/Editor.tsx:121-138 — **query failure renders
  as a misleading empty state** on four core pages: after the single retry, `isError`
  is never branched, `data` is undefined, and the `?? []` fallbacks make a 500/network
  failure look like first-run emptiness — Dashboard shows the EmptyHero "No videos
  yet — pick a path above", Review shows "No clips yet — generate them from the
  Dashboard", VideoClipsMap shows "Video not found.", Editor shows its no-clips UI. A
  creator with 50 videos who hits a transient API error is told they have none (401 is
  handled — `api()` redirects to login — this is the 5xx/network path). | fix: branch
  on `query.isError` with the retry-affordance card Recap.tsx:114-132 already
  implements; extract that block into a shared `<QueryErrorState onRetry={refetch}>`.

- [cleanup] (carry-forward) frontend/src/components/ActivityPanel.tsx:193-199
  (`<Link to={item.link_url}>`) and components/dashboard/DashboardBanners.tsx:32
  (`href={setup.next_action_url ?? '/app/onboarding'}`) — server-supplied URLs rendered
  directly; currently server-controlled (low risk) but unvalidated against
  `javascript:`/external schemes if those fields ever become user-influenced. | fix:
  assert leading `/` (same-origin path) before rendering; OWASP DOM-XSS safe-URL
  guidance.

- [cleanup] (carry-forward, extended) frontend/src/index.css:14 and
  static/_design-tokens.css:20 — Google Fonts loaded via render-blocking CSS `@import`
  from fonts.googleapis.com (index.css pulls three families: Geist, Geist Mono, Inter);
  leaks visitor IPs to Google on every page view (German GDPR rulings) and is on the
  pre-EU-launch critical path. | fix: self-host the woff2 files (Fontsource or
  `google-webfonts-helper`) and drop both @imports.

- [cleanup] frontend/src/pages/Pricing.tsx:20 — `// TODO: drive from /billing/packs
  API…` — CLAUDE.md forbids TODO comments in closed work. | fix: file the follow-up in
  docs/issues.md (it already says "follow-up issue") and delete the comment.

- [cleanup] static/tos.html:55 + static/privacy.html:91,94,149,171 — external
  `target="_blank"` anchors carry no `rel="noopener noreferrer"`. Modern browsers
  default `noopener` for target=_blank so exploitation risk is nil; flagged only for
  consistency (Login.tsx:86-98 sets it). | fix: add the rel attribute.

- [info / NOT a repo defect — requested malformed-font-URL investigation] The HAR
  (`autoclip.studio.har`) request
  `fonts.googleapis.com/css2?family=Open+Sans:...&display=swap%CC%A6` (stray U+0326
  combining comma) **does not originate from this codebase**. Evidence: (1) repo-wide
  grep for `Open+Sans` / `ital,wght` / `Poppins` across frontend/, static/, docs, the
  design-handoff files and untracked files — zero hits outside the HAR itself;
  (2) `git log --all -S "Open+Sans"` — never existed in history; (3) the live deployed
  bundle (fetched `https://autoclip.studio/app/assets/index-Bx-OecO_.css`, 2026-07-20)
  contains only the well-formed Geist/Geist-Mono/Inter URL matching index.css:14;
  (4) in the HAR both Open+Sans entries have empty Referer, initiator `{"type":
  "other"}` and status 0 (blocked/cancelled), unlike the app's own font request. All
  four signals point to a **browser-extension-injected stylesheet** in the capture
  session. No file:line exists to fix; recommend re-capturing the HAR in a clean
  profile before spending more time on it. (Self-hosting fonts — cleanup above — would
  also make any third-party font request trivially attributable.)

- [cleanup / NOT-a-defect, retained so the next audit doesn't re-raise it]
  src/components/chip/ChipStates.tsx `ChipAnalyzing`/`ChipRendering` exported but
  unmounted — confirmed intentional Issue 314 deferral (documented in-file). Leave.

Re-verified this run: no `dangerouslySetInnerHTML`/`innerHTML`/`eval` anywhere in
frontend/src (LLM brief output rendered as auto-escaped React children, brief.ts);
zero `: any`/`as any` outside tests; no secrets/tokens in client code or storage;
401 → login redirect centralized in api.ts:42-46; honesty disclaimer present on
Dashboard/Review DisclaimerBands, Recap copy, tos.html:52/68 — no virality promise
anywhere (Onboarding's new small-catalog path and Login's signup-paused notice both
keep the honest framing); SSE lifecycles all close on unmount/URL change
(useTaskStream:55, ActivityPanel:121-127, CaptionStylePanel subRef cleanup); the
corrected SSE-cap posture (server is sole enforcer, activeTasks.ts header) matches the
consumers.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle (EventSource/subscription cleanup) | ok — prior F4 double-open fixed with ref-diff pattern; all SSE hooks close on unmount; CaptionStylePanel new subscription closes via subRef cleanup |
| 2 Concurrency & scale | ok / 1 SEV2 facet — Recap lacks the poll fallback every other in-flight surface has (Review/Dashboard/CleanedUriPoll all bound their refetchIntervals) |
| 3 Security & compliance (XSS/secrets/auth) | ok / 2 cleanup — no HTML sinks; legacy static XSS surfaces deleted; server-URL Link hardening + static rel=noopener remain; fonts-CDN IP leak (GDPR) open |
| 4 Clip-quality | n/a (not a clip module; principle/fit-tier surfaced read-only, honest) |
| 5 Anthropic SDK | n/a (frontend; LLM consumed via SSE only) |
| 6 Cleanliness & typing | ok / 1 cleanup — one TODO (Pricing.tsx:20); no `any`, signatures typed |
| 7 Error handling / user-facing error states | 2 SEV2 — four pages render query errors as empty states; Recap's no-poll/cap-message gap. Mutations across changed files now surface errors correctly (Retry labels, ApiError messages) |
| 8 Config & paths | ok — relative fetch paths, `BASE_URL` respected, no client secrets |

## Module verdict
NEEDS-WORK — the two headline 2026-07-01 defects (missing root error boundary SEV1,
stuck-busy raw fetches) are verified fixed and the legacy static backlog closed by
deletion; what remains is one SEV2 pair in the error-state layer: Recap can latch
"rendering" forever with no poll fallback, and four core pages present API failures
as first-run empty states.
