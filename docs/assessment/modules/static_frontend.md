# static_frontend — assessed 2026-06-16

## Findings

### Issue-138 SEV1 fixes — VERIFIED CLOSED

- [RESOLVED] analysis.html non-catalog crash (was SEV1) — the dead
  `getElementById('youtube_url')` is gone. `startAnalysis()` now builds the ingest CTA
  href from the in-scope `urlRaw` captured at line 645: analysis.html:711–713
  `const ingestUrl = '/?yt=' + encodeURIComponent('https://www.youtube.com/watch?v=' +
  urlRaw)`, escaped through `_esc` at :716. The only remaining `youtube_url` token is the
  JSON request-body key at :676 (`{ youtube_url: urlRaw, query }`), which is correct.
  The metadata-only analysis path and the Issue-125 "Ingest this video" CTA now work.

- [RESOLVED] innerHTML XSS sinks (was SEV1) — all three flagged sinks now escape via the
  shared `window.escapeHtml` (static/util.js, escapes `& < > " '`):
  index.html:682 `${escapeHtml(v.title || '—')}` (third-party YouTube title) and :683
  `${escapeHtml(v.youtube_video_id)}`; insights.html:574 `${escapeHtml(data.content)}`
  (reflected LLM output) with the Save button's id `${escapeHtml(data.id)}`;
  insights.html:605 `${escapeHtml(ins.title || 'Insight')}` and :606
  `${escapeHtml(ins.content)}` (stored saved-insights). analysis.html:799
  `const _esc = window.escapeHtml;` now delegates to the canonical escaper, used at
  :703/:716/:817/:819/:943/:1114/:1201/:1325. util.js is linked in `<head>` BEFORE the
  inline scripts on all three pages (index.html:14, insights.html:12, analysis.html:11;
  it is a non-deferred classic script, so `window.escapeHtml` is defined before any
  inline script at index.html:396/570, insights.html:370, analysis.html:539/631 runs).
  Regression scan of every `innerHTML = \`...${...}\`` across static/ found no remaining
  unescaped third-party/LLM/DB interpolation on these three pages.

### New finding surfaced by the regression scan

- [SEV2] onboarding.html:461 — `el.innerHTML = \`<span class="ok">Connected as
  ${user.channel_title || user.email}</span>\`` interpolates the third-party YouTube
  `channel_title` into innerHTML UNESCAPED, and onboarding.html does NOT link util.js
  (no `escapeHtml` available). Every other page renders this same value via the safe
  `textContent` path (auth.js:83, index.html:586, analysis.html:639). This sink is
  outside the three Issue-138 targeted, so the bulk sweep did not touch it; a channel
  title containing `<img src=x onerror=…>` executes on the onboarding page | fix: link
  `<script src="/static/util.js"></script>` in the head and wrap with
  `escapeHtml(...)`, or build the node with `textContent` like the other pages.

### Carried forward from 2026-06-09 — STILL PRESENT

- [SEV2] profile.html:881 — `onclick="openRevokeModal('${_escape(k.id)}','${_escape(k.name)}')"`
  — HTML-entity escaping does not protect a JS-in-attribute context (the parser decodes
  `&#39;` back to `'` before the JS runs), so an API-key name with `');payload//` is
  self-XSS and any apostrophe breaks the Revoke button | fix: drop the inline handler;
  render the row, then `btn.addEventListener('click', ...)` reading `row.dataset.keyId`
  and the name from a closure/dataset.

- [SEV2] review.html:803 + editor.js:275 — `registerTask({ taskId, streamUrl, ... })`
  passes camelCase, but `activeTasks.registerTask` (activeTasks.js:184) requires
  `task_id` AND `stream_url` and silently `return`s otherwise. Clean-pass and
  transcript-edit renders are NEVER tracked in the cross-page activity panel. Every
  other call site uses snake_case correctly | fix: rename the keys at both call sites;
  add a `console.warn` in registerTask when required keys are missing.

- [SEV2] pricing.html:312 — `const authed = !!e.detail?.creator;` is always false:
  `/auth/me` returns `{id, channel_id, channel_title, email, onboarding_state, setup}`
  — no `creator` key. Every signed-in user sees "Sign in to buy" and the balance bar
  never renders on the purchase page | fix: `const authed = !!(e.detail && e.detail.id);`.

- [SEV2] pricing.html:184 — `<body class="app-page">` still lacks `data-allow-anonymous`,
  so auth.js redirects every anonymous visitor of the public pricing funnel to
  login.html, while hero.css keeps the Pricing nav link visible in anonymous mode | fix:
  add `data-allow-anonymous` and listen for `auth:anonymous` (renderPacks(false) already
  pre-renders the anonymous display).

- [SEV2] htmx CDN on index.html:12, review.html:11, insights.html:10, profile.html:10,
  onboarding.html:10 — `<script src="https://unpkg.com/htmx.org@1.9.12">` loaded with no
  `integrity`/`crossorigin`, and a repo-wide grep for `hx-(get|post|target|swap|trigger|
  boost)` finds ZERO uses: unused third-party CDN script with full DOM access on five
  authenticated pages (supply-chain risk + wasted bytes) | fix: delete the five tags; if
  htmx is adopted later, self-host or pin with SRI.

- [SEV2] privacy.html:85 + tos.html:58 — both policies promise "the account deletion
  option in your profile settings". `DELETE /auth/me` exists, but profile.html has no
  account-deletion UI (the only DELETE in profile.html, line 968, is the API-key revoke
  call to `/creators/me/api-keys/{id}`). The published policy describes a control that
  does not exist (Google OAuth verification reviewers check this) | fix: add a "Delete
  account" danger-zone to profile.html that confirms then calls `DELETE /auth/me` and
  redirects to login.

- [SEV2] early-access.html:225/230/231 vs pricing.html — two contradictory live pricing
  surfaces: early-access sells $29/$79-per-month subscriptions ("Unlimited videos") while
  pricing.html sells one-time minute packs ("No subscription. No expiry."). early-access
  is also the ONLY page with no honesty-constraint statement (confirmed: 0 disclaimer
  matches vs ≥1 on every other surface) | fix: retire early-access.html or rewrite it on
  the minute-pack model and add the standard disclaimer block.

- [SEV2] review.html:702–706 (sendFeedback) — handles only `resp.ok` and the network
  `catch`; a 4xx/5xx response does nothing (no `else`), so a Keep/Drop vote silently
  vanishes and preference-model training data is lost without the user knowing | fix:
  add an `else` surfacing `(await resp.json()).detail` in `#feedback-flash` with danger
  styling.

- [SEV2] index.html:777, 790 — `queueVideo()`/`generateClips()` failure path still sets
  only `btn.textContent = 'Retry'` with no error message or danger styling | fix:
  surface `data.detail` next to the button and color it `--color-danger`.

- [cleanup] analysis.html:733 & :743 — duplicate `onEvent` key in the
  `subscribeToTaskStream` options literal; the first block (732–737) is dead (second key
  wins) | fix: delete the first `onEvent` (merge its `step` handling into the second).

- [cleanup] DRY escape helpers only PARTIALLY consolidated by Issue 138 — analysis.html
  now delegates to `window.escapeHtml`, but editor.js:151 and profile.html:857 still
  define their own full `_escape` copies, and activityPanel.js:147 still defines a
  text-node-only `safe` (missing `"`/`'`). editor.js (loaded on review.html),
  profile.html, and activityPanel.js do not link util.js, so they cannot delegate yet |
  fix: link `static/util.js` on review.html/profile.html and replace the three local
  helpers with `window.escapeHtml`.

- [cleanup] auth.js:109 defines `logout()` and six pages redefine it identically
  (index.html:580, review.html:553, analysis.html:632, insights.html:371,
  profile.html:622, onboarding.html:452) | fix: delete the page copies; auth.js's global
  is loaded everywhere.

- [cleanup] review.html:592 — `metaEl.textContent += ' (not yet rendered)'` is appended,
  then immediately overwritten by the line-595 assignment, so the hint never shows | fix:
  compose the meta line first, then append the suffix.

- [cleanup] pricing.html:196 — `<span id="nav-auth">` is never populated by any script
  (auth.js writes `#nav-user`/`#nav-balance`), so signed-in users get no name/logout on
  the pricing nav | fix: use the standard `#nav-user` + logout pattern.

- [cleanup] _design-tokens.css:20 — Google Fonts loaded via render-blocking `@import`
  from fonts.googleapis.com; leaks visitor IPs to Google on every page (German GDPR
  rulings have sanctioned this) and is on the pre-launch critical path | fix: self-host
  Inter + JetBrains Mono (woff2) before EU-facing launch.

- [cleanup] index.html:654 — `loadVideos()` issues one `/videos/{id}/clips` fetch per
  done video on every load and poll refresh (client-side N+1; 50 videos → 50 parallel
  requests) | fix: have `/videos` return `clips_total`/`clips_rendered` counts per row
  and drop the fan-out.

Re-verified: no virality promise anywhere (the only `viral`/`guarantee` hits are
anti-virality framing — login.html:137 "audience-fit over generic virality",
analysis.html:586 "cannot guarantee specific CTR or view outcomes"). Honesty disclaimer
present on every authenticated/public surface except early-access.html (flagged). No
OAuth tokens or secrets in any JS; all API fetch paths are relative; user input passes
through `encodeURIComponent`. The dashboard ingest-polling BLOCKER fixed in the
2026-06-08 sweep remains fixed (bounded ticks, backoff, hidden-tab pause).

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | n/a (frontend; EventSources closed on terminal events, polls bounded) |
| 2 Concurrency & scale | 1 finding (client N+1 in loadVideos; EventSource-per-task acceptable at the ≤3-slot cap) |
| 3 Security & compliance | 6 findings (NEW onboarding channel_title innerHTML sink; JS-in-attribute escape bug; htmx CDN w/o SRI; deletion UI promised but absent; fonts-CDN PII) — both Issue-138 SEV1 XSS/dead-id fixes VERIFIED CLOSED; no tokens/secrets in JS; honesty constraint green except early-access.html |
| 4 Clip-quality | n/a (frontend; principle + setup→peak→end surfaced read-only in review.html) |
| 5 Anthropic SDK | n/a (frontend; LLM streams consumed via SSE only) |
| 6 Cleanliness & typing | 5 findings (duplicate onEvent key; escape-helper consolidation only partial; 6× logout copies; dead meta suffix; unpopulated nav-auth) |
| 7 Error handling / API | 3 findings (silent feedback failure; Retry-with-no-reason; pricing auth flag) |
| 8 Config & paths | ok — relative fetch paths, encodeURIComponent on user input, no hardcoded hosts |

## Module verdict
NEEDS-WORK — both Issue-138 SEV1s (analysis non-catalog crash, the three innerHTML XSS
sinks) are verified closed with util.js correctly linked and ordered; no BLOCKER and no
token/cross-tenant exposure remain. The regression scan surfaced one new SEV2 the sweep
left behind (onboarding.html:461 interpolates the third-party YouTube channel_title into
innerHTML unescaped, and onboarding does not link util.js), and the prior SEV2/cleanup
backlog — JS-in-attribute escape bug, broken activity-panel registration, signed-in
pricing telling users to "Sign in to buy", missing account-deletion UI the policies
promise, unused htmx CDN — is still entirely open.

## Issue 75 Reconciliation (2026-06-23)

| Finding | Disposition |
|---|---|
| [SEV2] onboarding.html:461 channel_title innerHTML unescaped (NEW) | → tracked in Issue 229 (HTTP security-headers middleware — frontend XSS hardening) |
| [SEV2] profile.html:881 JS-in-attribute escape bug | → tracked in Issue 229 |
| [SEV2] review.html + editor.js camelCase registerTask (activity panel) | → tracked in Issue 211 (global active-tasks panel) |
| [SEV2] pricing.html authed check always false | → tracked in Issue 109 (deferred design cleanups) |
| [SEV2] pricing.html data-allow-anonymous missing | → tracked in Issue 109 |
| [SEV2] htmx CDN no SRI on 5 pages | → tracked in Issue 229 |
| [SEV2] privacy.html + tos.html promise deletion UI that doesn't exist | → CLOSED: account deletion UI (DELETE /auth/me + profile Danger zone) shipped in Issue 158 |
| [SEV2] early-access.html contradictory pricing + no disclaimer | → tracked in Issue 226 (retire or lock down legacy static UI output sink) |
| [SEV2] sendFeedback silent 4xx/5xx | → tracked in Issue 213 (per-video clips map — feedback UI) |
| [SEV2] queueVideo/generateClips no error display | → tracked in Issue 210 (per-video pipeline status stepper) |
| [cleanup] duplicate onEvent key (analysis.html) | → tracked in Issue 109 |
| [cleanup] escape helpers partially consolidated | → tracked in Issue 109 |
| [cleanup] logout() defined 6× across pages | → tracked in Issue 109 |
| [cleanup] dead meta suffix (review.html:592) | → tracked in Issue 109 |
| [cleanup] unpopulated #nav-auth (pricing.html:196) | → tracked in Issue 109 |
| [cleanup] Google Fonts render-blocking CDN @import | → tracked in Issue 109 |
| [cleanup] loadVideos() client-side N+1 (index.html:654) | → tracked in Issue 210 (per-video pipeline status stepper / dashboard refactor) |
