"""
Preference model: LogisticRegression cold-start → LightGBM warm-start.

Below PERSONALIZATION_THRESHOLD_LABELS uses LogisticRegression (fast, stable
with sparse data). At or above uses LightGBM for better non-linear fit.
"""

import logging
import pickle

import numpy as np

from config import settings

logger = logging.getLogger(__name__)


class PreferenceScorer:
    """Wraps either a LogisticRegression or LightGBM classifier."""

    def __init__(self, model, label_count: int) -> None:
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
        return pickle.dumps(self)

    @classmethod
    def from_bytes(cls, data: bytes) -> "PreferenceScorer":
        return pickle.loads(data)


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
