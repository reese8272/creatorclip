# Runbook — BETA Deploy Gates (Issues 24, 25, 26)

> Hand-drafted W0 runbook for the three external/operational beta gates. No app code changes —
> these are executed on the **prod VM** and in the **Google Cloud / provider consoles**.
> Together they unblock **Issue 28** (beta go-live smoke test). Verify type: `external`.

Secrets are referenced **by name only** — never paste real values into git, CI logs, or this file.

---

## Issue 24 — Production environment configuration

The app is already live (`autoclip.studio`, `main==staging==origin`), so most secrets exist in
practice. This is a **verification pass** that every AC holds on the live box and that the
irreplaceable secrets are absent from git history.

**Steps (on the prod VM + GitHub UI):**
1. SSH to the prod VM. Confirm `/opt/autoclip/.env` exists and is **not** a symlink to a committed file.
2. Confirm `.env` is gitignored and absent from history:
   - `git ls-files | grep -c '^.env$'` → must be `0`
   - `git log --all -S 'TOKEN_ENCRYPTION_KEY' -- .env` → no hits
3. Verify the live values (redact before logging):
   - `ENV=production`
   - `ALLOWED_ORIGINS=https://autoclip.studio` (single entry, **no wildcard, no localhost**)
   - `OAUTH_REDIRECT_URI=https://autoclip.studio/auth/callback`
   - `APP_BASE_URL=https://autoclip.studio`
   - `TOKEN_ENCRYPTION_KEY` is a valid Fernet key (44 url-safe b64 chars). Generate if absent:
     `python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`
   - `JWT_SECRET_KEY` ≥ 32 bytes random hex. Generate if absent: `openssl rand -hex 32`
4. GitHub → Settings → Secrets and variables → Actions: confirm **`STRIPE_SECRET_KEY`** and
   **`GHCR_TOKEN`** exist (the only two `deploy.yml` reads; legacy `VPS_*` can be deleted).
5. Trigger a `workflow_dispatch` run of `deploy.yml` → must run on the **self-hosted** runner and
   reach `Smoke test` = `STATUS=ok`. (GitHub-hosted CI is red on billing — unrelated; deploy path is self-hosted.)

**Done when:**
- [ ] `/opt/autoclip/.env` complete, gitignored, no secret in git history
- [ ] `ALLOWED_ORIGINS` is the single prod origin (no wildcard)
- [ ] `STRIPE_SECRET_KEY` + `GHCR_TOKEN` present in GitHub Actions secrets
- [ ] `curl -s -o /dev/null -w '%{http_code}' https://autoclip.studio/docs` → **404** (`/docs` gated to dev)

---

## Issue 25 — External API services provisioning

Live credentials for every external dependency: Anthropic, Voyage, Deepgram, Cloudflare R2, Stripe.
The preflight validator (`scripts/doctor.py`) is the verification surface — note `/health` only probes
Postgres+Redis, **not** the external providers.

**Steps (on the prod VM):**
1. Run the full live preflight (the doctor redacts secrets, safe to capture):
   `docker compose -f docker-compose.prod.yml exec app python scripts/doctor.py --full`
   → all critical sections OK (Anthropic `models.list`, Voyage embed, Deepgram, R2 `HeadBucket`, Stripe `account.retrieve`).
2. Confirm the backend switches are set so the live checks aren't skipped:
   `STORAGE_BACKEND=r2`, `TRANSCRIPTION_BACKEND=deepgram` (or `assemblyai`).
3. Confirm Stripe is in the intended mode (test vs live) and the webhook secret matches the live endpoint.

**Done when:**
- [ ] `doctor.py --full` exits 0 against prod (all critical providers reachable)
- [ ] No key appears in any log line (doctor output redacted)
- [ ] Storage/transcription backends point at the managed providers, not `local`/`whisperx`

---

## Issue 26 — Google OAuth consent screen + beta test users

Console-only gate. The OAuth **code** path is already shipped; the consent screen must register
**exactly** the four read-only scopes the code requests — keep it read-only (do **not** add
`youtube.upload` here; that re-triggers verification — see Issue 194 / 29).

**Steps (Google Cloud Console → APIs & Services):**
1. OAuth consent screen: User type **External**, Publishing status **Testing**.
2. App name **CreatorClip**, support email set, authorized domain **autoclip.studio**.
3. Register exactly these scopes (must match `youtube/oauth.py:46-51` byte-for-byte):
   `userinfo.email`, `userinfo.profile`, `youtube.readonly`, `yt-analytics.readonly`.
4. Add each beta tester's Gmail under **Test users** (Testing allows up to 100).
5. Credentials → confirm the authorized redirect URI includes **`https://autoclip.studio/auth/callback`**
   (matches `routers/auth.py` `/callback` under `/auth`). Mismatch = `400 redirect_uri_mismatch`.
6. Confirm `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` in the VM `.env` match this project.

**Done when:**
- [ ] ≥2 testers added as Test users
- [ ] Consent screen shows exactly the four read-only scopes (no extras)
- [ ] Full flow works: `/auth/login` → Google consent → `/auth/callback` → creator row created
- [ ] Protected route returns 401 without a session; two test creators see only their own data (live DB isolation check)

---

**Next gate:** all four of {24, 25, 26, 27} green → **Issue 28** (beta go-live smoke + friend onboarding).
