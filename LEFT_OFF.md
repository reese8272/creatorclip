# LEFT_OFF — upload→clips pipeline LIVE-verified end-to-end; LLM lane live; prod self-sufficient

> **Read this first.** Living "where we are right now" handoff for a fresh session with zero memory.
> Source-of-truth docs live in `docs/`; this file orients and points to them — it is NOT a source of truth.

**Last updated:** 2026-06-27
**Checked out:** `main` @ `705cb56` (== `origin/main` == `origin/staging`; **0 ahead / 0 behind**).
**Working tree:** clean except unrelated screenshot churn + this file. **Worktrees:** only the main checkout.
**Last prod deploy:** `705cb56` → autoclip.studio **succeeded** (Docker publish + Deploy: secret-sync →
doctor preflight → migrations → rollout → smoke, no rollback). Prod `/health` =
`{"status":"ok","postgres":"ok","redis":"ok","storage":"ok","version":"dev"}`.

---

## CURRENT FOCUS

**E2E stress testing on autoclip.studio.** The code is production-ready for the ≤100-user beta and fully
deployed; BOTH the **upload→clips pipeline** AND the LLM feature surfaces are now **live-verified end-to-end**
(not just mocked) — see "WHAT WORKS NOW". Remaining work is exercising the rest of the funnel under load and
watching logs — a **user-driven** activity (needs a logged-in creator + real videos), not a code task.

**→ NEXT ACTION (resume here):**
1. **Continue walking the funnel on autoclip.studio** (Google OAuth) — the core ingest→clips path is proven;
   now exercise the remaining live-LLM surfaces on the 6 clips just generated: Review a clip (per-clip AI
   titles + hook-rewrite #322, caption/overlay-text #323, "explain this clip" #325) · Analyze a video ·
   Title/Thumbnail/Hook suggestions · Assistant chat (#324 tools) · render a clip (9:16 reframe) · publish.
2. **Watch verbose logs while testing** (SSH alias `creatorclip-vm`, dir `/opt/autoclip`):
   `docker compose -f docker-compose.prod.yml logs -f app worker`.
   Expect `*_tokens: in=… cached_read=… out=…` per LLM call and `HTTP Request: POST …/v1/messages 200`.
   For the upload path: `ingest_video_done` → `Transcribing via deepgram` + `POST api.deepgram.com/v1/listen …200`
   → `build_signals_done` → `Generated N ranked clips` → `clips_ready` notification.
3. **(Optional, recommended) Activate the nightly live-LLM check:** the `ANTHROPIC_API_KEY` GitHub secret
   now exists, so `gh workflow run llm-e2e-nightly.yml` (or wait for the 03:00 UTC cron) runs the 22-check
   live suite automatically.
4. **(Optional nits, offered — not done):** (a) inject the deployed commit SHA into `IMAGE_SHA` so
   `/health` `"version"` stops reading `"dev"`; (b) commit a refresh of this file + `docs/PROJECT_STATE.md`.

**To re-run the live LLM harness locally any time** (needs the key in `.env` + local Redis):
```
cd /home/reese/workspace/Youtube-Video-AI-Editor && \
export ANTHROPIC_API_KEY="$(grep -E '^[[:space:]]*ANTHROPIC_API_KEY[[:space:]]*=' .env | head -1 | sed -E 's/^[^=]*=[[:space:]]*//; s/[[:space:]]*$//')" RUN_LLM_LIVE=1 && \
.venv/bin/python scripts/llm_e2e.py
```

---

## WHAT WORKS NOW (verified this session — don't re-investigate)

- **The UPLOAD→CLIPS pipeline works END-TO-END in prod (verified 2026-06-27).** A real upload ran the full
  chain live: `ingest_video` (reads source from R2) → `transcribe_video` (Deepgram nova-3, `POST
  api.deepgram.com/v1/listen …200`, `mip_opt_out=true`) → `build_signals` → `generate_clips` (Anthropic
  `POST /v1/messages 200`, `clip_scoring tokens in=… cached_write=…`) → **"Generated 6 ranked clips"** →
  `clips_ready` email. Getting here required fixing **3 stacked prod bugs the mocked suite hid** (full writeup
  in `~/.claude/ISSUES_LOG.md` ISSUE-2026-06-27-01): (1) `STORAGE_BACKEND=local` on the VM → worker (separate
  container, no shared volume) couldn't read uploads from R2 [`7f775e1`]; (2) `deepgram-sdk` was commented out
  of `requirements.txt` while it's the default backend → `ImportError` [`8db5c3c`]; (3) `words=True` is an
  invalid `PrerecordedOptions` kwarg → `TypeError` [`705cb56`]. Each is now boot-blocked/guarded by a test.
- **Storage is now mandatory + observable in prod (`7f775e1`).** Config fails fast unless `STORAGE_BACKEND=r2`
  + all `R2_*` set; deploy pins `=r2` and syncs `R2_*` from GH secrets; `doctor.py` FAILs on prod+backend≠r2;
  `/health` has an R2 bucket-HEAD `"storage"` probe; `videos.failure_reason` (migration `0036`) puts a
  humanized, secret-safe reason on the dashboard badge instead of a bare `FAILED`. ⚠️ **The 4 `R2_*` GitHub
  secrets are NOT set yet** — the VM's existing `.env` creds carried the deploy; add them (`gh secret set
  R2_…`) so a fresh VM / rotation can't drift back to the broken state.
- **The LLM works END-TO-END against the real API.** `scripts/llm_e2e.py` ran **22 passed / 0 failed** live
  (real `POST /v1/messages 200`, real token usage, `cache_read_input_tokens > 0` = server prompt-cache landing).
  The live run caught + fixed **3 real bugs mocks missed** (all deployed): `hooks` was 400-ing in prod
  (Haiku 4.5 can't use `web_search_20260209` dynamic filtering → fixed with `allowed_callers=["direct"]`);
  `titles`/`thumbnails` JSON parse failed on fenced/preamble output → `extract_json_block` helper + direct caller.
- **L20 "LLM Features & Hardening" (#318–325) shipped + deployed.** W0: model-per-task registry (no hardcoded
  model IDs), live-API E2E harness + nightly CI, SDK conformance test, usage-ledger guard + brief quota.
  W1: per-clip titles/hook-rewrite, caption/overlay-text, chat clip/outcome tools, "explain this clip".
- **Prod is self-sufficient for all AI functionality.** All 4 AI keys are GitHub secrets and the deploy's
  `Sync secrets to .env` step syncs them (guarded; never blanks a VM value): `ANTHROPIC_API_KEY`,
  `DEEPGRAM_API_KEY`, `VOYAGE_API_KEY`, `YOUTUBE_API_V3_KEY` (+ existing `STRIPE_SECRET_KEY`). Verified in the
  deploy log (`synced …`).
- **Verbose logging is wired.** `LOG_LEVEL` setting (default `INFO`) feeds both the API and worker
  `configure_logging`. INFO logs every LLM call's tokens + httpx request line + pipeline stages.
- **Scope is locked to a ≤100-user private beta** (2026-06-26): build-for-10k infra (Lane L12 K8s/GKE/KEDA,
  most of L13 scale/load, Batch API #219) is DESCOPED. Trunk green, gates: backend unit lane **1617 passed /
  0 failed**, frontend `tsc -b`+`vite` clean + vitest **207 passed**.

---

## THE ARC THAT LED HERE

1. User: change scope to <100 users; want more functionality + LLM; run an issue workflow with Sonnet 4.6.
2. Locked the ≤100-user scope (descoped the 10k infra); defined a new lane **L20** (issues 318–325) and ran it
   as two Sonnet-4.6 build waves (W0 hardening, W1 features) → merged to `main`.
3. Cleaned the repo (removed all stale wave worktrees/branches → only `main`+`staging`; parked #279 work as a tag),
   pushed `main` (auto-deploy), synced `staging`.
4. User wanted to **actually test the LLM live**. Ran `scripts/llm_e2e.py` with the real key → caught 3 prod bugs,
   fixed them, re-ran to **22/22**, deployed.
5. User's final ask: make GH Actions carry everything for prod, ensure all functionality runs on autoclip.studio
   for E2E stress testing, with verbose logging. Done: AI keys → GH secrets + guarded deploy sync; `LOG_LEVEL` knob.
6. **(2026-06-27) First real upload on prod hit `FAILED`.** Watched worker logs across three deploys and peeled
   3 stacked bugs the mocks hid — storage backend (`local`→`r2`), missing `deepgram-sdk`, invalid `words=True`
   kwarg — each fix exposing the next stage. Hardened storage to be boot-blocking + observable (`/health`
   `storage`, doctor gate, `failure_reason` badge) along the way. A real re-upload then ran clean to **6 clips +
   `clips_ready`**. Logged as ISSUE-2026-06-27-01; DECISIONS 2026-06-27 records the R2-mandatory contract.

---

## KEY COORDINATES & FACTS

| Item | Value |
|------|-------|
| Trunk | `main` == `origin/main` == `origin/staging` == `staging` @ `c7e3752`; 0/0 ahead-behind |
| Prod | `autoclip.studio` (self-managed VM = `VPS_HOST`, docker-compose, self-hosted GH runner) |
| Deploy trigger | push `main` → "Docker publish" (GHCR) → "Deploy to production" (secret-sync → migrations → rollout → smoke + auto-rollback) |
| Watch a run | `gh run list --limit 5`; `gh run watch <id> --exit-status` |
| Prod health | `curl -s https://autoclip.studio/health` (note: `"version":"dev"` — IMAGE_SHA not injected; cosmetic) |
| Backend tests | `.venv/bin/python -m pytest -m "not integration" -q` (needs local Redis; use `.venv`) |
| Frontend gates | `cd frontend && npm run build` · `npx vitest run` |
| Live-LLM harness | `RUN_LLM_LIVE=1 ANTHROPIC_API_KEY=… .venv/bin/python scripts/llm_e2e.py` (also nightly `llm-e2e-nightly.yml`) |
| GitHub secrets (names only) | `ANTHROPIC_API_KEY`, `DEEPGRAM_API_KEY`, `VOYAGE_API_KEY`, `YOUTUBE_API_V3_KEY`, `STRIPE_SECRET_KEY`, `GHCR_TOKEN`, `PRODUCTION_URL`, `VPS_HOST/PORT/USER/SSH_KEY` |
| VM-managed secrets (names only) | `DATABASE_URL`, `REDIS_URL`, `JWT_SECRET_KEY`, `TOKEN_ENCRYPTION_KEY`, OAuth client id/secret, `OAUTH_REDIRECT_URI`, R2 keys, `MAILING_ADDRESS` (in the prod VM `.env`, not GH) |
| Parked work | tag `parked/issue-279-cosign` (descoped K8s supply-chain signing) |
| Beta host (alt, chosen) | **Render** — `render.yaml` at root; runbook `docs/RENDER_DEPLOY.md` |
| Open external launch gate | **Issue 29** — Google OAuth app verification (required for YouTube scopes, any user count) |

---

## CONSTRAINTS & GOTCHAS

- **Pushing `main` auto-deploys PROD** (and runs migrations). Branch first if you don't intend to deploy.
  Convention: keep `origin/staging` == `origin/main` (push `main:staging` after every `main` push).
- **Secrets are write-only in GitHub** (you can `gh secret list` names, never read values). To rotate an AI key:
  `printf '%s' "$NEWKEY" | gh secret set ANTHROPIC_API_KEY`, then redeploy. Prod also keeps a copy in the VM `.env`.
- **Do NOT run prod at `LOG_LEVEL=DEBUG` standing:** httpx logs request HEADERS at DEBUG, which include the
  Anthropic `x-api-key`. INFO is the verbose-and-safe level. DEBUG only for short, local diagnosis.
- **This dev box has NO Docker / Postgres / ffmpeg.** Local = unit lane + frontend + the live-LLM harness only.
  Full-app E2E happens on prod (autoclip.studio); the integration lane + render path run on CI/staging.
- **Local `.env` uses `KEY = value` with spaces** (the harness command handles it); the prod VM `.env` /
  docker `env_file` is strict `KEY=value`.
- **Issue HEADERS in `docs/issues.md` can read "open" even when DONE** — trust the `## Completed` section +
  `docs/PROJECT_STATE.md` + git log, not the headers. Never squash-merge the tracker docs.

---

## POINTERS (the real source-of-truth docs)

- `docs/PROJECT_STATE.md` — status; the three 2026-06-26 notes record the scope lock, L20 delivery, and the
  live-LLM run (22/22) + 3 fixes.
- `docs/issues.md` — work queue (Lane×Wave matrix; L20 briefs #318–325; descoped lanes annotated).
- `docs/DECISIONS.md` — two 2026-06-26 entries at top (scope lock + LLM track).
- `docs/SOT.md` — architecture/stack (per-task model registry, new clip endpoints). `docs/COMPLIANCE.md` — ToS/privacy.
- `CLAUDE.md` — project rules (research current standard first; CHECK→APPROVE→BUILD→REVIEW per issue).
- Memory: `~/.claude/projects/-home-reese-workspace-Youtube-Video-AI-Editor/memory/`.
