# CreatorClip — Documentation Index

The entry point for every session. **Read order (per `CLAUDE.md`):** SOT → PROJECT_STATE →
issues → DECISIONS → COMPLIANCE → CLIPPING_PRINCIPLES, before writing any code.

> Maintained in Issue 146. Each doc has **one** job — if a fact lives in two docs, one is a
> pointer. Superseded docs live in [`archive/`](archive/) (preserved, not deleted).

---

## Canonical — source of truth (read first, keep authoritative)

| Doc | What it answers | Update when |
|-----|-----------------|-------------|
| [PRD.md](PRD.md) | Product requirements, north star, scope | Formal scope change only |
| [SOT.md](SOT.md) | Architecture: stack, schema, file structure, API surface | Stack/schema/structure changes |
| [PROJECT_STATE.md](PROJECT_STATE.md) | What's done / in-flight / blocked + session log | Every issue close |
| [issues.md](issues.md) | Work queue + acceptance criteria (+ Phase-3 backlog) | Issue opened/closed |
| [DECISIONS.md](DECISIONS.md) | Every decision that diverged from the PRD/industry standard | Any deviation |
| [COMPLIANCE.md](COMPLIANCE.md) | YouTube ToS, data retention, privacy posture | Data classes/retention/scopes change |
| [CLIPPING_PRINCIPLES.md](CLIPPING_PRINCIPLES.md) | Named principles the clip engine cites | A new principle is cited |
| [OFF_COURSE_BUGS.md](OFF_COURSE_BUGS.md) | Incidental-defect log (triaged into issues.md) | A bug is found off-task |

## Operations — runbooks (how to run / deploy / rotate)

| Doc | What it answers |
|-----|-----------------|
| [DEPLOYMENT.md](DEPLOYMENT.md) | Dev/prod deploy, pre-deploy checklist, RLS setup, **Cloudflare health monitoring** |
| [BRANCHING.md](BRANCHING.md) | `feature → staging → main` model + the branch-protection ruleset (apply on GitHub Pro) |
| [RUNBOOKS.md](RUNBOOKS.md) | **Canonical** `TOKEN_ENCRYPTION_KEY` + `JWT_SECRET_KEY` rotation |
| [SECRETS.md](SECRETS.md) | Secrets/config registry — what each key is + where to get it |
| [ACCESS.md](ACCESS.md) | SSH / CI deploy key / Cloudflare Tunnel + **closed-beta OAuth onboarding** |
| [STAGING_ACCESS.md](STAGING_ACCESS.md) | Staging stack runbook + the `llm_harness.py` E2E driver |
| [SKILL_FRESHNESS.md](SKILL_FRESHNESS.md) | Skill/standard freshness convention + the `--require-fresh` CI gate |

## Reference

| Doc | What it answers |
|-----|-----------------|
| [COMPETITIVE_RESEARCH.md](COMPETITIVE_RESEARCH.md) | Market/pricing/UX analysis of competing AI clippers (feeds UI/UX work) |
| [assessment/](assessment/) | Production-readiness register: baselines, per-module findings, report history |

## Archive (superseded — kept for provenance, not authoritative)

[`archive/`](archive/) holds `KICKSTART.md` (pre-build planning megadoc),
`PRODUCTION_COMMANDS.md` (frozen skill dump — live skills are in `.claude/`),
`ISSUE_APPROVED_PLANS.md` (Issue 7–20 approval log), and `BETA_LAUNCH_RUNBOOK.md`
(its live OAuth-onboarding steps were merged into ACCESS.md; its deploy steps were stale).
Don't follow these for current procedure.
