# worker — assessed 2026-06-07

## Findings

- [SEV1] worker/tasks.py:2250 — Redis async client created per-task in `_generate_thumbnail_concepts_async` without connection pooling | fix: create a module-level redis.asyncio singleton (mirror progress.py's _async_client pattern) and reuse it; document that thumbnails caching is best-effort observable, never load-bearing.

- [SEV1] worker/tasks.py:2208-2213 — async `session.scalar(select(...))` fetches transcript inside `AdminSessionLocal()` but transcript_hook call (line 2211) may await implicitly if dna_profile lazy-loads | fix: explicitly load `dna_profile.top_video_ids_jsonb` via `await session.refresh(...)` or eager select before exiting the session context, verify no lazy-load on dna_brief access.

- [SEV2] worker/tasks.py:2221–2243 — loop over `top_ids[:10]` attempts UUID parsing without handling malformed IDs, and the bare `except Exception` catches UUID conversion errors silently | fix: explicitly catch `(ValueError, TypeError)` before general Exception, log malformed IDs so they can be debugged, do not suppress silently.

- [SEV2] worker/tasks.py:2302 — `parse_concepts(raw_json)` parse failure is caught but no retry gate exists — a single malformed Claude response dooms the task to terminal failure despite max_retries=3 on the task decorator | fix: verify that parse_concepts raises on malformed input; if it does, the task's retry logic (via `raise self.retry(exc=exc) from exc` in the wrapper) should still fire — check that the exception propagates cleanly without being swallowed.

- [cleanup] worker/tasks.py:354–359 — `_set_status` and `_set_clip_render_status` duplicate the pattern (get → mutate → commit) | fix: extract `_update_model_field(Model, id, field, value)` helper to DRY; both callsites then become one-liners.

- [cleanup] worker/tasks.py:2247 — unused import `_json` shadowing built-in; line 2259 uses bare `_json.loads` | fix: use standard `import json` at module level (already imported at line 2033 in `_generate_title_suggestions_async`); remove the local `import json as _json` shadow.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | 2 findings (SEV1: Redis client leak × 2; temp media cleanup OK, DB sessions OK) |
| 2 Concurrency & scale | 2 findings (SEV1: lazy-load risk; SEV2: UUID parsing silenced) |
| 3 Security & compliance | OK — per-creator isolation on every query (creator_id == cid check at 2203–2204; video check at 2065; transcript scoped to vid; DNA top_ids filtered by creator); no PII in logs. |
| 4 Clip-quality | n/a (worker is pipeline, not clip scoring) |
| 5 Anthropic SDK | OK — streaming path wired (task_id forwarded to build_concepts, line 2298); token usage logged by anthropic_stream.py integration. |
| 6 Cleanliness & typing | 2 cleanup findings (duplicated _set_status; shadowed _json import). |
| 7 Error handling / API | n/a (worker tasks, no HTTP routes) |
| 8 Config & paths | OK — Redis URL via settings.REDIS_URL; all paths absolute. |

## Module verdict

**NEEDS-WORK** — Thumbnail concepts task has a SEV1 Redis connection leak, a SEV1 lazy-load risk on dna_profile, and a SEV2 silent UUID parse failure; fix before shipping.

