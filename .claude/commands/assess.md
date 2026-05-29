---
description: Run the full production-readiness assessment (Layer 0 gates + per-module subagents + scale verdict)
---

Run the **production-assessment** skill end to end.

1. Read `.claude/skills/production-assessment/SKILL.md` and follow it exactly.
2. Run Layer 0 (`scripts/run_layer0.py`), then dispatch the Layer-1 subagents in
   parallel (one per module, each writing to `docs/assessment/modules/`), then
   produce the Layer-2 verdict in `docs/assessment/REPORT.md` and snapshot it to
   `docs/assessment/history/`.
3. End by showing me the VERDICT line, the ranked register top 10, and the diff
   vs the previous report.

If `$ARGUMENTS` names a single module (e.g. `worker`), assess only that module:
run Layer 0, dispatch just that one subagent, and update its findings file —
skip the full verdict regeneration.
