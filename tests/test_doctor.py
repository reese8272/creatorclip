"""Unit tests for the preflight doctor.

Covers the load-bearing guarantees: (1) secrets are never shown in full,
(2) format validators accept valid and reject invalid input, (3) the production
CORS rule is enforced, and (4) the exit-code aggregation flips on any failure.
No network calls — live reachability checks are exercised manually via --full.
"""

from cryptography.fernet import Fernet

from scripts.doctor import (
    Result,
    Status,
    _section_storage,
    audit,
    check_cors,
    check_field,
    fmt_fernet,
    fmt_min_len,
    fmt_prefix,
    has_failures,
    redact,
)

# ── storage backend guards (root-caused from a prod FAILED upload) ───────────────


def test_doctor_storage_fails_on_local_backend_in_production():
    results = _section_storage({"ENV": "production", "STORAGE_BACKEND": "local"})
    assert results[0].status is Status.FAIL
    assert "r2" in results[0].detail


def test_doctor_storage_warns_when_r2_creds_set_but_backend_local():
    # Operator stood up R2 but forgot STORAGE_BACKEND=r2 — the exact misconfig.
    results = _section_storage(
        {"STORAGE_BACKEND": "local", "R2_ACCOUNT_ID": "acct", "R2_BUCKET": "b"}
    )
    assert any(r.status is Status.WARN and "STORAGE_BACKEND=local" in r.detail for r in results)


def test_doctor_storage_ok_for_fully_configured_r2():
    results = _section_storage(
        {
            "ENV": "production",
            "STORAGE_BACKEND": "r2",
            "R2_ACCOUNT_ID": "acct",
            "R2_ACCESS_KEY_ID": "ak",
            "R2_SECRET_ACCESS_KEY": "sk",
            "R2_BUCKET": "bucket",
        }
    )
    assert all(r.status is Status.OK for r in results)

# ── redaction never leaks the value ─────────────────────────────────────────────


def test_redact_hides_full_secret():
    secret = "sk-ant-supersecretvalue1234"
    shown = redact(secret)
    assert secret not in shown
    assert "1234" in shown  # last 4 are a fingerprint, not the secret
    assert "len 27" in shown


def test_redact_empty_and_short():
    assert redact("") == "(empty)"
    assert redact(None) == "(empty)"
    assert "len 3" in redact("abc")  # too short to expose a suffix


# ── format validators ───────────────────────────────────────────────────────────


def test_fmt_fernet_accepts_real_key_rejects_garbage():
    assert fmt_fernet(Fernet.generate_key().decode()) is None
    assert fmt_fernet("not-a-key") is not None


def test_fmt_min_len():
    assert fmt_min_len(32)("x" * 32) is None
    assert fmt_min_len(32)("short") is not None


def test_fmt_prefix():
    assert fmt_prefix("sk-ant-")("sk-ant-abc") is None
    assert fmt_prefix("sk-ant-")("nope") is not None


# ── production CORS rule is load-bearing for security ───────────────────────────


def test_cors_rejects_wildcard_and_localhost_in_prod():
    assert check_cors("*", "production") is not None
    assert check_cors("http://localhost:8000", "production") is not None
    assert check_cors("https://autoclip.studio", "production") is None


def test_cors_permissive_in_development():
    assert check_cors("http://localhost:8000", "development") is None


# ── per-field check status ──────────────────────────────────────────────────────


def test_required_field_missing_fails():
    assert check_field({}, "JWT_SECRET_KEY", required=True, secret=True).status is Status.FAIL


def test_optional_field_missing_warns():
    assert check_field({}, "VOYAGE_API_KEY", required=False, secret=True).status is Status.WARN


def test_bad_format_fails_present_field():
    r = check_field(
        {"ANTHROPIC_API_KEY": "wrong"},
        "ANTHROPIC_API_KEY",
        required=True,
        secret=True,
        fmt=fmt_prefix("sk-ant-"),
    )
    assert r.status is Status.FAIL


# ── exit-code aggregation ───────────────────────────────────────────────────────


def test_has_failures_true_on_any_fail():
    sections = [("x", [Result("a", Status.OK), Result("b", Status.FAIL)])]
    assert has_failures(sections) is True


def test_has_failures_false_when_only_ok_and_warn():
    sections = [("x", [Result("a", Status.OK), Result("b", Status.WARN), Result("c", Status.SKIP)])]
    assert has_failures(sections) is False


# ── offline audit produces no live sections and does not crash on empty env ─────


def test_offline_audit_has_no_live_sections():
    titles = [t for t, _ in audit({}, offline=True, full=False)]
    assert not any("Live" in t for t in titles)


def test_offline_audit_on_empty_env_flags_required_missing():
    sections = audit({}, offline=True, full=False)
    assert has_failures(sections) is True  # required vars are absent
