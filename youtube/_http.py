"""Shared httpx client for all YouTube/Google calls (Issue 72).

A lazy, per-process singleton AsyncClient with a configured timeout. Lazy (not
created at import) so its connection pool binds to the event loop that first uses
it — important because the worker installs its own loop after fork (Issue 39), and
the API uses the app loop. Reusing one client gives connection pooling instead of a
TLS handshake per call, and the timeout prevents a stalled Google endpoint from
hanging a request/worker indefinitely.
"""

import httpx

# connect=5s, read/write/pool=15s — Google APIs are normally sub-second; this caps
# the tail without cutting off legitimately slow responses.
_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_CLIENT: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    """Return the process-wide AsyncClient, creating it on first use."""
    global _CLIENT
    if _CLIENT is None or _CLIENT.is_closed:
        _CLIENT = httpx.AsyncClient(timeout=_TIMEOUT)
    return _CLIENT


async def aclose() -> None:
    """Close the shared client. Call on app/worker shutdown."""
    global _CLIENT
    if _CLIENT is not None and not _CLIENT.is_closed:
        await _CLIENT.aclose()
    _CLIENT = None
