# dna — assessed 2026-05-31

## Findings

- [cleanup] dna/builder.py:58 — `retention_rows: list` lacks type parameter | fix: annotate as `retention_rows: list[RetentionCurve]` for clarity (or use `list[Any]` if the row type varies dynamically).
- [cleanup] dna/builder.py:87 — `activity_rows: list` lacks type parameter | fix: annotate as `activity_rows: list[AudienceActivity]` (or `list[Any]`).
- [cleanup] dna/builder.py:292 — `vals: list` lacks type parameter | fix: annotate as `vals: list[float | None]` (the function filters and sums floats).
- [cleanup] dna/builder.py:45 — `segments_jsonb: dict` lacks value-type annotation | fix: annotate as `segments_jsonb: dict[str, Any]` for clarity.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — AsyncSession passed as parameter, all external clients (_ANTHROPIC, _voyage) are module-level singletons with timeout/retry config; no leaks on error paths; embeddings and brief generation respect commit flags for atomic transactions |
| 2 Concurrency & scale | ok — rank_videos capped to DNA_MAX_CANDIDATE_VIDEOS; _enrich_videos batches three IN-queries regardless of video count (fixed N+1 from Issue B); no blocking calls inside async functions; voyageai SDK call offloaded to thread via asyncio.to_thread; all internal pure helpers have no I/O |
| 3 Security & compliance | ok — every query touching creator-scoped tables (CreatorDna, CreatorIdentity, DnaEmbedding, Video in rank_videos context) filters by creator_id; brief generation passes patterns dict only (no YouTube tokens or PII); no logged secrets; prompt respects CLIPPING_PRINCIPLES.md guidance (identity → stated_identity block); embeddings stored per creator_id; no cross-tenant data leaks |
| 4 Clip-quality | n/a (dna generates insights, not clips) |
| 5 Anthropic SDK | ok — prompt caching configured (Issue 69): static instructions carry cache_control breakpoint, per-creator corpus after; token usage logged after every call; brief generation has .create and .stream paths with identical prompt structure; disclaimer appended to all briefs (honesty constraint) |
| 6 Cleanliness & typing | cleanup needed — 4 functions lack type parameters on list/dict arguments (see findings); all public functions have return types; no TODO/commented-out code; no print/debug statements; logging via logger module only |
| 7 Error handling / API | n/a (module is internal, no HTTP API) |
| 8 Config & paths | ok — VOYAGE_API_KEY, ANTHROPIC_API_KEY, ANTHROPIC_MODEL read from settings (pydantic); timeouts set (60s for Anthropic, 30s for Voyage); max_retries=2 on Anthropic client; silent skip when VOYAGE_API_KEY absent (not a blocker) |

## Module verdict

**clean** — The dna module is well-structured, security-conscious, and scales safely. Creator isolation is enforced throughout. Resource lifecycle and concurrency are handled correctly. Four minor type-annotation gaps exist in internal helper functions but do not impact behavior or risk. Prompt caching is properly configured per Issue 69. All external clients are singletons with appropriate timeouts.

