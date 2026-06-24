# ingestion — assessed 2026-06-24

Slice: `ingestion/__init__.py` (empty), `ingestion/audio.py`, `ingestion/signals.py`,
`ingestion/transcribe.py`. Pure transformation layer: an audio/media path goes in, a
dict comes out. No DB queries, no creator-scoped tables, no OAuth/token handling, no
Anthropic calls live in this slice — so isolation/token/LLM rubric rows are n/a here and
are enforced at the worker caller boundary (verified: `worker/tasks.py:1010-1013` scopes
the only creator-owned read as `RetentionCurve.video_id == video.id`, and both heavy
entry points are offloaded via `asyncio.to_thread` at `worker/tasks.py:937-940` and
`:1019`). Code is materially unchanged since the 2026-06-09 assessment except the
Issue 251 MIP opt-out and the Issue 188 waveform helper; the prior SEV2 cluster is
confirmed still unfixed by re-reading.

## Findings

- [SEV2] ingestion/transcribe.py:208-209 — `aai.Transcriber().transcribe(audio_path)` is
  normalized with no `transcript.status` check. The AssemblyAI SDK returns a transcript
  with `status == error` (it does not raise), in which case `transcript.words` is `None`
  and `_normalize_assemblyai` (transcribe.py:212-229) silently returns
  `{"source": "assemblyai", "segments": []}`. The job then "succeeds" with an empty
  transcript — no Celery retry, and no terminal-failure refund (docs/COMPLIANCE.md billing
  section: refund only fires on a *terminal* failure), plus garbage downstream clip
  windows. AssemblyAI is config-selectable, not the default, so blast radius is bounded to
  self-host/hosted-alt operators. (needs-runtime-confirmation on the exact SDK error path,
  but the code has zero status check.) | fix: after `transcribe()`,
  `if transcript.status == aai.TranscriptStatus.error: raise RuntimeError(f"AssemblyAI transcription failed: {transcript.error}")`
  so the worker retry/refund chain engages. Pin with a unit test feeding an error-status
  transcript.

- [SEV2] ingestion/audio.py:47 — `extract_audio_events` calls `librosa.load(..., sr=16000,
  mono=True)` which decodes the whole waveform into memory (~115 MB/hour float32 at 16 kHz
  mono) with no size/duration cap, while the transcription path has the `_guard_audio_size`
  /`TRANSCRIPTION_MAX_MB` guard (transcribe.py:50-67). A pathological multi-hour or
  mis-extracted WAV across concurrent workers is an OOM vector — and unlike transcription
  this runs in the `_signals_async` `asyncio.to_thread` pool inside the same process.
  (verified: no `AUDIO_MAX_*` in config.py.) | fix: add a duration probe via
  `librosa.get_duration(path=...)` (or `Path(audio_path).stat().st_size`) at the top of
  `extract_audio_events`, fail fast with `ValueError` above an `AUDIO_MAX_DURATION_S`/
  `AUDIO_MAX_MB`, document in `.env.example` next to `TRANSCRIPTION_MAX_MB`.

- [SEV2] ingestion/audio.py:52,55 — RMS and ZCR are normalized to the per-file peak
  (`rms / (rms.max() + 1e-8)`), so `_ENERGY_THRESHOLD=0.6` / `_SILENCE_THRESHOLD=0.03`
  (audio.py:21-22) are *relative*, not anchored to physical loudness. A uniformly quiet
  recording still "spikes" at 0.6 of its own peak; a constantly loud room never emits a
  silence event — which undermines principle 5 (Dead-air elimination) for the downstream
  clip engine that consumes these events. | fix: threshold against dBFS
  (`librosa.amplitude_to_db`) or a rolling noise floor; at minimum log a warning when the
  absolute peak is so low the relative signal is meaningless.

- [SEV2→cleanup] ingestion/audio.py:177 — in `_emit`,
  `end_s = float(times[min(end_idx-1, len(times)-1)]) + frame_duration` can push the final
  run's `end_s` past `duration_s` (the last frame is centred before the audio end, then a
  full hop is added), so an out-of-range event is written into `signals.timeline_jsonb`.
  Blast radius is narrow: downstream scoring clamps the grid index
  (`clip_engine/scoring.py:105`: `i1 = min(len(signal), int(end_s/RESOLUTION_S)+1)`), so it
  is largely cosmetic in the stored JSON rather than a scoring defect. | fix: pass
  `duration_s` into `_emit` and clamp `end_s = min(end_s, duration_s)` before append.

- [cleanup] ingestion/audio.py:76-138 — `generate_waveform_image` (Issue 188) has **no
  caller anywhere in the repo** (grep-verified: only its own definition + docstring). It is
  dead code on the hosted path; the Editor "waveform surface" it was added for does not
  invoke it. | fix: either wire it into `_signals_async` (write a waveform PNG alongside the
  signals row, best-effort like the docstring promises) or remove it until the Editor
  surface actually consumes it — don't ship an unexercised ffmpeg shell-out.

- [cleanup] .env.example:71 — `TRANSCRIPTION_BACKEND=whisperx`, but `config.py:122` default
  is `deepgram` and CLAUDE.md/architecture name Deepgram the launch default. An operator who
  copies `.env.example` verbatim silently flips off the default GPU-free backend. | fix: set
  the example to `deepgram` (matching the real default) and leave whisperx in the inline
  comment as the alternative.

- [cleanup] ingestion/signals.py:54 — `"relative_retention": rrp or 0.0` uses truthiness
  where line 48 uses explicit `is not None`. Behaviour is identical for `float | None`
  (`0.0 or 0.0 == 0.0`), so it is a readability trap, not a defect. | fix:
  `rrp if rrp is not None else 0.0`.

- [cleanup] ingestion/transcribe.py:208 — a fresh `aai.Transcriber()` (with its own HTTP
  client/pool) is constructed per call, contrary to the module-singleton pattern used for
  Deepgram (transcribe.py:84-98) and the lock-guarded `_ASSEMBLYAI_READY` init right above
  it. Bounded churn since AssemblyAI is not the default, but inconsistent. | fix: cache a
  module-level `_ASSEMBLYAI_TRANSCRIBER` inside the existing `_ASSEMBLYAI_LOCK` block.

- [cleanup] ingestion/audio.py:30,76,147 + signals.py:21 — return types are bare `dict` /
  `list[dict]`; the real shape lives only in docstrings, and signals.py compensates with
  defensive `.get(..., [])`. | fix: define `EventInterval` / `AudioEvents` / `Timeline`
  TypedDicts at the top of each module and annotate with them.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | 2 findings — Deepgram streams from an open handle inside `with open(...)` (transcribe.py:121-127) so the WAV is never fully buffered; Deepgram client + AssemblyAI init are lock-guarded module singletons (transcribe.py:32-35, 84-98, 198-207); WhisperX model/align cached via `lru_cache` (transcribe.py:235, 243). Gaps: per-call `aai.Transcriber()` (cleanup) and the missing AssemblyAI error-status check (SEV2). Temp media cleanup is the caller's contract (`worker/storage.alocal_path`). |
| 2 Concurrency & scale | 1 finding — both heavy entry points dispatched off the loop via `asyncio.to_thread` (worker/tasks.py:937-940 `transcribe_audio` + `wait_for` timeout; :1019 `extract_audio_events`); double-checked locking verified at transcribe.py:89-98 and 198-207; CPython `lru_cache` is thread-safe (the inner `whisperx.load_model` is not serialized, but WhisperX is not the default). Remaining gap: no audio size/duration cap (SEV2) — unbounded in-memory decode in the signals thread pool. |
| 3 Security & compliance | ok — only two log lines, both safe interpolation: `logger.info("Transcribing via %s", backend)` (transcribe.py:72) and `logger.info("waveform image written to %s", output_path)` (audio.py:137) — no API key, transcript text, channel id, or PII. No SQL in the module (the two `%s` are log placeholders). Per-creator isolation is the caller boundary (pure transform over a path + pre-filtered retention rows). No virality language. **Deepgram MIP opt-out (Issue 251) correctly wired**: `addons={"mip_opt_out": True}` passed as the 3rd positional arg to `transcribe_file` (transcribe.py:116,123-127) — the documented deepgram-sdk v3 workaround, pinned by `tests/ingestion/test_transcribe.py` + `tests/test_ingest.py:193`, and matches docs/SUBPROCESSORS.md. Source-media retention enforced upstream. Config keys verified in config.py + .env.example. |
| 4 Clip-quality | partial — `is_rewatch_spike` honoured as ground-truth crowd signal even when `rrp` is below/absent (signals.py:47-48, principle 6); events sorted by `start_s` (signals.py:59). The per-file-peak normalization (audio.py:52,55) undermines silence detection (principle 5) for the downstream engine — see SEV2. This module emits raw signal only; principle citation happens in clip_engine. |
| 5 Anthropic SDK | n/a (no LLM calls) |
| 6 Cleanliness & typing | 4 cleanup — dead `generate_waveform_image`, bare-`dict` returns, `rrp or 0.0` truthiness, per-call `Transcriber()`; no TODO / commented blocks / `print()` (grep-verified); every signature typed. |
| 7 Error handling / API | ok — internal module, no HTTP surface. `ImportError`/`ValueError`/`FileNotFoundError` raise upward into the worker retry chain (transcribe.py:60-67, 95-96, 104-105, 195-196), the correct boundary. The AssemblyAI silent-empty path is the one hole (SEV2). |
| 8 Config & paths | ok — `TRANSCRIPTION_BACKEND/TIMEOUT_S/HTTP_TIMEOUT_S/MAX_MB`, `DEEPGRAM_API_KEY`, `ASSEMBLYAI_API_KEY`, `WHISPER_MODEL` all via `config.settings` with `.env.example` entries; paths accepted as `str | Path` and the worker passes absolute tmp paths. Two extensions would close gaps: an `AUDIO_MAX_*` cap, and fixing the `.env.example` default mismatch (both above). |

## Module verdict

NEEDS-WORK — no BLOCKER or SEV1. The Issue 74/76/123 hardening (lock-guarded singletons,
streaming Deepgram upload, transcription size guard, HTTP + job timeouts) and the Issue 251
MIP opt-out hold up to independent re-reading and are well-tested. Three carried-over SEV2s
remain unfixed (audio size/duration cap, peak-relative thresholds, trailing-run `end_s`
overshoot — the last de-rated toward cleanup because downstream scoring clamps it), plus the
most material one: AssemblyAI error-status is never checked, so a failed hosted
transcription can silently become an empty "successful" transcript, bypassing both the
Celery retry chain and the terminal-failure refund. New since 2026-06-09: a dead
`generate_waveform_image` shell-out with no caller, and an `.env.example` default that
contradicts the real `deepgram` default.
