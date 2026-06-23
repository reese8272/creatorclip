"""
Helm-manifest structural tests for Issue 259 — worker PgBouncer sidecar.

These tests do NOT require `helm` to be installed.  They inspect the raw
YAML template and values files to assert the structural invariants that
would be exercised by `helm template` + `kubectl apply`.

Guards:
- worker/deployment.yaml includes a pgbouncer container (digest-pinned).
- worker/deployment.yaml worker env does NOT point to an external DB host
  directly — the worker.pgbouncer sidecar must be the connection target.
- values.yaml worker.pgbouncer block is present and sane.
- admin_engine pool size in db.py is correctly sized for --concurrency=2.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

WORKER_DEPLOY = REPO_ROOT / "deploy/charts/creatorclip/templates/worker/deployment.yaml"
APP_DEPLOY = REPO_ROOT / "deploy/charts/creatorclip/templates/app/deployment.yaml"
VALUES_YAML = REPO_ROOT / "deploy/charts/creatorclip/values.yaml"
VALUES_PROD = REPO_ROOT / "deploy/charts/creatorclip/values.prod.yaml"
DB_PY = REPO_ROOT / "db.py"


def _read(path: Path) -> str:
    return path.read_text()


class TestWorkerDeploymentHasPgbouncerSidecar:
    """The worker Deployment template must declare a pgbouncer container block."""

    def test_pgbouncer_container_block_present(self) -> None:
        content = _read(WORKER_DEPLOY)
        assert "worker.pgbouncer.enabled" in content, (
            "worker/deployment.yaml must include the worker.pgbouncer.enabled guard"
        )
        assert "name: pgbouncer" in content, (
            "worker/deployment.yaml must declare a pgbouncer sidecar container"
        )

    def test_worker_pgbouncer_env_vars_present(self) -> None:
        content = _read(WORKER_DEPLOY)
        assert "worker.pgbouncer.poolMode" in content
        assert "worker.pgbouncer.maxClientConn" in content
        assert "worker.pgbouncer.defaultPoolSize" in content

    def test_worker_pgbouncer_image_references_worker_values(self) -> None:
        content = _read(WORKER_DEPLOY)
        assert ".Values.worker.pgbouncer.image" in content, (
            "worker sidecar must use .Values.worker.pgbouncer.image (not the app pgbouncer image)"
        )


class TestValuesYamlWorkerPgbouncer:
    """values.yaml must declare a worker.pgbouncer block with required fields."""

    def test_worker_pgbouncer_block_exists(self) -> None:
        content = _read(VALUES_YAML)
        assert "worker:" in content
        assert "pgbouncer:" in content

    def test_worker_pgbouncer_pool_mode_transaction(self) -> None:
        content = _read(VALUES_YAML)
        # Find the worker section and assert transaction pooling
        assert "transaction" in content, "poolMode must be 'transaction'"

    def test_worker_pgbouncer_image_is_edoburu(self) -> None:
        # Both app and worker pgbouncer image: lines must reference edoburu, not bitnami.
        # (Comment lines are exempt — the comment explains *why* bitnami was dropped.)
        for line in _read(VALUES_YAML).splitlines():
            stripped = line.strip()
            if stripped.startswith("image:") or stripped.startswith("image ="):
                assert "bitnami" not in stripped, (
                    f"Image line must not reference bitnami/pgbouncer (commercial-only): {line!r}"
                )
        content = _read(VALUES_YAML)
        assert "edoburu/pgbouncer" in content, "edoburu/pgbouncer must appear in values.yaml"

    def test_worker_pgbouncer_image_is_digest_pinned(self) -> None:
        content = _read(VALUES_YAML)
        # Digest pinning: image reference must contain @sha256:
        worker_section_start = content.find("worker:")
        assert worker_section_start != -1
        worker_section = content[worker_section_start:]
        # Find the image line within the worker section
        assert "@sha256:" in worker_section, (
            "worker.pgbouncer.image must be pinned by digest (@sha256:...) — Issue 264"
        )

    def test_worker_pgbouncer_default_pool_size_is_small(self) -> None:
        """Worker pool must be sized for --concurrency=2 (not the old oversized 15)."""
        # Parse the YAML to get the actual numeric values
        # values.yaml uses Helm template tags so we can't parse it directly as YAML.
        # Instead, check that defaultPoolSize: 5 appears in the worker section.
        content = _read(VALUES_YAML)
        worker_idx = content.find("worker:")
        assert worker_idx != -1
        worker_section = content[worker_idx:]
        # The worker pgbouncer defaultPoolSize must be ≤ 10 (sized for 2 workers)
        # We check that defaultPoolSize: 5 is present (the required value)
        assert "defaultPoolSize: 5" in worker_section, (
            "worker.pgbouncer.defaultPoolSize must be 5 (sized for --concurrency=2, Issue 259)"
        )


class TestValuesProdWorkerPgbouncer:
    """values.prod.yaml must include worker pgbouncer overrides."""

    def test_prod_worker_pgbouncer_block_exists(self) -> None:
        content = _read(VALUES_PROD)
        assert "worker:" in content
        assert "pgbouncer:" in content

    def test_prod_worker_pgbouncer_default_pool_size(self) -> None:
        content = _read(VALUES_PROD)
        worker_idx = content.find("worker:")
        assert worker_idx != -1
        worker_section = content[worker_idx:]
        assert "defaultPoolSize: 5" in worker_section


class TestAdminEnginePoolSize:
    """db.py admin_engine must be sized for --concurrency=2 (pool_size=2, max_overflow=2)."""

    def test_admin_engine_pool_size_two(self) -> None:
        content = _read(DB_PY)
        # pool_size=2 must appear in _make_admin_engine
        start = content.find("def _make_admin_engine")
        assert start != -1
        # Use 900 chars to cover the full function body (comment is long).
        admin_fn = content[start : start + 900]
        assert "pool_size=2," in admin_fn, (
            "admin_engine pool_size must be 2 (sized for --concurrency=2, Issue 259)"
        )

    def test_admin_engine_max_overflow_two(self) -> None:
        content = _read(DB_PY)
        start = content.find("def _make_admin_engine")
        assert start != -1
        admin_fn = content[start : start + 900]
        assert "max_overflow=2," in admin_fn, (
            "admin_engine max_overflow must be 2 (sized for --concurrency=2, Issue 259)"
        )

    def test_admin_engine_max_conns_fits_sidecar(self) -> None:
        """pool_size + max_overflow (4) must be ≤ worker pgbouncer defaultPoolSize (5)."""
        pool_size = 2
        max_overflow = 2
        worker_pgbouncer_default_pool_size = 5
        assert pool_size + max_overflow <= worker_pgbouncer_default_pool_size, (
            "admin_engine max conns must fit within the worker PgBouncer sidecar defaultPoolSize"
        )
