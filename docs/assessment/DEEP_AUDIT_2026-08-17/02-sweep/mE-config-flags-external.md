# Modality E — Config, flags, and external registration

**Sweep date:** 2026-08-17 · **Tree:** `main` @ `1def133` · **Interpreter:** `.venv/bin/python` (3.12.7) only.
**Target class:** *an intermediate layer reports success without exercising the thing it claims to verify.*
**Scope:** `config.py` (214 settings), `.env.example`, `flags.py`, `scripts/doctor.py`, `deploy.yml`,
the three compose files, `render.yaml`, the Helm values, and every seam where a third party is
configured to call this app.

**Method.** Three AST/grep scripts over the tree (kept in the session scratchpad, all read-only):
(1) parse `Settings` and symmetric-diff against `.env.example`; (2) parse `Settings` and `git grep -w`
every field name across the tree with `config.py`/`.env.example`/`docs/` excluded, then re-check the
lowercase `@property` accessors by hand; (3) parse `Settings` for off/empty defaults and cross-check
each against `deploy.yml`, `docker-compose.prod.yml`, `docker-compose.staging.yml`, `render.yaml`,
`deploy/charts/**`. Then a manual pass over every external registration point.

**Honest yield: 11 candidates, 10 of them with a repro.** Four are HIGH and I believe **E1, E2, E3 and
E7 are each an independent instance of the named class**, not variants of one. E1 additionally
*corrects a factual claim in the Phase-1 pack* (`d08-deploy-observability.md:153`). E7 is the most
compact — two green tests named after cost accounting that assert `a*b == a*b` over a production code
path that does not exist.

**Deliberately not re-reported** (already in Phase 1 / `AUDIT_KNOWN_ISSUES.md`): `BACKUP_R2_BUCKET`
unset; `METRICS_TOKEN` unset; Sentry/OTel dormant; the `CC_JWT_SECRET` silent-skip in the prod smoke;
`stripe.WebhookEndpoint.list()` reconciliation (already `d09-billing.md:116` and `d10:337`);
`render.yaml`'s `VERBOSE_LOGGING` pair; the `CELERY_SOFT_TIME_LIMIT_S` / `YOUTUBE_PUBLISH_PRIVACY`
`.env.example` gaps (I do report the **five further** ones nobody has listed).

---

## E1 — The deploy "Preflight check" never runs a single external-API probe (HIGH)

**Evidence:** `.github/workflows/deploy.yml:279` and `scripts/deploy.sh:97` both invoke

```
docker compose -f docker-compose.prod.yml run --rm app python scripts/doctor.py
```

with **no `--full`**. `scripts/doctor.py:461` gates the five external probes behind `if full:`:

```python
if not offline:
    sections.append(("Live — internal", [_live_postgres(...), _live_redis(...)]))
    if full:                                   # doctor.py:461
        sections.append(("Live — external APIs", [
            _live_anthropic(...), _live_voyage(...), _live_deepgram(...),
            _live_r2(...), _live_stripe(...)]))
```

**What the step claims to verify.** A GitHub Actions step literally named **"Preflight check"**, sited
between `docker pull` and `alembic upgrade head`, that gates every production deploy. `docs/SECRETS.md`,
`docs/ACCESS.md:278` and `LEFT_OFF.md:114` all treat "run the doctor" as the credential-verification
ritual. `docs/assessment/.../01-domains/d08-deploy-observability.md:153` states, as fact, that
*"`scripts/doctor.py` (526 lines, the deploy preflight) live-probes Postgres, Redis, Anthropic, Voyage,
Deepgram, R2 and Stripe."* **It does not — not on any deploy, and not in any workflow.**

**Why it does not verify that.** Without `--full` the doctor performs *string* checks only (presence,
prefix, suffix, URL scheme) plus `SELECT 1` on Postgres and `PING` on Redis. `--full` appears in
**zero** automation — every hit is prose:

```
$ git grep -n -- "--full" .github/ scripts/ | grep -i doctor
scripts/doctor.py:16:    python scripts/doctor.py --full     # also probe Anthropic, Voyage, Deepgram, R2, Stripe
scripts/doctor.py:500:    parser.add_argument("--full", action="store_true", help="also probe external APIs")
      # ^ only its own docstring and its own argparse declaration. No caller anywhere.

$ git grep -rn "doctor.py --full" | cut -d: -f1 | sort -u
LEFT_OFF.md  docs/ACCESS.md  docs/AUDIT_BRIEF.md  docs/DECISIONS.md  docs/GO_LIVE.md
docs/OFF_COURSE_BUGS.md  docs/PROJECT_STATE.md  docs/SECRETS.md
      # ^ eight prose files. Zero workflows, zero scripts.
```

**This is the second layer of the 10-week Stripe outage, still open.** `OFF_COURSE_BUGS.md:148`
root-caused that the Stripe probe bypassed the app's own client, and on 2026-08-13 `_live_stripe` was
rewritten to drive `billing.stripe_client._STRIPE` (`doctor.py:398-411`) with three probe-integrity
tests. That fix is real — **and it has never executed on a deploy**, because the probe it lives in is
`--full`-only. The same is true of the `_live_r2` fix through `worker.storage._r2`.

**Repro (run; passes with every external credential bogus and exits 0):**

```bash
env -i PATH=/usr/bin:/bin HOME=$HOME ENV=production \
 DATABASE_URL='postgresql://u:p@h:5432/d' REDIS_URL='redis://h:6379/0' \
 ALLOWED_ORIGINS='https://autoclip.studio' POSTGRES_PASSWORD=x TRANSCRIPTION_BACKEND=deepgram \
 JWT_SECRET_KEY="$(head -c 48 /dev/urandom | base64 | tr -d '\n')" \
 TOKEN_ENCRYPTION_KEY="$(.venv/bin/python -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')" \
 ANTHROPIC_API_KEY='sk-ant-COMPLETELY-BOGUS' VOYAGE_API_KEY='pa-BOGUS' \
 DEEPGRAM_API_KEY='0123456789abcdef0123456789abcdef01234567' \
 STORAGE_BACKEND=r2 R2_ACCOUNT_ID=bogus R2_BUCKET=bogus R2_ACCESS_KEY_ID=bogus R2_SECRET_ACCESS_KEY=bogus \
 STRIPE_SECRET_KEY='sk_live_TOTALLY_BOGUS' STRIPE_WEBHOOK_SECRET='whsec_bogus' STRIPE_PUBLISHABLE_KEY='pk_live_bogus' \
 GOOGLE_OAUTH_CLIENT_ID='x.apps.googleusercontent.com' GOOGLE_OAUTH_CLIENT_SECRET='y' \
 OAUTH_REDIRECT_URI='https://autoclip.studio/this/route/does/not/exist' \
 CLOUDFLARE_TUNNEL_TOKEN='tok' \
 .venv/bin/python scripts/doctor.py --offline; echo "EXIT=$?"
```

```
  23 ok · 0 warn · 0 fail · 0 skipped
  Result: PASS
EXIT=0
```

(`--offline` additionally drops the PG/Redis probes; the deploy invocation keeps those two and drops
the other five. Either way, a revoked Anthropic key, an expired R2 credential, or a broken Stripe
transport ships to production behind a green "Preflight check".)

**Secondary defect in the same function:** `has_failures()` (`doctor.py:477`) counts **only**
`Status.FAIL`. Every `_live_*` probe returns `Status.SKIP` when its key is absent, and `SKIP` never
fails the gate — the identical shape to `run_layer0.py`'s tool-skip hole (`process-map.md` §1).

---

## E2 — The staging-parity gate structurally cannot exercise render or object storage, and the test named `..._parity` does not check that (HIGH)

**Evidence:** `docker-compose.staging.yml:131` vs `docker-compose.prod.yml:46,72`; `worker/celery_app.py:71-84`;
`tests/test_ci_config.py:298`.

**What it claims to verify.** `deploy.yml:31-147` — job name **"Staging gate (data-bearing DB)"**,
`needs:` by the prod job, described in `process-map.md` §8 as *"the strongest gate in the pipeline."*
`tests/test_ci_config.py:298 test_staging_prod_compose_parity` exists specifically so *"a version skew
would [not] make the gate's green meaningless."*

**Why it does not verify that.** Three divergences none of which the parity test looks at:

1. **No render consumer.** `worker/celery_app.py:84` routes five tasks to the dedicated `render` queue:
   `render_clip`, `render_video_clips`, `clean_clip`, `edit_clip`, `render_summary`. Prod runs two
   workers — `worker … -Q celery` (`prod:46`) and `render-worker … -Q render -n render@%h` (`prod:72`).
   **Staging has one worker with no `-Q` at all** (`staging:131`: `celery … --concurrency=2`), so it
   consumes only the default queue. Every render task enqueued on `ccstage` returns a task id, the API
   answers 202, and the message sits in the `render` queue forever with no consumer.
2. **Different storage backend.** Staging pins `STORAGE_BACKEND: local` (`staging:89,126`); prod pins
   `STORAGE_BACKEND=r2` authoritatively in `deploy.yml:250-256` and `config.py:1173` *hard-fails* prod
   on anything else. `routers/videos.py:288-293` (`_require_multipart_mode`) 409s the entire
   browser→R2 presigned-multipart upload path when the backend is not `r2` — so Issue 395's upload
   path, the first thing a new user touches, cannot run in staging by construction.
3. **No `beat` service.** Prod runs `beat` (`prod:82`); staging does not. Nothing scheduled — including
   `run_lifecycle_scan` (E3) — is ever exercised by the gate.

The parity test asserts exactly four things: the `postgres` image string, the `redis` image string, that
staging's app/worker image is `${STAGING_IMAGE:-creatorclip:staging}`, and that `pgbouncer` is absent
from prod. **It asserts nothing about the service set, the queue topology, `STORAGE_BACKEND`, or
`command:`.** Its scope is a hand-written pair of dict keys — the "hand-maintained literal registry"
shape applied to an environment-parity claim.

The gate's smoke is `llm_harness.py --flow core` = `health, auth_me, videos_list, dna, insights,
billing_balance, videos_envelope_shape`. No render. No upload.

**Repro (state where the gate is green while the thing is dead), no prod access needed:**

```bash
.venv/bin/python - <<'PY'
import yaml
p = yaml.safe_load(open('docker-compose.prod.yml'))['services']
s = yaml.safe_load(open('docker-compose.staging.yml'))['services']
print("prod services   :", sorted(p))
print("staging services:", sorted(s))
print("prod worker cmd :", p['worker']['command'])
print("prod render cmd :", p['render-worker']['command'])
print("stg  worker cmd :", s['worker']['command'])
print("stg STORAGE     :", s['app']['environment']['STORAGE_BACKEND'])
from worker.celery_app import RENDER_TASKS, RENDER_QUEUE
print("tasks with NO consumer in staging:", RENDER_QUEUE, list(RENDER_TASKS))
PY
```

Then: `pytest tests/test_ci_config.py::test_staging_prod_compose_parity -q` → **passes**.

---

## E3 — Every transactional email is recorded `status=sent` over a console sink, and the lifecycle sweep short-circuits into a green Celery success (HIGH)

**Evidence:** `config.py:993` (`NOTIFY_BACKEND: str = "console"`), `config.py:1010`
(`MAILING_ADDRESS: str = ""`), `worker/tasks.py:4556-4561`, `worker/tasks.py:6701-6710`,
`worker/tasks.py:6730-6738`, `notify/mailer.py:183-189`, `worker/schedule.py:77`.

**What it claims to verify / report.** `notification_deliveries` is the app's own record that an email
went out; it is also the **idempotency latch**. `run_lifecycle_scan` is a beat-scheduled Celery task
whose success is counted by `CELERY_TASKS_TOTAL{status="success"}`.

**Why it does not.** Two stacked layers:

1. `worker/tasks.py:6730-6738` inserts the delivery row with `status=NotificationDeliveryStatus.sent`
   **before** the mailer is called (the send is deferred to step 8 "after the session closes"). With
   `NOTIFY_BACKEND="console"` — the config default — `notify/mailer.py:183` dispatches to
   `_send_console`, which is a `logger.info(...)` and *"never calls any external service."* No
   exception is raised, so the row is never downgraded to `failed`. The DB therefore says **sent** for
   `balance_low`, `reauth_required`, `dna_built`, `trial_ending` and `refund_issued` while nothing left
   the box. Worse, the `UNIQUE dedupe_key` short-circuit at `worker/tasks.py:6741-6759` only retries
   rows whose status is `failed` — so a later switch to `NOTIFY_BACKEND=resend` can **never** re-send
   any of them. The dead path is permanently latched by its own success record.
2. `worker/tasks.py:4556`: `if not settings.MAILING_ADDRESS: logger.info(...); return` — the canonical
   silent-skip. The whole welcome / first-clip-nudge / re-engagement sweep no-ops at INFO level and the
   task reports SUCCESS.

**Why this is a config finding, not a code finding.** Neither `NOTIFY_BACKEND` nor `MAILING_ADDRESS`
appears in **any** deployment artifact:

```
$ grep -rn "NOTIFY_BACKEND\|MAILING_ADDRESS" .github/workflows/ docker-compose*.yml deploy/
render.yaml:27:      - key: NOTIFY_BACKEND        # render.yaml is the UNUSED path
```

`.env.example:332` ships `NOTIFY_BACKEND=console` and `:345` ships `MAILING_ADDRESS=` (empty).
`config.py:1130 _require_prod_secrets` does not check either. `scripts/doctor.py` has no notifications
section at all. `/health` does not report the mail backend. **The production value is unknowable from
this repo** and the failure mode is total silence with a `sent` row.

**Repro:**

```bash
.venv/bin/python - <<'PY'
import inspect, notify.mailer as m, worker.tasks as t
from config import settings
print("NOTIFY_BACKEND default:", settings.NOTIFY_BACKEND, "| MAILING_ADDRESS:", repr(settings.MAILING_ADDRESS))
src = inspect.getsource(t.send_notification)
i = src.index("status=NotificationDeliveryStatus.sent")
j = src.index("mailer.send") if "mailer.send" in src else len(src)
print("row written as 'sent' BEFORE the send:", i < j)
print(inspect.getsource(m._send_console))
PY
```

Then read the operator-facing consequence: `pytest tests/test_lifecycle_email.py -q` is green, and
`tests/test_lifecycle_email.py:133` is a test whose *docstring* is *"with MAILING_ADDRESS unset the
whole sweep is a no-op"* — the suite pins the dead state as correct.

---

## E4 — The R2 bucket CORS policy is externally-registered state reconciled with nothing (MEDIUM-HIGH)

**Evidence:** `scripts/r2_set_cors.py:29-59`; `config.py:86` `ALLOWED_ORIGINS`; `LEFT_OFF.md:233`.

**What claims to be verified.** `docs/GO_LIVE.md:72` marks the "can a new user upload a real-world file"
row **CODE-GREEN** citing *"bucket CORS set via `scripts/r2_set_cors.py` (echo verified)"*.
`docs/PROJECT_STATE.md:791` records it as done infra.

**Why it does not verify that.** The bucket's `CORSRules.AllowedOrigins` is set from **`sys.argv`**
(`r2_set_cors.py:29`), not from `settings.ALLOWED_ORIGINS`. Nothing anywhere compares the two:

```
$ git grep -rn "get_bucket_cors" -- ':!scripts/r2_set_cors.py'   # → nothing
$ git grep -rln "r2_set_cors" -- ':!docs/' ':!LEFT_OFF.md'        # → nothing (no workflow, no test, no doctor probe)
```

The script is run by a human, once per environment, with the origin list retyped each time.
`LEFT_OFF.md:233` already encodes the mitigation as a ritual: *"Before any fresh-upload drill: run
`.venv/bin/python scripts/r2_set_cors.py https://autoclip.studio`."*

**The failure is silent on the server side by construction.** `POST /videos/upload/init` mints presigned
part URLs and returns 200 — the app has done its job. The browser's `PUT` to
`https://<acct>.r2.cloudflarestorage.com/...` is then blocked by the *bucket's* CORS preflight, which
this app never sees. The documented symptom (`r2_set_cors.py:5-7`) is *"the upload stalls at 100% with
opaque CORS errors."* Add a second origin to `ALLOWED_ORIGINS` (a staging hostname, an apex/`www`
split, a domain change) and the whole upload path dies for that origin with every server-side signal
green.

**Repro / detection command** (read-only, needs the R2 creds; this is the check that does not exist):

```bash
.venv/bin/python - <<'PY'
from worker.storage import _r2
from config import settings
rules = _r2().get_bucket_cors(Bucket=settings.R2_BUCKET).get("CORSRules", [])
allowed = {o for r in rules for o in r.get("AllowedOrigins", [])}
app_origins = set(settings.ALLOWED_ORIGINS if isinstance(settings.ALLOWED_ORIGINS, list)
                  else str(settings.ALLOWED_ORIGINS).split(","))
print("bucket:", allowed); print("app   :", app_origins)
print("MISSING FROM BUCKET:", app_origins - allowed)
print("ETag exposed:", any("ETag" in r.get("ExposeHeaders", []) for r in rules))
PY
```

---

## E5 — `OAUTH_REDIRECT_URI`'s path is never reconciled against the app's route table (MEDIUM)

**Evidence:** `scripts/doctor.py:273` + `:261`; `routers/auth.py:32,252`; `config.py:83`.

**What it claims to verify.** `doctor.py`'s "Google / YouTube OAuth" section is the only automated check
on the single most important externally-registered URL in the product — the redirect URI registered in
the Google Cloud Console.

**Why it does not verify that.** `doctor.py:261` builds the validator as
`redirect_fmt = fmt_url("https") if prod else fmt_url("http", "https")`. It checks the **scheme and
nothing else**. The path is unexamined. The app serves the callback at `/auth/callback`
(`routers/auth.py:32` prefix + `:252`), and nothing in the tree ties that string to the setting:

```
$ git grep -rln "app.routes" tests/     # test_flags.py test_gpc.py test_response_models.py — none about redirect/webhook paths
```

**This is exactly the `/webhooks/stripe` vs `/billing/webhook` shape** (`AUDIT_BRIEF` incident #1,
a 404 for its entire life). Google accepts whatever exact string is registered on its side and
redirects there; if the app's route moves, or a router prefix changes, or the VM `.env` carries a stale
path, every sign-in lands on a 404 and the doctor stays green. The Phase-1 pack proposes the fix in one
line (`d10-test-strategy.md:209`) but only for Stripe, with the OAuth case as an aside — it is not filed.

**Repro:** the E1 command above sets
`OAUTH_REDIRECT_URI='https://autoclip.studio/this/route/does/not/exist'` and the doctor prints
`✓ OAUTH_REDIRECT_URI` / `Result: PASS` / `EXIT=0`.

---

## E6 — `CSRF_FETCH_METADATA_ENABLED` is a security control that defaults off, is set by no deploy artifact, and has a green test asserting it is off (MEDIUM)

**Evidence:** `config.py:746`; `.env.example:328`; `auth.py:37-40`; `tests/test_security_baselines.py:263-294`.

**What claims to be verified.** Issue 230 shipped a Sec-Fetch-Site CSRF defence on every mutating route,
with tests. `.env.example:328` says, in the comment, *"Set true in production."*

**Why it does not.** `CSRF_FETCH_METADATA_ENABLED: bool = False` and the string appears in **no**
deployment artifact:

```
$ grep -rn "CSRF_FETCH_METADATA" .github/workflows/ docker-compose*.yml render.yaml deploy/
(no output)
```

`config.py:1130 _require_prod_secrets` does not check it (its production requirement list is a literal
tuple of `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` plus the four `R2_*`). `doctor.py` does not check
it. There is no warn, no log line, no `/health` field. The defence's production state depends entirely
on a human having hand-edited `/opt/autoclip/.env`, and is **unknowable from this repo** — the same
posture that left `BACKUP_R2_BUCKET` unset since inception.

Meanwhile `tests/test_security_baselines.py:263 test_csrf_disabled_in_dev` asserts that a cross-site
`POST /videos/link` is **not** blocked, and it is green in the required Unit lane. The suite therefore
carries a passing test attesting to the control being inert, and no test at all attesting that
production has it on.

**Repro:** `.venv/bin/python -c "from config import Settings; print(Settings().CSRF_FETCH_METADATA_ENABLED)"` → `False`;
`pytest tests/test_security_baselines.py::test_csrf_disabled_in_dev -q` → passes.

---

## E7 — Two "price-book math" tests assert `a*b == a*b` over a cost path that does not exist; transcription/embedding/storage/render cost is never recorded (HIGH)

**Evidence:** `tests/test_usage_ledger.py:95-131`; `config.py:179,182,185,188,191,195,199`.

**What it claims to verify.** `tests/test_usage_ledger.py:91` heads the block *"Unit: extended
price-book math (Issue 289)"*. `test_estimate_cost_deepgram_minutes` and
`test_estimate_cost_mixed_llm_and_deepgram` read as cost-accounting coverage for the non-LLM half of
the ledger. `config.py:199` documents `PRICE_BOOK_VERSION` as *"a version mismatch between a stored
cost_estimate and this stamp signals a rate-change event."*

**Why they do not verify that.** Neither test calls any production function for the Deepgram part —
the test body *is* the arithmetic:

```python
expected_usd = minutes * settings.COST_PER_MIN_DEEPGRAM   # tests/test_usage_ledger.py:103
assert abs(expected_usd - 0.582) < 1e-9
```

That is `60 * 0.0097 == 0.582`, a tautology over two constants. The "mixed" test hand-rolls the sum the
same way (`:128`). **There is no production code that multiplies minutes by `COST_PER_MIN_DEEPGRAM`:**

```
$ git grep -n "COST_PER_MIN_DEEPGRAM" -- '*.py'
config.py:179   tests/test_usage_ledger.py:98,103,105,128,152
$ git grep -n "deepgram\|transcription" billing/
(no output)
```

Sweep result (script 2) — settings referenced **nowhere** outside `config.py`/`.env.example`/`docs/`:

| Setting | `config.py` | Documented in `.env.example` | Read by |
|---|---|---|---|
| `COST_PER_MTOK_VOYAGE` | :182 | :40 | nothing |
| `COST_PER_GB_MO_R2` | :185 | :41 | nothing |
| `COST_PER_M_R2_CLASS_A` | :188 | :42 | nothing |
| `COST_PER_M_R2_CLASS_B` | :191 | :43 | nothing |
| `COST_PER_RENDER_CPU_S` | :195 | :44 | nothing |
| `COST_PER_MIN_DEEPGRAM` | :179 | :39 | **tests only** |
| `PRICE_BOOK_VERSION` | :199 | — | **tests only** (`:138` asserts it is a non-empty string; nothing stores or compares it) |
| `RECAP_TARGET_DURATION_MIN_S` | :567 | :258 | nothing (`_MAX_S` is used) |

Consequence: the spend guard and `usage_ledger` account for **Anthropic tokens only**. Deepgram minutes
— plausibly the largest per-job non-LLM cost — plus Voyage embeddings, R2 storage/ops and render CPU
are invisible to `billing/spend_guard.py`, while a "price-book" test block and a rate-change sentinel
report green. `PRICE_BOOK_VERSION` is a signal with no receiver.

**Repro:** `pytest tests/test_usage_ledger.py::test_estimate_cost_deepgram_minutes -q` → passes; then
`git grep -n "COST_PER_MIN_DEEPGRAM" billing/ worker/ routers/` → no hits.

---

## E8 — `MAX_SNAP_S` / `SENTENCE_BOUNDARY_MIN_PAUSE_MS` are documented operator knobs on a code path production never enters — and a structural tripwire test pins the dead signature (MEDIUM)

**Evidence:** `config.py:314,318`; `.env.example:315-316`; `clip_engine/candidates.py:280-286,381-398`;
`clip_engine/ranking.py:322-333`; `tests/test_merge.py:484-504`; `tests/test_clip_engine.py:867-930`.

**What claims to be verified.** `.env.example:315-316` presents both as live tunables of the
sentence-boundary snapper (Issue 127, cited by principle #12). Six tests in `tests/test_clip_engine.py`
exercise `snap_to_sentence_boundary` directly.

**Why it does not.** The snapping block at `clip_engine/candidates.py:381-398` is guarded by
`if words:`, and the **only** production caller passes no `words`:

```python
# clip_engine/ranking.py:324-327
extract_candidates(timeline, signal_pool_max, container_duration_s=container_duration_s)
```

(the comment two lines above says so explicitly: *"extract_candidates no longer receives words —
sentence_snap is the single snapping authority"*, Issue 428). Snapping moved to
`clip_engine/sentence_snap.py`, which uses its own module constant `SENTENCE_SNAP_MAX_S = 10.0`
(`sentence_snap.py:34`) — **not** the setting. So `settings.MAX_SNAP_S` and
`settings.SENTENCE_BOUNDARY_MIN_PAUSE_MS` are read by nothing anywhere in the tree; an operator who
tunes them in `/opt/autoclip/.env` changes nothing, silently.

Compounding: `tests/test_merge.py:484 test_extract_candidates_is_byte_untouched` is an explicit
*structural tripwire* that asserts the parameter list still contains `words`, `min_pause_ms` and
`max_snap_s` — a green guard protecting the shape of a dead path, which is also what makes the drift
invisible.

**Repro:**

```bash
git grep -n -w "max_snap_s\|min_pause_ms" -- config.py clip_engine/ranking.py   # → config comment only
.venv/bin/python -c "
import inspect, clip_engine.ranking as r
s=inspect.getsource(r); i=s.index('extract_candidates(')
print(s[i:i+140])"
pytest tests/test_merge.py::test_extract_candidates_is_byte_untouched -q   # passes
```

---

## E9 — `.env.example` ↔ `config.py` parity: the actual symmetric diff (MEDIUM, low blast radius but this is the drift generator)

Nothing enforces this (`process-map.md` §7 confirms: one grep hit for `env.example` in `tests/`, in
`test_beat_ha.py`, unrelated). Measured on this tree: **214 `Settings` fields, 213 `KEY=` lines in
`.env.example`** (212 uncommented). The near-match is coincidence.

**In `config.py`, absent from `.env.example` (7):**

| Setting | Line | Notes |
|---|---|---|
| `ANTHROPIC_MODEL_CLIP_TITLES` | :130 | **new** — per-feature model override, undocumented |
| `ANTHROPIC_MODEL_CLIP_CAPTIONS` | :131 | **new** |
| `ANTHROPIC_MODEL_CLIP_EXPLAIN` | :132 | **new** |
| `COST_CACHE_WRITE_MULTIPLIER` | :172 | **new** — money path |
| `MAX_INGESTED_CHANNEL_TITLE_CHARS` | :360 | **new** |
| `CELERY_SOFT_TIME_LIMIT_S` | :753 | already in `AUDIT_KNOWN_ISSUES` §E5 |
| `YOUTUBE_PUBLISH_PRIVACY` | :714 | already in `AUDIT_KNOWN_ISSUES` §E5 |

Five of these seven are not in `AUDIT_KNOWN_ISSUES`. Three are Anthropic model IDs — an operator cannot
discover that per-feature model overrides exist, and a stale one is a silent quality/cost change.

**Commented-out only (1):** `REDBEAT_REDIS_URL`.

**In `.env.example`, not a `Settings` field (6):** `CLOUDFLARE_TUNNEL_TOKEN`, `POSTGRES_PASSWORD`
(both compose-only, legitimate), `RUN_LIVE_SMOKE`, `RUN_LLM_LIVE` (test-lane toggles),
`STAGING_STRIPE_SECRET_KEY`, `STAGING_STRIPE_WEBHOOK_SECRET` (compose-interpolation only). Worth noting
that `pydantic-settings` is configured `extra="ignore"` (`config.py:35`), so a typo'd key in
`/opt/autoclip/.env` is silently discarded — there is no "unknown setting" signal at boot.

**Repro:** the parity script is 25 lines and is reproduced in "Method" above; re-running it is the check
that should exist.

---

## E10 — The preflight registry and the prod-boot requirement list are both hand-maintained literals covering 22 of 214 settings (MEDIUM, roll-up)

`scripts/doctor.py:283 _SECTIONS` is a literal list of 8 section builders. Across all of them the
doctor names **24 environment keys**, of which 22 are `Settings` fields — **192 settings are invisible
to the deploy preflight**, including every one implicated in a past or present outage-by-silence:
`BACKUP_R2_BUCKET`, `BACKUP_ENCRYPTION_KEY`, `METRICS_TOKEN`, `SENTRY_DSN`, `OTEL_EXPORTER_OTLP_*`,
`NOTIFY_BACKEND`, `MAILING_ADDRESS`, `CSRF_FETCH_METADATA_ENABLED`, every `FLAG_*_ENABLED`.

`config.py:1130 _require_prod_secrets` is the same shape at boot: a literal
`("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET")` plus a literal four `R2_*`.

**And one gate actively forbids closing the hole.** `tests/test_backup_config.py:37`:

```python
def test_backup_config_is_not_a_production_boot_requirement():
    src = inspect.getsource(config.Settings._require_prod_secrets)
    assert "BACKUP_" not in src, (
        "Backup config is intentionally decoupled from app-boot validation; ... "
        "Do not add BACKUP_* to _require_prod_secrets")
```

The file is named `test_backup_config.py`; its three tests assert that the backup settings are **empty**
(`:17-18`), that a retention integer is ≤30, and that no production signal about backups may ever be
added. That is a green test module named after a disaster-recovery mechanism that has never been
configured, structurally guaranteeing silence about it. (The decoupling rationale — don't crash-loop
the app over a cron setting — is sound; a **warn-in-prod** would satisfy both. The test as written bans
even that from `_require_prod_secrets`.)

Related, and undocumented anywhere operators would look: `BACKUP_HEALTHCHECK_URL` — the dead-man's
switch that would report the nightly backup stopped (`scripts/backup_pg.sh:32,111`;
`backup_redis.sh:46,92`) — is **not a `Settings` field and not in `.env.example`**. The alarm for the
missing safety net is itself unconfigured and undiscoverable.

**Repro:**

```bash
.venv/bin/python - <<'PY'
import re, ast
d = open('scripts/doctor.py').read()
names = set(re.findall(r'check_field\(\s*env,\s*"([A-Z_0-9]+)"', d)) | set(re.findall(r'env\.get\("([A-Z_0-9]+)"', d))
t = ast.parse(open('config.py').read())
cls = [n for n in t.body if isinstance(n, ast.ClassDef) and n.name == "Settings"][0]
f = {n.target.id for n in cls.body if isinstance(n, ast.AnnAssign)
     and isinstance(n.target, ast.Name) and re.fullmatch(r"[A-Z][A-Z0-9_]*", n.target.id)}
print(f"doctor names {len(names)} keys; Settings has {len(f)}; UNCHECKED: {len(f - names)}")
PY
# doctor names 24 keys; Settings has 214; UNCHECKED: 192
pytest tests/test_backup_config.py -q   # 3 passed
```

---

## E11 — `STRIPE_WEBHOOK_SECRET` is absent from the deploy secret sync while `STRIPE_SECRET_KEY` is present (LOW-MEDIUM)

`.github/workflows/deploy.yml:193-243` syncs `STRIPE_SECRET_KEY` from GitHub secrets. It does **not**
sync `STRIPE_WEBHOOK_SECRET` — which lives only in the hand-edited `/opt/autoclip/.env`. Rotating the
Stripe credentials through the documented mechanism therefore updates one half of a pair. A stale
signing secret makes `construct_webhook_event` raise, `routers/billing.py:238` logs
`billing_webhook_rejected reason=bad_signature`, and the endpoint returns 400 — **fulfilment stops with
no alarm**, because there are zero alert rules in the repo (`process-map.md` §6) and no metric on that
counter. The `sync_secret` helper (`deploy.yml:218`) is also a silent-skip by design
(`if [ -z "$val" ]; then echo "skip $key"; return; fi`) with no post-sync assertion that the required
set landed.

**Repro:** `grep -n "sync_secret STRIPE" .github/workflows/deploy.yml` → one line, `STRIPE_SECRET_KEY`.

---

## External registrations: the complete inventory, and what reconciles each

| External registration | Where it lives | Reconciled against `app.routes` / config by | Command that would read the real value |
|---|---|---|---|
| Stripe webhook endpoint URL | Stripe Dashboard | **nothing** (already filed: `d09-billing.md:116`) | `_STRIPE.webhook_endpoints.list()` |
| Stripe webhook signing secret | VM `.env`, hand-edited | **nothing**; not in the deploy sync (E11) | same call, compare `secret` prefix / rotate |
| Google OAuth redirect URI | Google Cloud Console | **scheme only** (E5) | GCP console; or assert `urlparse(settings.OAUTH_REDIRECT_URI).path` resolves in `main.app.routes` |
| Google OAuth authorized JS origins | Google Cloud Console | nothing | GCP console |
| R2 bucket CORS `AllowedOrigins` / `ExposeHeaders` | R2 bucket | **nothing** (E4) | `_r2().get_bucket_cors(Bucket=settings.R2_BUCKET)` |
| R2 bucket lifecycle rules (backup retention, `docs/COMPLIANCE.md:104` claims 14d/56d) | R2 backup bucket | nothing | `_r2().get_bucket_lifecycle_configuration(Bucket=settings.BACKUP_R2_BUCKET)` |
| Cloudflare Tunnel ingress `autoclip.studio → app:8000` | CF Zero Trust dashboard (token-managed; `docker-compose.prod.yml:128-133` says so) | nothing; no config-as-code | `curl -H "Authorization: Bearer $CF_API_TOKEN" https://api.cloudflare.com/client/v4/accounts/$ACCT/cfd_tunnel/$TUNNEL_ID/configurations` |
| Cloudflare Health Check on `/health` | CF dashboard (`docs/DEPLOYMENT.md:145-172`) | nothing; only continuous uptime signal | CF API `/healthchecks` |
| Cloudflare WAF exception `stripe-webhook-skip-waf` (`docs/EDGE_SECURITY.md` Rule 2) | CF dashboard | nothing — and its absence caused Issue 485a | CF API `/rulesets` |
| Host cron `7 3 * * * backup_pg.sh`, `27 3 * * * backup_redis.sh` | VM crontab | nothing (E10) | `ssh …@147.182.136.107 crontab -l` |
| GitHub Actions secrets/vars (`CC_JWT_SECRET`, `CC_CREATOR_ID`, `SENTRY_DSN`, `OTEL_*`, `BACKUP_*`) | GitHub | silent-skip on absence | `gh secret list`, `gh variable list` |

**Nine externally-registered values; zero of them have any automated reconciliation.** Every one is a
`/webhooks/stripe`-shaped 404 waiting to happen, and the project has already been bitten by three of
them (Stripe endpoint URL, CF WAF ruleset, backup cron).

---

## Things I checked and found CLEAN (so nobody re-derives them)

- **`flags.py` kill switches.** All four `KNOWN_FLAGS` keys are genuinely enforced: `llm_generation`
  (`billing/spend_guard.py:406`, `worker/tasks.py:1704`, `require_flag` on routes), `youtube_publish`
  (`worker/tasks.py:1096`), `render_intake` (`require_flag`), `signup` (`routers/auth.py:165`). No
  orphan flag. Fail-open is deliberate and documented, the flip is dual-rail audited, and
  `tests/conftest.py:206` primes from `KNOWN_FLAGS` rather than a second literal list. The only nit:
  `scripts/flags.py:57-61` writes an unknown key anyway after a warning, then prints a confident
  `"<key> → OFF … live everywhere within ~30s"` — during an incident a typo would read as success.
  Low severity; noted, not filed.
- **`sentry_environment` / `logs_database_url` / `database_migration_url`** looked unreferenced in the
  raw grep but are consumed through lowercase `@property` accessors (`main.py:70`,
  `worker/celery_app.py:24`, `event_log.py:82`). Not findings.
- **`.dockerignore`** correctly excludes `.env` / `.env.*` with `!.env.example`, and `git ls-files`
  confirms no `.env` is tracked. The stray root `.env` noted in `process-map.md` §7 is untracked and is
  not baked into the image.
- **`config.py` prod validators** are genuinely load-bearing where they exist: the `STORAGE_BACKEND != "r2"`
  hard-fail (`:1173`), the Fernet key-format validator (`:1060`), the
  `TRANSCRIPTION_TIMEOUT_S < CELERY_SOFT_TIME_LIMIT_S - 30` invariant (`:1110`), and the
  `NOTIFY_BACKEND=resend` → `RESEND_API_KEY`/`EMAIL_FROM` fail-fast (`:1093-1105`). The gap is
  *coverage* (E6, E10), not correctness.
- **Staging's prod-`.env` bleed guards** (`docker-compose.staging.yml:87-99`) are a hand-maintained
  literal set of six overrides, but I could not find a live bleed today: `STORAGE_BACKEND: local`
  closes the R2 write path (`worker/storage.py:274`) and the presigned path (`routers/videos.py:288`),
  and `STRIPE_*` are blanked. It is a registry that will drift when the next external integration
  lands; flagged as a watch item, not a finding.

---

## Off-class

1. **`worker/tasks.py:6730` writes the delivery row as `sent` before the send and only downgrades it on
   exception.** Even with a correctly configured Resend backend this is a lost-update window: the
   process dying between `flush()` and the API call leaves a permanent `sent` row for an email that was
   never dispatched, and the `dedupe_key` latch prevents any retry. Ordinary bug, money-adjacent,
   independent of E3's config angle.
2. **`notify/mailer.py:54` captures `settings.MAILING_ADDRESS` into `_jinja_env.globals` at import
   time.** Setting the address later without a full process restart renders empty CAN-SPAM footers on
   otherwise-sent lifecycle mail.
3. **`scripts/flags.py:57-61`** — see above; an unknown flag key is written to the DB with a warning
   followed by a success line.
