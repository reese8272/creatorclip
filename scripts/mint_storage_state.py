"""Mint a Playwright storageState with a signed cc_session cookie (Issue 541).

Standalone by design — imports only PyJWT, never the app — so it runs before
uvicorn boots and never drags FastAPI/settings into the harness process. The
JWT payload duplicates auth.create_session_token exactly (claims: sub, iat,
exp; HS256 over JWT_SECRET_KEY), the same pattern scripts/llm_harness.py uses.
The storageState shape mirrors frontend/e2e/prod/build-auth-from-cookie.mjs,
adjusted for local dev: domain localhost, secure false (the cookie is Secure
only when ENV=production — routers/auth.py).

Usage:
    JWT_SECRET_KEY=... .venv/bin/python scripts/mint_storage_state.py
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
from datetime import UTC, datetime, timedelta

import jwt

_ALGORITHM = "HS256"
SESSION_COOKIE = "cc_session"
# The #540 seed creator (docs/issues.md, Lane L33).
DEFAULT_CREATOR_ID = "00000000-1111-2222-3333-444444444444"
AUTH_FILE = (
    pathlib.Path(__file__).resolve().parent.parent / "frontend" / "e2e" / ".auth" / "local.json"
)
# 2 h, matching llm_harness._mint_token — comfortably outlives one audit run.
_TOKEN_TTL = timedelta(hours=2)


def mint_token(secret: str, creator_id: str) -> tuple[str, int]:
    """Mint a session JWT the same way auth.create_session_token does."""
    now = datetime.now(UTC)
    exp = now + _TOKEN_TTL
    payload = {"sub": creator_id, "iat": now, "exp": exp}
    return jwt.encode(payload, secret, algorithm=_ALGORITHM), int(exp.timestamp())


def build_storage_state(token: str, expires: int) -> dict[str, object]:
    """Playwright storageState: one host-only localhost cookie, no origins."""
    return {
        "cookies": [
            {
                "name": SESSION_COOKIE,
                "value": token,
                "domain": "localhost",
                "path": "/",
                "expires": expires,
                "httpOnly": True,
                "secure": False,
                "sameSite": "Lax",
            }
        ],
        "origins": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--creator-id", default=DEFAULT_CREATOR_ID, help="creator UUID (sub claim)")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="app origin (informational; the cookie itself is host-only on localhost)",
    )
    args = parser.parse_args()

    secret = os.environ.get("JWT_SECRET_KEY")
    if not secret:
        print("JWT_SECRET_KEY is not set — export it (same value the uvicorn under test uses).")
        return 1

    token, expires = mint_token(secret, args.creator_id)
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTH_FILE.write_text(json.dumps(build_storage_state(token, expires), indent=2) + "\n")
    print(
        f"storageState written -> {AUTH_FILE} (creator {args.creator_id}, valid to exp={expires})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
