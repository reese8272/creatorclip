# ingestion — assessed 2026-07-20 (post-fix)

**Post-fix status:** clean, unchanged — `git diff ca3305c..e92b93a -- ingestion/` is empty;
verdict and all carry-forward cleanups stand as written below.

Slice: `ingestion/__init__.py` (empty), `ingestion/audio.py`, `ingestion/signals.py`,
`ingestion/transcribe.py`. Pure transformation layer: a media/audio path goes in, a dict comes
out. No DB queries, no creator-scoped tables, no OAuth/token handling, no Anthropic calls in
this slice — the isolation/token/LLM rubric rows are `n/a` and enforced at the worker caller
boundary (re-verified: heavy entry points offloaded via `asyncio.to_thread` at
`worker/tasks.py:1463` and the transcribe path, bounded by `TRANSCRIPTION_TIMEOUT_S`; source-media
purge lives in `worker/tasks.py:756` `purge_stale_source_media`, outside this slice). Ingestion
itself creates no temp files — `generate_waveform_image` writes only to the caller-supplied
`output_path`; Deepgram streams the WAV via `with open()`.

`git diff f70a857..HEAD -- ingestion/` touched `audio.py` and `transcribe.py` (Issue 352 Batch E)
— both diffs reviewed line-by-line and verified against real ffmpeg / the regression tests below.

## Resolved since 2026-07-01

- **[was SEV2] transcribe.py — AssemblyAI `status=error` → silent empty-segments "success"
  (charged, no refund): FIXED.** `transcribe.py:257-258` now raises
  `RuntimeError(f"AssemblyAI transcription failed: {transcript.error}")` when
  `transcript.status == aai.TranscriptStatus.error`, so the worker retry → terminal-failure →
  refund chain engages (the COMPLIANCE.md "automatic refund on terminal ingest failure"
  guarantee now fires for this path). Regression test verified at
  `tests/ingestion/test_transcribe.py:179-193` (error-status stub → raises); the happy-path stub
  in `tests/test_ingest.py:225-228` was updated to `status="completed"` to pass the new gate.
- **[was SEV2] audio.py — `showwavespic:bg_color` invalid ffmpeg option (100% broken helper):
  FIXED.** `audio.py:202-206` composites the transparent-background waveform over a `color`
  source via `overlay` — the correct pattern, since `showwavespic` has no background option.
  Verified 2026-07-20 by running the exact new filter string against local ffmpeg: exit 0, PNG
  produced, and byte-size delta vs a bg-only render (1894 B vs 1234 B) confirms the waveform is
  actually drawn. The mock-masked-test gap is closed: `tests/test_signals.py:323-336` runs REAL
  ffmpeg, and `:303-320` asserts the built filter contains no `bg_color` token. (The helper is
  still unwired — see carry-forward cleanup below.)
- **[was SEV2] audio.py — peak-relative silence threshold undermining the dead-air principle:
  FIXED.** Silence is now gated absolutely at `_SILENCE_DBFS = -60.0` dBFS
  (`audio.py:27,109,116`) via `librosa.amplitude_to_db(rms, ref=1.0, top_db=None)`, matching the
  ffmpeg silencedetect default noise floor; an all-silent file logs a warning
  (`audio.py:117-123`) and energy/laughter frames below the floor are masked out
  (`:127,130-132`), killing the near-silent-file false-spike vector. Regression tests at
  `tests/test_signals.py:263-297` (near-silent file → one silence run, zero energy spikes;
  audible content not misflagged). Energy spikes/laughter deliberately remain relative to the
  creator's own baseline — a documented design choice in the code comment, acceptable.

## Findings

- [cleanup] ingestion/audio.py:143-231 — `generate_waveform_image` is now *correct* but still
  has **no production caller** (carry-forward, downgraded from the SEV2 wrapper: grep shows only
  its own def, the config comment, and tests reference it). Dead code since Issue 188. | fix:
  wire it into the signals stage (or the Editor asset pipeline) or delete it until a surface
  consumes it — the real-ffmpeg test now protects it either way.
- [cleanup] ingestion/audio.py:270 + ingestion/signals.py:53 — (carry-forward) in `_emit`,
  `end_s = float(times[min(end_idx-1, len-1)]) + frame_duration` can push the final run's
  `end_s` past `duration_s`, and `_event_geometry_is_valid` still validates only
  `start_s > duration_s`, not `end_s`, so the slightly-over event lands in
  `signals.timeline_jsonb`. Cosmetic (downstream clamps the grid index). | fix: pass
  `duration_s` into `_emit` and clamp, or extend the validator to cap `end`.
- [cleanup] .env.example:91 — (carry-forward) `TRANSCRIPTION_BACKEND=whisperx` still contradicts
  `config.py:200` (default `deepgram`) and CLAUDE.md's GPU-free launch default; an operator
  copying `.env.example` verbatim silently flips the backend. | fix: set the example to
  `deepgram`; keep `whisperx` in the inline comment.
- [cleanup] ingestion/audio.py:35-40 — (carry-forward) `_EMPTY_EVENTS` module constant still
  defined but never referenced; the <2-sample guard (`:94-99`) builds its own dict literal.
  Dead code. | fix: use it in the guard (overriding `duration_s`) or delete it.
- [cleanup] ingestion/transcribe.py:250 — (carry-forward) a fresh `aai.Transcriber()` (own HTTP
  client/pool) is constructed per call, contrary to the module-singleton pattern used for
  Deepgram (`_deepgram_client`) and the lock-guarded `_ASSEMBLYAI_READY` init right above it.
  Bounded churn (non-default backend). | fix: cache a module-level `Transcriber` singleton.
- [cleanup] ingestion/signals.py:90 — (carry-forward) `"relative_retention": rrp or 0.0` uses
  truthiness where line 84 uses explicit `rrp is not None`; identical behaviour for
  `float | None` but a readability trap. | fix: `rrp if rrp is not None else 0.0`.

Observation (not a finding, carried): `transcribe.py:104` dumps the full joined transcript via
`vlog` — a hard no-op unless `verbose_logging_enabled` (prod requires the explicit
`VERBOSE_LOGGING_ALLOW_PROD=true` opt-in), so gated and low-risk, but it emits creator-authored
spoken content to the verbose log when enabled.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — Deepgram streams via `with open()`; `subprocess.run` timeout-bounded; no temp files created in-slice (1 cleanup: per-call Transcriber) |
| 2 Concurrency & scale | ok — sync functions offloaded via `asyncio.to_thread` at the worker boundary; audio-OOM cap + 16 kHz resample; size guard `TRANSCRIPTION_MAX_MB` before upload |
| 3 Security & compliance | ok — no tokens/PII in log lines; Deepgram `mip_opt_out` enforced (Issue 251); AssemblyAI failure now triggers the refund guarantee; no creator-scoped queries in slice |
| 4 Clip-quality | ok — absolute −60 dBFS silence gate restores the dead-air principle (was 1 finding, now resolved) |
| 5 Anthropic SDK | n/a (no LLM calls in this module) |
| 6 Cleanliness & typing | 4 cleanups (dead `_EMPTY_EVENTS`, dead-but-working waveform helper, per-call Transcriber, `rrp or 0.0`) |
| 7 Error handling / API | n/a (not a router) |
| 8 Config & paths | 1 cleanup — `.env.example` backend default mismatch; all new config (`WAVEFORM_TIMEOUT_S`, `AUDIO_ANALYSIS_MAX_DURATION_S`, timeouts) present in `.env.example` with descriptions |

## Module verdict
clean — all three 2026-07-01 SEV2s (AssemblyAI silent-failure/no-refund, broken showwavespic
filter, peak-relative silence gate) verified fixed with real regression tests; only
carry-forward cleanups remain, the largest being the still-unwired waveform helper and the
`.env.example` backend-default mismatch.
