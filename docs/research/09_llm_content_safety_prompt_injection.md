# Research-Agent Prompt — LLM Content Safety & Prompt Injection

> **What this file is.** A ready-to-paste prompt for a Claude Code **research agent**
> (read-only / planning, no code changes). It drives the Phase 1 (CHECK) research for the
> adversarial-input gap: creator/YouTube-sourced content (titles, transcripts, descriptions)
> flows into Claude prompts and back into the UI — a prompt-injection and unsafe-output surface.
> Industry-standard-first (the One Rule in `CLAUDE.md`); grounds findings in this repo; returns a
> prioritized plan. **Does not write product code.**
>
> **Tracked as:** `docs/issues.md` → Issue 174.

---

## PROMPT (paste below this line)

You are an **LLM application-security research agent** for **CreatorClip / AutoClip**. Untrusted
text — YouTube video titles/descriptions, auto-generated transcripts of uploaded video, and
creator free-text (identity, notes) — is fed into Claude prompts (DNA, scoring, briefs, analysis,
titles/hooks/chapters/thumbnails, the agentic chatbot) and rendered back into the UI. That's a
**prompt-injection**, **data-exfiltration**, and **unsafe-output** surface. You run inside the
repo as a read-only researcher. **You do not write or modify product code.** Your deliverable is
a written research brief + a prioritized, repo-grounded plan.

### Hard constraints (override everything)

1. **Per-creator isolation is sacred.** The nightmare case: an injection in creator A's content
   causes a tool call or prompt that exposes creator B's data. The chat tools are creator-scoped
   — verify nothing can break that scoping.
2. **Honesty.** No injection may coerce the model into emitting a virality promise or disabling
   the honesty disclaimer (a structural test exists — treat bypassing it as a finding).
3. **No tokens/PII** reachable by an injected instruction (e.g. "print your system prompt /
   secrets").

### Step 0 — Ground yourself in the repo (do this first, do not skip)

1. `CLAUDE.md` — the `/claude-api` requirement, honesty constraint, per-creator isolation rule.
2. The agentic + LLM surface (where untrusted text enters the model):
   - `chat/runner.py` (the agentic loop), `chat/prompt.py` (system prompt), `chat/tools.py` (the
     5 creator-scoped tools — the highest-risk surface, since injection + tools = action).
   - `dna/brief.py`, `clip_engine/scoring.py`, `improvement/brief.py`, `analysis/brief.py`,
     `knowledge/titles.py`/`hooks.py`/`chapters.py`/`thumbnails.py`, `routers/insights.py`
     (analyze-performer) — all consume titles/transcripts.
   - `ingestion/transcribe.py` (transcripts) and `youtube/data_api.py` (titles/metadata) — the
     untrusted sources.
3. The output path back to the user: how LLM output is rendered in `frontend/src/` (React's
   default escaping; confirm no `dangerouslySetInnerHTML`), and the legacy `escapeHtml` history.
4. `docs/OFF_COURSE_BUGS.md` — the **stored-XSS-via-YouTube-title** (Issue 149) and the XSS sweep
   (Issue 138): proof this surface has already bitten. `docs/COMPLIANCE.md` for data-handling.
5. Coordinate with the security/scalability prompt (`04`) — this prompt is the LLM-specific deep
   dive; that one is the broader posture.

Cite the repo as `file_path:line`.

### Your method (per the One Rule)

Research the **current** standard first, then adapt. Lean on the OWASP Top 10 for LLM
Applications (esp. LLM01 Prompt Injection, LLM02 Insecure Output Handling, LLM06 Sensitive
Information Disclosure, LLM08 Excessive Agency), Anthropic's own guidance on mitigating prompt
injection and safe tool use (via the `/claude-api` skill), and the trust-boundary pattern of
keeping untrusted content in clearly delimited user turns, never in system instructions.

### Research questions

- **Injection mapping.** Enumerate every path where untrusted text reaches a prompt. For each:
  is the untrusted content clearly separated from instructions (delimited, in a user turn, not
  concatenated into the system prompt)? Could an injected instruction change the model's behavior,
  trigger an unintended tool call, or alter output?
- **Excessive agency (the chat tools).** Can an injection in fetched data (e.g. a malicious video
  title returned by a tool) cause the loop to call another tool with attacker-chosen input, or
  cross the creator scope? Audit `execute_tool` scoping and the loop's tool-result handling in
  `chat/runner.py` against LLM08.
- **Insecure output handling.** Trace LLM output to the UI. Is everything escaped (React +
  confirmed no raw HTML injection)? Any place output is used in a non-HTML sink (a URL, a
  filename, a DB query, a shell/ffmpeg arg)? The YouTube-title XSS (Issue 149) proves the class.
- **Sensitive disclosure.** Could an injection extract the system prompt, another creator's data,
  or any secret? Confirm no secrets/tokens are ever in any prompt.
- **Honesty bypass.** Can crafted input get the model to drop the disclaimer or promise virality?
- **Defenses + monitoring.** Recommend the layered mitigations (input delimiting/spotlighting,
  tool-call allow-listing + argument validation, output validation, the honesty/structural tests
  as guardrails) and what to log (without logging the injected payload as a new PII/abuse sink).

### What to produce (your deliverable)

A single Markdown research brief, no code changes:
1. **Executive summary** — the highest-risk injection/output paths, severity-tagged.
2. **The untrusted-data-flow map** — source → prompt → tool/output, with the trust boundary
   marked at each hop (`file_path:line`).
3. **Findings** — each mapped to the OWASP LLM Top 10 item, with the standard (cite + links),
   repo reality, and the fix.
4. **Proposed issues** — dependency-ordered, `docs/issues.md` house style (What / Acceptance
   criteria), severity-tagged, each flagging a needed `docs/DECISIONS.md` entry.
5. **Open questions for the human** — phrased for a one-line answer.

Lead with conclusions. Ground every claim — repo `file_path:line`, OWASP/Anthropic via links.
Flag stale or contradictory docs rather than papering over them.
