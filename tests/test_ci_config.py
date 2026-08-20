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
import re

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


def test_manual_deploy_script_mirrors_auto_rollback() -> None:
    """scripts/deploy.sh must carry the same rollback safety net as deploy.yml
    (Issue 271 port): capture the running app's RepoDigest BEFORE pulling, and
    on smoke failure re-tag it as :rollback and relaunch via IMAGE_TAG=rollback.
    Without this, a failed manual deploy has no recovery path."""
    src = (_REPO_ROOT / "scripts" / "deploy.sh").read_text()
    assert "{{index .RepoDigests 0}}" in src, (
        "scripts/deploy.sh must capture PREV_IMAGE (docker inspect RepoDigests) "
        "before `docker compose pull` — it is the immutable rollback target."
    )
    assert 'docker tag "${PREV_IMAGE}" ghcr.io/reese8272/creatorclip:rollback' in src, (
        "scripts/deploy.sh rollback must re-tag the PREV_IMAGE digest as "
        ":rollback — a digest ref cannot sit in the compose tag slot."
    )
    assert "IMAGE_TAG=rollback docker compose -f docker-compose.prod.yml up -d" in src, (
        "scripts/deploy.sh rollback must launch with IMAGE_TAG=rollback so the "
        "${IMAGE_TAG:-latest} interpolation selects the previous image."
    )
    assert src.index("RepoDigests") < src.index("docker-compose.prod.yml pull"), (
        "PREV_IMAGE must be captured BEFORE the pull — pulling first can retag "
        "and lose the running image's digest reference."
    )
    assert src.index("docker image prune -f") > src.index("_rollback_and_fail"), (
        "Prune must stay AFTER the smoke/rollback block so a failed deploy never "
        "deletes its own rollback image."
    )


def test_staging_drills_pin_deployed_prod_image() -> None:
    """staging-drills.yml must drill the image prod actually RUNS (the app
    container's RepoDigest), not :latest — after an auto-rollback prod runs
    :rollback while :latest IS the bad image, so a :latest drill would
    green-stamp behavior prod doesn't run."""
    src = _load_workflow("staging-drills.yml")
    assert "{{index .RepoDigests 0}}" in src, (
        "staging-drills.yml must resolve the prod app container's RepoDigest "
        "as the drill target (same inspect as deploy.yml's PREV_IMAGE capture)."
    )
    assert "STAGING_IMAGE=ghcr.io/reese8272/creatorclip:latest" not in src, (
        "staging-drills.yml must not hardcode STAGING_IMAGE=:latest — :latest "
        "diverges from prod after an auto-rollback and can race a main-push."
    )


def test_staging_drills_invoke_the_module_not_the_bare_path() -> None:
    """staging-drills.yml must run ``python -m scripts.drills``, never
    ``python scripts/drills.py``.

    The bare path puts scripts/ on sys.path[0], so the drill's ``import flags``
    resolves to scripts/flags.py — the ops CLI, which exposes ``_set_flag`` —
    instead of the root flags module's ``set_flag``. That exact shadowing turned
    the 2026-07-03 drills run red with ``AttributeError: module 'flags' has no
    attribute 'set_flag'``; it was fixed in ca3305c but nothing pinned it, so a
    future edit could silently reintroduce it. Parsed as YAML so the workflow's
    own explanatory comment (which names the anti-pattern) can't satisfy it.
    """
    drills = yaml.safe_load(_load_workflow("staging-drills.yml"))
    runs = " ".join(
        step["run"]
        for job in drills["jobs"].values()
        for step in job["steps"]
        if isinstance(step.get("run"), str)
    )
    assert "python -m scripts.drills" in runs, (
        "staging-drills.yml must invoke the drills as a module (-m scripts.drills) "
        "so the project root stays ahead of scripts/ on sys.path"
    )
    assert "scripts/drills.py" not in runs, (
        "bare-path invocation shadows the root flags module with scripts/flags.py "
        "— use `python -m scripts.drills` (see ca3305c)"
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


# ── Migration downgrade round-trip + irreversibility detector (Issue 296) ────


def _migration_lint_steps() -> list[dict]:
    ci = yaml.safe_load(_load_workflow("ci.yml"))
    return ci["jobs"]["migration-lint"]["steps"]


def test_migration_lint_has_downgrade_round_trip_after_squawk() -> None:
    """The migration-lint job must exercise downgrade reversibility ONLINE
    (Issue 296): after the Squawk lint, dump the schema, downgrade to the oldest
    changed migration's parent, re-upgrade to head, and require the schema dumps
    to be byte-identical. Offline --sql cannot execute data-dependent downgrades
    (e.g. 0035's placeholder stamping), so this must stay a live-DB step."""
    steps = _migration_lint_steps()
    names = [s.get("name", "") for s in steps]
    assert "Downgrade round-trip (Issue 296)" in names, (
        "ci.yml's migration-lint job must keep the 'Downgrade round-trip "
        "(Issue 296)' step — it is the only place downgrade() paths are executed."
    )
    assert names.index("Downgrade round-trip (Issue 296)") > names.index(
        "Lint changed migrations with Squawk"
    ), "The round-trip must run AFTER the Squawk lint (DB is at head by then)."
    run = steps[names.index("Downgrade round-trip (Issue 296)")]["run"]
    assert "alembic downgrade" in run and "alembic upgrade head" in run, (
        "The round-trip step must actually run `alembic downgrade` then "
        "`alembic upgrade head` against the throwaway Postgres."
    )
    assert "pg_dump --schema-only" in run, (
        "The round-trip must schema-dump before AND after — succeeding commands "
        "alone don't prove the schema round-trips."
    )
    assert "diff -u before.sql after.sql" in run, (
        "The schema-diff assertion (diff before.sql after.sql must be empty) is "
        "the load-bearing check — removing it reduces the gate to 'commands ran'."
    )


def test_migration_lint_runs_irreversibility_detector() -> None:
    """The detector step must invoke scripts/check_downgrades.py over the changed
    migration files, and the reviewed-exception allowlist it reads must exist
    with the 0014 seed (state backfill — Issue 98 banner-stuck bug)."""
    steps = _migration_lint_steps()
    detector = [s for s in steps if "check_downgrades" in s.get("run", "")]
    assert detector, (
        "ci.yml's migration-lint job must keep a step invoking "
        "scripts/check_downgrades.py — it is what fails the PR on a no-op or "
        "raising downgrade() outside the reviewed allowlist (Issue 296)."
    )
    allowlist = _REPO_ROOT / "alembic" / "DOWNGRADE_EXCEPTIONS"
    assert allowlist.exists(), (
        "alembic/DOWNGRADE_EXCEPTIONS must exist — scripts/check_downgrades.py "
        "reads it as the reviewed-exception allowlist."
    )
    assert "0014" in allowlist.read_text(), (
        "The 0014 onboarding-state backfill is the seeded irreversible exception "
        "— removing it makes any future change to 0014 fail the detector."
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


def test_coverage_job_runs_all_three_gates_in_one_required_invocation() -> None:
    """The coverage job must run coverage+module_coverage+diff_cover as ONE
    run_layer0.py invocation with all three in --require (Issue 479).

    The gates share docs/assessment/_coverage.xml. When ci.yml split them into
    two invocations, the script's end-of-run cleanup deleted the XML between
    steps, so the per-module floors and the diff/patch gate reported "skipped"
    with exit 0 on every PR from 2026-06-23 to 2026-08-12 — two supposedly
    active controls enforced nothing. This test pins both halves of the fix:
    the single invocation, and the skip-is-failure requirement.
    """
    ci = _load_workflow("ci.yml")
    layer0_lines = [
        ln.strip() for ln in ci.splitlines() if "run_layer0.py" in ln or "--gates" in ln
    ]
    gates_flags = [ln for ln in layer0_lines if ln.startswith("--gates")]
    coverage_invocations = [ln for ln in gates_flags if "coverage" in ln]
    assert coverage_invocations == ["--gates coverage,module_coverage,diff_cover"], (
        "ci.yml must invoke run_layer0.py exactly once for the coverage family, "
        "with --gates coverage,module_coverage,diff_cover — a split invocation "
        f"re-introduces the Issue-479 silent skip. Found: {coverage_invocations}"
    )
    assert "--require coverage,module_coverage,diff_cover" in ci, (
        "ci.yml's coverage job must pass --require coverage,module_coverage,diff_cover "
        "so a skipped gate fails the job instead of printing 'All runnable gates "
        "passed' (Issue 479)."
    )


def test_render_env_step_is_hard_and_guarded() -> None:
    """The unit job's render-env step (Issue 478) must stay HARD: a real
    ffmpeg install with no `::warning` escape hatch, a collect-count floor so
    the lane can never silently empty out again, and the actual lane run.

    Same pattern as the Issue-479 pin above: the lane sat at 0 selected tests
    from its introduction (hyphenated marker, unusable as a decorator) and CI
    printed green the whole time.
    """
    ci_doc = yaml.safe_load(_load_workflow("ci.yml"))
    steps = ci_doc["jobs"]["unit"]["steps"]
    render_steps = [s for s in steps if "Render-env lane" in (s.get("name") or "")]
    assert len(render_steps) == 1, "ci.yml's unit job must have exactly one render-env step"
    run = render_steps[0]["run"]

    assert "apt-get install -y --no-install-recommends ffmpeg" in run, (
        "the render-env step must hard-install ffmpeg (NOT preinstalled on ubuntu-latest)"
    )
    assert "::warning" not in run, (
        "the render-env step must have NO `::warning` fallback — a missing "
        "ffmpeg must FAIL this lane, never soft-skip it (Issue 478)"
    )
    assert "pytest -m render_env --collect-only -q | grep -c '::'" in run and "-ge 7" in run, (
        "the render-env step must keep the collect-count floor (>= 7: 3 ffmpeg "
        "tests + 4 reframe_seats tests, Issue 478 leg 3) so an emptied lane "
        "fails the job instead of passing vacuously — bump BOTH this pin and "
        "ci.yml together when adding lane tests"
    )
    assert "pytest -m render_env -q" in run, "the render-env step must actually run the lane"
    # Issue 478 leg 3: the reframe_seats test hard-asserts a working MediaPipe
    # detector, so the step must provision the stack — image-pinned installs
    # (with the Dockerfile's contrib force-reinstall, so contrib owns cv2), the
    # pinned hub model, and the env var pointing at it. All hard: no fallback.
    assert "pip install -r requirements-image.txt" in run, (
        "the render-env step must install requirements-image.txt (mediapipe)"
    )
    assert "--force-reinstall --no-deps opencv-contrib-python==" in run, (
        "contrib must be force-reinstalled LAST so it deterministically owns "
        "the cv2 module path (same ordering as the Dockerfile)"
    )
    assert "blaze_face_short_range.tflite" in run and "wget -q -O" in run, (
        "the render-env step must fetch the pinned BlazeFace hub model"
    )
    assert "MEDIAPIPE_FACE_MODEL_PATH=" in run, (
        "the render-env step must export MEDIAPIPE_FACE_MODEL_PATH so the real "
        "detector initializes (a missing model silently falls back to frame "
        "center everywhere else — here it must FAIL)"
    )
    assert "||" not in run, (
        "no soft-fallback anywhere in the render-env step — provisioning "
        "failures must fail the lane"
    )


def test_nightly_runs_transcription_live_leg() -> None:
    """The nightly workflow must actually run the transcription_live leg
    (Issue 481) — the marker + addopts exclusion alone would leave the live
    ASR test executing NOWHERE, the exact silent-empty-lane failure mode of
    Issue 478. Pins the file path, the marker, the gate env var, and the
    real secret.

    BOUNDARY (Issue 504): this pins WIRING, not EXECUTION. Every assertion here
    is a YAML string literal, and a workflow whose secret expands to "" satisfies
    all of them while skipping every test. Execution is asserted by
    ``test_every_live_lane_asserts_a_minimum_executed_count`` below; neither test
    is sufficient alone.
    """
    nightly = _load_workflow("llm-e2e-nightly.yml")
    assert "tests/ingestion/test_transcription_live.py" in nightly, (
        "llm-e2e-nightly.yml must run the transcription_live test file"
    )
    assert "-m transcription_live" in nightly.replace("\\\n", " ").replace("  ", " "), (
        "the nightly must select the transcription_live marker explicitly"
    )
    assert 'RUN_TRANSCRIPTION_LIVE: "1"' in nightly, (
        "the nightly must set RUN_TRANSCRIPTION_LIVE=1 — without it the live "
        "test skips and the leg silently stops running"
    )
    assert "secrets.DEEPGRAM_API_KEY" in nightly, (
        "the nightly must inject the real DEEPGRAM_API_KEY secret"
    )


def test_llm_nightly_runs_scoring_behavioral_lane() -> None:
    """The nightly live-LLM workflow must run the Issue-476 scoring lane.

    llm-e2e-nightly.yml hardcodes its pytest path; before Issue 476 it ran ONLY
    tests/test_llm_live.py, so the clip scorer — the single decision-maker for
    what ships and which principle it cites — had no behavioral gate anywhere.
    This pin makes silently dropping the scoring lane from the nightly a CI
    failure, the same pattern as the render-env and coverage pins above.
    """
    src = _load_workflow("llm-e2e-nightly.yml")
    assert "tests/test_llm_live_scoring.py" in src, (
        "llm-e2e-nightly.yml's pytest invocation must include "
        "tests/test_llm_live_scoring.py — the Issue-476 scoring behavioral lane "
        "(setup-vs-aftermath ordering, shape/principle/range, dna_score ordering) "
        "runs nowhere else."
    )
    assert "tests/test_llm_live.py" in src, (
        "llm-e2e-nightly.yml must keep tests/test_llm_live.py — adding the "
        "scoring lane must not displace the Issue-319 feature-module lane."
    )
    assert "Scoring behavioral lane" in src, (
        "llm-e2e-nightly.yml's step summary must include the '## Scoring "
        "behavioral lane' section so the nightly posts the Issue-476 margins."
    )


# ── The live lanes must prove they EXECUTED, not merely exit 0 (Issue 504) ────


def test_nightly_fails_loudly_on_an_empty_api_key_secret() -> None:
    """`${{ secrets.X }}` expands to "" when the secret is unset/renamed/blanked.

    Both live lanes gate on bool(KEY), so an empty secret skips every test,
    pytest exits 0, and the nightly goes green having made zero live calls —
    while docs/GO_LIVE.md cites it as proof the external APIs are live. A
    preflight `test -n` turns that into a loud, early failure.
    """
    src = _load_workflow("llm-e2e-nightly.yml")
    for key in ("ANTHROPIC_API_KEY", "DEEPGRAM_API_KEY"):
        assert f'[ -n "${key}" ]' in src, (
            f"llm-e2e-nightly.yml must assert {key} is non-empty before running "
            "the live lanes — an empty secret is a silently green nightly"
        )


def test_every_live_lane_asserts_a_minimum_executed_count() -> None:
    """Every pytest invocation in the nightly must be paired with the guard.

    Derived, not listed (the house rule): the lanes are DISCOVERED by scanning
    the workflow for `python -m pytest`, so adding a third live lane without an
    executed-count guard fails here instead of shipping a third way to report
    green over zero live calls.

    A collect-only count would NOT do: skipif is evaluated after collection, so
    a fully-skipped lane still reports its full collected count. Only the JUnit
    report separates "ran and passed" from "skipped and exited 0".
    """
    src = _load_workflow("llm-e2e-nightly.yml")

    lanes = src.count("python -m pytest")
    assert lanes >= 2, (
        f"expected at least the LLM and ASR live lanes, found {lanes} pytest "
        "invocations — did a lane get renamed or dropped?"
    )

    guarded = src.count("scripts/assert_tests_executed.py")
    assert guarded == lanes, (
        f"{lanes} pytest lane(s) but {guarded} executed-count guard(s) — every "
        "live lane must assert what it EXECUTED, not just its exit code"
    )

    junit = src.count("--junitxml=")
    assert junit == lanes, (
        f"{lanes} pytest lane(s) but {junit} --junitxml flag(s) — the guard reads "
        "the JUnit report, so a lane without one cannot be checked"
    )

    floors = re.findall(r"scripts/assert_tests_executed\.py\s+\S+\s+(\d+)", src)
    assert len(floors) == lanes, "every guard invocation must pass an explicit floor"
    assert all(int(f) >= 1 for f in floors), (
        f"a floor of 0 asserts nothing — floors found: {floors}"
    )


def test_render_env_marker_excluded_from_default_lane() -> None:
    """pytest.ini must keep the underscored `render_env` marker registered and
    excluded from the default addopts lane. The hyphenated `render-env`
    spelling can never be applied as a decorator — reverting the rename
    re-empties the lane (Issue 478)."""
    pytest_ini = (_REPO_ROOT / "pytest.ini").read_text()
    assert "render_env:" in pytest_ini, "pytest.ini must register the render_env marker"
    assert "render-env:" not in pytest_ini, (
        "the marker must stay underscored — @pytest.mark.render-env is a syntax error"
    )
    assert "not render_env" in pytest_ini, (
        "addopts must exclude render_env from the default lane; the dedicated "
        "CI step is what runs it"
    )


def test_eval_commit_status_targets_the_pr_head_sha() -> None:
    """The `eval/clip-quality` commit status must be posted to the PR HEAD sha.

    On a `pull_request` event `context.sha` is the ephemeral refs/pull/N/merge
    commit, but branch protection evaluates required contexts on the HEAD commit.
    Posting to `context.sha` therefore lands the status on a commit nothing
    inspects, and any ruleset requiring `eval/clip-quality` hangs every PR on
    "Expected — Waiting for status to be reported". Measured on PR #100: head
    cf9fcca carried 0 statuses while merge 483ad33 carried the success.

    Issue 265 chose a commit status precisely so protection COULD require it, so
    this is the assertion that keeps that promise true.
    """
    ci = _load_workflow("ci.yml")
    posts = ci.count("context: 'eval/clip-quality'")
    assert posts == 3, (
        f"expected 3 eval/clip-quality post sites (success/failure/skipped), found {posts} "
        "— if a branch was added or removed, update this pin with it"
    )
    head_sha_expr = "context.payload.pull_request?.head?.sha ?? context.sha"
    assert ci.count(f"sha: {head_sha_expr},") == 3, (
        "every eval/clip-quality post site must target the PR head sha via "
        f"`{head_sha_expr}` — a bare `context.sha` posts to the merge commit, "
        "which branch protection never looks at"
    )


def test_local_ci_does_not_override_the_default_marker_lane() -> None:
    """scripts/ci_local.sh must invoke pytest BARE, never with a `-m` override.

    A `-m` on the command line REPLACES pytest.ini's addopts `-m` instead of
    intersecting with it, so `pytest -m "not integration"` re-selects every lane
    addopts deliberately excludes — render_env (real ffmpeg + mediapipe),
    llm_live and transcription_live (real API keys). None are available on a dev
    box, so the pre-push hook (Layer 1) errors for everyone and the only way to
    push becomes --no-verify — the gate defeated rather than passed.
    """
    ci_local = (_REPO_ROOT / "scripts" / "ci_local.sh").read_text()
    unit_gate = ci_local.split("gate_unit()", 1)[1].split("\n}", 1)[0]
    assert '-m "not integration"' not in unit_gate and "-m 'not integration'" not in unit_gate, (
        "gate_unit must run pytest bare so pytest.ini's addopts governs the lane; "
        "a -m override REPLACES addopts and drags in render_env/llm_live/"
        "transcription_live, which no dev machine can run"
    )
    assert "pytest -q" in unit_gate, "gate_unit must still actually invoke pytest"


# ── Issue 500: every run_layer0.py invocation must carry --require ────────────


_LAYER0_SCRIPT = "run_layer0.py"
# Gates exempt from --require, with the reason. `freshness` is warn-only by
# design and carries its own --require-fresh flag, so requiring it would turn a
# scheduled advisory into a blocking PR failure.
_REQUIRE_EXEMPT_GATES = {"freshness"}


def _layer0_invocations() -> list[tuple[str, str]]:
    """Every run_layer0.py invocation in the repo, as (source file, command).

    Discovered by scanning rather than listed (the "scan, don't list" house
    rule): a hand-maintained list of call sites is exactly how the coverage job
    was hardened in Issue 479 while `static-gates` and `ci_local.sh` were left
    behind for three months.
    """
    found: list[tuple[str, str]] = []
    roots = [_REPO_ROOT / ".github" / "workflows", _REPO_ROOT / "scripts"]
    for root in roots:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in {".yml", ".yaml", ".sh"}:
                continue
            text = path.read_text()
            if _LAYER0_SCRIPT not in text:
                continue
            # Drop comment lines FIRST. Both YAML and shell use `#`, and the
            # rationale comments around these very invocations name the script
            # and its flags in prose — scanning them would parse English as
            # arguments.
            code_lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
            # Shell callers assign the path to a variable and invoke through it
            # (ci_local.sh: LAYER0="...run_layer0.py" then `python3 "$LAYER0"`).
            # Splitting on the literal filename alone finds the ASSIGNMENT and
            # misses every real invocation — which made this assertion pass
            # vacuously for ci_local.sh until it was caught here. Resolve the
            # alias so the call sites are actually scanned.
            aliases = [_LAYER0_SCRIPT]
            for line in code_lines:
                if _LAYER0_SCRIPT in line and "=" in line:
                    name = line.split("=", 1)[0].strip()
                    if name.isidentifier():
                        aliases.append(f'"${name}"')

            # Join continuation lines so a YAML folded block or a shell
            # invocation reads as one command string.
            flat = "\n".join(code_lines).replace("\\\n", " ").replace("\n", " ")
            for alias in aliases:
                for chunk in flat.split(alias)[1:]:
                    if not chunk.lstrip().startswith("-"):
                        continue  # an assignment or a bare mention, not a call
                    # The command ends at the next step/keyword boundary.
                    command = chunk.split(" - name:")[0].split("; then")[0]
                    found.append((str(path.relative_to(_REPO_ROOT)), command))
    return found


def test_layer0_invocations_are_discoverable() -> None:
    """Guard the scanner itself: zero hits would make every assertion below vacuous."""
    invocations = _layer0_invocations()
    assert len(invocations) >= 3, (
        f"expected at least 3 run_layer0.py invocations (ci.yml coverage + "
        f"static-gates, ci_local.sh), found {len(invocations)}. If the call "
        "sites moved, fix this scanner — do not delete the assertion."
    )


def test_every_layer0_invocation_requires_its_gates() -> None:
    """A gate that did not run must never pass silently (Issue 500).

    ``--require`` escalates a `skipped` status to a hard failure. Without it a
    missing tool or unparseable output is reported green — which is how the
    per-module coverage floors and the patch gate went unenforced on every PR
    from 2026-06-23 to 2026-08-12.
    """
    for source, command in _layer0_invocations():
        if "--gates" not in command:
            continue  # a bare invocation runs everything; not a scoped gate job
        gates_arg = command.split("--gates", 1)[1].strip().split()[0]
        gates = {g for g in gates_arg.split(",") if g}
        must_require = gates - _REQUIRE_EXEMPT_GATES
        if not must_require:
            continue

        # `--require-coverage` is the documented shorthand: run_layer0.main()
        # does `if args.require_coverage: required.add("coverage")`. Treat it as
        # satisfying that one gate rather than flagging a real call site as a gap.
        if "--require-coverage" in command:
            must_require -= {"coverage"}
            if not must_require:
                continue

        assert "--require" in command, (
            f"{source} invokes run_layer0.py with --gates {gates_arg} but no "
            f"--require. A skipped gate there reports green (Issue 500)."
        )
        required_arg = command.split("--require", 1)[1].strip().split()[0]
        # ci_local.sh passes a shell variable; it is required by construction.
        if required_arg.startswith("$") or required_arg.startswith('"$'):
            continue
        required = {g for g in required_arg.split(",") if g}
        missing = must_require - required
        assert not missing, (
            f"{source}: gate(s) {sorted(missing)} are selected but not required, "
            f"so a skip of them still passes. Either add them to --require or "
            f"add them to _REQUIRE_EXEMPT_GATES with a stated reason."
        )


# ── 2026-08-18: no CI step may hang unbounded on a package mirror ─────────────


def test_every_apt_invocation_is_time_bounded() -> None:
    """A stalled apt mirror must fail fast, not hang until the job timeout.

    `sudo -n` (already present) only prevents a hang on a *password prompt*. It
    does nothing for a slow or unreachable mirror. This step normally runs in
    ~27s; on 2026-08-17 and twice on 2026-08-18 it sat for 20-66 minutes with no
    path to finishing, and the recorded remedy was a human noticing and
    re-running the job (LEFT_OFF gotcha 18). Three occurrences of a known failure
    mode whose only mitigation is a manual ritual is the exact shape this lane
    exists to retire.

    Scanned rather than listed: there are 14 apt invocations across 7 steps, and
    a hand-maintained list of them would drift the first time a job is added.
    """
    src = _load_workflow("ci.yml")
    unbounded = [
        line.strip()
        for line in src.splitlines()
        if "apt-get" in line and not line.lstrip().startswith("#") and "timeout " not in line
    ]
    assert not unbounded, (
        "unbounded apt invocation(s) in ci.yml — prefix each with `timeout N` so "
        f"a stalled mirror fails instead of hanging the job: {unbounded}"
    )


def test_apt_invocations_are_discoverable() -> None:
    """Guard the scanner: zero apt lines would make the assertion above vacuous."""
    src = _load_workflow("ci.yml")
    apt_lines = [
        line for line in src.splitlines() if "apt-get" in line and not line.lstrip().startswith("#")
    ]
    assert len(apt_lines) >= 10, (
        f"expected 10+ apt invocations in ci.yml, found {len(apt_lines)}. If the "
        "install steps moved, fix this scanner — do not delete the assertion."
    )


# ── No load-bearing CI step may swallow its exit code (Issue 502) ─────────────
#
# The mutation gate — the only mechanism measuring whether the tests ASSERT
# rather than merely execute — was `mutmut run || true`. It reported success on
# 8/8 weekly runs having never executed a single mutant.
#
# A blanket ban would be wrong and would be deleted the first time someone hit a
# legitimate use, so the rule is narrower: a `|| true` that DISCARDS a verdict
# needs a written reason. Two forms are exempt by construction:
#
#   1. Capture — `X=$(cmd || true)`. The failure is not swallowed, it is turned
#      into an empty string that the next line inspects. Handling, not hiding.
#   2. An allowlisted step, each carrying a reason below.

_SWALLOWED_EXIT_ALLOWLIST: dict[tuple[str, str], str] = {
    ("ci.yml", "--tb=no -q || true"): (
        "The flake-detection job is non-gating by design and by name: it re-runs the "
        "suite with --reruns 1 purely to surface tests that pass only on retry. A red "
        "test there must not fail the job, or the flake report becomes a second gate. "
        "RESIDUAL, logged not fixed: if pytest dies at COLLECTION the report file is "
        "absent and the summary step prints 'No flake report found' and exits 0 — the "
        "same empty-report-reads-as-clean shape as the mutmut defect this issue fixed."
    ),
    ("deploy.yml", "logs --tail 200 app worker || true"): (
        "Diagnostic dump on the failure path. If log collection fails we still want "
        "the real error from the step that actually failed, not this one."
    ),
    ("deploy.yml", "stop app worker || true"): (
        "Best-effort teardown of the staging stack. Stopping an already-stopped "
        "container is not a deploy failure."
    ),
    ("deploy.yml", 'docker pull "${PREV_IMAGE}" || true'): (
        "Rollback path. Every step here is best-effort by necessity: we are already "
        "in a failure, and a rollback that aborts halfway is worse than one that "
        "pushes on. The health check after it is the real verdict."
    ),
    ("deploy.yml", 'docker tag "${PREV_IMAGE}" ghcr.io/reese8272/creatorclip:rollback || true'): (
        "Rollback path — see the docker pull entry above. Tagging may fail if the "
        "pull did; the subsequent health check is what decides the outcome."
    ),
    ("deploy.yml", "down --timeout 30 || true"): (
        "Rollback path. Bringing down a stack that is already down must not abort "
        "the rollback before it brings the previous image back up."
    ),
    ("deploy.yml", "IMAGE_TAG=rollback docker compose -f docker-compose.prod.yml up -d || true"): (
        "Rollback path. A failure here is caught by the post-rollback health check, "
        "which reports the true state; aborting on this line would skip that check."
    ),
    ("staging-drills.yml", "logs --tail 150 app worker || true"): (
        "Diagnostic dump on the failure path — same reasoning as deploy.yml's."
    ),
    ("staging-drills.yml", "stop app worker || true"): (
        "Best-effort teardown of the staging stack — same reasoning as deploy.yml's."
    ),
}


def _swallows_exit_code(line: str) -> bool:
    """True if this line discards a command's verdict via `|| true`.

    False for the capture form `X=$(cmd || true)`, including the multi-line
    spelling where the closing paren lands on this line.
    """
    if "|| true" not in line or line.strip().startswith("#"):
        return False
    if "|| true)" in line:
        return False
    before = line[: line.index("|| true")]
    return before.count("$(") <= before.count(")")


def _bare_swallows() -> list[tuple[str, int, str]]:
    out: list[tuple[str, int, str]] = []
    for wf in sorted(_WORKFLOWS.glob("*.yml")):
        for lineno, line in enumerate(wf.read_text().splitlines(), 1):
            if _swallows_exit_code(line):
                out.append((wf.name, lineno, line.strip()))
    return out


def test_no_load_bearing_step_swallows_its_exit_code() -> None:
    """Every `|| true` outside a capture must be on the allowlist, with a reason.

    `mutmut run || true` is why this exists: the run crashed during stats
    collection and the workflow reported success on 8/8 weekly runs, having never
    executed a mutant.
    """
    unexplained: list[str] = []
    for name, lineno, line in _bare_swallows():
        if not any(wf == name and needle in line for (wf, needle) in _SWALLOWED_EXIT_ALLOWLIST):
            unexplained.append(f"{name}:{lineno}: {line}")

    assert not unexplained, (
        "CI step(s) discarding a command's exit code with no recorded reason:\n  "
        + "\n  ".join(unexplained)
        + "\n\nEither stop swallowing it, or add an entry to "
        "_SWALLOWED_EXIT_ALLOWLIST explaining why the verdict does not matter. "
        "'It was failing' is not a reason."
    )


def test_the_swallow_scan_finds_the_known_sites() -> None:
    """Anti-vacuity: a scanner matching nothing would approve everything."""
    found = _bare_swallows()
    assert len(found) >= 9, (
        f"the `|| true` scan found only {len(found)} site(s) — it has stopped "
        "matching, and an empty scan passes the test above vacuously"
    )


def test_no_stale_swallowed_exit_allowlist_entry() -> None:
    """The reverse arm: an allowlist entry no workflow line matches any more."""
    lines = _bare_swallows()
    stale = [
        f"{wf}: {needle}"
        for (wf, needle) in _SWALLOWED_EXIT_ALLOWLIST
        if not any(name == wf and needle in line for name, _lineno, line in lines)
    ]
    assert not stale, (
        f"allowlist entr(ies) matching no workflow line: {stale}. Remove them — a "
        "stale entry pre-approves any future step that happens to match."
    )


def test_every_swallow_allowlist_entry_carries_a_reason() -> None:
    """Membership is not a justification. Each entry must explain itself."""
    for key, reason in _SWALLOWED_EXIT_ALLOWLIST.items():
        assert len(reason.strip()) >= 60, f"{key}'s reason is too thin to be a decision"


# ── The required-context list must name real jobs (Issue 502) ────────────────
#
# Nothing machine-checked this. `docs/BRANCHING.md` carries the 8 required
# contexts TWICE — a bullet list and the restore-it-verbatim JSON — and both are
# hand-maintained, so a job rename in ci.yml silently turns a required check into
# a context that can never report. GitHub treats a never-reported required check
# as pending, so the practical failure is a permanently unmergeable branch; the
# quieter one is a context removed from protection while the docs still promise it.

_BRANCHING_DOC = _REPO_ROOT / "docs" / "BRANCHING.md"

# Contexts that are NOT ci.yml jobs, with the reason each is posted differently.
_NON_JOB_CONTEXTS: dict[str, str] = {
    "eval/clip-quality": (
        "A commit status posted via the GitHub API, not a job name (Issue 265): a "
        "SKIPPED required job reports 'success' to branch protection, so a path-"
        "filtered eval gate had to become a status to report its real outcome."
    ),
}


def _declared_required_contexts() -> list[str]:
    """The contexts from BRANCHING.md's restore-it-verbatim protection JSON."""
    import json

    src = _BRANCHING_DOC.read_text()
    start = src.index('"required_status_checks"')
    block = src[src.index("{", src.rindex("<<'JSON'", 0, start)) :]
    depth, end = 0, None
    for i, ch in enumerate(block):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    assert end is not None, "could not find the protection JSON block in BRANCHING.md"
    return json.loads(block[:end])["required_status_checks"]["contexts"]


def test_every_required_context_names_a_real_ci_job() -> None:
    """A required context that matches no job name can never report."""
    workflow = yaml.safe_load(_load_workflow("ci.yml"))
    job_names = {job.get("name") for job in workflow["jobs"].values()}

    contexts = _declared_required_contexts()
    assert len(contexts) >= 8, f"expected >=8 required contexts, parsed {contexts}"

    orphans = [c for c in contexts if c not in job_names and c not in _NON_JOB_CONTEXTS]
    assert not orphans, (
        f"required status check(s) matching no ci.yml job name: {orphans}. A renamed "
        f"job leaves the context permanently pending and the branch unmergeable. "
        f"Known job names: {sorted(n for n in job_names if n)}"
    )


def test_the_two_copies_of_the_required_context_list_agree() -> None:
    """BRANCHING.md states the contexts twice; drift between them is the whole risk."""
    src = _BRANCHING_DOC.read_text()
    # Each bullet leads with the context in backticks; trailing prose varies
    # (`eval/clip-quality` carries "(commit status, not job) — Issue 265: ...").
    bulleted = set(re.findall(r"^- `([^`]+)`", src, re.MULTILINE))
    declared = set(_declared_required_contexts())

    missing = declared - bulleted
    assert not missing, (
        f"context(s) in BRANCHING.md's protection JSON but absent from its bullet "
        f"list: {sorted(missing)} — the two copies have drifted"
    )


def test_no_ci_job_claims_to_be_gating_without_being_required() -> None:
    """A comment saying GATING over a job nothing requires is a false claim.

    `Visual regression` carried "GATING since 2026-07-29" while sitting outside the
    required-context set, so a regression there blocked nothing. Documented-but-
    unenforced is the exact posture Lane L30 exists to retire.
    """
    src = _load_workflow("ci.yml")
    workflow = yaml.safe_load(src)
    required = set(_declared_required_contexts())

    lines = src.splitlines()
    offenders: list[str] = []
    for key, job in workflow["jobs"].items():
        name = job.get("name")
        if not name or name in required:
            continue
        # The job's own comment block: lines between its key and its `name:`.
        try:
            start = next(i for i, ln in enumerate(lines) if ln.strip() == f"{key}:")
            end = next(i for i in range(start, len(lines)) if lines[i].strip().startswith("name:"))
        except StopIteration:  # pragma: no cover - malformed workflow fails elsewhere
            continue
        block = "\n".join(lines[start:end])
        if re.search(r"\bGATING\b", block):
            offenders.append(f"{key} ({name})")

    assert not offenders, (
        f"job(s) whose comment claims GATING but which are not required contexts: "
        f"{offenders}. Either add the context to branch protection and to "
        f"docs/BRANCHING.md, or delete the claim."
    )
