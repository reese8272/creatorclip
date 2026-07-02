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
import yaml

_REPO_ROOT = pathlib.Path(__file__).parent.parent
_WORKFLOWS = _REPO_ROOT / ".github" / "workflows"


def _load_workflow(name: str) -> str:
    return (_WORKFLOWS / name).read_text()


def _load_compose(name: str) -> dict:
    return yaml.safe_load((_REPO_ROOT / name).read_text())


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
    if path.name == "deploy.yml":
        # Scope to the prod `deploy` job: the staging gate (Issue 298) runs its
        # own `alembic upgrade head` earlier BY DESIGN and intentionally has no
        # pre-dump — staging data is the crash-test dummy (reseedable), and a
        # migration that breaks it is the gate doing its job.
        src = src[src.index("\n  deploy:") :]
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


# ── Staging-parity gate (Issue 298) + rollback IMAGE_TAG fix (Issue 271) ─────


def test_prod_deploy_needs_staging_gate() -> None:
    """The prod deploy job MUST be gated on the data-bearing staging deploy.

    Motivating incident (2026-07-02 00:41): CI's fresh-DB bootstrap passed while
    prod's data-bearing DB failed the same migration. Removing this edge
    re-introduces that blind spot (Issue 298)."""
    deploy = yaml.safe_load(_load_workflow("deploy.yml"))
    prod_job = deploy["jobs"]["deploy"]
    assert prod_job.get("needs") == "deploy-staging", (
        "deploy.yml's `deploy` job must declare `needs: deploy-staging` — the "
        "staging-parity gate (Issue 298) is what catches migrations that break "
        "on existing data before they reach prod."
    )
    assert "deploy-staging" in deploy["jobs"], (
        "deploy.yml must define the `deploy-staging` gate job (Issue 298)."
    )


def test_prod_deploy_skip_staging_break_glass() -> None:
    """The break-glass path must exist AND actually work.

    The condition needs `!cancelled()` — without a status-check function GitHub
    injects an implicit success(), which skips prod even when skip_staging=true
    (a skipped needs-job fails success())."""
    src = _load_workflow("deploy.yml")
    assert "needs.deploy-staging.result == 'success' || inputs.skip_staging == true" in src, (
        "deploy.yml's prod `if:` must gate on staging success with the "
        "skip_staging break-glass escape hatch (Issue 298)."
    )
    assert "!cancelled()" in src, (
        "The prod deploy `if:` must include !cancelled() — otherwise the "
        "implicit success() makes skip_staging=true a no-op (skipped needs-job)."
    )
    deploy = yaml.safe_load(_load_workflow("deploy.yml"))
    inputs = deploy[True]["workflow_dispatch"]["inputs"]  # `on:` parses as bool True
    assert inputs["skip_staging"]["type"] == "boolean"
    assert inputs["skip_staging"]["default"] is False, (
        "skip_staging must default to false — the gate is opt-OUT, not opt-in."
    )


def test_staging_migrations_run_in_container() -> None:
    """Staging migrations must run via `exec -T app` INSIDE the image under test
    (pinned deps), not from a host checkout, and must assert current == heads
    (mirrors scripts/deploy.sh's silent-no-op guard from the 2026-06-24 outage)."""
    src = _load_workflow("deploy.yml")
    assert (
        "docker compose -p ccstage -f docker-compose.staging.yml \\\n"
        "            exec -T app alembic upgrade head" in src
    ), (
        "deploy.yml's staging gate must run `alembic upgrade head` in-container "
        "(compose project ccstage, `exec -T app`) — a host-side run uses the "
        "runner's Python, not the image's pinned deps (Issue 298)."
    )
    assert "alembic heads" in src and "alembic current" in src, (
        "The staging gate must verify `alembic current` == `alembic heads` so a "
        "silently rolled-back upgrade cannot pass the gate."
    )


def test_staging_image_is_sha_pinned_never_latest() -> None:
    """The gate must deploy the EXACT image under test: the sha- short tag that
    docker-publish.yml pushes (`type=sha,prefix=sha-` → sha-<7 chars>), never
    :latest (which can race a concurrent push)."""
    docker_publish = _load_workflow("docker-publish.yml")
    assert "type=sha,prefix=sha-" in docker_publish, (
        "docker-publish.yml must keep the `type=sha,prefix=sha-` tag rule — the "
        "staging gate resolves images by that exact format (7-char short SHA)."
    )
    src = _load_workflow("deploy.yml")
    assert "creatorclip:sha-${HEAD_SHA:0:7}" in src, (
        "The staging gate must resolve the image as creatorclip:sha-<7-char "
        "short SHA> to match docker-publish.yml's sha- tag format exactly."
    )
    staging_job_src = src[src.index("deploy-staging:") : src.index("\n  deploy:")]
    assert "creatorclip:latest" not in staging_job_src, (
        "The staging gate must never run creatorclip:latest — gating a "
        "different image than the one prod will run defeats the gate (Issue 298)."
    )


def test_prod_compose_interpolates_image_tag_for_rollback() -> None:
    """Issue 271 fix: docker-compose.prod.yml hardcoded :latest, so the
    auto-rollback's IMAGE_TAG env was never interpolated and 'rollback'
    relaunched the broken image. All three app-image services must use
    ${IMAGE_TAG:-latest}, and deploy.yml must set IMAGE_TAG on the rollback
    path (normal deploys leave it unset → :latest, behavior unchanged)."""
    compose = _load_compose("docker-compose.prod.yml")
    expected = "ghcr.io/reese8272/creatorclip:${IMAGE_TAG:-latest}"
    for svc in ("app", "worker", "beat"):
        assert compose["services"][svc]["image"] == expected, (
            f"docker-compose.prod.yml service '{svc}' must pin image "
            f"'{expected}' so the rollback path's IMAGE_TAG actually selects "
            f"the previous image (Issue 271)."
        )
    deploy = _load_workflow("deploy.yml")
    assert 'docker tag "${PREV_IMAGE}" ghcr.io/reese8272/creatorclip:rollback' in deploy, (
        "deploy.yml's rollback must re-tag the PREV_IMAGE digest as :rollback — "
        "a digest ref cannot sit in the compose tag slot."
    )
    assert "IMAGE_TAG=rollback docker compose -f docker-compose.prod.yml up -d" in deploy, (
        "deploy.yml's rollback must launch with IMAGE_TAG=rollback so the "
        "${IMAGE_TAG:-latest} interpolation selects the previous image."
    )


def test_staging_prod_compose_parity() -> None:
    """Staging must exercise the same infra images as prod (Issue 298) — a
    version skew (e.g. postgres major) would make the gate's green meaningless.

    Documented inversion (allowlisted): pgbouncer is STAGING-ONLY. Staging
    fronts Postgres with PgBouncer (transaction mode) because that is the
    production-K8s topology under test since Issue 112; the single-VM prod
    compose connects directly. See docs/STAGING_ACCESS.md parity matrix."""
    prod = _load_compose("docker-compose.prod.yml")["services"]
    staging = _load_compose("docker-compose.staging.yml")["services"]

    assert prod["postgres"]["image"] == staging["postgres_staging"]["image"], (
        "postgres image must match between docker-compose.prod.yml and "
        "docker-compose.staging.yml — the gate must migrate the same PG version "
        "prod runs."
    )
    assert prod["redis"]["image"] == staging["redis_staging"]["image"], (
        "redis image must match between docker-compose.prod.yml and docker-compose.staging.yml."
    )
    # The staging app/worker image must be parametrized for the gate and must
    # never fall back to the prod-shared :latest tag (Issue 142).
    for svc in ("app", "worker"):
        assert staging[svc]["image"] == "${STAGING_IMAGE:-creatorclip:staging}", (
            f"docker-compose.staging.yml service '{svc}' must use "
            f"${{STAGING_IMAGE:-creatorclip:staging}} so the deploy gate can "
            f"inject the exact image under test (Issue 298)."
        )
    assert "pgbouncer" not in prod, (
        "pgbouncer is the allowlisted staging-only inversion — if it lands in "
        "prod compose, update the parity matrix in docs/STAGING_ACCESS.md and "
        "this test together."
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
