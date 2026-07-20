"""
Build and persist a preference model from a creator's clip feedback.

Training data: newest-first PREFERENCE_MAX_TRAINING_LABELS clip_feedback rows
for the creator's clips, weighted by recency decay × outcome multiplier.
Positive label = upvote or trim (explicit keep); negative = downvote; skip
is excluded.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime

import numpy as np
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import Clip, ClipFeedback, ClipOutcome, FeedbackAction, PreferenceModel
from preference import _scorer_cache as scorer_cache
from preference.decay import sample_weight
from preference.features import FEATURE_NAMES, clip_features
from preference.model import PreferenceScorer, fit

logger = logging.getLogger(__name__)

_POSITIVE_ACTIONS = {FeedbackAction.upvote, FeedbackAction.trim}
_NEGATIVE_ACTIONS = {FeedbackAction.downvote}
# Feedback actions that contribute a training label (skip/format are excluded).
TRAINABLE_ACTIONS = _POSITIVE_ACTIONS | _NEGATIVE_ACTIONS

# Superseded PreferenceModel rows to retain per creator. Only the newest 2
# versions are ever read (load_latest + the worker's NDCG warn-ratchet), but a
# few extra are kept as a manual-rollback margin; everything older is pruned on
# each retrain so weights_blob storage doesn't grow without bound.
_KEEP_MODEL_VERSIONS = 5


async def build_and_save(session: AsyncSession, creator_id: uuid.UUID) -> PreferenceScorer | None:
    """
    Load feedback, build training data, fit model, persist weights_blob to DB.
    Returns None if there are fewer than 2 training samples (one per class minimum).
    """
    # Fetch feedback + clip signals + outcomes in one pass. Newest-first +
    # LIMIT so a power creator with years of feedback doesn't pull the entire
    # set into memory and into LightGBM's ndarray copy on every retrain. The
    # 30d-half-life recency decay (preference/decay.py) makes rows past the
    # cap worth ~0 in the sample weight anyway. (Issue 102)
    result = await session.execute(
        select(ClipFeedback, Clip, ClipOutcome)
        .join(Clip, Clip.id == ClipFeedback.clip_id)
        .outerjoin(ClipOutcome, ClipOutcome.clip_id == ClipFeedback.clip_id)
        .where(
            ClipFeedback.creator_id == creator_id,
            ClipFeedback.action.in_(TRAINABLE_ACTIONS),
        )
        .order_by(ClipFeedback.created_at.desc())
        .limit(settings.PREFERENCE_MAX_TRAINING_LABELS)
    )
    rows = result.all()

    X_list, y_list, w_list = [], [], []
    for feedback, clip, outcome in rows:
        signals = clip.signals_jsonb or {}
        feats_dict = signals.get("features", {})
        feats = clip_features(
            signal_density=feats_dict.get("signal_density", 0.0),
            hook_energy=feats_dict.get("hook_energy", 0.0),
            silence_ratio=feats_dict.get("silence_ratio", 0.0),
            dna_match=clip.dna_match,
            clip_duration_s=feats_dict.get("clip_duration_s", 0.0),
            setup_length_s=feats_dict.get("setup_length_s", 0.0),
            has_retention_spike=feats_dict.get("has_retention_spike", False),
            has_laughter=feats_dict.get("has_laughter", False),
        )
        label = 1 if feedback.action in _POSITIVE_ACTIONS else 0
        performed_well = outcome.performed_well if outcome else None
        weight = sample_weight(feedback.created_at, performed_well=performed_well)

        X_list.append(feats)
        y_list.append(label)
        w_list.append(weight)

    if len(X_list) < 2 or len(set(y_list)) < 2:
        logger.info(
            "Insufficient training data for creator %s (n=%d, classes=%s)",
            creator_id,
            len(X_list),
            set(y_list),
        )
        return None

    X: np.ndarray = np.array(X_list, dtype=float)
    y: np.ndarray = np.array(y_list, dtype=int)
    w: np.ndarray = np.array(w_list, dtype=float)

    # LightGBM / LogisticRegression fit is CPU-bound; offload to a thread so
    # the surrounding async context (Celery retrain task's private loop, or
    # any future caller from the API loop) isn't blocked for seconds on a
    # power creator. asyncio.to_thread == loop.run_in_executor(None, ...) per
    # 2025 FastAPI guidance. (Issue 102)
    scorer = await asyncio.to_thread(fit, X, y, w)

    # Serialize concurrent retrains for this creator so the version assignment
    # (max+1) cannot race into a UNIQUE(creator_id, version) violation. The xact
    # lock is held until commit. (Issue 71)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": str(creator_id)}
    )

    # Persist to preference_models table
    result = await session.execute(
        select(PreferenceModel)
        .where(PreferenceModel.creator_id == creator_id)
        .order_by(PreferenceModel.version.desc())
    )
    existing = result.scalars().first()
    new_version = (existing.version + 1) if existing else 1

    model_row = PreferenceModel(
        creator_id=creator_id,
        version=new_version,
        weights_blob=scorer.to_bytes(),
        feature_schema_jsonb={"features": FEATURE_NAMES},
        updated_at=datetime.now(UTC),
    )
    session.add(model_row)
    # Prune superseded versions in the same transaction — still under the
    # advisory xact lock, so no concurrent retrain can race the delete.
    await session.execute(
        delete(PreferenceModel).where(
            PreferenceModel.creator_id == creator_id,
            PreferenceModel.version <= new_version - _KEEP_MODEL_VERSIONS,
        )
    )
    await session.commit()

    logger.info(
        "Saved PreferenceModel v%d for creator %s (n=%d labels)", new_version, creator_id, len(y)
    )
    return scorer


async def load_latest(session: AsyncSession, creator_id: uuid.UUID) -> PreferenceScorer | None:
    """Load the most recently trained model for a creator, or None.

    Backed by a per-worker LRU keyed by ``(creator_id, version)`` so the
    lock-contended joblib deserialize runs once per model version rather than
    on every rerank. The version + feature schema are read with a cheap query;
    the blob is fetched only on a cache miss. (Issue 78a)
    """
    result = await session.execute(
        select(PreferenceModel.version, PreferenceModel.feature_schema_jsonb)
        .where(PreferenceModel.creator_id == creator_id)
        .order_by(PreferenceModel.version.desc())
        .limit(1)
    )
    row = result.first()
    if row is None:
        return None
    version, feature_schema = row
    # Feature-schema drift: if the stored model was trained on a different feature
    # set than the current code, do NOT score with it — fall back to DNA ranking
    # (honest) rather than silently producing meaningless probabilities. (Issue 71)
    stored_features = (feature_schema or {}).get("features")
    if stored_features != FEATURE_NAMES:
        logger.warning(
            "Preference model feature-schema drift for creator %s — falling back to DNA",
            creator_id,
        )
        return None

    key = (creator_id, version)
    cached = scorer_cache.get(key)
    if cached is not None:
        return cached

    blob = await session.scalar(
        select(PreferenceModel.weights_blob).where(
            PreferenceModel.creator_id == creator_id,
            PreferenceModel.version == version,
        )
    )
    if not blob:
        return None
    # joblib.load runs sync + holds a process-wide unpickler lock (preserves
    # the RCE allowlist from Issue 71). Offload via to_thread so the lock
    # serializes threads, not coroutines — two creators hitting rerank on a
    # cold cache no longer queue behind each other on the API event loop.
    # joblib 1.x has no public per-load NumpyUnpickler injection slot, so
    # the module-global swap stays as the documented extension point
    # (DECISIONS 2026-05-31 — Issue 102). (Issue 102)
    try:
        scorer = await asyncio.to_thread(PreferenceScorer.from_bytes, blob)
    except Exception as exc:
        logger.warning("Failed to deserialize preference model: %s", exc)
        return None
    scorer_cache.put(key, scorer)
    return scorer
