# Modality C — Probes, health, smoke, drills

**Sweep date:** 2026-08-17 · **Target class:** an intermediate layer reports success without
exercising the thing it claims to verify. · **Scope:** every artefact in the repo that *attests* a
subsystem is alive — `scripts/doctor.py` (line by line), `scripts/live_smoke.py`,
`scripts/llm_harness.py`, `scripts/drills.py`, `scripts/check_downgrades.py`,
`scripts/backup_pg.sh`, `scripts/backup_redis.sh`, `scripts/deploy.sh`, `scripts/ci_local.sh`,
`scripts/llm_e2e.py`, `scripts/r2_inspect.py`, `scripts/clip_audit.py`, the `/health` endpoint and
its compose healthchecks, `deploy.yml` (staging gate + prod smoke), `staging-drills.yml`,
`health-check.yml`, `llm-e2e-nightly.yml`, `migration-lint` in `ci.yml`, and the meta-gates in
`tests/test_ci_config.py` / `tests/test_doctor.py`.

**Honest yield: 9 candidates, 4 of them high.** Two carry an executed repro. The rest carry an exact
state description. `scripts/drills.py` and the `migration-lint` job came back clean — they have been
hardened against precisely this class and the hardening holds; I say so below rather than padding.

**The organising observation.** The project fixed the *canonical* instance (`_live_stripe` now goes
through `billing.stripe_client._STRIPE`) and wrote three regression tests for it. But the fix lives
in `doctor.py --full`, and **the automated deploy gate does not pass `--full`**. The same pattern
repeats three more times below: the correct mechanism exists somewhere in the repo, and the path
that actually runs in production is the one without it.

---

## C1 — The deploy preflight runs `doctor.py` WITHOUT `--full`, so every external-API live probe (including the hard-won app-client Stripe probe) is skipped on every deploy

**Severity: high. Repro: structural, exact.**

| | |
|---|---|
| Evidence | `.github/workflows/deploy.yml:279` · `scripts/deploy.sh:97` · `scripts/doctor.py:454-474` |
| Claims to verify | `deploy.yml` step name: **"Preflight check"**. `docs/SOT.md:298` calls `doctor.py` the *"Preflight secrets validator (presence/format/**live**, redacted) — deploy gate"*. `docs/DECISIONS.md:10474` records that it runs *"after image pull and **before** migrations/cutover, so a bad secret"* is caught first. |
| Why it does not verify that | `audit()` only appends the `Live — external APIs` section under `if full:` (`doctor.py:461`). Both deploy paths invoke it bare: `docker compose … run --rm app python scripts/doctor.py`. No `--full`. So the deploy-time preflight probes **Postgres and Redis only**. Anthropic, Voyage, Deepgram, R2 and Stripe are never contacted by the automated gate. |

The consequence is specific and ugly: `OFF_COURSE_BUGS.md:148` and `DECISIONS.md:13008` record the
2026-08-13 fix that made `_live_stripe` go through `billing.stripe_client._STRIPE` and `_live_r2`
through `worker.storage._r2` — the fix to instance #2 of the project's own named #1 failure class.
**That fix is in a code path no scheduled job executes.** It runs only when a human types
`--full`, which the record shows happening twice in project history (2026-07-29 per
`GO_LIVE.md:71`, and the 2026-08-13 verification).

Repro — the section list is flag-dependent, and the flag is never passed:

```bash
.venv/bin/python -c "
import sys; sys.path.insert(0,'.')
import inspect, scripts.doctor as d
print(inspect.getsource(d.audit))"
# → sections.append(('Live — external APIs', [...])) is inside `if full:`
grep -n 'scripts/doctor.py' .github/workflows/deploy.yml scripts/deploy.sh
# → deploy.yml:279 and deploy.sh:97, neither with --full
```

**What can be dead while this stays green:** an expired/rotated/revoked `ANTHROPIC_API_KEY`,
`VOYAGE_API_KEY`, `DEEPGRAM_API_KEY` or Stripe key; an R2 credential whose scope changed; the exact
Stripe-transport class of defect the probe was rewritten to catch. All of it deploys clean, and the
`/health` + `llm_harness --flow core` smoke that follows (C4) touches none of those either.

**Rider — the same file, two more holes.**
- `_section_billing` (`doctor.py:247-249`) returns `Status.WARN` — never `FAIL` — when
  `STRIPE_SECRET_KEY` is absent, *including when `ENV=production`*. `has_failures()`
  (`doctor.py:477`) counts only `FAIL`. Combine with `deploy.yml:219`
  (`if [ -z "$val" ]; then echo "skip $key (no GitHub secret set)"; return; fi`): deleting or
  mistyping the `STRIPE_SECRET_KEY` repository secret silently disables billing and the preflight
  still exits 0.
- **`YOUTUBE_API_V3_KEY` has no section in `doctor.py` at all**, despite being one of the nine
  secrets `deploy.yml:197` synchronises. That is the credential behind catalog sync — instance #1
  of the project's failure table, 7 weeks dead. Nothing in any preflight validates its presence,
  format or liveness.

---

## C2 — The LLM E2E nightly and its live-ASR leg report GREEN with zero API calls when the secret is empty; the meta-gate that exists to prevent this pins the YAML string, not the execution

**Severity: high. Repro: EXECUTED — exit 0, 9 skipped, 0 live calls.**

| | |
|---|---|
| Evidence | `tests/test_llm_live.py:18-22` · `tests/test_llm_live_scoring.py:42-46` · `tests/ingestion/test_transcription_live.py:28-32` · `.github/workflows/llm-e2e-nightly.yml:63-92, 94-125` · meta-gate `tests/test_ci_config.py:491-537` |
| Claims to verify | The workflow header: *"verify … correct structured output from every LLM feature module, honesty disclaimer present, prompt cache landing, usage recorded, typed SDK exceptions, no PII in logs"* against **the real Anthropic API**, plus the Issue-481 leg that *"re-transcribes the checked-in LibriSpeech fixture against real Deepgram"*. `docs/GO_LIVE.md:71` cites *"LLM E2E nightly green daily"* as evidence that all external APIs are provisioned and live. |
| Why it does not verify that | The gate is `_LIVE = os.environ.get("RUN_LLM_LIVE") == "1" **and bool(os.environ.get("ANTHROPIC_API_KEY"))**`. The workflow sets `RUN_LLM_LIVE: "1"` unconditionally but sources the key as `${{ secrets.ANTHROPIC_API_KEY }}` — which expands to the **empty string** if the secret is unset, renamed, or rotated to blank. `_LIVE` goes False, every live test is `skipif`-skipped, and pytest exits **0**. Identical structure for `DEEPGRAM_API_KEY` on the transcription leg. |

Executed repro (this box, `.venv`):

```bash
env RUN_LLM_LIVE=1 ANTHROPIC_API_KEY="" \
  DATABASE_URL="postgresql+psycopg://creatorclip:dev_password@localhost:5432/creatorclip" \
  REDIS_URL="redis://localhost:6379/0" GOOGLE_OAUTH_CLIENT_ID=stub GOOGLE_OAUTH_CLIENT_SECRET=stub \
  OAUTH_REDIRECT_URI="http://localhost:8000/auth/callback" \
  TOKEN_ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  JWT_SECRET_KEY="stub-jwt-secret-32-bytes-minimum-!" ALLOWED_ORIGINS="http://localhost:8000" LOG_DIR="" \
  .venv/bin/python -m pytest tests/test_llm_live.py tests/test_llm_live_scoring.py -m llm_live -q --no-header
```

```
sssssssss                                                                [100%]
9 skipped, 1 deselected
EXIT=0
```

**The meta-gate makes this worse, not better.** `tests/test_ci_config.py:491` —
`test_nightly_runs_transcription_live_leg` — has the *right instinct* and its own docstring names
this failure mode: *"the marker + addopts exclusion alone would leave the live ASR test executing
NOWHERE, the exact silent-empty-lane failure mode of Issue 478."* But it asserts four **string
literals are present in the YAML**: the file path, `-m transcription_live`,
`RUN_TRANSCRIPTION_LIVE: "1"`, and `secrets.DEEPGRAM_API_KEY`. All four are satisfied by a workflow
whose secret is unset. The test pins the *half* of `_LIVE` that lives in YAML and cannot see the
half that lives in GitHub's secret store. Same for
`test_llm_nightly_runs_scoring_behavioral_lane` (`:513`).

**The project already owns the correct mechanism and did not apply it here.** `ci.yml:72-141`
guards the `render_env` lane with a hard `collect-only` count `-ge 7`, added after Issue 478
(marker registered, zero tests carried it). The nightly needs the same shape — a `--collect-only`
count, or `-p no:cacheprovider --strict-markers` plus an explicit assertion that skipped == 0.

**On top of it, a signal with no receiver:** `llm-e2e-nightly.yml:141` —
`grep "SCORING-MARGIN" "$RESULT_FILE" >> "$GITHUB_STEP_SUMMARY" || echo "No SCORING-MARGIN lines —
scoring lane did not run."` — writes "the scoring lane did not run" into a step summary and **exits
0**. The one place the vacuous run announces itself is a markdown blob with no consumer.

**What can be dead while this stays green:** the entire live-LLM surface, the Issue-476 scoring
behavioural lane (setup-vs-aftermath ordering — the single decision-maker for what ships), prompt
caching, and the Issue-481 ASR timing-fidelity check. All of it, indefinitely, at 03:00 UTC, with
a green tick every morning.

---

## C3 — The production deploy job never asserts `alembic current == head`; the staging job and the manual fallback script both do, and the meta-gate only covers staging

**Severity: high. Repro: structural, exact.**

| | |
|---|---|
| Evidence | `.github/workflows/deploy.yml:299-300` (prod, no assertion) vs `deploy.yml:92-101` (staging, has it) vs `scripts/deploy.sh:117-131` (manual, has it) · meta-gate `tests/test_ci_config.py:160-178` |
| Claims to verify | The step is named **"Run migrations"** and its success is taken as "prod schema is at head" — the very next step rolls the new image out. |
| Why it does not verify that | Incident #4 in the ground-truth taxonomy: *"`alembic/env.py` ran `SET lock_timeout` before `context.begin_transaction()`; `alembic upgrade head` exited 0, printed nothing, changed nothing"* — every prod migration a silent no-op, prod DB 7 revisions behind shipped code. The one-line fix added afterwards was `assert current == head`. It was added to `scripts/deploy.sh` (the **manual fallback**) and to `deploy.yml`'s **staging** job. The prod job in the same file is a bare `docker compose … run --rm app alembic upgrade head` with no verification. |

`tests/test_ci_config.py:160` (`test_staging_migrations_run_in_container`) asserts
`"alembic heads" in src and "alembic current" in src` — a **whole-file** substring check on
`deploy.yml`. It passes on the strings inside the staging job, so it can never notice that the prod
job lacks them. Verify:

```bash
grep -n "alembic current\|alembic heads\|CUR_REV" .github/workflows/deploy.yml
# → 92, 93, 95, 96, 97, 101  — all inside the deploy-staging job. Nothing in the deploy job.
sed -n '299,301p' .github/workflows/deploy.yml
```

**Mitigation that partially holds:** the staging gate runs the same image against a data-bearing DB
and does assert head, so a *code-level* env.py regression is caught before prod. **What it does not
cover:** anything prod-specific — a `DATABASE_MIGRATION_URL` pointing at the wrong role or database,
a lock timeout on live traffic, a partially-applied revision, a permission difference under the
`creatorclip_app`/`creatorclip` role split. In all of those, prod migrations no-op, the deploy
reports success, and the post-deploy smoke (C4) cannot see a stale schema because it only reads four
endpoints that do not touch the new columns.

---

## C4 — The post-deploy "critical journey" smoke, the only functional gate between a merge and production, asserts four read endpoints and nothing else

**Severity: medium. Repro: read the step list.**

| | |
|---|---|
| Evidence | `.github/workflows/deploy.yml:305-377` · `scripts/llm_harness.py:94-145` |
| Claims to verify | The step comment: *"Phase 2: critical journey — `llm_harness --flow core`"*, and it is what decides whether to auto-rollback. `docs/DEPLOYMENT.md` treats it as the post-deploy verification. |
| Why it does not verify that | `flow_core`'s **required** steps are exactly: `/health` returns a status in `("ok","healthy")`; `GET /creators/me` == 200; `GET /videos` == 200; and `/videos` returns a dict containing the keys `videos` and `state`. `dna`, `insights` and `billing_balance` are `required=False` (`llm_harness.py:111`) — warn-only. The single write-path step, `write_path_link_video`, is *also* `required=False` (`:142`), with an in-code comment stating that a failure there must not roll back a deploy. |

So: four read assertions, run against a seeded fixture creator with no real data, are the entire
functional gate.

**What can be dead while this passes:** both Celery workers (nothing enqueues or awaits a task);
the `render` queue; ffmpeg; Deepgram/transcription; every LLM feature; Stripe; R2 writes; the clip
engine; the preference model; YouTube publish. `GET /videos` returning `{"videos": [], "state": …}`
is a PASS — which is the *identical* shape to the catalog-sync incident, where `HTTP 200 +
"Synced N video(s)"` covered an importer that imported nothing for 7 weeks.

Note this is not an argument for making the smoke heavier at deploy time; it is an argument that the
name "critical journey" and the rollback authority attached to it overstate what four GETs prove.

---

## C5 — `live_smoke.check_pipeline` reads back the rows `live_smoke._seed()` just wrote, and calls the result "pipeline: ingest_status=done"

**Severity: medium. Repro: `--seed` then `--only pipeline`.**

| | |
|---|---|
| Evidence | `scripts/live_smoke.py:310-334` (the check) vs `:196-254` (`_seed`) |
| Claims to verify | Module docstring: *"Exercises the REAL pipeline capabilities against a deployed target … This is the post-deploy 'does it actually still work?' check the mocked unit lane and the LLM-only `llm_e2e.py` cannot answer."* `check_pipeline`'s own docstring: *"asserts the canary reached each stage."* The four emitted PASS lines are `pipeline: ingest_status=done`, `pipeline: transcript row present`, `pipeline: signals row present`, `pipeline: >=1 clip candidate generated`. |
| Why it does not verify that | `_seed()` INSERTs `videos … ingest_status='done'`, a `transcripts` row, a `signals` row and a `clips` row. `check_pipeline` then SELECTs exactly those four things. Its fixture is written by the same script, minutes earlier, with the values the assertions demand. It is a **closed loop**: no application code — not `ingest_video`, not `transcribe_video`, not `build_signals`, not `generate_clips` — executes at any point. Even without `--seed`, the canary rows persist from the last seeding run, so re-runs assert the durability of a row, not the health of a pipeline. |

The docstring does concede *"A full live re-run (real ingest+Deepgram+ffmpeg) is intentionally NOT
auto-triggered here"* — so the intent is honest. **The output is not.** Four lines reading
`[PASS] pipeline: …` are what gets pasted into a scorecard, and they are true of a corpse. This is
the exact shape of the taxonomy's incident #3 (*"0/18 clips had ever rendered … Rungs 1–3 all passed
because they seed a real video as `source_uri`; only rung 4 exercised the real path"*).

Repro:

```bash
RUN_LIVE_SMOKE=1 .venv/bin/python scripts/live_smoke.py --target staging --seed --only pipeline
# → 4 PASS lines, exit 0, with worker/, ingestion/ and clip_engine/ never imported.
```

**Rider — `check_isolation` (`:288-307`) claims "Per-creator RLS isolation is live" while testing
one table.** It sets the GUC and counts rows in `clips` only. `tests/test_rls_isolation_integration.py:265`
enumerates **17** tenant tables. This is the same shape as `OFF_COURSE_BUGS.md:25` (the RLS test that
hardcoded `("clips","signals")`), reappearing in the *live* probe — and per the taxonomy, RLS
defects "only show up against the live role", so this probe is the only place the class is
catchable in production.

---

## C6 — `live_smoke.check_publish` reports PASS on an `import` statement, and the publish capability is unexercised in every mode

**Severity: medium. Repro: read the three branches.**

| | |
|---|---|
| Evidence | `scripts/live_smoke.py:493-515`; registered as a first-class capability at `:545` and `:586` |
| Claims to verify | Module docstring lists `publish` among *"a fan-out of independent leaf operations (render, clean, title, caption, explain, publish)"* that *"exercise the REAL pipeline capabilities"*. `check_publish`'s docstring: *"Asserts the publish path is reachable up to the upload boundary."* |
| Why it does not verify that | Three branches, none of which reach YouTube: (a) `--publish-live --target staging` → `res.skip("publish", "real upload path not wired in the harness")`; (b) `--publish-live` on any other target → hard fail by design; (c) **default** → `from youtube.publish import YouTubeUploadError` inside a `try`, then `res.ok(True, "publish: pre-flight surface importable (dry-run; no real upload)")`. The assertion is that a Python module imports and an exception class exists. |

**What can be dead while this passes:** revoked OAuth scopes, an expired refresh token (which, per
`AUDIT_KNOWN_ISSUES.md` §H, expire every 7 days because Google verification is unsubmitted), an
exhausted quota, a wrong `YOUTUBE_PUBLISH_PRIVACY`, a broken `videos.insert` payload — anything at
all in `youtube/publish.py` below the import line.

---

## C7 — Nothing anywhere performs an R2 **write**; three independent probes all attest "storage healthy" from read-only operations

**Severity: medium. Repro: enumerate the three call sites.**

| | |
|---|---|
| Evidence | `main.py:529-551` (`_check_storage` → `head_bucket`) · `scripts/doctor.py:381-397` (`_live_r2` → `head_bucket`) · `scripts/live_smoke.py:518-530` (`check_r2` → `list_objects_v2(MaxKeys=1)`) |
| Claims to verify | `_check_storage`'s docstring is explicit about the failure it exists to catch: *"a misconfigured/unreachable bucket would otherwise stay invisible until a creator's upload silently FAILs in the worker pipeline; a HEAD on the bucket surfaces it at `/health` instead (Gap 5)."* `_live_r2`'s comment: *"probe through the app's own client factory … so a misconfigured `worker/storage._r2` … is what gets tested."* `check_r2`: *"Target R2 reachable."* |
| Why it does not verify that | `HeadBucket` and `ListObjectsV2` require only read permission. Cloudflare R2 API tokens are issued per-scope (**Object Read only** vs **Object Read & Write**). A token rotated or re-issued at the wrong scope leaves all three probes green — `/health` says `"storage":"ok"`, `doctor --full` says `bucket reachable (app client)`, `live_smoke` says `bucket reachable + canary prefix listable` — while **every** `put_object` in the upload and render paths 403s. The app-client fix applied to `_live_r2` corrected *which client* is used; it did not change *which operation*, and the operation is the wrong half of the contract. |

The precedent is on file: `ISSUES_LOG.md:542` — `STORAGE_BACKEND` left at `local` on a two-container
prod meant *uploads silently FAILED*. That specific misconfig is now caught (`doctor._section_storage`
FAILs on prod+non-r2). A **credential-scope** regression producing the same user-visible outcome is
caught by nothing. The fix is a round-trip: put a tiny object under a `healthcheck/` prefix, read it
back, delete it.

---

## C8 — `test_every_ffmpeg_task_is_routed` compares a literal to a copy of the same literal, and its claim is already false

**Severity: medium. Repro: run the AST scan below.**

| | |
|---|---|
| Evidence | `tests/test_celery_routing.py:29-38` vs `worker/celery_app.py:72-78` |
| Claims to verify | Test name and docstring: **"Every currently-known ffmpeg-encoding task is in the routed set."** The pins exist because of Issue 432: *"renders are CPU-saturating (a single libx264 encode uses the whole box), so N concurrent renders starve each other into the render timeout — the live failure was four on-demand clicks timing out together at ~266 s each."* |
| Why it does not verify that | The test body is `expected = {five string literals}` then `assert set(RENDER_TASKS) == expected`. `expected` is a hand-copied duplicate of `RENDER_TASKS` (`worker/celery_app.py:72`). It scans no source, inspects no task registry, and greps for no `ffmpeg` invocation. It can only fail if someone edits `RENDER_TASKS` and not the test — i.e. it is a change-detector for the constant, not a check on the codebase. Adding a new ffmpeg task and forgetting to route it — **the failure the pin exists for** — leaves all three tests in the file green. |

**And the claim is false today.** Tasks outside `RENDER_TASKS`, therefore on the default `celery`
queue at `--concurrency=4`, that shell out to ffmpeg:

- `worker.tasks.ingest_video` → `ingestion/audio.py` (ffmpeg audio extraction, `showwavespic`)
- `worker.tasks.backfill_video_peaks` → `ingestion/peaks.py`
- `worker.tasks.backfill_video_camera_regions` → `clip_engine/camera_region.py:508-640`
  (multi-pass ffmpeg decode + `image2` frame extraction)

```bash
.venv/bin/python - <<'EOF'
import ast, pathlib, re
src = pathlib.Path("worker/tasks.py").read_text(); tree = ast.parse(src)
routed = {"render_clip","render_video_clips","clean_clip","edit_clip","render_summary"}
for n in ast.walk(tree):
    if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and any("task" in ast.unparse(d) for d in n.decorator_list):
        seg = ast.get_source_segment(src,n) or ""
        if re.search(r'ffmpeg|peaks|camera_region|extract_audio', seg, re.I) and n.name not in routed:
            print("UNROUTED ffmpeg-touching task:", n.name)
EOF
```

The rule the project needs here is its own: **scan, don't list.** Derive the ffmpeg set from the
call graph (or from a decorator/marker on the task) and assert the derived set equals `RENDER_TASKS`.

---

## C9 — `/health` has no worker, queue or schema dimension; `docs/DEPLOYMENT.md` misstates what it returns; and the only continuous production monitor is unversioned and unverifiable from this repo

**Severity: medium (with an unverifiable component — see the command below).**

| | |
|---|---|
| Evidence | `main.py:553-575` (`health`), `:500-551` (`_check_postgres` / `_check_redis` / `_check_storage`) · `docs/DEPLOYMENT.md:145-172` · `docker-compose.prod.yml:22-30` |
| Claims to verify | `DEPLOYMENT.md:145`: *"Continuous uptime monitoring is owned by Cloudflare Health Checks."* It is the **only** continuous production signal in the system (`health-check.yml`'s schedule was removed; `process-map.md` §6). |
| Why it does not verify that | (a) `/health` probes exactly three things: Postgres, Redis, R2 head-bucket. It has **no** dimension for the Celery workers, the `render` queue depth, or the applied Alembic revision. Every ingest/transcribe/clip/render job in the product runs on Celery. A dead or wedged worker leaves `/health` returning `{"status":"ok", …}`, so the Cloudflare monitor, the deploy-time Phase-1 gate, `health-check.yml` and `llm_harness`'s `health` step are *all* satisfied while nothing the creator uploads is ever processed. (b) `DEPLOYMENT.md:163-165` states `/health` *"degrades to non-`ok` + **503** when a backing service is down."* **It does not** — `health()` returns a plain `dict`, i.e. HTTP 200, in every state. An operator following those setup instructions who relied on "Expected codes 200" without also enabling the body match on `"status":"ok"` (step 3 of the same list) has a monitor that cannot detect a total Postgres outage. |

The compose healthcheck (`docker-compose.prod.yml:23-29`) is explicit and correct about the 200:
*"Liveness only: /health returns 200 even when a dependency is degraded"* — so the code comment and
the deployment doc directly contradict each other, and the doc is the one that was used to configure
the monitor.

**What cannot be verified from this repo** (state it plainly rather than guess): whether the
Cloudflare Health Check exists at all, whether it is suspended, whether the response-body match on
`"status":"ok"` was actually enabled, and whether a notification destination is attached. There is
no config-as-code and no test. Given `OFF_COURSE_BUGS.md:104` — *prod fully down, silently, for up
to 9 days* because a monitor had quietly stopped — this is the single highest-value unverified
claim in the operational stack. The command that settles it:

```bash
curl -s -H "Authorization: Bearer $CF_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/healthchecks" \
| jq '.result[] | {name, address, path: .http_config.path,
                   expected_codes: .http_config.expected_codes,
                   expected_body:  .http_config.expected_body,
                   suspended, consecutive_fails, check_regions}'
# then, for the alert wiring:
curl -s -H "Authorization: Bearer $CF_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/alerting/v3/policies" \
| jq '.result[] | select(.alert_type|test("health_check")) | {name, enabled, mechanisms}'
```

A non-empty `expected_body` containing `status":"ok"` plus `suspended: false` plus one enabled alert
policy is the whole proof. Anything less means the 9-day outage can recur.

---

## Verified clean — stated so nobody re-sweeps them

- **`scripts/drills.py`.** I went looking for a second `all([])`. There isn't one.
  `drill_rate_limit:309` now asserts `first_429 == limit` *before* the `all(c == 404 …)` guard, which
  is precisely what makes the guard non-vacuous; the empty-slice case fails loudly with a message
  that says the run proves nothing. `drill_flags_flip:165-171` demands `200 <= c < 400` rather than
  `!= 503`, having been burned by a 429 masquerading as success. `_clear_rate_limit_buckets` clears
  the whole `LIMITS:*` namespace *and* the spend cool-down at both ends of the run. `_run_drills`
  uses one event loop. This file is the best-hardened artefact in the sweep. The only residual is
  `_RENDER_BURST_PER_HOUR = 20` (`:283`), a hand-maintained mirror of the decorators in
  `routers/clips.py:938-939` — but a drift there fails the run loudly (`first_429 != limit`, or no
  429 within `limit + 5`), so it is a maintenance cost, not a vacuous-green generator.
- **`migration-lint` in `ci.yml:340-417`.** `set -o pipefail` is set before both
  `alembic … --sql | squawk` and the `pg_dump | grep` round-trip, with in-line comments naming the
  two past vacuous passes (the off-by-one that rendered the wrong migration into suppressed stderr;
  the empty-dump diff). `scripts/check_downgrades.py` also validates its own allowlist for staleness
  in the direction that matters. The job being *non-required* is a separate, already-documented
  problem — the machinery itself is sound.
- **`tests/test_doctor.py:161-185`.** The three probe-integrity tests patch
  `billing.stripe_client._STRIPE` and assert `_live_stripe` drives it, including the failure
  direction. That is a legitimate structural pin against reverting to `httpx`. (There is no
  equivalent test for `_live_r2`; minor.)
- **`scripts/backup_pg.sh` / `backup_redis.sh`.** Streamed with `set -euo pipefail`, secrets via
  `-pass env:` never argv, the 3-2-1 bucket-inequality guard, and an optional dead-man's-switch
  ping. The known `BACKUP_R2_BUCKET`-unset gap is already filed (`AUDIT_KNOWN_ISSUES.md` §B); I add
  only that **no restore has ever been drilled** — nothing reads a backup back. Not filed as a
  candidate because it is a missing check, not a false one.

---

## Off-class

- **`scripts/live_smoke.py:177-183` and `scripts/llm_e2e.py:142-145` — the honesty assertion is
  near-vacuous.** `_HONESTY_WORDS` (both files, identical sets) includes the bare substring
  `"may"`, matched with `any(w in text.lower() …)`. Any prose containing "may", "maybe", "dismay",
  "Mayor" — or the word "may" in any sentence at all — satisfies *"honesty disclaimer present"*.
  `"This clip will go viral, and you may love it"` passes. Honesty is the project's stated identity
  and `CLAUDE.md` requires the constraint in every interface; this is the only assertion of it in
  the live harnesses. Borderline in-class (a check that reports what it does not verify) — I list it
  here rather than as a candidate because the structural no-virality test in the unit lane is the
  primary gate and this is the secondary one. Fix: drop `"may"` and `"based on"`, or require one of
  the *specific* phrases (`not a guarantee` / `cannot guarantee` / `does not promise` / `estimate`).
- **`tests/test_rls_isolation_integration.py:265` — `_TENANT_TABLES` is a hand-maintained literal
  with no drift guard.** The suite is otherwise excellent (deny-by-default, pooled-connection reuse,
  WITH CHECK on writes, a real worker function under the app role). But grep confirms **no test
  anywhere queries `pg_class.rowsecurity` or `pg_policies`**, so the set of tables the RLS gate
  covers is a tuple in a test file — a third independent copy alongside migration `0010`'s. A
  tenant table added tomorrow is silently outside every assertion, which is exactly how
  `improvement_briefs` and `creator_insights` went unprotected until migration 0038. The structural
  fix is one query: assert `_TENANT_TABLES` equals the set of tables with a `creator_id` column,
  and that each has `rowsecurity = true` plus a policy.
- **`.github/workflows/staging-drills.yml:70-78` — the "Wait for staging /health" step captures
  `BODY` inside a `for` subshell loop and then tests `test -n "${BODY:-}"`.** The assignment happens
  in the same shell so this works, but the step never inspects `status` — unlike the deploy gate
  (`deploy.yml:125-132`), which parses it. A staging app returning `{"status":"degraded"}` proceeds
  straight into the drills. Low impact (the drills themselves would fail), noted for completeness.
