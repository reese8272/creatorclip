#!/usr/bin/env python3
"""Re-apply right-to-erasure after a backup restore (Issue 254).

A restored dump is older than the live DB it replaces, so it can resurrect
creators who were erased AFTER the dump was taken. This script replays the
append-only audit trail: for every ``creator.deleted`` audit row whose
creator_id still has a surviving ``creators`` row, it re-runs the SAME erasure
cascade as ``DELETE /auth/me`` (``routers.auth.erase_creator``: OAuth revoke,
R2 media purge, telemetry purge, audit append, DB cascade delete).

Idempotent by construction: ids with no surviving creator row are skipped, so
re-running the script is always safe. MANDATORY after any restore — source
erasures from the NEWEST audit trail available (live or newest dump), not the
restored (older) one. See docs/RUNBOOKS.md (Disaster Recovery b/d).

Security posture (mirrors scripts/backup_pg.sh): no secrets in argv or logs —
the DB DSN comes from project settings/env, and only pseudonymous creator
UUIDs and summary counts are ever printed.

Usage (from the project root, with the project .env in the environment):
    python3 scripts/reapply_erasures.py
"""

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger("reapply_erasures")


async def reapply(
    session_factory: Callable[[], Any],
    erase: Callable[[Any, Any], Awaitable[None]],
) -> tuple[int, int]:
    """Replay ``creator.deleted`` audit rows; return (reapplied, skipped)."""
    from sqlalchemy import select

    from models import AuditLog, Creator

    async with session_factory() as session:
        deleted_ids = (
            (
                await session.execute(
                    select(AuditLog.entity_id)
                    .where(AuditLog.action == "creator.deleted")
                    .distinct()
                )
            )
            .scalars()
            .all()
        )

    reapplied = skipped = 0
    for creator_id in deleted_ids:
        if creator_id is None:
            continue
        # One session per creator, stamped for the per-creator RLS GUC — the
        # same isolation posture erase_creator itself enforces before deletes.
        async with session_factory() as session:
            session.info["creator_id"] = creator_id
            creator = (
                await session.execute(select(Creator).where(Creator.id == creator_id))
            ).scalar_one_or_none()
            if creator is None:
                skipped += 1  # already erased — idempotent no-op
                continue
            logger.info("Re-applying erasure for resurrected creator %s", creator_id)
            await erase(session, creator)
            reapplied += 1
    return reapplied, skipped


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
    from db import AsyncSessionLocal
    from routers.auth import erase_creator

    reapplied, skipped = asyncio.run(reapply(AsyncSessionLocal, erase_creator))
    logger.info(
        "reapply_erasures done: %d erasure(s) re-applied, %d already absent (skipped)",
        reapplied,
        skipped,
    )


if __name__ == "__main__":
    main()
