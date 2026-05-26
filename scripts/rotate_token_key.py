#!/usr/bin/env python3
"""
TOKEN_ENCRYPTION_KEY rotation script.

Re-encrypts every youtube_tokens row from old-key to new-key in a single
atomic transaction. Roll back occurs automatically on any error.

Usage:
    python3 scripts/rotate_token_key.py \\
      --old-key <current TOKEN_ENCRYPTION_KEY> \\
      --new-key <newly generated key>

Run from the project root so the project modules are importable.
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Ensure project root is on sys.path when run directly
sys.path.insert(0, str(Path(__file__).parent.parent))


async def _rotate(old_key: str, new_key: str) -> int:
    from cryptography.fernet import Fernet, InvalidToken
    from sqlalchemy import select

    from db import AsyncSessionLocal
    from models import YoutubeToken

    old_fernet = Fernet(old_key.encode())
    new_fernet = Fernet(new_key.encode())

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(YoutubeToken))
        rows = list(result.scalars())

        total = len(rows)
        print(f"Re-encrypting tokens for {total} creator(s)...")

        errors = 0
        for i, row in enumerate(rows, 1):
            try:
                access_plain = old_fernet.decrypt(row.access_token_encrypted.encode()).decode()
                row.access_token_encrypted = new_fernet.encrypt(access_plain.encode()).decode()

                if row.refresh_token_encrypted:
                    refresh_plain = old_fernet.decrypt(
                        row.refresh_token_encrypted.encode()
                    ).decode()
                    row.refresh_token_encrypted = new_fernet.encrypt(
                        refresh_plain.encode()
                    ).decode()

                print(f"  [{i}/{total}] creator={row.creator_id} ok")
            except InvalidToken as exc:
                print(f"  [{i}/{total}] creator={row.creator_id} FAILED: {exc}", file=sys.stderr)
                errors += 1

        if errors:
            await session.rollback()
            print(f"\nRolled back. {errors} error(s) — do NOT update the key.", file=sys.stderr)
            return errors

        await session.commit()
        print(f"\nDone. {total} row(s) re-encrypted, 0 errors.")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Rotate TOKEN_ENCRYPTION_KEY")
    parser.add_argument("--old-key", required=True, help="Current Fernet key (base64)")
    parser.add_argument("--new-key", required=True, help="New Fernet key (base64)")
    args = parser.parse_args()

    # Validate both keys are valid Fernet keys before touching the DB
    from cryptography.fernet import Fernet

    try:
        Fernet(args.old_key.encode())
    except Exception:
        print("ERROR: --old-key is not a valid Fernet key", file=sys.stderr)
        sys.exit(1)
    try:
        Fernet(args.new_key.encode())
    except Exception:
        print("ERROR: --new-key is not a valid Fernet key", file=sys.stderr)
        sys.exit(1)

    if args.old_key == args.new_key:
        print("ERROR: old-key and new-key are identical — nothing to do", file=sys.stderr)
        sys.exit(1)

    exit_code = asyncio.run(_rotate(args.old_key, args.new_key))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
