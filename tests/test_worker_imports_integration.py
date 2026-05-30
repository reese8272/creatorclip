"""Integration test that catches today's PYTHONPATH-bug class forever (Issue 86).

The bug — prod incident 2026-05-30, the trigger for Issue 86 — was that
``build_dna`` crashed 4× on ``ModuleNotFoundError("No module named 'dna'")``
because Celery is started via the ``celery`` console script (sys.path[0] =
``/root/.local/bin/``), so the forked pool worker couldn't resolve first-party
packages even though WORKDIR was ``/app``.

The fix was ``ENV PYTHONPATH=/app`` in the Dockerfile. This test guards that
fix forever: it spawns a real ``celery -A worker.celery_app worker`` subprocess
from a directory where first-party packages would NOT be importable via plain
sys.path[0] semantics (mimicking how the console-script entry behaves), and
asserts that a tiny celery task which does ``from dna.brief import generate_brief``
runs successfully.

If anyone removes the PYTHONPATH env var, or someone adds another lazy
first-party import in worker/tasks.py that breaks the same way, this test
fails immediately — they don't have to ship a broken image to find out.

Marked `integration` because it spawns a real Celery worker + needs a real
Redis. Run via the integration-tests CI lane (or `pytest -m integration`).
"""

from __future__ import annotations

import os
import select
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from config import settings

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent


def _redis_reachable() -> bool:
    from urllib.parse import urlparse

    url = urlparse(settings.REDIS_URL)
    try:
        with socket.create_connection((url.hostname or "localhost", url.port or 6379), timeout=2):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _redis_reachable(), reason="needs a live Redis")
def test_celery_worker_can_resolve_first_party_imports(tmp_path: Path) -> None:
    """Spawn a real celery worker, fire a task that imports `dna.brief`, assert success.

    This is a true subprocess test — no patching, no mocking. The whole point
    is to verify the *real* sys.path resolution in a forked celery pool worker.
    """
    # The probe module we'll register as a celery task. It does the EXACT lazy
    # import that failed in prod, plus a few other first-party packages so a
    # future regression on any of them is caught by the same test.
    probe = tmp_path / "import_probe.py"
    probe.write_text(
        f"""
import sys
sys.path.insert(0, {str(REPO_ROOT)!r})
from worker.celery_app import celery


@celery.task(name='test.import_probe')
def import_probe():
    # The exact import that failed in prod 2026-05-30.
    from dna.brief import generate_brief
    # Other lazy first-party imports — all should resolve cleanly.
    from dna.builder import build_patterns
    from dna.embeddings import embed_brief
    from worker.progress import sync_emit
    return 'ok'
""",
        encoding="utf-8",
    )

    # Spawn celery in a CWD that does NOT contain the project, mimicking how
    # the prod container's console-script entry exposes sys.path[0] as the
    # script's directory rather than /app. The test passes only when PYTHONPATH
    # (or an equivalent mechanism) makes the project root importable
    # independent of CWD.
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    # Unbuffered output — without this, the "ready" log line sits in stdout
    # buffer indefinitely and our readiness detector hangs.
    env["PYTHONUNBUFFERED"] = "1"
    # Ensure the worker uses the same Redis as the rest of the test suite.
    env["REDIS_URL"] = settings.REDIS_URL
    # Forward the test env's required settings — config.py fails fast otherwise.
    for key in (
        "DATABASE_URL",
        "ANTHROPIC_API_KEY",
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "OAUTH_REDIRECT_URI",
        "TOKEN_ENCRYPTION_KEY",
        "JWT_SECRET_KEY",
        "ALLOWED_ORIGINS",
    ):
        if key in os.environ:
            env[key] = os.environ[key]

    queue = f"test-import-probe-{uuid.uuid4().hex[:8]}"

    worker_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "import_probe.celery",
            "worker",
            "--loglevel=info",  # info-level prints the "ready" line we detect
            "--concurrency=1",
            "--pool=solo",  # solo pool = no fork — fastest startup + cleanest teardown for the test
            f"--queues={queue}",
            f"--hostname=probe-{uuid.uuid4().hex[:6]}@%h",
        ],
        cwd=str(tmp_path),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,  # line-buffered
    )

    try:
        # Wait for the worker to be ready
        ready = _wait_for_worker_ready(worker_proc, timeout=20.0)
        assert ready, "Celery worker did not start in time"

        # Send the probe task and verify it returns 'ok'.
        from celery import Celery

        client = Celery("probe-client", broker=settings.REDIS_URL, backend=settings.REDIS_URL)
        client.conf.task_default_queue = queue
        result = client.send_task("test.import_probe", queue=queue)
        value = result.get(timeout=15.0)
        assert value == "ok", f"unexpected probe result: {value!r}"
    finally:
        worker_proc.terminate()
        try:
            worker_proc.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            worker_proc.kill()


def _wait_for_worker_ready(proc: subprocess.Popen, *, timeout: float) -> bool:
    """Read the worker's stdout until the celery 'ready' line appears or timeout.

    Uses ``select`` so a stalled worker doesn't block the test indefinitely on
    ``readline()`` — we re-check ``proc.poll()`` and the deadline between every
    short read.
    """
    deadline = time.monotonic() + timeout
    fd = proc.stdout.fileno() if proc.stdout else -1
    captured: list[str] = []
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            remaining = proc.stdout.read() if proc.stdout else ""
            captured.append(remaining)
            raise AssertionError(
                f"celery worker exited with code {proc.returncode} before ready\n"
                + "".join(captured)
            )
        ready_fds, _, _ = select.select([fd], [], [], 0.5)
        if not ready_fds:
            continue
        line = proc.stdout.readline() if proc.stdout else ""
        if not line:
            continue
        captured.append(line)
        if "ready" in line.lower():
            return True
    return False
