# CreatorClip — Secrets & Config Registry

**The single source of truth for every key, secret, and config value this project uses.**
If a secret exists, it is in the table below. If it is not here, it should not exist — add it
here first.

- **Never commit a real secret.** `.env` and `*.key`/`*.pem` are gitignored. Verify before any commit.
- **Validate, don't guess.** Run the doctor (below) to see exactly what is set, valid, and reachable —
  with values redacted so output is safe to paste anywhere.
- **Rotation procedures** live in [`docs/RUNBOOKS.md`](RUNBOOKS.md).

---

## 0. Naming map (read this first)

Three names refer to **one** project. This is the historical source of confusion — here is the
canonical mapping:

| Name | What it is | Where you see it |
|------|-----------|------------------|
| **CreatorClip** | The product / brand / code name | `CLAUDE.md`, docs, Docker image `ghcr.io/reese8272/creatorclip` |
| **autoclip** | The **server directory** the app is deployed into | `/opt/autoclip` on the VM; `deploy.yml` target path |
| **agenticlip.studio** | The **public domain** | Cloudflare DNS, `OAUTH_REDIRECT_URI`, `ALLOWED_ORIGINS`, `APP_BASE_URL`, the `/health` smoke test |

> When in doubt: **image = creatorclip, folder = /opt/autoclip, URL = agenticlip.studio.**

---

## 1. Where secrets live (the five locations)

A given value may need to be set in more than one place. This is the second source of confusion.

| Location | What lives here | How to edit |
|----------|-----------------|-------------|
| **Local `.env`** | Dev values on your machine (gitignored) | edit the file |
| **VM `.env`** | Production values at `/opt/autoclip/.env` (chmod `600`) | `ssh` in, edit, `docker compose up -d` |
| **GitHub → Settings → Secrets and variables → Actions → Secrets** | CI/CD deploy credentials (encrypted) | GitHub web UI |
| **GitHub → … → Actions → Variables** | Non-secret CI config (e.g. the health-check URL) | GitHub web UI |
| **External dashboards** | The origin of each API key (Anthropic, Voyage, Deepgram, Cloudflare, Stripe, Google) | the provider's console |

**Rule of thumb:** an API key is *generated* in an external dashboard, *stored* in the VM `.env`
(production) and your local `.env` (dev), and is **never** put in a GitHub Actions secret unless CI
itself needs it. Only the deploy/CI credentials (`VPS_*`, `GHCR_TOKEN`) live in GitHub secrets.

---

## 2. Application config & secrets (`.env`)

These are read by `config.py` (pydantic-settings). **Required** vars have no default — the app
**exits on startup** if they are missing. `Secret?` = must never appear in a log line or be shared.

### Core infrastructure

| Var | Secret? | Required | What it does | Where to get it |
|-----|:------:|:--------:|--------------|-----------------|
| `DATABASE_URL` | 🔑 | ✅ | Postgres DSN (`postgresql+psycopg://…`). In production: connects as the **`creatorclip_app`** role (no `BYPASSRLS`). | Compose builds it from `POSTGRES_PASSWORD`; prod set by hand |
| `DATABASE_MIGRATION_URL` | 🔑 | – (falls back to `DATABASE_URL`) | Postgres DSN for the **`creatorclip_migrate`** role (`BYPASSRLS`). Used by Alembic and Celery worker tasks. Required in production; optional in dev. | See `docs/DEPLOYMENT.md` "RLS one-time setup" |
| `REDIS_URL` | – | ✅ | Celery broker + cache DSN | `redis://redis:6379/0` (compose) / `redis://localhost:6379/0` (local) |
| `POSTGRES_PASSWORD` | 🔑 | ✅ (compose) | Password the `postgres` container initializes with. **Not** read by `config.py` — used only by docker-compose. | You choose it; `openssl rand -hex 24` |
| `ENV` | – | – (`development`) | `development` \| `production`. Gates `/docs` and error verbosity. | Set `production` on the VM |

### Security keys

| Var | Secret? | Required | What it does | Where to get it |
|-----|:------:|:--------:|--------------|-----------------|
| `TOKEN_ENCRYPTION_KEY` | 🔑 | ✅ | Fernet key encrypting YouTube OAuth tokens at rest | `python -c "from crypto import generate_key; print(generate_key())"` |
| `JWT_SECRET_KEY` | 🔑 | ✅ | Signs session JWTs (≥32 bytes) | `openssl rand -hex 32` |
| `JWT_EXPIRY_MINUTES` | – | – (`60`) | Session lifetime | n/a |
| `ALLOWED_ORIGINS` | – | ✅ | CORS allow-list. **In production: exactly `https://agenticlip.studio`** — never `*`, never localhost. | You set it |

### AI / LLM / embeddings

| Var | Secret? | Required | What it does | Where to get it |
|-----|:------:|:--------:|--------------|-----------------|
| `ANTHROPIC_API_KEY` | 🔑 | ✅ | Claude API (DNA synthesis, scoring, briefs). Format `sk-ant-…` | console.anthropic.com → Settings → API Keys |
| `VOYAGE_API_KEY` | 🔑 | ⚠️ | Voyage embeddings → pgvector. Warn-level if empty (local-embeddings fallback). | dash.voyageai.com → API Keys |
| `LLM_TIMEOUT_SECONDS` | – | – (`120`) | Per-call Anthropic timeout | n/a |

### Transcription

| Var | Secret? | Required | What it does | Where to get it |
|-----|:------:|:--------:|--------------|-----------------|
| `TRANSCRIPTION_BACKEND` | – | – (`deepgram`) | `deepgram` \| `whisperx` \| `assemblyai`. **MVP default is `deepgram`** (no GPU). | n/a |
| `DEEPGRAM_API_KEY` | 🔑 | ⚠️ | Required when backend = `deepgram` | console.deepgram.com → API Keys |
| `ASSEMBLYAI_API_KEY` | 🔑 | ⚠️ | Required when backend = `assemblyai` | assemblyai.com → dashboard |
| `WHISPER_MODEL` | – | – (`large-v3`) | WhisperX model size (self-hosted GPU only) | n/a |

### Storage (Cloudflare R2)

| Var | Secret? | Required | What it does | Where to get it |
|-----|:------:|:--------:|--------------|-----------------|
| `STORAGE_BACKEND` | – | – (`local`) | `local` (dev) \| `r2` (prod) | n/a |
| `R2_ACCOUNT_ID` | – | ⚠️ | Cloudflare account id. Required when backend = `r2`. | Cloudflare → R2 → Overview |
| `R2_ACCESS_KEY_ID` | 🔑 | ⚠️ | R2 S3 access key | Cloudflare → R2 → Manage R2 API Tokens |
| `R2_SECRET_ACCESS_KEY` | 🔑 | ⚠️ | R2 S3 secret key (shown once at creation) | same token-creation screen |
| `R2_BUCKET` | – | ⚠️ | Bucket name (`creatorclip-beta`) | Cloudflare → R2 → Create bucket |

### Billing (Stripe)

| Var | Secret? | Required | What it does | Where to get it |
|-----|:------:|:--------:|--------------|-----------------|
| `STRIPE_SECRET_KEY` | 🔑 | ⚠️ | Server-side Stripe key (`sk_live_…`/`sk_test_…`). Empty ⇒ billing disabled. | dashboard.stripe.com → Developers → API keys |
| `STRIPE_PUBLISHABLE_KEY` | – | ⚠️ | Browser key (`pk_…`) used in `pricing.html` | same screen |
| `STRIPE_WEBHOOK_SECRET` | 🔑 | ⚠️ | Verifies webhook signatures (`whsec_…`) | Stripe → Developers → Webhooks → your endpoint |
| `APP_BASE_URL` | – | – (`localhost`) | Public base for Stripe redirect/cancel URLs. **Prod: `https://agenticlip.studio`** | n/a |
| `FREE_TRIAL_MINUTES` | – | – (`60`) | Minutes granted on first login | n/a |

### Google / YouTube OAuth

| Var | Secret? | Required | What it does | Where to get it |
|-----|:------:|:--------:|--------------|-----------------|
| `GOOGLE_OAUTH_CLIENT_ID` | – | ✅ | OAuth client id (`….apps.googleusercontent.com`) | console.cloud.google.com → APIs & Services → Credentials |
| `GOOGLE_OAUTH_CLIENT_SECRET` | 🔑 | ✅ | OAuth client secret | same Credentials screen |
| `OAUTH_REDIRECT_URI` | – | ✅ | Callback URL. **Prod: `https://agenticlip.studio/auth/callback`** — must match the Google console exactly. | you set it; register it in Google |

### Deploy / tunnel

| Var | Secret? | Required | What it does | Where to get it |
|-----|:------:|:--------:|--------------|-----------------|
| `CLOUDFLARE_TUNNEL_TOKEN` | 🔑 | ✅ (prod) | Auth token for the `cloudflared` service in `docker-compose.prod.yml`; routes `agenticlip.studio` → `app:8000` with no open inbound ports. | Cloudflare → Zero Trust → Networks → Tunnels → your tunnel → Install connector (token is in the `--token` value) |

### Engine tunables (non-secret, safe defaults)

`SOURCE_MEDIA_RETENTION_HOURS` `CLIPS_PER_VIDEO_DEFAULT` `MIN_VIDEOS_FOR_DNA` `MIN_SHORTS_FOR_DNA`
`PERSONALIZATION_THRESHOLD_LABELS` `YOUTUBE_QUOTA_DAILY_UNITS` `YTDLP_ENABLED` `UPLOAD_MAX_MB`
`LOCAL_MEDIA_DIR` — all have defaults in `config.py` and are documented in `.env.example`. None are secret.

---

## 3. GitHub Actions secrets & variables (CI/CD only)

Set at **GitHub repo → Settings → Secrets and variables → Actions**. These exist so the deploy
pipeline can reach the VM and pull the image — they are **not** app config.

| Name | Kind | What it does | Where to get it |
|------|------|--------------|-----------------|
| `VPS_HOST` | secret | VM IP/hostname (`147.182.136.107`) | DigitalOcean → Droplet |
| `VPS_USER` | secret | SSH user for deploy | the VM user you created |
| `VPS_SSH_KEY` | secret | **Private** SSH key the runner uses to reach the VM | your deploy keypair (see [`docs/ACCESS.md`](ACCESS.md)) |
| `VPS_PORT` | secret | SSH port (defaults to 22 if unset) | your sshd config |
| `GHCR_TOKEN` | secret | PAT (read:packages) so the VM can `docker login ghcr.io` and pull the private image | github.com → Settings → Developer settings → PAT |
| `PRODUCTION_URL` | **variable** | Base URL the scheduled health check probes (`https://agenticlip.studio`) | you set it |

> `GITHUB_TOKEN` (used in `docker-publish.yml`) is injected automatically by GitHub — you never create it.

---

## 4. Validate everything — the doctor

`config.py` only checks that **required vars are present**. To check that they are *valid* and
*reachable*, run the doctor. Output is **redacted** (length + last 4 chars only) — safe to share.

```bash
# presence + format + live checks for Postgres/Redis (the deploy gate)
python scripts/doctor.py

# also hit the external APIs (Anthropic, Voyage, Deepgram, R2, Stripe)
python scripts/doctor.py --full

# presence + format only, no network at all
python scripts/doctor.py --offline

# machine-readable
python scripts/doctor.py --json
```

Exit code is non-zero if any **required** check fails, so the same command gates the deploy.

---

## 5. Rotation

Step-by-step procedures are in [`docs/RUNBOOKS.md`](RUNBOOKS.md):

- **`TOKEN_ENCRYPTION_KEY`** — re-encrypt tokens with `scripts/rotate_token_key.py` *before* swapping the key.
- **`JWT_SECRET_KEY`** — rotating logs everyone out; no migration needed.

For provider keys (Anthropic, Voyage, Deepgram, R2, Stripe, Google): create the new key in the
dashboard, update `.env` on the VM (and locally), run `python scripts/doctor.py --full`, then revoke
the old key once the doctor is green.

---

## 6. Adding a new secret (the checklist)

1. Add the field to `config.py` (with a sensible default if optional).
2. Add it to `.env.example` with a one-line description.
3. Add a row to the right table **here**.
4. Add a check to `scripts/doctor.py` (presence + format, plus a live probe if it has one).
5. If CI needs it, add it as a GitHub Actions secret and document it in §3.
