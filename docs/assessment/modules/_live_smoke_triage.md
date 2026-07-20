# Live-smoke triage — autoclip.studio (prod VM, 2026-07-20)

Artifacts: `CreatorClip.png` / `error.png` (sign-in banner), `render loop.png` (spinner),
`rendered-clip.png` (black frame at 0:00), `autoclip.studio.har` (fonts, 3 entries, all status 0).
Code-only triage — prod is unreachable from this box; each finding lists the exact runtime check.

---

## 1. Google sign-in failure — "We couldn't complete sign-in with Google" — **BLOCKER**

The banner renders only when the login page URL carries `?error=oauth_failed`
(`frontend/src/pages/Login.tsx:24,44-52`). Exactly **two** backend sites emit that redirect,
both in the `/auth/callback` handler:

| Path | File:line | What raises it |
|---|---|---|
| A | `routers/auth.py:277-284` | `httpx.HTTPStatusError` from the Google round-trips in `_exchange_and_persist` (`auth.py:115-131`): token exchange (`youtube/oauth.py:149-158` → `_call_token_endpoint` `raise_for_status` at `oauth.py:123`) **or** identity fetch (`_call_userinfo` `oauth.py:132`, `_call_youtube_channels` `oauth.py:142`). Logs `"OAuth callback exchange failed: HTTP %s"`. |
| B | `routers/auth.py:285-292` | Catch-all `Exception` (DB/schema/RLS/unknown) in `_persist_oauth_grant` (`auth.py:134-239`). Logs `"OAuth callback failed: %s"` (exception TYPE only). |

**Paths that do NOT produce this banner** (useful discriminators):
- State-cookie mismatch / missing (SameSite, tunnel, expired 10-min cookie) → raw JSON 400
  `"Invalid OAuth state — possible CSRF attempt"` (`auth.py:259-261`), not the banner.
- Google consent-screen error (`?error=access_denied`, unverified-app/test-user rejection) →
  raw JSON 400 (`auth.py:255-257`).
- Google returned no refresh token → HTTPException 400 re-raised verbatim (`auth.py:268-270`;
  raised at `youtube/oauth.py:238-239`) — raw JSON, not the banner. (Unlikely anyway:
  `prompt=consent` at `oauth.py:95` forces refresh-token issuance.)
- Signup kill switch → `?error=signup_paused`, different banner (`auth.py:271-276`).
- redirect_uri mismatch vs the Google console fails at Google's **authorize** page (error 400:
  redirect_uri_mismatch), before our callback — user never sees our banner. The same
  `settings.OAUTH_REDIRECT_URI` is used in both the auth URL (`oauth.py:86`) and the exchange
  (`oauth.py:155`), so an exchange-time-only mismatch cannot happen from this code.

**Ranked candidates:**

1. **Token exchange 400 `invalid_grant` — authorization code consumed twice** (path A).
   Codes are single-use; a duplicate GET of `/auth/callback?code=...` (browser refresh after a
   slow callback, Chrome URL-bar prefetch, a Cloudflare retry, or the user re-clicking) burns the
   code. The callback is slow by design — it does token exchange + userinfo + channels + a
   multi-write DB transaction before redirecting — widening the double-request window.
   *Confirm:* `docker compose logs app | grep "OAuth callback exchange failed"` → `HTTP 400`,
   paired with two `/auth/callback` access-log lines seconds apart with the same client IP.
2. **Identity fetch 403 — YouTube Data API quota exhausted or API disabled** (path A).
   `_call_youtube_channels` (`oauth.py:136-143`) hits `youtube/v3/channels` on every login;
   the default 10k-units/day project quota is shared with catalog syncs. A 403 here fails
   sign-in even though OAuth itself succeeded.
   *Confirm:* same log line but `HTTP 403`; cross-check quota in the GCP console.
3. **DB failure in `_persist_oauth_grant`** (path B) — migration drift (exact precedent:
   2026-06-24 outage cited at `auth.py:287-289`) or the Issue 343 RLS role split: the callback
   manually sets the `app.creator_id` GUC (`auth.py:171-174`); if a new tenant-table write lands
   before/outside that, RLS rejects with SQLSTATE 42501.
   *Confirm:* `docker compose logs app | grep "OAuth callback failed:"` → exception type name
   (`ProgrammingError`/`IntegrityError` ⇒ DB; then `docker compose exec app alembic current`
   vs `alembic heads`).
4. **Wrong `GOOGLE_OAUTH_CLIENT_SECRET` on the VM** (path A) — exchange returns 401
   `invalid_client`. *Confirm:* log shows `HTTP 401`.
5. **VM clock skew** (path A, low) — large skew can make Google reject the code as expired
   (`invalid_grant`). *Confirm:* `timedatectl` on the VM; NTP inside WSL/VM hosts does drift.

**(needs-runtime-confirmation)** — single decisive step:
`ssh creatorclip-vm 'cd /opt/autoclip && docker compose logs --tail 2000 app | grep -Ei "oauth callback (exchange )?failed"'`
The two log formats split path A (with HTTP status) from path B (with exception type) immediately.

**Fix directions once confirmed:** (1) make the callback idempotent-ish / redirect-fast (persist
then background the slow work) or add a one-shot guard keyed on `state`; (2) raise quota / stop
fetching channels at login when a Creator row already exists; (3) run migrations; (4) fix env.

---

## 2. Render stuck at "Rendering your clip… (~30s)" — **SEV1**

Spinner condition: `requesting || clip.render_status === 'running'`
(`frontend/src/components/review/ClipPlayer.tsx:39`). `requesting` is now cleared in `finally`
(`ClipPlayer.tsx:54-62` — the old latch was already fixed), and Review's poll keeps refetching
every 4 s while any clip is `pending|running` (`frontend/src/pages/Review.tsx:121-127`), so an
**indefinite spinner means the DB row is stuck at `render_status='running'`**. The poll itself
cannot be the cause — it never stops while `running` is reported.

How a clip gets stuck `running` (worker sets `running` at `worker/tasks.py:1580`, commits at
`tasks.py:1595`; `done` only at `tasks.py:1656-1661`; `failed` written in the task wrapper
`tasks.py:451/470/476` and batch handler `tasks.py:1746/1758`):

1. **Worker process killed after `running` committed — no `failed` write ever happens.**
   SIGKILL (OOM during the ffmpeg encode, `docker compose` restart/deploy exceeding the 10 s
   stop grace, VM reboot) skips every `except` block. With `task_acks_late=True` +
   `task_reject_on_worker_lost=True` (`worker/celery_app.py:71,76`) the message is redelivered,
   but if the whole container died, redelivery waits for the Redis `visibility_timeout` —
   **≥ 3600 s** (`celery_app.py:49-61,88-90`). Best case the user spins for an hour; if the
   redelivery OOMs again, or Redis was restarted (unacked restore lost / broker flushed), it
   spins forever.
2. **No stale-`running` recovery path exists — the stuck state is permanent by design.**
   `POST /clips/{id}/render` 409s on `running` (`routers/clips.py:450-451`), and the frontend
   treats 409 as "in progress, keep polling" (`ClipPlayer.tsx:49-50`). So once (1) happens there
   is no user-reachable retry; only a manual SQL update unsticks it. There is no TTL/heartbeat on
   `running` anywhere (the anomaly guard at `tasks.py:1560-1569` only fires on a redelivery, and
   only when `render_uri` is already set).
3. **Encode legitimately still in flight, "~30 s" copy is optimistic.** The manual path
   re-downloads the full source from R2 per render (`tasks.py:1694-1697`, `alocal_path` — no
   timeout on the boto3 download), then runs a loudnorm measure pass + the encode with a
   `max(120, 4×duration)` s ffmpeg budget (`clip_engine/render.py:376`), under a 3000 s Celery
   soft limit (`config.py` `CELERY_SOFT_TIME_LIMIT_S`). A multi-GB source on a small VM can
   plausibly spin 10–30 min while healthy.
4. **Issue 353 reset interaction — secondary defect, produces "Not rendered yet", not the
   spinner:** the endpoint resets `done→pending` and **nulls `render_uri`**, committing at
   `routers/clips.py:484-487`, *before* enqueueing at `clips.py:496`. If `.delay()` then throws
   (broker down) the request 500s and the clip is left `pending` with its previously-watchable
   `render_uri` destroyed. Also, `pending` (worker never started: worker container down, queue
   backlog) shows the "Not rendered yet" button — consistent with the fact the user saw the
   spinner: the worker **did** start this render.
5. Status-enum mismatch ruled out: worker writes the same enum the API serializes
   (`models.py:92-96`, `routers/clips.py:175`) and the frontend compares the same strings
   (`ClipPlayer.tsx:39,84`, `Review.tsx:124`).

**(needs-runtime-confirmation)** — decisive steps, in order:
1. `docker compose exec db psql -U … -c "SELECT id, render_status, render_uri, updated_at FROM clips WHERE render_status='running' ORDER BY updated_at;"`
   — a `running` row with `updated_at` hours old ⇒ candidate 1/2 confirmed.
2. `docker compose logs --tail 2000 worker | grep -E "render_clip|Rendered clip|SIGKILL|WorkerLostError|oom"` and `dmesg | grep -i oom` on the VM.
3. `docker compose ps` — is the worker container even up / recently restarted?

**Fix (code-side, regardless of which trigger):** add a stale-`running` recovery — either a
Beat sweep that flips `running` rows older than `soft_limit + margin` to `failed`, or change
`routers/clips.py:450` to allow re-render when `updated_at` is older than the hard time limit.
Secondarily: enqueue **before** nulling `render_uri` (or only null on task start) at
`clips.py:484-496`.

---

## 3. Black frame at 0:00 in the player — **SEV2 (likely cleanup)**

**Most likely benign — player-side, not a black render:**
- The `<video>` at `ClipPlayer.tsx:68-76` has **no `poster`** and no `preload` attribute, and
  sits on a `bg-black` container (`ClipPlayer.tsx:75`).
- It sets `autoPlay` **without `muted`** (`ClipPlayer.tsx:73`). Chrome's autoplay policy blocks
  unmuted autoplay without prior user interaction, so the element stays paused at frame 0; until
  enough data arrives (src is the authed `/clips/{id}/download?disposition=inline` stream →
  presigned R2 redirect in prod, `ClipPlayer.tsx:28`) the black background is all you see, and
  if the clip's true first frame is dark (mid-scene setup boundary) it stays black.

**Real-black-render audit — nothing found:** the cut uses `-ss` **before** `-i` plus
`-accurate_seek` **with a full re-encode** (`clip_engine/render.py:541-553`, rationale comment at
`render.py:451-454`), which decodes from the prior keyframe to the exact start frame — this is
the correct pattern and cannot emit the grey/black frames that `-c copy` seeking produces. Stream
mapping is implicit single-video/single-audio; crop x-offset is clamped into the frame
(`render.py:385-386,448`); filters (`crop→scale→subtitles`, `render.py:463-506`) can't blank the
video. The only true-black possibility is that the **source content** at `setup_start_s`
(`worker/tasks.py:1510-1519,1644`) is a black scene transition.

**(needs-runtime-confirmation):** download the rendered mp4 and run
`ffmpeg -i clip.mp4 -vf "blackdetect=d=0.1:pix_th=0.05" -an -f null -` — no `blackdetect` output
⇒ purely a player artifact.

**Fix:** add `muted` alongside `autoPlay` (makes autoplay actually run) **or** add
`preload="auto"`/a real `poster` (a rendered thumbnail already exists infra-wise via the
keyframe extractor, `render.py:438-446`) at `ClipPlayer.tsx:68-76`.

---

## 4. Google Fonts: malformed URL + blocked requests — **SEV2 (CSP) + cleanup (malformed URL)**

The HAR contains exactly 3 entries, all `fonts.googleapis.com` stylesheets, **all status 0**:
1. `family=Geist:…&family=Geist+Mono:…&family=Inter:…&display=swap` — **ours**:
   `frontend/src/index.css:14` (the SPA `@import`; the legacy pages' equivalent is
   `static/_design-tokens.css:20`, Inter+JetBrains Mono).
2. `family=Open+Sans:ital,wght@0,300..800;1,300..800&display=swap%CC%A6` — the malformed one
   (stray U+0326 combining comma below after "swap").
3. `family=Open+Sans:…&family=Poppins:…&display=swap` — also not ours.

**Malformed-URL source: NOT in this codebase.** `grep -rn $'\xcc\xa6'` over all
css/html/ts/tsx/js finds nothing; "Open Sans"/"Poppins" appear **nowhere** in `frontend/` or
`static/` (the only "Open Sans" hit in the repo is `clip_engine/captions.py` — a server-side
ffmpeg caption font, never a browser request). Requests 2 and 3 are injected client-side —
almost certainly a **browser extension** (dark-mode/reader/overlay extensions inject exactly
these Open Sans/Poppins imports; the combining-char typo is a known artifact of one of them).
No app fix; verify by reproducing in an incognito window with extensions off. → cleanup/non-issue.

**The block itself IS ours — CSP has no fonts allowance.** `_CSP_BASE` is
`default-src 'self'; …` with **no `style-src` and no `font-src`** (`main.py:285-292`); both
directives therefore fall back to `default-src 'self'`, which blocks the cross-origin
`@import` stylesheet (status 0 = CSP-blocked in a Chrome HAR). The only escape hatch is
`CSP_EXTRA_SOURCES`, which is **empty by default** (`config.py:459-463`, `.env.example:213` —
the config comment even spells out the exact needed value). Unless the VM's
`/opt/autoclip/.env` sets it, **the SPA has been silently falling back to system fonts in prod
since Issue 229 shipped** — request 1 being blocked is the direct evidence.

**(needs-runtime-confirmation):** `curl -sI https://autoclip.studio/app/ | grep -i content-security-policy`
— if no `style-src https://fonts.googleapis.com` / `font-src https://fonts.gstatic.com` appears,
confirmed (also check `grep CSP_EXTRA_SOURCES /opt/autoclip/.env`). Cloudflare does not add CSP
by default, so the app header is authoritative.

**Fix:** set on the VM
`CSP_EXTRA_SOURCES=style-src 'self' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com`
— or better, self-host Geist/Inter (woff2 under `/static` or the Vite bundle), which removes the
third-party dependency and the CSP carve-out entirely.

---

## Cross-cutting note

Symptoms 1 and 2 share a failure family: prod-only state (Google config/quota, DB migration
level, worker liveness) with good in-code logging that nobody has tailed yet. The single highest-
value next action is one SSH session running the four confirm commands above; every candidate
here is falsifiable from those logs in minutes.
