# Assessment — LLM, Rendering & Video-Creation Pipeline (logic gaps + observability)

**Date**: 2026-06-28
**Scope**: `clip_engine/*`, `ingestion/*`, `youtube/ingest.py`, `worker/tasks.py` (pipeline),
`dna/brief.py`, `chat/*`, `knowledge/*`, `worker/anthropic_stream.py`, `observability.py`.
**Lens**: what is *logically* missing, and how debuggable a production failure is (verbose logging).
**Method**: four parallel read-only sweeps + direct spot-verification of the highest-impact claims.
Findings are tagged **[verified]** (I read the lines myself) or **[reported]** (sub-agent finding,
plausible, not line-checked). Nothing in the codebase was modified.

---

## TL;DR — the through-line

The pipeline is **structurally sound and idempotent**: every stage early-returns on empty input,
falls back gracefully (cold-start scoring, preference-rerank failure, Claude API errors), and the
clip-generation guard prevents duplicate clips on Celery retries. The eval harness is genuinely
strong (16 scenarios incl. adversarial geometry + prompt injection).

The gaps cluster in **two themes**, not in core algorithm correctness:

1. **Silent failure modes** — three classes of input/result anomaly pass through with no error and
   no log: (a) `stop_reason == "max_tokens"` LLM truncation, (b) malformed signal events
   (negative / inverted / out-of-bounds timestamps), (c) ffmpeg subprocess detail (command + full
   stderr) discarded on failure.
2. **Asymmetric observability** — failure paths and token *cost* are well-logged; the *creative
   decision path* (peaks→candidates→NMS→scores→ranks) and *per-stage timing* are nearly invisible.
   You can see **that** a video produced 1 clip in 4 minutes; you cannot see **why 1** or **where the
   4 minutes went**.

Neither theme is a correctness bug today — they are **debuggability + robustness** gaps that will
bite during a real beta incident.

---

## P0 — Silent failures (fix first; each is a 1–5 line change)

### 1. LLM `max_tokens` truncation is never detected **[verified]**
`stop_reason` is inspected in exactly one place — `chat/runner.py:98`, only for `"tool_use"`.
**No LLM call site checks for `stop_reason == "max_tokens"`.** Every JSON-producing call
(`clip_engine/scoring.py` max_tokens=1200, `knowledge/titles.py` =2000, `dna/brief.py` =2000,
`chat/intake.py` =1500) silently truncates on overflow. Scoring/titles then hit `JSONDecodeError`
and fall back to signal-only scores / empty titles — i.e. **a truncated response is indistinguishable
in logs from a model that legitimately returned nothing.**
**Fix**: after each `.create()` / `get_final_message()`, `if msg.stop_reason == "max_tokens":
logger.warning("LLM output truncated", extra={"task": ..., "model": ...})`. Cheap, high-value.

### 2. ffmpeg command + full stderr discarded on failure **[verified]**
`clip_engine/render.py:70-76` (`_run`): on non-zero exit it raises `result.stderr[:500]` and the
**command itself is never logged** (success-only log at the end). Same truncation pattern in
`youtube/ingest.py` (500) and `ingestion/audio.py` (400). When a render fails in prod, the one
artifact you need — the exact ffmpeg invocation + the tail of its stderr — is gone.
**Fix**: `logger.debug("ffmpeg %s: %s", label, shlex.join(cmd))` before `subprocess.run`, and on
failure log the **last** ~2000 chars of stderr (ffmpeg puts the real error at the end, so `[:500]`
from the front is the least useful slice).

### 3. Malformed signal events pass through silently **[reported, high-confidence]**
`clip_engine/window.py`, `clip_engine/candidates.py:_find_setup_start`, and
`ingestion/signals.py:build_signal_timeline` accept events with `end_s < start_s`, negative
timestamps, or `timestamp_s > duration_s` with no validation and no log. A single bad retention/silence
event can anchor a clip's setup to the wrong point, and there is **zero logging anywhere in
`window.py` / `candidates.py`** to explain the result.
**Fix**: one `WARNING`-and-skip guard at the timeline-build boundary (`signals.py`) so bad data is
rejected once, with a count, rather than silently distorting candidates downstream.

### 4. Clip timing not validated before ffmpeg in the render task **[reported]**
`worker/tasks.py` `_render_clip_async` snapshots `start_s/end_s/duration` from the DB but does not
assert `end_s > start_s` / `duration > 0` before invoking the renderer. `render.py:302` does guard
`duration <= 0`, so this is defense-in-depth, but the task-level error would be far clearer than a
deep ffmpeg failure if a row is ever corrupt. **Fix**: explicit guard + log with `clip_id` at task entry.

### 5. Face-detection fallback is invisible **[reported]**
`render.py` falls back to frame-center crop on any OpenCV/face-detection failure, logged only at
DEBUG/at the exception. If detection silently fails for a whole batch, every Short is mis-framed and
nothing surfaces at INFO. **Fix**: INFO log on fallback + ideally a counter so a systemic
model/codec problem is visible.

> Note (nuance I verified): the sub-agent flagged a *zero-duration cut crash* in
> `clip_engine/filler.py:151`. **Not a live bug** — `filler.py:160` (`if end > start`) and `:172`
> (`if cut_end > cut_start`) already guard cut emission. Worth an explicit `phrase_dur <= 0` skip for
> clarity, but it does not currently reach ffmpeg.

---

## P1 — Observability gaps (debugging a beta incident)

### 6. Prometheus LLM-token metric is emitted by only ~half the LLM call sites **[verified]**
`record_llm_tokens` (the `llm_tokens_total` counter) is called in `chat/intake.py`, `chat/runner.py`,
`knowledge/hooks.py`, `routers/insights.py`, `routers/clips.py` — **but not** in
`clip_engine/scoring.py`, `dna/brief.py`, `knowledge/titles.py`, `knowledge/chapters.py`,
`knowledge/thumbnails.py`, `analysis/brief.py`, `improvement/brief.py`. Those modules *do* log token
counts and *do* write the DB usage ledger (`increment_usage`, guarded by `test_usage_coverage.py`),
so **billing is complete** — but the **Grafana token/cost-by-feature dashboard is blind** to scoring,
DNA-brief and most knowledge features (the heaviest LLM consumers). This is the one I'd rank highest in
P1 because it silently under-reports exactly where the spend is.
**Fix**: add the one-line `record_llm_tokens(...)` call alongside the existing `increment_usage`
calls in those modules. (Consider wrapping both in a single `log_llm_result()` helper so they can
never again drift apart — DRY per CLAUDE.md.)

### 7. No per-stage timing anywhere in the pipeline **[verified via observability sweep]**
`celery_task_duration_seconds` exists, but only at whole-task granularity. There is **no timing** for:
ffmpeg subprocess, transcription backend call, librosa signal extraction, the Claude scoring call,
or ingest sub-steps (probe → extract → upload). "Ingest took 10 minutes" cannot be decomposed from
metrics — you must hand-correlate progress-event timestamps. **Fix**: wrap the expensive
spans (ffmpeg, transcribe, score) in `time.perf_counter()` and log a `duration_s` + emit a histogram.
OTel auto-instrumentation (already wired for Anthropic/SQLAlchemy/Redis) covers the LLM HTTP latency;
the **ffmpeg/librosa/transcription** spans are the blind spots.

### 8. No `video_id` in scoring logs; no decision-path logging in candidates/ranking **[verified]**
`clip_engine/scoring.py` logs `creator_id` but not `video_id`. `candidates.py` and `window.py` have
**no logging at all** — peak count, NMS suppressions, `MIN_CLIP_S` filter rejections, final candidate
count are all invisible. `derive_skip_reason` exists but is computed after the fact and not emitted.
The literal question a beta user will ask — *"why did I only get one clip from this video?"* — is
**not answerable from logs today**. **Fix**: DEBUG-level breadcrumbs in `extract_candidates`
(`"video=%s peaks=%d after_nms=%d filtered_min_clip=%d"`) and add `video_id` to scoring/ranking logs.

### 9. No single correlation key across the per-video pipeline **[verified]**
`request_id` propagates API→Celery well, but each pipeline stage (ingest→transcribe→signals→
generate→render) is a separate Celery task with its own id; the *lineage* is not logged. Tracing one
video end-to-end means grepping by `video_id` across several task ids and correlating timestamps by hand.
**Fix**: log `video_id` on every stage's started/done event (some already do) and emit a
`pipeline_stage_transition` event carrying `(video_id, prev_task_id, next_task_id, stage)`.

### 10. `LOG_LEVEL=DEBUG` is effectively unusable in prod **[verified]**
`config.py:443-445` correctly warns that DEBUG leaks `x-api-key` via httpx request-header logging, so
DEBUG is discouraged. The consequence: the verbose breadcrumbs recommended above must be authored as
**INFO/WARNING**, not hidden behind DEBUG, or they will never run where they're needed. (Alternatively:
raise the `httpx`/`anthropic` loggers to WARNING explicitly so app-DEBUG becomes safe to enable.)

---

## P2 — Robustness hardening (lower probability)

- **NaN propagation [reported]**: defensive `min/max` clamping is implicit throughout
  (`scoring.py`, `ranking.py`, `preference/*`). An upstream NaN in a signal/feature would survive
  (`min/max(NaN)` can return NaN; `sort` parks it last). Add explicit `math.isnan` checks at the
  feature/score handoff rather than relying on clamp side-effects.
- **Audio memory ceiling [reported]**: `ingestion/audio.py` loads the whole file via librosa at 16 kHz;
  `transcribe.py` guards size via `TRANSCRIPTION_MAX_MB` but the librosa path and the hardcoded 60 s
  waveform-render timeout don't scale with duration → OOM / timeout risk on long sources.
- **Config-validation gaps [reported]**: `DECAY_HALF_LIFE_DAYS=0` → `λ=inf` silently; missing
  `TRANSCRIPTION_MAX_MB` → `AttributeError` at call time rather than startup. Assert at config load.
- **Preference-model unpickler allowlist [reported]**: `preference/model.py` restricted-unpickler
  allowlist is hand-derived from current LightGBM/sklearn versions; a dependency bump could break model
  load with no version guard. Document the regenerate procedure + stamp a serialization version.
- **`edits.py` silent clamp [reported]**: user-supplied cut `end` is silently clamped to clip duration
  with no log; the right-edge tolerance (`+MIN_KEEP_SEGMENT_S`) has no cumulative cap.
- **AssemblyAI word parsing [reported]**: `transcribe.py` divides `w.start/1000.0` without a `None`
  guard, unlike the Deepgram/WhisperX paths which defend.

---

## What is genuinely good (don't regret-refactor these)

- **Idempotency**: `generate_and_rank_clips` early-return guard; render task `with_for_update` lock +
  status short-circuit; `minute_deductions` UNIQUE idempotency key.
- **Graceful degradation everywhere**: cold-start signal-only scoring, honest preference-weight ramp,
  preference-rerank failure falls back to DNA ranking and still commits.
- **Cost/cache discipline**: `scoring.py:290-330` — correct Sonnet-4.6 1024-token cache-floor gating
  (Issue 315) plus thorough token/cache-hit logging. This is the model the other LLM modules should copy.
- **Redaction**: multi-layer (`redact.py` blocklist → JSON formatter backstop → event-log → Sentry
  before-send), dependency-free, conservative. OTel content-capture explicitly disabled.
- **Streaming resilience**: `worker/anthropic_stream.py` wraps each forwarded event so a Redis hiccup
  can't abort the stream before `get_final_message()`.

---

## Suggested sequencing (if these become issues)

1. **One PR, P0 #1–#5 + P1 #6** — all are ≤5-line additive logging/guards, no behavior change, no new
   deps. Biggest debuggability ROI. Wrap LLM result-logging in a shared `log_llm_result()` helper so
   `record_llm_tokens` + `increment_usage` + `stop_reason` check can't drift (#1, #6).
2. **P1 #7–#9** — per-stage timing + decision-path breadcrumbs + `video_id`/lineage. One module-by-module
   pass; pairs naturally with the existing progress-event emitters.
3. **P2** — schedule as hardening; each is independent and low-risk.

> Per CLAUDE.md these are **CHECK-phase findings**, not yet built. None were fixed inline (they are
> out-of-scope-of-a-single-task observations); the P2 items belong in `docs/OFF_COURSE_BUGS.md` and the
> P0/P1 items as `docs/issues.md` entries once triaged.
