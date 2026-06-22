# Research Brief 07 — Activation, Onboarding & Growth Funnel (Issue 172)

**Author:** read-only research agent · **Date:** 2026-06-22
**Drives:** Issue 172 (Phase 1 CHECK) → sub-issues 188–193 below
**Scope:** Define activation · map the visit→first-clip funnel · the "not enough data yet" data-gate ·
the minutes-to-hours wait UX · first-run friction (OAuth scope, identity gate, empty states) ·
**product/funnel** instrumentation to *measure* activation per cohort.
**Method:** current industry standard researched first (links inline); every repo claim cited
`file_path:line`. Where I could not verify a claim, I say so.

> **Telemetry lane (per the supervisor brief).** This brief owns **product/funnel analytics**
> (time-to-first-clip, the data-gate funnel, activation events). System observability (logs/metrics/
> traces/alerting) is prompt `05`/Issue 170; out-of-app notification *delivery* is prompt `11`/Issue
> 176. I recommend the funnel *events* and reuse the existing `event_log` sink; I do not design the
> alerting pipeline or the notification channel — I cross-reference them.

> **Guardrails respected throughout.** No onboarding copy proposed here implies a virality promise
> (the honesty constraint, `docs/PRD.md:6-10`; structural test must stay green); no flow downloads
> YouTube-hosted media (the `yt-dlp` boundary re-confirmed in the Issue 139 ruling,
> `docs/OFF_COURSE_BUGS.md:26`, `docs/DECISIONS.md`); funnel events carry `creator_id` only and are
> redacted server-side (`event_log.py:71-84`).

---

## 1. Executive summary

**The activation event for CreatorClip is: the creator keeps or exports their first clip** (an
upvote, a trim-and-keep, or a render download in the review queue). That is the first moment the
product delivers its differentiated value — a clip scored against *this* creator's DNA — and it is
the behavior most likely to predict return. The **aha moment** (the emotional "oh, it actually gets
my channel") and the **activation event** (the measurable proxy) are deliberately distinct; the
2026 standard is to pick a *measurable* proxy that passes the retention-divergence / segment-
stability / causal-impact tests, not the feeling itself
([digitalapplied TTV framework](https://www.digitalapplied.com/blog/customer-onboarding-time-to-value-2026-saas-metrics-framework),
[Amplitude pirate metrics](https://amplitude.com/blog/pirate-metrics-framework)).

**The hard truth about time-to-value:** CreatorClip's first-run loop is *structurally* slow —
OAuth → catalog sync (minutes, YouTube quota fan-out) → **data-gate** → identity intake → DNA build
(an LLM job) → link/upload a source file → ingest + transcribe + render (minutes-to-hours) → review.
The industry average TTV across 547 SaaS companies is **1 day, 12 hours**
([digitalapplied](https://www.digitalapplied.com/blog/customer-onboarding-time-to-value-2026-saas-metrics-framework)),
and **>98% of new users churn within two weeks if they never hit the value milestone** (ibid.). Our
loop has *more* serial waits than a typical SaaS, so the wait UX is not polish — it is the activation
strategy.

**Top 3 drop-off fixes (highest leverage first):**

1. **We cannot currently *measure* activation.** Backend funnel events
   (`auth_callback_completed`, `catalog_sync_requested`, `dna_build_requested`, `dna_confirmed`) are
   emitted via `observability.log_event()`, which writes to a **rotating log file only** (`event`
   logger, `observability.py:107-134`) — **not** to the queryable `event_logs` DB table. The DB sink
   (`event_log.record_event`, `event_log.py:102`) is wired into **exactly one** caller:
   `routers/activity.py:66` (UI clicks/submits). So there is **no per-cohort, per-creator funnel** we
   can query for activation rate or time-to-first-clip. This is fix #1 because every other fix needs
   it to prove it worked. **Issue 188.**

2. **The post-OAuth landing skips the onboarding flow, and the "wait" is a dead console.** A brand-new
   creator's OAuth callback fires the catalog sync (`routers/auth.py:134-140`) and then redirects to
   `/` → `/app/dashboard` (`routers/auth.py:165`), **not** to `/app/onboarding`. They land on a
   dashboard with an empty table and a one-line `DnaCta` banner
   (`frontend/src/components/dashboard/DashboardBanners.tsx:21-38`) — the catalog sync is running with
   no visible progress on that page. On the onboarding page itself, the sync streams into a raw
   `StreamConsole` log dump (`frontend/src/components/onboarding/StreamConsole.tsx`,
   `Onboarding.tsx:149`) with no stage labels, no time estimate, and no "we'll tell you when it's
   ready." For a multi-minute wait this is exactly the uncertainty pattern that makes users bounce
   ([NN/g](https://www.nngroup.com/articles/designing-for-waits-and-interruptions/),
   [LogRocket](https://blog.logrocket.com/ux-design/ui-patterns-for-async-workflows-background-jobs-and-data-pipelines/)).
   **Issues 189 + 190 (the wait + the routing).**

3. **The data-gate is a near-dead-end for small-catalog creators, and the identity gate quietly
   contradicts its own "optional" label.** A creator below `MIN_VIDEOS_FOR_DNA` (10) /
   `MIN_SHORTS_FOR_DNA` (5) sees "Link more of your published videos to unlock DNA"
   (`Onboarding.tsx:33`) with **no alternative path** — yet the clip engine does not actually require
   DNA to run on an uploaded video (DNA gates *personalized scoring*, not clip generation). And step 3
   is labelled "(optional — 45 seconds)" (`Onboarding.tsx:152`) while step 4's Build-DNA button is
   **hard-disabled until identity exists** (`Onboarding.tsx:166`, the Issue-100 gate,
   `docs/DECISIONS.md:204`). Small-catalog creators should get a real "you can still clip a video
   today" path, and the optional/required contradiction must be resolved one way. **Issues 191 + 192.**

A fourth, lower-severity but real item: the `onboarding_state` machine has an **orphan state**
(`awaiting_data` is never written) and the server's `next_action_url` still points at retired
`/static/*.html` pages — both are latent traps (§2.6, **Issue 193**).

---

## 2. The funnel map

Each stage: current behavior (`file_path:line`) · drop-off risk · the best-practice fix. The funnel
is the AARRR "Activation" leg ([Amplitude](https://amplitude.com/blog/pirate-metrics-framework),
[PostHog](https://posthog.com/product-engineers/aarrr-pirate-funnel)).

### 2.0 Define activation (the anchor)

- **Activation event (proposed):** `clip_kept` — first `upvote` OR `trim`+keep OR render export in
  the review queue (the `clip_feedback` actions already exist: `upvote/downvote/skip/trim/format`,
  `docs/SOT.md:337`; `clip_outcomes.published_youtube_id` is the strongest downstream signal,
  `docs/SOT.md:340-342`).
- **Why this proxy:** it is the first irreversible "this is good enough to use" act, downstream of
  everything the product exists to do. It must pass the three 2026 validity tests — retention
  divergence, segment stability, causal impact
  ([digitalapplied](https://www.digitalapplied.com/blog/customer-onboarding-time-to-value-2026-saas-metrics-framework)) —
  which we **cannot test today** because we don't store the funnel (fix #1). Until validated, treat
  `clip_kept` as the *hypothesis* activation event and instrument the whole funnel so the data can
  confirm or replace it. **This choice needs a `docs/DECISIONS.md` entry** (it sets a product KPI not
  in the PRD).
- **TTV target:** there is no current measurement, so no baseline. Benchmark to beat once measured:
  the 547-company average of ~1.5 days, with activation-rate benchmark ~37% median B2B
  ([digitalapplied](https://www.digitalapplied.com/blog/customer-onboarding-time-to-value-2026-saas-metrics-framework)).

### 2.1 Visit → OAuth connect

- **Current:** `GET /auth/login` builds the Google consent URL with **5 scopes up front** —
  `openid`, `userinfo.email`, `userinfo.profile`, `youtube.readonly`, `yt-analytics.readonly`
  (`youtube/oauth.py:46-52`), `prompt=consent select_account`, `access_type=offline`
  (`youtube/oauth.py:62-77`). Onboarding copy carries the honesty band and "Connect your channel
  once" (`Onboarding.tsx:120-124`).
- **Drop-off risk:** the consent screen is the single biggest pre-value cliff. Requesting all 5
  scopes (two of them sensitive YouTube scopes) at first touch maximizes consent friction. Google's
  own guidance: *"do not request access to data when the user first authenticates unless it is
  essential for core functionality… use incremental authorization"*
  ([Google OAuth best practices](https://developers.google.com/identity/protocols/oauth2/resources/best-practices),
  [incremental auth](https://developers.google.com/identity/sign-in/web/incremental-auth)). Both
  YouTube scopes *are* essential here (the product is "uses your own analytics"), so a hard split
  isn't obviously warranted — **but** the consent screen should be pre-framed with a one-line "why
  we need each scope" *before* the redirect, which measurably lifts consent conversion (ibid.).
- **Fix:** keep the scopes (they are minimal and core to the North Star), but add an in-app "what
  each permission unlocks" pre-consent panel on the connect step, and emit `oauth_started` /
  `oauth_completed` / `oauth_abandoned` funnel events so we can see consent drop-off. (Scope
  reduction is a ToS/compliance call — cross-ref prompt `12`/Issue 177.) **Issue 188 (events) +
  189 (copy).**

### 2.2 OAuth callback → catalog sync (the first wait)

- **Current:** on `is_new`, the callback enqueues `sync_channel_catalog` and **redirects to `/`**
  (`routers/auth.py:134-140,165`), which the SPA cutover sends to `/app/dashboard`
  (`docs/SOT.md:29`, `_SPA_BUILT` gate). The sync is a Celery fan-out over the uploads playlist +
  per-video duration (minutes on large channels; the reason it's async, `routers/auth.py:130-133`).
  The dashboard shows the `DnaCta` banner (`DashboardBanners.tsx:21-38`) pointing to `/onboarding`.
- **Drop-off risk (HIGH):** the new creator never sees the onboarding flow first; they see an empty
  dashboard while an invisible job runs. If they *do* click into `/onboarding`, the sync progress is
  a raw text console (`StreamConsole.tsx`, 23 lines, dumps `buffer`) — no labeled stages, no "this
  takes a few minutes," no estimate. NN/g: spinners/consoles "are not appropriate for waits exceeding
  10 seconds"; show labeled steps and elapsed/remaining
  ([NN/g](https://www.nngroup.com/articles/designing-for-waits-and-interruptions/)). The worker
  already emits labeled `step` events (per Brief 01 §1, `worker/tasks.py`), so the data for a real
  stepper exists.
- **Fix:** route a new creator to `/app/onboarding` (not `/`) after callback, and replace the raw
  console with a labeled progress stepper + honest "a few minutes" microcopy (not a fake ETA — NN/g:
  uncertainty, not duration, is what makes waiting feel long). **Issues 190 (routing) + 189 (wait
  UX).** The dashboard's empty-state already does its job well as a *fallback* (`EmptyHero.tsx`).

### 2.3 Data-gate (pass / fail)

- **Current:** `GET /creators/me/data-gate` → `check_data_gate` counts videos that have a
  `VideoMetrics` row with non-null `engagement_rate`, per kind; `ready` is **OR** across buckets
  (≥10 long-form *or* ≥5 Shorts, `youtube/analytics.py:323-368`, thresholds
  `MIN_VIDEOS_FOR_DNA`/`MIN_SHORTS_FOR_DNA`, `docs/SOT.md:58-59`). The UI shows per-kind ✓/• counts
  and either "Ready to build your Creator DNA" or "Link more of your published videos to unlock DNA"
  (`Onboarding.tsx:19-36`). The display predicate was deliberately aligned to the build predicate to
  fix the Issue-88 "gate says ready, build says 0/0" disagreement (`youtube/analytics.py:326-331`).
- **PRD check:** the PRD demands *"a clear 'not enough data yet' state telling me exactly how many
  more videos/Shorts unlock Research Mode"* (`docs/PRD.md:50-51`). **Partial pass:** counts are
  exact, but it shows *current* counts, not the *delta* ("3 more long-form to go"), and it offers
  **no path for a small-catalog creator** — it is effectively a dead end for exactly the user the
  PRD story is about.
- **Drop-off risk (HIGH for small channels):** a creator with 4 longs + 2 Shorts is fully blocked
  from the headline feature with no "meanwhile, here's value." 2026 empty-state standard: orient +
  show value + prompt *an action they can take now*, written as a positive next step, never a
  blocker ([Carbon](https://carbondesignsystem.com/patterns/empty-states-pattern/),
  [Userpilot progressive disclosure](https://userpilot.com/blog/progressive-disclosure-examples/),
  Dropbox's "upload a file before completing your profile" pattern).
- **Fix:** (a) show the *delta to unlock* ("2 more Shorts to unlock Creator DNA"); (b) give
  small-catalog creators a real path — **DNA gates *personalized scoring*, not clip generation**, so
  a creator can still upload one video and get DNA-light/signal-based clips today, with honest copy
  that scoring is generic until DNA is built (consistent with the PRD's "below threshold, falls back
  to DNA + signals with an honest UI label," `docs/PRD.md:139`). **Issue 191.**

### 2.4 Identity intake (Issue-100 gate)

- **Current:** step 3 `OnboardingIdentity` collects niche (1–3) + one-line audience, labelled
  "(optional — 45 seconds)" (`Onboarding.tsx:152`, `OnboardingIdentity.tsx:8-12`). But step 4's
  Build-DNA button is **`disabled={!identityExists}`** (`Onboarding.tsx:166`) — the Issue-100
  decision made intake *required* for DNA (`docs/DECISIONS.md:204`, `docs/issues.md:2381`).
- **Drop-off risk (MEDIUM) + honesty defect:** the label says optional, the gate says required. A
  creator who reads "optional" and skips hits a silently-disabled button with a warning to "Finish
  step 3 first" (`Onboarding.tsx:161-163`). Issue 100's own block flags the tension and says *"the
  right answer is to make the tutorial so good that the intake is enthusiastically filled in, not
  forced"* (`docs/issues.md`, Issue 100 block). The original Issue-83 design made it optional
  *specifically* to avoid a ~70% intake drop-off; Issue 100 overrode that without re-litigating the
  number.
- **Fix:** resolve the contradiction explicitly — either (a) drop the "(optional)" label and keep
  the gate (honest, but adds friction), or (b) keep it optional and let DNA build from video data
  alone with identity as an *enhancer* (the original Issue-83 intent, and what
  `OnboardingIdentity.tsx:55` copy already promises: "Skip and we'll use your video data only").
  **This is a product call that needs a `docs/DECISIONS.md` entry** because it reverses or re-affirms
  Issue 100. **Issue 192.** See Open Question 1.

### 2.5 DNA build → confirm (the second wait) → first clip → keep/export

- **Current:** `POST /creators/me/dna/build` queues `build_dna` (LLM job, 202 + SSE stream,
  `routers/creators.py:249-287`); on save the draft advances `connected→dna_pending`
  (`dna/profile.py:83-84`); confirm advances `dna_pending→active` (`dna/profile.py:135-136`,
  `routers/creators.py:320-343`). Then the creator must **link or upload a source video** to get
  clips — and **only `origin=upload` is clip-trackable** (linked videos return 409 with upload
  guidance, the Issue-139 ruling: we never download YouTube media, `docs/OFF_COURSE_BUGS.md:26`,
  `docs/SOT.md:283-292`). Ingest→transcribe→render is the long pole (minutes-to-hours).
- **Drop-off risk (HIGH, compounding):** this is a *second* invisible LLM wait, then a *third*
  (render) — and the "link a video, then realize you must upload the file" step is a known
  trust-eroder (Issue 139). The same wait-UX gap as 2.2 applies to DNA build and render. Brief 01
  covers the render-progress and "clips are ready" notification surfaces (its Issues 181/184); I
  defer to it for the render leg and cross-reference rather than duplicate.
- **Fix (activation-specific):** (a) same labeled-stepper + honest-wait treatment on DNA build;
  (b) the moment DNA confirms, the next-step CTA must make "upload a source file to get your first
  clip" unmissable and explain *why upload, not link* in one honest line (ToS); (c) instrument
  `dna_build_started/completed`, `first_video_added`, `first_clip_generated`, `clip_kept`
  (= activation) so the drop between "DNA ready" and "first kept clip" is visible. **Issues 188 +
  189.** The "we'll notify you when it's ready" pull-back is the notifications lane — cross-ref
  prompt `11`/Issue 176.

### 2.6 Return — and the orphan-state trap

- **Current:** `resolve_setup_step` (`dna/onboarding.py`) is the single next-step resolver, surfaced
  via `/auth/me` + `/creators/me`. **Two defects:**
  1. **Orphan state.** `onboarding_state` is set to `connected` on first OAuth
     (`youtube/oauth.py:179`); the only writers advance `connected→dna_pending` (`dna/profile.py:83`)
     and `dna_pending→active` (`dna/profile.py:135`). **Nothing ever writes `awaiting_data`** — so
     the `awaiting_data→dna_pending` branch at `worker/tasks.py:1223-1224` is dead code, and the
     resolver's `connected/awaiting_data` grouping (`dna/onboarding.py:110-124`) handles a state that
     can't occur. Harmless today, but a latent "why is this branch never hit" trap.
  2. **Stale `next_action_url`.** The resolver returns `/static/onboarding.html`,
     `/static/profile.html#dna-brief`, `/static/index.html#link-form` (`dna/onboarding.py:117-149`)
     — the **retired legacy pages**. The SPA papers over this client-side with a `STEP_ROUTE` map
     (`DashboardBanners.tsx:15-19`) and only falls back to the server URL "for any unexpected step"
     — so an unexpected step would deep-link a user into a dead `/static/*` page. SOT flags the
     backend `next_action` URL repointing as an unfinished follow-up (`docs/SOT.md:29`).
- **Fix:** delete the `awaiting_data` branch (or document it as reserved) and repoint
  `next_action_url` to `/app/*` routes server-side so the client map is belt-and-suspenders, not
  load-bearing. **Issue 193** (low severity, but it's a correctness/trust cleanup).

---

## 3. The instrumentation gap (product/funnel analytics)

**The core finding:** the funnel cannot be measured today. The split is:

| Sink | Code | Queryable? | Who writes to it |
|---|---|---|---|
| **Log file** (`event` logger) | `observability.log_event()` (`observability.py:107-134`) | No — rotating file only | All backend funnel events: `auth_callback_completed` (`auth.py:157`), `catalog_sync_requested` (`creators.py:237`), `dna_build_requested` (`creators.py:278`), `dna_confirmed` (`creators.py:337`) |
| **`event_logs` DB table** | `event_log.record_event()` (`event_log.py:102`) | **Yes** — queryable, redacted, best-effort | **Only** `routers/activity.py:66` (UI clicks/submits) |

So we have **UI interaction telemetry in the DB** (Issue 151/155, restored after going dark at the
React cutover, `docs/OFF_COURSE_BUGS.md:34`) but **the backend lifecycle events that define the
activation funnel are write-only to a log file** — you cannot run a cohort funnel or compute
activation rate / TTV against them.

**What the standard says we need.** A fixed, governed event taxonomy (`object_action` snake_case,
*no* dynamic names — variable data goes in properties, never the event name), `creator_id` as a
pseudonymous identifier (never email/PII), and a single org-wide activation definition
([digitalapplied taxonomy](https://www.digitalapplied.com/blog/product-analytics-event-taxonomy-tracking-plan-2026),
[Segment naming](https://segment.com/academy/collecting-data/naming-conventions-for-clean-data/),
[Google PII guidance](https://support.google.com/analytics/answer/6366371?hl=en)). The existing
`event_log` sink already enforces the PII rule (`_redact()`, `event_log.py:39-84`) and keys on
`creator_id` only (`event_log.py:13`) — **reuse it, don't build new infra.**

**Events to add (route the existing backend `log_event` calls through `event_log.record_event` too,
with `source="backend"`):**

| Event (`object_action`) | Stage | Already emitted to log? | Key properties (no PII) |
|---|---|---|---|
| `oauth_started` | 2.1 | No | `is_returning` (bool) |
| `oauth_completed` | 2.1 | partial (`auth_callback_completed`) | `is_new` |
| `catalog_sync_started` / `_completed` | 2.2 | partial (`catalog_sync_requested`) | `long_count`, `short_count`, `duration_ms` |
| `data_gate_evaluated` | 2.3 | No | `ready` (bool), `long_count`, `short_count` |
| `identity_saved` / `identity_skipped` | 2.4 | No | `niche_count` |
| `dna_build_started` / `_completed` / `_failed` | 2.5 | partial (`dna_build_requested`) | `version`, `duration_ms` |
| `dna_confirmed` | 2.5 | yes (file only) | `version` |
| `first_video_added` | 2.5 | No | `origin` (upload/link) |
| `first_clip_generated` | 2.5 | No | `clip_count` |
| **`clip_kept`** (ACTIVATION) | 2.5 | No | `action` (upvote/trim/export) |

All carry `creator_id` (pseudonymous) so a cohort funnel and TTV (`oauth_completed → clip_kept`
elapsed) can be computed. **This is product/funnel analytics — distinct from prompt 05's system
observability**; the two can share the `event_log` table but answer different questions (05 =
"is the system healthy", 07 = "are creators activating").

---

## 4. Proposed issues (dependency-ordered, house style)

> Numbered 188+ to avoid collision with Brief 01's proposed Issues 181–187. Issues 189/190 build on
> the render-progress/notification work in Brief 01 (181/184) — coordinate before scheduling.

### Issue 188: Funnel instrumentation — route backend lifecycle events into the queryable `event_log` sink
**Depends on:** nothing (foundation; everything else measures against it).
**What:** Emit the activation-funnel events in §3 through `event_log.record_event(source="backend", …)`
(in addition to the existing `log_event` file lines, which stay), using a fixed `object_action`
taxonomy. Add the new events at their stage sites (`auth.py`, `creators.py`, the worker DNA/clip
tasks, the review-queue feedback endpoint). No PII — `creator_id` only; rely on `_redact()`.
**Acceptance criteria:**
- [ ] Each event in §3 written to `event_logs` with `source="backend"`, `creator_id`, and the listed
      properties; event names are fixed strings (no interpolation).
- [ ] `clip_kept` fires on first upvote/trim-keep/export per creator (the activation event).
- [ ] A documented SQL query computes activation rate and median TTV (`oauth_completed → clip_kept`)
      per signup cohort.
- [ ] No email/token/PII in any new event (assert via `_redact` + a test on the new call sites).
- [ ] Per-creator isolation preserved (events carry only the acting creator's id).
- [ ] **`docs/DECISIONS.md` entry** recording the activation-event definition + taxonomy convention.

### Issue 189: Onboarding wait UX — labeled progress stepper + honest microcopy (replace raw consoles)
**Depends on:** none (can reuse Brief-01 stepper work if it lands first — coordinate).
**What:** Replace the raw `StreamConsole` dumps on the catalog-sync and DNA-build steps
(`Onboarding.tsx:149,178`, `StreamConsole.tsx`) with a labeled stage stepper driven by the worker's
existing `step` events, plus honest "this takes a few minutes — you can leave and come back"
microcopy. No fabricated ETA.
**Acceptance criteria:**
- [ ] Catalog-sync and DNA-build steps show labeled stages + elapsed time, not a raw log buffer.
- [ ] Copy sets a coarse expectation ("a few minutes"), never a precise countdown.
- [ ] Status survives navigation away and back (re-attaches to the SSE stream / re-reads state).
- [ ] No virality language; honesty band preserved.
- [ ] Emits `source="ui"` funnel events for step views so the fix is measurable (ties to 188).

### Issue 190: Route new creators to the onboarding flow after OAuth (not the empty dashboard)
**Depends on:** 189 (so the destination is a good wait experience).
**What:** After a `is_new` OAuth callback, redirect to `/app/onboarding` instead of `/`
(`routers/auth.py:165`); returning creators still land on the dashboard. Keep `EmptyHero` as the
dashboard fallback.
**Acceptance criteria:**
- [ ] First-ever login lands on `/app/onboarding` with the catalog sync visibly in progress.
- [ ] Returning creators (already `active`) land on `/app/dashboard`.
- [ ] Resolver `next_action_url` and the redirect agree (no conflicting next-step signals).
- [ ] Funnel event `onboarding_viewed` recorded (ties to 188).

### Issue 191: Data-gate — show the unlock delta + a real small-catalog path
**What:** In the data-gate UI, show the *delta to unlock* ("2 more Shorts to unlock Creator DNA")
not just current counts, and give sub-threshold creators an honest "you can still clip a video now"
path (upload → DNA-light/signal-based clips), since DNA gates *scoring*, not clip generation
(`docs/PRD.md:139`).
**Acceptance criteria:**
- [ ] Gate shows exact remaining count per the PRD story (`docs/PRD.md:50-51`), as a positive next step.
- [ ] Sub-threshold creators get a working "clip a video now" CTA with honest "scoring is generic
      until your DNA is built" copy (no virality implication).
- [ ] Display predicate stays aligned to the build predicate (no Issue-88 regression).
- [ ] `data_gate_evaluated` event recorded with `ready`/counts (ties to 188).
- [ ] **`docs/DECISIONS.md` entry** if the small-catalog path changes what "below threshold" surfaces
      (reaffirms/extends `docs/PRD.md:139`).

### Issue 192: Resolve the identity-gate contradiction (optional label vs. required gate)
**Depends on:** human decision (Open Question 1).
**What:** Make step 3's label and step 4's gate agree — either drop "(optional)" and keep the
Issue-100 required gate, or keep it optional and let DNA build from video data alone with identity
as an enhancer (the original Issue-83 intent, already promised by `OnboardingIdentity.tsx:55`).
**Acceptance criteria:**
- [ ] Step-3 label and step-4 enablement are consistent (no "optional" that is actually required).
- [ ] If kept required, the walkthrough motivates intake so skips are rare (Issue 100 intent); if
      made optional, the build path works without identity and the conflict-nudge still fires later.
- [ ] `identity_saved` / `identity_skipped` events recorded (ties to 188).
- [ ] **`docs/DECISIONS.md` entry** — this reverses or re-affirms the Issue-100 decision
      (`docs/DECISIONS.md:204`), which itself overrode Issue 83.

### Issue 193: Clean up the onboarding state machine + repoint `next_action_url` to SPA routes
**What:** Remove (or document as reserved) the never-written `awaiting_data` state and its dead
`worker/tasks.py:1223-1224` branch + the resolver grouping (`dna/onboarding.py:110-124`); repoint
`resolve_setup_step` URLs from `/static/*.html` to `/app/*` so the client `STEP_ROUTE` fallback
(`DashboardBanners.tsx:15-19`) can never deep-link into a dead legacy page (`docs/SOT.md:29`).
**Acceptance criteria:**
- [ ] No code path can land on a `/static/*.html` onboarding/profile page from the resolver.
- [ ] `awaiting_data` is either removed end-to-end (enum, resolver, worker, migration note) or
      explicitly documented as reserved with the dead branch deleted.
- [ ] Existing onboarding-state tests still green (`tests/test_onboarding_setup_step.py`,
      `test_onboarding_state_backfill_integration.py`).

#### Issues needing a `docs/DECISIONS.md` entry
- **188** — defines the activation event (`clip_kept`) + the funnel taxonomy (a new product KPI not
  in the PRD).
- **191** — if it changes what sub-threshold creators can do (extends `docs/PRD.md:139`).
- **192** — reverses or re-affirms Issue 100's "intake mandatory" decision.

---

## 5. Open questions for the human

1. **Identity gate (Issue 192):** Keep intake *required* before DNA build (drop the "optional"
   label), or make it genuinely *optional* with DNA building from video data alone? (One line:
   "required" / "optional".)
2. **Activation event (Issue 188):** Confirm `clip_kept` (first upvote/trim-keep/export) as the
   activation event, or prefer `first_clip_generated` (cheaper to reach, weaker signal)? (One line.)
3. **Small-catalog path (Issue 191):** Let a sub-threshold creator clip a single uploaded video with
   generic/signal-based scoring today, or hard-block until the data-gate passes? ("allow" / "block".)
4. **Post-OAuth landing (Issue 190):** Send brand-new creators to `/app/onboarding` (recommended) or
   keep them on `/app/dashboard` with the `DnaCta` banner? ("onboarding" / "dashboard".)
5. **Notify-on-ready channel:** Which channel pulls a creator back when DNA/clips finish — email,
   in-app, or both? (Defer detail to prompt `11`/Issue 176; I just need the channel to wire the
   funnel event.)

---

### Stale / self-contradictory docs flagged
- **`onboarding_state = 'awaiting_data'` is unreachable** (never written; `youtube/oauth.py:179` sets
  `connected`, the only advances are in `dna/profile.py`). The SOT lists it as a live state
  (`docs/SOT.md:270`) and `worker/tasks.py:1223` branches on it — dead code (Issue 193).
- **`next_action_url` points at retired `/static/*.html` pages** (`dna/onboarding.py:117-149`) after
  the SPA cutover; SOT already flags backend URL repointing as an unfinished follow-up
  (`docs/SOT.md:29`) (Issue 193).
- **Step-3 "optional" vs. step-4 required gate** (`Onboarding.tsx:152` vs `:166`) — a live honesty
  defect and a documented Issue-100-vs-Issue-83 tension (Issue 192).
- **Backend funnel events are write-only to a log file** while the DB sink exists and is used for UI
  events only — the activation funnel is unmeasurable today (§3, Issue 188).
- The supervisor brief and prompt reference a `DnaCta` *component file*; the CTA lives inside
  `DashboardBanners.tsx` (the `DnaCta` export, `:21`), and the DNA *card* is
  `components/profile/DnaCard.tsx` — no standalone `DnaCta.tsx` exists. Noted so the next reader
  doesn't hunt for a missing file.
