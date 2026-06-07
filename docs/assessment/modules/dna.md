# dna — assessed 2026-06-07

## Findings

- [cleanup] builder.py:58 — Parameter `retention_rows: list` lacks type annotation; should be `retention_rows: list[RetentionCurve]` for consistency with other typed parameters | fix: annotate as `list[RetentionCurve]`.
- [cleanup] builder.py:87 — Parameter `activity_rows: list` lacks type annotation; should be `list[AudienceActivity]` | fix: annotate the parameter type.
- [cleanup] builder.py:115 — Nested function `_base_query` lacks return type annotation | fix: add `-> Select[tuple[Video, VideoMetrics]]` return annotation (or equivalent SQLAlchemy Select type).
- [cleanup] builder.py:298 — Nested function `_avg` parameter `vals: list` lacks element type; should be `vals: list[float | None]` | fix: annotate `vals` parameter.
- [SEV2] brief.py:156–157 — Type ignore comments suppress TypedDict violations instead of fixing root cause | fix: properly type `system` and `messages` as `list[SystemBlockParam]` and `list[MessageParam]` (or cast at construction), removing the type: ignore directives; consider using Pydantic models or TypedDict for type safety.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — all async session operations use provided session; module is stateless on resources; Anthropic singleton cached at module level with explicit timeout |
| 2 Concurrency & scale | ok — no sync/blocking calls in async functions; Voyage SDK wrapped in `asyncio.to_thread` (embeddings.py:45); bounded queries: `rank_videos` caps at `DNA_LONGS_CAP` (50) and `DNA_SHORTS_CAP` (75); `_enrich_videos` batches all lookups into 3 IN-queries regardless of video count; no N+1 |
| 3 Security & compliance | ok — all queries include explicit `creator_id` filter (builder.py:120, profile.py:53/111/169/185, identity.py:31/44/81, embeddings.py:89/125); no PII in logs; no virality promise (brief.py:31 disclaimer appended); tokens never handled (module-level separation) |
| 4 Clip-quality | n/a — dna module computes patterns and brief, not clip ranking; clipping principles cited in brief.py docstring as context but not applied here |
| 5 Anthropic SDK | ok — token usage logged on every call (brief.py:160-166); cache_control set correctly (brief.py:88, marked ephemeral); max_tokens bounded (2000); streaming path (task_id parameter) delegates to worker.anthropic_stream |
| 6 Cleanliness & typing | 1 SEV2, 4 cleanup — no print/TODO/debug; 4 nested functions lack type hints; brief.py uses type: ignore to suppress TypedDict issues; all main entry points fully typed |
| 7 Error handling / API | n/a — dna module is internal (no HTTP router); proper exception handling in profile.py (IntegrityError recovery with retry logic) and identity.py (same pattern); ValueError raised with clear messages (builder.py:278, profile.py:125) |
| 8 Config & paths | ok — all required settings present in config.py (ANTHROPIC_API_KEY, VOYAGE_API_KEY, ANTHROPIC_MODEL, MIN_VIDEOS_FOR_DNA, MIN_SHORTS_FOR_DNA, DNA_LONGS_CAP, DNA_SHORTS_CAP); no paths used in this module |

## Module verdict

NEEDS-WORK — 4 cleanup items (untyped nested function parameters) and 1 SEV2 (type: ignore misuse in brief.py should be resolved with proper TypedDict typing). The security and concurrency posture is sound; creator isolation is correctly enforced on every query. Recommend fixing typing before wider adoption of the typing test harness.

