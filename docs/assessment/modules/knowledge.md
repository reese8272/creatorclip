# knowledge — assessed 2026-06-07

## Findings

- [cleanup] knowledge/thumbnails.py:176 + knowledge/titles.py:104 — `_build_concepts_request` and `_build_request` return bare `tuple` without type annotation | fix: annotate as `-> tuple[list[dict], list[dict], list[dict]]` (system, tools, messages).
- [cleanup] knowledge/thumbnails.py:88 — `_empty_patterns()` return type bare `dict` lacking structure definition | fix: define a TypedDict `ChannelThumbnailPatterns` with keys (face_present, dominant_emotions, text_overlay_style, typical_colors, composition_pattern, channel_thumbnail_signature) and annotate return as `-> ChannelThumbnailPatterns`.
- [cleanup] knowledge/thumbnails.py:161 + knowledge/titles.py:89 — duplicated `_extract_transcript_*` functions with identical logic (DRY violation) | fix: extract to shared `_extract_transcript(segments_jsonb: dict | None, max_chars: int = 1500) -> str` in `knowledge/__init__.py`, import and call from both modules with different max_chars defaults.
- [cleanup] knowledge/thumbnails.py:102 — `analyze_thumbnail_patterns` return type bare `dict` (docstring refers to non-existent `ChannelThumbnailPatterns`) | fix: return `ChannelThumbnailPatterns` after defining the TypedDict.

## Rubric coverage

| Category | Status |
|---|---|
| 1 Resource lifecycle | ok — module-level `_ANTHROPIC` singleton, no per-call constructions; `.with_options()` returns a configured view, not a new client. |
| 2 Concurrency & scale | ok — functions designed for `asyncio.to_thread` (sync-friendly); no hidden blocking calls. Exception handling delegated to task wrapper. |
| 3 Security & compliance | ok — API key loaded via settings (never hardcoded); no PII/secrets in logger calls; no credential leaks in f-strings. |
| 4 Clip-quality | n/a (knowledge module is advisory, not a core clipping function) |
| 5 Anthropic SDK | ok — prompt caching used correctly (Block 2 DNA brief + cache_control breakpoint); token usage logged post-call; web_search tool passed via `settings.ANTHROPIC_WEB_SEARCH_TOOL`; `type: ignore[typeddict-item]` on line 144 is justified (SDK/stub lag on Anthropic 0.40 TypedDict content shape). |
| 6 Cleanliness & typing | 4 findings — missing TypedDict defs, bare `tuple` return types, duplicated extraction logic (see Findings). No TODO, commented code, or debug prints. |
| 7 Error handling / API | ok — parse functions raise ValueError on malformed JSON; task-level exception handler catches and emits error events. |
| 8 Config & paths | ok — all settings loaded via pydantic Settings; no hardcoded paths. |

## Module verdict

NEEDS-WORK — Type annotations incomplete (bare dict/tuple without structure definition); duplicated transcript extraction logic violates DRY and increases maintenance burden.
