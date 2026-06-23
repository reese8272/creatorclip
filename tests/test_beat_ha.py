"""
Unit tests for Issue 263 — Beat HA: RedBeat scheduler and beat liveness probe.

All tests are DB-free and do not require Helm installed.  They inspect:
  - The Celery config to confirm beat_scheduler and redbeat_redis_url are set.
  - The beat deployment YAML to confirm a livenessProbe is present.
  - requirements.txt to confirm celery-redbeat is pinned.
  - .env.example to confirm REDBEAT_REDIS_URL is documented.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

BEAT_DEPLOY = REPO_ROOT / "deploy/charts/creatorclip/templates/beat/deployment.yaml"
VALUES_YAML = REPO_ROOT / "deploy/charts/creatorclip/values.yaml"
VALUES_PROD = REPO_ROOT / "deploy/charts/creatorclip/values.prod.yaml"
REQUIREMENTS = REPO_ROOT / "requirements.txt"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
CELERY_APP = REPO_ROOT / "worker/celery_app.py"
CONFIG_PY = REPO_ROOT / "config.py"


def _read(path: Path) -> str:
    return path.read_text()


class TestCeleryRedBeatConfig:
    """worker/celery_app.py must configure RedBeat as the beat scheduler."""

    def test_beat_scheduler_is_redbeat(self) -> None:
        content = _read(CELERY_APP)
        assert 'beat_scheduler="redbeat.RedBeatScheduler"' in content, (
            "celery_app must set beat_scheduler to redbeat.RedBeatScheduler (Issue 263)"
        )

    def test_redbeat_redis_url_set(self) -> None:
        content = _read(CELERY_APP)
        assert "redbeat_redis_url" in content, (
            "celery_app must configure redbeat_redis_url (Issue 263)"
        )

    def test_redbeat_redis_url_from_settings(self) -> None:
        content = _read(CELERY_APP)
        assert "settings.redbeat_redis_url" in content, (
            "redbeat_redis_url must come from settings (not hardcoded)"
        )


class TestRedBeatImportable:
    """celery-redbeat must be in requirements.txt and importable."""

    def test_celery_redbeat_in_requirements(self) -> None:
        content = _read(REQUIREMENTS)
        assert "celery-redbeat==2.3.3" in content, (
            "celery-redbeat==2.3.3 must be pinned in requirements.txt (Issue 263)"
        )


class TestBeatDeploymentLivenessProbe:
    """beat/deployment.yaml must have a livenessProbe."""

    def test_liveness_probe_present(self) -> None:
        content = _read(BEAT_DEPLOY)
        assert "livenessProbe:" in content, (
            "beat deployment must have a livenessProbe (Issue 263)"
        )

    def test_liveness_probe_checks_heartbeat_file(self) -> None:
        content = _read(BEAT_DEPLOY)
        assert "celerybeat-schedule" in content, (
            "beat liveness probe must check the celerybeat-schedule heartbeat file"
        )

    def test_liveness_probe_threshold_is_300s(self) -> None:
        content = _read(BEAT_DEPLOY)
        assert "-lt 300" in content, (
            "beat liveness probe file-mtime threshold must be 300 seconds"
        )

    def test_schedule_file_arg_removed_from_command(self) -> None:
        # With RedBeat, the --schedule= file path is no longer needed.
        # The template comment explains why, but the actual arg must not be in the command.
        content = _read(BEAT_DEPLOY)
        # Check that no uncommented '--schedule=' appears in the command block.
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "- --schedule=" in stripped:
                raise AssertionError(
                    f"beat command must not include --schedule= (RedBeat uses Redis): {line!r}"
                )


class TestConfigRedBeatProperty:
    """config.py must expose a redbeat_redis_url property that falls back to REDIS_URL."""

    def test_redbeat_redis_url_field_exists(self) -> None:
        content = _read(CONFIG_PY)
        assert "REDBEAT_REDIS_URL" in content, (
            "config.py must declare REDBEAT_REDIS_URL (Issue 263)"
        )

    def test_redbeat_redis_url_property_fallback(self) -> None:
        content = _read(CONFIG_PY)
        # The property must reference REDIS_URL as fallback
        prop_idx = content.find("def redbeat_redis_url")
        assert prop_idx != -1, "config.py must have a redbeat_redis_url property"
        prop_body = content[prop_idx : prop_idx + 200]
        assert "REDIS_URL" in prop_body, (
            "redbeat_redis_url property must fall back to REDIS_URL"
        )


class TestEnvExampleRedBeat:
    """.env.example must document REDBEAT_REDIS_URL."""

    def test_redbeat_redis_url_documented(self) -> None:
        content = _read(ENV_EXAMPLE)
        assert "REDBEAT_REDIS_URL" in content, (
            ".env.example must document REDBEAT_REDIS_URL (Issue 263)"
        )


class TestValuesYamlRedisHa:
    """values.yaml must include redis.haUrl for the HA Redis migration."""

    def test_redis_ha_url_key_exists(self) -> None:
        content = _read(VALUES_YAML)
        assert "haUrl:" in content, (
            "values.yaml must include redis.haUrl for HA Redis config (Issue 263)"
        )

    def test_prod_redis_ha_url_placeholder(self) -> None:
        content = _read(VALUES_PROD)
        assert "haUrl:" in content, (
            "values.prod.yaml must set redis.haUrl to the managed HA Redis endpoint (Issue 263)"
        )
