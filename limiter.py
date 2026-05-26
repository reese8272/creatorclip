"""
Shared slowapi Limiter keyed on creator_id extracted from the session JWT.
Falls back to remote IP for unauthenticated requests.
"""

import jwt
from slowapi import Limiter
from slowapi.util import get_remote_address

from config import settings

SESSION_COOKIE = "cc_session"


def _creator_key(request) -> str:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        try:
            payload = jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=["HS256"],
                options={"verify_exp": False},
            )
            return str(payload["sub"])
        except Exception:
            pass
    return get_remote_address(request)


limiter = Limiter(
    key_func=_creator_key,
    storage_uri=settings.REDIS_URL,
)
