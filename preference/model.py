"""
Preference model: LogisticRegression cold-start → LightGBM warm-start.

Below PERSONALIZATION_THRESHOLD_LABELS uses LogisticRegression (fast, stable
with sparse data). At or above uses LightGBM for better non-linear fit.

Serialisation note
------------------
`to_bytes` / `from_bytes` use joblib (sklearn's recommended serialiser) backed
by `_RestrictedUnpickler`.  joblib uses pickle internally; the restricted
unpickler closes the RCE surface by raising `pickle.UnpicklingError` for any
class that is not explicitly in the allowlist.  An attacker who writes a
crafted blob to `preference_models.weights_blob` cannot execute arbitrary code
because `find_class` will reject unknown modules before any object is built.
"""

from __future__ import annotations

import io
import logging
import pickle
from typing import Any

import joblib
import joblib.numpy_pickle as _jnp
import numpy as np

from config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Allowlist — the *complete* set of (module, name) pairs that joblib may
# emit when serialising a PreferenceScorer wrapping either a
# LogisticRegression or a LGBMClassifier.  Derived by running an Inspector
# subclass of pickle.Unpickler against real joblib dumps of each model type.
# Any class not in this set raises UnpicklingError before the object is built.
# ---------------------------------------------------------------------------
_ALLOWED_CLASSES: frozenset[tuple[str, str]] = frozenset(
    {
        # This wrapper class
        ("preference.model", "PreferenceScorer"),
        # sklearn LogisticRegression
        ("sklearn.linear_model._logistic", "LogisticRegression"),
        # LightGBM classifier + internal booster
        ("lightgbm.sklearn", "LGBMClassifier"),
        ("lightgbm.basic", "Booster"),
        # joblib numpy wrapper (emitted by NumpyPickler for every ndarray)
        ("joblib.numpy_pickle", "NumpyArrayWrapper"),
        # numpy primitives used by both models
        ("numpy", "ndarray"),
        ("numpy", "dtype"),
        ("numpy._core.multiarray", "scalar"),
        # stdlib collections used by LightGBM's internal parameter dicts
        ("collections", "defaultdict"),
        ("collections", "OrderedDict"),
    }
)


class _RestrictedUnpickler(_jnp.NumpyUnpickler):
    """NumpyUnpickler subclass that enforces the class allowlist.

    Overrides `find_class` so that any module/name pair outside
    `_ALLOWED_CLASSES` raises `pickle.UnpicklingError` immediately —
    before the class is looked up and before any `__reduce__` / `__setstate__`
    is called.  This prevents arbitrary code execution even when a crafted
    bytes blob reaches `from_bytes`.
    """

    def find_class(self, module: str, name: str) -> Any:
        """Allow only pre-approved (module, name) pairs."""
        if (module, name) not in _ALLOWED_CLASSES:
            raise pickle.UnpicklingError(f"class not allowed: {module}.{name}")
        return super().find_class(module, name)


class PreferenceScorer:
    """Wraps either a LogisticRegression or LightGBM classifier."""

    def __init__(self, model: Any, label_count: int) -> None:
        self._model = model
        self.label_count = label_count

    def predict_score(self, features: list[float]) -> float:
        """Return probability of positive label in [0, 1]."""
        x = np.array(features, dtype=float).reshape(1, -1)
        try:
            proba = self._model.predict_proba(x)
            return float(proba[0][1])
        except Exception as exc:
            logger.warning("predict_score failed: %s", exc)
            return 0.5

    def to_bytes(self) -> bytes:
        """Serialise scorer to bytes using joblib (sklearn's recommended format)."""
        buf = io.BytesIO()
        joblib.dump(self, buf)
        return buf.getvalue()

    @classmethod
    def from_bytes(cls, data: bytes) -> PreferenceScorer:
        """Deserialise scorer, enforcing the class allowlist.

        Temporarily replaces `joblib.numpy_pickle.NumpyUnpickler` with
        `_RestrictedUnpickler` for the duration of the load, then restores
        the original.  This ensures all internal joblib code paths (including
        `_unpickle`) use the restricted class.

        Raises:
            pickle.UnpicklingError: if the blob contains a disallowed class.
        """
        _original = _jnp.NumpyUnpickler
        _jnp.NumpyUnpickler = _RestrictedUnpickler  # type: ignore[assignment]
        try:
            obj = joblib.load(io.BytesIO(data))
        finally:
            _jnp.NumpyUnpickler = _original  # type: ignore[assignment]

        if not isinstance(obj, cls):
            raise pickle.UnpicklingError(f"expected PreferenceScorer, got {type(obj).__name__}")
        return obj


def fit(
    X: np.ndarray,
    y: np.ndarray,
    sample_weights: np.ndarray,
    threshold: int | None = None,
) -> PreferenceScorer:
    """
    Fit and return a PreferenceScorer.

    Uses LogisticRegression when label_count < threshold, LightGBM otherwise.
    """
    if threshold is None:
        threshold = settings.PERSONALIZATION_THRESHOLD_LABELS

    n = len(y)
    if n < threshold:
        from sklearn.linear_model import LogisticRegression

        clf = LogisticRegression(max_iter=500, class_weight="balanced")
        clf.fit(X, y, sample_weight=sample_weights)
        logger.info("Fitted LogisticRegression (n=%d, threshold=%d)", n, threshold)
    else:
        import lightgbm as lgb

        clf = lgb.LGBMClassifier(n_estimators=100, learning_rate=0.1, verbosity=-1)
        clf.fit(X, y, sample_weight=sample_weights)
        logger.info("Fitted LightGBM (n=%d)", n)

    return PreferenceScorer(clf, label_count=n)
