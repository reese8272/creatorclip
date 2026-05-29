---
name: best-practices
description: >-
  CreatorClip engineering best-practices gate. Invoke in Phase 1 (CHECK) of every
  non-trivial issue — architecture, library, model, scoring math, security
  boundary, UX pattern — and whenever the user asks for "best practice", "the
  right way", a design review, or runs /best-practices. Operationalizes the One
  Rule: research the CURRENT industry standard first, never build from memory.
last_verified: 2026-05-29
---

<!-- This skill is deliberately EVERGREEN: it encodes how to find the current
     standard and the durable principles, NOT a frozen list of "current best"
     facts (which rot). Perishable specifics (model ids, tool versions, library
     choices) live in config.py / requirements*.txt and are fetched/verified
     live. See docs/SKILL_FRESHNESS.md. -->

# Best Practices

The One Rule (CLAUDE.md): on every non-trivial decision we **research the current
industry standard FIRST and justify any deviation in `docs/DECISIONS.md`.** This
skill is how that rule is executed. Its value is the *process*, not a snapshot of
2026 opinions.

## Phase-1 procedure (run this before writing code)

1. **Name the decision.** What are we choosing — a library, a pattern, a model, a
   security boundary, a scoring formula?
2. **Research it live.** Use `web_search` (and the `/claude-api` skill for any
   Anthropic SDK decision). Do not answer from memory — your training has a
   cutoff and these facts move. Look for: the current de-facto standard, the
   maintained options, and known failure modes.
3. **Check it against this project.** Does it fit the stack (FastAPI + Celery +
   pgvector + Anthropic/Voyage + R2) and the North Star (deepen the
   channel-knowledge loop)?
4. **Write the CHECK brief** in the CLAUDE.md format (Approach / Why for this
   project / Industry standard checked + source / Alternatives ruled out / Good
   to go?). Record the source + date.
5. **On approval, if it diverges** from the PRD or a prior decision, add a dated
   `docs/DECISIONS.md` entry with what / why / source / alternatives.

## Durable principles (evergreen — safe to apply from memory)

**DRY** — extract any logic used more than once; flag the second occurrence.
**SOLID** — single responsibility, open/closed, Liskov, interface segregation,
dependency inversion. **KISS** — simplest solution wins; no premature
abstraction; a >30-line function that does more than one thing probably splits.

**Production standards** (also enforced mechanically by `/assess` Layer 0):
- No hardcoded secrets; config via `pydantic-settings`; fail-fast on missing
  required.
- `logging` module only, never `print()`; no PII or token in any log line.
- Pydantic model on every request AND response; correct HTTP status codes; safe
  error messages (no stack trace / DB error to client).
- Per-creator isolation on every query; parameterized SQL only.
- Resource lifecycle: context-managed DB sessions; module-level singleton
  external clients; idempotent, retry-safe Celery tasks; temp media cleaned up.
- Type hint on every signature.
- Anthropic SDK: prompt caching (split static/volatile blocks so the cached
  prefix is actually reused), token usage logged, structured output / token
  limits — use `/claude-api`.
- No interface or response ever promises virality.

## Where the PERISHABLE facts live (never restated here)

| Fact | Single source | How it stays fresh |
|---|---|---|
| Anthropic model id, web_search tool version | `config.py` (`ANTHROPIC_MODEL`, `ANTHROPIC_WEB_SEARCH_TOOL`) | verify vs live catalog via `/claude-api` before launch |
| Tool / lib versions | `requirements.txt`, `requirements-dev.txt` | `pip-audit` (live CVEs) + scheduled bump |
| "Current best library for X" | not stored — researched per decision | Phase-1 `web_search`, recorded in DECISIONS.md |

If you find a perishable fact hardcoded in code or restated in a skill, that is a
finding: hoist it to its single source.

## Cadence

- **Every non-trivial issue:** Phase-1 procedure above.
- **Quarterly (or when `last_verified` is stale):** re-verify the perishable
  sources with `deep-research`/`web_search`, then bump `last_verified` here and in
  `production-assessment`. The `run_layer0.py` freshness gate surfaces staleness.
