# frontend — assessed 2026-07-29 (ready-pass delta; prior clean pass 2026-07-20 retained below)

Slice: React SPA under `frontend/src` + e2e harness under `frontend/e2e`, plus residual
`static/` pages. Delta range reviewed: `e92b93a..HEAD` on `w3/ready-pass-closeout`
(43 files — ApiError structured detail, ClipPlayer source-expired card, Recap error
state, WhyThisClip Apply + AppliedTitleField + useApplyClipMetadata, CleanedPreviewConfirm
extraction + YourCall trim-rerender, PublishPanel + SchedulePublishDialog + lib/schedule,
Review clipDur origin fix, lib/safeUrl, cleaned-preview → download endpoint, Fontsource
self-hosting, e2e fixtures/smoke pages/visual baselines).

Method: read every new/changed file; traced the new contracts into the backend
(routers/publications.py, routers/review.py trim timebase, routers/clips.py 409
source_expired + clean/confirm + download 302, upload_intel/timing.py day convention,
models.py:418); re-swept XSS sinks, ApiError message consumers, TanStack
mutation/invalidation keys against page query keys; ran the unit suite (277/277 pass,
44 files).

## Findings — 2026-07-29 delta

- [SEV1] frontend/src/components/review/ClipPlayer.tsx:26 — the dd92fcd
  "clip-relative timebase" fix updated Review.tsx's `clipDur` to the setup origin but
  MISSED ClipPlayer's own `const clipDur = clip.end_s - clip.start_s`, which is what
  actually feeds `<TrimFilmstrip duration={clipDur}>` (line 144). The filmstrip's
  x↔time mapping therefore uses `end_s - start_s` while feedback/trim-render validate
  and cut against `end_s - (setup_start_s ?? start_s)` (routers/review.py:106-155,
  routers/clips.py:944-945). On setup clips (the normal case — e.g. fixture c1:
  setup 120 / start 125 / end 160 → real 40s vs filmstrip 35s) every handle drag
  submits systematically compressed seconds (~12% early in the fixture geometry),
  the last `start_s - setup_start_s` seconds are unreachable, and the ruler/readout/
  playhead are mis-scaled — corrupting both the Save-trim preference signal and the
  trim-render output. The duration LABEL at line 137 already uses the setup origin, so
  the display and the filmstrip disagree in the same component. | fix: line 26 →
  `const clipDur = clip.end_s - (clip.setup_start_s ?? clip.start_s)`; add a
  ClipPlayer test asserting the filmstrip max/readout equals the setup-origin duration
  for a clip with `setup_start_s < start_s`.

- [SEV2] frontend/src/components/review/CleanedPreviewConfirm.tsx +
  hooks/useCleanedUriPoll.ts:16 — stale-cache latch on repeat trims/cleans. The poll
  caches under `['clips-clean-poll', videoId]`; POST /clean/confirm nulls
  `cleaned_render_uri` server-side but only `['review-clips', video_id]` is
  invalidated (CleanedPreviewConfirm.tsx:32). With the global `staleTime: 30_000`
  (lib/queryClient.ts:12) a second "Apply trim & re-render" within 30s remounts the
  poll against fresh-by-staleTime cached data whose old `cleaned_render_uri` is still
  set → the "Use cleaned version" card renders instantly for the PREVIOUS (already
  swapped-in) version, `refetchInterval` returns `false` (URI truthy) so it never
  polls for the new render, and a fast Confirm hits the idempotent noop
  (routers/clips.py:802-808) — reporting success while the new trim is still in
  flight and later lands as an orphaned pending clean (next attempt 409
  pending_clean_or_edit). | fix: in CleanedPreviewConfirm's `confirm()` also
  `queryClient.removeQueries({ queryKey: ['clips-clean-poll', clip.video_id] })`,
  and/or give the poll query `staleTime: 0` so re-enable always refetches before the
  interval callback decides.

- [SEV2] frontend/src/components/review/ClipPlayer.tsx:75-77 — after a confirmed
  swap the main player keeps playing the OLD media. `<video key={clip.id}
  src="/clips/{id}/download?disposition=inline">`: confirm changes `render_uri`
  server-side and the invalidation refreshes the clip object, but key and src are
  both unchanged so the element never reloads (media was fetched once via the 302
  presign, routers/clips.py:1185-1187). The YourCall flow tells the creator
  "Trimmed version is now the main render" (YourCall.tsx:178) while the player above
  still shows the untrimmed mp4 until navigation/reload. Same latent issue on the
  Editor main player (Editor.tsx:378/456 — pre-existing there via the clean pass). |
  fix: key the video on the artifact, e.g. `key={`${clip.id}:${clip.render_uri}`}`
  (or append a version param derived from `render_uri`) so the swap remounts and
  refetches.

- [SEV2] frontend/src/components/ui/modal.tsx:11-27 — the Modal underlying the new
  SchedulePublishDialog has none of the WAI-ARIA dialog contract: no `role="dialog"`
  / `aria-modal="true"` / `aria-labelledby` (the h3 title is unassociated), no
  initial focus or focus trap (keyboard focus stays in the page behind), no Escape
  handling (backdrop close is pointer-only; the Close button is the only keyboard
  exit). Pre-existing component (ApiKeysSection, ChannelBrowser) but newly load-
  bearing on the review page's publish flow. | fix: migrate Modal to native
  `<dialog>` + `showModal()` (focus + Esc + top-layer for free) or add
  role/aria-modal/aria-labelledby, focus the panel on open, restore focus on close,
  and close on Escape.

- [cleanup] frontend/src/lib/schedule.ts:5-12 — `nextOccurrence` sets the hour on
  the FROM day before adding days, so on a DST spring-forward day a window hour
  inside the 02:00 gap normalizes forward and the +1h skew carries onto the target
  day (and a target-day gap hour skews regardless). One hour, one day a year, only
  for 02:00-03:00 windows — bounded. Week-rollover + tz-aware serialization ARE
  correct and tested (PublishPanel.test.tsx nextOccurrence describe; toISOString →
  UTC; `datetime-local` parses as local per spec). Convention verified end-to-end:
  0=Sunday matches models.py:418 and upload_intel/timing.py `_DAY_NAMES`. | fix:
  compute delta from `from.getDay()`, `setDate` first, then `setHours(hour,0,0,0)`;
  optionally add a lib/schedule.test.ts pinning the DST cases.

- [cleanup] frontend/src/components/review/PublishPanel.tsx:232-237 — the
  publications poll runs only while a pub is pending/running; with the global
  `refetchOnWindowFocus: false` a Review page left open across the scheduled time
  never observes scheduled/confirmed → pending → done. Bounded (windows are
  typically days out; a revisit refetches). | fix (optional): also return a slow
  interval when a confirmed pub's `scheduled_at` is within a few minutes.

- [cleanup] frontend/e2e/fixtures/mock-api.ts:399 — fixture fidelity gap:
  `TRANSCRIPT.clip_duration_s: 35` for c1 is shaped to the buggy `end_s - start_s`
  math; the backend computes `end_s - (setup_start_s ?? start_s)` = 40
  (routers/clips.py:665-666), so the harness geometry can never catch the ClipPlayer
  SEV1 above. Everything else checked is shape-faithful: PublicationOut fields +
  per-row privacy_note + envelope truncated match routers/publications.py:73-120;
  suggested_windows keys match upload_intel/timing.py:64-67; cancel-as-
  status=failed + "Cancelled by creator" matches routers/publications.py:338;
  BRAND_KIT/SUMMARIES consistent with their routers. | fix: set clip_duration_s to
  40 (and keep fixtures on the setup-origin timebase).

- [cleanup] frontend/src/pages/Editor.tsx test lane — vitest stderr shows a React
  controlled→uncontrolled input warning during "renders the editor with clip meta"
  (Editor.test.tsx). Test-lane only; not reproduced in the app (needs a quick chase
  — likely a fixture field going undefined). | fix: pin the offending input's value
  with a fallback.

### Carried from 2026-07-20 (still open)

- [cleanup] ActivityPanel.tsx:193-196 — `<Link to={item.link_url}>` is the one
  server-influenced sink the safeUrl pass missed (React Router ≥6.4 renders
  absolute/scheme'd `to` strings as-is, `javascript:` included). Timeline waveform
  and DashboardBanners WERE fixed via safeUrl this range. | fix: `to={safeUrl(item.link_url) ?? '#'}`
  or assert leading `/`.
- [cleanup] static/_design-tokens.css:20 — Google Fonts @import remains on the
  static tos/privacy/accessibility pages (GDPR IP leak). The SPA side is RESOLVED —
  self-hosted Fontsource variable fonts imported in main.tsx:8-10, index.css @import
  removed. | fix: self-host for static pages too.
- [cleanup] Pricing.tsx:20 — TODO comment still present. | fix: file follow-up +
  delete.
- [cleanup] static/tos.html + static/privacy.html — external `target="_blank"`
  anchors still without `rel="noopener noreferrer"`.

### Verified clean this delta (no findings)

- **ApiError blast radius**: every message-only consumer (23 call sites) reads
  `.message` behind an `instanceof ApiError` guard or via typed onError; no consumer
  exact-matches an ApiError message (the only exact string match, SSE_CAP_MESSAGE,
  is on the taskStream path, untouched). Object `{code,message}` details now render
  the human message (was "[object Object]"); Pydantic-422 array details fall back to
  "Request failed (422)" — safe. `.code` branches (source_expired in
  ClipPlayer/Recap, pending_clean_or_edit in YourCall) match the backend's
  structured 409s verbatim (routers/clips.py:517-522, 1648-1653; routers/clips.py
  edit 409). api.test.ts covers derivation.
- **safeUrl**: correct OWASP pattern (real URL parse + http/https allowlist, no
  blocklist); relative paths pass, `javascript:`/`data:`/`s3:`/unparseable →
  undefined so React omits the attribute. Applied to both intended sinks.
  s3:// cleaned URIs no longer bound to <video> anywhere — all four cleaned-preview
  players (CleanedPreviewConfirm, TranscriptEditor, Editor, ClipPlayer main) go
  through the authed download endpoint; PublishPanel's only external href is
  youtu.be with an encodeURIComponent'd id.
- **Mutation/invalidation keys**: useApplyClipMetadata → `['review-clips',
  clip.video_id]` matches Review/Editor query keys; PublishPanel mutations await
  `['publications', clip.id]` invalidation (awaited → isPending holds until fresh);
  applied-state (`clip.applied_title === t.title`) reads from the refreshed query.
- **UTF-8 byte counting**: TITLE_MAX_CHARS=100 / DESCRIPTION_MAX_BYTES=5000 via
  TextEncoder mirror the backend's Field(max_length=100) + byte validator
  (routers/clips.py:183-208); JS pre-checks are equal-or-stricter, never looser.
- **Honesty copy**: intact everywhere new — FitBadge tiers lead, "fit estimate, not
  a guarantee", suggestion disclaimers rendered, publications privacy_note surfaced
  verbatim; no virality promise in any new string.
- **Suite**: 277/277 unit tests pass; PublishPanel (10 tests incl. tz-aware body,
  past-datetime rejection, cancel gating, badge inference), AppliedTitleField,
  YourCall trim flow, ClipPlayer source-expired card, Recap error card all
  regression-tested; e2e visual baselines committed per the Linux-runner workflow.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle (subscriptions/polls) | 1 SEV2 — clips-clean-poll stale-cache latch on repeat trim; TanStack-managed intervals otherwise stop on settle/unmount |
| 2 Concurrency & scale | ok / 1 cleanup — polls bounded (3-4s, stop conditions correct modulo the stale-cache case); publications poll gap across scheduled transition |
| 3 Security & compliance (XSS/secrets/auth) | ok / 1 carried cleanup — safeUrl allowlist correct; ActivityPanel link_url still unguarded; no HTML sinks; no secrets client-side |
| 4 Clip-quality | n/a (not a clip module) — but the SEV1 timebase miss corrupts the trim preference signal the clip engine consumes |
| 5 Anthropic SDK | n/a (frontend) |
| 6 Cleanliness & typing | ok / 2 cleanup — Pricing TODO carried; Editor test controlled-input warning; new code fully typed, no any/console |
| 7 Error handling / user-facing states | 2 SEV2 + 1 SEV1 adjacency — source-expired/Recap/ApiError branches correct and tested, but stale main-player after swap and the dialog a11y contract fail the surface; trim filmstrip mis-scaled |
| 8 Config & paths | ok — relative fetch paths, fonts self-hosted (SPA), no new config |

## Module verdict
NEEDS-WORK — the ready-pass features are contract-faithful and well-tested (ApiError
codes, publish scheduling, byte-counted metadata, safeUrl, e2e fidelity), but the
clipDur origin fix is incomplete: ClipPlayer's filmstrip still maps drags against
`end_s - start_s` (SEV1, skews every saved trim + trim-render on setup clips), plus
three SEV2s (stale clean-poll cache latch on repeat trims, main player not reloading
after a confirmed swap, missing ARIA dialog semantics on the new publish dialog).

---

# frontend — assessed 2026-07-20 (post-fix) [historical]

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
  OWASP DOM-XSS safe-URL guidance. → 2026-07-29: DashboardBanners FIXED via
  safeUrl; ActivityPanel STILL OPEN.

- [cleanup] frontend/src/index.css:14 and static/_design-tokens.css:20 — Google
  Fonts still loaded via render-blocking CSS `@import` from fonts.googleapis.com;
  leaks visitor IPs to Google on every page view (German GDPR rulings), on the
  pre-EU-launch critical path. → 2026-07-29: SPA side FIXED (Fontsource
  self-hosted, main.tsx); static pages STILL OPEN.

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

## Module verdict (2026-07-20)
clean — both SEV2s from this morning verified fixed with regression tests; only
four pre-existing cleanups remained. Superseded by the 2026-07-29 delta above.
