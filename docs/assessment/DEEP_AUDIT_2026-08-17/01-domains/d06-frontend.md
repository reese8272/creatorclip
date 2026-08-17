# D06 — Frontend architecture & the type contract

**Domain owner (this pass):** deep-audit domain researcher · **Date:** 2026-08-17
**Scope:** `frontend/` (~168 source files excl. tests, ~35k TS), the FastAPI↔SPA contract,
the frontend gate posture, bundle/perf, accessibility, state & routing.
**Method:** read-only. I generated the app's real OpenAPI document
(`.venv/bin/python -c "from main import app; app.openapi()"` → 104 paths / 143 schemas) and
diffed all 80 interfaces in `frontend/src/types.ts` against it mechanically. Numbers below are
measured, not estimated.

---

## Verdict

The React app itself is in better shape than its reputation in `architecture-map.md` — the `lib/`
layer is genuinely pure-and-tested, the timeline ARIA is real and documented, and the
state/routing pairing is exactly what 2026 practice recommends. The problem is not the code, it
is that **every mechanism that could catch a frontend↔backend contract break is either
hand-written from the same source of truth it is supposed to check, or is not a merge gate.**
`types.ts` → test fixtures → green CI is a closed loop that cannot fail for the reason it exists;
that is the project's own named #1 failure mode, in the one domain where it has never been
catalogued. Measured drift is 25 backend response fields no UI reads, including two shipped
creator-transparency features that are inert.

---

## What the current standard is, with sources

**1. Type contract.** In 2026 the settled answer for a FastAPI backend is: *generate, don't
transcribe.* Three live options, and they are not interchangeable:

| Tool | What it is | Fit here |
|---|---|---|
| `openapi-typescript` | Types only, zero runtime deps, "the gold standard for type generation" | **Best fit.** Pairs with `openapi-fetch` (6 kB, middleware/auth support), which is a drop-in for the ~40-line `lib/api.ts` |
| `@hey-api/openapi-ts` | Current frontrunner for a full SDK; plugin architecture; TanStack Query + Zod plugins | Good, but generates an SDK layer this app does not need — it already has one |
| `orval` | Batteries-included: hooks, mocks, schemas | Over-generates for a co-owned monorepo with one client |

Runtime validation at the boundary is **still recommended in 2026 even with codegen** — codegen
proves the *spec* and the *client* agree, not that the *server* obeys the spec. Both Hey API and
the Zod ecosystem ship generators for exactly this. ([saschb2b, 2026-02-23](https://saschb2b.com/blog/typesafe-api-codegen-2026);
[Hey API Zod plugin](https://heyapi.dev/openapi-ts/plugins/zod);
[Kubb comparison](https://kubb.dev/docs/5.x/guide/comparison);
[dev.to codegen comparison](https://dev.to/nyaomaru/which-openapi-codegen-should-you-choose-openapi-typescript-vs-hey-api-vs-orval-vs-kubb-100p);
[openapi-fetch docs](https://openapi-ts.dev/openapi-fetch/))

**2. Coverage.** The industry moved off coverage-as-target to the **Testing Trophy**: static
analysis first, integration tests as the biggest investment, coverage as a *map* rather than a
gate. Vitest supports `thresholds` (including per-file) and will fail CI, but current guidance is
"begin from measured reality, protect new code" — not a headline number.
([Vitest coverage config](https://vitest.dev/config/coverage);
[Frontend Testing in 2026](https://www.atinatechnology.in/frontend-testing-in-2026/))

**3. Performance budget.** 2026 working budget for initial JS is **<200 KB gzipped** (aggressive)
or **<150 KB gz** for mobile-first, with JS payload named "the single largest performance
predictor" for INP.
([Web Perf Clinic](https://webperfclinic.com/article/javascript-bundle-optimization-complete-guide-shipping-less-code);
[Core Web Vitals 2026](https://www.techcognate.com/core-web-vitals-guide/))

**4. Accessibility.** The bar is **WCAG 2.2 AA**, not 2.1. EN 301 549 v4.1.1 — the technical
standard behind European Accessibility Act compliance — aligns to WCAG 2.2. The 2.2-specific
criteria that bite an editor UI are **2.5.7 Dragging Movements**, **2.5.8 Target Size (Minimum,
24×24 CSS px)** and **2.4.11 Focus Not Obscured**. axe-core's `target-size` rule is tagged
`wcag22aa` and is **off by default** — you must opt in.
([Level Access WCAG 2.2 checklist](https://www.levelaccess.com/blog/wcag-2-2-aa-summary-and-checklist-for-website-owners/);
[Level Access EAA guide](https://www.levelaccess.com/compliance-overview/european-accessibility-act-eaa/);
[Deque axe-core 4.5 / WCAG 2.2](https://www.deque.com/blog/axe-core-4-5-first-wcag-2-2-support-and-more/);
[axe-core rule descriptions](https://github.com/dequelabs/axe-core/blob/develop/doc/rule-descriptions.md))

**5. Routing + state.** Current consensus: do **not** duplicate caching between router loaders
and a query cache. Using React Router purely for routing/layouts/error boundaries with TanStack
Query as the single server-state cache is a recommended arrangement, and TanStack Router's
advantages (typed routes, built-in SWR) do not justify a migration when Query already owns the
cache. ([tkdodo, TanStack Router and Query](https://tkdodo.eu/blog/tan-stack-router-and-query);
[react-router discussion #14037](https://github.com/remix-run/react-router/discussions/14037);
[devtoolbox comparison 2026](https://devtoolbox.blog/tanstack-router-vs-react-router-v7-2026/))

---

## Findings

### F1 — The type contract is a closed loop, and it makes a required CI check vacuous — HIGH

**Evidence.**
- `frontend/src/types.ts:1-814` — 80 hand-written interfaces mirroring Pydantic response models.
- `frontend/src/lib/api.ts:82` — `return (await resp.json()) as T`. `T` is asserted, never checked.
- `frontend/e2e/fixtures/mock-api.ts:11-29` — the Playwright fixtures import `CurrentUser`,
  `Balance`, `ReviewClipListResponse`, `CropTrack`, … **from `../../src/types`**, and the file's
  own header says *"reply with fixtures shaped to frontend/src/types.ts."*
- No test anywhere compares `types.ts` to the OpenAPI document. `grep -rn "app.openapi()" tests/`
  returns 3 files (`test_fingerprint.py`, `test_compliance_no_virality.py` ×2) — none of them
  touch the frontend.

**Measured contract state** (mechanical diff, 80 TS interfaces vs 143 OpenAPI schemas):

| | Count |
|---|---|
| Field-exact against a Pydantic response model | **48** (60%) |
| Matched but drifted — API fields absent from TS | **15 interfaces, 25 fields** |
| No confident backend counterpart at all | **17** |
| TS fields the API does **not** send | **0** |

That last row matters and I want to be honest about it: **the drift today is entirely
one-directional.** Nothing in `types.ts` currently promises a field the server does not send, so
there is no live `undefined`-render bug from the modelled types. The risk is latent, not realized.

**Failure scenario.** Rename `ClipOut.render_uri` → `ClipOut.output_uri` in `routers/clips.py`
(a 1-line change; `clips.py` has 67 commits).
- `mypy` passes — the backend is self-consistent.
- `tsc -b` passes — `types.ts` was not edited, so nothing in the SPA is type-wrong.
- `vitest` passes — its fixtures are typed by `types.ts` and still supply `render_uri`.
- **`Playwright (smoke + a11y)` — a REQUIRED check — passes**, because `mock-api.ts` serves the
  same `types.ts`-shaped fixture at the network boundary.
- All 8 required checks green → merge → `docker-publish` → `deploy.yml` → production. Every clip
  player renders `<video src={undefined}>`; the staging gate's `llm_harness --flow core` does not
  drive a browser, so it does not catch it either.

This is the shape `AUDIT_BRIEF.md:177-203` names as the house failure mode ("an intermediate
layer reports success without exercising the thing it claims to verify"), and the frontend
contract is not on that list. **Candidate instance #5.**

**Recommendation (not a survey — one answer).** `openapi-typescript` + `openapi-fetch`.
Rationale specific to this repo:
- **98 of 119 endpoints already declare a `response_model`** — the generation input is basically
  finished. This is not a migration, it is turning on a tap.
- `lib/api.ts` is already a thin `fetch` wrapper with exactly two behaviours worth keeping
  (`credentials:'include'`, redirect-on-401). `openapi-fetch` is 6 kB and supports both as
  middleware, so the diff is ~40 lines replaced, not an SDK adoption.
- Do **not** adopt hey-api or orval: both generate an SDK/hooks layer that duplicates the 89
  existing `api<T>()` call sites and the TanStack Query wrappers already written.
- Runtime validation: **do not put Zod on all 89 call sites.** Put it on the ~5 endpoints where
  the backend has no schema at all (F2 below) — which is exactly where `isCropTrack` and
  `isWaveformPeaks` already are. Codegen covers the other 84 because the server *does* validate
  there (Pydantic response_model coerces on the way out).
- Cost estimate: one `scripts/gen_openapi.py` (I ran the equivalent in ~4 s), one npm script, one
  CI step that regenerates and `git diff --exit-code`s. That last step is the whole point — it
  turns "the two sides agree" into a gate instead of a habit.

**Verdict:** gap. Bet #13 in `architecture-map.md` has zero recorded decision and
`grep -i "openapi\|codegen\|zod" docs/DECISIONS.md` confirms it: 2 hits for "openapi", both
incidental (a `ForwardRef` crash at `:1883`, a union-shape note at `:6452`); 0 for codegen/zod.

---

### F2 — 25 backend fields no UI reads, including two shipped transparency features and one paid-for architectural deviation — MEDIUM

The drift is not abstract. Four items with concrete consequences:

**(a) `VideoTranscriptOut.degraded` — `routers/videos.py:1411-1414`** (added by Issue 481, one of
the 2026-08-12 clipping-integrity batch). Comment: *"additive degradation flag from the
normalizer (e.g. `no_utterances` when Deepgram's one-segment fallback ran)."*
`grep -rn degraded frontend/src` → **1 hit, in an unrelated comment.**
*Failure:* a video whose transcription fell back to Deepgram's single-utterance output renders in
`FullTranscriptPanel.tsx` as an ordinary transcript — one giant segment — with no indication it is
degraded. The creator then trims against a transcript the system already knows is bad. This is the
Class-5 honesty inversion the project explicitly guards against, and the backend already computed
the honest answer.

**(b) `ClipListOut.truncated` — `routers/clips.py:183-195, 821-832`** (Issue 339, `_LIST_LIMIT =
100`, queried as `limit(101)` specifically so the flag is trustworthy).
`frontend/src/types.ts:446-458` (`ReviewClipListResponse`) does not declare it.
*Failure:* with append-mode regeneration ("Generate more clips", Issue 431, 12/pass), a video past
100 clips silently loses the tail from the review queue. `PublishPanel.tsx:266` *does* surface
`truncated` for publications — so the pattern is known and was simply not applied here.

**(c) `NextActionOut` on `VideoListOut` / `ClipListOut` / `SavedInsightsOut`.** This is the
sharpest one. `docs/DECISIONS.md:5493` (2026-06-08) deliberately deviates from Google AIP-158 and
strict REST to adopt a **BFF envelope** — `{state, message, next_action{label, action_type, url}}`
— justified by *"the 2026-06-08 `/assess` report flagged 'barren' empty states as the
highest-leverage SEV2 cluster."*
In the React SPA: `message` is consumed (`VideoPickerLanding.tsx:273`, `DnaCard.tsx:77`,
`FullTranscriptPanel.tsx:52`); `state` is **modelled but never branched on** (`types.ts:84`,
`types.ts:135`, zero non-test reads); `next_action` is **not modelled at all**.
*Failure:* the API-design deviation was paid for (a documented departure from the REST standard,
plus `routers/_envelopes.py`) and two thirds of it was never collected. Empty states in the SPA
are hand-written client-side — the exact duplication 5493 existed to remove.

**(d) `ClipOut.has_crop_track`** unmodelled → `frontend/src/hooks/useCropTrack.ts:20` fetches
`/clips/{id}/crop-track` for every clip and swallows the 404. Minor, but it is one wasted
round-trip + one 404 log line per clip per review session, on an endpoint the backend added a flag
specifically to avoid.

**Verdict:** deviation-unjustified. These are not "TS is behind"; each is a backend feature that
shipped, is serialized on every response, and reaches no human.

---

### F3 — Six AST-based structural gates and the entire 92-file vitest suite are advisory; one of them guards a bug that already shipped to production — HIGH

**Evidence.** `.github/workflows/ci.yml:437-457` defines `Frontend (lint, test, build)`. It is not
in the 8 required contexts (`docs/BRANCHING.md:100-127`).
`frontend/src/test/` contains `no-glyph-icons`, `no-native-form-controls`,
`no-native-video-controls`, `no-local-cut-storage`, `no-synthetic-waveform`,
`design-tokens.contract` — all built on `sourceScan.ts`, which walks the **TypeScript AST**
(not regex) with a written rationale for why regex fails (~2,600 box-drawing chars in comment
banners produce false positives).
`no-synthetic-waveform.test.ts` exists because of `docs/OFF_COURSE_BUGS.md:40` —
`LongFormEditor.tsx:131-138` drew a **fabricated** waveform (`20 + ((i*37) % 60)%`) in production
under the label "Source timeline", over 22-minute sources.

**Important correction to the "the frontend is ungated" narrative.** TypeScript *is* gated:
`Docker build (smoke test)` **is** required, and `Dockerfile:66-71` runs `npm run build`, which is
`tsc -b && vite build`. A TS type error blocks merge. So the split is precise:
**compile-time types block merge; runtime behaviour and every design-system rule do not.**

**Failure scenario.** A PR reintroduces a synthesized waveform (or a raw `✓` glyph, or a colour
utility naming a token `index.css` does not declare). `no-synthetic-waveform.test.ts` goes red in
the advisory `Frontend` job. The 8 required contexts are green. GitHub enables Merge. Merge to
`main` triggers `docker-publish.yml` → `deploy.yml` → autoclip.studio. The gate written
*specifically to make a shipped honesty bug impossible* cannot block the merge that re-ships it.

Same mechanism costs the a11y story its teeth: `TimelineKeys.test.tsx` — the only assertion that
arrow keys actually move a trim handle — lives in this job. axe cannot test that; only vitest can;
vitest does not gate.

**Recommendation.** Add `"Frontend (lint, test, build)"` to the required contexts. One `gh api`
call, using the block already written verbatim in `docs/BRANCHING.md:104-127`. The job already
runs on every PR. This is the single highest value-per-minute change in this domain.

**Judgement call:** none. This one is not close.

---

### F4 — No frontend coverage measurement at all; the untested set is concentrated in the hooks that own persistence — MEDIUM

**Evidence.** `frontend/vite.config.ts` `test:` block has no `coverage` key;
`@vitest/coverage-v8` is absent from `package.json` devDependencies. 92 test files / 168 source
files. Colocation gaps: **14 of 15 `src/hooks/*.ts`** have no colocated test — including
`useEditDocument.ts` (the autosave + revision-CAS path), `useUploader.ts` (Uppy multipart to R2),
`useCropTrack.ts`, `useClipRender.ts`, `useApplyClipMetadata.ts`. **11 of ~22 `src/lib/*.ts`** —
including `keyboard.ts`, which is the keyboard bus Issue 390 introduced.

**Failure scenario.** `useEditDocument`'s conflict path (server returns 409 on a stale `revision`
→ refetch → reapply) is exercised by no test. A regression there silently discards a creator's
trim edits while `SaveStatus` reports saved — the identical shape to `OFF_COURSE_BUGS.md:22`
(`YourCall.tsx:124` rendered `'Error — try again'` in success green and discarded the rating).
Some of this is covered indirectly by `Editor.test.tsx` / `editor-persistence.spec.ts`; without
measurement, nobody can say which.

**Recommendation — and this is a judgement call I want to state plainly: do NOT add a frontend
coverage floor.** Add `@vitest/coverage-v8` and a `coverage.reporter: ['text','lcov']` block with
`thresholds` **unset**. Run it once. Use it as a map to decide which 3 hooks get tests.
Reasons for refusing the floor:
- The backend already demonstrated the failure mode: Issue 479 — per-module and diff-cover floors
  **silently no-op'd from 2026-06-23 to 2026-08-12** while printing "All runnable gates passed."
  Adding a second number-shaped gate to a project whose #1 failure class is vacuous green signal
  is adding a new place for the same bug.
- The project's own `CLAUDE.md` testing philosophy is 80/20 and explicitly anti-over-testing.
- 2026 practice has moved to the Testing Trophy: static analysis (already gated via Docker), then
  integration tests. A percentage is not the lever here — **F3 is.** Making the existing 92 files
  actually block a merge is worth more than measuring the ones that do not exist.

**Verdict:** gap on measurement; **over-engineered** if answered with a floor.

---

### F5 — Bundle: one 887 KB chunk, no code-splitting, no budget. The risk is smaller than it looks; the missing decision is real — LOW

**Measured** (`frontend/dist/`, built 2026-08-15):

| Asset | Raw | gzip | brotli |
|---|---|---|---|
| `assets/index-DJ9gBY7A.js` | 887,823 B | **261,004 B** | 215,633 B |
| `assets/index-Bbw2H-EN.css` | 58,847 B | 11,255 B | — |

`dist/index.html` loads **one** `<script type="module">`. Zero `React.lazy`, zero `Suspense`
boundaries, zero `manualChunks` in `vite.config.ts`.

**Why the risk is over-stated, honestly.** `App.tsx:66-73` documents that `/` is served by FastAPI
as a **server-rendered `static/landing.html`**, not the SPA — so anonymous marketing traffic never
downloads the bundle. The SPA is a repeat-visit authenticated tool behind Cloudflare with
content-hashed assets; after the first load it is a 304. At ≤100 desktop creators, 261 KB gz once
per deploy is not a business problem.

**Why it is not zero.** 261 KB gz is above the 2026 working budget (150–200 KB gz), and
`/app/login` — the very first authenticated hop — pays all of it to render a Google button. The
`Waveform.tsx` canvas is fine (DPR-aware, redraw-on-effect, no per-frame RAF); Uppy, Radix and
`typescript` (pulled in by `sourceScan.ts`, dev-only) are the weight.

**Recommendation.** This is a **"you do not need this at 100 users"** finding. Do not start a
code-splitting programme. Write one DECISIONS entry: *single chunk; budget 300 KB gz; revisit if
mobile becomes a real surface or the bundle crosses the budget.* React Router v7 Data Mode makes
route-level `lazy` a per-route 3-line change if it ever matters, so deferring costs nothing.

**Verdict:** aligned-with-caveat. The code is fine; the vacuum is the finding.

---

### F6 — Accessibility: the implementation is ahead of the recorded decision, but the gate is one WCAG version behind and has never run the rule that matters — MEDIUM

**Credit first, because `architecture-map.md` D3.20 understates what exists.** The timeline is
genuinely keyboard-operable:
- `frontend/src/components/editor/Timeline.tsx:250-256` — each cut edge is
  `role="slider" tabIndex={0}` with `aria-label`, `aria-valuemin/max/now` and a human
  `aria-valuetext`.
- `TimelineRail.tsx:239-245` — same for the scrubber; and `TimelineRail.tsx:70-71` documents *why*
  the container is `role="group"` and not `role="slider"` (**"`role="slider"` forces every
  descendant to `presentation` (MDN)"**) — a correct, sourced, non-obvious call.
- 17 `aria-live` / `role="status"` regions, incl. `SaveStatus.tsx:84` with a written rationale for
  polite over assertive.
- `e2e/a11y.spec.ts` covers 12 routes including both editor modes, with comments recording exactly
  why each was added.

**The gap.** `e2e/a11y.spec.ts:44`:
```ts
.withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
```
`wcag22aa` is absent. WCAG 2.2 has been a W3C Recommendation since October 2023 and EN 301 549
v4.1.1 (the EAA technical standard) aligns to it. axe-core's `target-size` rule is tagged
`wcag22aa` and is **off by default** — so it has never executed against this app, on any route,
ever.

**Concrete failure.** `Timeline.tsx:263` styles each cut edge `'absolute inset-y-0 w-[3px]
cursor-ew-resize bg-danger'` — a **3 px wide** target. SC 2.5.8 requires 24×24 CSS px, or the
spacing exception (a 24 px circle centred on the target must not intersect another target's
circle). Issue 134 removes filler words and 150 ms silences, so a cut can be a fraction of a
second wide; at default zoom its `start` and `end` handles sit a few pixels apart, failing both
the size test **and** the spacing exception. On a touch device or for a creator with a motor
impairment, neither edge is reliably grabbable. Nothing reports this, because the rule that
reports it is not enabled.

Two second-order notes: the gate filters to `serious || critical` (`a11y.spec.ts:50-52`), so
moderate-impact violations are invisible; and a route-level axe scan cannot verify that arrow keys
*do* move the handle — that lives in `TimelineKeys.test.tsx`, in the non-required job (F3).

**Recommendation.** Add `'wcag22aa'` to `withTags`, run it, triage. Then take a position: if you
conclude 2.5.8's **Essential** exception applies to frame-accurate timeline handles, that is a
legitimate and defensible answer — write it in `DECISIONS.md`. `grep -i "wcag 2.2\|target size"
docs/DECISIONS.md` returns 1 incidental hit and 0. Right now there is no answer at all, which is
the actual finding.

---

### F7 — State management and routing are correct; do not touch them — ALIGNED

`App.tsx` uses `createBrowserRouter` with **zero `loader`s and zero `action`s**. React Router v7
Data Mode is used only for nested layout contexts and the route-level `errorElement` (`RootError`,
Issue 346). TanStack Query v5 owns 100% of server state (one `QueryClient`, `staleTime: 30s`,
`retry: 1`, `refetchOnWindowFocus: false`, with the rationale in-file). Client state is one
ES-module singleton (`stores/activeTasks.ts`); Zustand was explicitly rejected
(`docs/DECISIONS.md:11500`, Issue 211).

That is exactly the arrangement 2026 guidance recommends — the failure mode being warned against
is duplicating a cache between router loaders and Query, and this app does not have that failure
mode because it uses no loaders. TanStack Router's advantages (typed routes, built-in SWR) do not
pay for a migration when Query already owns the cache and the router is doing three things well.

**No action. This is a settled, correct decision and it should be recorded as one** — `grep -i
"react-router" docs/DECISIONS.md` returns **0**, so the correct answer is currently undocumented
and therefore re-litigable.

---

## What is genuinely right here

1. **`src/test/sourceScan.ts` + the six structural gates.** AST-walking rather than regex, with a
   written explanation of why regex fails on this specific tree (~2,600 box-drawing characters in
   comment banners; `https://` inside string literals). Encoding a design rule as a build failure
   is unusual and effective. It is undermined only by F3, not by its own construction.
2. **`isCropTrack` (`lib/cropTrack.ts:39`) and `isWaveformPeaks` (`lib/peaks.ts:41`).** These are
   the only two runtime type guards in the app — and they sit at exactly the two endpoints where
   the backend declares no schema (`GET /clips/{id}/crop-track` is `response_model=None, -> dict`
   at `routers/clips.py:2132-2139`; `GET /videos/{id}/peaks` likewise). The instinct was correct
   and precisely targeted; it was simply never generalized into a policy.
3. **`lib/` is genuinely pure-function-first.** `timelineZoom`, `timelineInteraction`,
   `editorCuts`, `editCommands`, `saveScheduler`, `peaks`, `cropTrack`, `fit`, `safeUrl` each have
   a colocated `.test.ts`, and the components are thin over them. The hard math is the tested part.
   That is the right allocation.
4. **`sampleCropX` (`lib/cropTrack.ts:16-35`) reimplements the render's geometry contract with the
   snap-at-cuts rule stated in a comment** ("lerping across it would show the crop window gliding
   through a hard cut that the render performs instantly"). Cross-language duplication done with
   the contract written down.
5. **98 of 119 endpoints already declare a `response_model`.** The precondition for codegen is
   substantially satisfied; F1's fix is much cheaper than it would be in a typical repo.
6. **`lib/api.ts`'s `ApiError`** handles FastAPI's dual `detail` shape (string | `{code,message}`)
   and records the `"[object Object]"` incident that motivated it. Small, correct, documented.

---

## Decisions this domain needs but does not have

Every one of these returns **zero** relevant hits from `docs/DECISIONS.md`:

1. **The frontend↔backend type contract.** Hand-written vs generated; where (if anywhere) runtime
   validation belongs. Bet #13 with no entry. → recommend: `openapi-typescript` + `openapi-fetch`,
   Zod only at the ~5 schemaless endpoints, and a CI `git diff --exit-code` on the generated file.
2. **Which frontend checks gate a merge.** There is no recorded position that the vitest suite and
   the structural gates are *deliberately* advisory — it reads as an omission from the 2026-08-15
   branch-protection setup, not a choice.
3. **Whether frontend coverage is measured, and whether it is ever a floor.** Recommend: measured,
   never a floor. Write the "never a floor" part down, with the Issue-479 precedent as the reason.
4. **Frontend performance budget** (`architecture-map.md` D4.19 already names this gap). Recommend:
   single chunk, 300 KB gz budget, revisit trigger stated.
5. **WCAG target version.** Issue 165 decided *contrast*; nothing decided *2.1 vs 2.2*. The gate
   silently encodes 2.1 in a `withTags` array.
6. **Keyboard operation and target size for the timeline/editor.** The implementation is good and
   undocumented; SC 2.5.8 on 3 px handles is unevaluated. Either fix or claim the Essential
   exception — but claim it in writing.
7. **Routing/state posture** (React Router v7 as router-only + TanStack Query as the single cache).
   Correct, undocumented, therefore re-litigable by the next session.
8. **What `types.ts` is for after codegen** — hand-written types would still be needed for the
   LLM-shaped payloads (`TitleSuggestionsOut.titles` and `hook_rewrites`,
   `CaptionHooksOut.options`, `PerformerOut.performance_score_components` are all
   `list[dict[str, Any]]`/free-form on the backend). A one-line rule — "generated types for
   schema'd endpoints; hand-written + a runtime guard for the LLM payloads" — closes it.

---

## Cross-domain handoffs

- **To the vacuous-green-signal sweep (Phase 2):** F1 is a candidate **instance #5** — a *required*
  check (`Playwright (smoke + a11y)`) that validates the frontend against fixtures typed by the
  frontend's own belief about the API, and therefore cannot fail for a backend contract break.
- **To the process/CI domain:** F3 — `Frontend (lint, test, build)` should join the required
  contexts; the `gh api` block to do it is already written at `docs/BRANCHING.md:104-127`.
- **To the honesty/UX domain:** F2(a) `degraded` and F2(b) `truncated` are Class-5 shapes where the
  backend computed the honest answer and the UI never asked for it.
