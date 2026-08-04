# LEFT_OFF.md — CreatorClip Session Handoff

**Last updated:** 2026-08-04 · **Branch:** `main` @ **`7b8f281`** · **working tree clean**
**Trunk:** `main` and `staging` both at `7b8f281`. No open PRs, no feature branches.
**Prod:** `https://autoclip.studio` `GET /health` → **200**, postgres/redis/storage `ok`.
Prod DB head **`0052_clip_edit_documents`**, in sync with the repo.

> Source-of-truth docs live in `docs/`. This file orients and points to them — it is NOT a source of truth.

---

## CURRENT FOCUS

**Lane L25 Batch B is COMPLETE and deployed. There is no work in flight.** The next goal is to start
**Batch C — close the capability gap (393–397, 401)**.

### → NEXT ACTION

1. **Start Issue 393 — client-side cut preview.** It is the natural first pick: it is *directly*
   unblocked by 391, because a server-authoritative edit document is exactly what lets preview be
   the fast path while the server keeps the truth used at export. Read `docs/issues.md` § Batch C.
   ```bash
   git checkout main && git pull --ff-only
   git checkout -b feat/393-cut-preview
   ```
2. **Plan it through `/issue-workflow`** — Phase 1 research with LIVE documentation lookup, not
   memory. That process caught three plan-invalidating facts during 391; it earns its cost.
3. **Before any local gate run:**
   ```bash
   export PATH=/home/reese/.nvm/versions/node/v22.17.1/bin:$PATH
   redis-cli ping || redis-server --daemonize yes --save '' --appendonly no
   ```
   and run every frontend command **from `frontend/`**.

**Nothing is blocked.** The one long-standing red gate (`pip_audit`) was fixed by Issue 406, so
**Layer 0 is fully green** — the first clean state in the lane.

**Two operator items block no code but are worth doing:**
- **`BACKUP_R2_BUCKET` is unset**, so every prod migration runs with no pre-migration safety dump
  (Issue 256). It did not matter for `0051`/`0052` — both are additive and DDL-only, and a dump
  would not help against the only real risk there (a wrong policy, whose remedy is `downgrade -1`).
  It **will** matter for the first migration that drops or rewrites data.
- **Operator punch-list** in `docs/GO_LIVE.md`: **#29** Google OAuth verification · **#26/#28**
  friend beta · **#282** uptime monitor.

---

## WHAT WORKS NOW (verified on `7b8f281` — do not re-investigate)

| Gate | Value |
|---|---|
| Backend pytest | **2581 passed**, 64 skipped |
| Frontend vitest | **595 passed / 83 files** |
| Playwright | **76 passed** — desktop **and** mobile, incl. axe on **both** editor routes |
| Layer 0 | `ruff 0 · mypy 0 · coverage 83.31 · bandit 0/0 · **pip_audit 0**` — **all green** |
| module floors | clip_engine 92.61 (floor 91.0) · preference 90.45 · crypto/limiter/auth 100.0 |
| `tsc -b` · lint · build | clean · **0 errors** (1 pre-existing warning in `useStageStream.ts`) · clean |
| Last CI (PR #73) | **12 / 12 green** |
| Live prod | `/health` **200**; last deploy = PR #73 |

**Integration/RLS tests need Docker, which this box lacks — CI-verified only. Say that plainly in
close-outs rather than claiming they passed locally.**

### Batch B, closed

| Issue | What it did | PR |
|---|---|---|
| **389** | The tool routes became an application — `100dvh`, no page scroll, docked honesty statement | #70 |
| **392** | The fabricated waveform became real audio, or honestly absent | #70 |
| **390** | The timeline became an editor — zoom, drag, snap, keyboard | #70 |
| **391** | The edit stopped being browser state — server document, unbounded undo, autosave | #71 + #73 |
| **406** | Dependency advisories cleared; `pip_audit` back to 0 | #72 |

---

## FACTS WORTH NOT RE-DERIVING (Issue 391)

- **The compare-and-set is `ON CONFLICT DO UPDATE … WHERE revision = :base_revision`.** Per the
  Postgres manual a row locked but not updated because that condition failed is **not returned**, so
  zero rows back *is* the stale signal — no second read. Verified by compiling the statement.
- **A stored row is always at `revision >= 1`**, which is what makes `revision: 0` a meaningful
  "no document yet" sentinel — and what lets the PUT detect the INSERT branch firing under a
  non-zero base (a vanished row) and 409 instead of resurrecting it under a new lineage.
- **The save validator deliberately does NOT apply the render caps.** A work-in-progress edit is
  routinely past 85%-removed; refusing to *save* it would destroy the work. A test pins that a
  90%-removed document saves 200 while the same cuts fail at render. **Do not "fix" the split.**
- **`indices` is derived and never persisted** — a pure function of (times, transcript), and the
  transcript is server-owned and mutable. Recomputed on load. Only safe because #390 made committed
  times land on snapped word boundaries.
- **The query is the SEED, not the state.** `useEditDocument` does not invalidate on save, deviating
  from TanStack's documented pattern and from `useApplyClipMetadata`: between a PUT leaving and its
  200 arriving the creator has made more edits, so refetching would visibly revert them.
- **`flush()` is awaited by export, and DRAINS** (a save re-fires itself via `pendingAfterFlight`).
  Fire-and-forget would render the previous document out of a paid render slot.
- **`getRevision()` is a getter, not a value** — `query.data.revision` never advances under
  `staleTime: Infinity`, so a snapshot would 409 every export *after the first save*.
- **`pending_clean_or_edit` lives on the render POST and NOWHERE else.** It is a render-queue
  invariant; putting it on the document PUT would block saves while a render is pending, which is
  the exact failure the issue removes. **Saving is always allowed; exporting is gated.** An AST test
  pins both halves.
- **`/clean/confirm` clears the document; `/clean/discard` does not.** Confirm bakes the edit into
  the render, so leaving the cuts would double-cut the next export. Discard means the creator
  rejected that render and their cuts still describe an unapplied edit.
- **The RLS clean-deny sweep used to hardcode `("clips","signals")`** instead of iterating the tenant
  tables — which is why the `0048`/`0049` bare-`::uuid` regression survived undetected. It now sweeps
  every policied table, and every listed table is **seeded** (the assertion is vacuous on an empty
  table). `0052` repairs both policies.

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
7. **2026-08-04 — Batch B part 1 merged (PR #70) and deployed.** CI came back 11/12; the predicted
   visual-baseline regeneration turned out to be unnecessary. The one red gate was triaged per-CVE
   rather than waved through and promoted to **Issue 406**.
8. **Issue 391 split into two PRs at plan time**, because the render path is paid, flag-gated and
   budget-checked. PR A (additive, migration `0052`) merged as **#71** and deployed.
9. **Issue 406 done before PR B, deliberately** — PR B is the SEV1 change of the lane, and reviewing
   it against a matrix with a permanently-red job is how a real regression hides behind an expected
   one. 12/12 green afterwards.
10. **PR B (#73) merged and deployed.** Two races on the paid path surfaced during the wiring —
   neither in the plan — and both are mutation-checked. **Batch B is complete**, and Layer 0 is
   fully green for the first time in the lane.

---

## KEY COORDINATES & FACTS

| Thing | Value |
|---|---|
| Repo | `github.com/reese8272/creatorclip` |
| Production | `https://autoclip.studio` — VM + docker-compose + Cloudflare tunnel (**not** Render) |
| Branches | `main`, `staging` — both at **`7b8f281`**. All feature branches merged and deleted; none live |
| Last merged PR | **#73** — Issue 391 PR B (2026-08-04). #72 = Issue 406 · #71 = 391 PR A · #70 = Batch B part 1 |
| Staging sync | after any merge to `main`: `git push origin origin/main:staging` (`docs/BRANCHING.md`) |
| Deploy chain | push to `main` → `docker-publish.yml` → `deploy.yml` (staging gate → prod) |
| Baseline regen | `gh workflow run ci.yml -f update_snapshots=true --ref <branch>` → `gh run download <id> -n visual-baselines-<sha>` |
| Node for local gates | `/home/reese/.nvm/versions/node/v22.17.1/bin` (**`frontend/.nvmrc` = 22**) |
| Python | `.venv/bin/python` (system `python3` lacks pydantic) |
| Redis for unit lane | `redis-server --daemonize yes --save '' --appendonly no` |
| Layer 0 | `.venv/bin/python .claude/skills/production-assessment/scripts/run_layer0.py` |
| Alembic head | **`0052_clip_edit_documents`** — repo and prod DB **in sync**. Next migration is `0053`; **re-check `alembic heads` before writing it** |
| R2 prefixes added | `posters/{creator_id}/…` (387) · **`peaks/{creator_id}/…`** (392) — both creator-scoped |
| Beat tasks added | `backfill-video-posters-hourly` (387) · **`backfill-video-peaks-hourly`** (392) |
| Plans dir | `~/.claude/plans/` — 391's is `the-final-issue-391-zippy-lampson.md` (it supersedes the older `yes-but-...` Part 2, which predated #390 merging and was wrong in six places) |
| Active lane | **L25 Batch C** — 393–397, 401. Batches A and B are closed |
| Secrets | `.env` on the VM; `TOKEN_ENCRYPTION_KEY` rotation in `docs/RUNBOOKS.md`. **Names only — never values.** |

---

## CONSTRAINTS & GOTCHAS

**Process traps that actually bit a session**

- **Integration seeders CAN be smoke-checked locally without Docker.** CI caught
  `VideoSentiment.positive` (the enum is `like`/`dislike`) only after a push. `tests/` is excluded
  from mypy in `pyproject.toml`, so nothing local covered it — but constructing the ORM objects in a
  plain script needs no database and takes seconds. Do that before pushing any new seeder row.
- **`ast.unparse` normalises string quoting and INCLUDES docstrings.** An AST test matching
  `'require_flag("x")'` fails against the unparsed `'require_flag(\'x\')'`; and a test asserting a
  token is *absent* will match the prose that explains why it is absent. Match bare tokens, and
  strip the docstring node first.
- **`ruff format` reflows code between an Edit and the next Edit.** After running it, re-read before
  editing the same region — a multi-line `select(...)` had already been collapsed onto one line.
- **Check `git ls-remote` before assuming trunk state.** PR #73 was merged externally mid-session;
  `staging` was 4 commits behind `main` until it was synced. Always `git fetch --prune` first.
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

Canonical list is `docs/OFF_COURSE_BUGS.md`. **Two long-standing entries closed on 2026-08-04** —
the `pip_audit` advisories (Issue 406) and the `0048`/`0049` bare-`::uuid` RLS cast (migration
`0052`). What remains, most likely to matter first:

1. **Prod migrations run with NO pre-migration safety dump.** `BACKUP_R2_BUCKET` has never been set,
   so `deploy.yml` skips the dump (**Issue 256**). This did *not* matter for `0051` or `0052` —
   both are additive/DDL-only, and a dump would not help against the only real risk there (a wrong
   policy, whose remedy is `alembic downgrade -1`). **It will matter for the first migration that
   drops or rewrites data.** Set it before writing `0053` if that migration touches existing rows.
2. **`clips/` is not creator-scoped**, so `DELETE /auth/me`'s `clips/{creator_id}/` prefix purge
   matches nothing — a live right-to-erasure gap. `posters/`, `peaks/` and `clip_edit_documents`
   deliberately do not replicate the pattern.
3. **`/settings` has pre-existing serious contrast failures** (2.14–2.54:1) from the 2026-06-23 "Soon"
   preview rows — decorative disabled mocks under `pointer-events-none opacity-50`, which halves
   contrast while leaving fake controls in the accessibility tree. **This is why `settings` is the one
   dense route still outside the axe gate.** Likely a one-attribute fix (`aria-hidden` on the mock).
4. **Roving tabindex on the timeline is a named, accepted follow-up.** N cuts currently produce
   **1 + 2N** tab stops. A stated cost, not an oversight.
5. **#387's ffmpeg poster chain is asserted against mocked `_run` only.** Needs one pass over a
   genuinely awkward real file (VFR screen recording, `.mkv` with a broken index, source shorter than
   the seek offset) during the next staging soak.
6. **Review-queue badge counts rendered clips, not shortlisted-unreviewed ones** (needs a backend field).
7. **`App.tsx`'s `*` route silently redirects typos to `/dashboard`** instead of 404ing.
8. **`ORIGINALITY_SIMILARITY_THRESHOLD=0.92` is UNVALIDATED** — a silent advisory means "unmeasured",
   not "clean".
9. **The export collector omits ~10 existing tables** (`creator_style`, `video_feedback`,
   `clip_publications`, `notifications`, …) and nothing fails if one is forgotten — it is a
   hand-written dict with no registry. 391 added `clip_edit_documents` plus a test for it, but the
   general gap stands.

**Operator punch-list** (no code can close these — `docs/GO_LIVE.md` is canonical): **#29** Google
OAuth verification · **#26/#28** friend beta · **#282** uptime monitor (still open, beta-critical) ·
**#255** off-box key escrow · **`MAILING_ADDRESS` is deliberately unset** — `config.py` skips all
lifecycle email while empty, because CAN-SPAM requires a real postal address. **Do not "fix" this in
code**; whatever is set becomes public in every footer.

---

## POINTERS

| Doc | Purpose |
|---|---|
| `docs/issues.md` | Work queue — **Batches A ✅ and B ✅ closed; 406 ✅. Active: Batch C (393–397, 401)** |
| `docs/PROJECT_STATE.md` | Progress log — top entry is Issue 391 PR B / Batch B complete |
| `docs/DECISIONS.md` | Top entries are 391 PR B, 391 PR A (three deviations), 390 / 392 / 389 |
| `docs/SOT.md` | Stack, schema, structure — `clip_edit_documents`, the shell layer, timeline + edit modules |
| `docs/COMPLIANCE.md` | ToS/retention — peaks row (392) and the **clip-edit-documents row (391)** |
| `docs/UI.md` | Design system — "App shell" + "Timelines" sections |
| `docs/OFF_COURSE_BUGS.md` | Logged-not-fixed defects — the pip-audit and RLS-sweep rows are now ✅ |
| `docs/GO_LIVE.md` | Canonical launch scorecard (the operator punch-list lives here) |
| `docs/BRANCHING.md` | Promotion model — incl. the `staging` sync command |
| `docs/MIGRATIONS.md` | Migration templates + the PR checklist |
| `docs/CLIPPING_PRINCIPLES.md` | Named principles the engine cites |
| `~/.claude/ISSUES_LOG.md` | Cross-project incident log — **ISSUE-2026-08-03-01** is the npm outage |
| `~/.claude/plans/` | Approved plans; 391's is `the-final-issue-391-zippy-lampson.md` |
| Memory dir | `/home/reese/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/` |
