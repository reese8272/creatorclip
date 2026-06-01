# ingestion — assessed 2026-05-31 (Wave 9 re-assessment, Issues 103 + 108)

Slice: `ingestion/__init__.py` (empty, 0 lines), `ingestion/audio.py` (113
lines), `ingestion/signals.py` (58 lines), `ingestion/transcribe.py` (257
lines). Worker call sites (`worker/tasks.py:462, 500-516, 554-598`) were
re-traced only to confirm the async/blocking, timeout, and temp-cleanup
boundaries — those fixes live in another slice but are intrinsic to how this
slice runs.

**Wave 9 touched ingestion in two commits at HEAD `d6a7393`:**
- `7bd1cfe` (Issue 103) — closed both prior carry-forward SEV2s
  (Deepgram normalizer hard-key indexing → `.get()` + skip; `_guard_audio_size`
  OSError → `raise FileNotFoundError`).
- `d6a7393` (Issue 108) — closed five of the six prior cleanups by adding
  return/param typing: `_deepgram_client() -> Any`,
  `_normalize_assemblyai(transcript: Any)`, `_whisperx_model -> Any`,
  `_whisperx_align_model -> Any`, `transcribe_audio -> dict[str, Any]`, and
  widened `build_signal_timeline` to `dict[str, Any]` + `Sequence[Any]` with
  an inline Protocol-as-doc-comment (chosen over `Protocol` because SQLAlchemy
  `Mapped[T]` descriptors don't satisfy a structural Protocol under mypy).

All six prior findings are now closed. One residual cleanup remains (loose
`dict` return on the three private normalizers + `build_signal_timeline`),
and the assessment surfaces one net-new SEV2 introduced by the Issue 103
guard-clause change.

## Findings

- [SEV2] ingestion/transcribe.py:54-55 — `_guard_audio_size` now raises
  `FileNotFoundError` from `OSError`, but the docstring (lines 47-51) still
  promises "a missing/unreadable file is left for the backend to surface, so
  callers that pass a fake path aren't affected." The behavior diverged from
  the contract: tests or callers that previously relied on a fake-path
  passthrough (e.g. unit-test fixtures hitting `_transcribe_*` directly) now
  fail at the guard. The commit message for `7bd1cfe` notes "Three routing
  tests updated to use real `tmp_path` files," confirming the behavior change
  was breaking. The fix is correct — the docstring is now misleading. | fix:
  rewrite the third paragraph of the `_guard_audio_size` docstring (lines
  47-51) to state: "A missing or unreadable file raises `FileNotFoundError` so
  the caller's retry/refund pathway sees a clear terminal error instead of
  burning the per-job budget on a silent empty-pipeline run (Issue 103)."

- [cleanup] ingestion/transcribe.py:90, 112, 171, 191, 229, 244 — three
  private normalizers (`_normalize_deepgram`, `_transcribe_assemblyai`,
  `_normalize_assemblyai`, `_transcribe_whisperx`, `_normalize_whisperx`) and
  the public `_transcribe_deepgram` still return bare `dict` rather than
  `dict[str, Any]`. `transcribe_audio` (line 64) was widened in Issue 108,
  but the helpers it delegates to weren't, so mypy can't track the same shape
  end-to-end. Cosmetic — no behavior risk. | fix: change all six return
  annotations from `-> dict:` to `-> dict[str, Any]:` to match
  `transcribe_audio`. Or, the higher-leverage version: introduce a
  `TypedDict` in `ingestion/types.py`
  (`TranscriptionResult`/`TranscriptSegment`/`TranscriptWord`) and use that
  across all six returns + `transcribe_audio` — the docstring at
  transcribe.py:1-13 already specifies the schema verbatim.

- [cleanup] ingestion/signals.py:21-24 — `build_signal_timeline(audio_events:
  dict[str, Any], retention_points: Sequence[Any]) -> dict:` returns bare
  `dict`. The function returns `{"version": 1, "duration_s": float, "events":
  list[dict]}` per the body (lines 54-58). | fix: annotate
  `-> dict[str, Any]` (or a `TypedDict` `SignalTimeline` co-located with the
  `TranscriptionResult` types above).

- [cleanup] ingestion/audio.py:24, 76, 96 — `extract_audio_events(audio_path:
  str | Path) -> dict:`, `_merge_runs(...) -> list[dict]:`, and `_emit(...,
  events: list[dict], ...)` all use bare `dict` / `list[dict]`. KISS-fine
  internally, but inconsistent with the typing sweep that just landed in
  signals.py and transcribe.py. | fix: annotate `extract_audio_events
  -> dict[str, Any]`; tighten `_merge_runs -> list[dict[str, float]]` and
  `events: list[dict[str, float]]` (the only values stored are floats —
  start_s, end_s, optional value).

## Fixed since prior assessment (re-verified at HEAD `d6a7393`)

- **Deepgram normalizer hard-key indexing (was SEV2):** Both branches of
  `_normalize_deepgram` now use `.get("start")` / `.get("end")` /
  `.get("transcript", "")` and skip any utterance/word missing either
  timestamp (transcribe.py:113-164). A partial Deepgram response no longer
  KeyErrors the entire job. Issue 103 / commit `7bd1cfe`. CLOSED.

- **`_guard_audio_size` silently swallows OSError (was SEV2):** The
  `except OSError as exc` block now raises
  `FileNotFoundError(f"audio not found: {audio_path}") from exc`
  (transcribe.py:54-55). Missing audio is surfaced as a terminal error so the
  Celery retry/refund pathway sees a clear failure rather than burning the
  budget on a silent empty pipeline run. Issue 103 / commit `7bd1cfe`.
  CLOSED. (See SEV2 above re: stale docstring promising the old behavior.)

- **`_deepgram_client()` untyped return (was cleanup):** Now annotated
  `-> Any` (transcribe.py:78). Issue 108 / commit `d6a7393`. CLOSED.

- **`_normalize_assemblyai(transcript)` untyped param (was cleanup):** Now
  `_normalize_assemblyai(transcript: Any) -> dict:` (transcribe.py:191).
  Issue 108 / commit `d6a7393`. CLOSED.

- **`_whisperx_model` / `_whisperx_align_model` untyped returns (was
  cleanup):** Both `lru_cache`-decorated loaders now return `Any`
  (transcribe.py:215, 223). Issue 108 / commit `d6a7393`. CLOSED.

- **`build_signal_timeline` loose `list`/`dict` params (was cleanup):**
  Widened to `dict[str, Any]` + `Sequence[Any]` (signals.py:21-24) with an
  inline 5-line comment documenting the duck-typed `RetentionCurve`-shaped
  row contract (`timestamp_s: float`, `audience_watch_ratio: float`,
  `relative_retention_performance: float | None`). The DECISIONS-style
  comment explains why `Protocol` was rejected (SQLAlchemy `Mapped[T]`
  descriptors don't satisfy structural Protocol under mypy invariance).
  Issue 108 / commit `d6a7393`. CLOSED.

- **`transcribe_audio` returning bare `dict` (was cleanup):** Now
  `transcribe_audio(audio_path: str | Path) -> dict[str, Any]:`
  (transcribe.py:64). Issue 108 / commit `d6a7393`. PARTIALLY CLOSED — the
  helper functions it delegates to (`_transcribe_*`, `_normalize_*`) still
  return bare `dict`; see open cleanup above.

## Fixed in prior waves (re-verified at HEAD `d6a7393`, STILL GREEN)

- **Deepgram whole-file buffering (was SEV1):** `_transcribe_deepgram` opens
  the WAV and streams the file handle via httpx
  (transcribe.py:102-108). Issue 76 / commit `b8a8735`. STILL RESOLVED.
- **No SDK-native transcription timeout (was SEV1):** `_http_timeout()`
  constructs `httpx.Timeout(TRANSCRIPTION_HTTP_TIMEOUT_S, connect=10.0)` and
  is passed to Deepgram's `transcribe_file(..., timeout=...)`
  (transcribe.py:106). AssemblyAI uses
  `aai.settings.http_timeout = float(TRANSCRIPTION_HTTP_TIMEOUT_S)` at module
  init (transcribe.py:185). Issue 76 / commit `b8a8735`. STILL RESOLVED.
- **Size guard:** `_guard_audio_size` rejects audio over
  `TRANSCRIPTION_MAX_MB` (default 1024 MB) before any read/upload
  (transcribe.py:56). STILL IN PLACE.
- **AssemblyAI normalizer hardened:** reads `w.text/w.start/w.end` off SDK
  objects, not dict keys (transcribe.py:191-208). STILL RESOLVED.
- **Librosa OOM bound:** `librosa.load(..., sr=16000, mono=True)`
  (audio.py:41) caps memory ~3× vs the native-rate decode. Issue 74. STILL
  RESOLVED.

## Notes verified clean (no finding)

- **Resource lifecycle:** the one file handle in slice (transcribe.py:102)
  is inside a `with`. SDK clients are module-level singletons
  (`_DEEPGRAM_CLIENT` guarded by `_deepgram_client()`, `_ASSEMBLYAI_READY`
  flag, `@functools.lru_cache(maxsize=2/4)` on WhisperX load/align models).
  Temp WAV cleanup is in the caller's `alocal_path` context manager
  (`worker/tasks.py:500, 591` — out of slice, correct).

- **Concurrency & scale:** no blocking call sits inside an `async def` in
  this slice (the slice exposes only sync functions; `worker/tasks.py:514,
  595` offloads them via `asyncio.to_thread` with `asyncio.wait_for`
  bounding). librosa is bounded at `sr=16000` (audio.py:41). Deepgram
  upload is streamed; `_merge_runs` is O(n) over frame count with no
  unbounded accumulation. WhisperX `lru_cache(maxsize=2/4)` puts a hard
  ceiling on warm-model memory per worker process. The new `Sequence[Any]`
  signature in `build_signal_timeline` does not change runtime cost — the
  caller passes `list(retention_result.scalars())` (worker/tasks.py:589),
  which is bounded by the per-video `RetentionCurve` row count (typically <
  1k points).

- **Security & compliance:** no OAuth tokens, no PII, no SQL in slice. Sole
  `logger.info` call (transcribe.py:66) logs only the backend name — API
  keys are read from `settings`, never logged. No virality language in any
  docstring/log/error string. Per `docs/COMPLIANCE.md`, transcripts are
  derived data (not YouTube-origin) and retained until video deletion —
  slice produces but does not store, so retention is the caller's
  responsibility. The `yt-dlp` guard called out in the Pre-Public-Launch
  Compliance Gates is not a concern for this slice — this module accepts an
  already-downloaded audio path and never reaches out to YouTube.

- **Anthropic SDK:** module makes no LLM calls. n/a.

- **Config & paths:** required-key guards fail fast with `ValueError`
  (transcribe.py:85, 177). All four transcription keys
  (`TRANSCRIPTION_BACKEND` / `_HTTP_TIMEOUT_S` / `_MAX_MB` / `WHISPER_MODEL`)
  present in `.env.example` with descriptions (verified at
  `.env.example:47, 49, 50, 52`). Paths typed `str | Path`; callers pass
  absolute temp paths from `alocal_path`.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — singletons + lru_cache; file handle context-managed; caller-owned temp cleanup |
| 2 Concurrency & scale | ok — Deepgram streams; httpx timeout set; librosa sr=16000; sync-only API offloaded by caller |
| 3 Security & compliance | ok — no tokens/PII/SQL in slice; backend-name-only logging; no virality strings |
| 4 Clip-quality | n/a — ingestion produces signals, does not score/anchor clips |
| 5 Anthropic SDK | n/a — module makes no LLM calls |
| 6 Cleanliness & typing | 3 cleanup (bare `dict` on 6 private/public transcribe normalizers + `_transcribe_*`; bare `dict` on `build_signal_timeline` return; bare `dict`/`list[dict]` across `extract_audio_events` + `_merge_runs` + `_emit`) |
| 7 Error handling / API | 1 SEV2 (stale `_guard_audio_size` docstring contradicts the Issue 103 behavior change) |
| 8 Config & paths | ok — keys + descriptions in `.env.example`; fail-fast guards |

## Module verdict
NEEDS-WORK — Wave 9 closed both prior carry-forward SEV2s (Deepgram
normalizer hard-key indexing; `_guard_audio_size` swallowing OSError) and
five of the six prior cleanups (typing sweep across `_deepgram_client`,
`_normalize_assemblyai`, both WhisperX loaders, `transcribe_audio`, and
`build_signal_timeline`). One net-new SEV2 surfaced: the `_guard_audio_size`
docstring still promises the old passthrough behavior even though the body
now raises `FileNotFoundError`. Three cleanup-severity typing gaps remain
where the bare `dict` / `list[dict]` returns weren't widened (the six
private/public transcribe helpers, `build_signal_timeline` return, and the
audio.py helpers). No BLOCKER, no SEV1, no security or cross-tenant defect.
