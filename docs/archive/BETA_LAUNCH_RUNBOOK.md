> ⚠️ **ARCHIVED (Issue 146) — contains STALE steps.** It cites a dead migration hash
> (`b8c9d0e1f2a3`; head is now `0024`) and a dead deploy branch. The still-useful **Google
> OAuth closed-beta onboarding** steps were merged into `docs/ACCESS.md`; for deploy/branching
> follow `docs/DEPLOYMENT.md` + `docs/BRANCHING.md`. Do not follow this doc for current procedure.

# AutoClip — Beta Launch Runbook (human steps)

A plain-English, do-this-then-check-that checklist to take AutoClip from "code is
ready" to "an invited creator can log in and use it." Written for a human, not the
code. Do the steps **in order** — each one has *what it is*, *why it matters*, *exactly
what to do*, and *how to confirm it worked*.

> Scope: a **closed beta** (a handful of creators you invite by email). Wider/open beta
> and public launch need more (Google verification, load testing, legal review) — those
> are flagged at the end, not here.

**Key coordinates** (from `LEFT_OFF.md` / `docs/ACCESS.md` — adjust if yours differ):
- Public URL: `https://autoclip.studio` · health check: `https://autoclip.studio/health`
- Server: `ssh creatorclip-vm` (`147.182.136.107`), app dir `/opt/autoclip/`
- Deploy: pushing to the `main` branch triggers the CD pipeline (`deploy.yml`)
- Latest DB migration that must be applied: **`b8c9d0e1f2a3`**

---

## Step 0 — Set the one prod setting the pipeline needs (transcription)

**What:** A transcription backend + key. (A metrics token is *optional* — see the note.)

**Why:** The clip pipeline can't turn a video into clips without transcribing the audio
first. No transcription key = videos ingest but never produce clips.

**Do this** — on the server, edit `/opt/autoclip/.env` (or your secrets manager) and set:

```bash
# Transcription — the simplest beta path is a hosted backend:
TRANSCRIPTION_BACKEND=deepgram
DEEPGRAM_API_KEY=<your Deepgram API key>
#    (or TRANSCRIPTION_BACKEND=assemblyai + ASSEMBLYAI_API_KEY=...)
```

**Optional — metrics:** `/metrics` is an internal monitoring page. If you don't run
Prometheus yet, **do nothing** — in production it auto-disables itself safely when no
token is set, so it's never exposed and the deploy can't break over it. If you *do* want
to scrape it, set `METRICS_TOKEN=$(openssl rand -hex 32)` and give that token to your
scraper.

**Confirm:** `grep -E 'TRANSCRIPTION_BACKEND|DEEPGRAM_API_KEY' /opt/autoclip/.env`
shows your values.

---

## Step 1 — Confirm every other prod secret is set

**What:** The full list of values the app needs in production.

**Why:** A missing secret either fails startup (fast, obvious) or fails silently at the
first use (slow, confusing). Better to check now.

**Do this** — there's a built-in checker. On the server:

```bash
cd /opt/autoclip && docker compose exec app python scripts/doctor.py --full
```

It prints a table with everything **redacted** (safe to screenshot). The must-haves:
`ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, the transcription key (Step 0), `R2_*`,
`TOKEN_ENCRYPTION_KEY`, `JWT_SECRET_KEY`, `ALLOWED_ORIGINS` (set to your domain), and
`METRICS_TOKEN` (Step 0). `STRIPE_*` only matters if you charge during beta — you can
skip it and run on free trial minutes.

**Confirm:** every required row shows `✓` (not `✗`).

---

## Step 2 — Deploy the latest code and verify it landed

**What:** Get the current code (these fixes live on the `claude/hopeful-dirac-g0M3x`
branch) onto production, and confirm the database upgraded.

**Why:** None of the recent work is live until the server redeploys and runs the DB
migrations. Migration `b8c9d0e1f2a3` adds an index the DNA build depends on.

**Do this:**
1. Merge the work into `main` (this triggers the deploy). *(Ask me to open a PR if you
   want a review first.)*
2. Wait for the deploy pipeline to finish (GitHub → Actions tab → the `deploy` run goes
   green).
3. Migrations run automatically on deploy. Confirm the DB is at the latest revision:

```bash
ssh creatorclip-vm "cd /opt/autoclip && docker compose exec app .venv/bin/alembic current"
# expect:  b8c9d0e1f2a3 (head)
```

**Confirm:**
```bash
curl -fsS https://autoclip.studio/health
# expect:  {"status":"ok","postgres":"ok","redis":"ok"}
```
If health is `degraded` or alembic shows an older revision, check
`docker compose logs --tail 100 app` before continuing.

---

## Step 3 — Let your testers actually log in (Google OAuth)

**What:** Right now the Google sign-in app is in **"Testing" mode**, which only lets
explicitly-listed Google accounts log in.

**Why:** If you skip this, a tester clicking "Connect YouTube" just gets blocked. This is
the single most common "why can't they log in?" cause.

**Do this:**
1. Go to **Google Cloud Console → APIs & Services → OAuth consent screen**.
2. Scroll to **Test users → + Add users**. Add each tester's **Google account email**
   (the one tied to their YouTube channel). Up to 100.
3. Save.

**⚠️ Important caveat to understand (don't skip reading this):** In Testing mode, Google
**expires each tester's connection after 7 days** — after a week they'll have to click
"Connect YouTube" again. For a short beta that's fine. For a multi-week beta you have two
options:
- **(a) Accept weekly re-connect** — simplest; just tell testers it may ask them to
  reconnect weekly.
- **(b) Submit the app for Google verification** — removes the 7-day limit and the
  "unverified app" warning screen, but the review (sensitive YouTube scopes) can take
  **days to weeks**. Start it early if you want a long beta.

**Confirm:** From a tester's Google account (or a test account you added), go to
`https://autoclip.studio`, click **Connect YouTube**, and you reach the AutoClip
dashboard with their channel name shown.

---

## Step 4 — Make sure the consent screen links your policies

**What:** Your Privacy Policy and Terms pages, linked on the Google sign-in screen.

**Why:** Google requires a privacy policy link to show the consent screen, and you're
handling real creators' YouTube data — they should see the terms. The draft pages already
exist in the app.

**Do this:**
1. The pages are live at `https://autoclip.studio/static/privacy.html` and
   `.../static/tos.html` once deployed.
2. In **Google Cloud Console → OAuth consent screen → App information**, set the
   **Privacy Policy URL** and **Terms of Service URL** to those links. Save.

**Confirm:** open both URLs in a browser — they render (not 404). They're marked "draft —
legal review pending"; that's acceptable for a closed beta (get a lawyer pass before a
public launch).

---

## Step 5 — Do one full end-to-end smoke test yourself

**What:** Walk the whole product once, as a tester would, on production.

**Why:** Passing automated tests proves the pieces work; this proves the *whole thing*
works for a real human on real infrastructure. Do this before you invite anyone.

**Do this** — at `https://autoclip.studio`, in order:
1. **Connect YouTube** → you land on the dashboard, channel name shows (no "undefined").
2. Open **Set up** → step 2 "Channel data" shows a friendly readout (✓/• per type), not
   raw JSON.
3. Click **Build Creator DNA** → it says it's analysing (no task id), then flips to
   "your brief is ready" within ~30–60s.
4. **Review & confirm** the brief on the Profile page → click **Confirm & activate**.
5. Back on the dashboard, **Link a video** (paste one of your own YouTube IDs) → wait for
   it to ingest (status goes pending → done).
6. **Generate clips** on that video → clips appear.
7. **Render** one clip → it completes and is playable.
8. Open **Review** → leave a thumbs-up/down on a clip.

**Confirm:** every step completes without an error message. If a step stalls, check
`docker compose logs --tail 200 app worker` and tell me what you see — I can diagnose
from the log.

---

## Step 6 — Invite your testers

**What:** Send the invite.

**Do this:** email your 2–5 testers with: the URL (`https://autoclip.studio`), a one-line
"connect your channel, build your DNA, link a video, tell me what breaks," and (if you
chose option (a) in Step 3) a heads-up that it may ask them to reconnect weekly. Ask them
to report anything confusing — beta is for finding the rough edges.

**Confirm:** at least one tester gets through Step 5's flow on their own channel.

---

## What's deliberately NOT in this runbook (and why)

These are **not** needed for a small closed beta — do them before widening or going
public:
- **Google OAuth verification** — only needed to remove the 7-day limit / unverified
  warning at scale (Step 3 option b).
- **Load test behind PgBouncer** — proves the app holds up at hundreds of concurrent
  users; a handful of testers won't hit those limits.
- **Legal review of ToS/Privacy** — the drafts are fine to link for a closed beta.
- **Paid billing / plan tiers** — beta runs on free trial minutes.
- **A short tail of low-severity code items** — tracked in `docs/issues.md` (Issues
  76/77); none are data-loss or security issues.

---

## If something breaks — fast triage

| Symptom | First thing to check |
|---|---|
| Deploy won't start | `docker compose logs --tail 100 app` — most likely a missing *required* secret (Step 1), e.g. `TOKEN_ENCRYPTION_KEY` or `ANTHROPIC_API_KEY`. (A missing metrics token will NOT crash it — it just disables /metrics.) |
| `/health` says `degraded` | Which dependency: the JSON shows `postgres`/`redis` status. |
| Tester can't log in | Are they added as a Test User (Step 3)? Right Google account? |
| "Build DNA" never finishes | `docker compose logs --tail 200 worker` — check the transcription key (Step 0) and that the Celery worker is running. |
| Clips never render | Worker logs — ffmpeg present? R2 keys set? |

Send me the relevant log tail and I'll diagnose.
