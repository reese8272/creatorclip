"""
Unit tests for the per-(creator, version) preference-scorer cache (Issue 78a).

DB-free: a fake async session mimics the two-step query shape of
`load_latest` (cheap version+schema lookup, then a blob fetch only on a cache
miss). These run in the default suite.
"""

import uuid
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from preference import _scorer_cache as scorer_cache
from preference.features import FEATURE_NAMES
from preference.model import PreferenceScorer, fit
from preference.train import load_latest


@pytest.fixture(autouse=True)
def _clear_cache():
    """Each test starts with an empty per-worker cache."""
    scorer_cache.clear()
    yield
    scorer_cache.clear()


def _training_arrays(n_pos: int = 5, n_neg: int = 5):
    rng = np.random.default_rng(42)
    X = rng.random((n_pos + n_neg, len(FEATURE_NAMES)))
    y = np.array([1] * n_pos + [0] * n_neg)
    w = np.ones(n_pos + n_neg)
    return X, y, w


def _real_blob() -> bytes:
    """A genuine serialized scorer so `from_bytes` succeeds in the miss path."""
    X, y, w = _training_arrays()
    return fit(X, y, w, threshold=20).to_bytes()


class _FakeSession:
    """Mimics AsyncSession for load_latest's two queries.

    `execute(...).first()` → (version, feature_schema) | None
    `scalar(...)`          → weights_blob bytes
    """

    def __init__(self, version: int | None, feature_schema: dict | None, blob: bytes | None):
        self._version = version
        self._feature_schema = feature_schema
        self._blob = blob
        self.scalar_calls = 0

    async def execute(self, _stmt):
        result = MagicMock()
        result.first.return_value = (
            None if self._version is None else (self._version, self._feature_schema)
        )
        return result

    async def scalar(self, _stmt):
        self.scalar_calls += 1
        return self._blob


@pytest.mark.asyncio
async def test_same_version_deserializes_once():
    """Two reranks at the same model version → joblib load runs exactly once."""
    creator_id = uuid.uuid4()
    session = _FakeSession(version=1, feature_schema={"features": FEATURE_NAMES}, blob=_real_blob())
    real = PreferenceScorer.from_bytes

    with patch.object(PreferenceScorer, "from_bytes", side_effect=lambda b: real(b)) as spy:
        first = await load_latest(session, creator_id)
        second = await load_latest(session, creator_id)

    assert first is not None
    assert second is first  # cache returns the same object
    assert spy.call_count == 1  # deserialized once, not per rerank
    assert session.scalar_calls == 1  # blob fetched only on the miss


@pytest.mark.asyncio
async def test_new_version_busts_cache():
    """A retrain (new version) is a fresh key → reload, never a stale model."""
    creator_id = uuid.uuid4()
    schema = {"features": FEATURE_NAMES}
    real = PreferenceScorer.from_bytes

    with patch.object(PreferenceScorer, "from_bytes", side_effect=lambda b: real(b)) as spy:
        v1 = await load_latest(_FakeSession(1, schema, _real_blob()), creator_id)
        v2 = await load_latest(_FakeSession(2, schema, _real_blob()), creator_id)

    assert v1 is not None and v2 is not None
    assert v2 is not v1
    assert spy.call_count == 2  # each version deserialized once


@pytest.mark.asyncio
async def test_feature_drift_returns_none_without_touching_cache():
    """A schema mismatch falls back to DNA (None) before any blob fetch/deserialize."""
    creator_id = uuid.uuid4()
    session = _FakeSession(version=1, feature_schema={"features": ["stale"]}, blob=_real_blob())

    with patch.object(PreferenceScorer, "from_bytes") as spy:
        result = await load_latest(session, creator_id)

    assert result is None
    spy.assert_not_called()
    assert session.scalar_calls == 0


@pytest.mark.asyncio
async def test_no_model_returns_none():
    """No trained model for the creator → None, no blob fetch."""
    session = _FakeSession(version=None, feature_schema=None, blob=None)
    result = await load_latest(session, uuid.uuid4())
    assert result is None
    assert session.scalar_calls == 0


def test_lru_eviction_bound(monkeypatch):
    """The cache never grows past PREFERENCE_SCORER_CACHE_SIZE; LRU entry is evicted."""
    from config import settings

    monkeypatch.setattr(settings, "PREFERENCE_SCORER_CACHE_SIZE", 2)
    X, y, w = _training_arrays()
    scorer = fit(X, y, w, threshold=20)
    creator = uuid.uuid4()

    scorer_cache.put((creator, 1), scorer)
    scorer_cache.put((creator, 2), scorer)
    # Touch v1 so v2 becomes least-recently-used, then overflow.
    assert scorer_cache.get((creator, 1)) is scorer
    scorer_cache.put((creator, 3), scorer)

    assert scorer_cache.get((creator, 2)) is None  # evicted (LRU)
    assert scorer_cache.get((creator, 1)) is scorer
    assert scorer_cache.get((creator, 3)) is scorer
