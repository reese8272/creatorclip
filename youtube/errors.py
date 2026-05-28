"""
Typed exceptions raised by the YouTube API client modules.

YouTubeAuthError signals that the creator's OAuth grant is no longer valid
(401, or 403 with reasons like authError / forbidden / accountClosed). Callers
should delete the YoutubeToken row and stop calling Google for that creator.
Distinguishing this from transient 403s (quotaExceeded, rateLimitExceeded)
prevents indefinite backoff loops against revoked grants.
"""


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
