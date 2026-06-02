# dna — assessed 2026-06-02

## Findings

- [SEV2] dna/embeddings.py — Voyage API token usage not logged, unlike Anthropic SDK calls which log `input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens`. Missing observability makes cost tracking incomplete for embeddings. | fix: Add token logging post-embed: `logger.info("dna_embeddings tokens: usage=%d", result.usage.tokens)` or equivalent after Voyage API call (if SDK provides usage).
- [SEV2] dna/brief.py — type: ignore comments on line 156–157 suppress legitimate MessageParam validation. While harmless at runtime (system/messages are correct shape), suppressing type errors masks future breakage. | fix: Import `MessageParam` and `System` types from anthropic, cast `system` to `System` and `messages` to `list[MessageParam]` instead of ignoring.
- [cleanup] dna/builder.py:204–221 `_video_summary()` duplicates field extraction logic from the video dict (v.get("field")) in a way that mirrors the dict structure exactly. With 15+ lines of get() calls, this is ripe for a TypedDict or dataclass to enforce schema at construction. | fix: Define `VideoSummary: TypedDict` with the full schema; construct it once in `rank_videos()` so no caller has to remember the field set.
- [cleanup] dna/embeddings.py:32–37 `_embed()` is a thin pass-through that adds only error handling; the `_aembed()` wrapper (which does the real lifting: asyncio.to_thread) obscures that `_embed()` is sync-only. The two-layer indirection is confusing. | fix: Collapse into a single async function or rename `_embed` → `_voyage_embed_sync` to make the sync/async boundary explicit.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | OK — AsyncSession properly managed; external Anthropic/Voyage clients are module-level singletons as required; session passed through all functions; commit/rollback patterns correct |
| 2 Concurrency & scale | OK — No blocking calls in async functions; batched IN-queries instead of N+1; all queries have creator_id WHERE clause; `FOR UPDATE` locks on identity/profile versioning to serialize concurrent writes; Voyage API calls offloaded to thread pool |
| 3 Security & compliance | OK — Parameterized SQL throughout; creator_id isolation enforced on every query (Video, CreatorDna, CreatorIdentity, Transcript, Signals, RetentionCurve, AudienceActivity all filtered by creator_id); logging sanitized (tokens only, no brief text or identity content); no PII in logs; "never virality" disclaimer appended to briefs |
| 4 Clip-quality correctness | OK — DNA patterns computed from retention curves, engagement rates, hook text, energy/laughter counts, source regions; builder explicitly ranks against creator's own engagement data (Issue 120 split longs/shorts by kind). System prompt instructs Claude to honor creator's stated identity and surface disagreement. No CLIPPING_PRINCIPLES citations in code, but system prompt emphasizes data-backed estimates and no virality promises. |
| 5 Anthropic SDK | PARTIAL — prompt caching configured with cache_control: ephemeral breakpoint in brief.py (line 88); token usage logged for both streaming (line 145) and non-streaming (line 161) paths. However: (a) type: ignore comments suppress MessageParam validation (SEV2 below); (b) brief.py doc says "cache does not actually engage for this low-frequency call" (line 8) — caching overhead may exceed benefit for periodic builds. |
| 6 Code cleanliness & typing | OK — No TODOs, commented code, print() statements, or debug artifacts. All functions fully typed with return annotations. Two functions >30 lines: `build_patterns()` (108 lines, justified: single entry point coordinating ranking → enrichment → aggregation) and `_build_request()` (39 lines, justified: assembles prompt structure once for both sync/streaming paths). Explanatory comments are concise and issue-referenced. |
| 7 Error handling | OK — Builder raises ValueError on insufficient data with diagnostic logging (Issue 88); profile/identity handle IntegrityError on concurrent writes and retry/fallback correctly; brief raises RuntimeError if Claude returns no text block. All exception paths safe. |
| 8 Config & paths | OK — All settings read from config module (DNA_LONGS_CAP, DNA_SHORTS_CAP, MIN_VIDEOS_FOR_DNA, MIN_SHORTS_FOR_DNA, ANTHROPIC_API_KEY, ANTHROPIC_MODEL, VOYAGE_API_KEY, all via settings). No hardcoded paths. Missing: VOYAGE_API_KEY is optional (warning if not set) but not documented in .env.example assumption. |

## Module verdict

clean — DNA module is production-ready with robust creator isolation, proper concurrency control, comprehensive logging, and clean code structure. Two low-impact cleanup opportunities (type ignores, logging) and one observability gap (Voyage token usage). No blockers.
