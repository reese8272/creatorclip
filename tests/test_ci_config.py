"""Regression guard for the deploy pipeline runner targets (Issue 101).

The deploy pipeline (docker-publish.yml -> deploy.yml via workflow_run)
runs entirely on the production VM's self-hosted GitHub Actions runner.
This eliminates GitHub-hosted minute consumption, which had repeatedly
blocked deploys when account billing lapsed (live-observed 2026-05-31 on
both `8074392` and `05ddf54` push runs -- every hosted job fast-failed
in 3-5s with "recent account payments have failed").

These tests pin the `runs-on: self-hosted` directive so a well-meaning
future "let me unblock CI by moving everything back to ubuntu-latest"
PR can't silently re-introduce the billing dependency.

CI / Quality Gates / Integration workflows intentionally stay on
ubuntu-latest -- they're informational only and don't gate deploys
(deploy.yml's workflow_run depends ONLY on Docker publish completing).

Also guards the NOTIFY_BACKEND default so CI/test runs never accidentally
call an external email provider (Issue 242).
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
        f"ubuntu-latest` -- any hosted-runner job in the deploy pipeline "
        f"re-introduces the billing failure mode (Issue 101)."
    )


def test_docker_publish_triggers_deploy_workflow() -> None:
    """deploy.yml depends on Docker publish via workflow_run. If the
    Docker publish workflow gets renamed, the workflow_run trigger
    silently breaks (no error, deploys just never fire) -- pin the name."""
    docker_publish = _load_workflow("docker-publish.yml")
    deploy = _load_workflow("deploy.yml")

    assert "name: Docker publish" in docker_publish, (
        "docker-publish.yml's top-level `name` must stay 'Docker publish' -- "
        "deploy.yml's workflow_run trigger references it by exact string."
    )
    assert 'workflows: ["Docker publish"]' in deploy, (
        "deploy.yml's workflow_run trigger must reference 'Docker publish' "
        "exactly. If docker-publish.yml's name changes, this string must "
        "change in lockstep or deploys silently never run."
    )


@pytest.mark.parametrize(
    "path,migrate_marker",
    [
        (_WORKFLOWS / "deploy.yml", "alembic upgrade head"),
        (_REPO_ROOT / "scripts" / "deploy.sh", "alembic upgrade head"),
    ],
)
def test_pre_migration_dump_runs_before_alembic(path, migrate_marker) -> None:
    """Both deploy paths must take a pg_dump (via backup_pg.sh, Issue 257) BEFORE
    `alembic upgrade head`, so a bad migration has a restore point. They must stay
    behavior-identical (the deploy.sh ⇄ deploy.yml mirror contract)."""
    src = path.read_text()
    assert "backup_pg.sh" in src, (
        f"{path.name} must invoke scripts/backup_pg.sh for the pre-migration safety "
        f"dump (Issue 257) — do not reimplement pg_dump logic."
    )
    assert "BACKUP_PREFIX=predeploy/" in src, (
        f"{path.name} pre-migration dump must use the predeploy/ prefix so it is "
        f"retained separately from the nightly dailies."
    )
    dump_at = src.index("backup_pg.sh")
    migrate_at = src.index(migrate_marker)
    assert dump_at < migrate_at, (
        f"{path.name}: the backup_pg.sh dump must run BEFORE '{migrate_marker}', "
        f"otherwise a bad migration has no restore point."
    )
    # The gate must hard-fail the deploy when backups ARE configured but the dump fails.
    assert "BACKUP_R2_BUCKET=.+" in src, (
        f"{path.name}: the dump must be gated on BACKUP_R2_BUCKET being configured "
        f"(run-and-abort-on-failure when set; skip-with-warning when unset)."
    )


def test_notify_backend_is_console_in_test_env() -> None:
    """NOTIFY_BACKEND must default to 'console' so tests (and CI) never call Resend.

    The console backend logs emails via logging.getLogger and returns without
    making any external HTTP call. If this test fails, check that .env does not
    override NOTIFY_BACKEND=resend in the test environment. (Issue 242)
    """
    from config import settings

    assert settings.NOTIFY_BACKEND == "console", (
        f"NOTIFY_BACKEND must be 'console' in the test environment to prevent "
        f"accidental Resend API calls; got {settings.NOTIFY_BACKEND!r}. "
        "Check that no .env file is setting NOTIFY_BACKEND=resend for tests."
    )
