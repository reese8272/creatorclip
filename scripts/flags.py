#!/usr/bin/env python3
"""Ops CLI for the runtime feature flags / kill switches (Issue 284).

Usage (from the project root, inside the app container or a venv with .env):
    python3 scripts/flags.py list
    python3 scripts/flags.py disable llm_generation --reason "Anthropic outage"
    python3 scripts/flags.py enable llm_generation --reason "outage resolved"

Every flip goes through flags.set_flag(), so it upserts the feature_flags row
AND emits an audited ``flag_flipped`` event (actor = --by, default the shell
user). Running processes converge within one TTL window (~30 s) — no deploy,
no restart.
"""

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

# Ensure project root is on sys.path when run directly (mirrors
# scripts/rotate_token_key.py) — and ahead of scripts/, so `import flags`
# resolves to the root reader module, not this script.
sys.path.insert(0, str(Path(__file__).parent.parent))


async def _list_flags() -> int:
    from sqlalchemy import select

    import flags as flags_mod
    from db import AsyncSessionLocal
    from models import FeatureFlag

    async with AsyncSessionLocal() as session:
        rows = {r.key: r for r in (await session.execute(select(FeatureFlag))).scalars()}

    print(f"{'flag':<20} {'state':<6} {'source':<12} details")
    for key in sorted(set(flags_mod.KNOWN_FLAGS) | set(rows)):
        row = rows.get(key)
        if row is not None:
            state = "ON" if row.enabled else "OFF"
            details = (
                f"by {row.updated_by} at {row.updated_at:%Y-%m-%d %H:%M}Z"
                f" — {row.reason or 'no reason given'}"
            )
            print(f"{key:<20} {state:<6} {'db':<12} {details}")
        else:
            state = "ON" if flags_mod.env_default(key) else "OFF"
            print(f"{key:<20} {state:<6} {'env-default':<12}")
    return 0


async def _set_flag(key: str, enabled: bool, updated_by: str, reason: str | None) -> int:
    import flags as flags_mod
    from db import AsyncSessionLocal

    if key not in flags_mod.KNOWN_FLAGS:
        print(
            f"warning: {key!r} is not a known kill switch "
            f"({', '.join(sorted(flags_mod.KNOWN_FLAGS))}) — writing the row anyway"
        )
    async with AsyncSessionLocal() as session:
        row = await flags_mod.set_flag(
            key, enabled, updated_by=updated_by, reason=reason, session=session
        )
    print(
        f"{row.key} → {'ON' if row.enabled else 'OFF'} (by {row.updated_by})"
        f" — live everywhere within ~{int(flags_mod.FLAG_TTL_S)}s"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="List / flip runtime feature flags (Issue 284)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="show every flag with its effective state and source")

    for cmd, help_text in (
        ("enable", "turn a flag ON (subsystem resumes)"),
        ("disable", "turn a flag OFF (kill switch — subsystem returns 503/fails cleanly)"),
    ):
        p = sub.add_parser(cmd, help=help_text)
        p.add_argument("key", help="flag key, e.g. llm_generation")
        p.add_argument("--reason", default=None, help="why this flip is happening (audited)")
        p.add_argument("--by", default=getpass.getuser(), help="operator identity (audited)")

    args = parser.parse_args()
    if args.command == "list":
        return asyncio.run(_list_flags())
    return asyncio.run(_set_flag(args.key, args.command == "enable", args.by, args.reason))


if __name__ == "__main__":
    raise SystemExit(main())
