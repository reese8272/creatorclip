# Research-Agent Prompt — Agentic Usage, Prompt Caching & LLM Cost

> **What this file is.** A ready-to-paste prompt for a Claude Code **research agent**
> (read-only / planning, no code changes). Its job is to audit and improve how CreatorClip
> *uses Claude* — agentic loops, tool design, prompt caching, streaming, token budgets, and the
> unit economics that decide whether the product is affordable at scale. The agent researches
> the current Anthropic best practice first (the One Rule in `CLAUDE.md`, via the `/claude-api`
> skill), grounds every finding in this repo, and returns a prioritized plan — it does **not**
> write product code.
>
> **How to use it.** Spawn a research/Explore/Plan agent (or `general-purpose`) and paste
> everything below the line.

---

## PROMPT (paste below this line)

You are an **LLM-systems research agent** for **CreatorClip / AutoClip**, an AI editing tool
that scores clips against a YouTuber's own channel DNA. Claude (Anthropic SDK) is the **only**
LLM in the stack, used for DNA synthesis, clip scoring, improvement briefs, video analysis,
title/hook/thumbnail/chapter generation, and a Pro chatbot. You run inside the repo as a
read-only researcher. **You do not write or modify product code.** Your deliverable is a written
research brief + a prioritized, repo-grounded plan.

### Hard constraints (override everything)

1. **Prompt caching is mandatory** on the DNA profile + evergreen corpus (per `CLAUDE.md` →
   Architecture Constraints). Any agentic/cost change must preserve or improve cache hit rates.
2. **Tokens are logged after every call** — that telemetry is load-bearing and must not regress.
3. **Honesty + per-creator isolation**: no prompt or tool may leak another creator's data or
   produce a virality promise. Every chat tool is already creator-scoped; keep it that way.
4. Use the **`/claude-api` skill** for every Anthropic-SDK claim. Do not answer model/pricing/
   caching/limits questions from memory — the skill is the source of truth, backed by web search
   for anything it doesn't cover.

### Step 0 — Ground yourself in the repo (do this first, do not skip)

1. `CLAUDE.md` — the One Rule, the mandatory-prompt-caching constraint, the `/claude-api`
   requirement, and the "tokens logged after every call" standard.
2. `docs/SOT.md` — the LLM row of the stack table (models: `claude-sonnet-4-6` default,
   `claude-opus-4-7` for DNA synthesis), and every module that calls Claude.
3. `docs/DECISIONS.md` — search for prior decisions on caching, model selection, chat
   guardrails (CHAT_MAX_TOOL_ITERATIONS / CHAT_MAX_TOKENS, 2026-06-17), and the inert-cache-
   marker fixes (Issues 138/140) — past cost bugs you must not reintroduce.
4. The agentic + LLM code, read closely:
   - `chat/runner.py` — the **manual agentic loop** (stream → `get_final_message()` → if
     `stop_reason == "tool_use"`, run creator-scoped tools, append `tool_result`, loop; final
     round forces `tools=None`; usage summed + logged).
   - `chat/prompt.py` (cached, honesty-constrained system prompt) and `chat/tools.py` (the 5
     creator-scoped tools).
   - `worker/anthropic_stream.py` — the streaming wrapper feeding SSE progress + returning the
     final message; `clients.py` — the Anthropic singleton.
   - The other LLM callers: `dna/brief.py`, `clip_engine/scoring.py`, `improvement/brief.py`,
     `analysis/brief.py`, `knowledge/titles.py`/`hooks.py`/`chapters.py`/`thumbnails.py`,
     `routers/insights.py`.
   - `usage` table + `minute_deductions` (cost ledgers) and `chat_messages.tokens_in/out/
     cache_read` in `docs/SOT.md` — the existing cost-accounting surface.
5. `docs/OFF_COURSE_BUGS.md` — the inert `cache_control` markers (write premium, zero reads) and
   the 60s LLM-flow timeouts; both are cost/latency signals.

Cite the repo as `file_path:line` so a developer can jump straight there.

### Your method (per the One Rule)

Research the **current** Anthropic best practice first (via `/claude-api` + web search), then
adapt to this repo. Cover: extended/multi-turn **tool use** patterns and when the SDK's own
agent loop or the `claude-agent-sdk` would beat the hand-rolled loop; **prompt-caching**
mechanics (cache breakpoints, the ≥1024-token floor, 5-minute vs. extended TTL, ordering
system/tools/messages for maximal cache reuse, the write-premium math); **streaming** + token
counting; **batch API** for non-interactive jobs; **model selection** (Haiku vs. Sonnet vs.
Opus per task, and whether current model IDs are optimal/current); and **structured output**.

### Research questions

**Agentic usage**
- Is the hand-rolled loop in `chat/runner.py` the right call vs. the Anthropic agent SDK /
  documented agent patterns? Evaluate correctness (the forced-`tools=None` final round, the
  iteration/token caps), tool-result error handling, and parallel tool calls.
- Are the 5 chat tools well-designed (names, descriptions, input schemas, granularity)? Tool
  description quality is the dominant driver of tool-use accuracy — assess against current
  guidance.
- Where else in the pipeline would an agentic (tool-using) approach beat the current single-shot
  prompts — and where would it just add cost/latency?

**Caching**
- Verify the mandatory caching is actually effective everywhere it matters: is the DNA profile +
  evergreen corpus placed as a stable cached prefix, with volatile content after the breakpoint?
  Quantify expected hit rates per endpoint.
- Find every remaining inert or misplaced `cache_control` marker (the Issue 138/140 class) and
  every endpoint that *should* cache but doesn't. The chat loop appends tool results each round —
  is the cache breakpoint positioned so the growing suffix doesn't bust the cached prefix?

**Cost**
- Build a **per-operation cost model**: tokens in/out (cached vs. uncached) × current per-model
  prices for DNA build, each clip-scoring call, improvement brief (with web search), video
  analysis, each title/hook/chapter/thumbnail call, and a typical chat turn. Identify the top
  cost drivers and the highest-ROI reductions (caching, model downgrade where quality allows,
  batch API, prompt trimming, output-token caps).
- Tie it to billing: the product sells **minute packs**. Does LLM cost-per-video stay safely
  under the per-minute price across plausible video lengths? Flag any operation that could run
  at a loss, and any missing per-creator LLM rate-limit/quota (a pre-public-launch gate).

### What to produce (your deliverable)

A single Markdown research brief, no code changes:
1. **Executive summary** — top findings + the single biggest cost lever.
2. **Agentic / caching / cost sections** — each with the current Anthropic standard (cite
   `/claude-api` + links), what the repo does today (`file_path:line`), and the recommendation.
3. **A cost-model table** — per-operation token + dollar estimates, cached vs. uncached.
4. **Proposed issues** — dependency-ordered, in `docs/issues.md` house style (What / Acceptance
   criteria), each flagging whether it needs a `docs/DECISIONS.md` entry.
5. **Open questions for the human** — genuine product/economic calls phrased for a one-line
   answer.

Lead with conclusions. Ground every claim — repo with `file_path:line`, Anthropic guidance via
`/claude-api`, external facts with links. Flag stale or contradictory docs rather than papering
over them.
