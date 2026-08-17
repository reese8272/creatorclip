# Modality D — Log, counter and status honesty

**Sweep date:** 2026-08-17 · HEAD `1def133` · READ-ONLY pass, `.venv/bin/python` only.

**Scope swept.** Every `+= 1` / `len()` / `count` that reaches a log message or a status field
in `worker/`, `youtube/`, `ingestion/`, `clip_engine/`, `knowledge/`, `dna/`, `chat/`,
`billing/`, `improvement/`, `preference/`, `notify/`, `upload_intel/`; every `if not X: return`
boundary in those packages (scripted enumeration, not eyeball); every `logger.info/warning`
containing a success word; all 36 `log_event()` call sites in `worker/tasks.py`; every
`aemit(..., "done", ...)`; the three render entry points in `clip_engine/render.py`; the whole
notification rail (`notify/` + `send_notification`); and the deploy-time smoke in
`.github/workflows/deploy.yml` + `scripts/llm_harness.py`.

**Honest yield.** The catalog-sync lesson was *genuinely* internalised where it was learned:
`youtube/data_api.py:294` now separates `seen` from kept, `youtube/analytics.py:299` counts
inserts, `worker/tasks.py:4899-4952` carries a comment explaining the exact 2026-08 bug and
counts `fetched` vs `attempted`, and `ingestion/signals.py:100-112` is a model raw-vs-kept
boundary. The generalisation did **not** travel. Eight live instances below (D-1…D-8), four
with executed repros. Three of them (D-1, D-3, D-4) are, in my judgement, the same severity
class as the four instances the brief names.

---

## D-1 — `NOTIFY_BACKEND` defaults to `console`; every notification is recorded `status=sent` and logged `"email sent"` while nothing leaves the box

**Severity: HIGH. Repro executed.**

| | |
|---|---|
| **Claims to verify** | A `NotificationDelivery` row with `status = NotificationDeliveryStatus.sent` (`worker/tasks.py:6737`, adopted again at `:6769`) plus `logger.info("send_notification: email sent event_type=%s creator=%s dedupe_key=%s")` (`worker/tasks.py:6845-6850`) claim a transactional email reached the creator. |
| **Why it does not** | `notify.mailer.send()` dispatches on `settings.NOTIFY_BACKEND` (`notify/mailer.py:183-203`). The default is `"console"` (`config.py:993`), and `_send_console` (`notify/mailer.py:206-222`) renders the template, writes one `logger.info`, and **returns `None`** — no external call, by design and by its own docstring ("dev / CI sink. Never calls any external service"). The caller cannot distinguish that from a real send: it only reacts to an exception (`worker/tasks.py:6826-6866`). No exception → row committed `sent`, log line says "email sent". |

**Why nothing catches it.** The only validator on this setting runs in one direction —
`_validate_notify_backend` (`config.py:1085-1107`) fails fast when `NOTIFY_BACKEND == "resend"`
*without* a key. There is no production guard the other way. Compare `_require_prod_secrets`
(`config.py:1130+`), which **does** hard-fail production on missing Stripe secrets and
auto-degrades `/metrics` — the asymmetry is the finding. `scripts/doctor.py` probes Postgres,
Redis, Anthropic, Voyage, Deepgram, R2 and Stripe; grep for `resend|notify|NOTIFY|email` in
that file returns **zero hits**, so the deploy preflight is silent on it. `NOTIFY_BACKEND` /
`RESEND_API_KEY` / `EMAIL_FROM` are not in `deploy.yml`'s secret-sync list either, so their
production values live only in the hand-edited `/opt/autoclip/.env` and cannot be established
from this repo.

This is structurally identical to `ISSUES_LOG.md:542` (`STORAGE_BACKEND` left at `local` on a
two-container prod → uploads silently FAILED). That incident earned a production validator.
This one did not.

**Repro** (boots a *production* Settings with no notify config and shows the whole chain):

```bash
DATABASE_URL="postgresql+psycopg://c:p@localhost:5432/c" REDIS_URL="redis://localhost:6379/0" \
GOOGLE_OAUTH_CLIENT_ID=x GOOGLE_OAUTH_CLIENT_SECRET=x \
OAUTH_REDIRECT_URI=http://localhost:8000/auth/callback \
TOKEN_ENCRYPTION_KEY=$(.venv/bin/python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())") \
JWT_SECRET_KEY="test-jwt-secret-32-bytes-minimum-!" ALLOWED_ORIGINS=http://localhost:8000 \
LOG_DIR="" ENV=production STRIPE_SECRET_KEY=sk_x STRIPE_WEBHOOK_SECRET=whsec_x STORAGE_BACKEND=r2 \
.venv/bin/python -c "
import logging; logging.basicConfig(level=logging.INFO)
from config import settings; print('ENV =', settings.ENV, '| NOTIFY_BACKEND =', repr(settings.NOTIFY_BACKEND))
import notify.mailer as m
class C: email='creator@example.com'; channel_title='Chan'
print('send() ->', m.send(to='creator@example.com', template='clips_ready',
      context={'creator':C(),'clip_count':0,'creator_name':'there','video_title':'V','review_url':'u'},
      idempotency_key='k1'))"
```

Observed:

```
WARNING:config:METRICS_TOKEN is unset in production — disabling /metrics.
INFO:notify.mailer:notify.mailer [console] template=clips_ready idempotency_key=k1 ...
ENV = production | NOTIFY_BACKEND = 'console'
send() -> None
```

Production boots clean, emits **no** warning about email, and every downstream delivery is
recorded `sent`. Right-to-erasure confirmations, refund notices, `reauth_required` (the message
that tells a creator their YouTube grant died) and `balance_low` all ride this rail.

**The rule this needs:** a backend whose whole contract is "does nothing" must be rejected in
`ENV == "production"` the same way `STORAGE_BACKEND=local` already is; and the delivery row
should record the backend that handled it, not a bare `sent`.

---

## D-2 — `clips_ready` fires unconditionally on **zero** clips: "Your 0 clips … are ready for review."

**Severity: HIGH (honesty inversion on the product's primary outbound message). Repro executed.**

`worker/tasks.py:3870-3885` (`send_notification.delay(..., "clips_ready", ...)` at `:3872-3874`) enqueues the `clips_ready` notification gated only on
`if clip_creator_id is not None:` — never on `len(clips)`. `len(clips)` is passed into the
payload as `clip_count` and then rendered verbatim by the template.

Zero clips is a first-class reachable state, not a pathological one:
`clip_engine/ranking.py:408-409` is a bare `if not ranked: return []` — a silent boundary with
no log and no count — and `score_and_rank` returns `[]` whenever candidate extraction yields
nothing (flat-energy source, empty transcript, no retention curve). The terminal SSE event one
line earlier is already honest about it (`message=f"Generated {len(clips)} clip(s)."`,
`worker/tasks.py:3825-3831`) — the email is not.

**Repro** (renders the shipped template with the payload the worker actually sends):

```bash
.venv/bin/python - <<'EOF'
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
env = Environment(loader=FileSystemLoader("notify/templates"),
                  autoescape=select_autoescape(["html"]), trim_blocks=True,
                  lstrip_blocks=True, undefined=StrictUndefined)
env.globals["app_url"]="https://autoclip.studio"; env.globals["mailing_address"]=""
class C: email="x@y.z"; channel_title="Chan"
print(env.get_template("clips_ready.txt").render(creator=C(), clip_count=0,
      creator_name="there", video_title="My 90-minute stream",
      review_url="https://autoclip.studio/app/review"))
EOF
```

Output:

```
Subject: Your clips are ready — My 90-minute stream

Hi there,

Your 0 clips from "My 90-minute stream" are ready for review.
```

Plus the in-app notification (`worker/tasks.py:6896-6900`): *"Your clips are ready to review. —
We found candidate clips from your video. Tap to review them."* — a claim the code has just
disproved. A `NotificationDelivery(status=sent)` row is written for it.

**No test covers the zero case.** `tests/test_notifications_triggers.py:57-110` fixes
`mock_clips = [MagicMock(rank=1), MagicMock(rank=2)]` and its docstring reads "after a
**successful** clip generation" — the assumption is baked into the only test.

**The rule this needs:** a notification whose subject asserts a quantity must be conditioned on
that quantity, and the zero case needs its own copy ("we couldn't find clips in this one").

---

## D-3 — The clip render never verifies its own output; ffmpeg exits 0 on an empty file, the clip is marked `done` and the creator is told "Clip ready."

**Severity: HIGH. Repro executed against real ffmpeg.**

`_run` (`clip_engine/render.py:94-125`) treats `returncode == 0` as proof of work and discards
`result.stderr` entirely on the success path. All three render entry points —
`render_clip_file` (`:936`), `render_cleaned_clip_file` (`:1105`), `render_summary_file`
(`:1288`) — call `_run` and return without ever looking at `out_path`.
`_encode_and_upload_clip` (`worker/tasks.py:2666-2699`) then uploads the file, sets
`clip.render_status = RenderStatus.done`, logs `"Clip %s rendered → %s"`, and emits the terminal
SSE `done` with `message="Clip ready."`

**The project already knows this hazard and fixed it in exactly one place.**
`clip_engine/render.py:314-317`, in the *poster* helper:

> `-ss` before `-i` is a fast seek and can land past the end on a VFR or broken-index source,
> **exiting 0 with no output** — hence the explicit non-empty check and the seek-0 retry rather
> than trusting the return code.

That helper checks `out_path.exists() and out_path.stat().st_size > 0` (`:329`). Repo-wide,
`st_size` appears in production code only there and at `worker/tasks.py:2037` (the ingest WAV).
It appears in `tests/test_render_env.py:177,242`, `tests/test_render.py:1583` and
`scripts/live_smoke.py:411,433` — **the tests and the manual smoke assert the invariant that
production does not enforce.**

**Repro** — a truncated recording (OBS crash, aborted multi-GB upload, partial R2 object; the
project explicitly ships a resumable path for 1–3 GB sources whose reload-resume and
JWT-expiry drills per `AUDIT_KNOWN_ISSUES.md` §B were never run). `+faststart` puts the moov
atom first, so ffprobe still reports the *full* duration while only the first seconds decode:

```bash
cd "$SCRATCH"
ffmpeg -y -f lavfi -i testsrc=duration=60:size=320x240:rate=25 \
       -f lavfi -i sine=frequency=440:duration=60 \
       -c:v libx264 -c:a aac -shortest -movflags +faststart long.mp4 -loglevel error
head -c 60000 long.mp4 > trunc.mp4                       # interrupted recording
ffprobe -v error -show_entries format=duration -of csv trunc.mp4
#   format,60.000000        <-- what _source_duration_s() sees; the end_s clamp passes

ffmpeg -y -ss 40 -accurate_seek -i trunc.mp4 -t 15 \
       -vf "crop=135:240:0:0,scale=1080:1920" \
       -c:v libx264 -crf 23 -preset fast -c:a aac -b:a 128k \
       -movflags +faststart out_trunc.mp4 2>err.txt
echo "exit=$?"; ls -l out_trunc.mp4; grep -i "nothing was encoded" err.txt
```

Observed:

```
exit=0
-rw-r--r-- 1 reese reese 262 out_trunc.mp4
[out#0/mp4] Output file is empty, nothing was encoded(check -ss / -t / -frames parameters if used)
```

**262 bytes, zero frames, exit 0** — and the diagnostic line lives in the stderr `_run`
throws away. `worker/storage.py:61-69` uploads it without inspection. The creator sees a clip
in the Ready pile whose player is blank; the only trace anywhere is a `poster_frame_unavailable`
WARNING from the poster helper, which does not affect status.

**The rule this needs:** `_run` should verify the declared output artefact is non-empty (and
ideally has a decodable video stream) before returning — the check the poster path already has,
lifted one level up so all three render entry points inherit it.

---

## D-4 — The deploy success signal never touches the worker; `/health` has no worker or queue component

**Severity: HIGH.**

Both post-deploy smoke phases run **inside the `app` container**
(`.github/workflows/deploy.yml:342-380`): Phase 1 `exec -T app python3 … urlopen('/health')`,
Phase 2 `exec -T app python3 scripts/llm_harness.py --flow core`. `llm_harness.flow_core`
(`scripts/llm_harness.py:94-145`) hard-asserts exactly `GET /health`, `GET /creators/me`,
`GET /videos` and the `/videos` envelope shape; `dna`, `insights`, `billing_balance` and the
one write probe are `required=False` (WARN, never fail).

`/health` (`main.py:553-567`) aggregates `_check_postgres`, `_check_redis`, `_check_storage`.
There is **no** Celery ping, no queue-depth read, no worker heartbeat. The rollout step is
`docker compose … up -d --remove-orphans` (`deploy.yml:302`) with **no `--wait`**, so it does
not block on container health either.

Consequence: `worker`, `render-worker` and `beat` can all be crash-looping (bad env, import
error, missing native dep) and every signal is green — `up -d` exits 0, `/health` returns
`"status": "ok"`, `llm_harness` prints `ALL REQUIRED STEPS PASSED ✓`, the deploy job reports
success, and the `autoheal` sidecar (`docker-compose.prod.yml:144-146`) silently restarts the
unhealthy containers forever. Meanwhile 100% of the product — ingest, transcribe, generate,
render, publish — is dead. The compose file *does* define correct Celery `inspect ping`
healthchecks (`docker-compose.prod.yml:49-53, 75-80`); **nothing in the deploy pipeline or the
continuous monitor ever reads them.**

The only continuous production signal is one Cloudflare Health Check on `/health`
(`docs/DEPLOYMENT.md:145-172`), so the same blind spot persists indefinitely after deploy. This
is the shape that produced the 9-day silent outage (`OFF_COURSE_BUGS.md:104`), one layer in.

**Repro / verification state:** `grep -n "worker\|celery" .github/workflows/deploy.yml` returns
nothing in the smoke step; `grep -n "celery\|queue\|worker" main.py` returns nothing in the
`/health` handler. Locally: stop the worker container, curl `/health` → `"status": "ok"`.

**The rule this needs:** the post-deploy gate must assert the thing the product is *for* —
minimally `celery -A worker.celery_app inspect ping` (the check already written in compose) as
a required Phase-3 step, and a worker-liveness field on `/health`.

---

## D-5 — `render_video_clips_done` reports `count = len(clip_ids)` — the number attempted, not the number rendered

**Severity: MEDIUM.**

`worker/tasks.py:1024-1029` (`count=len(clip_ids)` at `:1028`) emits
`log_event("render_video_clips_done", …)` on the event_log dual rail. `count` is the input list
length. `_render_video_clips_async` (loop at `worker/tasks.py:2786`, handlers `:2788-2814`) isolates **every** per-clip failure —
`ValueError`/`FileNotFoundError` marks that clip `failed` and continues; any other exception
marks it `failed`, re-enqueues, and continues. Neither branch propagates. A batch in which all
13 clips fail permanently therefore reaches the outer wrapper cleanly and records
`render_video_clips_done count=13`.

This is the catalog-sync shape verbatim — `fetched += 1` per loop iteration rather than per kept
item — relocated to the auto-render fan-out, which is the default path for every upload
(`AUTO_RENDER_CLIPS`). There *are* per-clip `render_video_clip_failed_permanent` events, so the
information exists on the rail; but the aggregate event that names itself `_done` and carries a
`count` is the one an operator or a dashboard would read, and it is unconditioned.

**The rule this needs:** count successes, or name the field `attempted` and add `rendered`.
The fix pattern is already written down at `worker/tasks.py:4899-4906`.

---

## D-6 — Three Beat backfill sweeps increment `done` before checking whether anything was produced

**Severity: MEDIUM.**

| Sweep | Counter | Log |
|---|---|---|
| `_backfill_video_posters_async` | `done += 1` at `worker/tasks.py:3972`, **before** `if not poster_uri: … continue` (`:3974`) | `"poster backfill: processed %d video(s)"` (`:3983`) |
| `_backfill_video_camera_regions_async` | `done += 1` at `:4116`, immediately after the `except Exception` that swallows a total failure (`:4113-4114`) | `"video-analysis backfill: processed %d video(s)"` (`:4138`) |
| `_backfill_video_peaks_async` | `done += 1` at `:4215`, before `if not peaks_uri: … continue` (`:4217`) | `"peaks backfill: processed %d video(s)"` (`:4226`) |

A systematically broken detector (bad ffmpeg build, R2 egress failure, a
`detect_overlay_spans` regression) produces `"video-analysis backfill: processed 5 video(s)"`
every hour while writing zero rows — *and* sets a 7-day Redis failure marker on each one, so the
next passes process fewer and fewer and the log count decays quietly rather than going red.

Mitigating, and why this is MEDIUM not HIGH: each failure does emit a per-video
`logger.warning`, and the camera-region sweep versions its markers against
`VIDEO_REGION_VERSION` precisely because this trap "cost a full cycle on Issue 443" (comment at
`:4053-4058`). The project identified the trap and then reproduced its counter half.

**The rule this needs:** `processed=%d written=%d failed=%d`, the shape
`ingestion/signals.py:105-112` already uses correctly.

---

## D-7 — The clip-count funnel breadcrumb and the LLM-scoring degradation are both invisible in production

**Severity: MEDIUM.**

Two sub-findings on the same critical path, `generate_clips`:

**(a) The funnel counter is at DEBUG.** `clip_engine/candidates.py:372-379` logs
`peaks=%d pre_nms=%d after_nms=%d final=%d` with the comment *"Breadcrumb for 'why did I get N
clips?' debugging (Issue 328)"* — at `logger.debug`. Production `LOG_LEVEL` is `INFO`
(`config.py:777`, pinned by `deploy.yml:257`), and `.env.example:306` explicitly forbids DEBUG
in production ("DEBUG can leak request headers (incl. the API key) via httpx … never standing
in prod"). The single raw-vs-kept breadcrumb on the clip pipeline is therefore structurally
unreachable in the only environment that matters. It is a signal with no receiver by
construction.

**(b) A wholly-empty LLM score set produces zero log output.** In
`clip_engine/scoring.py:521-600`: if the model response parses but lacks a `scores` key (`:532`), or
returns an empty array, `scored = []`. The validation loop `for item in scored:` (`:551`) then iterates
nothing — `score_map` stays empty — and every candidate falls through to
`_cold_start_annotate(c)` with `reasoning = "Fallback: signal-only score"`. **No aggregate log
line records that 0 of N candidates kept an LLM score.** The per-item warnings at `:552`,
`:574` and `:588` only fire for *malformed* items, so the total-degradation case is quieter
than the partial one. `score_candidates` is already the subject of Issue 476 (SEV1 — "evaluated
nowhere; every test patches `_ANTHROPIC`"); this is the complementary hole: even in production,
with real traffic, silent 100% degradation of the scorer emits nothing.

**The rule this needs:** promote the funnel breadcrumb to INFO (it contains no PII — four
integers and a duration), and add one `scored=%d/%d` line at the end of `score_candidates`.

---

## D-8 — `generate_improvement_brief` writes `status = ready` from zero analytics rows

**Severity: MEDIUM (paid path + North Star inversion).**

`worker/tasks.py:5448-5469` loads up to 50 `VideoMetrics` rows and builds
`analytics = {"videos_in_db": len(all_metrics), …}` (`:5466`), every average `None` on empty. There is **no
minimum-evidence gate**. With `videos_in_db = 0` and every average `None`, the paid
`web_search`-enabled Claude call still runs against a system prompt that instructs *"Cite the
creator's data (engagement rate, retention, hook performance) as evidence"*
(`improvement/brief.py:52-53`), the row is committed `ImprovementBriefStatus.ready`
(`worker/tasks.py:5530`), and the rails report `logger.info("Improvement brief ready for
creator %s")` (`:5535`) + SSE `done` `"Brief ready."` (`:5536-5541`).

The comment eight lines above the query (`worker/tasks.py:5399-5402`) records that this exact
failure already happened once under the RLS role split — *"the brief query + the VideoMetrics
join return empty under the production role split, and the brief silently writes a `ready` row
with no analytics."* The GUC bug was fixed; **the unconditional `ready` was not.** Any other
route to an empty result set (a new creator, a stalled catalog sync — the subsystem that was
dead for 7 weeks) reproduces the same outcome.

Directly contradicts the North Star ("grounded in your own data, not a guarantee") and spends
real Anthropic tokens plus up to 5 `web_search` uses to do it.

**The rule this needs:** refuse (or mark `insufficient_data`) below a minimum
`videos_in_db`, and surface the count in the brief itself.

---

## Lower-confidence / already-surfaced — listed for completeness, not for filing

- **`worker/tasks.py:1252`** — `logger.info("Published clip %s → youtube %s (%s)", clip_id,
  video_id, "private")` hardcodes the literal `"private"` while the upload uses
  `settings.YOUTUBE_PUBLISH_PRIVACY` (`:1199`), a setting undocumented in `.env.example`
  (`AUDIT_KNOWN_ISSUES.md` §E5) that controls whether uploads land public. The audit log would
  claim `private` for every public upload. **Already recorded** as a `[cleanup]` in
  `docs/assessment/modules/worker.md:47-50` and still unfixed — noting only that it sits on the
  compliance-sensitive publish path, which is a stronger framing than "cleanup".
- **`notify/copy.py:79` + `worker/tasks.py:6928` + `notify/templates/catalog_sync_done.{txt,html}`**
  — the `catalog_sync_done` event type has copy, in-app copy and two templates, and **no
  emitter**: `grep -rn "send_notification.delay"` yields nine call sites, none of them this one.
  Dead copy in a hand-maintained registry; harmless today, but it is the drift signature the
  brief warns about, in a registry no test scans.
- **`_signals_async` (`worker/tasks.py:2392-2415`)** sets `video.ingest_status =
  IngestStatus.done` and emits `ingest_complete` without recording
  `len(timeline["events"])`. A source that yields zero signal events is indistinguishable from a
  healthy one on every rail. Downgraded because `build_signal_timeline` itself logs its drops
  correctly and `extract_audio_events` warns on degenerate audio — the gap is the missing final
  count, not a silent swallow.
- **`clip_engine/reframe.py`** was checked and **cleared**: although the mediapipe/cv2 failure
  paths are DEBUG-only (`:224`, `:303`, `:439`, `:510`), `compute_dynamic_crop` emits an
  aggregate `fallback_pct=%.0f%%` at INFO (`:1286-1300`), so a 100%-dead reframe *is* visible.
  No alert consumes it, but the log line is honest. Not reported as a finding.
- **`_backfill_*` / `_purge_stale_*` / `sweep_*`** silent `if not X: return` boundaries
  (enumerated: `worker/tasks.py:264, 350, 591, 740, 1742, 1858, 3341, 3521, 3948, 4002, 4048,
  4191, 4279, 4301`) were each read. Apart from D-6 they claim nothing on the zero path, so
  they are quiet rather than dishonest. Flagging one for the record:
  `_purge_stale_source_media_async:4279` returns silently on an empty target set, and
  `docs/COMPLIANCE.md` treats that sweep as the ToS §III.E.4.b retention control — a query bug
  there would be a permanently silent compliance no-op with zero log output. No evidence of such
  a bug today; the observability shape is the concern.

---

## Off-class

- `notify/mailer.py:59-81` — `_init_resend()` sets `_resend_initialised = True` after assigning
  `resend.api_key`, but the module-level `_resend_initialised` flag means a
  `TOKEN`/`RESEND_API_KEY` rotation applied via `scripts/flags.py`-style hot reload would not be
  picked up without a process restart. Minor; noted only because the rest of the file is
  restart-agnostic.
- `worker/tasks.py:6737` writes the `NotificationDelivery` row as `sent` **before** the mailer
  call and repairs it to `failed` afterwards on a *separate* session (`:6853-6858`). If the
  worker is SIGKILLed between commit and send (deploy teardown, OOM), the row is permanently
  `sent` for an email that never went out, and the step-5 dedupe guard then suppresses every
  retry. Not my class (it is a crash-window ordering bug, not a hollow check), but it compounds
  D-1.
