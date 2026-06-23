# CreatorClip — Migration Authoring Policy

**Last updated:** 2026-06-23
**Issue:** 294

This document is the **standalone authoritative policy** for Alembic migration authoring.
Every migration merged to `main` must comply with this policy. The CI `migration-lint` job
(Squawk — see `ci.yml`) enforces the SQL-safety rules, but **deploy sequencing** (the
expand→backfill→contract rule) cannot be mechanically checked — this document is that check.

Cross-references:
- CI enforcement: `migration-lint` job in `.github/workflows/ci.yml` (Squawk)
- Rollback runbook: `docs/DEPLOYMENT.md` → "Migration Rollback Runbook"
- Deploy pipeline: `docs/DEPLOYMENT.md` → "Production Deployment"

---

## The Fundamental Rule: Expand → Backfill → Contract across SEPARATE DEPLOYS

CreatorClip runs `alembic upgrade head` inline before `up -d` in `deploy.yml`. This means
the new schema is live before the new image starts. The PREVIOUS image is still handling
requests during the rolling restart window.

**A migration is only safe if the prior image can read the new schema without errors.**

This requires decomposing breaking changes across at least two separate deploys:

| Phase | What ships | What it does |
|-------|-----------|--------------|
| **Expand** (Deploy N) | Additive-only migration | Add new column/table/index. New code reads it; old code ignores it. |
| **Backfill** (Deploy N or N+1) | Data migration or background job | Populate existing rows. |
| **Contract** (Deploy N+1 or later, only after full rollout) | Destructive migration | Drop old column, add NOT NULL, rename. Old image is gone. |

**Never ship a drop, rename, or NOT NULL constraint in the same deploy as the code change
that removes the old column reference.** The overlap window is real — even a 30-second
rolling restart has the old image serving requests against the new schema.

---

## Rules (the repo already follows these ad-hoc — now they are explicit)

### 1. CREATE INDEX — always CONCURRENTLY, always outside a transaction

```python
# ✅ Correct
def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_clips_creator_score ON clips (creator_id, score DESC)"
        )
```

```python
# ❌ Wrong — locks the table for the duration of the index build
def upgrade() -> None:
    op.create_index("ix_clips_creator_score", "clips", ["creator_id", "score"])
```

`op.get_context().autocommit_block()` is required because `CREATE INDEX CONCURRENTLY`
cannot run inside a transaction. Squawk (`ban-concurrent-index-creation-in-transaction`)
enforces this in CI.

### 2. New NOT NULL constraints — NOT VALID first, then VALIDATE

```python
# Phase 1 (Expand): add the constraint NOT VALID — does not scan existing rows.
def upgrade() -> None:
    op.execute(
        "ALTER TABLE clips ADD CONSTRAINT clips_creator_id_not_null "
        "CHECK (creator_id IS NOT NULL) NOT VALID"
    )

# Phase 2 (Contract, separate deploy): validate without a full table lock.
def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TABLE clips VALIDATE CONSTRAINT clips_creator_id_not_null"
        )
```

Squawk (`constraint-missing-not-valid`) enforces this for CHECK and FK constraints
added to existing tables.

### 3. Column renames — expand + dual-write + contract

**Never rename a column in place.** The rename is invisible to the prior image.

```
Deploy N   — add_column(new_name)  [prior image ignores it]
Deploy N   — backfill: UPDATE t SET new_name = old_name WHERE new_name IS NULL
Deploy N+1 — code reads new_name; old image is gone
Deploy N+2 — drop_column(old_name)  [new image only]
```

### 4. Backfills — bounded UPDATE loops, never one giant UPDATE

A single unbounded `UPDATE` on a large table holds a lock for minutes and replicates as
one giant transaction, bloating WAL. Use batched updates:

```python
# ✅ Correct — bounded batches
def upgrade() -> None:
    connection = op.get_bind()
    while True:
        result = connection.execute(
            text(
                "UPDATE clips SET new_col = old_col "
                "WHERE new_col IS NULL LIMIT 1000"
            )
        )
        if result.rowcount == 0:
            break
```

### 5. Additive-only in the deploy that ships new code

The expand migration and the new code ship together. The contract migration ships only
after the new code has been fully rolled out and the old image is confirmed gone.

---

## Copy-paste snippet templates

### Template A — Add nullable column (expand phase)

```python
"""Add <column> to <table> (expand — Issue NNN phase 1)."""

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.add_column(
        "<table>",
        sa.Column("<column>", sa.TYPE(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("<table>", "<column>")
```

### Template B — CREATE INDEX CONCURRENTLY

```python
"""Add index on <table>.<column> (Issue NNN)."""

from alembic import op


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_<table>_<column> ON <table> (<column>)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_<table>_<column>")
```

### Template C — Add NOT VALID constraint (expand phase)

```python
"""Add <constraint> NOT VALID (expand — Issue NNN phase 1)."""

from alembic import op


def upgrade() -> None:
    op.execute(
        "ALTER TABLE <table> ADD CONSTRAINT <constraint_name> "
        "CHECK (<condition>) NOT VALID"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE <table> DROP CONSTRAINT <constraint_name>")
```

### Template D — VALIDATE CONSTRAINT (contract phase, separate deploy)

```python
"""Validate <constraint> (contract — Issue NNN phase 2)."""

from alembic import op


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TABLE <table> VALIDATE CONSTRAINT <constraint_name>")


def downgrade() -> None:
    pass  # Validation is reversible only by dropping the constraint (Template C downgrade)
```

---

## PR checklist (add to every migration PR description)

```
## Migration checklist

- [ ] **Expand-only in this deploy?** If this migration adds a NOT NULL column, renames a
      column, or drops anything — is it decomposed across separate deploys?
- [ ] **CREATE INDEX uses CONCURRENTLY inside autocommit_block()?**
- [ ] **New constraints use NOT VALID (Expand) then VALIDATE (Contract, separate deploy)?**
- [ ] **Backfills are bounded UPDATE loops (not one giant UPDATE)?**
- [ ] **squawk lint passes** (CI `migration-lint` job — enforced automatically)?
- [ ] **lock_timeout / statement_timeout** set on migration connection (configured globally
      in `alembic/env.py` — verify env.py has not been modified to remove these)?
- [ ] **down_revision** set to the current HEAD? (Note: multiple lanes may add migrations
      against the same head — linear renumbering at merge may be required.)
```

---

## What Squawk enforces vs. what only policy enforces

| Rule | Squawk enforces? | Policy (this doc) enforces? |
|------|-----------------|----------------------------|
| `CREATE INDEX CONCURRENTLY` outside transaction | Yes | — |
| `NOT VALID` for constraints on existing tables | Yes | — |
| No `DISALLOWED UNIQUE CONSTRAINT` | Yes | — |
| Lock timeout set on connection | No (env.py config) | Yes |
| Expand→Contract across separate deploys | **No** | **Yes — this doc** |
| Bounded backfill loops | No | Yes |
| No in-place column rename | No | Yes |

Squawk is a SQL-safety gate; it cannot see whether a drop migration ships in the same
deploy as the code that removes the column reference. That is a human + policy check.

---

## Related files

| File | What it covers |
|------|---------------|
| `alembic/env.py` | `lock_timeout` + `statement_timeout` on the migration connection |
| `docs/DEPLOYMENT.md` | Rollback runbook (image rollback + `alembic downgrade` break-glass) |
| `.github/workflows/ci.yml` | `migration-lint` job (Squawk) |
| `alembic/versions/` | Migration files |
