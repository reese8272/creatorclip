# frontend_react (frontend/src/) — assessed 2026-06-24

Slice: the React/TS SPA. Code quality only (prototype visual fidelity owned by
another agent). Stack per package.json: **React 19** (prompt said 18 — actual is
`react ^19.2.6`), Vite 8, Tailwind v4, TanStack Query v5, React Router v7 (Data Mode).

Mechanical gates run locally:
- `npm run lint` → **0 errors, 4 warnings** (2 are stale `eslint-disable` directives in
  ActivityPanel.tsx:94 / useStageStream.ts:100; 2 are missing-dep warnings on Editor.tsx
  effects 212/222 that are intentionally keyed on `clip?.id`).
- `npx vitest run` → **194 passed / 194 (31 files)**. Green.

## Findings

- [SEV2] App.tsx:33-69 + main.tsx — **no global ErrorBoundary and no route `errorElement`
  anywhere**. The tree is `StrictMode > QueryClientProvider > RouterProvider`; no route
  defines `errorElement` and there is no class ErrorBoundary. Any render-time throw in a
  page (an unanticipated `undefined` on a narrowly-typed payload, a `.map` over a non-array,
  a bad date) unmounts the **entire** SPA to a blank white screen with no recovery but a
  manual reload. Blast radius = whole app. | fix: add a top-level `errorElement` on the root
  route rendering a `useRouteError`-based fallback ("Something went wrong — reload"), or wrap
  `<App/>` in a class ErrorBoundary; ideally also a per-AppChrome boundary so chrome survives
  a single page's crash.

- [SEV2] components/dashboard/VideoTable.tsx:59-70 — `act()` does `const resp = await fetch(...)`
  with **no try/catch**. The handler is an unawaited `async` onClick, so a network failure
  (fetch rejects) (a) throws an **uncaught promise rejection**, and (b) leaves the button
  stuck on `busy=true` / `label='Queuing…'|'Generating…'` forever with no error surface — the
  creator's Queue/Generate action silently dies on any transient blip. | fix: wrap the fetch in
  try/catch; in catch set `setBusy(false); setLabel('Retry')` (mirror the `!resp.ok` branch).
  Prefer routing through `lib/api.ts` (`api(url,{method:'POST'})`) for consistent error typing
  instead of a raw `fetch`.

- [SEV2] stores/activeTasks.ts (the cap store) vs its consumers — the module documents a
  **hard invariant**: "All EventSource opens for task-stream state MUST go through this store
  so the [server-side 3-slot] cap cannot be exceeded." In practice **only ActivityPanel.tsx
  honors it.** `Onboarding.tsx` opens TWO streams via `useTaskStream` (catalog + DNA),
  `Chat.tsx` opens one via `subscribeToChatStream`, `useStageStream` opens one per in-flight
  dashboard row, and `DnaCard.tsx` resync opens one directly — all bypassing `isCapExhausted()`.
  A creator mid-onboarding (2) who also has the activity panel running (up to 3) can request
  >3 concurrent SSE; the server `MAX_CONCURRENT_SSE_PER_CREATOR=3` then rejects the surplus, so
  the page that "loses" the race shows a dead/erroring stream rather than a graceful "waiting"
  state. Not a leak (each consumer closes on unmount), but the documented client-side guard is
  largely illusory. (needs-runtime-confirmation — exact behavior depends on the server's reject
  shape.) | fix: either (a) route every consumer's open through the store and gate on
  `isCapExhausted()` (the stated design), or (b) downgrade the comment to reflect that the
  server is the sole enforcer and the panel is only a courtesy pre-empt. Pick one; today the
  code and the docstring disagree.

- [cleanup→SEV2] pages/Editor.tsx ↔ components/review/TranscriptEditor.tsx — ~150 lines of
  cut-editing logic are duplicated **near-verbatim** across both files: `storageKey`, `loadCuts`,
  `mergeAdjacent`, `ancestorWord`, the selection→indices logic (`onMouseUp`/`selectionToIndices`),
  `apply()`, `confirmFinal()`, the cut-queue + warning rendering, and the `WARNING_REMOVED_PCT` /
  `ADJACENT_MERGE_S` constants. A `components/review/trim.ts` helper already exists as the natural
  home. DRY risk: the two will drift (they share a localStorage key, so a divergent merge/parse
  is a real correctness hazard, not just tidiness). | fix: extract the pure helpers + a
  `useClipCuts(clip)` hook into `review/trim.ts` and consume from both.

- [cleanup] pages/Pricing.tsx:19-29 — flagged `TODO` ("drive from /billing/packs API to
  eliminate DRY drift") plus a **hardcoded `PACKS` price table** that duplicates server
  `billing/packs.py`. CLAUDE.md §"No TODO" + rubric §6 DRY. A stale price here would mislead
  users at the checkout boundary. | fix: fetch `/billing/packs` (TanStack Query) and drop the
  literal; or, if intentionally static for now, replace the TODO with a DECISIONS.md pointer.

- [cleanup] components/ui/modal.tsx — backdrop modal has **no `role="dialog"` / `aria-modal`,
  no focus trap, and no Escape-to-close**. Keyboard users get no focus management. Bounded:
  only used in 2 low-frequency spots (ApiKeysSection reveal/revoke). | fix: add
  `role="dialog" aria-modal="true" aria-label={title}`, focus the panel on open, trap Tab, and
  close on `Escape`. (A headless primitive would also fix this app-wide.)

- [cleanup] components/profile/DnaCard.tsx:56-109 — bespoke `useState` + manual
  `api().then()/.catch()` data flow + a hand-managed `subRef` stream, instead of the TanStack
  Query + stream-hook pattern the rest of the app standardized on (DECISIONS 2026-06-18). It is
  correct (closes the sub on unmount), just inconsistent and re-implements load/error plumbing
  the query layer already owns. | fix: migrate the DNA read to `useQuery(['dna'])` and reuse a
  stream hook for resync.

- [cleanup] lib/taskStream.ts:84-92 vs 124-126 — `subscribeToTaskStream` registers its native
  `error` listener via `Object.keys(RENDERERS)` (key `error` exists), so a network-drop Event
  (no `.data`) hits `dispatch('error', …)` → `JSON.parse(undefined)` throws → caught →
  `data={message:'undefined'}` → surfaces the literal string **"undefined"** to the user. The
  chat variant handles this correctly ("Connection lost."). | fix: in `dispatch`, treat a
  payload-less `error` event as a connection-lost message, matching `subscribeToChatStream`.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle (EventSource/timers) | ok — every SSE consumer closes on unmount/URL-change; stale timers cleared. 1 cap-store invariant gap (SEV2 above). |
| 2 Concurrency & scale | mostly ok — TanStack `refetchInterval` poll loops stop on settle; clip counts batched (no N+1). SSE cap pre-empt not enforced app-wide (SEV2). |
| 3 Security & compliance | **clean** — no `dangerouslySetInnerHTML`/`innerHTML`/`eval`; brief renders via auto-escaped React children (lib/brief.ts, explicitly documented OWASP DOM-XSS posture). No tokens/secrets in client state or logs; telemetry sends only bounded UI labels (lib/activity.ts). No `console.*`. Honesty/no-virality copy present on every surface. |
| 4 Clip-quality | n/a (presentation layer) |
| 5 Anthropic SDK | n/a (no SDK calls client-side; streams consume server SSE) |
| 6 Cleanliness & typing | NEEDS-WORK — **zero `any`** in src (strong), but Editor/TranscriptEditor DRY (~150 lines), 1 TODO + hardcoded price table, DnaCard pattern drift. |
| 7 Error handling / API surface | n/a (no FastAPI routers here) — but UI error/empty/loading states are present on every page audited (Dashboard, Review, Insights, VideoClipsMap, Analysis, Onboarding, Login, Editor); one missing app-level boundary (SEV2). |
| 8 Config & paths | ok — `import.meta.env.BASE_URL` used correctly for the `/app/` base (Chip sprite); no hardcoded base-URL drift. |

A11y note (positive): 41 `aria-label`s, correct `role` usage (button+tabIndex+onKeyDown+aria-pressed
on timeline markers, alert/status/slider/tab/textbox/region), all `<img>` carry `alt`. Above baseline.

## Module verdict
NEEDS-WORK — no BLOCKER; data/streaming/security/a11y are genuinely strong (no `any`, no XSS,
no token leakage, tests green), but three SEV2s should land before launch: add an app-level
ErrorBoundary, guard `VideoTable.act()` against network rejection, and reconcile the
SSE-cap-store invariant (enforce it everywhere or correct the docstring).
