# ingestion — assessed 2026-07-01

Slice: `ingestion/__init__.py` (empty), `ingestion/audio.py`, `ingestion/signals.py`,
`ingestion/transcribe.py`. Pure transformation layer: a media/audio path goes in, a dict
comes out. No DB queries, no creator-scoped tables, no OAuth/token handling, no Anthropic
calls live in this slice — so the isolation/token/LLM rubric rows are `n/a` here and are
enforced at the worker caller boundary (verified: the only creator-owned read is
`worker/tasks.py:1136-1139` `RetentionCurve.video_id == video.id`, and both heavy entry
points are offloaded via `asyncio.to_thread` at `worker/tasks.py:1064` and `:1145`, bounded
by `asyncio.wait_for(..., TRANSCRIPTION_TIMEOUT_S)`).

`audio.py` + `transcribe.py` changed this run (Issue 334). **Verified fixed** since the
2026-06-24 assessment: the audio-OOM cap is now present (`audio.py:59-78` — `librosa.get_duration`
probe + `AUDIO_ANALYSIS_MAX_DURATION_S=14400` truncation + 16 kHz resample), the
degenerate <2-sample WAV guard (`:84-94`), and the AssemblyAI `None`-timestamp filter
(`transcribe.py:260-264`). **Verified STILL open:** the AssemblyAI error-status →
silent-empty-segments concern flagged last run was NOT addressed — Issue 334 only added the
`None`-timestamp filter, not a `transcript.status` check (see finding 1).

## Findings

- [SEV2] ingestion/transcribe.py:250-251 — `aai.Transcriber().transcribe(audio_path)` is
  normalized with **no `transcript.status` check** (the previously-flagged concern; NOT
  fixed by Issue 334). Verified against AssemblyAI's official Python SDK docs: `.transcribe()`
  does **not raise** on a failed job — it returns a transcript with
  `status == aai.TranscriptStatus.error`, and in that state `transcript.words` is `None`, so
  `_normalize_assemblyai` (transcribe.py:254-277) silently returns
  `{"source": "assemblyai", "segments": []}`. Traced downstream: `_transcribe_async`
  (worker/tasks.py:1075-1088) commits that empty transcript and the task **succeeds** — no
  exception, so `RefundableTask.on_failure` (worker/tasks.py:88-146, "fires only on TERMINAL
  failure") never runs. Net: the creator is charged, gets an empty transcript + garbage clip
  windows, and the docs/COMPLIANCE.md "automatic refund on terminal ingest failure" guarantee
  does not fire. Blast radius bounded — AssemblyAI is config-selectable, not the default
  (`deepgram`), and Deepgram's SDK *does* raise on API errors. | fix: after `transcribe()`,
  `if transcript.status == aai.TranscriptStatus.error: raise RuntimeError(f"AssemblyAI transcription failed: {transcript.error}")`
  so the worker retry→refund chain engages; add a unit test feeding an error-status stub.
  Source: https://github.com/AssemblyAI/assemblyai-python-sdk/blob/master/README.md
  (status-check pattern; verified 2026-07-01).

- [SEV2] ingestion/audio.py:183-184 — `generate_waveform_image` passes
  `showwavespic=...:bg_color={bg_color}` but **`bg_color` is not a valid `showwavespic`
  option**, so ffmpeg exits non-zero and the function raises `RuntimeError` on *every* real
  call. Verified locally against the repo's own ffmpeg with the code's exact filter string:
  `Error applying option 'bg_color' to filter 'showwavespic': Option not found`; the same
  command without `bg_color` succeeds. `ffmpeg -h filter=showwavespic` lists only
  `size/s, split_channels, colors, scale, draw, filter` — no background option. The function
  has **no production caller** (grep: only its own def, the config comment, and tests), and
  its test (`tests/test_signals.py:250-273`) monkeypatches `subprocess.run`, so the broken
  invocation is never exercised — a green test over dead-and-broken code. | fix: drop the
  `bg_color` token (background is transparent by default) or composite a background via a
  `color` source + `overlay` filterchain; then either wire the helper into `_signals_async`
  or delete it until the Editor surface consumes it. Add a test that runs real ffmpeg (or
  asserts the built `cmd` contains no invalid option). Source: `ffmpeg -h filter=showwavespic`
  (ffmpeg on PATH, verified 2026-07-01); https://ffmpeg.org/ffmpeg-filters.html#showwavespic.

- [SEV2] ingestion/audio.py:99-114, 21-24 — RMS and ZCR are normalized to the **per-file
  peak** (`rms / (rms.max() + 1e-8)`), so `_ENERGY_THRESHOLD=0.6` / `_SILENCE_THRESHOLD=0.03`
  are relative to that file, not anchored to physical loudness. Consequence: a uniformly
  quiet clip still "spikes" at 0.6 of its own peak and a constantly-loud room **never emits a
  silence event**, which undermines the dead-air-elimination principle the downstream clip
  engine relies on. Industry standard for loudness/silence is an **absolute** measure —
  ITU-R BS.1770-4 / EBU R128 gate silence at an absolute −70 LUFS, not a per-file ratio.
  (heuristic-quality; needs-runtime-confirmation on real-clip impact.) | fix: threshold
  against dBFS via `librosa.amplitude_to_db` (or a `pyloudnorm` LUFS meter) with an absolute
  floor, or at minimum log a warning when the absolute peak is so low the relative signal is
  meaningless. Sources: https://tech.ebu.ch/publications/r128 ;
  https://en.wikipedia.org/wiki/EBU_R_128 (silence gate −70 LUFS; verified 2026-07-01).

- [cleanup] ingestion/audio.py:241 — in `_emit`, `end_s = float(times[min(end_idx-1, len-1)])
  + frame_duration` can push the final run's `end_s` past `duration_s`. The Issue 327 geometry
  validator (`signals.py:25-53`) only rejects `start_s > duration_s`, not `end_s > duration_s`,
  so the slightly-over event is written into `signals.timeline_jsonb`. Downstream scoring
  clamps the grid index, so it is cosmetic in the stored JSON. | fix: pass `duration_s` into
  `_emit` and `end_s = min(end_s, duration_s)` before append, or extend
  `_event_geometry_is_valid` to also cap `end`.

- [cleanup] .env.example:91 — `TRANSCRIPTION_BACKEND=whisperx` contradicts `config.py:197`
  (default `deepgram`) and CLAUDE.md, which names Deepgram the GPU-free launch default. An
  operator copying `.env.example` verbatim silently flips off the intended default. | fix:
  set the example to `deepgram`; keep `whisperx` in the inline comment as the alternative.

- [cleanup] ingestion/audio.py:30-35 — `_EMPTY_EVENTS` module constant is defined but never
  referenced; the <2-sample early-return (`:89-94`) builds its own dict literal instead. Dead
  code. | fix: either return `_EMPTY_EVENTS` (with `duration_s` overridden) from the guard, or
  delete the constant.

- [cleanup] ingestion/transcribe.py:250 — a fresh `aai.Transcriber()` (with its own HTTP
  client/pool) is constructed per call, contrary to the module-singleton pattern used for
  Deepgram (`_deepgram_client`) and the lock-guarded `_ASSEMBLYAI_READY` init right above it.
  Bounded churn (non-default backend). | fix: cache a module-level `Transcriber` singleton
  alongside `_ASSEMBLYAI_READY`.

- [cleanup] ingestion/signals.py:91 — `"relative_retention": rrp or 0.0` uses truthiness where
  line 84 uses explicit `rrp is not None`. Identical behaviour for `float | None`, but a
  readability trap. | fix: `rrp if rrp is not None else 0.0`.

Observation (not a finding): `transcribe.py:104` logs the full joined transcript via `vlog`.
`vlog` is a hard no-op unless `settings.verbose_logging_enabled` (prod requires the explicit
`VERBOSE_LOGGING_ALLOW_PROD=true` opt-in per `verbose.py:3-8`), so this is gated and low-risk,
but note it dumps creator-authored spoken content to the verbose log when enabled.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — Deepgram streams via `with open()`; `subprocess.run` bounded by timeout; no DB/handle leak (1 cleanup: per-call Transcriber) |
| 2 Concurrency & scale | ok — sync functions offloaded via `asyncio.to_thread`; audio-OOM cap now present (Issue 334); Deepgram streams the file handle |
| 3 Security & compliance | ok — no tokens/PII in log lines; Deepgram `mip_opt_out` enforced; no creator-scoped queries in slice (isolation enforced at caller) |
| 4 Clip-quality | 1 finding — peak-relative loudness thresholds weaken the dead-air principle |
| 5 Anthropic SDK | n/a (no LLM calls in this module) |
| 6 Cleanliness & typing | 3 cleanups (dead `_EMPTY_EVENTS`, per-call Transcriber, `rrp or 0.0`) |
| 7 Error handling / API | n/a (not a router) |
| 8 Config & paths | 1 cleanup — `.env.example` backend default mismatch; paths handled via `Path`/absolute |

## Module verdict
NEEDS-WORK — no blockers, but the previously-flagged AssemblyAI error-status → silent-empty
"success" (no refund, garbage clips) is still unfixed, and the Issue 188 waveform helper is
both dead code and 100% broken (invalid `bg_color` ffmpeg option) with a mock-masked test.
