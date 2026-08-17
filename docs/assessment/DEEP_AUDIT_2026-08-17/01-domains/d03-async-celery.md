# D03 — Celery, async/await, concurrency, idempotency, backpressure

**Auditor:** domain researcher, deep standards audit 2026-08-17.
**Scope:** `worker/`, `db.py` engine/pool lifecycle, `docker-compose*.yml` worker topology,
idempotency mechanisms across `worker/tasks.py` + `clip_engine/ranking.py` + `billing/`.
**Read-only.** Line references verified on HEAD `1def133`.

---

## Verdict

The *mechanism* choices in this domain are, individually, at or above 2026 standard — the
per-worker singleton loop, the derived `soft < hard < visibility_timeout` invariant, the
workload-class queue split, and the acks_late/prefetch=1/reject_on_worker_lost triple are all
exactly what current Celery documentation prescribes, and each is defended in `DECISIONS.md`
with sources. What is missing is **the seam between the async pattern and Celery's
signal-based timeout model**: `run_async()` uses `loop.run_until_complete()`, which — unlike
the `asyncio.run()` it replaced — does not cancel the pending task when an exception escapes
the loop, so a soft-time-limit hit abandons the coroutine mid-flight with every `finally`,
`__aexit__`, advisory-lock release and temp-file unlink unexecuted. Separately, **backpressure
has no position at all**: the auto-render batch is one message on a one-slot queue with no
priority lane, so ten simultaneous uploads serialise ~80 encodes ahead of any creator's manual
render click.

Two caveats to the brief that shaped this audit, corrected against the code:
`asyncio.run()` is used **zero** times in `worker/tasks.py` (67 × `run_async` on the singleton
loop, `worker/celery_app.py:129`); and prod does **not** run one `--concurrency=2` worker — it
runs `worker --concurrency=4 -Q celery` plus `render-worker --concurrency=1 -Q render`
(`docker-compose.prod.yml`). Queue separation by workload class already exists and is correct.

---

## What the current standard is, with sources

**1. Async inside Celery (2026).** Celery 5.6 shipped in early 2026 and *the worker is still
synchronous* — native `async def` task support has been requested since 2017 and is not there
([celery/celery#3884](https://github.com/celery/celery/issues/3884),
[discussion #9049](https://github.com/celery/celery/discussions/9049)). The three live options
are (a) a sync shell driving a loop, (b) a third-party pool
([`celery-aio-pool`](https://pypi.org/project/celery-aio-pool), recommended by
[StreamHacker 2025-09](https://streamhacker.com/2025/09/22/async-python-functions-with-celery/)
but described there as "not as actively developed"), (c) a different runner
([`celery-asyncio` 6.0.0a3](https://pypi.org/project/celery-asyncio/6.0.0a3/), April 2026, still
alpha; or Taskiq/Dramatiq/Hatchet). **(a) remains the mainstream, lowest-risk choice**, and
within (a), binding one loop per worker child after fork is the SQLAlchemy-blessed pattern
(async engines must be created *after* fork). The project's Issue-39 entry
(`DECISIONS.md:10020`) reaches this conclusion with the right sources and rules out
`celery-aio-pool`, `asgiref`, and gevent explicitly. **I agree with the recorded position.**
The cost it did not price is the cancellation semantics (Finding F1).

**2. Timeouts.** Current guidance: always set `time_limit > soft_time_limit`, with a
**recommended minimum gap of 300 s** for cleanup
([Markaicode 2026 guide](https://markaicode.com/errors/celery-task-timeout-fix/),
[Celery 5.6 exceptions ref](https://docs.celeryq.dev/en/stable/reference/celery.exceptions.html)).
`HARD_LIMIT_MARGIN_S = 300` (`worker/celery_app.py:43`) is exactly this. Deriving the Redis
`visibility_timeout` from the soft limit rather than hand-maintaining it
(`visibility_timeout_s()`, `celery_app.py:49-61`) is **better than standard** — the Redis-broker
visibility caveat is the single most common cause of duplicate long-running tasks and most
teams leave the 3600 default.

**3. Mixed long/short workloads on one node.** Celery's own optimizing guide is unambiguous:
*"If you have a combination of long- and short-running tasks, the best option is to use two
worker nodes that are configured separately, and route the tasks according to the run-time"*
([Optimizing, 5.6.3](https://docs.celeryq.dev/en/stable/userguide/optimizing.html)). `acks_late`
plus `worker_prefetch_multiplier = 1` is the documented back-pressure pairing, and the docs
state the precondition the project also states: tasks must be idempotent. Celery 5.5+ adds
`worker_disable_prefetch` (Redis-only) so a worker fetches only when a slot is free.

**4. Graceful shutdown.** Celery 5.5 added **soft shutdown**
(`worker_soft_shutdown_timeout`) and documented **`REMAP_SIGTERM`** for containerised
environments where SIGTERM is the standard stop signal
([Workers Guide, 5.6.3](https://docs.celeryq.dev/en/stable/userguide/workers.html),
[v5.5.0 release](https://github.com/celery/celery/releases/tag/v5.5.0)). The container-side
half is `stop_grace_period` in compose.

**5. Exactly-once.** The 2026 consensus is unchanged and worth quoting because it is exactly
the project's posture: *"delivery is at-least-once, so the system must be safe to repeat…
what we actually implement is effectively-exactly-once"*
([digitalapplied 2026 reference](https://www.digitalapplied.com/blog/background-job-queue-patterns-2026-engineering-reference),
[oneuptime 2026-01-30](https://oneuptime.com/blog/post/2026-01-30-exactly-once-delivery/view)).
The operative rule for money paths: **store the idempotency key in the same transaction as the
business operation, and acquire it *before* the effect** — not validate a collision afterwards.

**6. Redis priority.** Celery's Redis transport does support `queue_order_strategy: 'priority'`
+ `priority_steps`, with a documented caveat that Redis has no native priority and the
implementation is n lists per queue
([Routing Tasks, 5.6.3](https://docs.celeryq.dev/en/stable/userguide/routing.html)).

**7. RedBeat liveness.** RedBeat stores schedule *and* lock in Redis and writes **nothing** to
disk; file-mtime liveness probes therefore do not work for it — the documented approaches are a
signal-registered PID/heartbeat file, Redis key inspection, or `celery inspect`
([redbeat docs](https://redbeat.readthedocs.io/en/latest/intro.html),
[sibson/redbeat](https://github.com/sibson/redbeat),
[health checks for Celery in k8s](https://medium.com/ambient-innovation/health-checks-for-celery-in-kubernetes-cf3274a3e106)).

---

## Findings

### F1 — `run_until_complete` abandons the coroutine on soft timeout; no `finally` runs *(high)*

`run_async()` (`worker/celery_app.py:129-139`) calls `_LOOP.run_until_complete(coro)`. Celery's
soft time limit is delivered as a signal, raising `SoftTimeLimitExceeded` in the **main thread**.
Because every task body is `async` and spends essentially all of its wall clock suspended at an
`await` (`asyncio.to_thread` for ffmpeg/MediaPipe, network I/O for R2/Anthropic/YouTube), the
exception overwhelmingly surfaces in the loop's own machinery, not inside the coroutine frame.
`run_until_complete` then propagates it and leaves the task **pending on the shared loop**.

`asyncio.run()` — the call Issue 39 replaced — does not have this shape: `asyncio.Runner.close()`
runs `_cancel_all_tasks(loop)`, which throws `CancelledError` into the suspended coroutine so
every `finally` / `__aexit__` executes before the loop dies
([CPython `asyncio/runners.py`](https://github.com/python/cpython/blob/main/Lib/asyncio/runners.py),
[Common Mistakes Using Python3 asyncio](https://xinhuang.github.io/posts/2017-07-31-common-mistakes-using-python3-asyncio.html)).
Issue 39 was right to move off `asyncio.run` for the engine-binding reason; it silently traded
away cancellation-on-teardown and no entry records that trade.

**What does not run when a task is abandoned:**

| Cleanup | Site | Consequence |
|---|---|---|
| `db.tenant_session.__aexit__` | `db.py` (used ~80× in `worker/tasks.py`) | connection never returned → **pool leak**. `_POOL_SIZE=15 + _MAX_OVERFLOW=5` (`db.py:42-44`). On the `--concurrency=1` render worker, 20 abandonments permanently deadlock that process on `QueuePool limit … timed out`. |
| `finally: _rollback_then_unlock(...)` | `worker/tasks.py:168-203`, 8 beat sweeps | advisory lock leaks on a checked-out-forever connection. **The 2026-07-29 hardening (OFF_COURSE_BUGS:61) fixed the "rollback raised inside the finally" case — it cannot fix the "finally never ran" case.** Every subsequent tick of that sweep logs `beat_lock_skip` and no-ops. |
| `finally: tmp_path.unlink(missing_ok=True)` | `worker/storage.py:245-251` (`alocal_path`, `delete=False`) | the full downloaded source (GB-scale) is orphaned in the container's writable layer. No temp sweep and no disk alarm exist anywhere in the repo. |
| `finally: out_path.unlink(...)` | `worker/tasks.py:2683` | orphaned clip mp4. |

**Worse than a leak:** the abandoned coroutine stays scheduled on the *shared* loop. The next
task's `run_until_complete` drives the same loop, so the abandoned render can resume and run to
completion **inside an unrelated task's time budget**, committing `render_status = done` for a
clip a later code path already marked `failed`.

**Concrete scenario.** Auto-render batch of 8 clips (`AUTO_RENDER_TOP_N=8`) on a 22-minute
source crosses `CELERY_SOFT_TIME_LIMIT_S = 3000` (`config.py:753`) during clip 6. The soft signal
lands while the coroutine awaits `asyncio.to_thread(render_clip_file, …)`
(`worker/tasks.py:2654`). `run_until_complete` raises; `render_video_clips` has no
`SoftTimeLimitExceeded` handler (see F2), so `self.retry` fires. Meanwhile: the source temp file
is never unlinked, `_encode_and_upload_clip`'s output temp is never unlinked, the ffmpeg worker
thread keeps encoding (Python cannot cancel a thread), and the retry — enqueued immediately, one
slot, prefetch 1 — starts a second batch that re-downloads the same source while the first
encode still holds a core. 300 s later the hard limit SIGKILLs the child, and
`task_reject_on_worker_lost=True` redelivers the *original* batch as well.

**Guard that would catch it:** none exists. `tests/test_celery_event_loop.py` is 5 tests — loop
reuse, `asyncio.run` fallback, init/shutdown, engine rebind. None asserts that an exception out
of `run_async` leaves zero pending tasks on `_LOOP` and zero checked-out connections.

**Cheapest structural fix:** make `run_async` own teardown the way `asyncio.Runner` does —
wrap `run_until_complete` in `try/except BaseException:` → cancel the task, drain it with
`loop.run_until_complete(asyncio.gather(task, return_exceptions=True))`, then re-raise. ~10
lines in one place, and it converts every abandonment in the file into ordinary cleanup.

*Judgement call on severity:* at ≤100 beta users the soft limit rarely fires, so this is latent
rather than live. I rate it high anyway because the blast radius is a permanently wedged render
worker whose `celery inspect ping` healthcheck (`docker-compose.prod.yml`) still answers — i.e.
autoheal will not restart it, and nothing will page.

---

### F2 — `SoftTimeLimitExceeded` handling is per-task hand-work with no structural guard *(medium)*

`SoftTimeLimitExceeded` subclasses `Exception`. `worker/tasks.py` contains **91 `except
Exception`** blocks and **15 `contextlib.suppress(Exception)`**, and handles the soft timeout
explicitly at only **5** sites: `ingest_video:430`, `transcribe_video:490`, `build_signals:860`,
`render_clip:963`, `render_summary:7156`. The pattern is understood — those five deliberately
mark the entity `failed` and **re-raise without retry** so `RefundOnFailureTask.on_failure`
fires. It was simply never made structural.

Not covered, and each is a task that can plausibly run long:
`render_video_clips:985` (the auto-render default — 8 encodes in one message), `clean_clip:1033`,
`edit_clip:1256`, `generate_clips:917`, and all six LLM-feature tasks.

**Concrete scenario (in-frame branch).** When the soft signal *does* land inside the coroutine
frame during clip 6 of a batch, `_render_video_clips_async`'s per-clip isolation handler
(`worker/tasks.py:2800`, `except Exception … isolate per-clip transient errors`) catches it,
sets clip 6 to `RenderStatus.failed`, increments `RENDER_FAILURES_TOTAL`, and **continues to
clip 7** — starting a fresh `render_clip_file` on a `--concurrency=1` queue that exists
specifically to guarantee one encode at a time (Issue 432, `celery_app.py:64-71`), while the
abandoned encode's ffmpeg is still running. The creator sees a clip marked failed for no reason,
and re-enqueues it by hand; the box runs two encodes.

`generate_clips` is the sharper case: it is a `RefundOnFailureTask` whose only handler is
`except Exception: raise self.retry(exc=exc)` (`worker/tasks.py:925`). A soft timeout during the
30–120 s Opus-5 `score_candidates` round-trip therefore **retries** — and since nothing was
persisted, the `if not existing_clips` guard (`:3747`) does not hold, so the retry pays for the
scoring call again, up to `max_retries=2`.

**Guard:** a one-line meta-test over `worker/tasks.py`'s AST asserting that every
`base=RefundOnFailureTask` task and every task routed to `RENDER_QUEUE` names
`SoftTimeLimitExceeded` before its bare `except Exception`. This is the same shape as the
existing `tests/test_worker_invariants.py` admin-session allowlist, which works well.

---

### F3 — No backpressure position: one batch message, one slot, no priority lane *(medium)*

Search for `backpressure` / `back-pressure` / `prefetch` across all 13,018 lines of
`docs/DECISIONS.md`: **zero hits.** `worker_prefetch_multiplier=1` (`celery_app.py:89`) is set
correctly but justified only as a companion to `acks_late`. Architecture-map D4/16 already names
this gap; this finding is the concrete consequence.

Auto-render enqueues **one** `render_video_clips(video_id, [8 clip ids])` message
(`DECISIONS.md:2100`, sound reasoning — download the source once). Combined with a
`--concurrency=1` render worker and `prefetch_multiplier=1`, that message is indivisible and
non-preemptible.

**Concrete scenario.** Ten creators upload within the same hour (a plausible beta launch day).
Ten batches land on `render`. At the observed per-encode cost of ~60–270 s
(`celery_app.py:66-68` records the live incident: "four on-demand clicks timing out together at
~266 s each on the 4-core VM"), the queue holds ~80 serialised encodes = **1.5–6 hours**. A
creator who then clicks "Render" on a single clip in the Review UI enqueues `render_clip` onto
the *same* queue behind all of it. There is no interactive lane, no per-creator fairness, no
`queue_order_strategy: 'priority'`, and no admission control on queue depth — `CELERY_QUEUE_DEPTH`
is instrumented but `/metrics` self-disables in prod without `METRICS_TOKEN`
(`config.py:1144-1150`, already-known drift). The user-visible outcome is an indefinitely
spinning "Rendering…" with no ETA and no queue-position feedback.

The per-creator daily slowapi ceilings (`limiter.py:136-175`, Issue 228) are cost control, not
backpressure — they cap *daily* jobs, not *in-flight* ones, so they do not bound the depth.

**Standard answer at this scale** (from the Celery optimizing guide + the queue-split the
project already did well): a third queue — `render:interactive` for endpoint-triggered
single-clip renders, consumed by the same worker with `-Q render:interactive,render` so Celery
drains the interactive queue first — or `queue_order_strategy: 'priority'` with the documented
Redis caveats. Either is a compose + `task_routes` change. A per-creator in-flight cap
(one `render_video_clips` per creator, enforced by the `_try_advisory_lock` helper that already
exists) would add round-robin fairness.

---

### F4 — The "celery" queue is documented as I/O-bound and is not *(medium)*

`worker/celery_app.py:69` states the premise the whole queue split rests on:
*"Network/LLM-bound tasks stay on the default `celery` queue."* `_ingest_async` — the largest and
most frequent task on that queue — runs, all via `asyncio.to_thread` and all CPU-saturating:
`probe_duration_s` (`:2069`), `extract_audio_wav` (`:2080`), `frame_dimensions` +
`detect_video_camera_region` (`:2143-2145` — up to nine 60-second decode windows per Issue 443's
consensus), `frame_dimensions` + `detect_overlay_spans` (`:2172-2173`), `compute_peaks`
(`:2605`). `_signals_async` adds librosa `extract_audio_events` (`:2390`).

That queue runs at `--concurrency=4` (`docker-compose.prod.yml`) on a 4-core VM, alongside the
render worker's libx264 encode and `uvicorn --workers 2`.

**Concrete scenario.** Four concurrent ingests each run a MediaPipe camera-region consensus while
the render worker encodes. Eight-plus CPU-hungry processes contend for 4 cores; the encode's
wall clock inflates ~2–3×; `render_clip_file`'s ffmpeg subprocess timeout (Issue 42 formula,
`DECISIONS.md:10609`) trips → `RENDER_FAILURES_TOTAL` → the clip is marked `failed`. That is
**precisely the Issue-432 symptom the render queue was created to eliminate**, reproduced from
the other side. The queue split solved render-vs-render contention and left render-vs-ingest
contention untouched, because the premise said ingest was I/O-bound.

The fix is small: route the ingest CPU passes to the `render` queue (they are already
CPU-class), or drop the default queue to `--concurrency=2` and let the CPU class share one lane.
Either way the premise comment needs correcting — it is currently load-bearing and wrong.

---

### F5 — No `stop_grace_period`: every deploy SIGKILLs in-flight work, and a sweep was built instead *(medium)*

No service in `docker-compose.yml`, `docker-compose.staging.yml`, or `docker-compose.prod.yml`
sets `stop_grace_period`, and no `stop_signal` override exists. Docker's default is **10 s**.
Deploys are fully automatic on merge to `main` with no window and no drain step
(`deploy.yml` → `docker compose up -d`).

So every deploy sends SIGTERM to `worker` and `render-worker`, Celery begins a warm shutdown that
waits for the running task, Docker SIGKILLs ~10 s later, and a 60–270 s encode or a multi-minute
ingest dies mid-flight. `task_reject_on_worker_lost=True` redelivers the message — which is the
*correct* recovery — but nothing cleans up: `alocal_path`'s `delete=False` temp
(`worker/storage.py:245`) and `_encode_and_upload_clip`'s output temp survive, on a single
droplet with **no disk-space alarm and no temp sweep anywhere in the repo**.

The telling artefact is `sweep_stale_renders` (Issue 359, every 15 min, `worker/schedule.py:42`),
whose docstring says it exists for *"renders orphaned by a worker SIGKILL (OOM / deploy
teardown)"*. The project correctly diagnosed the symptom and built a recovery sweep for a root
cause that is one compose line (`stop_grace_period: 600s`) plus Celery 5.5's
`worker_soft_shutdown_timeout` / `REMAP_SIGTERM`. This is the taxonomy's §E4 pattern —
*"identifies the correct structural fix and then does not make it structural."*

Blocking half of it: `celery[redis]==5.4.0` (`requirements.txt:36`, April 2024) predates both
5.5 features. `stop_grace_period` works today regardless.

---

### F6 — The idempotency house pattern guards persistence, not spend *(medium)*

Answering question 4 directly: **yes, this should be one decision entry plus one helper**, and
the helper is nearly written already. The primitives in `worker/tasks.py` are excellent
individually — `_try_advisory_lock` / `_rollback_then_unlock` (`:168-229`), the Redis
render-start marker `_amark_render_started` / `ais_render_stale` (`:117-165`), the SAVEPOINT +
`UNIQUE(video_id)` minute-deduction dedupe (`DECISIONS.md:10534`), and the genuinely clever
**DEFERRABLE** `uq_clips_video_rank` compare-and-set (`clip_engine/ranking.py:394-438`, Issue
361). Nine or ten separate entries; no entry names the pattern.

The gap the un-named pattern leaves is a specific one, and it is on the money path:

> **every guard makes the *persist* idempotent; none makes the *paid call* idempotent.**

`persist_ranked_clips` (`clip_engine/ranking.py:401`) reads `load_existing_clips`, and if two
executions both pass, the deferred UNIQUE makes the loser's COMMIT raise `IntegrityError`, which
is caught at `:427` and converted into "return the winner's set". Correct — no duplicate rows,
no cascade-deleted feedback. But that `IntegrityError` arrives **after** the loser has already
paid for a full Opus-5 `score_candidates` call.

**Concrete scenario.** `build_signals` commits `Signals` + `ingest_status=done`, enqueues
`generate_clips` (`worker/tasks.py:882`), and is SIGKILLed before ack. Redelivery: `_signals_async`
short-circuits correctly (`:2364`), then enqueues `generate_clips` a **second** time. Both
executions read `existing_clips == []` (`:3747`) because the first is still inside its 30–120 s
LLM round-trip. Two Opus-5 scoring calls are billed to the creator's ledger; one result is
discarded. (`AUDIT_KNOWN_ISSUES.md:86` files the *enqueue*; the point here is that the
downstream `if not existing_clips` guard is widely assumed to have closed it, and it has not —
it closes the duplicate-row half, not the double-spend half.)

The standard rule is the one the 2026 references state: **acquire the idempotency key before the
effect.** The helper that would make this one line at every paid site:

```python
# worker/idempotency.py
@asynccontextmanager
async def once(key: str, *, ttl_s: int) -> AsyncIterator[bool]:
    """Redis SET NX PX lease. Yields True to the single winner; False to everyone
    else. Released on clean exit so a crashed holder's lease expires with the TTL
    rather than wedging the key. DB-side sibling: worker.tasks._try_advisory_lock."""
```

Call sites become `async with once(f"score:{video_id}", ttl_s=600) as mine: if not mine: return`
placed *above* `score_and_rank`, above each LLM-feature task's Anthropic call, and above
`_publish_to_youtube_async`. The existing `_try_advisory_lock` is the same idea with Postgres
semantics; the DECISIONS entry should name both, say which to use when (advisory lock when the
critical section is already inside a session; Redis lease when the effect is an external paid
call with no session held), and state the invariant "the key is taken before the effect, and
the persist-side UNIQUE is the backstop, not the guard."

---

### F7 — Beat has zero liveness on the live deployment, and the test that says otherwise is a string match *(medium)*

`docker-compose.prod.yml`'s `beat` service is the only worker-family service with **no
`healthcheck` and no `autoheal=true` label**. `app`, `worker`, and `render-worker` all have both.
A beat process that hangs rather than exits therefore stops every scheduled task silently — and
that set includes `purge_stale_youtube_analytics`, which `worker/schedule.py:58` and
`docs/RUNBOOKS.md:341` both identify as a **YouTube ToS §III.E.4.b compliance obligation**, not
merely an operational one. `BEAT_LOCK_SKIPS_TOTAL` exists and is emitted correctly
(`worker/tasks.py:228`) but `/metrics` self-disables in prod without `METRICS_TOKEN`, so it has
no scrape target (already-known drift, not re-filed).

The reason this reads as covered: `tests/test_beat_ha.py` is a 130-line suite named for beat HA
in which **every assertion is a substring match over YAML/config text**, and
`test_liveness_probe_checks_heartbeat_file:67-71` asserts the string `"celerybeat-schedule"`
appears in `deploy/charts/creatorclip/templates/beat/deployment.yaml`. RedBeat writes nothing to
disk — schedule and lock live in Redis
([redbeat docs](https://redbeat.readthedocs.io/en/latest/intro.html)) — so that probe's
`stat -c %Y /tmp/celerybeat-schedule` can never succeed, and the chart's own comment
(`beat/deployment.yaml:37-39`, *"RedBeat still updates on each scheduling tick"*) is factually
wrong. The test does not merely fail to catch this: it **pins** it.

I am deliberately not filing the chart itself (`AUDIT_BRIEF.md` §9 descopes the K8s track). The
finding is that the only artefact in the repo carrying the words "beat HA" asserts liveness for a
platform that has never run, using a claim that is false, while the platform that *is* running has
no beat liveness at all. Shape-wise this is a candidate for the §6 "vacuous green" hunt.

**Concrete scenario.** Beat's Redis connection wedges (the broker is a single container; AOF
fsync stalls and connection resets are the common cause). The process stays up, `restart:
unless-stopped` never triggers, no healthcheck exists, no metric is scraped. `poll_clip_outcomes`,
the lifecycle-email scan, the Stripe↔ledger reconciliation, and the 30-day ToS purge all stop.
The precedent for how long that goes unnoticed is in the taxonomy: `health-check.yml`'s schedule
died 2026-06-17 and nobody noticed for six weeks.

**Fix at this scale:** a `healthcheck` on `beat` that reads Redis directly —
`redis-cli --raw HGET redbeat::<key> …` or simply asserting `redbeat::lock` has a live TTL —
plus `labels: [autoheal=true]`. Five lines of compose, and it replaces a K8s-shaped runbook that
cannot be executed on the VM.

---

## What is genuinely right here

Naming these precisely, because several are better than what most teams ship:

1. **`visibility_timeout_s()` (`worker/celery_app.py:49-61`) derives the
   `soft < hard < visibility` invariant instead of hand-maintaining it**, with the Celery Redis
   visibility caveat quoted and the failure it prevents named (double ffmpeg encode / double
   paid LLM call). This is the single most commonly-missed Celery+Redis footgun and it is closed
   structurally — raising the soft limit raises the visibility timeout automatically.
2. **`acks_late=True` + `worker_prefetch_multiplier=1` + `task_reject_on_worker_lost=True`** is
   exactly the documented combination, and the comment at `:90-93` states the precondition it
   depends on (idempotent tasks, Issue 61) rather than assuming it.
3. **The workload-class queue split (Issue 432)** is the answer Celery's own optimizing guide
   gives for mixed long/short workloads, arrived at from a real production incident, with the
   `-n render@%h` / `%%h` compose-interpolation trap documented inline so it cannot regress.
4. **The per-worker singleton loop + post-fork `db.recreate_engine()`** is the correct
   SQLAlchemy-asyncio-on-fork pattern, and Issue 39's alternatives-ruled-out section is still
   accurate in 2026 — Celery 5.6 has no native async worker and the third-party pools remain
   thin.
5. **`_rollback_then_unlock` (`worker/tasks.py:168-203`)** is a sophisticated read of Postgres
   semantics: rollback first so the unlock statement can run, and if that fails,
   `session.invalidate()` so the *connection* is discarded and Postgres frees the session-level
   lock at session end. Most codebases do not get past `finally: unlock()`.
6. **`_keyset_batches` (`:246-269`)** bounds sweep memory with keyset pagination rather than
   `OFFSET`, on exactly the two unbounded result sets that needed it.
7. **The DEFERRABLE `uq_clips_video_rank` as a compare-and-set** (`clip_engine/ranking.py:394-438`)
   with the discriminating `if "uq_clips_video_rank" not in str(exc.orig): raise` is a
   genuinely good trick — a lost race is distinguished from a real integrity bug rather than
   both being swallowed.
8. **Idempotency guards are placed at the top of the async body, not the shell** (`:2364`,
   `:3680`, `:3747`), so they survive redelivery *and* direct invocation, and each carries the
   issue number that motivated it.

---

## Decisions this domain needs but does not have

1. **Backpressure and fairness.** Zero `DECISIONS.md` hits for backpressure/prefetch/priority.
   Needs a position on: an interactive vs. batch render lane, per-creator in-flight caps, what
   happens at queue depth N, and what the creator is told while waiting.
2. **The idempotency house pattern, as one entry plus `worker/idempotency.py`** — with the
   explicit rule that the key is acquired *before* the paid effect (F6). Ten entries describe
   instances; none states the rule, so every new task re-derives it and the paid-call half keeps
   being missed.
3. **Cancellation semantics of the shared loop.** Issue 39 priced the engine binding correctly
   and never priced teardown. Needs either the `run_async` cancel-and-drain wrapper or a written
   acceptance that abandonment leaks pool connections and advisory locks.
4. **Per-workload-class time limits.** One global `CELERY_SOFT_TIME_LIMIT_S = 3000` covers a
   5-second notification email and an 8-encode render batch. Standard is per-task
   `soft_time_limit` sized to the work; only `distill_style_prefs` (`:1573`) sets one.
5. **Whether the auto-render batch should stay one message.** Download-once is right; one
   indivisible 8-encode message is what makes it non-preemptible and un-budgetable. A chord
   over a shared cached source, or a batch that re-enqueues itself after K clips, would keep the
   download saving without the head-of-line block.
6. **Celery version posture.** Pinned at 5.4.0; 5.5 shipped soft shutdown + `REMAP_SIGTERM` +
   `worker_disable_prefetch`, 5.6 shipped in early 2026. No entry states whether the project
   tracks Celery minors or pins deliberately.
7. **"The box is full" — what it means and who finds out.** No disk alarm, no temp-file sweep,
   no CPU saturation alarm, no queue-depth alarm on a single droplet running ffmpeg + MediaPipe.
8. **The DB connection budget for the deployment that exists.** `docs/DEPLOYMENT.md:84-105`
   documents a Cloud SQL / PgBouncer / KEDA inequality; the live VM's arithmetic is
   `(2 uvicorn + 4 worker children + 1 render child) × (20 main + 4 admin + 5 event_log)` ≈ 200
   against a `pgvector/pgvector:pg16` container with no `max_connections` override (default 100).
   Latent at beta demand — pools grow lazily and each task holds 1–2 sessions — but the number
   has never been written down for the deployment that is actually running, and
   `_make_admin_engine`'s comment (`db.py:60`) still says *"Worker concurrency is
   `--concurrency=2`"* when prod runs 4 + 1. (`prepare_threshold=None` for a PgBouncer that
   isn't deployed is already named in the architecture map; not re-filed.)
