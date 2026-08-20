"""Every tenant table has an RLS policy, or a written exemption (Issue 505).

RLS coverage was 100% hand-maintained across FOUR independent copies of the
table list — ``_TENANT_TABLES`` and ``_CHILD_TABLES`` in
tests/test_rls_isolation_integration.py, migration 0010's loop, and migration
0045's rewrite — and four later migrations outgrew 0045. **Nothing in the repo
referenced ``pg_policies`` or ``pg_class.relrowsecurity``: zero hits.**

So the construction could not detect the one thing that actually matters — a
tenant table shipped with **no policy at all**. That is not hypothetical: it is
exactly how ``improvement_briefs`` and ``creator_insights`` shipped unprotected
until migration 0038 caught them, and it is how ``creator_identity`` shipped
unprotected until this issue caught it.

**This module is the STATIC half and runs in the default unit lane** — it reads
the SQLAlchemy models and the migration sources, so it catches a new unpoliced
tenant table at commit time on any machine, with no database. The runtime half
(``pg_policies`` + ``relrowsecurity`` as the live server actually has them) lives
in tests/test_rls_isolation_integration.py and runs in CI only.

Both are needed and neither replaces the other: the static half cannot see a
policy that failed to apply, and the runtime half cannot run without Postgres.
"""

from __future__ import annotations

import ast
import re
from functools import lru_cache
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_VERSIONS = _REPO_ROOT / "alembic" / "versions"

# Tables carrying a creator_id column that deliberately have NO RLS policy.
# Each entry is a decision with a reason, checked in both directions below.
#
# The bar for landing here rather than getting a policy: a policy keyed on
# `app.creator_id` must be either IMPOSSIBLE (the GUC is not set yet) or
# STRUCTURALLY REDUNDANT (the access pattern cannot express a cross-tenant read).
# "We always filter by creator_id" is NOT sufficient — that is true of every
# policied table too, and RLS exists precisely for the query that forgets.
_RLS_EXEMPT: dict[str, str] = {
    "creators": (
        "Pre-auth bootstrap. This is the table that ESTABLISHES the tenant, so a "
        "policy keyed on app.creator_id could never be satisfied during sign-in — "
        "the GUC is not set until after this row is read. Documented in migration "
        "0010's docstring."
    ),
    "audit_log": (
        "Shared operational/security trail spanning all tenants by design; "
        "operators query it directly and no creator-facing route reads it. "
        "Documented in migration 0010's docstring."
    ),
    "creator_api_keys": (
        "Pre-auth bootstrap, same shape as `creators` (Issue 505 — the reason was "
        "correct but had never been written down anywhere). api_key.py looks the row "
        "up by `key_hash` BEFORE any creator identity exists; the row is what "
        "ESTABLISHES creator_id and then sets session.info['creator_id']. A policy "
        "keyed on app.creator_id would make bearer-token authentication impossible, "
        "not safer. The lookup is by SHA-256 hash of a secret the caller must already "
        "hold, so it is not an enumeration surface."
    ),
    "notification_preferences": (
        "``creator_id`` IS the primary key — exactly one row per creator — so a "
        "read or write cannot address another tenant's row without already knowing "
        "its creator_id, and the single-row access pattern has no unfiltered-SELECT "
        "shape for RLS to catch. Recorded in the NotificationPreference docstring "
        "(Issue 243); this entry makes that prose machine-checked."
    ),
    "notification_deliveries": (
        "Operational send-log for the notification pipeline, written by the worker "
        "and read by operators; no creator-facing route selects from it. Carries "
        "creator_id for joins and cascade-on-erasure, not for tenant reads. "
        "Documented alongside notification_preferences (Issue 243)."
    ),
    "event_logs": (
        "Append-only behavioural telemetry, not tenant business data. Written only "
        "via event_log.record_event(), which redacts PII/tokens at the boundary; "
        "creators read only their own rows via /api/logs/me and operators query the "
        "table directly. Recorded in the EventLog docstring and DECISIONS.md "
        "(2026-06-17, Issue 151) — this entry makes that prose machine-checked."
    ),
}


@lru_cache(maxsize=1)
def tenant_tables() -> frozenset[str]:
    """Every mapped table carrying a ``creator_id`` column."""
    import models

    out: set[str] = set()
    for mapper in models.Base.registry.mappers:
        table = getattr(mapper.class_, "__table__", None)
        if table is not None and "creator_id" in table.columns:
            out.add(table.name)
    return frozenset(out)


@lru_cache(maxsize=1)
def policied_tables() -> frozenset[str]:
    """Tables named by a ``CREATE POLICY ... ON <table>`` in any migration.

    Parsed from the SQL text rather than from a list, including the f-string
    spellings (`CREATE POLICY tenant_isolation ON {_TABLE}`) that later
    migrations use — those are resolved through the module's own constants.
    """
    found: set[str] = set()
    for path in sorted(_VERSIONS.glob("*.py")):
        src = path.read_text()
        if "CREATE POLICY" not in src:
            continue
        consts = _module_constants(src)
        for raw in re.findall(r"CREATE POLICY\s+\w+\s+ON\s+([\w{}.]+)", src):
            name = raw.strip()
            if name.startswith("{") and name.endswith("}"):
                resolved = consts.get(name[1:-1])
                if resolved:
                    found.add(resolved)
                continue
            found.add(name)
        # Migrations that loop a tuple of table names (0010, 0038, 0040, 0045).
        found |= _looped_table_names(src)
    return frozenset(found)


def _module_constants(src: str) -> dict[str, str]:
    """Module-level ``NAME = "literal"`` assignments."""
    out: dict[str, str] = {}
    try:
        tree = ast.parse(src)
    except SyntaxError:  # pragma: no cover - a broken migration fails elsewhere
        return out
    for node in tree.body:
        # Both `X = "..."` and the annotated `X: str = "..."` spelling.
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if (
            isinstance(target, ast.Name)
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
        ):
            out[target.id] = value.value
    return out


def _looped_table_names(src: str) -> set[str]:
    """Table names from module-level tuple/list literals in an RLS migration.

    0010 and 0045 apply their policy by iterating a tuple of names, so the
    CREATE POLICY regex above sees only the loop variable.
    """
    out: set[str] = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:  # pragma: no cover
        return out
    for node in tree.body:
        # 0040 declares `_CHILD_TABLES: list[tuple[str, str, str]] = [...]` — an
        # AnnAssign, not an Assign. Skipping annotated targets silently lost all
        # five chat child-table policies, which then read as unprotected.
        if isinstance(node, ast.Assign) or (
            isinstance(node, ast.AnnAssign) and node.value is not None
        ):
            value = node.value
        else:
            continue
        if not isinstance(value, ast.Tuple | ast.List):
            continue
        for elt in value.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                out.add(elt.value)
            elif isinstance(elt, ast.Tuple | ast.List) and elt.elts:
                first = elt.elts[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    out.add(first.value)
    # NOT `| consts.values()`: that would sweep every module string constant,
    # including the _GUC_HARDENED SQL fragment, into the "policied" set. The
    # `{_TABLE}` spelling is resolved by the placeholder branch in the caller.
    return out


# ── The gate ─────────────────────────────────────────────────────────────────


def test_the_model_sweep_finds_the_tenant_tables() -> None:
    """Anti-vacuity: an empty sweep would make the coverage check pass trivially."""
    tables = tenant_tables()
    assert len(tables) >= 29, f"only {len(tables)} tenant table(s) discovered: {sorted(tables)}"
    for known in ("clips", "videos", "youtube_tokens", "preference_models"):
        assert known in tables, f"{known} missing — the model sweep is broken"


def test_the_migration_sweep_finds_the_policies() -> None:
    """Anti-vacuity for the other half: a parser matching nothing exempts everything."""
    policied = policied_tables()
    assert len(policied) >= 20, f"only {len(policied)} policied table(s) parsed"
    for known in ("clips", "improvement_briefs", "creator_insights", "creator_identity"):
        assert known in policied, f"{known} has a policy in a migration but was not parsed"


def test_every_tenant_table_has_a_policy_or_a_written_exemption() -> None:
    """The check that did not exist: a tenant table with NO policy at all.

    Four copies of the table list existed and none of them could answer this,
    because every one of them was a list of tables someone had REMEMBERED.
    """
    unprotected = sorted(tenant_tables() - policied_tables() - set(_RLS_EXEMPT))
    assert not unprotected, (
        f"table(s) with a creator_id column, no RLS policy, and no recorded "
        f"exemption: {unprotected}. Add a policy in a migration, or add an entry to "
        "_RLS_EXEMPT explaining why one is impossible or structurally redundant. "
        "'We always filter by creator_id' is not a reason — that is true of every "
        "policied table too, and RLS is for the query that forgets."
    )


def test_no_stale_rls_exemption() -> None:
    """The reverse arm: an exemption for a table that no longer needs one.

    A table that gained a policy, or lost its creator_id column, or was dropped,
    leaves an entry that reads as a deliberate decision while describing nothing —
    and silently pre-approves any future table that reuses the name.
    """
    import models

    all_tables = {
        t.name
        for m in models.Base.registry.mappers
        if (t := getattr(m.class_, "__table__", None)) is not None
    }
    policied = policied_tables()

    # NOT keyed on tenant_tables(): `creators` and `audit_log` have no creator_id
    # column at all, and their entries are the two ORIGINAL documented exemptions.
    # Dropping them because they fall outside the creator_id population would
    # delete the very reasoning this registry exists to preserve.
    gone = sorted(name for name in _RLS_EXEMPT if name not in all_tables)
    assert not gone, (
        f"RLS exemption(s) naming a table that no longer exists: {gone}. Remove the "
        "entry — it pre-approves any future table that reuses the name."
    )

    contradicted = sorted(name for name in _RLS_EXEMPT if name in policied)
    assert not contradicted, (
        f"table(s) both exempted AND policied: {contradicted}. The table gained a "
        "policy, so the exemption is now false — remove it."
    )


def test_every_exemption_carries_a_reason() -> None:
    """Membership is not a justification."""
    for table, reason in _RLS_EXEMPT.items():
        assert len(reason.strip()) >= 80, (
            f"{table}'s exemption reason is too thin to be a decision: {reason!r}"
        )


def test_behavioural_sweep_covers_every_policied_tenant_table() -> None:
    """Reconcile the integration lane's hand-written tuple against the schema.

    The behavioural sweep cannot simply BE derived: it seeds a row per table, and
    seeding is per-table fixture code. A table auto-added to the loop with no seed
    would make the clean-deny assertion pass vacuously — the file's own comment
    warns about exactly that. So the tuple stays hand-written and this reconciles
    it, naming anything policied but never behaviourally exercised.
    """
    from tests.test_rls_isolation_integration import _CHILD_TABLES, _TENANT_TABLES

    swept = set(_TENANT_TABLES) | {table for table, _fk in _CHILD_TABLES}

    # A name in the tuple that no migration polices: the literal has drifted.
    ghosts = sorted(swept - policied_tables())
    assert not ghosts, (
        f"the behavioural sweep iterates table(s) with no policy in any migration: "
        f"{ghosts} — either the policy was dropped or the tuple names a ghost"
    )

    # Policied direct-tenant tables the behavioural sweep never touches. These are
    # covered by schema checks only; each is listed so the gap is visible rather
    # than merely absent.
    schema_only = sorted((tenant_tables() & policied_tables()) - swept)
    assert schema_only == _SCHEMA_ONLY_TABLES, (
        "the set of policied tenant tables NOT behaviourally swept has changed.\n"
        f"  now:      {schema_only}\n"
        f"  expected: {_SCHEMA_ONLY_TABLES}\n"
        "If you added a table, either seed it in _seed_all_tenant_rows and add it "
        "to _TENANT_TABLES, or add it here to acknowledge it is schema-checked only."
    )


# Policied tenant tables verified by schema, not by a live cross-tenant read.
#
# MEASURED 2026-08-20, not guessed: 8 policied tenant tables never enter any loop
# in the behavioural sweep — which corroborates the "8 of 31" figure Issue 505 was
# filed on, arrived at independently. `chat_conversations`, `clip_publications`
# and `data_exports` are the three the filing singled out as having no guard
# anywhere.
#
# Not a clean bill of health — a RATCHET. Every name here is a table whose policy
# TEXT is checked but whose actual isolation BEHAVIOUR has never been exercised
# against a second tenant. Shrink this list by seeding the table in
# _seed_all_tenant_rows and adding it to _TENANT_TABLES; do not grow it without a
# reason. Growing it silently is how "8 of 31" happened.
_SCHEMA_ONLY_TABLES = [
    "chat_conversations",
    "clip_impressions",
    "clip_publications",
    "creator_identity",
    "creator_style",
    "data_exports",
    "notifications",
    "summaries",
]
