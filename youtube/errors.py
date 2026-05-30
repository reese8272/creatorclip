"""
Typed exceptions raised by the YouTube API client modules.

YouTubeAuthError signals that the creator's OAuth grant is no longer valid
(401, or 403 with reasons like authError / forbidden / accountClosed). Callers
should delete the YoutubeToken row and stop calling Google for that creator.
Distinguishing this from transient 403s (quotaExceeded, rateLimitExceeded)
prevents indefinite backoff loops against revoked grants.
"""

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx


def retry_after_seconds(resp: httpx.Response) -> float | None:
    """Parse a Retry-After header (delta-seconds or HTTP-date) into seconds, or None.

    Google returns Retry-After on rate-limit/quota responses; honoring it avoids
    retrying before the server-stated window (RFC 9110 §10.2.3). (Issue A / Issue 76)
    """
    headers = getattr(resp, "headers", None)
    raw = ((headers.get("Retry-After") if headers is not None else None) or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return float(raw)
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return max(0.0, (dt - datetime.now(UTC)).total_seconds())


class YouTubeAuthError(Exception):
    """Raised when a YouTube API call fails for a permanent auth reason."""

    def __init__(self, reason: str, status_code: int, message: str = ""):
        self.reason = reason
        self.status_code = status_code
        super().__init__(message or f"{status_code} {reason}")


# 403 reasons that indicate a permanent auth/account problem — never retry.
PERMANENT_403_REASONS = frozenset(
    {
        "authError",
        "forbidden",
        "accountClosed",
        "accountSuspended",
        "accountDelegationForbidden",
        "channelClosed",
        "channelSuspended",
    }
)

# 403 reasons that are transient — retry with backoff.
TRANSIENT_403_REASONS = frozenset(
    {
        "quotaExceeded",
        "rateLimitExceeded",
        "userRateLimitExceeded",
    }
)
