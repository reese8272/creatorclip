"""Shared redaction helpers for PII / secret scrubbing (Issue 233).

Extracted from event_log.py so both the DB sink (_redact) and the formatter-level
backstop (scrub_dict in JsonLogFormatter) share the same blocklist without
duplication. Dependency-free: no third-party imports, no circular risk.

OWASP Logging Cheat Sheet recommends masking PII at the formatter/middleware level
(structural backstop) in addition to call-site discipline. This module provides the
single source of truth for that policy.
"""

from __future__ import annotations

from typing import Any

# Substrings that mark a dict key as sensitive (matched case-insensitively).
# Conservative + broad: better to drop a benign field than leak a token.
_REDACT_SUBSTRINGS: tuple[str, ...] = (
    "email",
    "token",
    "secret",
    "password",
    "passwd",
    "authorization",
    "cookie",
    "session",
    "jwt",
    "bearer",
    "api_key",
    "apikey",
    "raw_key",
    "refresh",
    "access_key",
    "credential",
)

_REDACTED = "[redacted]"


def is_sensitive(key: str) -> bool:
    """Return True when *key* looks like it could carry a secret or PII."""
    k = key.lower()
    return any(s in k for s in _REDACT_SUBSTRINGS)


def scrub_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *data* with sensitive values replaced by '[redacted]'.

    Intended as the formatter-level backstop in JsonLogFormatter so that a
    careless log_event(..., email=...) never leaks to stdout or app.log even if
    the call site forgot to sanitize. Pure function — no side effects.
    """
    return {k: (_REDACTED if is_sensitive(k) else v) for k, v in data.items()}
