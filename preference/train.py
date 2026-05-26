"""
Build and persist a preference model from a creator's clip feedback.

Training data: all clip_feedback rows for the creator's clips, weighted by
recency decay × outcome multiplier.  Positive label = upvote or trim (explicit
keep); negative = downvote; skip is excluded.
"""

import logging
import uuid
from datetime import UTC, datetime

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Clip, ClipFeedback, ClipOutcome, FeedbackAction, PreferenceModel
from preference.decay import sample_weight
from preference.features import FEATURE_NAMES, clip_features
from preference.model import PreferenceScorer, fit

logger = logging.getLogger(__name__)

_POSITIVE_ACTIONS = {FeedbackAction.upvote, FeedbackAction.trim}
_NEGATIVE_ACTIONS = {FeedbackAction.downvote}


async def build_and_save(session: AsyncSession, creator_id: uuid.UUID) -> PreferenceScorer | None:
    """
    Load feedback, build training data, fit model, persist weights_blob to DB.
    Returns None if there are fewer than 2 training samples (one per class minimum).
    """
    # Fetch feedback + clip signals + outcomes in one pass
    result = await session.execute(
        select(ClipFeedback, Clip, ClipOutcome)
        .join(Clip, Clip.id == ClipFeedback.clip_id)
        .outerjoin(ClipOutcome, ClipOutcome.clip_id == ClipFeedback.clip_id)
        .where(
            ClipFeedback.creator_id == creator_id,
            ClipFeedback.action.in_(list(_POSITIVE_ACTIONS) + list(_NEGATIVE_ACTIONS)),
        )
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
        w = sample_weight(feedback.created_at, performed_well=performed_well)

        X_list.append(feats)
        y_list.append(label)
        w_list.append(w)

    if len(X_list) < 2 or len(set(y_list)) < 2:
        logger.info(
            "Insufficient training data for creator %s (n=%d, classes=%s)",
            creator_id,
            len(X_list),
            set(y_list),
        )
        return None

    X = np.array(X_list, dtype=float)
    y = np.array(y_list, dtype=int)
    w = np.array(w_list, dtype=float)

    scorer = fit(X, y, w)

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
    await session.commit()

    logger.info(
        "Saved PreferenceModel v%d for creator %s (n=%d labels)", new_version, creator_id, len(y)
    )
    return scorer


async def load_latest(session: AsyncSession, creator_id: uuid.UUID) -> PreferenceScorer | None:
    """Load the most recently trained model for a creator, or None."""
    result = await session.execute(
        select(PreferenceModel)
        .where(PreferenceModel.creator_id == creator_id)
        .order_by(PreferenceModel.version.desc())
    )
    row = result.scalars().first()
    if not row or not row.weights_blob:
        return None
    try:
        return PreferenceScorer.from_bytes(row.weights_blob)
    except Exception as exc:
        logger.warning("Failed to deserialize preference model: %s", exc)
        return None
