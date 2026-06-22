# Gap-Closure Research Initiative — COMPLETE (2026-06-22)

The gap-closure research initiative (Issues **166–180**) is **done**. Each of the 15 conceptual
gaps got a Phase-1 (CHECK) research pass — industry-standard-first, repo-grounded with
`file_path:line` citations — and produced a written brief plus concrete, dependency-ordered
implementation issues.

## Where everything lives now

| Artifact | Location |
|----------|----------|
| **Research findings (briefs)** — full acceptance criteria + evidence + draft DECISIONS entries | `docs/research/findings/01–15` *(live reference)* |
| **Filed implementation issues** (181–274, deduped + prioritized) | `docs/issues.md` |
| **Scope decisions** (stream recap, publishing, i18n, editor) | `docs/DECISIONS.md` (2026-06-22 entry) |
| **Spent research prompts** (the prompts that were run) | `docs/archive/research_prompts_2026-06-22/` |

## How to use the findings

When you start any Issue 181–274, open its `Src:` finding in `docs/research/findings/` — the
backlog entry is a condensed tracker; the finding holds the full acceptance criteria, the
`file_path:line` evidence, and the draft `docs/DECISIONS.md` entry to adapt at build time.

## Findings index

| # | Finding | Filed as |
|---|---------|----------|
| 01 | UX / product gaps (status visibility, clips map, stream recap) | 181–193, 210–215 |
| 02 | Agentic usage, prompt caching & LLM cost | 218–223 |
| 03 | Editorial capabilities | 181–189 |
| 04 | Security posture + scaling to 10k+ | 228–232, 259–264 |
| 05 | Logging, metrics, tracing, alerting | 233–241 |
| 06 | Monetization, pricing & unit economics | 205–209, 220, 228 |
| 07 | Activation, onboarding & funnel | 203–204, 214–215, 235 |
| 08 | Personalization efficacy & clip-quality eval (the moat) | 198–202, 216 |
| 09 | LLM content safety & prompt injection | 224–227 |
| 10 | Disaster recovery, backups & durability | 255–258 |
| 11 | Notifications & lifecycle comms | 242–246 |
| 12 | Data privacy & compliance | 247–254 |
| 13 | Multi-platform distribution & publishing | 182, 194–197 (+ TikTok/Reels deferred) |
| 14 | Internationalization & multilingual | **deferred** (English-only v1) |
| 15 | QA, test-suite hardening & release engineering | 265–274 |
