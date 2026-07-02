"""Per-retrain NDCG emission + warn-only regression ratchet (Issue 202).

`_emit_preference_metrics` is best-effort by contract: it stores the offline eval on the
newest PreferenceModel row and warns on a regression, but must NEVER fail the retrain.
Sessions are mocked (unit lane); the harness math itself is covered in tests/eval/.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from config import settings
from preference.efficacy import CreatorMetrics
from worker.tasks import _emit_preference_metrics


def _creator_metrics(ndcg: float, map_: float = 0.6, n_eval: int = 5) -> CreatorMetrics:
    cm = CreatorMetrics(creator_id=uuid.uuid4(), n_eval=n_eval)
    cm.ndcg = {"dna_preference": ndcg, "random": 0.3, "generic_signal": 0.5}
    cm.map = {"dna_preference": map_, "random": 0.2, "generic_signal": 0.4}
    return cm


def _session_with_versions(rows: list[MagicMock]) -> AsyncMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    return session


async def test_emission_stores_metrics_on_newest_version() -> None:
    newest = MagicMock(version=3)
    previous = MagicMock(version=2, metrics_jsonb={"ndcg_at_5": 0.71})
    session = _session_with_versions([newest, previous])

    with (
        patch(
            "preference.efficacy.evaluate_creator", AsyncMock(return_value=_creator_metrics(0.7))
        ),
        patch("worker.tasks.log_event") as events,
    ):
        await _emit_preference_metrics(session, uuid.uuid4())

    stored = newest.metrics_jsonb
    assert set(stored) == {"ndcg_at_5", "map_at_5", "n_eval", "computed_at"}
    assert stored["ndcg_at_5"] == 0.7
    assert stored["map_at_5"] == 0.6
    assert stored["n_eval"] == 5
    session.commit.assert_awaited_once()
    emitted = [c.args[0] for c in events.call_args_list]
    assert "preference_metrics_computed" in emitted
    # 0.71 -> 0.70 is within the 0.05 threshold: no regression warning.
    assert "preference_metrics_regression" not in emitted


async def test_ratchet_warns_on_ndcg_drop_beyond_threshold() -> None:
    newest = MagicMock(version=4)
    previous = MagicMock(version=3, metrics_jsonb={"ndcg_at_5": 0.9})
    session = _session_with_versions([newest, previous])
    drop = settings.PREFERENCE_NDCG_REGRESSION_THRESHOLD + 0.10  # well past the ratchet

    with (
        patch(
            "preference.efficacy.evaluate_creator",
            AsyncMock(return_value=_creator_metrics(0.9 - drop)),
        ),
        patch("worker.tasks.log_event") as events,
    ):
        await _emit_preference_metrics(session, uuid.uuid4())

    regression = [c for c in events.call_args_list if c.args[0] == "preference_metrics_regression"]
    assert len(regression) == 1, "a crafted NDCG drop past the threshold must warn"
    assert regression[0].kwargs["severity"] == "warning"
    # WARN, don't block: metrics were still stored and committed.
    assert newest.metrics_jsonb["ndcg_at_5"] < 0.9
    session.commit.assert_awaited_once()


async def test_retrain_survives_evaluate_creator_raising() -> None:
    """Best-effort contract: any harness exception is swallowed (logged) — never raised."""
    session = AsyncMock()
    with (
        patch(
            "preference.efficacy.evaluate_creator",
            AsyncMock(side_effect=RuntimeError("harness exploded")),
        ),
        patch("worker.tasks.log_event"),
    ):
        await _emit_preference_metrics(session, uuid.uuid4())  # must not raise
    session.rollback.assert_awaited()
    session.commit.assert_not_awaited()


def test_harness_extracted_to_preference_package() -> None:
    """Issue 202 extraction: production imports come from preference.efficacy, and the
    tests.eval shims re-export the very same objects (no forked copies)."""
    import preference.efficacy as prod
    import tests.eval.efficacy as shim
    import tests.eval.metrics as metrics_shim

    for name in (
        "LabeledClip",
        "compute_creator_metrics",
        "load_labeled_clips",
        "evaluate_creator",
        "sweep_half_life",
        "select_best_half_life",
        "pool_metrics",
    ):
        assert getattr(shim, name) is getattr(prod, name)
    for name in ("ndcg_at_k", "chronological_split", "bootstrap_ci"):
        assert getattr(metrics_shim, name) is getattr(prod, name)
