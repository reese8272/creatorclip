# Research Brief 02 — Agentic Usage, Prompt Caching & LLM Cost

**Tracked gap:** Issue 167 (`docs/research/README.md`)
**Author:** LLM-systems research agent (read-only; no product code changed)
**Date:** 2026-06-22
**Method:** Anthropic current standard via `/claude-api` skill + live `platform.claude.com`
pricing page (fetched 2026-06-22); repo grounded with `file_path:line`.

---

## 1. Executive summary

**The good news first: the architecture is fundamentally sound and cheap.** Claude is the
only LLM, every call streams, usage is logged after every call, the chat loop is correctly
hand-rolled and creator-scoped, and the one high-volume operation (clip scoring) is the one
endpoint with caching wired correctly. There is no runaway-cost bug in flight.

**Top findings (ordered by impact):**

1. **The SOT is stale on the single most cost-relevant fact: the model.** `docs/SOT.md:16`
   claims `claude-opus-4-7` is used for DNA synthesis. **No code uses Opus anywhere.**
   Every caller reads `settings.ANTHROPIC_MODEL` which defaults to `claude-sonnet-4-6`
   (`config.py:65`), and the three highest-frequency-or-cheap paths use Haiku 4.5
   (`knowledge/chapters.py:22`, `knowledge/hooks.py:25`, `routers/insights.py:456`). This is
   GOOD for cost — but the doc must be corrected, and the model-per-task choice should be made
   *deliberately* rather than by default. **Needs a `docs/DECISIONS.md` entry.**

2. **The single biggest cost lever is prompt caching coverage on the web-search briefs.**
   Clip scoring caches its DNA prefix (1h TTL, `clip_engine/scoring.py:245`). But
   `titles.py`, `hooks.py`, `thumbnails.py`, `analysis/brief.py`, and `improvement/brief.py`
   deliberately have **no cache breakpoint** — the Issue 138/140 audits *removed* the markers
   because the static-instructions + DNA-brief prefix fell below the model's cacheable floor.
   That was the right call for an inert marker, but it leaves the fix half-done: the prefix
   should be *raised above the floor and then cached*, not abandoned. See §3.

3. **The DNA build cache breakpoint is positioned to almost never hit.** `dna/brief.py`
   caches the instructions + stated-identity prefix, but DNA is built **once per creator per
   refresh** with a >5-minute gap and the breakpoint uses the default 5-minute TTL
   (`dna/brief.py:88`). The cache is written and essentially never read. See §3.

4. **There is no per-creator LLM rate limit or token-cost ledger outside chat.** The `Usage`
   table (`models.py:664`) is **defined but never written to** — `grep` for `Usage(` finds only
   the class definition. Token cost is logged to `app.log` and (for chat) to
   `chat_messages.tokens_in/out/cache_read`, but there is no aggregate cost accounting per
   creator and no per-creator LLM quota on the non-chat endpoints. This is a pre-public-launch
   gate (CLAUDE.md → Pre-Public-Launch Requirements). See §4 and cross-ref to prompt 06.

5. **Unit economics are safe by a wide margin at current model choice, but thin at the
   cheapest pack tier if the model is ever upgraded to Opus.** A full video pipeline costs
   ~$0.01–0.04 in LLM spend on Sonnet/Haiku vs. 4.5¢–0.45¢/minute of revenue. The web-search
   briefs (titles/hooks/thumbnails/improvement) are the per-call cost drivers because each adds
   $0.01/search ($10 / 1,000 searches) on top of tokens. See §4.

**The single biggest cost lever:** raise the cached prefix above the model floor and re-enable
caching on the web-search brief endpoints (titles/hooks/thumbnails/analysis/improvement) — these
are creator-facing, repeated within a session, and currently pay full input price every call.

---

## 2. Agentic usage

### Current Anthropic standard (`/claude-api` → `tool-use.md`, `agent-design.md`)

- For **streaming + client-side tools**, the documented pattern is exactly a **manual agentic
  loop**: `messages.stream()` → `get_final_message()` → if `stop_reason == "tool_use"`, execute
  tools, append `tool_result`, loop. The SDK's `beta_tool` tool-runner "currently returns
  complete messages" and cannot give per-token SSE streaming — so the hand-rolled loop is
  correct here, not a workaround.
- **Tool descriptions are the dominant driver of tool-use accuracy.** Current Opus/Sonnet reach
  for tools *conservatively*; descriptions should be **prescriptive about *when* to call**, not
  just what the tool does (`tool-use-concepts.md` → "Be prescriptive about when to call it").
- **Managed Agents / `claude-agent-sdk`** are for server-hosted, stateful, container-backed
  agents — not applicable here (CreatorClip hosts its own DB-touching tools and needs SSE
  streaming + per-creator isolation per call).

### What the repo does today

- `chat/runner.py:73-104` — the manual loop. **Assessment: correct and well-built.**
  - Forced-`tools=None` final round (`chat/runner.py:76`) guarantees a text answer at the cap
    instead of a dangling `tool_use` — matches the documented "set a max_continuations limit"
    guidance.
  - Iteration cap `CHAT_MAX_TOOL_ITERATIONS=4` (`config.py:79`), output cap
    `CHAT_MAX_TOKENS=1500` (`config.py:81`), 8-turn history truncation (`config.py:83`) —
    all bound worst-case spend, matching the LeanOps "cap tool iterations" finding cited in
    DECISIONS 2026-06-17.
  - Tool-result error handling is correct: `execute_tool` (`chat/tools.py:290-308`) returns a
    JSON error payload instead of raising, so the model can recover. **Minor gap:** it returns
    the error as ordinary `content` and does **not** set `"is_error": True` on the
    `tool_result` block (`chat/runner.py:103`). The Anthropic standard
    (`tool-use-concepts.md` → "Error handling in tool results") is to set `is_error: true` so
    the model reliably treats it as a failure. Low-impact, but a cheap correctness win.
  - **Parallel tool calls:** the loop already handles multiple `tool_use` blocks in one round
    (`chat/runner.py:98-104`) and sends all results in one user turn — correct. It does not set
    `disable_parallel_tool_use`, which is fine (the 5 read-only tools are parallel-safe).

- `chat/tools.py:36-104` — the 5 creator-scoped tools. **Assessment: well-designed.** Names are
  specific (`get_video_performance`, not `get_video`), descriptions are prescriptive about when
  to call ("Call this when the creator asks why a particular video did well…"), schemas use
  `additionalProperties: False`, and `creator_id` is injected by the worker, never model-supplied
  (`chat/tools.py:291`) — the load-bearing isolation guarantee, pinned by
  `tests/test_chat_isolation_integration.py`. One refinement: `get_recent_videos.limit` has no
  `maximum` in its JSON schema (`chat/tools.py:58`); the bound is enforced in code
  (`chat/tools.py:131`) but advertising `"maximum": 25` in the schema would let the model
  self-correct.

### Where agentic would help / hurt elsewhere

- **Would help (low risk):** the **improvement brief** and **title/hook/thumbnail** endpoints
  already use the server-side `web_search` tool, which *is* an agentic loop under the hood. They
  are single-turn and correctly bounded by "N searches maximum" prompt instructions
  (`knowledge/hooks.py:50`, `titles.py:64`, `thumbnails.py:60`, `improvement/brief.py:44`).
  Consider adding **`web_search_20260209`'s dynamic filtering** (already the configured tool
  version — `config.py:71`) which filters results before they hit context, reducing input tokens
  on these calls at no extra config cost (`tool-use-concepts.md` → Dynamic Filtering).
- **Would hurt:** making **clip scoring** agentic. It is a single batched judgment over ≤8
  candidates (`clip_engine/scoring.py`, `candidates.py:140`). A tool loop would multiply both
  latency and token cost (intermediate results re-entering context every round) for no quality
  gain. Keep it single-shot.

---

## 3. Caching

### Current Anthropic standard (`/claude-api` → `prompt-caching.md`, live docs)

- **Prefix match:** any byte change anywhere before a breakpoint invalidates everything after.
  Render order `tools → system → messages`. Stable content first, volatile last.
- **Minimum cacheable prefix (authoritative, confirmed 2026-06-22):**
  | Model | Floor |
  |---|---:|
  | Opus 4.8 / 4.7 / 4.6 / 4.5, **Haiku 4.5** | **4096 tokens** |
  | Fable 5, **Sonnet 4.6**, Haiku 3.5 | **2048 tokens** |
  | Sonnet 4.5 / 4 / 3.7 | 1024 tokens |
  Below the floor it **silently won't cache** (`cache_creation_input_tokens: 0`), no error.
  This vindicates the DECISIONS 2026-06-16/138 correction (Sonnet 4.6 = 2048, Haiku 4.5 = 4096).
- **Economics:** cache read = **0.1×** input. Cache write = **1.25×** (5-min TTL) or **2×**
  (1-hour TTL). Break-even: 5-min pays off after **1** read; 1-hour after **2** reads.
- **20-block lookback** and **5-min/1-hour TTL** are the two silent killers for low-frequency,
  long-gap workloads.

### What the repo does today

| Endpoint | File:line | Cache breakpoint? | Effective? |
|---|---|---|---|
| **Clip scoring** | `clip_engine/scoring.py:245` (DNA brief, `ttl:"1h"`) | ✅ Yes | ✅ **Yes** — DNA is constant across a creator's videos; the 1h TTL spans a batch. This is the model endpoint, done right. |
| **DNA build** | `dna/brief.py:88` (instructions+identity, default 5-min TTL) | ✅ Yes | ⚠️ **Rarely hit** — DNA builds once per creator per refresh, gaps ≫ 5 min. Write premium paid, ~0 reads. |
| **Chat** | `chat/prompt.py:61` (instructions block) | ✅ Yes | ⚠️ **Conditional** — only hits if the prefix (instructions + tool schemas) clears the **2048-token Sonnet floor**; needs token-count verification (see open question). The breakpoint *placement* is correct (volatile channel name in a second uncached block, `chat/prompt.py:64-67`; tool results append after the prefix). |
| **Titles** | `knowledge/titles.py` | ❌ Removed (Issue 138) | ❌ No cache — pays full input every call |
| **Hooks** | `knowledge/hooks.py:174` | ❌ Removed (audit) | ❌ No cache |
| **Thumbnails** | `knowledge/thumbnails.py` | ❌ Removed (Issue 138) | ❌ No cache |
| **Video analysis** | `analysis/brief.py:90` | ❌ Removed (audit) | ❌ No cache |
| **Improvement brief** | `improvement/brief.py:88` | ✅ Yes | ⚠️ Same floor risk as chat; verify the static+DNA prefix clears 2048 |
| **Analyze-performer** | `routers/insights.py:566` | ❌ Removed (Issue 140) | ❌ No cache — but prefix is ~30 tokens (correctly inert) |

### Findings

- **No remaining *inert* markers** (the Issue 138/140 class — write premium, zero reads). That
  cleanup is genuinely complete; `tests/test_analyze_performer.py` pins the absence. Good.
- **But the audits stopped at "remove the marker" instead of "make the prefix cacheable."** For
  titles/hooks/thumbnails/analysis, the static instructions + DNA brief are the *same bytes
  across every call for a creator* — exactly what caching exists for. The fix is to **raise the
  cached prefix above the Sonnet-4.6 2048-token floor** (e.g. fold the evergreen corpus / a
  fuller instruction block into the cached prefix) and re-add a single 1h breakpoint at the end
  of it. A creator who runs titles → hooks → thumbnails on one video in one session would then
  read the DNA prefix 3× at 0.1× instead of paying 1× three times.
- **DNA-build cache is on the wrong TTL for its access pattern.** It's a single call with no
  near-term repeat; the breakpoint is effectively a pure write premium. Either drop the marker
  (honest: it never reads) or — better — **share the DNA-brief prefix with clip scoring**:
  scoring already caches `CREATOR DNA:\n{dna_brief}` at 1h (`clip_engine/scoring.py:241-246`),
  and DNA build runs just before scoring in the same pipeline. If the two used a byte-identical
  cached block, the scoring run would *read* what the build *wrote*. (Caveat: different system
  instructions precede each, so this needs the DNA block to be a separately-keyed breakpoint —
  worth a spike, flagged as an open question.)
- **The chat loop's growing suffix does not bust the cached prefix** — confirmed correct. Tool
  results append as new `messages` entries (`chat/runner.py:104`) *after* the cached system
  prefix, and the channel-name block is uncached (`chat/prompt.py:64`). The only risk is the
  20-block lookback if a single turn exceeds ~20 tool-result blocks, which the
  `CHAT_MAX_TOOL_ITERATIONS=4` cap makes impossible (≤4 rounds × small results).
- **`cached_write_1h` telemetry is wired** (`clip_engine/scoring.py:261` reads
  `usage.cache_creation.ephemeral_1h_input_tokens`) — keep this; it's the only place we can
  confirm a 1h breakpoint actually lands in the 1h tier. Extend the same logging to any newly
  cached endpoint.

---

## 4. Cost

### Per-operation cost model

Assumptions, grounded: default model **Sonnet 4.6** ($3 input / $15 output / $0.30 cache-read /
$3.75 5-min-write / $6 1h-write per MTok). Haiku 4.5 where noted ($1 / $5 / $0.10 / $1.25 / $2).
Web search **$10 / 1,000 searches = $0.01/search**. Token figures are engineering estimates from
the prompt sizes in-repo (`max_tokens` per call is hard-capped where shown); they are *order-of-
magnitude*, not metered — the repo logs real tokens per call to `app.log`, so these should be
replaced with measured values once a representative run is captured.

| Operation | Model | ~Input tok | ~Output tok | Web searches | Cached? | Est. $/call (uncached) | Est. $/call (cache hit) |
|---|---|---:|---:|---:|---|---:|---:|
| **DNA build** (`dna/brief.py:154`, max_out 2000) | Sonnet 4.6 | ~6k | ~1.2k | 0 | prefix (rarely hit) | ~$0.036 | ~$0.034 |
| **Clip scoring**, per video, ≤8 candidates (`scoring.py:239`, max_out 1200) | Sonnet 4.6 | ~5k (≈2.5k DNA cached) | ~1k | 0 | ✅ DNA 1h | ~$0.030 | **~$0.023** |
| **Improvement brief** (`improvement/brief.py:162`, max_out 2000) | Sonnet 4.6 | ~4k + search results | ~1.5k | ~3 | partial | ~$0.05 + $0.03 = **~$0.08** | ~$0.07 |
| **Title suggestions** (`titles.py:208`, max_out 2000) | Sonnet 4.6 | ~3k + results | ~1k | ~3 | ❌ | ~$0.025 + $0.03 = **~$0.055** | (no cache today) |
| **Hook analysis** (`hooks.py:212`, max_out 1024) | **Haiku 4.5** | ~3k + results | ~0.8k | ~2 | ❌ | ~$0.007 + $0.02 = **~$0.027** | — |
| **Chapters** (`chapters.py:213`, max_out 2000) | **Haiku 4.5** | ~4k | ~1k | 0 | ❌ | **~$0.009** | — |
| **Thumbnail concepts** (`thumbnails.py:284`, max_out 2000) | Sonnet 4.6 | ~3k + results | ~1.2k | ~2 | ❌ | ~$0.027 + $0.02 = **~$0.047** | — |
| **Video analysis** (`analysis/brief.py:154`, max_out 2000) | Sonnet 4.6 | ~4k | ~1.5k | 0 | ❌ | **~$0.035** | (re-cacheable) |
| **Analyze-performer** (`insights.py:579`, max_out 256) | **Haiku 4.5** | ~0.5k | ~0.25k | 0 | ❌ (inert, correct) | **~$0.002** | — |
| **Chat turn**, with 1 tool round (`runner.py`, max_out 1500) | Sonnet 4.6 | ~3k (prefix maybe cached) | ~0.5k | 0 | prefix | ~$0.016 | ~$0.010 |

**Top cost drivers:** (1) the **web-search briefs** — the $0.01/search adds up and they are
*uncached*; (2) **clip scoring** by volume (one call per video, the core pipeline). Output
tokens dominate marginal cost (5× input on Sonnet), so the `max_tokens` caps (1200 scoring,
1500 chat, 256 analyze-performer) are doing real work — keep them tight.

**Highest-ROI reductions, in order:**
1. **Re-enable caching on the 4 uncached repeated-prefix endpoints** (titles/hooks/thumbnails/
   analysis) by raising the prefix above 2048 tokens. Saves ~0.9× of the input cost on every
   repeat call within a 1h window.
2. **Move chapters fully to Haiku** — already done; confirm titles/thumbnails can't also drop to
   Haiku without quality loss (they're creator-visible; needs an eval, cross-ref prompt 08).
3. **Dynamic-filtering web search** (already the configured tool version) trims search-result
   input tokens.
4. **Batch API (50% off)** for any non-interactive job. **Candidate: clip scoring** is run in a
   Celery worker, not user-blocking past the SSE progress bar — if scoring tolerates
   minutes-not-seconds latency, routing it through the Batch API (`client.messages.batches`)
   halves its token cost. This is the largest structural saving for the highest-volume call.
   **Needs a `docs/DECISIONS.md` entry** (it changes the scoring call path and latency profile).

### Tie to billing — unit economics

- **Revenue per minute** (`billing/packs.py:28-34`): starter 200 min/$18 = **9.0¢/min**… wait —
  prices are in **cents**: starter = `Pack("starter", 200, 1800)` → $18.00 / 200 min = **9.0¢/
  min**; studio = $225 / 5000 = **4.5¢/min**. So revenue is **4.5¢–9.0¢ per video-minute**.
  (Correction to any prior "0.45¢" reading — `price_cents` is cents, not dollars.)
- **Cost per video-minute** (`billing/ledger.py:29`, 1 min charged per 60s, min 1): a 10-minute
  video charges 10 minutes = **45¢–90¢ revenue**. LLM cost for that video = 1 DNA-amortized +
  1 scoring call (~$0.023) + optional creator-invoked briefs. **Even the full set of briefs
  (~$0.30 LLM) sits comfortably under the floor revenue.** The pipeline is **not** at risk of
  running at a loss on LLM spend at current model choice.
- **The risk is upgrade drift, not today.** If `ANTHROPIC_MODEL` is ever moved to Opus
  ($5/$25, ~1.7× Sonnet) *and* the SOT's stale "Opus for DNA" line is taken as instruction, the
  briefs creep toward $0.10–0.15 each; still under the per-minute floor but the margin on a
  short (1-minute charge) video that triggers several briefs tightens. Decide model-per-task
  deliberately and log it.
- **Missing guardrail (pre-launch gate):** there is **no per-creator LLM rate limit / quota on
  the non-chat endpoints**, and the **`Usage` cost ledger is never written** (`models.py:664`
  defined, zero writers). Chat has a daily-message quota (`CHAT_DAILY_MESSAGE_LIMIT=25`,
  `config.py:76`) but title/hook/thumbnail/analysis/improvement have only the generic slowapi
  IP/creator limiter. A creator (or a bug) could fan these out. **Cross-ref prompt 06
  (monetization, Issue 171):** that brief owns the pricing/quota model; this brief owns the
  finding that the *accounting surface* (`Usage` table) is inert and must be populated for any
  quota to be enforceable.

---

## 5. Proposed issues (dependency-ordered, house style)

### Issue 167a: Correct stale LLM model facts in SOT + log model-per-task decision
**What:** `docs/SOT.md:16` says `claude-opus-4-7` is used for DNA synthesis; no code uses Opus.
Correct the stack row to reflect reality (`claude-sonnet-4-6` default via `ANTHROPIC_MODEL`;
Haiku 4.5 for chapters/hooks/analyze-performer) and record the deliberate model-per-task choice.
**Acceptance criteria:**
- [ ] `docs/SOT.md` LLM row matches code (`config.py:65`, the Haiku call sites)
- [ ] `docs/DECISIONS.md` entry: which task uses which model and why (cost vs. quality), citing
      this brief and `platform.claude.com/pricing`
- [ ] Note the eval dependency: any model downgrade for creator-visible output (titles/
      thumbnails) is gated on prompt 08's quality eval
**DECISIONS entry:** ✅ required.

### Issue 167b: Re-enable prompt caching on the repeated-prefix brief endpoints
**What:** titles/hooks/thumbnails/analysis lost their cache breakpoint because the prefix fell
below the 2048-token Sonnet-4.6 floor (Issue 138/140). Raise the shared static+DNA prefix above
the floor and re-add a single 1h breakpoint at its end, so repeat calls within a session read at
0.1×. Verify with `cache_read_input_tokens > 0` across two same-creator calls.
**Acceptance criteria:**
- [ ] Each endpoint's cached prefix measured > 2048 tokens via `messages.count_tokens`
- [ ] Breakpoint placed at end of the stable prefix; volatile per-video content after it
- [ ] A test asserts `cache_read_input_tokens > 0` on the 2nd of two same-creator calls
      (real Postgres + recorded fixture, no live YouTube)
- [ ] `cached_write` / `cached_write_1h` logged like `clip_engine/scoring.py:257`
**DECISIONS entry:** ✅ required (reverses the "remove the marker" stance of Issue 138/140 — note
the *why*: prefix raised above floor, not a fragile micro-marker).

### Issue 167c: Populate the `Usage` cost ledger + per-creator LLM quotas on brief endpoints
**What:** `models.py:664` `Usage` is never written. Write `tokens_in/tokens_out` (and a cost
estimate) per creator per period from every LLM call's logged usage, and add a per-creator
daily/period quota to the non-chat brief endpoints (mirroring `CHAT_DAILY_MESSAGE_LIMIT`).
**Acceptance criteria:**
- [ ] Every LLM caller increments `Usage` for the owning creator (single helper, DRY)
- [ ] Per-creator quota enforced on titles/hooks/thumbnails/analysis/improvement before the call
- [ ] Quota + ledger covered by tests; per-creator isolation asserted
- [ ] `.env.example` documents any new quota config
- [ ] Coordinated with prompt 06 (Issue 171) so the quota model matches the pricing model
**DECISIONS entry:** ✅ required (introduces LLM-level accounting + quota policy).

### Issue 167d: Route clip scoring through the Batch API (50% token discount)
**What:** Clip scoring is a Celery-worker call, not user-blocking past the SSE bar. If it
tolerates batch latency (minutes), route it through `client.messages.batches` for a 50% token
discount on the highest-volume LLM call. Prompt caching stacks with batch.
**Acceptance criteria:**
- [ ] Spike: confirm scoring latency budget tolerates batch turnaround (most < 1h, max 24h)
- [ ] If yes: scoring submits via batches, polls, idempotent + retry-safe (Celery rules)
- [ ] DNA cache prefix preserved inside the batch request (`batches.md` supports caching)
- [ ] Per-video cost halved in the cost model; verified against logged usage
**DECISIONS entry:** ✅ required (changes the scoring call path + latency profile).

### Issue 167e: Tool-result `is_error` flag + chat tool schema `maximum`
**What:** Small correctness wins in the chat loop. Set `"is_error": True` on failed
`tool_result` blocks (`chat/runner.py:103`, executor signals failure in `chat/tools.py:307`);
add `"maximum": 25` to `get_recent_videos.limit` schema (`chat/tools.py:58`).
**Acceptance criteria:**
- [ ] Failed tool results carry `is_error: true` per `tool-use-concepts.md`
- [ ] `get_recent_videos` schema advertises the bound it already enforces
- [ ] Existing chat isolation/loop tests still green
**DECISIONS entry:** ❌ not needed (conformance to documented standard).

### Issue 167f: Spike — share the DNA-brief cached block between DNA build and clip scoring
**What:** DNA build writes a DNA-prefix cache that never reads; scoring writes its own DNA
prefix moments later. Investigate making the `CREATOR DNA` block byte-identical and a separate
breakpoint so scoring reads what build wrote (same pipeline run, within 1h TTL).
**Acceptance criteria:**
- [ ] Spike documents whether a shared, separately-keyed DNA breakpoint is feasible given the
      differing system instructions (render-order + 20-block-lookback constraints)
- [ ] If feasible, a follow-up issue; if not, drop the DNA-build marker (it never reads) and say so
**DECISIONS entry:** ➖ only if it changes the caching approach.

---

## 6. Open questions for the human

1. **Model-per-task:** keep Sonnet 4.6 as default and Haiku for the cheap paths, or is any
   creator-visible output (titles/thumbnails) quality-sensitive enough to want Opus? (one line)
2. **Batch scoring latency:** is clip scoring allowed to take minutes (Batch API, –50%), or must
   it stay seconds-fast for the live SSE experience? (decides Issue 167d)
3. **Per-creator LLM quota policy:** what daily/period cap on briefs is acceptable, and should it
   deduct video-minutes or be a separate counter? (defer to prompt 06 / Issue 171 if owned there)
4. **Verify chat/improvement prefix clears the 2048 floor:** should we run `count_tokens` on the
   live system prompt + tool schemas to confirm the existing breakpoints actually cache, or is
   that already known from `app.log` `cache_read` values? (one line)

---

### Cross-references
- **Prompt 06 (monetization, Issue 171):** owns pricing/packaging/quota; this brief flags the
  inert `Usage` ledger (Issue 167c) and the 4.5¢–9.0¢/min revenue floor as inputs.
- **Prompt 08 (personalization eval, Issue 173):** gates any model downgrade for clip
  scoring/titles/thumbnails on a quality eval.
- **Prompt 04 (security/scale, Issue 169):** per-creator LLM rate limiting is also a scale gate.

### Doc-freshness flags raised
- `docs/SOT.md:16` — stale model claim (Opus for DNA; no Opus in code). → Issue 167a.
- `docs/DECISIONS.md` 2026-06-17 chat entry cites "Sonnet 4.6 $3/$15, cache read 0.1×,
  ~$0.014–0.08/message" — **all confirmed accurate** against the live pricing page. No correction
  needed; good precedent to mirror.
