# Research Brief — Multi-Platform Distribution & Publishing (Issue 178)

> **Type:** Read-only Phase 1 (CHECK) research. No product code changed.
> **Scope tension:** This studies a **deliberate v1-scope expansion** — `docs/PRD.md:99-100`
> lists "Direct auto-publishing to YouTube Shorts (recommend + export in v1)" and
> "Platforms other than YouTube (TikTok / Reels export is MVP+)" as **Out of Scope (v1)**.
> Adopting any publish/schedule capability requires a `docs/DECISIONS.md` entry (drafted in §6).
> **Date:** 2026-06-22

---

## 1. Executive summary (conclusions first)

**The pipeline ends at a rendered 9:16 mp4 in storage; getting it onto a platform is 100%
manual.** The `render_uri` is handed straight to a browser `<video src>` (`frontend/src/components/review/ClipPlayer.tsx:76-79`);
there is no download/export endpoint and no publish path. Yet the project already *recommends*
when to post (`upload_intel/timing.py`) and already tracks post-publish outcomes keyed on a
YouTube video id (`models.py:577` `ClipOutcome.published_youtube_id`, polled hourly by
`worker/tasks.py:308` `poll_clip_outcomes`). Publishing is the missing middle of a loop the
codebase has already built both ends of.

**Recommended staircase (smallest valuable step first):**

| Phase | Capability | Scope/approval cost | Verdict |
|-------|-----------|--------------------|---------|
| **D0** | **First-class export**: download button + platform presets (9:16 Short, 1:1, 16:9 — `ClipFormat` already has `short`/`horizontal`, `models.py:85-87`) | **None** — no new OAuth scope, no app review | **Ship next.** Closes the literal gap with zero compliance cost. |
| **D1** | **YouTube: scheduled, creator-confirmed publish** via `videos.insert` with `status.publishAt`, slotting into the `upload_intel/timing.py` window | New **`youtube.upload`** scope (sensitive) → re-trigger Google OAuth verification **+ a YouTube API compliance audit** to publish anything other than `private` | **Adopt deliberately.** This is the honest sweet spot. |
| **D2** | **TikTok cross-post** (Content Posting API) | TikTok app **audit** required for public; unaudited = private + ≤5 users/24h | Defer behind a TikTok audit; ship draft/inbox first. |
| **D3** | **Instagram Reels cross-post** (Instagram Graph API) | **Meta App Review + Business Verification**; Business/Creator account linked to a Page | Defer; highest friction, lowest control. |

**The honest publishing sweet spot is D1 done as "scheduled, one-click, creator-confirmed
publish," NOT silent auto-publish.** Three independent reasons converge on this:

1. **Compliance reality.** Until the app passes the **YouTube API compliance audit**, `videos.insert`
   can only post videos as **`private`** regardless of the requested `privacyStatus`
   ([YouTube Data API docs](https://developers.google.com/youtube/v3/docs/videos/insert)). So
   "auto-publish public" is *not even technically available* on day one — a scheduled-publish UX
   that ends in a creator confirming is the only thing we can honestly ship pre-audit, and it stays
   the right UX post-audit.
2. **Honesty constraint (`CLAUDE.md`).** "Post this to go viral" framing is banned. A *recommended
   schedule the creator approves* keeps the estimate-based posture; a fire-and-forget auto-poster
   implies a confidence we do not have.
3. **The 100-uploads/day bucket.** `videos.insert` lives in its own quota bucket capped at **100
   uploads/day per project** ([YouTube quota docs](https://developers.google.com/youtube/v3/determine_quota_cost)) —
   fine per-creator, but a real ceiling at 10k creators that argues for a queued, throttleable
   publish job rather than synchronous posting.

**Proposed issues:** 7 (D0a, D0b, D1a–D1d, D2/D3 spike). **DECISIONS.md entries required:** 1
umbrella scope-expansion entry (drafted §6) covering the `youtube.upload` scope + the publish/schedule
capability; D2/D3 each get their own entry when scheduled.

**Notable cross-references:** export presets coordinate with prompt `03` (editorial capabilities);
the publish scheduler reuses prompt `05` (observability) for failure surfacing and `06` (unit
economics) for the per-upload cost ceiling; the new write scope must update `docs/COMPLIANCE.md`
(which already pre-stages `youtube.upload` as "deferred to Phase 2", `docs/COMPLIANCE.md:98`).

---

## 2. Platform matrix (verified against official docs, June 2026)

| Dimension | **YouTube (Data API v3)** | **TikTok (Content Posting API)** | **Instagram (Graph API)** |
|---|---|---|---|
| **Programmatic publish?** | Yes — `videos.insert`. No separate "Shorts API"; a Short is just a ≤3-min vertical (9:16) video with `#Shorts` in title/description. [src](https://developers.google.com/youtube/v3/guides/uploading_a_video) | Yes — **Direct Post** (`/v2/post/publish/video/init/`) or **Upload/Inbox draft** (`/v2/post/publish/...`). [src](https://developers.tiktok.com/doc/content-posting-api-get-started) | Yes — 2-step container then publish; Reel = container with **`media_type=REELS`**. [src](https://developers.facebook.com/docs/instagram-platform/content-publishing/) |
| **Required scope/perm** | **`https://www.googleapis.com/auth/youtube.upload`** (sensitive). [src](https://developers.google.com/youtube/v3/docs/videos/insert) | **`video.publish`** (Direct Post) / `video.upload` (draft). [src](https://developers.tiktok.com/doc/content-posting-api-get-started) | `instagram_business_content_publish` (IG Login) or `instagram_content_publish` + `pages_read_engagement` (FB Login). [src](https://developers.facebook.com/docs/instagram-platform/content-publishing/) |
| **Approval gate** | Google **OAuth verification** (sensitive scope) **+ YouTube API compliance audit** to publish non-private. Without the audit: uploads forced to `private`, ≤100 test users. [src](https://developers.google.com/youtube/v3/docs/videos/insert) | App **audit** to lift the private-only restriction; unaudited = private viewing only, ≤5 users posting / 24h. [src](https://developers.tiktok.com/doc/content-sharing-guidelines) | **Meta App Review + Business Verification** (Advanced Access). Typically 2–7 business days, can be weeks; rejections add 3–5 days each. [src](https://developers.facebook.com/docs/instagram-platform/app-review/) |
| **Scheduling built in?** | **Yes** — `status.privacyStatus=private` + `status.publishAt=<RFC3339>`; YouTube flips to public at that time. [src](https://developers.google.com/youtube/v3/docs/videos/insert) | No native schedule — Direct Post is immediate; we schedule app-side via Celery beat. | No native schedule — we schedule app-side. |
| **Quota / rate limit** | `videos.insert` = **1 unit/call** in its **own bucket capped at 100 uploads/day per project** (NOT drawn from the 10k pool). [src](https://developers.google.com/youtube/v3/determine_quota_cost) ⚠️ *Many third-party guides still cite the stale "1,600 units" figure — superseded; see §5.* | Per-app rate limits + the unaudited ≤5-users/24h cap. | **100 API-published posts / rolling 24h per IG account** (Reels/images/carousels share the bucket; some accounts throttle lower by age/trust). [src](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/content_publishing_limit/) |
| **Format constraints** | Vertical 9:16, ≤3 min ⇒ Short; resumable upload up to 256 GB. Our renders are 1080×1920 mp4 (`clip_engine/render.py:34-35`) — already conformant. | MP4/H.264, ≤300s; file upload or pull-from-verified-URL. [src](https://developers.tiktok.com/doc/content-posting-api-get-started) | Reel container, mp4; account must be **Professional (Business/Creator)** linked to a Page. |
| **ToS posture** | Publishing is *creator's own* content to *creator's own* channel via official API — clean, unlike the yt-dlp source-acquisition line the project already refuses (`docs/PRD.md:93`). | Direct Post needs the creator to have seen the content per TikTok UX-compliance rules; honor "creator confirms" UX. | Standard Meta Platform Terms; per-creator token. |

**Candor on feasibility:** YouTube is the only platform where programmatic *public scheduled*
publishing is both first-party-supported and aligned with the product's existing channel-knowledge
loop. TikTok is feasible but the audit gate + UX-confirmation rules make **"export + manual post"
still the right answer until a TikTok audit is justified by demand**. Instagram has the highest
approval friction (App Review + Business Verification + Page linkage) and the least scheduling
control — **export-only is the honest position for IG indefinitely** unless a strong cross-post
demand signal appears.

---

## 3. Phased plan with architecture sketches (reusing existing pieces)

### D0 — First-class export (no new scope, ship next)
- **D0a — Export/download endpoint + button.** Add `GET /api/clips/{id}/download` returning the
  current `render_uri` as an attachment (or a short-lived presigned R2 URL — `worker/storage.py`
  already builds R2 clients at `:29-43`). Surface a Download button in `ClipPlayer.tsx` beside the
  existing feedback buttons (`:137-146`). Per-creator isolation reuses the same guard `routers/clips.py`
  already applies to clip reads (`routers/clips.py:95-96`).
- **D0b — Platform export presets.** Add render variants beyond 9:16: 1:1 and 16:9. `ClipFormat`
  already enumerates `short` + `horizontal` (`models.py:85-87`); `clip_engine/render.py` already
  parameterizes output W/H (`:34-35`). Coordinate the preset list with prompt `03` so editorial and
  distribution share one preset registry rather than two.

### D1 — YouTube scheduled, creator-confirmed publish (the sweet spot)
Architecture: a new `clip_publications` table + a Celery task `publish_to_youtube` on the **existing**
beat/queue stack (`worker/celery_app.py:34` already sets `task_acks_late=True` for at-least-once;
tasks are idempotent by `self.request.id`, `worker/tasks.py:325`).

- **D1a — `youtube.upload` scope + reconnect flow.** Add the scope to `youtube/oauth.py:46-52`
  (incremental auth so existing read-only creators re-consent only when they opt into publishing).
  Update `docs/COMPLIANCE.md:92-100` (the scope table already pre-stages `youtube.upload` as "No —
  deferred to Phase 2"). **Gated on Google OAuth verification + YouTube API audit** (track as a
  launch dependency, not a code blocker).
- **D1b — Publish job.** `publish_to_youtube(publication_id)` reads the clip's `render_uri`, streams
  a resumable `videos.insert` with `#Shorts` in the description and `status.publishAt` = the chosen
  window. Reuse the read-only Data API client pattern in `youtube/data_api.py`. Retry/idempotency:
  copy the `bind=True, max_retries=3, default_retry_delay=60` shape from `worker/tasks.py:199`; the
  idempotency key prevents a double-post on at-least-once redelivery (store the returned video id
  before acking).
- **D1c — Schedule from the upload window.** `clip_publications.scheduled_at` defaults to the next
  best window from `best_upload_windows()` (`upload_intel/timing.py:18`); a beat tick (add to
  `worker/schedule.py:25`) sweeps due rows and enqueues `publish_to_youtube`. Honesty: the UI labels
  it "recommended time — your data" (estimate, never a virality promise).
- **D1d — Close the loop.** On successful insert, write `ClipOutcome.published_youtube_id`
  (`models.py:577`) so the **already-existing** `poll_clip_outcomes` (`worker/tasks.py:308`) picks it
  up at the 48h/7d checkpoints and feeds `performed_well` into preference retraining — zero new
  outcome-tracking code; the loop just gets its missing input.

### D2 / D3 — Cross-post spike (TikTok then Instagram)
- One spike issue: model `clip_publications.platform` + a per-platform token store (mirror
  `YoutubeToken`, `models.py:212`, Fernet-encrypted per `docs/COMPLIANCE.md`). Implement TikTok
  **draft/inbox** first (no audit needed) to validate the flow, then pursue the TikTok audit only if
  demand warrants Direct Post. Instagram stays export-only pending an App Review business case.

---

## 4. Proposed issues (dependency-ordered, `docs/issues.md` house style)

> Every issue below inherits the umbrella **DECISIONS.md** entry in §6 once the scope expansion is
> approved. D0a/D0b need **no** DECISIONS entry (no scope change, no PRD deviation — export is
> already promised "recommend + export in v1", `docs/PRD.md:100`).

**Issue D0a — Clip export/download endpoint + UI affordance**
*What:* Add `GET /api/clips/{id}/download` (attachment or short-lived presigned R2 URL) and a
Download button in `ClipPlayer.tsx`. Per-creator isolation enforced as on existing clip reads.
*Acceptance criteria:*
- [ ] Authenticated creator downloads only their own clip's current `render_uri`; cross-creator → 404
- [ ] Button appears beside feedback controls; works for both `render_uri` and `cleaned_render_uri`
- [ ] No new OAuth scope; no DECISIONS entry needed

**Issue D0b — Platform export presets (aspect-ratio variants)**
*What:* Offer 1:1 and 16:9 export renders in addition to 9:16, via a shared preset registry
coordinated with prompt `03`. Reuse `clip_engine/render.py` W/H parameterization.
*Acceptance criteria:*
- [ ] Creator picks a preset; render job produces the variant; download serves it
- [ ] `ClipFormat` extended only if a third stored format is needed (else presets are render-time)
- [ ] Preset list is single-sourced with the editorial-capabilities work (no duplicate registry)

**Issue D1a — Add `youtube.upload` scope + incremental-consent reconnect**
*What:* Add the write scope to `youtube/oauth.py`; existing read-only creators re-consent only on
opting into publishing. Update `docs/COMPLIANCE.md` scope table.
*Acceptance criteria:*
- [ ] Scope requested only for creators who enable publishing (minimum-necessary; `docs/COMPLIANCE.md:15`)
- [ ] Tokens stored Fernet-encrypted, read via `decrypt()`, never logged
- [ ] `docs/COMPLIANCE.md` updated; **DECISIONS entry (§6) merged**
- [ ] Google OAuth verification + YouTube API audit tracked as a launch dependency

**Issue D1b — `publish_to_youtube` Celery task (`videos.insert`, idempotent)**
*What:* Resumable upload of `render_uri` with `#Shorts` description; idempotent on
`self.request.id`; stores returned video id before ack.
*Acceptance criteria:*
- [ ] At-least-once redelivery never double-posts (idempotency verified by test)
- [ ] Retries on transient errors; permanent errors (e.g. quota/audit) surface, don't retry-loop
- [ ] Respects the 100-uploads/day bucket (throttle/queue, not synchronous)
- [ ] Temp media cleaned up; no token/PII in any log line

**Issue D1c — Scheduled publish from the upload-timing window**
*What:* `clip_publications` table (status, scheduled_at, platform, published_id); beat sweep enqueues
due publishes; default `scheduled_at` from `best_upload_windows()`.
*Acceptance criteria:*
- [ ] Creator confirms a recommended time (estimate-framed, never "go viral")
- [ ] Beat tick enqueues only due, creator-confirmed rows; failures surfaced (cross-ref prompt `05`)
- [ ] Per-creator isolation on every `clip_publications` query

**Issue D1d — Wire published clips into the outcome loop**
*What:* On success, set `ClipOutcome.published_youtube_id` so the existing `poll_clip_outcomes`
checkpoints feed `performed_well` into preference retraining.
*Acceptance criteria:*
- [ ] Published clip appears in the 48h/7d outcome poll with no new poller code
- [ ] `performed_well` flows into preference retraining as today

**Issue D2/D3 — Cross-post spike: per-platform token model + TikTok draft mode**
*What:* Generalize `clip_publications.platform` + encrypted per-platform tokens; implement TikTok
draft/inbox (no audit). Instagram stays export-only pending a business case.
*Acceptance criteria:*
- [ ] Token model mirrors `YoutubeToken` encryption posture, per-creator isolated
- [ ] TikTok draft post round-trips end to end; audit/Direct-Post deferred behind demand
- [ ] **Separate DECISIONS entry** filed per platform before any build

---

## 5. Flagged discrepancies / stale docs

- **YouTube quota cost: 1,600 vs 1 unit.** Most third-party guides (and one of my own search hits)
  cite `videos.insert` = **1,600 units**. The **official** Quota Calculator now states `videos.insert`
  costs **1 unit/call in its own bucket capped at 100/day**
  ([src](https://developers.google.com/youtube/v3/determine_quota_cost)). Treat the official doc as
  authoritative; **the real constraint is the 100-uploads/day-per-project cap, not the 10k pool.**
  Re-verify live before building D1b — Google has revised this number before.
- **The binding gate is the YouTube API *audit*, not just OAuth verification.** Pre-audit,
  `videos.insert` is forced to `private`. This is *the* reason "auto-publish public" is off the table
  on day one and why D1's UX is scheduled-confirmed-publish.
- **`docs/COMPLIANCE.md:98` already pre-stages `youtube.upload` as "deferred to Phase 2"** and
  `docs/issues.md` Phase 3 backlog lists "Auto-publish to YouTube Shorts (additional OAuth scope)"
  and "Multi-platform export (TikTok / Reels)". The docs anticipate this; they just need the scope
  table + retention notes updated when D1a lands. Not contradictory — consistent.
- **`ClipOutcome.published_youtube_id` + `poll_clip_outcomes` already exist** (`models.py:577`,
  `worker/tasks.py:308`) assuming a publish step that doesn't exist yet. The outcome half of the loop
  was built ahead of the publish half — D1d simply connects them.

---

## 6. Draft `docs/DECISIONS.md` entry (required to adopt the scope expansion)

```markdown
## 2026-06-?? — Issue 178: Scope expansion — YouTube publish/schedule + cross-post groundwork

**What changed:** Adopts a publishing capability that `docs/PRD.md:99-100` listed as Out of Scope
(v1). Specifically: (a) first-class export with platform aspect-ratio presets [no scope change];
(b) **scheduled, creator-confirmed publish to YouTube Shorts** via `videos.insert` with
`status.publishAt`, requiring the new sensitive scope
`https://www.googleapis.com/auth/youtube.upload`; (c) cross-post groundwork (per-platform token
model) with TikTok draft mode and Instagram deferred to export-only.

**Why:** Competitor parity — direct publish/scheduling is now table-stakes in the repurposing
category (`docs/COMPETITIVE_RESEARCH.md:29`, `:112`). The product already *recommends* upload
windows (`upload_intel/timing.py`) and *tracks* post-publish outcomes
(`ClipOutcome.published_youtube_id`, `worker/tasks.py:308`) — publishing is the missing middle of an
existing loop. We deliberately choose **scheduled creator-confirmed publish, NOT silent
auto-publish**: (1) pre-audit, `videos.insert` can only post `private` regardless of requested
status; (2) the honesty constraint forbids virality/auto-post framing; (3) the 100-uploads/day
bucket argues for a throttleable queue.

**Source/evidence:** YouTube Data API videos.insert + audit/private-default behavior
(developers.google.com/youtube/v3/docs/videos/insert); quota = 1 unit/call, 100/day bucket
(developers.google.com/youtube/v3/determine_quota_cost); TikTok Content Posting API audit/unaudited
restrictions (developers.tiktok.com); Instagram content-publishing rate limit + App Review/Business
Verification (developers.facebook.com). Repo grounding: render output `clip_engine/render.py:34-35`;
storage `worker/storage.py:29-43`; OAuth scopes `youtube/oauth.py:46-52`; pre-staged compliance note
`docs/COMPLIANCE.md:98`.

**Compliance impact:** New write scope → re-trigger Google OAuth verification + a YouTube API
compliance audit before any non-private publish. Update `docs/COMPLIANCE.md` scope table and add a
publishing data-class row. TikTok/Instagram each get their own DECISIONS entry when scheduled.

**Date:** 2026-06-??
```

---

## 7. Open questions for the human (one-line answers)

1. **Scope call:** Adopt D0 (export presets, no scope) + D1 (YouTube scheduled publish, `youtube.upload`)
   as the next release, leaving TikTok/Reels as export-only for now? **(yes / D0-only / no)**
2. **Audit appetite:** Are we willing to take on a **YouTube API compliance audit** (required for
   non-private publish) as a launch dependency, or ship D1 as "schedule → private upload → creator
   publishes manually in YouTube Studio" until the audit clears? **(audit now / private-bridge first)**
3. **Cross-post priority:** Is there a demand signal for TikTok/Reels, or do we defer D2/D3 entirely
   until export adoption proves the need? **(defer / spike TikTok draft now)**

---

*Brief by the read-only distribution research agent. Every external claim links an official platform
doc; every repo claim cites `file_path:line`. No product code modified.*
