"""Router contract for the async improvement brief (202 + poll, Issue 75).

DB-free unit tests for the paths that don't need a row (channel check, debounce,
GET passthrough), driven through the real app with get_current_creator overridden.
The happy-path enqueue (which counts metrics) is covered as an integration test in
test_improvement_isolation.py.
"""

import uuid

import pytest

from auth import get_current_creator
from main import app
from models import Creator, OnboardingState


def _fake_creator(channel_id: str | None = "UC_test") -> Creator:
    return Creator(
        id=uuid.uuid4(),
        google_sub="unit-fake",
        channel_id=channel_id,
        channel_title="Unit Channel",
        onboarding_state=OnboardingState.active,
    )


@pytest.fixture
def override_creator():
    creator = _fake_creator()

    def _set(c: Creator) -> Creator:
        app.dependency_overrides[get_current_creator] = lambda: c
        return c

    yield _set, creator
    app.dependency_overrides.pop(get_current_creator, None)


def test_post_requires_connected_channel(client, override_creator):
    set_creator, _ = override_creator
    set_creator(_fake_creator(channel_id=None))
    resp = client.post("/creators/me/improvement-brief")
    assert resp.status_code == 400
    assert "channel not connected" in resp.json()["detail"].lower()


def test_post_debounces_when_a_job_is_active(client, override_creator, mocker):
    """A second enqueue while one is in flight returns the in-flight status, no new task."""
    set_creator, creator = override_creator
    set_creator(creator)

    mocker.patch("improvement.jobs.is_active", new=mocker.AsyncMock(return_value=True))
    mocker.patch(
        "improvement.jobs.get_status",
        new=mocker.AsyncMock(return_value={"status": "running"}),
    )
    delay = mocker.patch("worker.tasks.generate_improvement_brief.delay")

    resp = client.post("/creators/me/improvement-brief")
    assert resp.status_code == 202
    assert resp.json()["status"] == "running"
    delay.assert_not_called()  # debounced — no duplicate LLM spend


def test_get_returns_job_status(client, override_creator, mocker):
    set_creator, creator = override_creator
    set_creator(creator)
    mocker.patch(
        "improvement.jobs.get_status",
        new=mocker.AsyncMock(return_value={"status": "done", "brief": "hello"}),
    )
    resp = client.get("/creators/me/improvement-brief")
    assert resp.status_code == 200
    assert resp.json() == {"status": "done", "brief": "hello"}
