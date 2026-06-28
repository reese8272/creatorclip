# CreatorClip — Lane L21: Edge-Case Hardening (pre-production test sweep)

> **Purpose.** A production-readiness test backlog covering the *whole* project. Each issue groups one
> subsystem's **uncovered** edge cases (cross-referenced against the existing `tests/*.py` so these are
> genuine gaps, not duplicates) into an acceptance-criteria checklist. Writing these tests is expected
> to **flush out latent defects** — the suspected ones are called out per issue under **"Suspected
> defects to confirm."**
>
> **Companion docs.** Findings folded in from `docs/assessment/LLM_RENDER_VIDEO_ASSESSMENT.md`
> (the LLM/render/observability review). This lane slots into `docs/issues.md` as **Lane L21**;
> issue numbers **327–340** continue from the existing 181–326 range.
>
> **Per-issue legend** (matches `docs/issues.md`): `Status` OPEN/DONE · `Wave` · `Lane` L21 ·
> `Size` S/M/L · `Verify` `local` (unit lane, mocks DB/Redis) / `integration` (real PG+pgvector via
> docker-compose, `-m integration`) / `render-env` (needs ffmpeg/real media) / `external` (live API).
> Run via `/issue-workflow N`.

---

## ⚠️ Read first — the systemic finding (governs Issue 327)

One pattern recurs in **every** signal/geometry/cut module: range checks compare only against a
**positive threshold**, so **inverted (`end < start`), negative, or out-of-bounds timestamps pass
through silently** and either distort output or produce empty results with no error and no log.
Sources: `window.py`, `candidates.py`, `scoring.py:compute_features`/`_transcript_context`,
`signals.py:build_signal_timeline`, `captions.py:_iter_clipped_words`, `filler.py:detect_cut_segments`,
`edits.py:_invert_cuts`.

**Honesty note — verified, NOT live crashes (don't chase as bugs; still add the guard + test):**
- `candidates.py:303` — NMS IoU **is** guarded: `iou = inter / union if union > 0.0 else 0.0`. No divide-by-zero.
- `render.py:409` — punch-in **is** guarded: `if 0.0 <= peak_offset_s <= duration:` before the filter.
- `filler.py:160/172` — cut emission **is** guarded by `if end > start` / `if cut_end > cut_start`.
- `render.py:302` — render **does** guard `duration <= 0`.

The value of Issue 327 is therefore **defense-in-depth + observability + regression locks**, not
firefighting. Validate once at the data-entry boundary (`signals.py`), log+reject malformed events with
a count, and assert the geometry invariants downstream so a future refactor can't silently reintroduce
the silent-pass behavior.

**Suspected defects that ARE worth confirming first** (each has a dedicated AC below):
| # | Suspected defect | Issue |
|---|---|---|
| 1 | `generate_clips` task may not inherit `RefundOnFailureTask` (no refund on terminal failure) | 336 |
| 2 | Ingest idempotency short-circuit trusts an existing WAV **without integrity check** | 336 |
| 3 | Prometheus `record_llm_tokens` **missing** at ~9 LLM call sites (cost dashboard blind) | 332 |
| 4 | No call site detects `stop_reason == "max_tokens"` (silent LLM truncation) | 331 |
| 5 | `transcribe.py` AssemblyAI path divides `w.start/1000` with **no `None` guard** | 334 |
| 6 | `decay.py` `DECAY_HALF_LIFE_DAYS=0` → `λ=inf`, silent wrong weights (no startup assert) | 338 |
| 7 | `preference/model.py predict_score` assumes binary classifier (`proba[0][1]`) | 338 |
| 8 | `oauth.py` `expires_at IS NULL` (legacy row) → `timedelta` on `None` → `TypeError` | 340 |
| 9 | JWT `alg:"none"` / wrong-key / future-`iat` not explicitly tested (security) | 340 |

---

## Issue 327: Cross-cutting malformed-geometry validation + property tests

**Status** `DONE` (2026-06-28) · **Wave** W0 · **Lane** L21 · **Size** `M` · **Verify** `local`
**Blocked by** nothing — **ready now** · **Coordinate (hot files)** `ingestion/signals.py`, `clip_engine/window.py`, `clip_engine/candidates.py`
**Shipped:** `ingestion/signals.py::_event_geometry_is_valid` + a sanitize pass in `build_signal_timeline`
that drops inverted/negative/non-finite/out-of-bounds events at the build boundary with a WARNING +
dropped-count; defense-in-depth `if i1 <= i0: continue` guard in `clip_engine/window.py`;
`tests/test_geometry_validation.py` (13-case predicate table + boundary/log/regression + window guard).
Deterministic adversarial tests used instead of adding a Hypothesis dependency pre-launch. The
downstream candidates/scoring invariant asserts (`setup<peak<end`, `compute_features` logging) remain
in **Issue 328**.

**Problem.** See the systemic finding above. Malformed timestamps are accepted silently across the
signal/geometry layer.

**Approach.** Add one shared validator (e.g. `ingestion/signals.py:_valid_event()` /
`clip_engine/_geometry.py`) that rejects events/words/ranges with `end <= start`, negative times, or
`t > duration_s`, **logs a WARNING with a dropped-count**, and is applied at the timeline-build
boundary so downstream modules receive clean data. Add geometry-invariant asserts downstream. Lock with
a **property-based test** (Hypothesis) feeding random/adversarial timelines.

**Acceptance criteria (edge cases to cover)**
- [ ] `build_signal_timeline` drops + WARN-logs events with `end_s < start_s`, `start_s < 0`, `timestamp_s > duration_s`; returns a clean timeline; emits a dropped-count.
- [ ] `build_signal_array` (`window.py`) with an inverted event no longer writes a reversed slice.
- [ ] `window.py` negative `value_scale` no longer contributes inverted signal (clamp at source).
- [ ] `extract_candidates` post-snap invariant `setup_start_s < peak_s < end_s` holds for all outputs (assert + test, incl. the `candidates.py:334` re-assignment path).
- [ ] `compute_features` with `setup_start_s > end_s` returns zeroed features **and** logs (not silent).
- [ ] `_transcript_context` rejects negative/inverted segment times instead of capturing the wrong window.
- [ ] Property test: random timelines (incl. NaN, inf, duplicate-timestamp, all-negative) never raise and never violate the geometry invariant.

**Tests** — `tests/test_geometry_validation.py` (new) + Hypothesis property test; extend `test_signals.py`, `test_clip_engine.py`.

---

## Issue 328: clip_engine geometry / scoring-features / ranking edge suite

**Status** `OPEN` · **Wave** W0 · **Lane** L21 · **Size** `M` · **Verify** `local`
**Coordinate** `tests/test_clip_engine.py`, `tests/test_scoring.py`

**Acceptance criteria**
- [ ] `extract_candidates`: empty signal, no peaks, single peak, all candidates < `MIN_CLIP_S` → `[]` (each distinctly asserted) and a DEBUG breadcrumb (`peaks=… after_nms=… filtered=…`).
- [ ] NMS: two identical zero-duration candidates → no crash, `union==0` branch hit (lock the existing guard).
- [ ] `_find_setup_start`: empty events; no silence/energy in window → `max(0, peak_s - window_s)`.
- [ ] `snap_to_sentence_boundary`: `peak_s=0` (backward window negative); empty words; no boundary found.
- [ ] `_signal_score`: all-zero features → 0.0; verify monotonic in each feature; output always in `[0,1]`.
- [ ] `rank_candidates`: empty list → `[]`; all-equal scores → deterministic, documented tie-break; missing `"score"` key → 0.0.
- [ ] `rerank_with_preference`: **NaN preference score** must not corrupt sort order — add explicit `math.isnan` guard + test **[BUG? — confirm sort is currently undefined]**.
- [ ] Idempotency: `generate_and_rank_clips` called twice does not duplicate or rescore clips.

**Tests** — extend `test_clip_engine.py`, `test_scoring.py`, `test_preference_rerank.py`.

---

## Issue 329: render.py + reframe.py edge suite (ffmpeg + crop geometry)

**Status** `OPEN` · **Wave** W0 · **Lane** L21 · **Size** `M` · **Verify** `local` (logic) + `render-env` (real ffmpeg)
**Coordinate** `tests/test_render.py`, `tests/test_reframe.py`

**Acceptance criteria**
- [ ] `_run`: on non-zero exit, the raised error includes the **command** (`shlex.join`) and the **tail** of stderr (not `[:500]` from the front); covered by a fake-subprocess test. _(folds in assessment P0 #2)_
- [ ] `_run`: `OSError`/`PermissionError`/`FileNotFoundError` from `subprocess.run` are handled, not bare-propagated; `TimeoutExpired` path asserted.
- [ ] `render_clip_file`: `start_s < 0`, `end_s > video_duration`, `start_s >= end_s` → guarded with a clear error + `clip_id` in the log **before** ffmpeg is invoked. _(P0 #4)_
- [ ] Face-detection fallback to center-crop logs at **INFO** (+ ideally a counter), distinguishing "no face" from "corrupt frame". _(P0 #5)_
- [ ] `_frame_dimensions` parse-failure → `(1920,1080)` default **logged**, so silent quality loss is visible.
- [ ] `crop_w > frame_w` path produces a valid crop (no negative/over-wide crop).
- [ ] Loudnorm: unparseable measurement → flat render **with a WARNING** (lock existing behavior).
- [ ] `render_cleaned_clip_file`: unsorted/overlapping `keep_ranges` → rejected or normalized (not a malformed filter graph).
- [ ] `reframe.build_crop_center_track`: `start_s<0` / `end_s>video_duration` (seek past EOF) handled.
- [ ] `reframe._read_frame_cv2`: `CAP_PROP_FPS` returns 0/NaN → frames don't all map to frame 0 **[BUG? — confirm]**.
- [ ] `reframe.smooth_crop_track`: non-monotonic input timestamps don't break EMA/pan-clamp.
- [ ] `clamp_crop_x` with `crop_w > frame_w` → no silent zero-movement failure.

**Tests** — extend `test_render.py`, `test_reframe.py`, `test_render_style.py`; gate real-ffmpeg cases behind the `render-env` marker.

---

## Issue 330: captions / filler / edits cut-list edge suite

**Status** `OPEN` · **Wave** W0 · **Lane** L21 · **Size** `S` · **Verify** `local`
**Coordinate** `tests/test_captions.py`, `tests/test_filler.py`, `tests/test_edits.py`

**Acceptance criteria**
- [ ] `captions.build_ass_subtitles`: empty segments, no overlap with clip window, all-empty word text → returns `None` **and logs the skip with context** (today it's silent).
- [ ] `_iter_clipped_words`: words with `end < start` are dropped, not yielded to event builders.
- [ ] Word timestamps that are NaN/inf → handled (no malformed ASS times).
- [ ] `filler.detect_cut_segments`: `clip_end_s <= clip_start_s` → `[]` (explicit guard); inverted phrase/gap durations don't pass the positive-threshold checks **(add explicit `<=0` skip even though `end>start` guards emission)**.
- [ ] Multi-word filler spanning the clip boundary ("you|know" across the edge) — document/verify match behavior.
- [ ] `percent_removed`: overlapping cuts not double-counted (call `merge_adjacent_cuts` first or assert).
- [ ] `edits.validate_user_cuts`: NaN via `math.isnan` (not `x==x`); non-numeric input → `CutValidationError` not `ValueError` leak; cumulative right-edge over-reach capped; **silent `end` clamp now logs**.
- [ ] `edits._invert_cuts`: unsorted/overlapping input cuts → defined behavior (sort/normalize) not silent-wrong keep-ranges **[BUG? — confirm]**.

**Tests** — extend `test_captions.py`, `test_filler.py`, `test_edits.py`.

---

## Issue 331: LLM `stop_reason == "max_tokens"` truncation detection

**Status** `OPEN` · **Wave** W0 · **Lane** L21 · **Size** `S` · **Verify** `local`
**Coordinate** `worker/anthropic_stream.py`, `clip_engine/scoring.py`, `dna/brief.py`, `chat/runner.py`, `chat/intake.py`, knowledge/*

**Problem.** `stop_reason` is inspected only in `chat/runner.py:98` for `"tool_use"`. Every JSON/text
call truncates silently on overflow; scoring/titles then fail JSON parse and fall back — indistinguishable
in logs from an empty response. _(assessment P0 #1.)_

**Acceptance criteria**
- [ ] A shared helper warns when `stop_reason == "max_tokens"` (model + task in `extra`), used by every `.create()` / `get_final_message()` site.
- [ ] `scoring.py`: mocked truncated response → WARNING emitted AND signal-only fallback still works.
- [ ] `chat/runner.py`: a round returning `stop_reason=="max_tokens"` mid-loop exits cleanly with a flagged-truncated result (not silently fed to the next round).
- [ ] `dna/brief.py`, `knowledge/titles.py`, `chat/intake.py`: truncation surfaces (test with mocked Anthropic message).

**Tests** — `tests/test_llm_truncation.py` (new); mocked Anthropic client with `stop_reason="max_tokens"`.

---

## Issue 332: Prometheus `record_llm_tokens` coverage + unified result-logging helper

**Status** `DONE` (2026-06-28) · **Wave** W0 · **Lane** L21 · **Size** `S` · **Verify** `local`
**Coordinate** `observability.py`, `clip_engine/scoring.py`, `dna/brief.py`, `knowledge/*`, `analysis/brief.py`, `improvement/brief.py`
**Shipped:** `observability.record_llm_metric(model, usage)` — a dual-shape adapter normalizing the
Anthropic `Usage` object (non-streaming) and the `stream_and_emit` usage dict (streaming) onto
`record_llm_tokens`, wired into the 10 previously-blind LLM modules (scoring, dna/brief, analysis,
improvement, titles, thumbnails, chapters, clip_titles, clip_captions, clip_explain) next to each
existing billing-ledger write; `tests/test_llm_metrics_coverage.py` (source guard over all 13 LLM
modules, mirrors `test_usage_coverage.py`) + 3 adapter unit tests in `test_observability.py`.
**Scope note:** this shipped the *metric coverage* half. The `stop_reason == "max_tokens"` truncation
warning stays in **Issue 331** (could later fold into a single `log_llm_result` over this adapter — the
helper was kept single-responsibility to avoid coupling the metric to truncation logging).

**Problem.** `record_llm_tokens` (the `llm_tokens_total` counter) is called in `chat/intake`, `chat/runner`,
`knowledge/hooks`, `routers/insights`, `routers/clips` — **but not** in `scoring.py`, `dna/brief.py`,
`knowledge/titles|chapters|thumbnails|clip_*`, `analysis/brief.py`, `improvement/brief.py`. Those *do*
write the DB usage ledger (billing is complete, guarded by `test_usage_coverage.py`) but the Grafana
**cost-by-feature dashboard is blind to the heaviest consumers**. _(assessment P1 #6.)_

**Approach.** Introduce one `log_llm_result(task, model, usage, stop_reason)` helper that does all three —
`record_llm_tokens` + token log line (with **model id**, per P1 #8) + the truncation check from 331 — so
the three can never drift again (DRY). Add a coverage test mirroring `test_usage_coverage.py`.

**Acceptance criteria**
- [ ] Every LLM task increments `llm_tokens_total` (asserted by a coverage test enumerating call sites — mirror `test_usage_coverage.py`'s approach).
- [ ] Token log lines include the resolved model id.
- [ ] `cache_read` / `cache_creation == None` (older SDK) coerced to 0 without error.
- [ ] Helper is the single path; a grep-style test forbids a raw `messages.create` LLM site that skips it.

**Tests** — `tests/test_llm_metrics_coverage.py` (new); extend `test_observability.py`.

---

## Issue 333: LLM robustness edge suite (parse / injection / errors / cache / loop bounds)

**Status** `OPEN` · **Wave** W0 · **Lane** L21 · **Size** `M` · **Verify** `local`
**Coordinate** `tests/test_scoring.py`, `test_chat.py`, `test_titles.py`, `test_hooks.py`, `test_chapters.py`, `test_prompt_safety.py`

**Acceptance criteria**
- [ ] Truncated/garbage JSON from Claude (`{"candidates":[{"title":"…`) → graceful fallback in scoring/titles/hooks/thumbnails (each asserted, not just scoring).
- [ ] Prompt injection via untrusted content (transcript / title / identity free-text) — payloads like `</untrusted>`, `[CLIP]:`, control chars — do not break section boundaries (extend `test_prompt_safety.py` to clip_titles/clip_captions).
- [ ] API-error catch blocks log status/`retry_after` where available, not just `exc_type` (mock `RateLimitError` with headers). _(assessment P1, error-context.)_
- [ ] Cache-floor boundary: prefix at exactly 4096 chars (~1024 tok) — `cache_control` applied iff floor cleared; below floor must **not** pay the 2.0× write multiplier.
- [ ] `chat/runner`: final iteration forces `tools=None`; a model that still emits a `tool_use` block is handled; loop count is exactly `0..max_iters`.
- [ ] `chat/intake`: `MAX_INTAKE_TURNS` runaway guard (history > 24) returns the form fallback without an LLM call.
- [ ] `knowledge/chapters`: zero silences → evenly-spaced `MIN_CHAPTERS` fallback; one very long silence → `MAX_CHAPTER_PERIOD_S` boundary.
- [ ] `_text_of` / empty-content message (thinking+tool_use only, no text) → defined behavior.

**Tests** — extend the named suites; mocked Anthropic client throughout.

---

## Issue 334: ingestion edge suite (transcribe / audio / signals)

**Status** `OPEN` · **Wave** W0 · **Lane** L21 · **Size** `M` · **Verify** `local` + `render-env` (real ffmpeg/librosa)
**Coordinate** `tests/ingestion/test_transcribe.py`, `tests/test_signals.py`, `tests/test_ingest.py`

**Acceptance criteria**
- [ ] **AssemblyAI** word with `None` start/end → guarded (no `TypeError` on `w.start/1000`); parity with Deepgram/WhisperX paths. **[BUG? — confirm]**
- [ ] `_guard_audio_size`: missing `TRANSCRIPTION_MAX_MB` setting fails fast at config load, not `AttributeError` at call time.
- [ ] Empty/silent WAV (all-zero RMS) → no spurious energy/laughter events; `rms.max()+1e-8` path asserted.
- [ ] Zero-duration / missing-audio-stream source → clear error, not a downstream crash.
- [ ] Very-long source: duration-based cap or chunking so librosa doesn't load unbounded audio; waveform timeout scales with duration (not hardcoded 60s).
- [ ] Deepgram normalize: empty utterances, utterance with no words, fallback to `channels[0].alternatives[0]`.
- [ ] WhisperX: segment/word missing start/end → 0.0 defaults; empty segments → `[]`.
- [ ] `transcribe_audio` logs transcript word-count/duration/segment-count (observability gap).

**Tests** — extend `tests/ingestion/test_transcribe.py`, `test_signals.py`; synthetic silent/long WAV fixtures.

---

## Issue 335: youtube edge suite (oauth / analytics / data_api / ingest)

**Status** `OPEN` · **Wave** W1 · **Lane** L21 · **Size** `M` · **Verify** `local` (recorded fixtures) + `integration`
**Coordinate** `tests/test_oauth_lifecycle.py`, `test_analytics.py`, `test_data_api.py`, `test_youtube_errors.py`

**Acceptance criteria**
- [ ] `get_valid_access_token`: `expires_at IS NULL` (legacy row) → no `TypeError` on timedelta. **[BUG? — confirm]**
- [ ] Returning creator re-auth where Google omits `refresh_token` → existing refresh token preserved, not nulled.
- [ ] Refresh `invalid_grant` (revoked) vs other 400 (`invalid_client`) → distinct handling; **no plaintext provider error leaked** to the client.
- [ ] Redis unavailable during refresh lock → fail-open path covered; lock TTL expiring mid-flight → no double-refresh corruption.
- [ ] `fetch_video_metrics` / `fetch_retention_curve` / `fetch_demographics`: empty rows (metric/retention unavailable) → `None`/empty, not crash.
- [ ] `fetch_audience_activity`: all-zero day totals / unparseable dates.
- [ ] `data_api.parse_duration_seconds`: malformed ISO-8601 → defined behavior (today silently 0.0 — decide reject vs default + log).
- [ ] `classify_video_kind`: zero-duration video.
- [ ] `clamp_ingest_field`: unicode/emoji title truncation doesn't split a surrogate pair.
- [ ] `check_captions_available`: captions absent → `False`.
- [ ] `ingest.probe_duration_s` / `extract_audio_wav`: missing audio stream, corrupt file → stderr **tail** captured.
- [ ] `download_via_ytdlp`: `YTDLP_ENABLED=False` blocks; enabled-but-fails surfaces `FileNotFoundError` clearly.

**Tests** — recorded fixtures only (never hit live YouTube, per CLAUDE.md); extend the named suites.

---

## Issue 336: worker pipeline task edge suite (retry / timeout / idempotency / refund)

**Status** `OPEN` · **Wave** W1 · **Lane** L21 · **Size** `M` · **Verify** `local` + `integration`
**Coordinate** `tests/test_worker_pipeline.py`, `test_issue_105_worker_idempotency.py`, `test_generate_clips_retry_integration.py`

**Suspected defects to confirm**
- **`generate_clips` may not inherit `RefundOnFailureTask`** → terminal failure leaves minutes deducted with no refund. Confirm; if true, fix is in scope here.
- **Ingest WAV short-circuit** (`tasks.py:~820`) returns early when `source_uri` already points to a WAV **without verifying the file is complete/non-corrupt** → a partially-failed prior run feeds a bad WAV downstream.

**Acceptance criteria**
- [ ] Retry exhaustion → `RefundOnFailureTask.on_failure` fires exactly once; refund idempotent under redelivery; `refund_for_video` raising is best-effort (workflow survives).
- [ ] `generate_clips` terminal failure → refund occurs (proves the base-class fix). **[BUG? — confirm]**
- [ ] Ingest idempotency short-circuit validates WAV integrity (size/probe) before skipping. **[BUG? — confirm]**
- [ ] `SoftTimeLimitExceeded` raised mid-render (not at entry) → clip status set + retry, and the timeout-vs-`timeout_s` mismatch is logged.
- [ ] `render_clip` no longer catches `BaseException`-class signals (KeyboardInterrupt/SystemExit re-raise).
- [ ] `_render_clip_async`: `render_uri` set but status still `running` → reconciled to `done`, not skipped-stale.
- [ ] `publish_to_youtube`: idempotency hit where `youtube_video_id` is NULL; quota consumed but upload fails → row `failed`, asymmetry asserted/logged.
- [ ] Duplicate Beat fire / advisory-lock contention on catalog sync → second run no-ops, lock released in `finally`.
- [ ] DNA build `job_id` idempotency races → `IntegrityError` caught, no partial commit.

**Tests** — extend named suites; `integration` lane for the DB-races and publish-quota cases.

---

## Issue 337: observability / progress / event_log / redact / health-metrics edge suite

**Status** `OPEN` · **Wave** W1 · **Lane** L21 · **Size** `M` · **Verify** `local` + `integration`
**Coordinate** `tests/test_observability.py`, `test_progress.py`, `test_event_log.py`, `test_health.py`

**Acceptance criteria**
- [ ] Correlation id absent (no request ctx, default `"-"`) flows through logs/events/metrics without error.
- [ ] `log_event` field-name collision (`event="ts"`) doesn't shadow reserved JSON keys.
- [ ] `record_llm_tokens` with `None` cache fields coerced to 0.
- [ ] `_sentry_before_send` with non-dict `extra`/`request.data` → no crash (type guard).
- [ ] `redact.py`: token/email/secret/jwt/bearer/api_key all redacted across the log formatter, event-log sink, and Sentry path (parametrized over the blocklist); a non-sensitive field is preserved.
- [ ] `progress.sync_emit` XADD ok but EXPIRE fails → event survives, logged (partial-failure path).
- [ ] `aacquire_slot`: at capacity + concurrent INCR/EXPIRE → cap exceeded by ≤1, documented; non-owner read denied.
- [ ] `aread_since`: no new events before `block_ms` → empty list / keepalive.
- [ ] `/health`: postgres / redis / storage each down → `degraded` with the right component flagged; probe timeout caught.
- [ ] `/metrics`: bearer-token gating (set vs unset); `collect_saturation_gauges` raising still returns 200 with stale gauges.
- [ ] `event_log.record_event`: non-UUID `creator_id` → `cid=None`, event still recorded; write failure is best-effort.

**Tests** — extend named suites; `integration` for health probes + RLS-context event rows.

---

## Issue 338: preference model / decay / features / train edge suite

**Status** `OPEN` · **Wave** W0 · **Lane** L21 · **Size** `S` · **Verify** `local`
**Coordinate** `tests/test_preference.py`, `test_preference_rerank.py`, `test_retrain_preference_integration.py`

**Acceptance criteria**
- [ ] `predict_score`: single-class / multi-class model → `proba[0][1]` assumption guarded (don't `IndexError`). **[BUG? — confirm binary-only]**
- [ ] `decay.recency_weight`: `DECAY_HALF_LIFE_DAYS=0` rejected at config load (assert `>0`), not silent `λ=inf`. **[BUG? — confirm]**
- [ ] `decay`: future timestamp (clock skew) clamped to age 0 → weight ≈ 1.0; half-life and 2×-half-life exact values.
- [ ] `sample_weight`: `performed_well=None` vs `False` semantics asserted (different multiplier application).
- [ ] `clip_features`: `dna_match=None` → 0.0; NaN `dna_match` explicitly handled (not propagated to sort).
- [ ] `FEATURE_NAMES` order regression lock (cross-training stability).
- [ ] `train.build_and_save`: <2 samples / all-positive / all-negative / single label → `None`, no crash; `PREFERENCE_MAX_TRAINING_LABELS` truncation logged.
- [ ] Serialize→deserialize round-trip preserves `label_count`; **corrupt/truncated/tampered blob** → `from_bytes` returns `None`/rejects (extend RCE test to corruption).
- [ ] `load_latest`: concurrent cache loads → one `joblib.load`; corrupt blob → `None` not crash.

**Tests** — extend named suites.

---

## Issue 339: API router surface edge suite (clips / review / publications / SSE)

**Status** `OPEN` · **Wave** W1 · **Lane** L21 · **Size** `M` · **Verify** `local` (TestClient) + `integration`
**Coordinate** `tests/test_clips.py`, `test_review.py`, `test_publish.py`, `test_scheduled_publish.py`, `test_tasks_sse.py`

**Acceptance criteria**
- [ ] Invalid `clip_id`/`video_id`: non-UUID → **422**; well-formed-but-absent → **404**; other creator's id → **403/404 isolation** (assert no leak) — across clips/review/publications.
- [ ] Pagination/list caps (`_LIST_LIMIT` 100/50): at cap, over cap → truncation is **signaled**, not silent.
- [ ] `review.submit_feedback`: `trim_start_s`/`trim_end_s` negative, inverted, NaN, or outside `[clip.start_s, clip.end_s]` → 422; `feedback_note` over max length → 422; empty-list vs `None` tags.
- [ ] `clips.submit_cuts`: each `CutValidationError.code` (empty/invalid_segment/out_of_bounds/overlap/kept_too_short/removed_too_much) → 422 with the code in the detail.
- [ ] `clips.render`/`clean`/`ingest`: `running`/`pending` state → 409 with stable `detail.code`; `aset_owner` Redis failure → clip still queued, `stream_url=None`.
- [ ] `clean_preview`: missing/None transcript → empty cuts; `percent_removed >= 30` boundary warning.
- [ ] `publications`: `scheduled_at` in the past / exactly now / naive datetime → 422; confirm/cancel from wrong state → 409; cancel-then-confirm race → 409; duplicate Beat `task_id`.
- [ ] `title/caption/explanation` generators: empty transcript, `dna_brief=None`, missing `signals_jsonb["principle"]` → graceful.
- [ ] SSE `task_events`: unknown task → 404; ownership TTL elapsed → 404; > slot cap → SSE error event; `Last-Event-ID` reconnect past the 600s hard cap.

**Tests** — extend named suites with `TestClient`; `integration` lane for the publish/state-machine races.

---

## Issue 340: security / auth / crypto / billing / compliance / isolation edge suite

**Status** `OPEN` · **Wave** W0 (unit) / W1 (RLS integration) · **Lane** L21 · **Size** `L` · **Verify** `local` + `integration` (RLS needs real PG)
**Coordinate** `tests/test_auth.py`, `test_crypto.py`, `test_billing*.py`, `test_rls_isolation_integration.py`, `test_consent.py`

> Highest priority — security is load-bearing per CLAUDE.md. Several items are explicit-attack tests
> that simply don't exist yet.

**Acceptance criteria — Auth/JWT**
- [ ] JWT `alg:"none"` → rejected (explicit bypass-attempt test). **[BUG? — confirm PyJWT config rejects]**
- [ ] JWT signed with wrong/compromised secret → 401.
- [ ] JWT missing `sub` claim entirely → 401 (distinct from non-UUID `sub`).
- [ ] Future `iat` (clock skew) policy asserted.

**Acceptance criteria — Crypto/OAuth**
- [ ] `decrypt` with `TOKEN_ENCRYPTION_KEY` unset → fails fast at config, not deep `.encode()` error.
- [ ] Rotated key: ciphertext from `TOKEN_ENCRYPTION_KEY_PREVIOUS` still decrypts; `PREVIOUS` empty-string vs `None`.
- [ ] Corrupt/invalid-base64 ciphertext → `TokenDecryptError`, never plaintext leak.

**Acceptance criteria — Billing**
- [ ] `video_minutes` negative/zero `duration_s` → defined (reject vs `max(1,…)`), tested.
- [ ] `deduct_for_video`: balance == minutes exact boundary; concurrent deduct across **different** videos stays atomic.
- [ ] `grant_minutes`: empty-string `stripe_session_id` (falsy ≠ None) → idempotency still holds; double-grant race.
- [ ] `_trial_expired`: `trial_ends_at == now` boundary; naive `trial_ends_at` from DB → tz-aware compare; trial end-date not leaked as PII in error detail.

**Acceptance criteria — Isolation / Compliance**
- [ ] Every creator-scoped query (chat tools, clips, videos, insights, improvement) with **missing RLS context** (`app.creator_id` unset) returns zero rows / errors — never cross-tenant data. (RLS = `integration`.)
- [ ] Account deletion: OAuth revocation fails but media purge succeeds → consistent end state; partial storage-purge (one prefix 404, other ok) → continues; 6th `DELETE /me` in an hour → 429.
- [ ] Consent: `terms_version`/`privacy_version` over `VARCHAR(32)` → handled; COPPA age attestation recorded.
- [ ] Prompt injection / PII via `channel_title` from YouTube → sanitized/escaped before storage+prompt use.
- [ ] `notify` idempotency key: length boundary (255/256/257) and disallowed-char rejection.
- [ ] `api_key.hash_api_key`: binary/unicode raw key → handled; `CreatorApiKey` orphaned by deleted `Creator` FK → 401.
- [ ] No response promises virality (extend the structural `test_honesty`/`test_compliance_no_virality` to the new clip_* LLM surfaces).

**Tests** — extend the named suites; mark RLS/account-deletion/billing-race cases `integration`.

---

## Sequencing & coverage note

- **W0, `local`, startable now:** 327, 328, 329(logic), 330, 331, 332, 333, 338, 340(unit). These are
  pure unit/logic on the dev box (no Docker/ffmpeg/live API) — the highest-ROI first wave.
- **W1, `integration`/`render-env`:** 335, 336, 337, 339, 340(RLS), 329(real-ffmpeg) — need real
  Postgres+pgvector, Redis, ffmpeg, or recorded YouTube fixtures.
- Folding the **[BUG?]** items into tests is the point: a red test on 336 (refund/WAV-integrity), 334
  (AssemblyAI `None`), 338 (`DECAY=0`/binary-classifier), 340 (JWT `alg:none`/null `expires_at`) confirms
  a real defect and the same PR carries the fix.
- Per CLAUDE.md these are **CHECK-phase** entries. Confirmed defects should also be logged to
  `docs/OFF_COURSE_BUGS.md`; promote to `docs/issues.md` proper when scheduled.
