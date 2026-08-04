# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-08-04 (Lane **L25 Batch B** — **389, 392, 390 DONE**; **391 remains**)
**Branch:** `wave/l25-batch-b` @ **`59dd586`** — **working tree clean**, **never pushed** (no upstream)
**vs trunk:** `0 27` against `origin/main` — **0 behind, 27 ahead**. `main` and `staging` are both at `40a53d3`.
**Prod DB head:** **`0050`** · **repo head `0051_video_peaks`** (Issue 392 — applies on the next deploy)

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source of truth.

---

## CURRENT FOCUS

**Batch B is 3 of 4 and the branch is finished, green, and unpushed.** #389 (tool-route app shell),
#392 (real waveform peaks) and #390 (Timeline v2) are complete with every acceptance criterion
checked. **#391 (server-side edit document + undo/redo) is the only Batch B issue left, and it was
deliberately deferred to its own PR.**

### → NEXT ACTION

1. **Push and open the PR for 389 + 392 + 390, then merge it.**
   ```bash
   git push -u origin wave/l25-batch-b
   gh pr create --base main --title "L25 Batch B (part 1): app shell, real waveform peaks, Timeline v2"
   ```
   Wait for the 12-job CI matrix — it has **never run on this branch**, because nothing has been
   pushed. Expect the **visual-regression job to need baselines regenerated on `ubuntu-latest`**:
   #389 and #390 both changed tool-route layout substantially. Procedure is in KEY COORDINATES.
   **Merging to `main` deploys to production**, and this deploy carries migration **`0051`**
   (`videos.peaks_uri`, nullable, additive) plus the new Beat task `backfill-video-peaks-hourly`.

2. **Then build #391 as its own PR** off fresh `main`. The plan is written and approved in full detail
   at `/home/reese/.claude/plans/yes-but-ensure-a-agile-glacier.md` (**Part 2**) — it carries the
   table schema, the atomic `ON CONFLICT … WHERE revision = :base_revision` upsert, the snapshot
   command stack, the 800ms-trailing / 2s-max-wait autosave state machine, and these confirmed
   decisions:
   - the render path **READS the document** via `base_revision` (time-boxed `segments` fallback,
     deleted inside the same issue)
   - autosave takes any **structurally** valid document; the 5s-kept / 85%-removed caps stay at export
   - a stale revision is an **explicit user choice**, never an auto-merge
   - `POST /clean/confirm` must **clear** the document; `/clean/discard` must **not**
   - **Re-check `alembic heads` before writing `0052`** — head is `0051` today.

3. **Before any local gate run:** `export PATH=/home/reese/.nvm/versions/node/v22.17.1/bin:$PATH`
   and run every frontend command **from `frontend/`** (see CONSTRAINTS — both bit this session).

**Known red gate, pre-existing, NOT caused by this branch:** `pip_audit` reports **6 advisories**
(aiohttp / cryptography / pytest) published since the Batch A baseline.
`git diff main -- requirements*.txt '*.py'` is **empty**, so nothing here introduced them.
`cryptography` backs the Fernet token path, so this deserves its own issue rather than a baseline bump.
Logged in `docs/OFF_COURSE_BUGS.md`.

---

## WHAT WORKS NOW (verified on `59dd586` — do not re-investigate)

| Gate | Value |
|---|---|
| Backend pytest | **2546 passed**, 64 skipped (2516 at Batch A close) |
| Frontend vitest | **549 passed / 78 files** (409 / 64 at Batch A close) |
| Playwright | **76 passed** — desktop **and** mobile, incl. axe on **both** editor routes |
| `tsc -b` · `npm run lint` · `npm run build` | clean · **0 errors** (1 pre-existing warning in `useStageStream.ts`) · clean |
| ruff · ruff format · mypy | 0 · clean · 0 |
| coverage | **83.08%** (floor 83.00) |
| module floors | **clip_engine 92.72** (floor 91.0) · preference 90.45 · crypto/limiter/auth 100.0 |
| bandit · pip-audit | 0/0 · **FAIL 6 — pre-existing** (see CURRENT FOCUS) |
| Clip-quality eval | **53 passed** — and green **by construction**: the 390 range touches no Python (`git diff 73c3223..HEAD -- clip_engine worker routers models.py alembic ingestion tests` is empty) |
| Diff vs `main` | **84 files, +7069 / −1653**, 27 commits |
| Live prod | `GET /health` **200** — postgres/redis/storage `ok`; last deploy = PR #69 (Batch A) |

### The 27 commits, by kind

8 × `feat(390)` · 4 × `feat(392)` · 4 × `feat(389)` · 3 × `refactor(389)` · 2 × `fix(390)` ·
1 each `test(389)`, `refactor(390)`, `docs(389)`, `docs(392)`, `docs(390)`.

---

## WHAT EACH ISSUE ACTUALLY SHIPPED

### #389 — the tool routes became an application

`100dvh`, no page scroll, independently scrolling panels, one `<main>`, honesty statement docked.

- **`components/ToolChrome.tsx`** — `flex min-h-dvh flex-col lg:h-dvh lg:overflow-hidden`, sets
  `--app-bottom-inset`. **`components/layout/ToolShell.tsx`** owns the single `<main>` + status bar.
- **The split is not stylistic.** `Editor.test.tsx:103-113` renders `<Editor/>` **standalone, with no
  layout element**, and asserts the disclaimer — and an acceptance criterion required that stay green.
  So the disclaimer lives in the **page tree** (`ToolShell`), not the route layout (`ToolChrome`).
  There is a `// WHY` comment at the site. **Do not "simplify" it back into one component.**
- **`lib/toolLayout.ts`** — three player-width constants derived from `100dvh`. Read the ⚠ comment:
  **this project's root font-size is ~14.39px, NOT 16px.** A value derived at 16px/rem comes out ~10%
  short and the viewer card silently overflows, clipping the meta row below the fold. All three were
  **measured in Chromium at 1440×900**, not estimated. `EDITOR_PLAYER_W` was re-measured
  30.5rem → **31.5rem** after #390's zoom toolbar + ruler cost the region grid ~13px.
  They are **complete literal class strings on purpose** — Tailwind v4 scans `.ts` as plain text, so
  a string built by concatenation emits no CSS at all.
- **`components/LegalLinks.tsx`** — one href list consumed by both `Footer` and `ToolStatusBar`.
- **`ShortFormEditor.tsx`** extracted from `Editor.tsx` (700 → 181 lines).
- Gates: `pages/toolRoutes.shell.test.tsx` (table-driven over 9 route branches: exactly one `<main>`,
  disclaimer exactly once, one legal-link set) + `e2e/tool-shell.spec.ts` (7 geometric assertions
  incl. "chrome stays put while a panel scrolls").

### #392 — the fabricated waveform is gone

- **`ingestion/peaks.py`** — `compute_peaks(wav_path) -> dict | None`, **never raises**. BBC
  `audiowaveform` JSON format (interleaved min/max pairs, 8-bit). `DEFAULT_SAMPLES_PER_PIXEL = 512`,
  `MAX_PAIRS = 60_000`, block-reads at `1 << 20` samples so a 3-hour source never materialises ~345 MB.
- **`alembic/0051_video_peaks`** — nullable `videos.peaks_uri`. No new RLS policy (existing table).
- R2 prefix **`peaks/{creator_id}/{video_id}.json`** — creator-scoped so `DELETE /auth/me` reaches it.
- **`lib/peaks.ts`** — `peakEnvelope` uses **max-of-bucket**, not mean, and returns **`null`** (not
  zeros) with no data. **`components/editor/Waveform.tsx`** draws an honest flat line on `null`, and
  both editors say *"Waveform unavailable — the audio is past its retention window"* in words.
  `useVideoPeaks` is gated on `has_peaks`, `staleTime: Infinity`, `retry: false`.
- **`src/test/no-synthetic-waveform.test.ts`** — a structural gate asserting `getContext('2d')`
  appears in **exactly** `['/src/components/editor/Waveform.tsx']`. It is **AST-based** (TypeScript
  AST over `import.meta.glob('?raw')`) because it false-positived on *comments* twice.
  **Never weaken it to `toContain`** — that is how a fake waveform sneaks back.
- Verified against real ffmpeg audio. (False alarm caught: ffmpeg's `sine` runs at 0.125 full scale
  / −18 dBFS, so a measured peak of 16/127 was correct, not a bug.)

### #390 — the timeline became an editor

New pure modules (all in `lib/`, deliberately outside `components/editor/` so the waveform gate can't
see them): **`timelineZoom.ts`** (24 tests; `ZOOM_FACTOR=2`, `MAX_PX_PER_SECOND=4000`,
`zoomAtAnchor`, `niceTickInterval`) · **`timelineInteraction.ts`** (`EDGE_GRAB_PX=6`,
`SNAP_THRESHOLD_PX=8`, binary-searched `snapTime`) · **`editorCuts.ts`** · **`keyboard.ts`**.
Hooks: **`useTimelineViewport.ts`**, **`useEditorShortcuts.ts`**. Components:
**`TimelineRail.tsx`** (the shared surface), **`TimelineRuler.tsx`** (DOM only, `aria-hidden`),
**`MasterTimeline.tsx`** (promoted out of `LongFormEditor`).

Load-bearing facts to not re-derive:

- **`role="slider"` on the container was a live production defect.** MDN: `role="slider"` forces
  **every descendant to `presentation`** — it was swallowing the waveform's `role="img"` and every
  cut label. Now W3C APG multi-thumb: the rail is `role="group"`, each thumb its own `role="slider"`.
- **The snap threshold is in PIXELS, converted at use.** That is what makes the feel identical at
  every zoom — the acceptance criterion made structural.
- **The shortcut bus is a module singleton on the CAPTURE phase**, with a LIFO scope stack.
  Capture, not bubble, because `VideoPlayer` mounts before `Timeline` and would win ←/→ on
  registration order alone. Handlers refresh in an **effect**, never during render.
- **`onKeyDownCapture` on the ScrubBar had to be deleted.** `stopPropagation()` in the *capture*
  phase also kills the target's own bubble handler — **ScrubBar arrow keys had never worked**. I wrote
  a test expecting a *double* seek; it failed reporting *no* movement. Both layers now bail on
  `e.defaultPrevented`.
- **`mergeAdjacent` was mutating caller-owned objects** through a shallow `.slice()`, so today's
  single-level undo was *already* wrong for merged cuts. Now pure; the merged survivor keeps the
  **earlier** id. Pinned with `Object.freeze`.
- **`EditorCut` gained `id: string`**; `indices` became optional. `key={idx}` → `key={c.id}`.
- **`clientXToTime` read a stale state width.** Before any ResizeObserver fires, `fit` collapses to
  `MIN_PX_PER_SECOND` and x=10 on a 300s source returned **300s instead of 30s**. It now reads the
  **live rect**, like its siblings. Fixed *before* `MasterTimeline` adopted the hook.
- **The measured element, the pointer handlers and `railTestId` must stay the SAME element** —
  `Editor.test.tsx` spies on exactly one element's rect; no wrapper may come between them.
- **`src/test/rect.ts`** — `rect()` / `stubRect()` (per-element) / `withMeasuredRender()`.
  Natively dispatched `KeyboardEvent`s need an explicit `act()`; ruler assertions need
  `flushResizeObservers` first. `vite.config.ts` gained `restoreMocks: true`.
- Deleted **`components/review/TranscriptEditor.tsx`** — verified dead, and it carried a second copy
  of the buggy `mergeAdjacent`.

---

## THE ARC THAT LED HERE

1. **W0–W5 (previous sessions)** closed every buildable issue in the old tracker; the queue emptied.
2. **2026-08-03, tracker reset.** The 7,096-line `docs/issues.md` had stopped working as a queue.
   Archived verbatim to `docs/issues-archive-2026-08-03.md`; rebuilt around **Lane L25 — Editor &
   Craft (384–405)** in five batches, from a review of the editor/presentation layer against the 2026
   field. Finding: **the engine is beta-ready and differentiated; the surface is not.**
3. **Batch A (384–388, 400a/b)** built sequentially on `wave/l25-batch-a`, merged as **PR #69 →
   `main` (`40a53d3`)** and deployed. An npm/Homebrew toolchain outage interrupted it mid-build
   (see CONSTRAINTS).
4. **Batch B is "make it an application, not a webpage."** #389 → #392 → #390 in that order, on
   `wave/l25-batch-b`, each planned through `/issue-workflow` with live documentation lookup.
5. **#390 was reordered after #392 deliberately** — the timeline had to be rebuilt on top of *real*
   peaks, not the fabricated ones, or the rail would have been designed around fake data.
6. **#391 was scoped, researched and approved in the same planning pass, then deferred** to keep this
   PR a coherent, reviewable deliverable: the routes became an app, the waveform became real, and the
   timeline became an editor. Persistence is a distinct, riskier change — it touches the **paid**
   render path.

---

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Repo | `github.com/reese8272/creatorclip` |
| Production | `https://autoclip.studio` — VM + docker-compose + Cloudflare tunnel (**not** Render) |
| Branches | `main`, `staging` — both at `40a53d3`; plus local `wave/l25-batch-b` @ `59dd586` (unpushed) |
| Last merged PR | **#69** — L25 Batch A (2026-08-03) |
| Deploy chain | push to `main` → `docker-publish.yml` → `deploy.yml` (staging gate → prod) |
| Baseline regen | `gh workflow run ci.yml -f update_snapshots=true --ref <branch>` → `gh run download <id> -n visual-baselines-<sha>` |
| Node for local gates | `/home/reese/.nvm/versions/node/v22.17.1/bin` (**`frontend/.nvmrc` = 22**) |
| Python | `.venv/bin/python` (system `python3` lacks pydantic) |
| Redis for unit lane | `redis-server --daemonize yes --save '' --appendonly no` |
| Layer 0 | `.venv/bin/python .claude/skills/production-assessment/scripts/run_layer0.py` |
| Alembic head (repo) | **`0051_video_peaks`** · prod DB is still at **`0050`** |
| R2 prefixes added | `posters/{creator_id}/…` (387) · **`peaks/{creator_id}/…`** (392) — both creator-scoped |
| Beat tasks added | `backfill-video-posters-hourly` (387) · **`backfill-video-peaks-hourly`** (392) |
| #391 plan | `~/.claude/plans/yes-but-ensure-a-agile-glacier.md` — **Part 2** |
| Secrets | `.env` on the VM; `TOKEN_ENCRYPTION_KEY` rotation in `docs/RUNBOOKS.md`. **Names only — never values.** |

---

## CONSTRAINTS & GOTCHAS

**Process traps that actually bit this session**

- **Run every frontend command from `frontend/`.** `tsc -b` and `vitest` were launched from the repo
  root more than once and failed confusingly.
- **Run `npm run lint` BEFORE every commit.** Two commits landed with lint errors. Both were then
  fixed **at the design level**, never suppressed — a `setState`-in-effect cascade became a
  clamp-on-read, a DOM ref moved to the caller, a latest-ref write moved into an effect.
- **Backticks in `git commit -m` trigger bash command substitution.** Use a heredoc.
- **CI runs `ruff format --check`, which the local Layer-0 script does not.** Run it before pushing.
- **Use Node 22.** System Node is 26.5.1, under which **jsdom stops exposing a global `localStorage`**
  and `Walkthrough.test.tsx` fails — pre-existing, passes on 22, and 22 is what CI's three
  `node-version` jobs pin.
- **Homebrew npm 11.5.2 on Node 24 could not install anything** — exited 1 with an *empty* error and
  left a half-extracted `node_modules`. Fixed by `brew reinstall node`. **Never try
  `npm install -g npm@…` as the repair; it destroys the brew npm.** Full diagnosis + every ruled-out
  hypothesis: `~/.claude/ISSUES_LOG.md` **ISSUE-2026-08-03-01**.
- **Don't slice source by fixed character offsets in tests.** `test_issue_110` sliced 6000 chars of
  `_ingest_async`; the peaks block pushed its target past the window. Use `inspect.getsource`.

**Environment**

- **Merging to `main` deploys to production.** No confirmation step beyond the staging gate.
- **Integration tests cannot run on this box** (no Docker) — CI-verified only. Say so plainly in
  close-outs rather than claiming they passed.
- **The unit lane needs a live Redis** (the rate limiter has no in-memory fallback).
- **Visual baselines MUST come from `ubuntu-latest`**, never locally (WSL2 anti-aliasing).
  `--update-snapshots` only rewrites snapshots that **FAIL** the `maxDiffPixelRatio: 0.01` comparison,
  so an unchanged PNG in the artifact is expected, not a bug. **A failed visual run uploads no diff
  artifact** — you cannot see what changed from the run alone; regenerate and diff the artifacts.

**Code invariants**

- **Five structural gates** now live in `frontend/src/test/` (`sourceScan.ts` + the waveform gate):
  no glyph icons, no native form controls, no `<video controls>`, no undeclared colour token, no
  synthetic waveform. They read the **source tree**, so a violation fails `npm test`, not just review.
  They use `import.meta.glob('?raw')` + the **TypeScript AST** deliberately — `node:fs` fails `tsc -b`
  (no `@types/node` in `tsconfig.app.json`) and a regex false-positives on comments and on ~2,600
  box-drawing characters in comment banners.
- **The honesty constraint is load-bearing and structurally tested.** `HONESTY_STATEMENT` in
  `ToolStatusBar.tsx` is the single definition; "predicts fit, does not promise virality" and the
  estimate-not-guarantee wording must stay verbatim on every tool surface.
- **User-visible copy is pinned by tests.** Shortening `Clip #1` → `#1` and `Back to Review` →
  `Review` during the 389 refactor broke 8 tests, correctly. Don't "tidy" label text.
- **Radix Select THROWS on `value=""`.** `components/ui/select.tsx` owns a `__none__` sentinel and
  translates both ways so call sites keep passing `''`. Do not "simplify" it away.
- **`--shadow-inset` never composed with `--shadow-sm`** (same Tailwind namespace, both write
  `--tw-shadow`). It is `--inset-shadow-highlight` in v4's inset namespace now.
- **axe's caption rule lands in `results.incomplete`, not `violations`** for a `<track>`-less video —
  measured, so a caption-less `VideoPlayer` does not trip the a11y gate.
- **axe `scrollable-region-focusable` is SERIOUS impact** (WCAG 2.1.1 / 2.1.3). The rail is
  `overflow-hidden` with self-managed `scrollLeft` specifically so the rule never applies.
- **In Postgres, use the hardened GUC form** — `NULLIF(current_setting('app.creator_id', true), '')::uuid`.
  A bare `::uuid` cast raises SQLSTATE 22P02 on a reused pooled connection (a 500 instead of a clean
  deny). `0045` fixed this; **`0048` and `0049` still use the bare form** — logged, and #391's `0052`
  must not copy them. `0049` also emits no `GRANT`.
- **`docs/UI.md` is the design SoT; `frontend/src/index.css` is the implementation SoT.** When they
  disagree, fix the mismatch — don't fork.
- **In a height-constrained flex column, stacking meta *under* a 9:16 player makes the player
  SMALLER**, because pixels beside the media come out of its height. This invalidated a planning
  assumption; the meta became one compact row and the reasoning moved to column A.
- **`min-h-0` is needed at every level** of a flex chain (flexbox auto-minimum-size);
  `grid-rows-[minmax(0,1fr)]` is the grid analogue.
- **React's `onWheel` is passive** — `preventDefault()` silently no-ops (facebook/react#14856).
  Use `addEventListener(…, {passive:false})`. Trackpad pinch arrives as `wheel` with `ctrlKey: true`.
- **jsdom stubs `setPointerCapture` as a no-op** and `hasPointerCapture` returns `false`. The drag
  state machine is a ref and never consults it; capture is a real-browser optimisation only.
- **e2e fixtures must span the whole source.** The peaks fixture initially covered 20.5s of a 1320s
  video, so the short-form timeline read as pure silence.

---

## OPEN, LOGGED, NOT FIXED

Canonical list is `docs/OFF_COURSE_BUGS.md`. The ones most likely to matter next:

1. **`pip_audit` FAIL 6** (aiohttp / cryptography / pytest) — pre-existing, but `cryptography` backs
   the Fernet token path. **Deserves its own issue**, not a baseline bump.
2. **`0048` / `0049` use the bare `::uuid` GUC cast** — the exact form `0045` replaced. A reused
   pooled connection can 500 instead of cleanly denying. `0049` also grants nothing.
3. **`/settings` has pre-existing serious contrast failures** (2.14–2.54:1) from the 2026-06-23 "Soon"
   preview rows — decorative disabled mocks under `pointer-events-none opacity-50`, which halves
   contrast while leaving fake controls in the accessibility tree. **This is why `settings` is the one
   dense route still outside the axe gate.** Likely a one-attribute fix (`aria-hidden` on the mock).
4. **Roving tabindex on the timeline is a named, accepted follow-up.** N cuts currently produce
   **1 + 2N** tab stops. A stated cost, not an oversight.
5. **#387's ffmpeg poster chain is asserted against mocked `_run` only.** Needs one pass over a
   genuinely awkward real file (VFR screen recording, `.mkv` with a broken index, source shorter than
   the seek offset) during the next staging soak.
6. **`clips/` is not creator-scoped**, so `DELETE /auth/me`'s `clips/{creator_id}/` prefix purge
   matches nothing — a live right-to-erasure gap. `posters/` and `peaks/` deliberately do not
   replicate the pattern.
7. **Review-queue badge counts rendered clips, not shortlisted-unreviewed ones** (needs a backend field).
8. **`App.tsx`'s `*` route silently redirects typos to `/dashboard`** instead of 404ing.
9. **`ORIGINALITY_SIMILARITY_THRESHOLD=0.92` is UNVALIDATED** — a silent advisory means "unmeasured",
   not "clean".

**Operator punch-list** (no code can close these — `docs/GO_LIVE.md` is canonical): **#29** Google
OAuth verification · **#26/#28** friend beta · **#282** uptime monitor (still open, beta-critical) ·
**#255** off-box key escrow · **`MAILING_ADDRESS` is deliberately unset** — `config.py` skips all
lifecycle email while empty, because CAN-SPAM requires a real postal address. **Do not "fix" this in
code**; whatever is set becomes public in every footer.

---

## POINTERS

| Doc | Purpose |
|---|---|
| `docs/issues.md` | Work queue — **Batch A ✅ · Batch B: 389 ✅ 392 ✅ 390 ✅, 391 open** |
| `docs/PROJECT_STATE.md` | Progress log — top entry is the Batch B (part 1) close-out |
| `docs/DECISIONS.md` | Top entries are 390 / 392 / 389 |
| `docs/UI.md` | Design system — **new "App shell" + "Timelines" sections** |
| `docs/SOT.md` | Stack, schema, structure — shell layer, `peaks_uri`, timeline modules documented |
| `docs/COMPLIANCE.md` | ToS/retention — **new peaks row** (audio derivative, purge-honoured) |
| `docs/OFF_COURSE_BUGS.md` | Logged-not-fixed defects |
| `docs/GO_LIVE.md` | Canonical launch scorecard |
| `docs/BRANCHING.md` | Promotion model |
| `docs/CLIPPING_PRINCIPLES.md` | Named principles the engine cites |
| `~/.claude/ISSUES_LOG.md` | Cross-project incident log — **ISSUE-2026-08-03-01** is the npm outage |
| `~/.claude/plans/yes-but-ensure-a-agile-glacier.md` | **Part 2 = the approved #391 plan** |
| Memory dir | `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` |
