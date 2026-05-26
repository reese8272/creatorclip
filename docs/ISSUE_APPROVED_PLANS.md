# Approved Phase 1 Plans — Issues 7–19

All approved. Build sequentially; do not deviate from these approaches without a DECISIONS.md entry.

---

## Issue 7 — Clip engine: candidates with backward setup-finding

**Approach**: `clip_engine/window.py` converts the signal timeline into a 1D numpy array
(0.5s resolution) with event weights (retention_spike=3, laughter=2, energy_spike=1.5,
silence=-0.5). `candidates.py` runs `scipy.signal.find_peaks` on that array, then for each
peak scans **backwards** up to 75s to find the most-recent silence end (or nearest energy
spike start) as `setup_start_s`. Returns `{setup_start_s, start_s, peak_s, end_s}`.

**Eval fixture**: `tests/eval/scenarios/basic_retention_peak.yaml` — labeled timeline with
expected `setup_start_s_max` < `peak_s`. The test must assert clip starts ≤ that bound.

**Clip principle cited**: #2 "Clip the setup, not the aftermath".

**Key constraints**:
- `setup_start_s` must always be < `peak_s`
- Minimum clip length 30s; post-peak context 20s
- Pure deterministic logic; no LLM
- `max_candidates` configurable (default=CLIPS_PER_VIDEO_DEFAULT from config)

---

## Issue 8 — Clip scoring + DNA-weighted ranking

**Approach**:
- `clip_engine/scoring.py`: compute a feature vector per candidate (signal density in
  window, hook quality = energy in first 5s, transcript word count, silence ratio) + call
  Claude `claude-sonnet-4-6` with the DNA corpus as a **prompt-cached prefix** and the
  candidate list as user message; Claude returns a JSON score + cited principle per clip.
- `clip_engine/ranking.py`: sort by `claude_score * dna_match_weight`; no preference model
  yet (cold start path). Persist scored candidates to `clips` table.
- New router: `routers/clips.py` — `POST /videos/{id}/clips/generate` and
  `GET /videos/{id}/clips`.

**Key constraints**:
- Every score must cite a named principle from CLIPPING_PRINCIPLES.md
- DNA prefix cached via `cache_control: {"type": "ephemeral"}` on system block
- Tokens logged
- Cold-start: if no confirmed DNA profile, score on signals alone (no LLM DNA-fit call)

---

## Issue 9 — Render: 9:16 cut + active-speaker reframe

**Approach**:
- `clip_engine/render.py`: ffmpeg two-pass cut — first extract segment, then apply 9:16
  crop filter.
- Keyframe extraction: pull one frame from the middle of the clip with ffmpeg.
- Face detection: OpenCV `cv2.CascadeClassifier` (Haar frontal-face) on that keyframe
  to find the face center-of-mass; if no face found, use frame center.
- Crop: 9:16 window centered on the detected face, padded with a blurred/scaled version
  of the full frame if content is too narrow.
- New Celery task: `render_clip(clip_id)` in `worker/tasks.py`.
- New endpoint: `POST /clips/{id}/render` in `routers/clips.py`.

**Key constraints**:
- Add `opencv-python==4.10.0.84` to `requirements.txt`
- ffmpeg binary is OS-level (already in Dockerfile)
- Render output stored to R2 (or local dev); URI written to `clips.render_uri`
- `render_status` transitions: pending → running → done/failed

---

## Issue 10 — Review UI + feedback capture

**Approach**: HTMX + vanilla HTML/CSS/JS, no build step (decided: HTMX is the right
lightweight enhancement layer, avoids a React/Vue dependency for a single-page review flow).

- `routers/review.py`: `POST /clips/{id}/feedback` — accepts
  `{action, trim_start_s, trim_end_s, chosen_format}`, persists to `clip_feedback`.
- `static/review.html`: single player (HTML5 video), Next button, trim handles (range
  inputs wired via HTMX). hx-post to feedback endpoint on each action.
- `static/onboarding.html`: connect YouTube, data-gate status, DNA confirm button.
- `static/profile.html`: show DNA brief + version.
- No client-side JS framework; HTMX for XHR. Inline `<script>` for trim-handle delta only.

**Key constraints**:
- Trim handle inputs capture `trim_start_s` and `trim_end_s` deltas (strongest timing signal)
- Disclaimer rendered in the UI on every page ("estimates, not guarantees")
- Session cookie auth (existing JWT) — no new auth mechanism

---

## Issue 11 — Preference model: recency-decayed reranker

**Approach**:
- `preference/decay.py`: `sample_weight(feedback_age_days) = e^(-λ * age_days)`,
  λ = ln(2)/30 (30-day half-life — tighter than DNA's 90-day because feedback signal
  should adapt faster than channel identity).
- `preference/features.py`: feature vector per clip: signal_density, hook_energy,
  silence_ratio, dna_match, clip_duration_s, setup_length_s.
- `preference/model.py`: `LogisticRegression` when label count <
  `PERSONALIZATION_THRESHOLD_LABELS`; `LightGBMClassifier` when ≥ threshold.
  `predict_score(features) -> float`.
- `preference/train.py`: load all `clip_feedback` for a creator, apply recency weights,
  fit model, pickle weights to `preference_models.weights_blob`.
- Integrate: `clip_engine/ranking.py` calls `predict_score` when model exists; falls back
  to DNA score below threshold.

**Key constraints**:
- Recency decay test: older feedback must get lower weight than recent
- Cold-start path communicates honestly (no fake personalization)
- Model retrained per session (not a background job yet; done in `ranking.py`)

---

## Issue 12 — Upload intelligence + improvement brief

**Approach**:
- `upload_intel/timing.py`: `best_upload_window(activity_rows) -> {day_of_week, hour, score}`
  — return the top-N activity peaks from `audience_activity`, format as human-readable
  schedule.
- `improvement/brief.py`: Claude call with `web_search` tool for live algorithm research
  (trend signals, recent YouTube algorithm guidance) **plus** the creator's own analytics
  data as a cached prefix. Output: 3–5 actionable improvements with data rationale.
- `routers/upload_intel.py`: `GET /creators/me/upload-intel`.
- `routers/improvement.py`: `GET /creators/me/improvement-brief`.

**Key constraints**:
- `web_search` tool requires the Anthropic `tools` parameter with `{"type": "web_search_20250305"}`
- Honesty disclaimer appended to improvement brief
- Upload timing is deterministic (no LLM); improvement brief uses LLM
- Tokens logged

---

## Issue 13 — Clip outcomes loop

**Approach**:
- Celery Beat periodic task `poll_clip_outcomes` in `worker/schedule.py`: runs every hour,
  finds clips where `published_youtube_id` is set and no outcome recorded (or last fetch
  was 48h or 7d ago), fetches current metrics via YouTube Data API.
- `performed_well = views >= channel_median_views` (computed from `video_metrics` for
  the creator).
- Store result in `clip_outcomes` table.
- `preference/train.py`: clips with `performed_well=True` receive 3× sample weight
  multiplier on top of recency decay when retraining the preference model.

**Key constraints**:
- Poll at 48h and 7d post-upload (two checkpoints)
- `performed_well` is a boolean; never a virality claim
- Channel median recomputed fresh on each training run
- Beat schedule uses `celery.schedules.crontab` or `timedelta`

---

## Issue 14 — Static serving + UI shell

**Approach:**
- Mount `static/` via FastAPI `StaticFiles` at `/static`
- Serve `index.html` at `GET /` via `FileResponse`
- Build `static/index.html` — dashboard: DNA status badge, video queue with ingest/clips status, nav
- Build `static/insights.html` — calls `/creators/me/upload-intel` and `/creators/me/improvement-brief`
- Build `static/tos.html` + `static/privacy.html` — stub legal pages
- Same vanilla JS + HTMX pattern as existing pages

---

## Issue 15 — Connected user flow + auth guard

**Approach:**
- OAuth callback already redirects to `/`; `index.html` checks `GET /auth/me` on load
- If 401 → redirect to `/auth/login`
- If `onboarding_state != active` → redirect to `/static/onboarding.html`
- If `onboarding_state == dna_pending` → redirect to `/static/profile.html`
- Shared `static/auth.js` (20-line guard) included in every page
- Consistent nav bar across all pages

---

## Issue 16 — Auto-trigger clip generation + status polling

**Approach:**
- New `generate_clips(video_id)` Celery task in `worker/tasks.py`
- `build_signals` chains into `generate_clips` on completion
- `index.html` polls `GET /videos/{id}/status` every 5s while processing; shows "clips ready" badge
- `setInterval` cleared when status transitions to done

---

## Issue 17 — Source media purge + YouTube analytics refresh

**Approach:**
- `purge_stale_source_media` Beat task (hourly): finds Videos where `source_uri` set and `created_at < now() - SOURCE_MEDIA_RETENTION_HOURS`; calls `delete_file`; nulls `source_uri`
- `refresh_youtube_analytics` Beat task (daily): re-fetches video_metrics + audience_activity for all creators with valid tokens
- Both added to `worker/schedule.py` Beat schedule

---

## Issue 18 — Per-creator rate limiting

**Approach:**
- `slowapi` library backed by Redis
- Keyed on session JWT creator_id (not IP)
- LLM endpoints (`/improvement-brief`, clips/generate): 10/hour per creator
- Render endpoint: 20/hour per creator
- All other API: 120/min per creator
- `RateLimitExceeded` → 429 with `Retry-After` header

---

## Issue 19 — Account deletion (right-to-erasure)

**Approach:**
- `DELETE /creators/me` in `routers/auth.py`
- Revoke Google OAuth via `POST https://oauth2.googleapis.com/revoke`
- Delete all R2/local storage under `source/{creator_id}/` and `clips/{creator_id}/`
- `DELETE FROM creators` — cascade handles all child rows
- Clear session cookie; append AuditLog entry

---

## Issue 20 — knowledge/ RAG layer

**Decision: DEFERRED to Phase 2.** `improvement/brief.py` already uses `web_search_20250305`
for live research. Static corpus adds complexity without meaningfully improving brief quality at MVP.
