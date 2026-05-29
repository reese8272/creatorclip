# Skill & Standards Freshness

A stale standards skill is worse than none — it gives confident, wrong answers.
This doc is the convention that keeps CreatorClip's skills and embedded standards
either evergreen or provably current.

## The principle

> **A skill encodes how to find the answer, not the answer.** Bake durable
> judgment; never bake perishable facts as the source of truth.

Sort every piece of skill/standards content by half-life and store it accordingly:

| Half-life | Examples | Where it belongs |
|---|---|---|
| Evergreen (years) | DRY/SOLID/KISS, "don't block the event loop", the assessment rubric, the *questions to ask* | Bake into the skill |
| Slow-drift (months–years) | "PgBouncer for transaction pooling", the pool-math formula | Bake **but** date-stamp + review on cadence |
| Perishable (weeks–months) | tool/lib versions, model ids, API/tool surfaces, CVEs, "best library for X", pricing | **Never** bake — fetch live, or isolate behind a single dated source |

## Mechanisms in this repo

1. **Fetch, don't store, where possible.** `pip-audit` pulls the *current* CVE
   list every run — it can't go stale. Phase-1 `web_search` (the One Rule)
   researches library/pattern choices live. Prefer this for anything perishable.
2. **Single source of truth for the perishable facts that must be stored.**
   - Anthropic model id + web_search tool version → `config.py`
     (`ANTHROPIC_MODEL`, `ANTHROPIC_WEB_SEARCH_TOOL`). Not hardcoded at call sites.
   - Tool/lib versions → `requirements.txt` / `requirements-dev.txt`.
3. **Date-stamp every skill.** Each `SKILL.md` carries `last_verified: YYYY-MM-DD`
   in its frontmatter. The skill itself instructs: *if `last_verified` is >90 days
   old, re-research the perishable section before trusting it.*
4. **Staleness is a CI signal.** `run_layer0.py` has a `freshness` gate that reads
   every skill's `last_verified` and reports the oldest. It warns by default and
   fails under `--require-fresh` (used by the scheduled re-verification job).

## The refresh ritual (quarterly, or when the gate flags stale)

1. Run `python3 .claude/skills/production-assessment/scripts/run_layer0.py --gates freshness --require-fresh`
   to see which skills are stale.
2. For each stale skill, re-verify its perishable section:
   - Model ids / SDK surfaces → the `/claude-api` skill + Anthropic's live catalog.
   - Library/pattern choices → `deep-research` / `web_search`, update `DECISIONS.md`.
   - Dependency pins → `pip-audit` + a Renovate/Dependabot-style bump PR.
3. Update the single source (config / requirements), not the skill prose.
4. Bump `last_verified` in the skill's frontmatter and commit.

Optionally automate step 1 with the `/loop` skill on a monthly interval.

## Owned vs managed skills

- **Owned, committed, process-first:** `best-practices`, `production-assessment`.
  Keep these evergreen; refresh on the ritual above.
- **Vendor-managed:** `/claude-api` (Anthropic-maintained). The Claude API surface
  moves fastest — lean on the managed skill that updates upstream rather than
  cloning its facts here.
