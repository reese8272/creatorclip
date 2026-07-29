"""Process-wide async-resource close registry (Issue 109b).

Long-lived async clients (shared httpx client, Redis pools, engine pools)
register their close coroutine here at module init. App shutdown then calls
``close_all()`` once instead of the lifespan reaching into module privates.
This registry is for IMPORT-TIME singletons only; lifespan-scoped resources
(e.g. the /health Redis client) are acquired before ``yield`` and released
after it inside the lifespan itself, because re-registration's
replace-and-never-clear semantics would leak earlier lifespans' instances.

Semantics:
- ``close_all()`` closes in REVERSE registration order (last-created first).
- Each close is error-isolated: one failing resource never blocks the rest,
  and shutdown itself never raises.
- Re-registering a name replaces the previous callable and moves it to the
  end of the order (the newest instance is the one that must close first).
- The registry is NOT cleared by ``close_all()``: import-time registrations
  must survive repeated lifespans (TestClient runs startup/shutdown many
  times per process), and the registered closes are idempotent.
"""

import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

_REGISTRY: list[tuple[str, Callable[[], Awaitable[None]]]] = []


def register_aclose(name: str, aclose: Callable[[], Awaitable[None]]) -> None:
    """Register (or replace) a named resource's zero-arg async close callable."""
    global _REGISTRY
    _REGISTRY = [(n, fn) for n, fn in _REGISTRY if n != name]
    _REGISTRY.append((name, aclose))


async def close_all() -> None:
    """Close every registered resource in reverse registration order.

    Error-isolated per resource: a failure is logged and the remaining
    resources still close.
    """
    for name, aclose in reversed(list(_REGISTRY)):
        try:
            await aclose()
        except Exception as exc:  # noqa: BLE001 — shutdown must never raise
            logger.warning("close_all: closing %r failed: %s", name, exc)
