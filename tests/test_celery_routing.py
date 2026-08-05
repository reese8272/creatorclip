"""Celery queue routing pins (Issue 432).

ffmpeg-render tasks must route to the dedicated `render` queue (consumed with
concurrency 1) — parallel renders starve each other into the render timeout on
a small box. Everything else stays on the default queue. These pins keep a new
render task or a rename from silently landing back on the parallel worker.
"""

from worker.celery_app import RENDER_QUEUE, RENDER_TASKS, celery


def test_render_tasks_route_to_render_queue() -> None:
    routes = celery.conf.task_routes
    for name in RENDER_TASKS:
        assert routes.get(name) == {"queue": RENDER_QUEUE}, (
            f"{name} must route to the {RENDER_QUEUE!r} queue — parallel ffmpeg "
            "encodes starve each other into the render timeout (Issue 432)"
        )


def test_render_task_names_are_registered() -> None:
    """A typo in RENDER_TASKS would route nothing while looking configured."""
    import worker.tasks  # noqa: F401 — registers the task names

    for name in RENDER_TASKS:
        assert name in celery.tasks, f"{name} is not a registered Celery task"


def test_every_ffmpeg_task_is_routed() -> None:
    """Every currently-known ffmpeg-encoding task is in the routed set."""
    expected = {
        "worker.tasks.render_clip",
        "worker.tasks.render_video_clips",
        "worker.tasks.clean_clip",
        "worker.tasks.edit_clip",
        "worker.tasks.render_summary",
    }
    assert set(RENDER_TASKS) == expected
