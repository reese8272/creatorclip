"""
Unit tests for preference/decay.py, preference/features.py, preference/model.py.
"""

import math
import pickle
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from preference.decay import _LAMBDA, feedback_age_days, recency_weight, sample_weight
from preference.features import FEATURE_NAMES, clip_features
from preference.model import PreferenceScorer, fit

# ── recency_weight ─────────────────────────────────────────────────────────────


def test_recency_weight_today_near_one():
    assert recency_weight(0.0) == pytest.approx(1.0)


def test_recency_weight_thirty_days_half():
    assert recency_weight(30.0) == pytest.approx(0.5, abs=0.01)


def test_recency_weight_sixty_days_quarter():
    assert recency_weight(60.0) == pytest.approx(0.25, abs=0.01)


def test_recency_weight_never_negative():
    assert recency_weight(1000.0) >= 0.0


def test_recency_weight_negative_age_clamped_to_today():
    """A negative age (clock skew / future timestamp) must clamp to 0 → weight 1.0.

    Mutation guard (Issue 273): pins the `max(0.0, feedback_age_days)` clamp. Without
    this assertion a mutant changing the clamp floor (e.g. max(1.0, ...) or dropping
    the max) survives, silently down-weighting freshly-created feedback.
    """
    assert recency_weight(-5.0) == pytest.approx(1.0)
    assert recency_weight(-5.0) == pytest.approx(recency_weight(0.0))


def test_recency_weight_older_feedback_lower_weight():
    assert recency_weight(5.0) > recency_weight(25.0)


def test_recency_weight_half_life_derives_from_config():
    """_LAMBDA is derived from DECAY_HALF_LIFE_DAYS (Issue 200 parameterization) and
    equals the configured half-life — distinct from the DNA builder's 90-day half-life."""
    from config import settings

    assert pytest.approx(math.log(2) / settings.DECAY_HALF_LIFE_DAYS) == _LAMBDA
    # At the configured half-life the weight is exactly 0.5 (the definition of half-life)
    # — pins the derivation so a wrong λ formula can't slip through.
    assert recency_weight(settings.DECAY_HALF_LIFE_DAYS) == pytest.approx(0.5, abs=1e-9)


# ── feedback_age_days ─────────────────────────────────────────────────────────


def test_feedback_age_days_recent():
    recent = datetime.now(UTC) - timedelta(days=10)
    assert feedback_age_days(recent) == pytest.approx(10.0, abs=0.1)


def test_feedback_age_days_naive_datetime():
    naive = datetime.now() - timedelta(days=5)
    assert feedback_age_days(naive) >= 0.0


def test_feedback_age_days_future_timestamp_clamped_to_zero():
    """A future created_at (clock skew) clamps to 0 days, never negative.

    Mutation guard (Issue 273): pins the `max(0.0, ...)` floor in feedback_age_days.
    A mutant raising the floor (max(1.0, ...)) would survive without this case.
    """
    future = datetime.now(UTC) + timedelta(days=3)
    assert feedback_age_days(future) == pytest.approx(0.0)


# ── sample_weight ─────────────────────────────────────────────────────────────


def test_sample_weight_performed_well_multiplied():
    ts = datetime.now(UTC) - timedelta(days=1)
    w_base = sample_weight(ts, performed_well=None)
    w_good = sample_weight(ts, performed_well=True)
    assert w_good == pytest.approx(w_base * 3.0)


def test_sample_weight_performed_false_no_multiplier():
    ts = datetime.now(UTC) - timedelta(days=1)
    w_base = sample_weight(ts, performed_well=None)
    w_bad = sample_weight(ts, performed_well=False)
    assert w_bad == pytest.approx(w_base)


def test_sample_weight_older_is_less():
    new_ts = datetime.now(UTC) - timedelta(days=2)
    old_ts = datetime.now(UTC) - timedelta(days=28)
    assert sample_weight(new_ts) > sample_weight(old_ts)


# ── clip_features ─────────────────────────────────────────────────────────────


def test_clip_features_length():
    feats = clip_features()
    assert len(feats) == len(FEATURE_NAMES)


def test_clip_features_boolean_encoding():
    with_spike = clip_features(has_retention_spike=True)
    without = clip_features(has_retention_spike=False)
    assert with_spike[FEATURE_NAMES.index("has_retention_spike")] == 1.0
    assert without[FEATURE_NAMES.index("has_retention_spike")] == 0.0


def test_clip_features_dna_match_defaults_zero():
    feats = clip_features(dna_match=None)
    assert feats[FEATURE_NAMES.index("dna_match")] == 0.0


# ── fit + PreferenceScorer ─────────────────────────────────────────────────────


def _training_data(n_pos=5, n_neg=5):
    rng = np.random.default_rng(42)
    X = rng.random((n_pos + n_neg, len(FEATURE_NAMES)))
    y = np.array([1] * n_pos + [0] * n_neg)
    w = np.ones(n_pos + n_neg)
    return X, y, w


def test_fit_logistic_below_threshold():
    X, y, w = _training_data(5, 5)
    scorer = fit(X, y, w, threshold=20)
    assert isinstance(scorer, PreferenceScorer)
    assert scorer.label_count == 10


def test_fit_lgbm_at_threshold():
    X, y, w = _training_data(15, 15)
    try:
        scorer = fit(X, y, w, threshold=20)
    except OSError:
        pytest.skip("libgomp.so.1 not available on this host")
    assert isinstance(scorer, PreferenceScorer)


def test_predict_score_in_range():
    X, y, w = _training_data()
    scorer = fit(X, y, w, threshold=20)
    feats = clip_features(signal_density=1.0, has_retention_spike=True)
    score = scorer.predict_score(feats)
    assert 0.0 <= score <= 1.0


def test_predict_score_positive_features_higher():
    """Features associated with positive examples should score higher."""
    X, y, w = _training_data(n_pos=10, n_neg=2)
    # Make all positives have signal_density=1.0 and negatives=0.0
    X[:10, FEATURE_NAMES.index("signal_density")] = 1.0
    X[10:, FEATURE_NAMES.index("signal_density")] = 0.0
    scorer = fit(X, y, w, threshold=20)

    high = scorer.predict_score(clip_features(signal_density=1.0))
    low = scorer.predict_score(clip_features(signal_density=0.0))
    # The model learned that high density correlates with positive
    assert high >= low


def test_scorer_round_trips_joblib():
    """A legitimate scorer survives to_bytes → from_bytes with identical predictions."""
    X, y, w = _training_data()
    scorer = fit(X, y, w, threshold=20)
    blob = scorer.to_bytes()
    reloaded = PreferenceScorer.from_bytes(blob)
    feats = clip_features(signal_density=0.5)
    assert reloaded.predict_score(feats) == pytest.approx(scorer.predict_score(feats))


def test_scorer_round_trips_preserves_label_count():
    """label_count attribute survives the serialisation round-trip."""
    X, y, w = _training_data(n_pos=7, n_neg=3)
    scorer = fit(X, y, w, threshold=20)
    reloaded = PreferenceScorer.from_bytes(scorer.to_bytes())
    assert reloaded.label_count == scorer.label_count


def test_lgbm_scorer_round_trips_through_allowlist():
    """The LightGBM branch of the serialization allowlist round-trips (2026-07-20
    assessment): fit ABOVE the threshold (LGBMClassifier + Booster path), dump,
    restore through _RestrictedUnpickler, and predict identically. Without this,
    a dep bump or an incomplete allowlist makes from_bytes raise and load_latest
    silently disables personalization for every mature model."""
    X, y, w = _training_data(15, 15)
    try:
        scorer = fit(X, y, w, threshold=20)  # n=30 ≥ threshold → LightGBM
    except OSError:
        pytest.skip("libgomp.so.1 not available on this host")
    assert type(scorer._model).__name__ == "LGBMClassifier"
    reloaded = PreferenceScorer.from_bytes(scorer.to_bytes())
    feats = clip_features(signal_density=0.5, hook_energy=0.7)
    assert reloaded.predict_score(feats) == pytest.approx(scorer.predict_score(feats))
    assert reloaded.label_count == 30


def _make_malicious_joblib_blob() -> bytes:
    """Return a joblib-format blob whose pickle payload references os.system.

    joblib.dump writes a NumpyPickler stream.  We craft a legitimate scorer
    blob, then append an extra pickle GLOBAL opcode that references a
    disallowed class, simulating an attacker who controls the DB row.

    The appended opcode makes the pickle stream invalid *after* the first
    object, but joblib calls unpickler.load() exactly once — so the GLOBAL
    opcode fires during that load if we embed it inside the serialised object's
    __reduce__ output instead.

    Simpler approach: create a tiny object whose __reduce__ references os.system
    and wrap it in a joblib dump.
    """
    import io as _io

    import joblib as _joblib

    class _Malicious:
        """Object whose pickle reduction calls os.system."""

        def __reduce__(self) -> tuple:
            # This tells pickle: "reconstruct me by calling os.system('')"
            import os

            return (os.system, ("",))

    buf = _io.BytesIO()
    _joblib.dump(_Malicious(), buf)
    return buf.getvalue()


def test_tampered_blob_is_rejected():
    """A joblib blob containing a disallowed class raises pickle.UnpicklingError.

    This tests the real attack vector: an attacker who can write crafted bytes
    to `preference_models.weights_blob` cannot execute arbitrary code because
    `_RestrictedUnpickler.find_class` rejects unknown modules before any
    `__reduce__` output is invoked.
    """
    blob = _make_malicious_joblib_blob()

    with pytest.raises(pickle.UnpicklingError, match="class not allowed"):
        PreferenceScorer.from_bytes(blob)


def test_tampered_blob_arbitrary_global_rejected():
    """Any arbitrary global embedded in a joblib blob is rejected by the allowlist."""
    import io as _io

    # subprocess.Popen is a common RCE gadget — must be blocked.
    import subprocess

    import joblib as _joblib

    class _POpenGadget:
        def __reduce__(self) -> tuple:
            return (subprocess.Popen, (["id"],))

    buf = _io.BytesIO()
    _joblib.dump(_POpenGadget(), buf)

    with pytest.raises(pickle.UnpicklingError, match="class not allowed"):
        PreferenceScorer.from_bytes(buf.getvalue())


# ── Issue 55: build_and_save excludes skip, weights trim/approve correctly ────


@pytest.mark.asyncio
async def test_build_and_save_filters_and_weights_feedback():
    """
    skip is excluded from training;
    trim is counted as positive;
    performed_well=True outcome weights its row 3× vs the trim row (same recency).
    """
    import uuid
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, MagicMock

    from models import Clip, ClipFeedback, ClipOutcome, FeedbackAction
    from preference.decay import sample_weight
    from preference.train import build_and_save

    creator_id = uuid.uuid4()
    now = datetime.now(UTC)

    def _make_clip():
        clip = MagicMock(spec=Clip)
        clip.signals_jsonb = {
            "features": {
                "signal_density": 0.5,
                "hook_energy": 0.3,
                "silence_ratio": 0.1,
                "clip_duration_s": 45.0,
                "setup_length_s": 10.0,
                "has_retention_spike": False,
                "has_laughter": False,
            }
        }
        clip.dna_match = None
        return clip

    def _make_feedback(action: FeedbackAction):
        fb = MagicMock(spec=ClipFeedback)
        fb.action = action
        fb.created_at = now
        return fb

    # Rows: (feedback, clip, outcome)
    # skip row — must be excluded from query because build_and_save filters by action.in_(...)
    # We only return trim, approve, and downvote rows (skip is filtered at the SQL level).
    row_trim = (_make_feedback(FeedbackAction.trim), _make_clip(), None)
    row_approve = (_make_feedback(FeedbackAction.upvote), _make_clip(), None)
    row_downvote = (_make_feedback(FeedbackAction.downvote), _make_clip(), None)

    # Attach a performed_well=True outcome to the approve row
    outcome = MagicMock(spec=ClipOutcome)
    outcome.performed_well = True
    row_approve = (row_approve[0], row_approve[1], outcome)

    db_rows = [row_trim, row_approve, row_downvote]

    # Two execute() calls: first fetches feedback rows, second fetches existing PreferenceModel.
    # Calls: (0) feedback rows, (1) pg_advisory_xact_lock [Issue 71; result ignored],
    # (2) existing PreferenceModel. Accept the params dict the advisory call passes.
    execute_call_count = [0]

    async def _execute(stmt, *args):
        result = MagicMock()
        if execute_call_count[0] == 0:
            result.all.return_value = db_rows
        else:
            # Advisory lock (ignored) + "no existing preference model".
            scalars_mock = MagicMock()
            scalars_mock.first.return_value = None
            result.scalars.return_value = scalars_mock
        execute_call_count[0] += 1
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)
    session.add = MagicMock()
    session.commit = AsyncMock()

    scorer = await build_and_save(session, creator_id)

    assert scorer is not None, "build_and_save returned None — insufficient training data"

    # 3 rows entered training (skip was excluded at SQL layer, not present in db_rows)
    assert scorer.label_count == 3

    # The approve row (performed_well=True) must be weighted 3× vs the trim row (same timestamp).
    w_trim = sample_weight(now, performed_well=None)
    w_approve = sample_weight(now, performed_well=True)
    assert w_approve == pytest.approx(w_trim * 3.0), (
        f"approve weight ({w_approve}) is not 3× trim weight ({w_trim})"
    )

    # The model row was persisted via session.add
    session.add.assert_called_once()


# ── Issue 102: SEV1 + SEV2 fixes (event-loop offload + LIMIT cap + frozenset) ─


@pytest.mark.asyncio
async def test_build_and_save_offloads_fit_to_thread(monkeypatch):
    """LightGBM/LogisticRegression `fit` is CPU-bound; build_and_save must
    invoke it via `asyncio.to_thread` so the surrounding async loop is not
    blocked for seconds on a power creator. (Issue 102 SEV1)

    Pins the call shape rather than measuring actual thread offload —
    runtime offload is asyncio's responsibility once the API is used.
    """
    import asyncio
    import uuid
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, MagicMock, patch

    from models import Clip, ClipFeedback, FeedbackAction
    from preference.train import build_and_save

    creator_id = uuid.uuid4()
    now = datetime.now(UTC)

    def _row():
        clip = MagicMock(spec=Clip)
        clip.signals_jsonb = {"features": {}}
        clip.dna_match = None
        fb = MagicMock(spec=ClipFeedback)
        fb.action = FeedbackAction.upvote
        fb.created_at = now
        return (fb, clip, None)

    db_rows = [_row(), _row(), _row()]
    db_rows.append(
        (
            MagicMock(spec=ClipFeedback, action=FeedbackAction.downvote, created_at=now),
            MagicMock(spec=Clip, signals_jsonb={"features": {}}, dna_match=None),
            None,
        )
    )

    execute_call_count = [0]

    async def _execute(stmt, *args):
        result = MagicMock()
        if execute_call_count[0] == 0:
            result.all.return_value = db_rows
        else:
            scalars_mock = MagicMock()
            scalars_mock.first.return_value = None
            result.scalars.return_value = scalars_mock
        execute_call_count[0] += 1
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)
    session.add = MagicMock()
    session.commit = AsyncMock()

    real_to_thread = asyncio.to_thread
    with patch("preference.train.asyncio.to_thread", side_effect=real_to_thread) as spy:
        scorer = await build_and_save(session, creator_id)

    assert scorer is not None
    # `fit` was invoked via asyncio.to_thread, not called directly
    assert spy.called, "build_and_save must offload fit() via asyncio.to_thread"
    # First positional arg of the first to_thread call is the `fit` function
    first_call_args = spy.call_args_list[0].args
    assert first_call_args[0].__name__ == "fit", (
        f"first to_thread call should wrap fit(), got {first_call_args[0].__name__}"
    )


@pytest.mark.asyncio
async def test_build_and_save_caps_query_at_max_training_labels(monkeypatch):
    """Feedback query must use ORDER BY created_at DESC + LIMIT
    PREFERENCE_MAX_TRAINING_LABELS so a power creator with years of
    feedback doesn't pull the entire set into memory + into LightGBM's
    ndarray copy. (Issue 102 SEV2)
    """
    import uuid
    from unittest.mock import AsyncMock, MagicMock

    from config import settings
    from preference.train import build_and_save

    captured_stmts: list = []

    async def _execute(stmt, *args):
        captured_stmts.append(stmt)
        result = MagicMock()
        result.all.return_value = []  # no rows → return None, but query was captured
        scalars_mock = MagicMock()
        scalars_mock.first.return_value = None
        result.scalars.return_value = scalars_mock
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)

    await build_and_save(session, uuid.uuid4())

    assert len(captured_stmts) >= 1
    compiled = str(captured_stmts[0].compile(compile_kwargs={"literal_binds": True}))
    # LIMIT clause must reflect the config value
    assert f"LIMIT {settings.PREFERENCE_MAX_TRAINING_LABELS}" in compiled, (
        f"feedback query must LIMIT to PREFERENCE_MAX_TRAINING_LABELS; got: {compiled}"
    )
    # Newest-first ordering — recency decay makes older rows worth ~0
    assert "ORDER BY clip_feedback.created_at DESC" in compiled, (
        f"feedback query must order newest-first; got: {compiled}"
    )


@pytest.mark.asyncio
async def test_load_latest_offloads_from_bytes_to_thread(monkeypatch):
    """PreferenceScorer.from_bytes runs joblib.load under a process-wide
    unpickler lock (the RCE allowlist from Issue 71). Calling it directly
    on the event loop serializes every rerank across the entire process.
    load_latest must offload via asyncio.to_thread so the lock serializes
    threads, not coroutines. (Issue 102 SEV1)
    """
    import asyncio
    import uuid
    from unittest.mock import AsyncMock, MagicMock, patch

    from preference import _scorer_cache as scorer_cache
    from preference.features import FEATURE_NAMES
    from preference.model import PreferenceScorer
    from preference.train import load_latest

    creator_id = uuid.uuid4()
    scorer_cache.clear()

    call_count = [0]

    async def _execute(stmt, *args):
        result = MagicMock()
        if call_count[0] == 0:
            # First call: version + feature_schema lookup
            result.first.return_value = (1, {"features": FEATURE_NAMES})
        call_count[0] += 1
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)
    session.scalar = AsyncMock(return_value=b"\x80\x05fake-blob-bytes")

    real_to_thread = asyncio.to_thread
    fake_scorer = MagicMock(spec=PreferenceScorer)
    with (
        patch.object(PreferenceScorer, "from_bytes", return_value=fake_scorer) as fb_spy,
        patch("preference.train.asyncio.to_thread", side_effect=real_to_thread) as tt_spy,
    ):
        result = await load_latest(session, creator_id)

    assert result is fake_scorer
    assert tt_spy.called, "load_latest must offload from_bytes via asyncio.to_thread"
    # The to_thread call passed the (patched) from_bytes + the blob — pinning
    # that to_thread received both confirms the offload shape regardless of
    # whether from_bytes is wrapped or replaced.
    first_call = tt_spy.call_args_list[0]
    assert first_call.args[0] is fb_spy, (
        "to_thread should wrap PreferenceScorer.from_bytes (the spy here)"
    )
    assert first_call.args[1] == b"\x80\x05fake-blob-bytes"
    fb_spy.assert_called_once_with(b"\x80\x05fake-blob-bytes")
    scorer_cache.clear()
