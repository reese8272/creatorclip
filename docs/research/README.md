# Gap-Closure Research Prompts

This directory holds **research-agent prompts** — one per known product/engineering gap. Each
file is a ready-to-paste prompt for a read-only Claude Code research agent (Explore / Plan /
`general-purpose`). The agent researches the current industry standard first (the One Rule in
`CLAUDE.md`), grounds every finding in this repo with `file_path:line` citations, and returns a
written brief + a set of proposed implementation issues. **These agents do not write product
code** — they drive the Phase 1 (CHECK) research that precedes building.

## Why this exists

The backlog (`docs/issues.md`) closed a lot of bugs but left large *conceptual* gaps —
visibility, capability, economics, safety, resilience. Rather than guess at solutions, each gap
gets a disciplined research pass first. This is the Check → Approve → Build → Review workflow
applied to the unknowns: research (here) → file concrete issues → approve → build.

## How to run one

1. Open the prompt file for the gap.
2. Spawn a research agent and paste everything below the file's `## PROMPT` line. (Optionally
   narrow scope by trimming sections.)
3. The agent returns a brief + proposed issues; triage those into `docs/issues.md` and capture
   any approach changes in `docs/DECISIONS.md`.

## The prompts

| # | Prompt | Gap | Tracked issue |
|---|--------|-----|---------------|
| 01 | `01_ux_product_gaps.md` | Analysis-status visibility, stream→summary, per-video clip surfacing, UX/bug sweep | Issue 166 |
| 02 | `02_agentic_caching_cost.md` | Agentic loop, prompt caching, LLM cost & unit economics | Issue 167 |
| 03 | `03_editorial_capabilities.md` | Editing capabilities vs. modern editorial software | Issue 168 |
| 04 | `04_security_scalability.md` | Security posture + scaling to 10k+ creators | Issue 169 |
| 05 | `05_logging_observability.md` | Logs, metrics, traces, alerting, product telemetry | Issue 170 |
| 06 | `06_monetization_unit_economics.md` | Pricing, packaging, billing correctness, margin per video | Issue 171 |
| 07 | `07_activation_onboarding_funnel.md` | Time-to-first-clip, the data-gate, funnel instrumentation | Issue 172 |
| 08 | `08_personalization_efficacy_eval.md` | Does the DNA/preference model actually pick good clips? (the moat) | Issue 173 |
| 09 | `09_llm_content_safety_prompt_injection.md` | Prompt injection + unsafe output from creator/YouTube content | Issue 174 |
| 10 | `10_disaster_recovery_durability.md` | Backups, restore, failover, key/secret recoverability | Issue 175 |
| 11 | `11_notifications_lifecycle_comms.md` | Transactional email/push, "your clips are ready", lifecycle | Issue 176 |
| 12 | `12_data_privacy_compliance.md` | GDPR/CCPA, erasure/export completeness, retention, sub-processors | Issue 177 |
| 13 | `13_multiplatform_distribution_publishing.md` | Publish/schedule to YouTube Shorts; cross-post TikTok/Reels (scope expansion) | Issue 178 |
| 14 | `14_internationalization_multilingual.md` | Multilingual content handling + product i18n | Issue 179 |
| 15 | `15_qa_eval_release_engineering.md` | Test-suite reliability, visual-regression, CI, safe deploy/rollback | Issue 180 |

## Cross-references between prompts

Several gaps overlap; the prompts name each other so the agents don't duplicate or contradict:

- **Cost** is split: `02` (LLM token cost) feeds `06` (pricing/margin).
- **Telemetry** is split: `05` (system observability) vs. `07` (product/funnel analytics) vs.
  `11` (notifications) — all reuse the same event/SSE infrastructure.
- **Eval** is split: `08` owns clip-quality/model eval; `15` owns CI reliability + how the eval
  gates `clip_engine/` changes.
- **Security** is split: `04` (broad posture + scale) vs. `09` (LLM-specific injection/output).
- **Compliance** is split: `docs/COMPLIANCE.md`/`04` (YouTube ToS) vs. `12` (privacy law).
- **Scope expansions** (`13` publishing, `01` stream-summary, parts of `03`) each require a
  `docs/DECISIONS.md` entry before building, since they move the PRD's v1 boundary.
