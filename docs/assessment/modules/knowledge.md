# knowledge — assessed 2026-07-01

Slice: `knowledge/{chapters,clip_captions,clip_explain,clip_titles,hooks,thumbnails,titles,util}.py`, `knowledge/__init__.py`.

All Anthropic-SDK claims below are verified against **live** Anthropic docs (fetched 2026-07-01),
not memory or DECISIONS.md (which the user flagged as having flip-flopped). Sources:
- Prompt caching floors & pricing multipliers: https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching (fetched 2026-07-01)
- Structured outputs shape + citation incompatibility: https://platform.claude.com/docs/en/docs/build-with-claude/structured-outputs (fetched 2026-07-01)
- Web search tool version / dynamic filtering / model support: https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool (fetched 2026-07-01)

Verified facts used below (verbatim from the live docs):
- **Claude Sonnet 4.6 minimum cacheable prefix = 1,024 tokens; Claude Haiku 4.5 = 4,096 tokens.**
  (So DECISIONS.md's 1024/4096 values are CORRECT; a synthesized web-search summary claiming
  Sonnet 4.6 = 2048 was wrong — the authoritative doc table says 1,024.)
- Cache-write multiplier: 5-min = **1.25×** base input; **1-hour = 2×** base input; cache read = **0.1×**. `ttl: "1h"` on `cache_control: {type: ephemeral}` is valid.
- Structured outputs: `output_config.format = {type:"json_schema", schema:{…}}`; objects **must** set `additionalProperties: false`; **returns 400 if citations are enabled** (i.e. cannot be combined with the web_search tool). Sonnet 4.6 + Haiku 4.5 both support structured outputs.
- Web search: latest version is `web_search_20260318`; `web_search_20260209` (used here) remains available; dynamic filtering requires the code-execution tool and is limited to Opus 4.7/4.6, Sonnet 4.6 etc. — **Haiku 4.5 is not in the dynamic-filtering list**, corroborating hooks.py's rationale for `allowed_callers=["direct"]`.
- Vision: image inputs are billed as input tokens and surface in `usage.input_tokens`.

## Findings

- [SEV2] titles.py:136-140, thumbnails.py:231-235, clip_titles.py:150-154, clip_captions.py:120-124, clip_explain.py:149-153 — the `cache_control {ttl:"1h"}` marker on Block 2 (DNA brief) is emitted **unconditionally**, but the cacheable prefix = Block1(static) + Block2(DNA). Measured static blocks incl. UNTRUSTED_CONTENT_POLICY are only ~471–709 tokens (chars/4: titles 709, thumbnails 621, clip_titles 596, clip_explain 544, clip_captions 471). When `dna_brief is None` the DNA text is "No DNA profile available yet." (~10 tok), so the prefix is **~480–720 tokens — well below Sonnet 4.6's 1,024-token floor** and the marker is INERT (Anthropic silently declines to cache). The inline comments assert "~1,550 tokens … clears the 1024 floor," which only holds when the DNA brief is near its full 3,000-char cap; for every new creator (no DNA yet) caching does nothing. This is the exact defect DECISIONS #315 fixed in `clip_engine/scoring.py` by gating the marker on measured prefix size; the knowledge builders never adopted the guard. No over-bill (usage is read post-hoc from the response, so `cache_creation=0` is recorded honestly), but zero cache benefit + misleading comment. Secondary cost note: the 1h TTL write is **2× base**; for the effectively one-shot-per-video Sonnet calls, if a same-creator read does not follow within 1h you pay 2× on the write with no 0.1× read to amortize — net costlier than not caching. | fix: gate the marker exactly like scoring.py — only attach `cache_control` when `(len(block1_text)+len(block2_text))//4 >= 1024` (Sonnet) and drop it when `dna_brief` is falsy; correct the comments to say the floor is cleared only with a populated DNA brief.

- [SEV2] titles.py:141-143, thumbnails.py:236-240, hooks.py:197-204 — untrusted content is placed in the **system role**. titles Block 3 interpolates `transcript_summary` and `video_title` directly into a system `text` block (`f"VIDEO TO TITLE:\n{video_context}"`); thumbnails Block 3 interpolates `transcript_hook`; hooks Block 3 interpolates `transcript_excerpt`. The module's own `UNTRUSTED_CONTENT_POLICY` explicitly names "Video transcripts" and "YouTube video titles" as untrusted, and the Anthropic prompt-injection guidance cited in util.py states untrusted content **must never go in the system role**. The newer clip builders (clip_titles/clip_captions/clip_explain) do this correctly — they `wrap_untrusted(...)` the transcript in the **user** turn — so the posture is internally inconsistent; only the older Issue-128/129/130 builders leak untrusted text into system. Mitigant: the in-context UNTRUSTED_CONTENT_POLICY reduces (does not remove) the risk, and the attacker is largely the creator's own uploaded video. | fix: move `transcript_summary`/`video_title` (titles), `transcript_hook` (thumbnails), and `transcript_excerpt` (hooks) out of the system blocks and into the user-turn message via `wrap_untrusted("video_transcript", …)` / `wrap_untrusted("video_title", …)`, matching clip_titles.py's pattern.

- [cleanup] 7 separate module-level `Anthropic()` clients — clip_titles.py:31, titles.py:32, clip_explain.py:34, chapters.py:24, hooks.py:27, clip_captions.py:31, thumbnails.py:32 — each builds its own httpx connection pool (DRY / resource duplication). They are module-level (not per-call) so the rubric's singleton rule is technically met, but 7 pools for one provider is wasteful. | fix: one shared client in `knowledge/_client.py` (or a common `llm/client.py`) imported by all builders; keep the `.with_options(timeout=…)` per-call override where needed (it shares the underlying pool).

- [cleanup] Identical `usage_dict` construction duplicated at clip_titles.py:273-278, clip_captions.py:219-224, clip_explain.py:273-278 (same 4-key `getattr(response.usage, …)` block). | fix: extract `usage_from_response(usage) -> dict` into knowledge/util.py and call it in all three.

- [cleanup] The per-call `logger.info("… tokens: in=%d cached_read=%d cached_write=%d out=%d", …)` block is duplicated in all 7 builders (titles.py:252, thumbnails.py:344, hooks.py:244, chapters.py:226, clip_titles.py:279, clip_captions.py:225, clip_explain.py:279). | fix: fold the log line into `record_llm_metric` / `record_llm_tokens` (both already receive the same four numbers) so each call site is one line.

- [cleanup] Bare unparameterized `-> tuple:` return annotation on titles.py:107 `_build_request` and thumbnails.py:193 `_build_concepts_request` (the clip builders correctly use `-> tuple[list[dict], list[dict]]`). | fix: parameterize to `-> tuple[list[dict], list[dict], list[dict]]` (system, tools, messages).

- [cleanup / needs-runtime-confirmation] Web-search builders pin `settings.ANTHROPIC_WEB_SEARCH_TOOL = "web_search_20260209"` and force `allowed_callers=["direct"]` because they do NOT want dynamic filtering (which, per the live doc, additionally requires the code-execution tool). The current basic-search version is `web_search_20250305`; the natural "direct, no dynamic filtering" choice. 20260209 remains available so this is not a defect, but the direct-call workaround on a dynamic-filtering-oriented version is worth confirming on a live call (mocked tests won't catch a routing regression). | fix (optional): consider `web_search_20250305` for the direct-only paths, or add a live smoke assertion that the streamed text block is non-empty.

## Rubric coverage
| Category | Status |
|---|---|
| 1 Resource lifecycle | 1 cleanup (7 duplicate clients); no DB/temp-media in this module |
| 2 Concurrency & scale | ok — sync `messages.create` builders are documented "call via asyncio.to_thread"; caller-side (out of slice). Bounded inputs (images `[:10]`, transcript char-capped) |
| 3 Security & compliance | 1 SEV2 (untrusted transcript/title in system role, older builders); virality/honesty guards + injection hardening otherwise strong; no tokens/PII/SQL here |
| 4 Clip-quality | n/a (generation module, not clip selection) — clip_explain correctly constrains `cited_principle` to the canonical enum |
| 5 Anthropic SDK | 1 SEV2 (inert cache marker below 1024 floor for empty DNA). Verified OK: token usage logged after EVERY call (record_llm_metric/record_llm_tokens + info log, all 7); thumbnail vision call IS metered+billed (image tokens in usage.input_tokens, logged); structured outputs correctly used on non-web-search paths with `additionalProperties:false` and correctly NOT used on web_search paths (would 400 with citations) |
| 6 Cleanliness & typing | 4 cleanup (dup clients, dup usage_dict, dup token-log line, bare `tuple`); no TODO/print/pdb |
| 7 Error handling / API | n/a (not a router; builders raise typed SDK errors to caller as documented) |
| 8 Config & paths | ok — model IDs + web-search tool are config-driven; no filesystem paths; thumbnail URLs are absolute https |

## Module verdict
NEEDS-WORK — no blockers; two SEV2s to fix (unconditional 1h cache marker goes inert below Sonnet 4.6's live-confirmed 1,024-token floor when the DNA brief is empty/short, and the older streaming builders put untrusted transcript/title in the system role — both already solved elsewhere in the codebase and just need to be adopted here), plus DRY cleanups.
