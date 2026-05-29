# REPORT.md template

The orchestrator writes `docs/assessment/REPORT.md` to this shape, then copies it
to `docs/assessment/history/<YYYY-MM-DD>-REPORT.md`.

```markdown
# CreatorClip — Production Assessment

**Date:** <YYYY-MM-DD>  ·  **Commit:** <sha>  ·  **LOC:** <n>  ·  **Tests:** <passed/skipped>

## VERDICT: PRODUCTION-READY — YES | CONDITIONAL | NO
<2–3 sentences. If CONDITIONAL/NO, name the gating items explicitly.>

---

## Layer 0 — deterministic gates (from _machine.json)
| Gate | Result | Baseline | Status |
|---|---|---|---|
| ruff | <n> issues | 0 | ✅/❌ |
| mypy | <n> errors | <baseline> | ✅/⚠️/❌ |
| coverage | <pct>% | <baseline>% | ✅/❌ |
| bandit | high <n> / med <n> | high 0 | ✅/❌ |
| pip-audit | <n> vulns | 0 | ✅/❌ |

Top untested load-bearing code (from coverage gaps):
1. <file> — <pct>% (<why it matters>)

## Layer 1 — module register (ranked)
| Sev | Module | Location | Issue | Backed fix |
|---|---|---|---|---|
| BLOCKER | ... | file:line | ... | ... |
| SEV1 | ... | ... | ... | ... |

Module verdicts: <module: clean/NEEDS-WORK/BLOCKER, ...>

## Layer 2 — scale checklist (scale-checklist.md)
| Axis | Status | Evidence |
|---|---|---|
| A Pool math | ✅/⚠️/❌ | <number / load result / needs evidence> |
| B Async loop hygiene | ... | ... |
| C Celery idempotency | ... | ... |
| D Tenant isolation | ... | ... |
| E Backpressure | ... | ... |
| F Rate limit/quota | ... | ... |
| G Observability | ... | ... |
| H Migration/pgvector safety | ... | ... |
| I Secrets/deletion | ... | ... |

## Diff vs previous report (<prev date>)
- Fixed: ...
- New: ...
- Regressed: ...

## Top 5 actions, in order
1. <highest-leverage fix with owner-ready detail>
```
