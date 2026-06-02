# clip_engine — assessed 2026-06-02

## Findings

- **BLOCKER** ranking.py:102 — Missing `creator_id` filter on clip idempotency query allows querying clips from other creators | fix: add `AND Clip.creator_id == creator_id` to the WHERE clause: `select(Clip).where((Clip.video_id == video_id) & (Clip.creator_id == creator_id)).order_by(Clip.rank)`
- **SEV1** scoring.py:207 — Type-ignore comment on SDK typing issue (Issue 78c) suggests unresolved SDK integration debt; the SDK may be updated without notice | fix: file a follow-up to revisit SDK typing once `cache_control` is formally in the SDK stubs, or add explicit type definition to avoid runtime surprises
- **cleanup** ranking.py:51-62 — Inline feature extraction in `_features()` lambda duplicates column-access logic from scoring.compute_features; refactor to shared helper | fix: extract to `clip_engine.scoring.extract_features_from_db(clip: Clip) -> dict` and reuse
- **cleanup** candidates.py:31-40 — Silences event filtering logic appears twice (silence-priority setup detection); refactor to helper | fix: extract silence filtering to `_silences_in_window(timeline, start_s, end_s) -> list[dict]`
- **cleanup** render.py:131-217 — render_clip_file() combines ffmpeg wrapper + face detection + style filtering (90 lines); multiple concerns | fix: split face-detection responsibility into own async-safe function, defer style/subtitle logic to a separate module (style_engine.py)

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | BLOCKER: missing creator_id isolation on DB query (line 102); tempfile cleanup ✓ |
| 2 Concurrency & scale | ok — asyncio.to_thread() offloads CPU work (candidates, features); no unbounded loops; queries indexed on video_id |
| 3 Security & compliance | BLOCKER: missing creator_id WHERE clause on idempotency check; token usage logged correctly (not tokens themselves); no PII logged |
| 4 Clip-quality correctness | ok — every score cites a named principle (principle field present); setup anchored backward (candidates.py core); DNA ranking via Claude with recency decay fallback honest |
| 5 Anthropic SDK | ok — prompt caching used (Issue 78b), cache_control with 1h TTL; token usage logged; max_tokens=1200 set; type-ignore debt flagged |
| 6 Cleanliness & typing | ok — all functions typed; no print/TODO/commented blocks; 8 functions >30 lines (largest 113); feature extraction duplicated |
| 7 Error handling | ok (non-router) — face detection graceful fallback; JSON decode fallback to signal scores; preference model failure honest fallback |
| 8 Config & paths | ok — paths absolute (Path objects); timeouts configurable (TRANSCRIPTION_TIMEOUT_S, etc.); no hardcoded secrets |

## Module verdict

**has BLOCKER** — The missing `creator_id` filter on line 102 breaks per-creator isolation and must be fixed before production.

