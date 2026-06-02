# ingestion — assessed 2026-06-02

## Findings

- SEV1: `ingestion/transcribe.py:78-87` — Race condition on `_DEEPGRAM_CLIENT` module-level singleton. Multiple threads (via `asyncio.to_thread()` in worker/tasks.py) can simultaneously check if the client is None and both proceed to initialize. DeepgramClient may not be thread-safe during construction. Fix: use `threading.Lock()` to guard initialization: `with _DEEPGRAM_LOCK: if _DEEPGRAM_CLIENT is None: ...`

- SEV1: `ingestion/transcribe.py:179-186` — Race condition on `_ASSEMBLYAI_READY` module-level flag. Same concurrent thread scenario via `asyncio.to_thread()` allows both threads to check the flag as False and both execute the initialization block (setting api_key and timeout). The `aai.settings` assignment is not atomic. Fix: use `threading.Lock()` to guard the entire init block.

- CLEANUP: `ingestion/transcribe.py:64` — Return type annotation should be more specific: `dict[str, Any]` is correct but could document the schema (source, segments). Consider: `-> dict[str, str | list[dict[str, Any]]]` or add a TypedDict. Current form is acceptable.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | SEV1 — Module-level client/flag singletons lack synchronization for concurrent thread access via asyncio.to_thread() |
| 2 Concurrency & scale | SEV1 — Deepgram and AssemblyAI initialization not guarded; httpx timeouts are configured correctly but initialization races remain |
| 3 Security & compliance | CLEAN — API keys not logged; transcripts are derived; no PII exposure |
| 4 Clip-quality correctness | n/a — module is signal extraction only |
| 5 Anthropic SDK usage | n/a — no LLM calls |
| 6 Code cleanliness & typing | CLEAN — no TODO/commented code; type hints complete; normalizers well-factored |
| 7 Error handling & API surface | CLEAN — appropriate exceptions raised; sync functions straightforward |
| 8 Config & paths | CLEAN — all config via settings; file sizing guard; paths absolute |

## Module verdict

NEEDS-WORK — Two SEV1 race conditions in module-level singleton initialization under asyncio.to_thread concurrency. Requires lock-guarded init blocks to prevent double-construction of SDK clients.
