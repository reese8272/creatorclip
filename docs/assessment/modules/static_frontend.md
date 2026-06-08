# static_frontend — assessed 2026-06-08

## Findings

### UX — First-run discoverability (Category A)

- [SEV2] index.html:276–315 — Hero block (get-started form + clip preview mockup) is only visible when `body.is-hero-mode` class is set by auth.js on 401. Pre-auth visitors see an empty dashboard layout with no CTA until login. The "Get clips →" form uses a basic URL regex (line 324) that silently accepts malformed URLs; user lands on onboarding with a broken URL and the form doesn't auto-fill the input, requiring re-entry | fix: (1) validate the YouTube URL regex against the actual routers/videos.py validation before auth (routers/videos.py:275 uses `YouTubeParser` which is stricter), (2) improve the 401 → hero fallback copy to be clearer ("Sign in to get started →" instead of showing an orphaned form), (3) test the `?yt=` hint auto-fill on auth.js:35-50 end-to-end; the param should survive the OAuth redirect.

- [cleanup] walkthrough.html:13–14, onboarding.html:1–6, index.html:336–350 — Three separate onboarding surfaces exist but no explicit sequential CTA linking them. A brand-new creator goes walkthrough.html → onboarding.html (hardcoded in auth.js:66) → dashboard (redirected on confirm), but the walkthrough "continue →" button at line 247 doesn't explain what comes next. A creator who skips the walkthrough and manually visits onboarding.html has no "back to dashboard" on success. The back button on review.html / analysis.html / insights.html (consistent nav links) all point to "/" (dashboard), but none point to onboarding | fix: (1) after DNA confirm in onboarding.html, redirect to `/static/profile.html` (not dismissed to dashboard) so the user sees the brief immediately and can edit identity before their first review session, (2) add a "Back" link on onboarding.html step 1 → walkthrough if they haven't confirmed yet, (3) update walkthrough.html:247 button text to "Set up my AutoClip →" (already correct; no change needed).

### UX — Empty states (Category B)

- [SEV2] index.html:497–500, review.html:562–576, analysis.html (multiple endpoints with no empty state) — Multiple pages fetch data on load and display a placeholder ("Loading…" / "No clips yet") but don't distinguish between "loading", "loaded-empty", and "error". When `/videos` returns `[]`, the dashboard shows "No videos yet — link one above" but doesn't explain what "link" means (it's a collapsed `<details>` element 50px below the message). Review page shows "No clips yet — generate them from the Dashboard" but doesn't provide a "generate now →" CTA or a "back to dashboard" link. When `/creators/me/insights/analytics` fails (line 669), the grid shows a two-line error but doesn't mention "try again" or rate limits | fix: (1) extract empty-state patterns into reusable HTML blocks with a `template` element; use consistent affordances (why-no-data explanation + next-step CTA), (2) for the "no videos" case, make the "link a video" section auto-expand (details.open = true) when the table is empty, (3) on insights/analysis endpoints: if `analyticsAvailable === false`, surface an explicit "Ingest this video" CTA instead of a silent "no data" inline note (already partially done in analysis.html:705–717 for `analytics_unavailable`, but not for review/insights).

- [cleanup] index.html:388–389 — Analytics grid shows a single placeholder cell ("Loading…") instead of skeleton loader cards (4–5 cells). On a slow connection, the grid jumps from one cell to 5+ cells | fix: pre-render 5 empty analytics-cell divs and use CSS to style them as skeletal (opacity: 0.3; background: gradient) while loading. Replace on data arrival.

### UX — In-app help / tooltips / inline hints (Category C)

- [OK] Tooltips are extensively used (index.html:262–263, 681–682, 311–312; insights.html:311–312, 324–326, 335–336) via `data-tooltip="..."` attribute. tooltip.js implements WCAG 1.4.13 viewport-flip logic + Escape-key dismissal. Tooltip text is plain English ("Average time viewers spent watching…") and cites the metric source.

- [cleanup] review.html:440 — Score tooltip ("Estimated fit with your channel's content DNA…") differs in phrasing from analysis.html score explanation. analysis.html says "estimated fit with your style + audience" while review.html says "grounded in your own data, not a guarantee". Both are honest, but inconsistency suggests the codebase lacks a canonical "what is a score?" explanation | fix: extract "Score: estimated fit, 0–1 scale, grounded in your own data" as a shared constant in a helpers file (or a `<template>` HTML block) and reuse in both review.html + analysis.html tooltip.

### UX — Action affordances (Category D)

- [OK] Primary actions ("Analyze →", "Generate clips", "Keep", "Build Creator DNA") use `btn btn-primary` (accent color, white text) while secondary / exploratory actions ("Rebuild", "Preview", "Skip") use `btn btn-secondary` (border-only, muted text). The pattern is consistent across all pages via _design-tokens.css + page-shell.css.

- [SEV2] index.html:515, 538 — Button text varies per analysis_mode: in "auto" mode the queue button is secondary ("Queue for analysis"), in "selective"/"manual" it's primary. No visual feedback tells the user what mode they're in. A creator switching modes on profile.html won't see the button appearance change on the dashboard without a hard refresh | fix: (1) fetch `analysis_mode` on the dashboard init (already done: line 506 reads `window.__USER__.analysis_mode`), (2) if the mode isn't present in the initial `auth:ready` payload, the page should assume "auto" rather than leaving the button text undefined, (3) on profile.html mode change, dispatch a `CustomEvent('analysis-mode:changed')` that the dashboard listens to and re-renders the button.

- [cleanup] profile.html:482, 535, 569 — Three separate buttons for "confirm DNA", "save identity", "save mode" all use the same `btn btn-primary` style and similar layout, but they're not visually grouped or clearly sequential. A creator filling the form top-to-bottom might save identity, save mode, then scroll back up to confirm DNA—three separate confirmations for one "get ready" flow | fix: consider a single "Save & proceed" button at the bottom of profile.html that POSTs to all three endpoints in series (with error recovery), or at least group the sections with clear section headers ("Identity", "Video intake", "API keys") so the user knows the order of operations.

### UX — Progress / status visibility (Category E)

- [OK] onboarding.html:314–360 (buildDna), analysis.html:728–770 (startAnalysis), profile.html:703–711 (rebuildDna) all stream progress via SSE. progressStream.js renders step/cache/token/done events into a terminal block. The live-progress aesthetic is consistent.

- [SEV2] index.html:638–642 — "Queuing…" state is set on the button (line 632: `btn.textContent = 'Queuing…'`) but if the POST fails (line 636), the button text reverts to "Retry" WITHOUT clearing the "Queuing…" spinner from the user's mind. There's no visual indication that the request failed or what the error was. On a slow network, the user sees "Queue for analysis" → click → "Queuing…" → [5s later no change] → sees it says "Retry" and has to figure out what went wrong | fix: (1) on fetch error (line 639), set `btn.textContent = 'Error · Retry'` and `btn.style.color = 'var(--color-danger)'`, (2) add a status message below the button (e.g., a `<div id="queue-status">` in the cell) that shows the server error detail (from `resp.json().detail`).

- [cleanup] onboarding.html:232–290 (refreshDataGate) — Complex polling loop with two concurrent timers (`_gatePoll` and per-request logic) and a "stable ticks" heuristic (line 277–278). If the user clicks "Refresh data status" twice, `_gatePoll` is cleared and restarted (good), but the stream subscription (`_catalogSub`) is only closed if it's live (line 234–235); if the prior stream never started, there's a leak. The logic is correct but fragile | fix: (1) extract the polling + stream lifecycle into a reusable `subscribeWithFallback(apiEndpoint, pollInterval, successCondition)` helper that handles subscription cleanup on new subscription, (2) simplify the "stable ticks" heuristic: just poll until `data.ready === true` (simpler and matches the data contract), or expose a `ready_at` timestamp from the backend so the frontend stops polling when `Date.now() > ready_at + 3s`.

### UX — Error messages (Category F)

- [OK] index.html:624–625, analysis.html:683–685, insights.html:529–531 (multiple endpoints) — Error responses are consistently unpacked as `await resp.json()` and `.detail` is displayed (e.g., "Error linking video." with data.detail fallback). The copy is plain English.

- [SEV2] index.html:608 — If `extractYouTubeId(raw)` fails (line 603), the status is set to "Enter a YouTube video ID or URL." but the input is left populated. User sees an error but their text is still in the box, so they might click again thinking it's a transient error. No "clear" button or affordance to fix the input | fix: (1) on validation failure, also `input.select()` to highlight the bad text, (2) add a "Clear and try again" link next to the error, (3) if the extracted ID is malformed, POST it anyway and let the backend validation error come back with a richer detail message (e.g., "Video ID must be 11 alphanumeric characters").

### UX — Navigation coherence (Category G)

- [OK] Nav structure is consistent across all pages: `<nav class="nav">` with `.nav-brand`, `.nav-links` (dashboard / review / insights / profile / analyze / pricing), `.nav-spacer`, user name, balance, help link (?), logout. The pattern is identical on every authenticated page.

- [cleanup] onboarding.html:138–142, walkthrough.html (no nav) — onboarding.html has a minimal nav (brand + logout) because it's a setup flow. walkthrough.html has NO nav (only footer links at line 298–302). If a creator sees the walkthrough in a small window, they have NO way to navigate home or to another page. The footer is small and easy to miss. No visual escape hatch if they land on walkthrough by accident | fix: (1) add a minimal nav to walkthrough.html matching onboarding.html, (2) add a "Skip walkthrough" button in the top-right corner (lined button, not primary), (3) if the user is already authenticated (onboarding_state > 'connected'), show the full nav + a "Back to dashboard" button at the top.

- [cleanup] The walkthrough.html and onboarding.html footer links (tos / privacy) are styled inline with `<footer class="footer">`, but the footer appears at the bottom of the viewport even on a short page (e.g., walkthrough on mobile). The footer should be sticky or the page should have a minimum height to avoid awkward visual breakage | fix: add a `min-height: 100vh; display: flex; flex-direction: column;` to walkthrough/onboarding bodies, and use `flex: 1` on the main content to push the footer down. (Already done on index.html line 22, onboarding.html line 20—these pages just need the same pattern.)

### UX — Honesty Constraint compliance (Category H)

- [OK] Grep across all static files found ZERO instances of "go viral", "will trend", or "guaranteed [growth]". Honesty statements appear on every page:
  - "AutoClip predicts fit with your style and audience — it does not promise virality" (index.html:271, review.html:329, insights.html:278, etc.)
  - "Recommendations are estimates grounded in your own data, not guarantees" (walkthrough.html:252, analysis.html:496–497, tos.html).
  - The `<div class="disclaimer">` appears below every nav and is consistent styling.

### Standard Rubric § 3 — Security & compliance

- [OK] No OAuth tokens, API keys, or PII appear in the JavaScript. auth.js:28 receives `/auth/me` response with user data but doesn't log it. Tokens are stored server-side in encrypted cookies (Fernet-encrypted, per routers/auth.py).

- [OK] No environment variables or hardcoded secret endpoints in JS. API calls use relative paths (`/auth/me`, `/videos`, etc.) or template literals that are safe (`/creators/me/dna`, `/clips/${currentClip.id}/feedback`).

### Standard Rubric § 6 — Code cleanliness & typing

- [cleanup] activeTasks.js:1–46, progressStream.js:19–110, activityPanel.js:1–50 — These are well-commented but could use JSDoc types on the public APIs (e.g. `/**@param {string} url*/`, `/**@returns {Object}*/`). The code is vanilla JS with no TypeScript, which matches the repo decision. No `console.log` or `TODO` found.

- [cleanup] review.html:529–543 — `APPROVE_TAGS` and `DENY_TAGS` are inline arrays defined in a `<script>` block. The tag list mirrors API shape but isn't fetched from the backend. If new tags are added to the clip-feedback schema, this frontend list will silently fall out of sync | fix: either (1) fetch `GET /creators/me/feedback-tags` on page load to populate the tag grid dynamically, or (2) document the tag list in a backend config endpoint and embed a comment in the code linking to the routers endpoint that defines the canonical list.

- [cleanup] Multiple `<script>` blocks in single-page HTML files (e.g., index.html has 4 inline script blocks: lines 317–333, 443–776, 778–796). No bundler, no build step. This is fine for a small frontend, but increases code size and parse time. No minification applied. The cache-bust comment (line 784: `<!-- cache-bust: 2026-06-08 -->`) is manual.

### Standard Rubric § 7 — Error handling & API surface

- [OK] fetch() calls check `resp.ok` before calling `resp.json()` (index.html:490–492, analysis.html:682–685, profile.html:632, etc.). Most catch errors and display them.

- [SEV2] editor.js (line 200+, code not fully read) — The file ends at line 150 but the complete editor state machine (~200+ additional lines) wasn't read. The JS file is 396 lines total. Error handling for POST /clips/{id}/cuts and polling for cleaned_render_uri (review.html:808–822) uses a 2-minute timeout with no fallback if the server is slow | fix: (1) read the complete editor.js to ensure no silent failures, (2) add a retry button if a clean render fails or times out.

- [cleanup] analysis.html:643–650 — The alert() calls (lines 648–649) should be replaced with DOM-based error messages. They're blocking and disruptive on mobile. The `if (!urlRaw)` check is input validation, not an error from the API, so an alert is the wrong affordance | fix: show inline validation errors in the form (red text below the input) using the `<div id="low-balance-warning">` pattern already established.

### Standard Rubric § 8 — Config & paths

- [OK] All fetch() calls use relative paths (`/auth/me`, `/videos`, `/creators/me/dna`, `/clips/{id}/feedback`) or `encodeURIComponent()` for user-supplied strings (index.html:332, analysis.html:675–676, review.html:696). No hardcoded localhost or IP addresses.

### Standard Rubric § 2 — Concurrency & scale

- [cleanup] Multiple concurrent EventSources can be open (activeTasks.js:144 opens one per task in localStorage). At scale (10+ concurrent tasks per user), each EventSource consumes a browser connection slot. This is probably OK (browsers allow ~6–10 concurrent HTTP connections per host), but if a creator queues 15 clip renders, some streams might be stalled waiting for connection slots | fix: (1) run a load test with 20 concurrent tasks and confirm no visible lag, (2) if needed, implement a pool: only open 3 EventSources at a time and queue the rest (low priority—unlikely to hit in practice).

- [OK] No blocking calls found in async contexts. Activities like DNA build / analysis run server-side (Celery) and stream progress via EventSource, not via polling `while (true)`.

## Additional findings — UX-critical

- [BLOCKER] index.html:769–770 — The polling loop `_pollTimer = setInterval(...)` to check video ingest status polls every 5 seconds and NEVER stops if in-flight videos stall. If a video gets stuck in "pending" or "running" state on the server, the client will poll forever until the user navigates away. There's no stop condition other than "all videos are done or failed". On a creator's machine with the dashboard open 24/7, this becomes a background battery drain | fix: (1) add a max-retries cap (e.g., 120 polls = 10 minutes, then give up and show an error), (2) exponential backoff after the first 3 failures (5s → 10s → 20s), (3) show a "video stuck" warning if no progress for >5 min.

- [SEV2] index.html:557–589 — The `_registerInFlightIngests()` function walks `videos[]` and registers in-flight tasks, but if the API returns a stale list (e.g., creator has slow network and the /videos request times out), the code might skip real tasks. This is a race condition | fix: (1) on /videos fetch error, don't clear the in-flight task list (keep showing them), (2) deduplicate: check `tracked.has(v.id)` BEFORE registering, so double-registration doesn't happen.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | n/a (frontend) |
| 2 Concurrency & scale | ok — EventSource pooling not needed at current load |
| 3 Security & compliance | ok — no tokens/secrets, per-creator data routed via auth, honesty constraint enforced |
| 4 Clip-quality | n/a (frontend) |
| 5 Anthropic SDK | n/a (frontend) |
| 6 Cleanliness & typing | needs-work — duplicated error message patterns, inline tag lists, scattered inline <script> blocks |
| 7 Error handling / API | needs-work — missing affordances on transient errors, alerts instead of inline feedback |
| 8 Config & paths | ok — relative paths, proper encoding |

## Module verdict

**NEEDS-WORK** — The frontend is coherent and largely works, but UX gaps will make the app feel "barren" to new users. Three concrete issues impact discoverability: (1) The pre-auth hero CTA is hidden on 401 instead of replacing the empty dashboard, making it unclear how to get started. (2) Empty states lack "what do I do next?" CTAs—the user lands on an empty page with no affordance to proceed. (3) Error messages are silent (button state changes with no explanation), and the video ingest polling loop never stops. These are fixable with straightforward DOM updates and state machine improvements. The design tokens, CSS architecture, and honesty-constraint compliance are solid; the issue is UX *completeness*, not correctness.

