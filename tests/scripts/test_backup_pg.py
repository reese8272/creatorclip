"""Guards for scripts/backup_pg.sh (Issue 256).

The #1 trap in a backup script is leaking a secret (the encryption passphrase, the
DB password, or R2 creds) into argv / stdout / a log. These tests lock in the
secret-hygiene properties statically, and exercise the fail-fast paths at runtime
(both reachable before any `docker`/`aws` call, so no live infra is needed).
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent.parent
_SCRIPT = _REPO / "scripts" / "backup_pg.sh"
_TEXT = _SCRIPT.read_text()

_CANARY = "secret-canary-DO-NOT-LEAK"


def test_script_exists_and_is_executable() -> None:
    assert _SCRIPT.exists(), "scripts/backup_pg.sh must exist"
    assert os.access(_SCRIPT, os.X_OK), "scripts/backup_pg.sh must be executable (chmod +x)"


def test_passphrase_read_from_env_never_argv() -> None:
    """openssl must take the passphrase via `-pass env:` (read from the environment),
    never as a CLI argument where `ps`/shell history could capture it."""
    assert "-pass env:BACKUP_ENCRYPTION_KEY" in _TEXT
    # The literal forms that would put the secret on the command line.
    assert "-pass pass:" not in _TEXT, "never pass the passphrase as a literal CLI arg"
    assert "-k " not in _TEXT, "never pass the key via openssl -k (argv-visible)"


def test_never_echoes_secrets() -> None:
    """No code path prints the passphrase, DB password, or R2 secret value."""
    for secret_var in ("BACKUP_ENCRYPTION_KEY", "POSTGRES_PASSWORD", "R2_SECRET_ACCESS_KEY", "PGPASSWORD"):
        assert f'echo "${secret_var}' not in _TEXT
        assert f'echo ${secret_var}' not in _TEXT
        assert f'printf "%s" "${secret_var}"' not in _TEXT
    # `set -x` would trace every expanded value (incl. secrets) into the log.
    # Check executable lines only — the string is allowed inside an explanatory comment.
    code_lines = [ln.strip() for ln in _TEXT.splitlines() if not ln.lstrip().startswith("#")]
    assert not any(ln == "set -x" or ln.startswith("set -x ") or " set -x" in ln for ln in code_lines)


def test_db_password_handled_container_side() -> None:
    """pg_dump runs inside the postgres container reading the container's own
    POSTGRES_* env — the DB password never enters this host script's argv."""
    assert 'PGPASSWORD="$POSTGRES_PASSWORD"' in _TEXT
    assert "docker compose" in _TEXT and "pg_dump" in _TEXT


def test_targets_separate_backup_bucket_not_media_bucket() -> None:
    """Uploads must target BACKUP_R2_BUCKET, and the script must refuse to run if it
    equals the media bucket (R2_BUCKET) — the 3-2-1 isolation invariant."""
    assert 's3://${BACKUP_R2_BUCKET}' in _TEXT
    assert '"$BACKUP_R2_BUCKET" = "$R2_BUCKET"' in _TEXT, "must guard BACKUP_R2_BUCKET != R2_BUCKET"


def test_no_client_side_deletes() -> None:
    """Retention is enforced by the R2 Lifecycle rule (Issue 258), never by the
    script — so a bug here can never mass-delete backups."""
    assert "aws s3 rm" not in _TEXT
    assert "delete-object" not in _TEXT


def _run(env_body: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    """Run the script against a temp ENV_FILE with a deliberately minimal process env."""
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as fh:
        fh.write(env_body)
        env_path = fh.name
    proc_env = {"PATH": os.environ.get("PATH", ""), "ENV_FILE": env_path}
    if extra_env:
        proc_env.update(extra_env)
    try:
        return subprocess.run(
            ["bash", str(_SCRIPT)],
            env=proc_env,
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        os.unlink(env_path)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_fails_fast_on_missing_config_without_leaking() -> None:
    """With required config absent the script aborts (non-zero) naming the missing
    var — and never prints the one secret value that WAS present."""
    result = _run(f"BACKUP_ENCRYPTION_KEY={_CANARY}\n")
    assert result.returncode != 0
    assert "BACKUP_R2_BUCKET is not set" in result.stderr
    combined = result.stdout + result.stderr
    assert _CANARY not in combined, "the passphrase value must never appear in output"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_refuses_when_backup_bucket_equals_media_bucket() -> None:
    """All creds present but BACKUP_R2_BUCKET == R2_BUCKET → abort before any upload."""
    body = (
        "BACKUP_R2_BUCKET=same-bucket\n"
        "R2_BUCKET=same-bucket\n"
        f"BACKUP_ENCRYPTION_KEY={_CANARY}\n"
        "R2_ACCOUNT_ID=acct\n"
        "R2_ACCESS_KEY_ID=akid\n"
        "R2_SECRET_ACCESS_KEY=secret\n"
    )
    result = _run(body)
    assert result.returncode != 0
    assert "must differ from R2_BUCKET" in result.stderr
    assert _CANARY not in (result.stdout + result.stderr)
