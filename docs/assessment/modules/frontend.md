# frontend — assessed 2026-07-20 (post-fix)

Slice: the React SPA under `frontend/src` (React 19, react-router-dom 7 Data Mode,
TanStack Query v5, Vite, Tailwind v4) plus the residual `static/` pages
(`tos.html`, `privacy.html`, `accessibility.html`, `_design-tokens.css`).

Method: re-verified every finding from this morning's assessment against HEAD
(e92b93a); reviewed the full `ca3305c..HEAD` frontend diff (15 files — the Issue 361
frontend-tail QueryErrorState refactor, Recap poll + SSE-cap fix, ClipPlayer
autoplay fix); traced the Recap poll's server-side dependencies end-to-end; ran the
seven touched test files (63/63 pass); re-swept XSS sinks, `any`-typing, debug
statements, auth-expiry, honesty copy.

## Resolved since this morning (2026-07-20 AM)

- [RESOLVED, was SEV2] **Recap in-flight render latched forever (no poll fallback)
  + SSE-cap misreported as render failure** — FIXED (commit ae1ce68).
  - Recap.tsx:65-69 adds `refetchInterval` on the summaries query: 4000ms while
    `summaries[0].render_status` is pending/running, `false` once settled — the
    exact fix prescribed. Verified end-to-end against the backend contract:
    POST `/videos/{id}/summaries` commits the Summary row **synchronously** with
    `render_status=pending` before returning (routers/clips.py:1656-1666,
    models.py:863-867 default), and GET orders `created_at.desc()`
    (routers/clips.py:1730), so `summaries?.[0]` is genuinely the newest and the
    poll engages both on the Redis-blip `stream_url=null` path and when the page
    is opened mid-render. Busy state (Recap.tsx:109-111) clears from the same
    poll. Test Recap.test.tsx:198-235 exercises the full cycle with fake timers:
    poll while running → settle without reload → polling stops.
  - Recap.tsx:18 + 198-209 adds `SSE_CAP_MESSAGE` and maps it to a subtle
    "still running, this page updates when it finishes" notice instead of
    "Render failed" (the CaptionStylePanel idiom). String verified verbatim
    end-to-end: routers/tasks.py:78 emits `{"message": "too many open streams"}`
    → lib/taskStream.ts:183 passes `data.message` through unchanged →
    useTaskStream:53 → exact-equality check. Tests cover both the cap mapping
    and that a real SSE failure still renders as a failure (Recap.test.tsx:237-259).
- [RESOLVED, was SEV2] **Query failure rendered as misleading empty state on four
  core pages** — FIXED (commit ae1ce68 + prior wave). Shared
  `components/QueryErrorState.tsx` (typed props, page-specific title/detail,
  uniform Retry) extracted from the Recap idiom and adopted on **all five pages**,
  each with a regression test asserting the retry card renders (not the empty
  state) on a 500 and that Retry refires the query:
  - Dashboard.tsx:157-163 — `videosQuery.isError` → retry card, EmptyHero no
    longer shown on failure (Dashboard.test.tsx:182-204).
  - Review.tsx:158-173 — `isError` branch before `reviewed`/`!clip`, disclaimer
    band preserved (Review.test.tsx:82-107).
  - VideoClipsMap.tsx:151-163 — `videosQuery.isError || clipsQuery.isError` →
    retry card before "Video not found."; onRetry refetches only the failed
    query/queries (VideoClipsMap.test.tsx:228-248).
  - Editor.tsx:359-375 — `clipsError` branch after `clipsPending`, before the
    no-clips UI; **bonus**: transcript failure now shows an explicit
    "Couldn't load the transcript" notice (Editor.tsx:517-521) instead of the
    misleading "No transcript available" (Editor.test.tsx:142-161).
  - Recap.tsx:129-139 — refactored to consume the shared component (was inline).
  Branch ordering audited on every page: `isPending` precedes `isError` precedes
  the empty state — **no page lost its loading/empty distinction** in the
  refactor.
- [RESOLVED] **ClipPlayer black-frame autoplay** (Issue 359d, live-smoke triage) —
  FIXED. ClipPlayer.tsx:73-78 adds `muted` + `preload="auto"` alongside
  `autoPlay`: Chrome blocks unmuted autoplay, which left the element paused on a
  black first frame (the "black render" symptom); muted autoplay is allowed and
  `controls` lets the user unmute. Test asserts all three attributes
  (ClipPlayer.test.tsx:48-57).

## Findings (all carry-forward cleanups; no new defects introduced by the fix waves)

- [cleanup] frontend/src/components/ActivityPanel.tsx:193-195
  (`<Link to={item.link_url}>`) and components/dashboard/DashboardBanners.tsx:32
  (`href={setup.next_action_url ?? '/app/onboarding'}`) — server-supplied URLs
  rendered directly; currently server-controlled (low risk) but unvalidated
  against `javascript:`/external schemes if those fields ever become
  user-influenced. | fix: assert leading `/` (same-origin path) before rendering;
  OWASP DOM-XSS safe-URL guidance. STILL OPEN.

- [cleanup] frontend/src/index.css:14 and static/_design-tokens.css:20 — Google
  Fonts still loaded via render-blocking CSS `@import` from fonts.googleapis.com;
  leaks visitor IPs to Google on every page view (German GDPR rulings), on the
  pre-EU-launch critical path. (The "CSP fonts" work in this range was the
  backend CSP header allowing the CDN, not self-hosting.) | fix: self-host the
  woff2 files (Fontsource or google-webfonts-helper) and drop both @imports.
  STILL OPEN.

- [cleanup] frontend/src/pages/Pricing.tsx:20 — `// TODO: drive from
  /billing/packs API…` — CLAUDE.md forbids TODO comments in closed work. | fix:
  file the follow-up in docs/issues.md and delete the comment. STILL OPEN.

- [cleanup] static/tos.html:55 + static/privacy.html:91,94,149,171 — external
  `target="_blank"` anchors carry no `rel="noopener noreferrer"`. Modern browsers
  default `noopener`, so exploitation risk is nil; flagged for consistency
  (Login.tsx sets it). | fix: add the rel attribute. STILL OPEN.

- [info, carried] The HAR malformed-font-URL (`display=swap%CC%A6`, Open+Sans) was
  confirmed this morning to be extension-injected, not from this codebase (zero
  repo/history hits, deployed bundle clean, empty-Referer status-0 requests).
  No action; re-capture the HAR in a clean profile if it recurs.

- [cleanup / NOT-a-defect, retained so the next audit doesn't re-raise it]
  src/components/chip/ChipStates.tsx `ChipAnalyzing`/`ChipRendering` exported but
  unmounted — intentional Issue 314 deferral (documented in-file). Leave.

Re-verified this run: 63/63 tests pass across the seven touched files; no
`dangerouslySetInnerHTML`/`innerHTML`/`eval` sinks in frontend/src (only comments
referencing the structural avoidance, brief.ts); no `console.log`/`: any`/`as any`
outside tests; 401 → login redirect centralized in api.ts unchanged; disclaimer
bands preserved on the new Review/Editor error branches (honesty copy intact — no
virality promise anywhere, including the new SSE-cap and retry copy); Profile
in-text link change (hover:underline → underline) is the axe link-in-text-block
a11y fix, no behavior change; QueryErrorState is fully typed with no state of its
own (pure presentational, retry delegated to the caller's `refetch`).

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle (EventSource/subscription cleanup) | ok — no SSE lifecycle changes in this range; ref-diff pattern intact; Recap poll is TanStack-managed (stops on settle/unmount) |
| 2 Concurrency & scale | ok — Recap poll gap closed; interval is bounded (4s, stops when settled), matching the Review/Dashboard idiom |
| 3 Security & compliance (XSS/secrets/auth) | ok / 2 cleanup — no HTML sinks; server-URL Link hardening + static rel=noopener remain; fonts-CDN IP leak (GDPR) open |
| 4 Clip-quality | n/a (not a clip module; principle/fit-tier surfaced read-only, honest) |
| 5 Anthropic SDK | n/a (frontend; LLM consumed via SSE only) |
| 6 Cleanliness & typing | ok / 1 cleanup — one TODO (Pricing.tsx:20); new component and branches fully typed |
| 7 Error handling / user-facing error states | ok — both AM SEV2s fixed and regression-tested on every page; loading vs error vs empty now distinct on Dashboard, Review, VideoClipsMap, Editor, Recap; SSE cap honestly mapped |
| 8 Config & paths | ok — relative fetch paths, `BASE_URL` respected, no client secrets |

## Module verdict
clean — both SEV2s from this morning (Recap forever-latch/no-poll + four pages
rendering API failures as first-run empty states) are verified fixed with
regression tests and correct branch ordering, the ClipPlayer black-frame autoplay
fix landed, and the diff introduced no regressions; only four pre-existing
cleanups remain (server-URL scheme guard, self-hosted fonts, Pricing TODO,
static rel=noopener).
