# e2e_functionality — assessed 2026-06-24

Focus assessment: **Can a real creator use the app front to back?** Traced all 9
core journeys by (a) booting `main.py` under a TestClient, (b) cross-checking
every frontend `api()` / `fetch` / `EventSource` call against the live route
table, and (c) verifying request/response body shapes and SSE ownership keying.

## Boot + wiring confirmation (method, not assumption)
- `python3 -c "import main"` succeeds with full required env. All 20 `include_router`
  lines register; OpenAPI resolves **69 documented paths** (+`/api/activity` POST
  hidden via `include_in_schema=False`). Routers are wrapped as `_IncludedRouter`
  sub-mounts (non-standard but functional — routes resolve and respond).
- Worker imports clean: `worker.celery_app` + `worker.tasks` load, **28 Celery
  tasks** registered. Jobs can be enqueued.
- TestClient probes: protected routes (`/auth/me`, `/billing/balance`, `/creators/me`,
  `/videos`, `/tasks/{id}/events`) return **clean 401** unauthenticated (never 500);
  public routes (`/creators/niches`, `/billing/packs`) return 200; `/auth/login`
  302→Google; `/` 302→`/app/dashboard` (SPA bundle present at `frontend/dist`).
- **Frontend↔backend cross-check: all 56 frontend-called paths map to a backend
  route** (path + method). The lone cross-check "miss" (`POST /api/activity`) is a
  false positive — route exists, hidden from schema; confirmed it returns 422 on a
  malformed body (wired) and the frontend payload `{page,event_type,target,extra}`
  exactly matches `ActivityEvent`.

## Journey trace results (all 9 connect end-to-end)
1. **Sign in (OAuth → JWT)** — `/auth/login` 302→Google, `/auth/callback` issues
   session cookie. WIRED.
2. **Connect YouTube / publishing scope** — base login carries read scopes;
   `/auth/connect-publishing` 302 with `include_publish=True` (incremental write
   scope). WIRED.
3. **Ingest/transcribe (link or upload) → Celery → status** — `/videos/upload`
   (multipart, sets SSE owner under `video.id`), `/videos/link` (`Form youtube_video_id`,
   matches frontend urlencoded POST), `/videos/{id}/queue` (pipeline trigger).
   `start_pipeline` chains ingest→transcribe→signals→clips, emitting to
   `task:{video.id}:events`. Status surfaced via `/videos/{id}/status` poll +
   `useStageStream`. **See SEV1 below — queue path drops SSE ownership.**
4. **Creator-DNA build** — `/creators/me/dna/build` returns `{task_id, stream_url}`
   (owner set), `useTaskStream` follows; `/creators/me/dna/confirm` (no body) +
   `/creators/me/dna` GET. WIRED.
5. **Generate + rank clips → billing deduct** — `/videos/{id}/clips/generate`
   (synchronous: runs `generate_and_rank_clips` inline, returns clips). Balance
   gated everywhere via `check_positive_balance`/`check_balance_for_minutes`
   (upload, ingest, render, clean, cuts) → 402 on insufficient; worker auto-refunds
   deducted minutes on terminal failure (`worker/tasks.py:90`). WIRED.
6. **Render styled clip (ffmpeg) → R2/local** — `/clips/{id}/render` (202, owner set
   under `clip_id`), `/clips/{id}/download?disposition=inline` backs the in-app
   player + waveform fetch. WIRED. (UX note: SEV2 below.)
7. **Review/triage + preference learning** — `/videos/{id}/clips` (review list),
   `/clips/{id}/feedback` (`FeedbackRequest` has all optional fields the UI sends:
   action/trim_start_s/trim_end_s/feedback_tags/feedback_note), `/clips/{id}/cuts`
   (`CutsIn{segments:[{start_s,end_s}]}` matches), clean-pass
   (`/clean-preview`→`/clean`→`/clean/confirm`) with `useCleanedUriPoll`. WIRED.
8. **Assistant chat** — `/api/chat/messages` + `/conversations/{id}/regenerate`
   return `stream_url` (owner set), `useTaskStream` consumes. WIRED.
9. **Billing / quota** — `/billing/balance`, `/billing/packs`, `/billing/checkout`
   (`CheckoutRequest{pack_id,success_url,cancel_url,intent_id}` matches frontend
   body exactly → Stripe Checkout URL), `/billing/webhook` for fulfillment. WIRED.

## Findings
- [SEV1] routers/videos.py:447 — `/videos/{id}/queue` runs `start_pipeline(video.id)`
  (which emits progress to `task:{video.id}:events`) but **never calls
  `progress.aset_owner(video.id, creator.id)`**, unlike the otherwise-identical
  `/videos/upload` path (videos.py:378–387) and `/clips/ingest` (clips.py:926).
  Consequence: the dashboard `useStageStream` opens `/tasks/{video.id}/events`, the
  SSE endpoint's `aget_owner(video.id)` returns `None`, and `task_events` raises
  **404 "Unknown task"** — the live stage-progress bar is silently dead for the
  "Queue for analysis" CTA (the primary entry point for selective/manual mode,
  Issue 125) and the auto-mode manual-retry path. The pipeline still completes and
  the row eventually refreshes via the `['videos']` query invalidation, so work is
  not lost — but live progress is broken on this journey. | fix: in
  `queue_video_for_analysis`, immediately before/after `start_pipeline`, add the
  same fail-open block used by upload:
  `try: await progress.aset_owner(str(video.id), str(creator.id)) except redis.RedisError: log`.
  Regression test: POST `/videos/{id}/queue`, then assert GET `/tasks/{id}/events`
  returns 200 (not 404) for the owning creator and 403 for a different creator.
- [SEV2] frontend/src/components/review/CaptionStylePanel.tsx:39 — `apply()` fires
  the render POST and shows "come back in ~30s" but does **not** subscribe to the
  render SSE stream (`/tasks/{clip_id}/events`, whose owner IS set) nor poll
  `render_status`/`render_uri`. The render completes server-side and `render_uri`
  surfaces only on the next clip fetch / manual re-navigation. Not a journey-breaker
  (render succeeds; the clean-pass panel already uses `useCleanedUriPoll`), but the
  styled-render feedback loop is dead-reckoned. | fix: have `apply()` consume the
  render stream via `useTaskStream`/`useStreamAction` (the endpoint already returns
  the owner-stamped stream key) or poll the clip until `render_status==done`, then
  invalidate the clip query.
- [cleanup] (config/dev-env) observability.py:297 + config default — `LOG_DIR`
  defaults to `/app/logs`; importing `main`/`worker` outside the container hard-fails
  with `PermissionError`/`FileNotFoundError` on `/app` before any router loads. Not a
  production defect (the image owns `/app`), but it blocks local `import main` smoke
  tests unless `LOG_DIR` is overridden. | fix: `log_path.mkdir(..., exist_ok=True)`
  is already there; wrap creation in a `try/except OSError` that falls back to a
  temp dir + warns, so a non-writable `LOG_DIR` degrades instead of crashing boot.
- [info] Not a defect: `/api/activity` POST is absent from OpenAPI
  (`include_in_schema=False`) — the only frontend call that fails a naive schema
  cross-check. Verified wired (422 on bad body, payload shape matches `ActivityEvent`).
- [info] Not a defect: `/videos/{id}/clips/generate` is synchronous (returns clips
  inline, no Celery enqueue, no `stream_url`); `VideoTable`'s `useStageStream` for
  the generate action is harmless decoration — the POST result drives the UI, and
  `act()` only checks `resp.ok`.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | n/a (focus run) |
| 2 Concurrency & scale | n/a |
| 3 Security & compliance | ok — unauth 401 clean; SSE ownership enforced (403 cross-creator); per-creator `WHERE` on traced video/clip gets |
| 4 Clip-quality | n/a |
| 5 Anthropic SDK | n/a |
| 6 Cleanliness & typing | 1 cleanup (LOG_DIR boot) |
| 7 Error handling / API | 1 SEV1 (queue SSE 404), 1 SEV2 (render no follow-up) |
| 8 Config & paths | ok — fail-fast on missing env confirmed (8 required vars) |

## Module verdict
NEEDS-WORK — every core journey connects end-to-end and the app boots cleanly with
all 56 frontend calls matched to backend routes; the one journey-breaker is the
missing `aset_owner` on `/videos/{id}/queue`, which silently 404s the live progress
stream on the queue/catalog ingest path (work still completes via polling).
