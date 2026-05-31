"""Regression guard for the deploy pipeline runner targets (Issue 101).

The deploy pipeline (`docker-publish.yml` → `deploy.yml` via `workflow_run`)
runs entirely on the production VM's self-hosted GitHub Actions runner.
This eliminates GitHub-hosted minute consumption, which had repeatedly
blocked deploys when account billing lapsed (live-observed 2026-05-31 on
both `8074392` and `05ddf54` push runs — every hosted job fast-failed
in 3-5s with "recent account payments have failed").

These tests pin the `runs-on: self-hosted` directive so a well-meaning
future "let me unblock CI by moving everything back to ubuntu-latest"
PR can't silently re-introduce the billing dependency.

CI / Quality Gates / Integration workflows intentionally stay on
ubuntu-latest — they're informational only and don't gate deploys
(deploy.yml's workflow_run depends ONLY on Docker publish completing).
"""

import pathlib

import pytest

_REPO_ROOT = pathlib.Path(__file__).parent.parent
_WORKFLOWS = _REPO_ROOT / ".github" / "workflows"


def _load_workflow(name: str) -> str:
    return (_WORKFLOWS / name).read_text()


@pytest.mark.parametrize("workflow", ["docker-publish.yml", "deploy.yml"])
def test_deploy_pipeline_runs_on_self_hosted(workflow: str) -> None:
    """The deploy pipeline workflows MUST target `self-hosted` runners.

    A regression to `ubuntu-latest` re-introduces the GitHub-hosted billing
    dependency that has now blocked production deploys twice (Issue 101).
    """
    src = _load_workflow(workflow)
    assert "runs-on: self-hosted" in src, (
        f".github/workflows/{workflow} must use `runs-on: self-hosted` so "
        f"deploys never depend on GitHub-hosted runner billing. See "
        f"Issue 101 in docs/DECISIONS.md for the rationale."
    )
    assert "runs-on: ubuntu-latest" not in src, (
        f".github/workflows/{workflow} must NOT contain `runs-on: "
        f"ubuntu-latest` — any hosted-runner job in the deploy pipeline "
        f"re-introduces the billing failure mode (Issue 101)."
    )


def test_docker_publish_triggers_deploy_workflow() -> None:
    """deploy.yml depends on Docker publish via workflow_run. If the
    Docker publish workflow gets renamed, the workflow_run trigger
    silently breaks (no error, deploys just never fire) — pin the name."""
    docker_publish = _load_workflow("docker-publish.yml")
    deploy = _load_workflow("deploy.yml")

    assert "name: Docker publish" in docker_publish, (
        "docker-publish.yml's top-level `name` must stay 'Docker publish' — "
        "deploy.yml's workflow_run trigger references it by exact string."
    )
    assert 'workflows: ["Docker publish"]' in deploy, (
        "deploy.yml's workflow_run trigger must reference 'Docker publish' "
        "exactly. If docker-publish.yml's name changes, this string must "
        "change in lockstep or deploys silently never run."
    )
