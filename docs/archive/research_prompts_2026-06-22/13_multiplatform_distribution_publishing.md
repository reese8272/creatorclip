# Research-Agent Prompt — Multi-Platform Distribution & Publishing

> **What this file is.** A ready-to-paste prompt for a Claude Code **research agent**
> (read-only / planning, no code changes). It drives the Phase 1 (CHECK) research for the
> distribution gap: today a finished clip is downloaded/exported manually; creators expect
> publishing + scheduling to YouTube Shorts and cross-posting to TikTok/Reels. These are
> **explicitly out of scope for v1** in the PRD, so this is a deliberate scope-expansion study.
> Industry-standard-first (the One Rule in `CLAUDE.md`); grounds findings in this repo; returns a
> prioritized, scope-aware plan. **Does not write product code.**
>
> **Tracked as:** `docs/issues.md` → Issue 178.

---

## PROMPT (paste below this line)

You are a **distribution + publishing research agent** for **CreatorClip / AutoClip**. The
pipeline ends at a rendered 9:16 clip in storage; getting it onto a platform is manual. Creators
of comparable tools expect **schedule + publish to YouTube Shorts** and **cross-post to TikTok /
Instagram Reels**. You run inside the repo as a read-only researcher. **You do not write or modify
product code.** Your deliverable is a written research brief + a prioritized, scope-aware plan.

### Hard constraints (override everything — read carefully)

1. **Scope tension.** `docs/PRD.md` → Out of Scope (v1) lists *"Direct auto-publishing to YouTube
   Shorts (recommend + export in v1)"* and *"Platforms other than YouTube (TikTok / Reels export
   is MVP+)."* Treat publishing/cross-posting as a **proposed scope expansion** and be explicit
   about what changes; every recommendation here needs a `docs/DECISIONS.md` entry to adopt.
2. **ToS across platforms.** YouTube ToS is the existing hard line (the project already refuses
   yt-dlp downloads). Publishing via the **YouTube Data API `videos.insert`** has its own scope,
   quota-cost (uploads are expensive in quota units), and verification implications; TikTok and
   Instagram each have their own Content Posting / Graph APIs, review processes, and limits.
   Map them honestly.
3. **Honesty.** No "post this to go viral" framing — scheduling/recommendation stays estimate-based.

### Step 0 — Ground yourself in the repo (do this first, do not skip)

1. `docs/PRD.md` — the Out-of-Scope list (above) and the user stories (the product *recommends*
   upload timing today — publishing is the natural next step).
2. `docs/SOT.md` + `docs/COMPLIANCE.md` — current YouTube scopes (`youtube.readonly`,
   `yt-analytics.readonly`) — note publishing needs **write** scope (`youtube.upload`), a
   material OAuth-verification change — and the source-acquisition ToS posture.
3. The relevant code:
   - `upload_intel/timing.py` (best upload window — the existing "when to post" intelligence a
     scheduler would consume) + `routers/upload_intel.py`.
   - `youtube/oauth.py` (scopes + tokens), `youtube/data_api.py` (current read-only Data API use),
     `clip_engine/render.py` (the output formats), `worker/storage.py` (where clips live).
   - `clips.format` / `render_uri` in the data model; `frontend/src/pages/Review.tsx` (the
     export affordance today).
4. `docs/COMPETITIVE_RESEARCH.md` — how competitors handle publishing/scheduling; build on it.

Cite the repo as `file_path:line`.

### Your method (per the One Rule)

Research the **current** standard first, then adapt. Cover the YouTube Data API upload flow +
scopes + quota cost + verification, the TikTok Content Posting API and Instagram Graph API
(content publishing) capabilities/limits/review, social-scheduling architecture (queued/scheduled
publish jobs, multi-account/multi-platform token management), and the export-only vs. publish vs.
schedule spectrum. Be candid about which platforms realistically allow programmatic posting and
at what approval cost.

### Research questions

- **Phasing.** Define the staircase: (a) better **export** (multiple aspect ratios/formats,
  platform presets — coordinate with the editorial-capabilities prompt `03`), (b) **publish to
  YouTube Shorts** (write scope + `videos.insert` + scheduling via the upload window), (c)
  **cross-post to TikTok/Reels**. What's the smallest valuable step, and what does each unlock?
- **YouTube publishing reality.** The write-scope + verification + quota cost of `videos.insert`:
  is auto-publish worth the OAuth-verification and quota implications, or is "scheduled, one-click,
  creator-confirmed publish" the honest sweet spot? Tie to `upload_intel/timing.py`.
- **Cross-platform feasibility.** For TikTok + Instagram: what does each API actually permit
  (direct publish vs. draft/inbox), the app-review process, the token/account model, and the
  rate/format limits? Where is "export + manual post" still the right answer?
- **Scheduling architecture.** A scheduled-publish system on the existing Celery/beat stack:
  per-platform tokens, retry/idempotency, failure surfacing, and how it reuses the upload-timing
  intelligence.
- **Scope decision.** Frame the v1-scope-expansion call: what (if anything) belongs in the next
  release vs. stays "export-only," and the DECISIONS entry that would adopt it.

### What to produce (your deliverable)

A single Markdown research brief, no code changes:
1. **Executive summary** — the recommended phasing + the honest publishing sweet spot, with the
   scope-expansion call called out.
2. **A platform matrix** — YouTube / TikTok / Instagram: programmatic-posting capability, required
   scopes/approval, quota/rate limits, format constraints, ToS notes.
3. **Phased plan** — export → YouTube publish/schedule → cross-post, each with the architecture
   sketch reusing existing pieces (`file_path:line`).
4. **Proposed issues** — dependency-ordered, `docs/issues.md` house style (What / Acceptance
   criteria), each flagging the **required** `docs/DECISIONS.md` entry (scope + new scopes).
5. **Open questions for the human** — the scope/priority call phrased for a one-line answer.

Lead with conclusions. Ground every claim — repo `file_path:line`, platform docs via links. Flag
stale or contradictory docs rather than papering over them.
