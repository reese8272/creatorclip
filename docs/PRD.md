# CreatorClip — Product Requirements Document

**Version**: 0.1 | **Status**: Approved for Issue scoping
**Last updated**: 2026-05-25

> **Honesty constraint (must appear in every interface and the system prompt)**:
> *CreatorClip predicts fit with your style and audience — it does not promise virality.
> Every recommendation is an estimate grounded in your own data, not a guarantee.
> We comply with the YouTube API Services Terms of Service at all times;
> creator analytics are handled per Google's data policies.*

---

## North Star

> **"The only AI editor that truly knows your channel — it learns your style from your own
> analytics, adapts as you evolve, and keeps you ahead of the algorithm."**

This is the single sentence that makes CreatorClip indispensable. Every feature decision is
tested against it: does it deepen the channel-knowledge loop, or is it a distraction?

---

## Problem Statement

Every AI clipping tool on the market detects generic virality signals and ignores the individual
creator. The result is clips that feel like they were made by someone who has never watched the
channel: the wrong moments, cut at the wrong time, with no memory and no improvement.

What creators actually need is an editor that knows *them* — one that improves every session,
never leaves a good moment on the table, reads tone and delivery, and grounds every
recommendation (clip selection, upload timing, content advice) in *their own* analytics
rather than generic advice.

---

## Target User (v1)

Individual YouTubers with an existing content catalog (long-form + Shorts). The product is
multi-tenant from day one because YouTube OAuth is inherently per-creator, but the MVP onboards
a small, hand-picked set (the developer's own channel plus a few invited creators) before any
public launch.

---

## User Stories

- As a creator, I want to **connect my YouTube account once** (OAuth) and have CreatorClip read
  my retention curves, engagement, demographics, and audience-activity windows.
- As a creator with too small a catalog, I want a **clear "not enough data yet" state** telling
  me exactly how many more videos/Shorts unlock Research Mode.
- As a creator, I want a **one-time Research Mode pass** over my catalog that builds my
  **Creator DNA**: what my best clips have in common, where in my videos they come from, my hook
  patterns, my optimal Short length, and what consistently underperforms.
- As a creator, I want a **plain-language Creator Brief** I can read, edit, confirm, or disagree
  with — and that becomes my living profile.
- As a creator, I want the engine to **run automatically on a new video** and surface a ranked
  set of candidate Shorts scored against *my* DNA, not a generic virality score.
- As a creator, I want clips that **start at the setup, not the aftermath** — the moment the bit
  begins, not the reaction after it lands.
- As a creator, I want a **review experience that feels like scrolling** — single-player with
  upvote / downvote / skip / drag-trim / Next — where every interaction silently trains the model.
- As a creator, I want the model to **reflect my taste more over time**, weighting recent
  feedback more heavily so a content pivot isn't anchored to who I was 18 months ago.
- As a creator, I want **upload-timing recommendations pulled from my own audience-activity
  data** — not generic "post at 5pm" advice.
- As a creator, I want a **content-improvement brief** after each profile refresh: what's
  working, what's underperforming, and specific actions — informed by **live research** of
  current Shorts formats and algorithm changes, not stale knowledge.
- As the operator, I want **per-creator usage tracked** (videos processed, clips generated,
  tokens) so cost and quotas are visible before monetization.

---

## Technical Decisions

See `docs/DECISIONS.md` for the full log including deviations from this document.

| Decision | Rationale |
|----------|-----------|
| YouTube-first, OAuth-grounded, multi-tenant from day one | The entire differentiator is "uses the creator's own analytics." Per-creator OAuth is required. |
| Job-pipeline (Celery + Redis), not monolithic request | Ingest → transcribe → signal → DNA → clip → render is minutes-to-hours. Must run as durable background tasks. |
| PostgreSQL 16 + pgvector as the single store | Creator profiles and embeddings in one DB (KISS). No separate vector DB for v1. |
| Claude (Anthropic SDK) as the only LLM | Nuanced language judgment + prompt caching on DNA profile + web-search tool for live research. |
| WhisperX (word-level) with hosted-API fallback | The "clip the setup" mechanic needs word-level timestamps. Hosted fallback for non-GPU environments. |
| Backward clip-start from peak signal | Core differentiator: clip the setup, not the reaction aftermath. |
| Learned reranker (recency-decayed), not fine-tuned LLM | Per-creator fine-tuning is expensive and brittle. A gradient-boosted reranker over clip features updates in seconds. |
| Voyage AI embeddings → pgvector | Consistent with Anthropic-centric stack; high-quality embeddings. |
| ffmpeg for cut + active-speaker vertical reframe | Industry-standard, scriptable, no per-render licensing. |
| Cloudflare R2 (S3-compatible) for object storage | Zero egress fees; S3 API compatibility; source media purged on retention timer. |
| Vanilla HTML/CSS/JS, player-first review UI | No build step. **Review-UI framework is a flagged DECISIONS.md candidate — decide before Issue 10.** |
| Vision signals deferred to Phase 2 | Transcript + audio + retention curves carry the MVP. Vision is additive later. |
| Creator-initiated source acquisition | `yt-dlp` is a ToS-risk convenience, off by default, never on third-party channels. |

---

## Out of Scope (v1)

- Platforms other than YouTube (TikTok / Reels export is MVP+)
- Direct auto-publishing to YouTube Shorts (recommend + export in v1)
- Live-stream ingestion
- Vision / facial-expression signals (Phase 2)
- Fine-tuned per-creator LLMs
- Team / multi-seat accounts, agencies
- Mobile-native app (responsive web only)
- A virality *guarantee* of any kind

---

## Acceptance Criteria (v1 MVP)

### YouTube Integration
- [ ] Creator completes Google OAuth with minimum YouTube Analytics + Data API scopes; tokens stored encrypted and auto-refreshed.
- [ ] App fetches per-video metrics, timestamp-level retention curves, demographics, traffic sources, and audience-activity windows.
- [ ] A clear minimum-data gate surfaced when catalog is too small.

### Research Mode (Creator DNA)
- [ ] Ranks catalog by engagement rate and analyzes top 5–10 and bottom 5–10 performers.
- [ ] Extracts: hook structure, best source region, clip-length patterns, title/thumbnail framing, retention-curve shape, tone/delivery, Shorts-specific patterns.
- [ ] Produces an editable plain-language Creator Brief.
- [ ] Confirmed brief persists as a versioned DNA profile (old versions superseded, never deleted).
- [ ] Recency weighting applied to performer selection.

### Clip Engine
- [ ] Runs as a background job when a video is linked/uploaded.
- [ ] Ingests transcript (word-level), audio energy/silence/laughter, and retention-curve spikes into a unified signal timeline.
- [ ] Looks backwards from each peak signal to set clip start at the setup, not the reaction.
- [ ] Produces N configurable candidate clips, each scored against the Creator DNA.
- [ ] Renders each candidate as a 9:16 Short with active-speaker-centered reframe.

### Review UI
- [ ] Single-player review: watch → upvote / downvote / skip, drag trim handles, select format, Next.
- [ ] Every interaction writes a training label.
- [ ] No full page reloads between clips.

### Preference Model
- [ ] Feedback updates a per-creator reranker with exponential recency decay.
- [ ] Reranking measurably shifts candidate order after the personalization threshold (default 20 labels).
- [ ] Below threshold, falls back to DNA + signals with an honest UI label.

### Upload Intelligence
- [ ] Returns best upload window (day/hour) from the creator's own audience-activity data.
- [ ] Returns optimal long-form → Short gap when catalog supports it.
- [ ] Surfaces as a single plain recommendation.

### Content Improvement Layer
- [ ] After each Research Mode run, generates a brief: what's working, what's underperforming, specific actions.
- [ ] Uses live web research; recommendations cite specific data rows — no generic advice.

### Operational & Compliance
- [ ] All Anthropic calls use prompt caching; token usage logged per call.
- [ ] Per-creator usage tracked (videos, clips, tokens).
- [ ] YouTube Analytics data retention/refresh complies with YouTube API Services policy; source media purged on retention timer.
- [ ] OAuth tokens encrypted at rest; no token or PII in logs.
- [ ] `docker compose up` brings the full stack live; `pytest` green; clip-quality eval harness green.
- [ ] No interface or response ever promises virality (structural test green).
