# Research Brief 01 — UX & Product Gaps (Issue 166)

**Author:** read-only research agent · **Date:** 2026-06-22
**Drives:** Issue 166 (Phase 1 CHECK) → sub-issues below
**Scope:** Gap 1 status/progress visibility · Gap 2 stream→summary output · Gap 3 per-video clip
surfacing · Gap 4 remaining UX/bug sweep
**Method:** industry standard researched first (links inline); every repo claim cited
`file_path:line`. Where I could not verify a claim, I say so.

> Honesty/ToS guardrails this brief respects throughout: no UX proposed here shows or implies a
> virality score (the structural test in Issue 53 must stay green — `docs/issues.md:886`); no flow
> downloads YouTube-hosted media (the `yt-dlp` boundary, `docs/PRD.md:93`, re-confirmed
> `docs/OFF_COURSE_BUGS.md:26`).

---

## 1. Executive summary — highest-leverage findings

1. **The pipeline is mostly invisible after the click, and the machinery to fix it already
   exists.** A full SSE progress system (Redis Streams, ownership, replay, per-creator cap) ships
   in `worker/progress.py` and `routers/tasks.py`, and the worker already emits per-stage `step`
   events for ingest/transcribe/signals/render (`worker/tasks.py:443,567,660,764`). But the
   **dashboard's only status surface is a 4-state `Badge`** (`pending/running/done/failed`,
   `frontend/src/components/dashboard/VideoTable.tsx:13-18`) fed by a 5s `refetchInterval` poll —
   the rich `step` stream is **never consumed on the dashboard or per-video row**, and the global
   activity panel was explicitly deferred (Issue 160). This is the single biggest "am I being
   ignored?" gap, and it is a wiring job, not new infrastructure. **Serves the North Star** by
   making the channel-knowledge loop feel alive and trustworthy.

2. **There is no out-of-app completion notification, and the infrastructure for it is "Not
   started."** Email (Issue 80) and in-app notifications (Issue 81) are both 🔲 Not started
   (`docs/issues.md:1008,1040`). Jobs run minutes-to-hours; the creator must keep the tab open to
   learn clips are ready. Industry standard is a workflow-completion trigger ("your clips are
   ready") — the textbook use case ([PushEngage](https://www.pushengage.com/push-notification-automation-saas/)).
   This is a real activation leak.

3. **"Stream → 5–10 min horizontal recap" is a genuine scope expansion, not a tweak — but ~70% of
   the pipeline transfers.** Today the render path is hardcoded 9:16 vertical
   (`clip_engine/render.py:34-35` `_OUTPUT_W=1080/_OUTPUT_H=1920`; the single-clip crop chain at
   `:197`). The `ClipFormat.horizontal` enum value exists (`models.py:87`) but **nothing renders
   it** — it is a stub. A multi-segment, duration-budgeted, narrative-ordered horizontal montage is
   new (selection-under-budget, ordering, concat render, multi-hour handling), but transcription,
   the signal timeline, peak detection, and DNA-fit scoring all carry over. **This must be gated on
   a `docs/DECISIONS.md` entry** because `docs/PRD.md:101` lists "Live-stream ingestion" as out of
   scope for v1. The ToS-clean framing: the creator **uploads a past-stream VOD file** (the
   existing `origin=upload` path), never live capture, never a YouTube download.

4. **Per-video clip surfacing is a dead-end-prone afterthought; there is no timeline-with-markers
   view anywhere.** A `grep` for `timeline|marker|scrubber|waveform` across `frontend/src` returns
   only unrelated hits (chapter markers, bold markdown markers) — **the scrubber-with-candidate-
   markers pattern that Descript/Opus/Riverside all use does not exist here.** The only path from "a
   video I gave you" to "its clips" is a `Review queue` button that opens `/review?video_id=…`
   (`VideoTable.tsx:151`), a one-clip-at-a-time player, not a map of where clips came from. The
   `clips` rows already carry `setup_start_s`/`peak_s`/`end_s` (`models.py:488`,
   `routers/clips.py:81-93`) — the data for a timeline exists, the view doesn't.

5. **Multiple input paths still risk the "done but not visible" failure mode the team has fought
   twice.** Issue 139 fixed linked-rows-vanishing and the doomed-ingest CTA
   (`docs/OFF_COURSE_BUGS.md:22,26`), but the lesson generalizes: every `origin` (catalog/link/
   upload, `models.py` / `docs/SOT.md:283-292`) needs an honest, non-dead-end destination, and the
   per-video clips view (Gap 3) is where that contract should be made visible.

6. **The dashboard's per-video clip-count fetch is N+1 and the failure/"stuck" states are thin.**
   `frontend/src/pages/Dashboard.tsx:55` fires one `GET /videos/{id}/clips` per done video
   (`useQueries`, logged `docs/OFF_COURSE_BUGS.md:32`). There is no time-budget / "this is taking
   longer than usual" affordance and no surfaced reason on `failed` beyond a red badge — the
   slow-LLM and ingest-failure paths are already logged as real
   (`docs/OFF_COURSE_BUGS.md:39,26`).

7. **Telemetry/observability of the funnel is patched but fragile.** UI telemetry went dark at the
   React cutover and was restored (Issue 155, `docs/OFF_COURSE_BUGS.md:34`), and the global
   activity panel (Issue 160) is still deferred — so cross-page "is anything running?" awareness and
   the funnel data to *measure* these UX fixes are both incomplete. Any new surface here should emit
   `source='ui'` events via the existing `lib/activity.ts` so we can prove the fix worked.

---

## 2. Per-gap findings

### Gap 1 — "How do creators know their video is being analyzed?"

#### Industry standard (researched)
- **Per-stage progress beats spinners for anything over ~4–10s.** Use looped spinners only for
  2–9s waits; for longer work show real, labeled steps ("Step 2 of 5") and, for batch work, an
  "X of Y" count.
  ([UX Movement](https://uxmovement.com/navigation/progress-bars-vs-spinners-when-to-use-which/),
  [Eleken](https://www.eleken.co/blog-posts/progress-indicator-ux),
  [Smart Interface Design Patterns](https://smart-interface-design-patterns.com/articles/designing-better-loading-progress-ux/)).
- **Don't promise a precise ETA you can't hit.** Prefer a coarse "This can take a few minutes" so
  the user can decide to walk away; uncertainty — not duration — is what makes waiting feel long
  ([LogRocket](https://blog.logrocket.com/ux-design/ui-patterns-for-async-workflows-background-jobs-and-data-pipelines/)).
- **Resumable status across navigation + failure surfacing are table stakes** for background-job
  pipelines (LogRocket, ibid.).
- **Out-of-app completion notification ("your clips are ready") is the canonical
  workflow-completion trigger** for SaaS
  ([PushEngage](https://www.pushengage.com/push-notification-automation-saas/)). Frequency cap
  2–3/week; one transactional "ready" mail per job is well within that.

#### What the repo already has
- SSE producer: `worker/progress.py` — Redis Streams, `sync_emit`/`aemit`, ownership
  (`aset_owner`/`aget_owner`, `:189-203`), `Last-Event-ID` replay (`aread_since`, `:209`),
  per-creator 3-slot cap (`aacquire_slot`, `:233`). Progress is explicitly observational, never
  load-bearing (`:22-26`).
- SSE consumer endpoint: `routers/tasks.py` (creator-scoped, keepalive, lifetime cap).
- Worker emits labeled per-stage `step` events keyed on `video_id`/`clip_id`:
  `ingest` (`worker/tasks.py:443,496,509,523`), `transcribe` (`:567,606`),
  `signals` (`:660,694,697,720`), `render` (`:764,809,840,853`), `clean` (`:889,897,957`).
  Terminal `done`/`error` close the stream (`worker/progress.py:59`).
- Frontend hooks: `useTaskStream` (SSE log), `useTaskResult` (token/step/done payload),
  `useCleanedUriPoll` — but these are wired into **Onboarding/Insights/Analysis/Review**, not the
  dashboard video list (`docs/SOT.md:209`).
- Dashboard status today: a 4-value `Badge` (`VideoTable.tsx:13-18`) refreshed by a gated 5s poll
  that stops when nothing is in-flight (`Dashboard.tsx:23-39`). The `step` stream is not shown.
- `videos.ingest_status` is only `pending/running/done/failed` (`docs/SOT.md:282`) — coarser than
  the `stage` labels the worker actually emits.

#### The full journey, and where the creator is left guessing
| Step | Creator sees today | Gap |
|---|---|---|
| Clicks Queue / Generate | Button → "Queuing…/Generating…" then optimistic "Queued ✓" (`VideoTable.tsx:48-56`) | No confirmation the job actually started; label is local state, not server truth |
| Ingest → Transcribe → Signals | Badge flips `pending`→`running` on next 5s poll | The 4 rich `stage` labels the worker emits are invisible; looks like one opaque "running" for minutes |
| Candidates → Score → Rank | (nothing — these run inside generate) | No stage feedback at all for the clip-generation half |
| Render (per clip) | Badge stays until poll sees `done` | Per-clip render `step` events exist (`:764`) but aren't surfaced on the row |
| Clips ready | Badge → `done`; row shows "N clips" link | Only visible if the tab is open; no out-of-app ping (Issues 80/81 not started) |
| Failure / stuck | Red `failed` badge | No reason shown; no "taking longer than usual"; slow-LLM spinner logged `docs/OFF_COURSE_BUGS.md:39` |

#### UX recommendation
- **Per-video row:** replace the single badge with a compact **stage stepper** ("Transcribing… ·
  step 2 of 4") driven by the existing `step` stream for the row's `video_id`. Coarse ETA copy only
  ("usually a few minutes"), never a countdown. On `failed`, show a one-line safe reason +
  Retry/Upload-source affordance (reuse the Issue 139 pattern, `VideoTable.tsx:108-124`).
- **Global activity affordance:** finally build the deferred Issue 160 floating panel as a
  `AppChrome`-level surface backed by a small "active tasks" store (mirror the legacy
  `static/activeTasks.js` + `activityPanel.js`, `docs/SOT.md:190-191`), subscribing to the 3 SSE
  slots. This gives cross-page "yes, something is running" awareness.
- **Out-of-app completion:** one transactional email on terminal `done` ("your clips are ready" +
  deep link to `/review?video_id=…`), gated behind a creator preference. **Compliance note:** the
  email contains only the creator's own video title + a link; no third-party PII, no token — within
  the no-PII-in-logs/comms posture (`docs/SOT.md:444-445`). Requires Issue 80 (email infra) first.
- **"Stuck" honesty:** when a stage's last `step` event is older than a stage-specific threshold,
  show "This is taking longer than usual — still working" rather than a frozen spinner (directly
  addresses `docs/OFF_COURSE_BUGS.md:39`).

#### Honesty/ToS implications
None negative. Status copy describes work, not outcomes — no virality language. Email touches the
notification/PII line only as noted above; keep it own-data-only.

---

### Gap 2 — "Turn a STREAM into a 5–10 minute summary video"

#### Industry standard (researched)
- **Long-video → recap/highlight-reel is a shipped product shape.** Opus Clip stitches scenes from
  different parts of a source into a themed mini-story and offers both a text recap and a ~30s video
  recap; a 30-min source typically yields a TL;DR + bullets + a short reel
  ([OpusClip summarization](https://www.opus.pro/blog/video-summarization-api),
  [OpusClip montage maker](https://www.opus.pro/tools/montage-maker),
  [highlight maker](https://www.opus.pro/tools/highlight-video-maker)).
- **Vizard explicitly handles multi-hour stream/VOD sources** ("paste a link to a multi-hour Twitch
  VOD or upload the file directly") and selects on semantic structure + emotional peaks + key
  talking points ([Vizard highlight maker](https://vizard.ai/tools/highlight-video-maker),
  [Vizard Twitch editor](https://vizard.ai/tools/twitch-editor)).
- **The competitive whitespace is exactly here:** "Nobody owns best-in-class YouTube livestream/VOD
  → highlights" and per-input-minute pricing punishes 3–8h streams
  (`docs/COMPETITIVE_RESEARCH.md:39,113`). Our own competitive report already recommends this as the
  Stage-1 wedge (`docs/COMPETITIVE_RESEARCH.md:108-113`).
- **Standard recap pipeline:** segment-select against a budget → order into a narrative (usually
  chronological or chapter-aware) → stitch with light transitions → target runtime.

#### What the repo already has (transfers)
- Transcription (word-level), audio signals, unified signal timeline:
  `ingestion/transcribe.py`, `ingestion/audio.py`, `ingestion/signals.py` (`docs/SOT.md:102-106`).
- Peak detection + backward-look candidate windows: `clip_engine/candidates.py`,
  `clip_engine/window.py` (`docs/SOT.md:117-118`).
- DNA-fit scoring + reranking: `clip_engine/scoring.py`, `clip_engine/ranking.py`.
- Chapter generation from transcript: `knowledge/chapters.py` (`docs/SOT.md:142`) — directly useful
  for chapter-aware summarization.
- `origin=upload` path (the only clip-trackable, media-carrying path, `docs/SOT.md:285-291`).
- `ClipFormat.horizontal` enum value already defined (`models.py:87`).

#### What is genuinely new
- **Horizontal render mode.** `clip_engine/render.py` is hardcoded 9:16 (`:34-35`), single-segment
  crop+scale (`:197`). Need a 16:9 path and a **multi-segment concat** (ffmpeg `concat`/
  `filter_complex` with transitions). The `horizontal` enum value is a stub today — nothing renders
  it (verified: no horizontal branch in `render_clip_file`, `:139`).
- **Selection under a total-duration budget** (5–10 min) — a different objective than top-N
  independent clips; needs ordering + de-duplication of overlapping beats.
- **Narrative ordering** (chronological/chapter-aware) rather than score-desc.
- **A new artifact type.** A "summary" spans many `(start,end)` segments — it is not one `clips`
  row. Proposed: a `summaries` table (creator_id, video_id, target_duration_s, segments_jsonb,
  render_uri, render_status, dna_version, status) **or** a `clips.kind='summary'` +
  `segments_jsonb`. A dedicated table is cleaner (a clip's single `start_s/end_s` shape doesn't fit
  a montage); recommend the table.
- **Multi-hour source handling** within `SOURCE_MEDIA_RETENTION_HOURS` (`docs/SOT.md:56`) and
  compute/cost limits — chunked transcription + signal extraction; verify WhisperX memory on
  multi-hour input (the GPU caveat, `docs/SOT.md:455`).

#### ToS resolution (definitive)
- **Compliant:** the creator **uploads a past-stream VOD as a file** via the existing
  `origin=upload` path. This is identical to today's upload flow — no YouTube download, no live
  capture.
- **Not compliant:** live-stream capture, or pulling the VOD from YouTube's servers (storing copies
  of YouTube audiovisual content is prohibited —
  [YouTube API ToS](https://developers.google.com/youtube/terms/api-services-terms-of-service),
  [developer policies](https://developers.google.com/youtube/terms/developer-policies); consistent
  with the team's Issue 139 ruling, `docs/OFF_COURSE_BUGS.md:26`).
- So "stream summary" = **"upload your stream VOD file → get a recap"**, never live ingestion. This
  keeps `docs/PRD.md:101` ("Live-stream ingestion out of scope") technically intact while adding the
  output shape — but it still expands the PRD's single-vertical-clip assumption, so it needs a
  DECISIONS entry (drafted in §3).

#### Honesty/DNA grounding
The recap must reflect *this* creator's DNA (their pacing, what their audience rewards via retention
curves — Principle 6 & 11, `docs/CLIPPING_PRINCIPLES.md:28,33`), not a generic "best moments"
heuristic. Each included segment should cite a named principle, same contract as clips. No virality
framing on the recap.

#### Staging recommendation
**Staged sequence, not one issue** (drafted in §3): (a) DECISIONS + data model + budgeted multi-
segment selection + ordering (logic, eval-testable); (b) horizontal concat render; (c) UI surface +
honesty/principle citation. This lets the moat-defining selection logic land and be eval-gated
(per `docs/CLAUDE.md` eval-before-`clip_engine`-change) before the heavier render work.

---

### Gap 3 — "Show me the clips on the video I analyzed/input"

#### Industry standard (researched)
- **Scrubber-with-markers is the universal pattern.** Riverside marks moments on the timeline and
  splits at the play-head into draggable in/out points; Descript exposes marker-set/scrub/zoom on a
  timeline; both let the editor jump to where a clip came from in the source
  ([Riverside editor walkthrough](https://riverside.com/university-videos/editor-clips-walkthrough),
  [Descript timeline help](https://help.descript.com/hc/en-us/sections/10120329331725-Timeline)).
- Opus presents detected clips as a **scored, sorted grid** with the source position visible, and
  uses progressive disclosure (grid → text edit → timeline)
  (`docs/COMPETITIVE_RESEARCH.md:44,117`).
- The translation for us: a **source timeline with candidate markers** + a panel per candidate
  showing the "why" rationale + named principle + the honest fit badge.

#### What the repo already has
- `clips` rows carry `setup_start_s`/`start_s`/`peak_s`/`end_s` + `score`/`dna_match` + per-clip
  reasoning (`models.py:488`, `routers/clips.py:31-93`) — **all the data a timeline needs.**
- `GET /videos/{id}/clips` returns the list (`routers/clips.py:142`).
- Review surface is a **queue**, one clip at a time (`frontend/src/pages/Review.tsx`,
  `WhyThisClip` component, FitBadge from `lib/fit.ts` per `docs/UI.md:9`).
- Entry point from a video row is the "N clips" / "Review queue" button →
  `/review?video_id=…` (`VideoTable.tsx:151`).
- **No timeline/marker view exists** (verified by grep — only unrelated `marker` hits in
  `Analysis.test.tsx`, `lib/brief.ts`, `ChaptersPanel.tsx`).

#### UX recommendation
- **Add a per-video clips view** (e.g. `/video/:id` or `/review?video_id=…&view=map`): a horizontal
  **source timeline** with a marker per candidate placed at `setup_start_s`→`end_s`, `peak_s`
  flagged. Clicking a marker previews that clip inline and shows the WhyThisClip rationale + named
  principle + FitBadge ("Strong/Moderate/Exploratory channel fit", `docs/UI.md:96-106`) — **never a
  raw score, never virality.**
- **Reconcile with Review, don't replace it.** Make the timeline the **map/overview** and keep the
  existing player-first Review as the **focused accept/reject/trim** mode — progressive disclosure
  (map → focus), exactly the Opus pattern. A "Review these in order" CTA on the map drops into the
  existing queue; clicking a single marker deep-links to that clip in Review.
- **Every input path must land here.** Wire each `origin` to an honest destination:
  - `upload` → timeline with markers (clip-trackable).
  - `link` → "Upload source file to clip" affordance already exists (`VideoTable.tsx:108-124`,
    Issue 139); the map view should show the same honest message instead of an empty timeline.
  - `catalog` → analytics/DNA reference only (hidden from `/videos`, `docs/SOT.md:286`); if ever
    surfaced, label it "reference only — not clippable" — no dead end (the Issue 139 cautionary
    tale, `docs/OFF_COURSE_BUGS.md:22`).
- **Fix the N+1 while here:** add a batched `GET /videos/clips/counts` (or fold counts into the
  `/videos` envelope) so the map/dashboard load is one request, not N
  (`docs/OFF_COURSE_BUGS.md:32`).

#### Honesty/ToS implications
Marker rationale cites a named principle (existing contract); FitBadge stays the only confidence
signal. No new ToS surface — uses already-ingested data.

---

### Gap 4 — Remaining UX/interactivity gaps & bugs (categorized inventory)

Cross-referenced against `docs/OFF_COURSE_BUGS.md` and open `docs/issues.md` items so nothing is
double-filed. "Open" = not yet promoted to a tracked, scheduled issue.

| # | Category | Item | Evidence | Severity | One-line fix direction |
|---|---|---|---|---|---|
| G4-1 | Status visibility | Global cross-page active-tasks panel deferred | Issue 160 (`PROJECT_STATE`, `docs/SOT.md:190-191`) | SEV2 | Build the `AppChrome`-level floating panel on the 3-slot SSE cap (→ Gap 1 issue). |
| G4-2 | Telemetry/funnel | No funnel instrumentation to *measure* these UX fixes; UI telemetry only just restored | Issue 155 (`docs/OFF_COURSE_BUGS.md:34`) | SEV2 | Emit `source='ui'` events from new surfaces via `lib/activity.ts`; define an activation funnel (overlaps Issue 172). |
| G4-3 | Perf | N+1 per-video clip-count fetch | `Dashboard.tsx:55`, `docs/OFF_COURSE_BUGS.md:32` | SEV3 | Batched `GET /videos/clips/counts` (→ folded into Gap 3 issue). |
| G4-4 | Failure honesty | Slow-LLM long spinner; analysis/title flows timed out at 60s on prod | `docs/OFF_COURSE_BUGS.md:39` | SEV3 | "Taking longer than usual" copy + assert on 200/headers, raise flow timeout; investigate real latency. |
| G4-5 | Mobile a11y | Visual-regression baselines (`toHaveScreenshot()`) still deferred | `PROJECT_STATE` (Issue 163 close) | SEV3 | Add Playwright visual baselines (overlaps Issue 180 QA). |
| G4-6 | Dead front-door | Stale backend `next_action` `/static/*` URLs not yet repointed to `/app/*` | Issue 161 (`docs/OFF_COURSE_BUGS.md` / `PROJECT_STATE`) | SEV2 | Repoint envelope URLs to SPA routes; validate against real Postgres. |
| G4-7 | Empty/gate states | Data-gate "not enough data yet" exists in Onboarding but no equivalent honest empty-state on the per-video map when a `link`/`catalog` video has no clips | Onboarding data-gate (`docs/SOT.md:161-162`); Gap 3 above | SEV3 | Reuse the Issue 139 upload affordance as the map empty-state. |
| G4-8 | Notifications | No out-of-app "clips ready"; email + in-app surfaces not started | Issues 80, 81 (`docs/issues.md:1008,1040`) | SEV2 | Build email infra (80) → completion email (→ Gap 1 issue); overlaps Issue 176. |
| G4-9 | Rollback debt | Legacy `static/*.html` still served unlinked; full retirement deferred | Issue 85g (`PROJECT_STATE`) | SEV3 | Retire after staging-verified `next_action` repoint (G4-6). |

No new defects discovered beyond what is already logged — the existing OFF_COURSE log is current
and accurate as of this review.

---

## 3. Proposed issues (dependency-ordered, house style)

> Numbering continues after the highest existing/registered number (165 closed; 166–180 are the
> research phase). New build issues start at **181**. These are the sub-issues Issue 166's research
> produces.

### Issue 181: Per-video pipeline status stepper (consume the existing `step` SSE on the dashboard)
**Depends on:** none (infra exists)
**What:** Replace the single 4-state badge on each video row with a live stage stepper driven by the
existing per-task `step` SSE stream (`worker/progress.py`, `routers/tasks.py`). Show the worker's
own stage labels (ingest/transcribe/signals/render/clean), an "X of N" where countable, coarse ETA
copy only ("usually a few minutes" — never a countdown), and a "taking longer than usual" state when
the last `step` event is stale. On `failed`, show a one-line safe reason + Retry/Upload-source
affordance.
**Acceptance criteria:**
- [ ] Row stepper subscribes to the `video_id` SSE stream via `useTaskStream`/`useTaskResult`; falls
      back to the badge if no stream (observational, never load-bearing — `worker/progress.py:22`).
- [ ] Stage labels reflect the worker's emitted `stage` fields; no fabricated stages.
- [ ] Coarse ETA copy only; no precise countdown.
- [ ] Stale-stream → "taking longer than usual"; failure → safe one-line reason (no stack trace).
- [ ] No virality language anywhere on the surface (structural test stays green).
- [ ] `source='ui'` telemetry emitted for stepper interactions.

### Issue 182: Global active-tasks panel (build deferred Issue 160)
**Depends on:** 181 (shares the SSE-subscription store)
**What:** An `AppChrome`-level floating activity widget showing all in-flight tasks across pages,
backed by a small active-tasks store on the 3-slot SSE cap (port the intent of legacy
`static/activeTasks.js` + `activityPanel.js`). Resumes across navigation; respects
`prefers-reduced-motion`.
**Acceptance criteria:**
- [ ] Panel appears whenever ≥1 task is in-flight; persists across SPA navigation.
- [ ] Honors the per-creator 3-slot SSE cap (`worker/progress.py:233`); degrades gracefully at cap.
- [ ] Closes/empties on terminal `done`/`error`; deep-links to the relevant page.
- [ ] Mobile-usable single-column; reduced-motion respected (`docs/UI.md:168`).

### Issue 183: Per-video clips map — source timeline with candidate markers
**Depends on:** none (clip data exists); folds in the N+1 fix
**What:** A per-video clips view rendering a source timeline with a marker per candidate
(`setup_start_s`→`end_s`, `peak_s` flagged). Clicking a marker previews the clip + WhyThisClip
rationale + named principle + FitBadge. "Review in order" CTA drops into the existing queue;
single-marker click deep-links into Review. Honest empty-state per `origin` (upload→markers,
link→"upload source file" affordance, catalog→"reference only"). Add batched
`GET /videos/clips/counts` and use it here + on the dashboard.
**Acceptance criteria:**
- [ ] Timeline renders one marker per candidate from existing `setup_start_s`/`peak_s`/`end_s`.
- [ ] Marker → inline preview + rationale + named principle (`CLIPPING_PRINCIPLES.md`) + FitBadge;
      **no raw score, no virality** (structural test green).
- [ ] Each `origin` lands on an honest, non-dead-end state (no "row vanishes" — Issue 139 lesson).
- [ ] Batched counts endpoint replaces the N+1 (`docs/OFF_COURSE_BUGS.md:32`).
- [ ] Deep-link into Review for a single clip; "Review in order" enters the queue.
- [ ] Per-creator isolation enforced on every query.

### Issue 184: Out-of-app "your clips are ready" completion notification
**Depends on:** Issue 80 (transactional email infra) · overlaps Issue 176
**What:** On terminal pipeline `done`, send one transactional email (creator-preference-gated) with
the creator's own video title + a deep link to the per-video map / Review. In-app surface via Issue
81 when available.
**Acceptance criteria:**
- [ ] One email per completed job; respects a per-creator notification preference.
- [ ] Email contains only own-data (title + link); no token, no third-party PII, no virality copy.
- [ ] Idempotent on Celery retry (no duplicate sends).
- [ ] Honesty disclaimer / unsubscribe present per comms standard.

### Issue 185: [SCOPE EXPANSION — needs DECISIONS entry] Stream-VOD → 5–10 min recap, Part A: data model + budgeted multi-segment selection + narrative ordering
**Depends on:** DECISIONS entry (drafted below) approved first
**What:** Add a `summaries` artifact (creator_id, video_id, target_duration_s, segments_jsonb,
dna_version, render_uri, render_status, status) and a selection step that, from the existing signal
timeline + DNA-fit scoring, chooses non-overlapping segments under a total-duration budget and
orders them into a narrative (chronological/chapter-aware via `knowledge/chapters.py`). Source is an
**uploaded past-stream VOD file** (`origin=upload`) only — no live capture, no YouTube download.
**Acceptance criteria:**
- [ ] `summaries` table + migration; per-creator isolation.
- [ ] Selection respects a configurable target duration (5–10 min) and excludes overlapping beats.
- [ ] Ordering is narrative (not score-desc); each segment cites a named principle.
- [ ] Eval scenario added (`tests/eval/scenarios/*.yaml`) asserting budget + setup-start per segment
      (runs before any `clip_engine/` change, per `CLAUDE.md`).
- [ ] DNA-grounded, honest: reflects this creator's data; no generic "best moments"; no virality.
- [ ] Multi-hour source handled within `SOURCE_MEDIA_RETENTION_HOURS` and compute limits.

### Issue 186: [SCOPE EXPANSION] Stream-VOD recap, Part B: horizontal multi-segment concat render
**Depends on:** 185
**What:** Add a 16:9 render path and multi-segment concat (ffmpeg `concat`/`filter_complex` with
light transitions) to `clip_engine/render.py`, producing the recap mp4 for a `summaries` row.
Activate the existing `ClipFormat.horizontal` stub (`models.py:87`).
**Acceptance criteria:**
- [ ] Renders a single horizontal mp4 stitched from the ordered segments.
- [ ] Runs as a Celery task with status + per-stage `step` events (reuse Gap 1 plumbing).
- [ ] Output stored to the configured storage backend; honors retention purge.
- [ ] No regression to the existing 9:16 single-clip render (eval green).

### Issue 187: [SCOPE EXPANSION] Stream-VOD recap, Part C: UI surface
**Depends on:** 185, 186
**What:** A surface to request a recap from an uploaded VOD, watch it render (stage stepper), and
review/accept it — with honest framing and per-segment principle citations.
**Acceptance criteria:**
- [ ] Recap request gated to `origin=upload` videos; honest copy on non-eligible inputs.
- [ ] Live status via the Gap 1 stepper; FitBadge-style honesty, never virality.
- [ ] Per-segment "why" rationale + named principle visible.

#### Issues needing a `docs/DECISIONS.md` entry
- **Issues 185–187** (the only scope expansion). Draft entry below.

##### Draft DECISIONS.md entry
> **2026-06-22 — Expand v1 output shapes: add an uploaded-stream-VOD → 5–10 min horizontal recap.**
> **What changed:** The PRD scoped v1 to single 9:16 vertical Shorts and listed "Live-stream
> ingestion" as out of scope (`docs/PRD.md:101,129`). We add a second output shape — a multi-segment
> horizontal recap (5–10 min) built from a **creator-uploaded past-stream VOD file** — gated behind
> this decision.
> **Why:** Strongest competitive whitespace ("nobody owns YouTube livestream/VOD → highlights",
> `docs/COMPETITIVE_RESEARCH.md:39`) and our own Stage-1 recommendation
> (`docs/COMPETITIVE_RESEARCH.md:108-113`); ~70% of the pipeline (transcription, signals, peaks,
> DNA-fit scoring, chapters) transfers. Deepens the channel-knowledge loop (recap reflects this
> creator's DNA), not a generic montage.
> **Scope boundary (ToS):** Source is an uploaded VOD **file** via the existing `origin=upload` path
> only. No live capture and no download of YouTube-hosted media — consistent with the Issue 139
> ruling and YouTube API ToS (no storing copies of YT audiovisual content)
> ([YouTube API ToS](https://developers.google.com/youtube/terms/api-services-terms-of-service)).
> So "Live-stream ingestion" stays out of scope; only the output shape expands.
> **Evidence:** OpusClip recap/montage + Vizard multi-hour VOD handling (links in research brief
> 01); repo render-path constraints (`clip_engine/render.py:34-35,197`); `ClipFormat.horizontal`
> stub (`models.py:87`).
> **Staging:** 185 (selection logic, eval-gated) → 186 (render) → 187 (UI).

---

## 4. Open questions for the human

1. **Scope:** Do we expand v1 now to ship the stream-VOD recap (Issues 185–187 + DECISIONS), or
   stage it post-MVP? (One line: "expand now" / "post-MVP".)
2. **Notifications:** Build email infra (Issue 80) next so the "clips are ready" completion email
   (Issue 184) can ship, or hold completion notifications until in-app (Issue 81)?
3. **Gap 3 routing:** New dedicated route (`/video/:id`) for the clips map, or a `view=map` mode on
   the existing `/review` route? (Affects nav + deep-link design.)
4. **Recap target length:** Fix the recap budget (e.g. 5–10 min) as a setting, or let the creator
   choose per-job? (Affects the selection objective in Issue 185.)
5. **Summary artifact storage:** Confirm a dedicated `summaries` table (recommended) vs. overloading
   `clips` with `kind='summary'` + `segments_jsonb`.

---

### Stale / self-contradictory docs flagged
- **Issue 160 has no full block in `docs/issues.md`** — it's referenced only in `PROJECT_STATE.md`
  and `docs/OFF_COURSE_BUGS.md:35` as "deferred." If it stays deferred it should at least get a stub
  in `issues.md` so it isn't lost (or be absorbed into Issue 182 above).
- **`ClipFormat.horizontal` (`models.py:87`) is a schema value with no renderer** — harmless but a
  latent "exists in backend, no front door" trap; Issue 186 resolves it.
- The PRD's "single 9:16 vertical" assumption (`docs/PRD.md:129`) and the SOT pipeline diagram
  (`docs/SOT.md:420`) both bake in single-vertical-clip output; if Issues 185–187 are approved,
  update SOT's pipeline + data-model sections in the same change.
