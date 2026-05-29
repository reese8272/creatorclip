# docs/assessment/ — production-readiness register

This directory is the **durable memory** of the production-assessment process.
Because findings live here on disk, each assessment run is a *diff against last
time*, not a from-scratch re-read — which is what keeps context flat as the
codebase grows. See `.claude/skills/production-assessment/SKILL.md` for the full
procedure, or run `/assess`.

## Files
| File | Written by | Purpose |
|------|-----------|---------|
| `baselines.json` | you / `--update-baseline` | the deterministic floor each gate must hold |
| `_machine.json` | `run_layer0.py` | latest Layer-0 tool results (gitignored — regenerated) |
| `modules/<module>.md` | Layer-1 subagents | per-module findings |
| `REPORT.md` | Layer-2 orchestrator | latest verdict + ranked register |
| `history/<date>-REPORT.md` | Layer-2 orchestrator | immutable per-run snapshots |

## The ratchet (why gates start permissive)

A hard gate set to "perfect" on day one would red-wall every PR against 16k lines
of existing code. So the gates are **regression gates**: each is seeded
permissively in `baselines.json`, you capture current reality once, and then you
tighten toward the target over time.

1. Install dev tooling: `pip install -r requirements-dev.txt`
2. Capture the current state as the floor (run from repo root, full stack up so
   coverage can measure):
   ```bash
   python3 .claude/skills/production-assessment/scripts/run_layer0.py --update-baseline
   ```
3. Commit the updated `baselines.json`. From now on CI fails any PR that drops
   coverage or adds mypy/security regressions.
4. **Tighten over time** — the targets, in priority order:
   - `bandit_high` → **0** (no high-severity security findings) — do this first.
   - `pip_audit_vulns` → **0** (patch/replace vulnerable deps).
   - `mypy_errors` → ratchet down each PR until 0, then enable
     `disallow_untyped_defs` in `pyproject.toml` (CLAUDE.md mandates typed
     signatures; this makes the mandate mechanical).
   - `coverage_line_rate` → bump after each meaningful test addition.

`coverage_line_rate` is a *floor* (must not drop); the others are *ceilings*
(must not rise).
