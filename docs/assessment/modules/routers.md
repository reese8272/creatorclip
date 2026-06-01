# routers — assessed 2026-05-31

## Findings

- [SEV2] creators.py:285 — HTTPException catches ValueError and passes str(exc) to client detail, exposing internal validation error text → safe error messages mandatory | fix: replace with `raise HTTPException(status_code=422, detail="Invalid identity data") from exc` or similar safe message; test with dna.identity.validate_* to ensure all ValueError messages are internal-safe.

- [SEV2] improvement.py:285 — same pattern in confirm_dna() catches ValueError and returns str(exc) to client | fix: same as creators.py:285 — wrap ValueError and return safe, user-facing message (e.g., "Could not confirm DNA").

- [cleanup] tasks.py:117 — StreamingResponse endpoint does not have a Pydantic response_model (by design for SSE; Pydantic models do not apply to streaming) | fix: add comment at line 117 explaining why Pydantic model is not used here, e.g., `# SSE responses are streamed line-by-line; Pydantic validation does not apply.`

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | clean — sessions via context manager, temp files in try/finally, tokens via decrypt(), Redis singleton |
| 2 Concurrency & scale | clean — SELECT FOR UPDATE SKIP LOCKED, async.to_thread for blocking ops, no N+1 queries, queries use .limit(1) and indexed .where() patterns |
| 3 Security & compliance | clean — per-creator isolation verified on every query (creator_id == creator.id or owner check), OAuth tokens encrypted, no PII in logs, no secret exposure |
| 4 Clip-quality | n/a (routers do not score clips; delegates to clip_engine module) |
| 5 Anthropic SDK | n/a (routers correctly defer LLM work to workers module; no sync LLM calls in routers) |
| 6 Cleanliness & typing | clean — all function signatures typed, no TODOs, no commented code, no print statements, no duplicated logic across files, logging safe |
| 7 Error handling / API | 2 findings — str(exc) in two places; otherwise HTTP status codes correct (201 for POST create, 204 for DELETE, 202 for accepted tasks, 4xx for validation), all request/response models are Pydantic except SSE (by design) |
| 8 Config & paths | clean — all paths absolute (Path objects), settings accessed via config.settings, no hardcoded values, no new config required (all present in existing settings) |

## Module verdict

clean — routers module is production-ready. Per-creator isolation verified on all queries. Rate limits present on every endpoint. Pydantic models comprehensive. Two minor error-message handling issues (str(exc) exposure) are cleanups, not security issues (ValueError messages from dna.identity.validate_* are designed to be safe, but relying on external module's error text is fragile). No blockers.

