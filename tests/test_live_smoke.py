"""Issue 341 — live-in-isolation smoke harness: skip-gating + pure-logic tests.

The harness's *live* assertions (DB/R2/ffmpeg/Anthropic) run only against a
deployed target with ``RUN_LIVE_SMOKE=1`` — never in the default unit lane.
These tests cover the parts that ARE verifiable offline: the run guard, the
deterministic canary fixture, argument parsing, the result framework, the
Postgres-URL normalization, and the publish safety-refusal.
"""

from __future__ import annotations

import uuid

from scripts import live_smoke


# ── Run guard: no live calls unless RUN_LIVE_SMOKE=1 ─────────────────────────
def test_guard_exits_zero_without_flag(monkeypatch) -> None:
    """main() returns 0 and never touches the DB when RUN_LIVE_SMOKE is unset."""
    monkeypatch.delenv("RUN_LIVE_SMOKE", raising=False)

    def _boom():  # pragma: no cover — must never be called
        raise AssertionError("DB connect attempted while RUN_LIVE_SMOKE was unset")

    monkeypatch.setattr(live_smoke, "_pg_connect", _boom)
    assert live_smoke.main(["--only", "db"]) == 0


def test_guard_requires_exact_flag_value(monkeypatch) -> None:
    """Any value other than '1' is treated as off."""
    monkeypatch.setenv("RUN_LIVE_SMOKE", "true")
    monkeypatch.setattr(
        live_smoke, "_pg_connect", lambda: (_ for _ in ()).throw(AssertionError("connected"))
    )
    assert live_smoke.main(["--only", "db"]) == 0


# ── Deterministic canary fixture ──────────────────────────────────────────────
def test_canary_ids_are_stable_uuid5() -> None:
    ns = live_smoke._CANARY_NS
    assert uuid.uuid5(ns, "creator") == live_smoke.CANARY_CREATOR_ID
    assert uuid.uuid5(ns, "video") == live_smoke.CANARY_VIDEO_ID
    assert uuid.uuid5(ns, "clip") == live_smoke.CANARY_CLIP_ID


def test_canary_ids_distinct_and_prefix_scoped() -> None:
    ids = {live_smoke.CANARY_CREATOR_ID, live_smoke.CANARY_VIDEO_ID, live_smoke.CANARY_CLIP_ID}
    assert len(ids) == 3
    # R2 writes are namespaced under the canary creator so teardown is scoped.
    assert str(live_smoke.CANARY_CREATOR_ID) in live_smoke.CANARY_R2_PREFIX
    assert live_smoke.CANARY_R2_PREFIX.startswith("smoke/")


# ── Argument parsing ──────────────────────────────────────────────────────────
def test_parse_args_defaults() -> None:
    args = live_smoke.parse_args([])
    assert args.target == "prod"
    assert args.only is None
    assert not args.with_llm
    assert not args.publish_live
    assert not args.seed and not args.teardown


def test_parse_args_flags() -> None:
    args = live_smoke.parse_args(["--target", "staging", "--only", "render", "--with-llm"])
    assert args.target == "staging"
    assert args.only == "render"
    assert args.with_llm


def test_registry_covers_every_declared_check() -> None:
    args = live_smoke.parse_args([])
    assert set(live_smoke._registry(args)) == set(live_smoke._ALL_CHECKS)


# ── Result framework ──────────────────────────────────────────────────────────
def test_results_ok_skip_and_report_exit_codes() -> None:
    res = live_smoke.Results()
    res.ok(True, "pass-case")
    res.skip("skip-case", "no dependency")
    assert live_smoke._report(res) == 0  # skips are not failures

    res.ok(False, "fail-case")
    assert live_smoke._report(res) == 1


def test_results_honesty_detects_disclaimer() -> None:
    res = live_smoke.Results()
    res.honesty("This is an estimate grounded in your own data.", "x")
    assert not res.failures
    res.honesty("Guaranteed to go viral.", "y")
    assert res.failures  # no honesty word present → recorded failure


# ── Postgres URL normalization ────────────────────────────────────────────────
def test_normalize_pg_strips_driver_and_prefers_ssl_for_remote() -> None:
    # sslmode=prefer (not require) so the same string works against a managed DB
    # AND the internal Docker Postgres that doesn't support SSL.
    out = live_smoke._normalize_pg("postgresql+asyncpg://u:p@db.example.com:5432/app")
    assert "+asyncpg" not in out
    assert "sslmode=prefer" in out
    assert "sslmode=require" not in out


def test_normalize_pg_internal_docker_host_gets_prefer_not_require() -> None:
    # The bug the VM run surfaced: a non-local Docker host was forced to require SSL.
    out = live_smoke._normalize_pg("postgresql+psycopg://creatorclip:p@172.18.0.2:5432/creatorclip")
    assert "sslmode=prefer" in out


def test_normalize_pg_preserves_explicit_sslmode() -> None:
    out = live_smoke._normalize_pg("postgresql://u:p@db.example.com/app?sslmode=require")
    assert "sslmode=require" in out
    assert "sslmode=prefer" not in out


def test_normalize_pg_leaves_local_untouched() -> None:
    out = live_smoke._normalize_pg("postgresql+psycopg://u:p@localhost:5432/app")
    assert "sslmode" not in out
    assert "+psycopg" not in out


# ── Publish safety: a real upload is refused off staging ─────────────────────
def test_publish_live_refused_on_prod() -> None:
    res = live_smoke.Results()
    live_smoke.check_publish(res, target="prod", publish_live=True)
    assert res.failures, "real publish must be refused on a non-staging target"


def test_publish_dry_run_default_passes() -> None:
    res = live_smoke.Results()
    live_smoke.check_publish(res, target="prod", publish_live=False)
    # Dry-run only imports the pre-flight surface — no failure, no real upload.
    assert not res.failures
