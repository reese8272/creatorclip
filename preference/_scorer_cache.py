"""Per-worker LRU cache of deserialized preference scorers.

Keyed by ``(creator_id, version)`` — an immutable key, since ``train.py``
assigns a new monotonically-increasing version on every retrain. A retrain
therefore produces a fresh key and the stale entry falls out by LRU eviction;
there is no manual invalidation and no risk of serving a stale model. The cache
lets ``load_latest`` skip the lock-contended joblib ``from_bytes`` on the rerank
hot path when the model is unchanged. (Issue 78a)
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict

from config import settings
from preference.model import PreferenceScorer

_CacheKey = tuple[uuid.UUID, int]

_lock = threading.Lock()
_cache: OrderedDict[_CacheKey, PreferenceScorer] = OrderedDict()


def get(key: _CacheKey) -> PreferenceScorer | None:
    """Return the cached scorer for ``key`` (marking it most-recently-used), or None."""
    with _lock:
        scorer = _cache.get(key)
        if scorer is not None:
            _cache.move_to_end(key)
        return scorer


def put(key: _CacheKey, scorer: PreferenceScorer) -> None:
    """Store ``scorer`` under ``key`` and evict the least-recently-used entries past the bound."""
    with _lock:
        _cache[key] = scorer
        _cache.move_to_end(key)
        while len(_cache) > settings.PREFERENCE_SCORER_CACHE_SIZE:
            _cache.popitem(last=False)


def clear() -> None:
    """Drop all cached scorers (used by tests for isolation)."""
    with _lock:
        _cache.clear()
