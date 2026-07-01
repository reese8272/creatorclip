# frontend — assessed 2026-07-01

Slice: everything under `frontend/src`. Stack confirmed from `frontend/package.json`:
**React 19.2.6**, **react-router-dom 7.18.0** (Data Mode / `createBrowserRouter`),
**@tanstack/react-query 5.101.0**, TypeScript ~6.0, Vite 8. (Last run's docs assumed
React 18 — the code is on React 19, which changes the uncaught-error default; see F1.)

All framework-behavior claims below were verified against current official docs
(WebSearch/WebFetch, 2026-07-01), per the hard constraint — citations inline.

## Findings

- [SEV1] src/App.tsx:33-69 (+ src/main.tsx:8) — **No route `errorElement`/`ErrorBoundary`
  anywhere in the router, and no `onUncaughtError` on `createRoot`.** React Router v7 Data
  Mode renders an **empty page** when a route throws during render if no error boundary is
  defined — the docs state "route modules will automatically catch errors … render the
  closest `ErrorBoundary`" and "**All applications should at a minimum export a root error
  boundary**." So a single render throw in *any* page (a malformed `/auth/me` shape, an
  undefined access on API/LLM data) blanks the whole SPA — no nav, no recovery. Under React
  19 an error not caught by a boundary is reported to `window.reportError` and the root
  unmounts (blank screen) by default. | fix: add a root `errorElement` on the top
  `RootLayout` route that renders a branded fallback via `useRouteError()` with a
  "reload / back to dashboard" affordance; add `onUncaughtError`/`onRecoverableError` to
  `createRoot` for telemetry. Cite: https://reactrouter.com/how-to/error-boundary ,
  https://react.dev/reference/react-dom/client/createRoot (2026-07-01).

- [SEV2] src/components/dashboard/VideoTable.tsx:59-70 — `act()` calls `fetch(url, {POST})`
  with **no try/catch**. On a network-level rejection (offline, DNS, CORS, aborted request)
  the promise rejects unhandled and `busy` stays `true` with the label stuck on
  "Queuing…/Generating…" forever — the exact spinner-latch failure class the repo already
  fixed elsewhere (commit aca664b, render-spinner latch). It also bypasses `api()`'s
  ApiError/401 handling. | fix: wrap the fetch so a thrown/failed request resets
  `setBusy(false)` + `setLabel('Retry')` in a `catch`/`finally`; or route through `api()`.

- [SEV2] src/components/dashboard/ChannelBrowser.tsx:26-52 — `clip()` has the **identical
  raw-`fetch` no-try/catch latch**: a rejected fetch leaves `busy=true` and the button stuck
  on "Adding…". | fix: same catch/finally reset. (See F5 — this and VideoTable.act share the
  busy/label POST pattern; extract one hook.)

- [SEV2] (needs-runtime-confirmation) src/components/ActivityPanel.tsx:65-98 —
  `useTaskSubscriptions` calls `upsert(entry.taskId, { subscribed: true })` **synchronously
  inside the effect whose dependency is the `tasks` map itself**. `upsert` runs `_notify()`
  which replaces the map reference (activeTasks.ts:96) → re-render → this effect's cleanup
  fires (closing the EventSource just opened) → the re-run sees `subscribed === true` and
  skips reopening. Net risk: each task's SSE is opened then torn down immediately and never
  streams live progress into the panel. | fix: don't mutate the store dependency inside the
  same effect — track opened slots in a `useRef`, or split "select tasks needing a sub"
  (read) from the `subscribed` write so the write doesn't invalidate the effect that opened
  the socket. Verify against a live in-flight task before/after.

- [cleanup] src/lib/api.ts vs five raw `fetch()` sites (Editor.tsx:165, VideoTable.tsx:62,
  ChannelBrowser.tsx:37, BrandKitSection.tsx:42, activity.ts:23) — the typed `api()` client
  exists precisely to centralize `credentials`/401-redirect/`ApiError` (DRY). Three sites are
  legitimately outside it (multipart FormData in ChannelBrowser, `arrayBuffer` waveform in
  Editor, `keepalive` beacon in activity — all with their own error handling); the JSON POSTs
  in VideoTable are not. | fix: extend `api()` to accept `FormData`/blob responses and route
  the JSON callers through it; keep the beacon/arraybuffer cases as documented exceptions.

- [cleanup] src/components/ActivityPanel.tsx:164-171 & src/components/dashboard/DashboardBanners.tsx:32
  — `<Link to={item.link_url}>` / `href={setup.next_action_url}` render a **server-supplied
  URL directly**. Currently server-controlled (low risk), but RR `<Link>`/an `<a>` will emit a
  `javascript:`/`data:` href unchallenged if that field ever becomes user-influenced. | fix:
  validate the value is a same-origin path (leading `/`, not a scheme) before rendering.
  OWASP DOM-XSS "safe URL" guidance: https://cheatsheetseries.owasp.org/cheatsheets/DOM_based_XSS_Prevention_Cheat_Sheet.html

- [cleanup / NOT-a-defect, logged so the next audit doesn't re-raise it]
  src/components/chip/ChipStates.tsx:25 (`ChipAnalyzing`) and :186 (`ChipRendering`) are
  exported + unit-tested but never mounted. The file documents this as an **intentional
  Issue 314 deferral** (no dedicated scoring surface without stacking two animated chips in
  the tight VideoTable cell; no honest numeric render-progress signal — fabricating a % is
  forbidden by the honesty scaffold). Deliberate and documented — leave as-is. (Last run
  flagged these as "built but unmounted"; confirmed intentional, not dead code.)

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle (EventSource/subscription cleanup) | ok — useTaskStream/useTaskResult/useStageStream all close on unmount+URL-change; chat stream bounds reconnects; activeTasks caps at 3. 1 needs-runtime-confirmation (F4 ActivityPanel double-open). |
| 2 Concurrency & scale | ok — SSE 3-slot cap enforced client-side (activeTasks.ts) matching the server; slot-exhaustion guard skips settled rows. |
| 3 Security & compliance (XSS/secrets/auth) | ok / 2 cleanup — no `dangerouslySetInnerHTML`/`innerHTML`/`eval`; brief.ts renders LLM markdown as structurally-escaped React children (OWASP textContent-over-innerHTML); no secrets in client (only `import.meta.env.BASE_URL`); `signInHref` uses `encodeURIComponent` on a fixed `/auth/login` path (no open-redirect/`javascript:`); `target=_blank` links carry `rel="noopener noreferrer"`; localStorage/sessionStorage hold only UI state + transcript cuts, never tokens. Cleanups: server-URL `<Link>` hardening (F6). |
| 4 Clip-quality | n/a (not a clip module) |
| 5 Anthropic SDK | n/a (frontend; LLM calls are server-side) |
| 6 Cleanliness & typing | ok / 1 cleanup — zero `: any`/`as any` in non-test code; every signature typed. DRY: raw-fetch duplication (F5). |
| 7 Error handling / user-facing error states / ErrorBoundary | **N findings** — no app/route error boundary (F1, SEV1); two raw-fetch spinner latches (F2, F3). |
| 8 Config & paths | ok — no client secrets; `BASE_URL` used correctly for the `/app` mount; no absolute-path concerns (browser). |

## Module verdict
NEEDS-WORK — no app-level error boundary means any render throw blanks the entire SPA with
no recovery (SEV1), compounded by two raw-`fetch` POST paths that latch their spinners on a
network error.
