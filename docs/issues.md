# CreatorClip — Issue Backlog

Dependency-ordered. Each issue follows Check → Approve → Build → Review (see `CLAUDE.md`).
**Phase 1 of every issue begins by researching the current industry standard.**

Check `[ ]` → `[x]` when an acceptance criterion is met. Update `docs/PROJECT_STATE.md` when an issue closes.

---

## Issue 1: Repo scaffold + Docker Compose + health endpoint
**Depends on**: none
**Status**: 🔄 In Progress

**What**: New repo with `CLAUDE.md`, `requirements.txt`, `Dockerfile`, `docker-compose.yml`
(`app` + `worker` + `postgres` + `redis`), `main.py` with `/health`, `config.py` env loading,
`crypto.py` Fernet helpers.

**Acceptance criteria**:
- [ ] `docker compose up` brings all four services healthy
- [ ] `GET /health` returns `{status, postgres, redis}`
- [ ] `.env.example` lists every var from SOT
- [ ] Missing required env fails app start with a clear error
- [ ] `pytest` passes with a `/health` smoke test

---

## Issue 2: Postgres schema + Alembic + pgvector
**Depends on**: 1

**What**: SQLAlchemy models for every entity (see `docs/SOT.md` data model) + memory/feedback
tables. pgvector extension enabled. Alembic wired. Encrypted round-trip for token columns.

**Acceptance criteria**:
- [ ] `alembic upgrade head` creates every table incl. `creator_dna`, `dna_embeddings`, `clip_feedback`, `clip_outcomes`, `preference_models`
- [ ] pgvector column type works (insert + similarity query)
- [ ] Token encrypt/decrypt round-trip test passes
- [ ] Audit log append-only at the app layer

---

## Issue 3: Google/YouTube OAuth + creator session
**Depends on**: 2

**What**: OAuth 2.0 flow (`/auth/login`, `/auth/callback`), minimum YouTube Analytics + Data API
scopes, encrypted token storage + refresh, `get_current_creator` dependency, per-creator
isolation.

**Acceptance criteria**:
- [ ] Creator completes OAuth; channel identity + tokens persisted (encrypted)
- [ ] Expired access token auto-refreshes
- [ ] Protected routes 401 without a session
- [ ] Cross-creator data access rejected (isolation test)

---

## Issue 4: YouTube data fetch — metrics, retention, activity
**Depends on**: 3

**What**: `youtube/analytics.py` + `youtube/data_api.py`: per-video metrics, timestamp-level
retention curves, demographics, audience-activity windows, video metadata, caption availability.
Caching + backoff. **Resolve transcription-host decision (GPU vs hosted) here.**

**Acceptance criteria**:
- [ ] Fetches and stores metrics, retention curves, activity windows for the creator's catalog
- [ ] Quota/backoff handling on 403
- [ ] Minimum-data gate computed from catalog size
- [ ] Tests use recorded fixtures (no live API in CI)

---

## Issue 5: Ingestion pipeline — source + transcript + signals
**Depends on**: 4

**What**: Celery tasks: ingest (creator upload / guarded yt-dlp → R2), transcribe (WhisperX or
hosted, word-level), audio signals (energy/silence/laughter), unified signal timeline.

**Acceptance criteria**:
- [ ] A linked/uploaded video runs ingest → transcribe → signals as background tasks with status
- [ ] Word-level transcript persisted
- [ ] Signal timeline persisted (audio + retention-spike markers merged)
- [ ] `yt-dlp` path guarded to own-content only; off by default
- [ ] Tests cover the task chain with a short fixture clip

---

## Issue 6: Creator DNA builder + brief (Research Mode)
**Depends on**: 5

**What**: `dna/builder.py` ranks by engagement, analyzes top/bottom performers + Shorts-specific
patterns; `dna/brief.py` synthesizes a plain-language brief via Claude (prompt-cached corpus);
embeddings → pgvector; creator confirms → living profile.

**Acceptance criteria**:
- [ ] Produces top/bottom analysis + Shorts patterns (extraction point, optimal length, upload gap, ratio)
- [ ] Generates an editable plain-language Creator Brief
- [ ] Confirmed brief persists as a versioned DNA profile; edits supersede, never delete
- [ ] Recency weighting applied to performer selection
- [ ] Anthropic calls use prompt caching; tokens logged

---

## Issue 7: Clip engine — candidates with backward setup-finding
**Depends on**: 6

**What**: `clip_engine/window.py` rolling 60–90s window; `candidates.py` peak detection +
**backward look to setup start**; produces candidate windows.

**Acceptance criteria**:
- [ ] Given a signal timeline, emits candidate windows with `setup_start_s`, `peak_s`, `end_s`
- [ ] **Eval assertion**: on labeled fixtures, clip start lands at the setup, not the post-peak aftermath
- [ ] Configurable candidate count
- [ ] Pure logic where possible; deterministic given fixed input

---

## Issue 8: Clip scoring + DNA-weighted ranking
**Depends on**: 7

**What**: `scoring.py` combines signal features + Claude DNA-fit judgment (cached on DNA
profile); `ranking.py` orders by predicted fit. No preference model yet (cold-start path).

**Acceptance criteria**:
- [ ] Each candidate gets a `score` and `dna_match`
- [ ] Ranking reflects DNA (clips matching the brief rank higher) on a fixture
- [ ] Claude scoring rationale citable ("why this clip?")
- [ ] Tokens logged; prompt caching verified

---

## Issue 9: Render — 9:16 cut + active-speaker reframe
**Depends on**: 8

**What**: `render.py` ffmpeg cut + vertical reframe (face/active-speaker-centered) → R2;
render status on the clip.

**Acceptance criteria**:
- [ ] Candidate renders to a playable 9:16 Short
- [ ] Reframe keeps the speaker in frame on a fixture
- [ ] Render runs as a Celery task with status
- [ ] Output stored to configured storage backend

---

## Issue 10: Review UI + feedback capture
**Depends on**: 9

**What**: Player-first `review.html`: play, upvote/downvote/skip, drag-trim, choose format,
Next; `routers/review.py` persists every interaction as a label. **Decide the review-UI
framework question in Phase 1.**

**Acceptance criteria**:
- [ ] Creator can review a queue of candidate clips without full page reloads
- [ ] Each action (vote/skip/trim-delta/format) writes a `clip_feedback` row
- [ ] Trim handles produce timing-delta labels
- [ ] Tests cover the feedback endpoints end-to-end

---

## Issue 11: Preference model — recency-decayed reranker
**Depends on**: 10

**What**: `preference/` feature vectors + LightGBM/logistic reranker with exponential recency
decay; retrain per session; rerank candidates; surface the personalization threshold.

**Acceptance criteria**:
- [ ] Feedback updates a per-creator model
- [ ] Recency decay verifiably down-weights old feedback (unit test)
- [ ] Reranking shifts candidate order after the threshold volume
- [ ] Below threshold, falls back to DNA + signal ranking with an honest UI label

---

## Issue 12: Upload intelligence + improvement brief
**Depends on**: 11

**What**: `upload_intel/timing.py` best window + optimal gap from audience activity;
`improvement/brief.py` what's-working / underperforming / actions, grounded in data citations
+ live research (web-search tool).

**Acceptance criteria**:
- [x] `GET` returns a best upload window from the creator's own activity data
- [x] Returns optimal long-form → Short gap when supported
- [x] Improvement brief cites specific data rows + current-format research; no generic advice
- [x] Disclaimer/honesty text present (structural test)

---

## Issue 13: Clip outcomes loop (strongest signal)
**Depends on**: 12

**What**: When a creator publishes a clip, capture its real-world performance via the API
and feed it back as the strongest positive label.

**Acceptance criteria**:
- [x] Published clip outcomes fetched and stored
- [x] Outcome feeds the preference model at the highest weight
- [x] Tests cover the outcome → model path

---

## Issue 14+: Phase 2 Backlog

Each becomes its own issue after the core loop ships:
- Vision signals (MediaPipe / face-emotion)
- Auto-publish to YouTube Shorts (additional OAuth scope)
- Multi-platform export (TikTok / Reels)
- Production deployment (Kubernetes, KEDA, GPU nodes)
- Usage-based billing (Stripe metered billing)
- Eval harness hardening (adversarial / edge cases)
- TOKEN_ENCRYPTION_KEY rotation runbook
- Hot-key clipping during live recording / OBS integration
- In-app subtitle, font, crop editor on the review surface
