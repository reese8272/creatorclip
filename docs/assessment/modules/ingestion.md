# ingestion — assessed 2026-06-07

## Findings

- [SEV2] ingestion/audio.py:41 — `librosa.load(...)` reads the entire decoded waveform
  into memory (~30 MB per hour at 16 kHz mono float32). For a 4-hour podcast across
  several concurrent workers this is the dominant per-job RSS footprint; there is no
  upstream duration/size cap that mirrors the `TRANSCRIPTION_MAX_MB` guard in
  `transcribe.py:62`. | fix: add an analogous `AUDIO_MAX_MB` (or duration probe via
  `librosa.get_duration(path=...)`) check at the top of `extract_audio_events` so a
  pathological multi-hour WAV fails fast instead of OOM-killing the worker. Document
  the cap in `.env.example`.
- [SEV2] ingestion/audio.py:46,49 — `rms / (rms.max() + 1e-8)` normalises to the
  per-clip peak, so the absolute `_ENERGY_THRESHOLD=0.6` / `_SILENCE_THRESHOLD=0.03`
  thresholds (audio.py:15–16) are not anchored to physical loudness — a uniformly
  quiet recording still produces "energy spikes" at relative 0.6 of its own peak, and
  a noisy room never produces "silences". This is load-bearing for clip-quality
  category 4 (signal quality). | fix: normalise against a global reference (e.g.
  dBFS via `librosa.amplitude_to_db`) or compute a rolling background-noise floor
  and threshold against `rms / (floor + epsilon)`. At minimum, log a warning when
  `rms.max() < <some absolute>` so we know the signal is meaningless.
- [SEV2] ingestion/signals.py:48 — Retention-spike branch uses
  `rrp or 0.0` for the emitted `relative_retention`. When `rrp` is a legitimate
  numeric `0.0` (a "below-threshold but rewatch-flagged" point), the fallback is a
  no-op, but if `rrp` is ever a falsy non-`None` value the truthiness check is
  surprising. This is a low-risk readability trap, not a behaviour bug today. |
  fix: write `relative_retention = rrp if rrp is not None else 0.0` to match the
  explicit-None pattern used immediately above on line 47.
- [SEV2] ingestion/audio.py:106 — `end_s = float(times[min(end_idx - 1, len(times) - 1)]) + frame_duration`
  can extend the reported `end_s` past `duration_s` for the trailing run, because
  the final frame is centred earlier than the audio end and we then add a full
  hop. Downstream clip math that clamps to `duration_s` will absorb this, but the
  timeline written into `signals.timeline_jsonb` is technically out-of-range. |
  fix: `end_s = min(end_s, duration_s)` before append, or compute the actual frame
  end via `librosa.frames_to_samples`.
- [cleanup] ingestion/transcribe.py:38 — `_http_timeout()` is fine, but
  `_guard_audio_size` (transcribe.py:50) and `_http_timeout` are only ever called
  from one place each; small, but the docstring repeats the rationale that already
  lives in DECISIONS / Issue 76 — risk is doc drift, not behaviour. | fix:
  shorten the in-code rationale to one line and link the Issue 76 / DECISIONS
  entry by ID.
- [cleanup] ingestion/audio.py:24,70,96 — return type is bare `dict` /
  `list[dict]`. The shape is documented in the docstring (`duration_s`,
  `energy_spikes`, etc.); a `TypedDict` would let mypy catch a missing-key bug in
  callers (`signals.py:31` already uses `.get(..., [])` defensively, which masks
  exactly that class of mistake). | fix: define `AudioEvents`, `EventInterval`
  TypedDicts at the top of `audio.py` and use them as the return annotations;
  swap `signals.py`'s `dict[str, Any]` for the same.
- [cleanup] ingestion/signals.py:21 — `build_signal_timeline` is annotated
  `-> dict` and the docstring says it returns the timeline; same TypedDict fix as
  above (`Timeline = TypedDict("Timeline", {"version": int, "duration_s": float,
  "events": list[dict]})`).

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — Deepgram upload streams from an open file handle inside a `with open(...)` block (transcribe.py:113), so the WAV is never fully buffered into a Python `bytes`; `_guard_audio_size` (transcribe.py:50) closes the only `stat()` handle implicitly. No subprocess use in this module — ffmpeg lives in `worker/`. Temp-file cleanup is the caller's responsibility (`worker/storage.py::alocal_path` does it in a `finally`). |
| 2 Concurrency & scale | ok — both heavy calls are dispatched from `worker/tasks.py` via `asyncio.to_thread` (`worker/tasks.py:593`, `:674`) so the event loop is not blocked. Issue 123 hardening verified: `_DEEPGRAM_CLIENT` (transcribe.py:32–33) and `_ASSEMBLYAI_READY` (transcribe.py:34–35) both guard the lazy first-init with a `threading.Lock` using the canonical double-checked-locking pattern (transcribe.py:90–98, 191–199). `_whisperx_model` and `_whisperx_align_model` (transcribe.py:227, 235) rely on `functools.lru_cache`, which is documented thread-safe in CPython (cache hits + insertions are guarded by the GIL + a per-cache lock) — this is correct. SEV2 audio-size cap recommended above. |
| 3 Security & compliance | ok — no API key, transcript text, audio path content, or PII written to any `logger.*` call (the only log lines are `logger.info("Transcribing via %s", backend)` at transcribe.py:72). Source-media retention is enforced by the caller (`worker/storage.alocal_path` deletes the local tmp; `videos.ingest_done_at` drives the 72h R2 purge per `docs/COMPLIANCE.md`). Per-creator isolation is not relevant — the module is pure transformation over a `Path` and a `RetentionCurve` row list, both of which the caller already filtered. No virality language. |
| 4 Clip-quality | partial — silence + spike timeline construction is straightforward (signals.py:31–57), `is_rewatch_spike` is honoured as ground-truth crowd signal (Issue 127), and events are sorted by `start_s` before write. The per-clip-peak normalisation in `audio.py:46,49` is the main correctness concern — see SEV2 above. No score citation needed; this module emits the raw signal that `clip_engine/` cites against `CLIPPING_PRINCIPLES.md`. |
| 5 Anthropic SDK | n/a — no LLM calls. |
| 6 Cleanliness & typing | 3 cleanup — bare `dict` returns where TypedDicts would catch caller errors; no TODO / commented blocks / `print()`; every function signature typed. `_http_timeout` and `_guard_audio_size` docstrings repeat DECISIONS rationale (drift risk). |
| 7 Error handling / API | n/a — internal module, no HTTP surface. Backend failures (ImportError, ValueError on missing keys, oversize audio) raise upward for the worker retry chain to catch, which is the correct boundary. |
| 8 Config & paths | ok — `TRANSCRIPTION_BACKEND`, `TRANSCRIPTION_HTTP_TIMEOUT_S`, `TRANSCRIPTION_MAX_MB`, `DEEPGRAM_API_KEY`, `ASSEMBLYAI_API_KEY`, `WHISPER_MODEL` all routed through `config.settings`; `audio_path: str | Path` accepts whatever the caller hands in (worker passes absolute paths via `alocal_path`). The recommended `AUDIO_MAX_MB` (SEV2 above) would extend this. |

## Module verdict

NEEDS-WORK — no BLOCKER or SEV1. The concurrency story is correct (Issue 123
threadlock + Issue 74 singletons verified by reading); the resource-lifecycle and
security baselines hold. Three SEV2s worth fixing before scale: (1) audio-size /
duration guard symmetric with `TRANSCRIPTION_MAX_MB`, (2) audio thresholds
anchored to an absolute reference rather than per-clip peak (load-bearing for
signal quality), and (3) the small `rrp or 0.0` falsy-vs-None readability fix.
The cleanup items (TypedDicts) are nice-to-have.
