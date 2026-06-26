#!/usr/bin/env python3
"""
CreatorClip preflight doctor.

Checks every secret/config value for presence, format, and (optionally) live
reachability, then prints a status table with all values REDACTED — safe to
paste into a ticket or chat. Exits non-zero if any required check fails, so the
same command doubles as a deploy gate.

This module deliberately does NOT import ``config`` (which exits the process on a
missing required var) — the whole point of the doctor is to *report* what is
missing rather than crash. It reads the environment directly.

Usage (run from the project root):
    python scripts/doctor.py            # presence + format + live Postgres/Redis
    python scripts/doctor.py --full     # also probe Anthropic, Voyage, Deepgram, R2, Stripe
    python scripts/doctor.py --offline  # presence + format only, no network
    python scripts/doctor.py --json     # machine-readable output

See docs/SECRETS.md for the full registry.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from urllib.parse import urlparse

# Ensure the project root is importable when run directly as a file.
sys.path.insert(0, str(Path(__file__).parent.parent))

_ENV_FILE = Path(__file__).parent.parent / ".env"


class Status(Enum):
    OK = "ok"
    FAIL = "fail"
    WARN = "warn"
    SKIP = "skip"


_GLYPH = {Status.OK: "✓", Status.FAIL: "✗", Status.WARN: "!", Status.SKIP: "–"}


@dataclass
class Result:
    name: str
    status: Status
    detail: str = ""


# ── value display helpers ──────────────────────────────────────────────────────


def redact(value: str | None, keep: int = 4) -> str:
    """Render a secret as length + last ``keep`` chars only — never the value."""
    if not value:
        return "(empty)"
    n = len(value)
    if n <= keep:
        return f"set (len {n})"
    return f"set ····{value[-keep:]} (len {n})"


def _scrub(text: str, secrets: list[str]) -> str:
    """Remove any known secret value from a string (e.g. an exception message)."""
    out = text.splitlines()[0] if text else ""
    for s in secrets:
        if s:
            out = out.replace(s, "***")
    return out[:160]


# ── format validators (return None if ok, else an error message) ───────────────


def fmt_fernet(value: str) -> str | None:
    from cryptography.fernet import Fernet

    try:
        Fernet(value.encode())
        return None
    except Exception:
        return "not a valid Fernet key (regenerate with crypto.generate_key)"


def fmt_min_len(minimum: int) -> Callable[[str], str | None]:
    def _check(value: str) -> str | None:
        return None if len(value) >= minimum else f"too short (len {len(value)}, need >= {minimum})"

    return _check


def fmt_prefix(prefix: str) -> Callable[[str], str | None]:
    def _check(value: str) -> str | None:
        return None if value.startswith(prefix) else f"expected prefix {prefix!r}"

    return _check


def fmt_suffix(suffix: str) -> Callable[[str], str | None]:
    def _check(value: str) -> str | None:
        return None if value.endswith(suffix) else f"expected to end with {suffix!r}"

    return _check


def fmt_url(*schemes: str) -> Callable[[str], str | None]:
    def _check(value: str) -> str | None:
        scheme = urlparse(value).scheme
        return None if scheme in schemes else f"unexpected scheme {scheme!r} (want {schemes})"

    return _check


def check_cors(value: str, env: str) -> str | None:
    """In production, CORS must be a concrete domain — never '*' or localhost."""
    origins = [o.strip() for o in value.split(",") if o.strip()]
    if env == "production":
        if "*" in origins:
            return "wildcard '*' not allowed in production"
        if any("localhost" in o or "127.0.0.1" in o for o in origins):
            return "localhost origin present in production"
    return None


# ── per-field presence + format check ──────────────────────────────────────────


def check_field(
    env: dict[str, str],
    key: str,
    *,
    required: bool,
    secret: bool,
    fmt: Callable[[str], str | None] | None = None,
) -> Result:
    value = env.get(key, "")
    if not value:
        if required:
            return Result(key, Status.FAIL, "missing (required)")
        return Result(key, Status.WARN, "not set (optional)")
    if fmt is not None:
        err = fmt(value)
        if err:
            return Result(key, Status.FAIL, err)
    return Result(key, Status.OK, redact(value) if secret else value)


# ── section builders ───────────────────────────────────────────────────────────


def _section_core(env: dict[str, str]) -> list[Result]:
    return [
        check_field(
            env,
            "DATABASE_URL",
            required=True,
            secret=True,
            fmt=fmt_url("postgresql", "postgres", "postgresql+psycopg", "postgresql+asyncpg"),
        ),
        check_field(env, "REDIS_URL", required=True, secret=False, fmt=fmt_url("redis", "rediss")),
        check_field(env, "POSTGRES_PASSWORD", required=False, secret=True),
        check_field(env, "ENV", required=False, secret=False),
    ]


def _section_security(env: dict[str, str]) -> list[Result]:
    cors = check_field(env, "ALLOWED_ORIGINS", required=True, secret=False)
    if cors.status is Status.OK:
        err = check_cors(env.get("ALLOWED_ORIGINS", ""), env.get("ENV", "development"))
        if err:
            cors = Result("ALLOWED_ORIGINS", Status.FAIL, err)
    return [
        check_field(env, "TOKEN_ENCRYPTION_KEY", required=True, secret=True, fmt=fmt_fernet),
        check_field(env, "JWT_SECRET_KEY", required=True, secret=True, fmt=fmt_min_len(32)),
        cors,
    ]


def _section_ai(env: dict[str, str]) -> list[Result]:
    return [
        check_field(
            env, "ANTHROPIC_API_KEY", required=True, secret=True, fmt=fmt_prefix("sk-ant-")
        ),
        check_field(env, "VOYAGE_API_KEY", required=False, secret=True),
    ]


def _section_transcription(env: dict[str, str]) -> list[Result]:
    backend = env.get("TRANSCRIPTION_BACKEND", "deepgram")
    results = [check_field(env, "TRANSCRIPTION_BACKEND", required=False, secret=False)]
    if backend not in {"deepgram", "whisperx", "assemblyai"}:
        results[0] = Result("TRANSCRIPTION_BACKEND", Status.FAIL, f"unknown backend {backend!r}")
    if backend == "deepgram":
        results.append(check_field(env, "DEEPGRAM_API_KEY", required=True, secret=True))
    elif backend == "assemblyai":
        results.append(check_field(env, "ASSEMBLYAI_API_KEY", required=True, secret=True))
    return results


def _section_storage(env: dict[str, str]) -> list[Result]:
    backend = env.get("STORAGE_BACKEND", "local")
    prod = env.get("ENV", "development") == "production"
    results = [check_field(env, "STORAGE_BACKEND", required=False, secret=False)]
    if backend not in {"local", "r2"}:
        results[0] = Result("STORAGE_BACKEND", Status.FAIL, f"unknown backend {backend!r}")
    elif prod and backend != "r2":
        # Mirror the config validator: prod's app/worker split has no shared media
        # volume, so local-disk storage is unreadable by the worker and every
        # upload FAILs. Catch it here, at deploy preflight, before the rollout.
        results[0] = Result(
            "STORAGE_BACKEND",
            Status.FAIL,
            f"must be 'r2' in production (worker can't read local disk); got {backend!r}",
        )
    if backend == "r2":
        results += [
            check_field(env, "R2_ACCOUNT_ID", required=True, secret=False),
            check_field(env, "R2_ACCESS_KEY_ID", required=True, secret=True),
            check_field(env, "R2_SECRET_ACCESS_KEY", required=True, secret=True),
            check_field(env, "R2_BUCKET", required=True, secret=False),
        ]
    elif backend == "local" and any(
        env.get(k)
        for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
    ):
        # Blind-spot guard: R2 creds present but backend still local almost always
        # means the operator stood up R2 and forgot STORAGE_BACKEND=r2 — the exact
        # misconfig that silently broke a prod upload. Flag it (WARN in dev so the
        # local lane isn't noisy; the prod FAIL above already covers production).
        results.append(
            Result(
                "STORAGE_BACKEND",
                Status.WARN,
                "R2 credentials are set but STORAGE_BACKEND=local — did you mean r2?",
            )
        )
    return results


def _section_billing(env: dict[str, str]) -> list[Result]:
    if not env.get("STRIPE_SECRET_KEY"):
        return [Result("STRIPE_SECRET_KEY", Status.WARN, "not set (billing disabled)")]
    return [
        check_field(env, "STRIPE_SECRET_KEY", required=True, secret=True, fmt=fmt_prefix("sk_")),
        check_field(
            env, "STRIPE_PUBLISHABLE_KEY", required=True, secret=False, fmt=fmt_prefix("pk_")
        ),
        check_field(
            env, "STRIPE_WEBHOOK_SECRET", required=True, secret=True, fmt=fmt_prefix("whsec_")
        ),
    ]


def _section_oauth(env: dict[str, str]) -> list[Result]:
    prod = env.get("ENV", "development") == "production"
    redirect_fmt = fmt_url("https") if prod else fmt_url("http", "https")
    return [
        check_field(
            env,
            "GOOGLE_OAUTH_CLIENT_ID",
            required=True,
            secret=False,
            fmt=fmt_suffix(".apps.googleusercontent.com"),
        ),
        check_field(env, "GOOGLE_OAUTH_CLIENT_SECRET", required=True, secret=True),
        check_field(env, "OAUTH_REDIRECT_URI", required=True, secret=False, fmt=redirect_fmt),
    ]


def _section_deploy(env: dict[str, str]) -> list[Result]:
    # Only required when running in production (the tunnel fronts the prod deploy).
    required = env.get("ENV", "development") == "production"
    return [check_field(env, "CLOUDFLARE_TUNNEL_TOKEN", required=required, secret=True)]


_SECTIONS: list[tuple[str, Callable[[dict[str, str]], list[Result]]]] = [
    ("Core infrastructure", _section_core),
    ("Security keys", _section_security),
    ("AI / embeddings", _section_ai),
    ("Transcription", _section_transcription),
    ("Storage", _section_storage),
    ("Billing", _section_billing),
    ("Google / YouTube OAuth", _section_oauth),
    ("Deploy / tunnel", _section_deploy),
]


# ── live reachability checks ────────────────────────────────────────────────────


def _live_postgres(env: dict[str, str], secrets: list[str]) -> Result:
    url = env.get("DATABASE_URL", "")
    if not url:
        return Result("postgres connect", Status.SKIP, "no DATABASE_URL")
    try:
        import psycopg

        dsn = url.replace("postgresql+psycopg://", "postgresql://")
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            conn.execute("SELECT 1")
        return Result("postgres connect", Status.OK, "SELECT 1 ok")
    except Exception as exc:  # noqa: BLE001 — report any failure, don't crash the doctor
        return Result("postgres connect", Status.FAIL, _scrub(str(exc), secrets))


def _live_redis(env: dict[str, str], secrets: list[str]) -> Result:
    url = env.get("REDIS_URL", "")
    if not url:
        return Result("redis ping", Status.SKIP, "no REDIS_URL")
    try:
        import redis

        client = redis.from_url(url, socket_connect_timeout=5)
        client.ping()
        client.close()
        return Result("redis ping", Status.OK, "PONG")
    except Exception as exc:  # noqa: BLE001
        return Result("redis ping", Status.FAIL, _scrub(str(exc), secrets))


def _live_anthropic(env: dict[str, str], secrets: list[str]) -> Result:
    key = env.get("ANTHROPIC_API_KEY", "")
    if not key:
        return Result("anthropic auth", Status.SKIP, "no key")
    try:
        from anthropic import Anthropic

        client = Anthropic(api_key=key, timeout=10)
        if not hasattr(client, "models"):
            return Result("anthropic auth", Status.WARN, "SDK lacks models.list — cannot verify")
        client.models.list()
        return Result("anthropic auth", Status.OK, "models.list ok")
    except Exception as exc:  # noqa: BLE001
        return Result("anthropic auth", Status.FAIL, _scrub(str(exc), secrets))


def _live_voyage(env: dict[str, str], secrets: list[str]) -> Result:
    key = env.get("VOYAGE_API_KEY", "")
    if not key:
        return Result("voyage auth", Status.SKIP, "no key")
    try:
        import voyageai

        voyageai.Client(api_key=key).embed(["ping"], model="voyage-3.5")
        return Result("voyage auth", Status.OK, "embed ok")
    except Exception as exc:  # noqa: BLE001
        return Result("voyage auth", Status.FAIL, _scrub(str(exc), secrets))


def _live_deepgram(env: dict[str, str], secrets: list[str]) -> Result:
    key = env.get("DEEPGRAM_API_KEY", "")
    if not key:
        return Result("deepgram auth", Status.SKIP, "no key")
    try:
        import httpx

        resp = httpx.get(
            "https://api.deepgram.com/v1/projects",
            headers={"Authorization": f"Token {key}"},
            timeout=10,
        )
        resp.raise_for_status()
        return Result("deepgram auth", Status.OK, "projects ok")
    except Exception as exc:  # noqa: BLE001
        return Result("deepgram auth", Status.FAIL, _scrub(str(exc), secrets))


def _live_r2(env: dict[str, str], secrets: list[str]) -> Result:
    if env.get("STORAGE_BACKEND", "local") != "r2":
        return Result("r2 head_bucket", Status.SKIP, "STORAGE_BACKEND != r2")
    account = env.get("R2_ACCOUNT_ID", "")
    bucket = env.get("R2_BUCKET", "")
    if not (account and bucket):
        return Result("r2 head_bucket", Status.SKIP, "R2 not fully configured")
    try:
        import boto3

        client = boto3.client(
            "s3",
            endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
            aws_access_key_id=env.get("R2_ACCESS_KEY_ID", ""),
            aws_secret_access_key=env.get("R2_SECRET_ACCESS_KEY", ""),
            region_name="auto",
        )
        client.head_bucket(Bucket=bucket)
        return Result("r2 head_bucket", Status.OK, f"bucket {bucket} reachable")
    except Exception as exc:  # noqa: BLE001
        return Result("r2 head_bucket", Status.FAIL, _scrub(str(exc), secrets))


def _live_stripe(env: dict[str, str], secrets: list[str]) -> Result:
    key = env.get("STRIPE_SECRET_KEY", "")
    if not key:
        return Result("stripe auth", Status.SKIP, "no key")
    try:
        import httpx

        resp = httpx.get("https://api.stripe.com/v1/balance", auth=(key, ""), timeout=10)
        resp.raise_for_status()
        return Result("stripe auth", Status.OK, "balance ok")
    except Exception as exc:  # noqa: BLE001
        return Result("stripe auth", Status.FAIL, _scrub(str(exc), secrets))


_SECRET_KEYS = {
    "DATABASE_URL",
    "POSTGRES_PASSWORD",
    "TOKEN_ENCRYPTION_KEY",
    "JWT_SECRET_KEY",
    "ANTHROPIC_API_KEY",
    "VOYAGE_API_KEY",
    "DEEPGRAM_API_KEY",
    "ASSEMBLYAI_API_KEY",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "CLOUDFLARE_TUNNEL_TOKEN",
}


# ── orchestration ───────────────────────────────────────────────────────────────


def load_env(env_file: Path = _ENV_FILE) -> dict[str, str]:
    """Merge the .env file with the real environment (os.environ wins)."""
    import os

    from dotenv import dotenv_values

    merged: dict[str, str] = {}
    if env_file.exists():
        merged.update({k: v for k, v in dotenv_values(env_file).items() if v is not None})
    merged.update(os.environ)
    return merged


def audit(env: dict[str, str], *, offline: bool, full: bool) -> list[tuple[str, list[Result]]]:
    sections = [(title, builder(env)) for title, builder in _SECTIONS]
    secrets = [env[k] for k in _SECRET_KEYS if env.get(k)]
    if not offline:
        sections.append(
            ("Live — internal", [_live_postgres(env, secrets), _live_redis(env, secrets)])
        )
        if full:
            sections.append(
                (
                    "Live — external APIs",
                    [
                        _live_anthropic(env, secrets),
                        _live_voyage(env, secrets),
                        _live_deepgram(env, secrets),
                        _live_r2(env, secrets),
                        _live_stripe(env, secrets),
                    ],
                )
            )
    return sections


def has_failures(sections: list[tuple[str, list[Result]]]) -> bool:
    return any(r.status is Status.FAIL for _, results in sections for r in results)


def _print_table(sections: list[tuple[str, list[Result]]]) -> None:
    counts = {s: 0 for s in Status}
    for title, results in sections:
        print(f"\n  {title}")
        print("  " + "─" * 60)
        for r in results:
            counts[r.status] += 1
            print(f"  {_GLYPH[r.status]} {r.name:<28} {r.detail}")
    print("\n  " + "─" * 60)
    print(
        f"  {counts[Status.OK]} ok · {counts[Status.WARN]} warn · "
        f"{counts[Status.FAIL]} fail · {counts[Status.SKIP]} skipped"
    )
    verdict = "FAIL" if counts[Status.FAIL] else "PASS"
    print(f"  Result: {verdict}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="CreatorClip preflight doctor")
    parser.add_argument("--full", action="store_true", help="also probe external APIs")
    parser.add_argument("--offline", action="store_true", help="presence + format only; no network")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    env = load_env()
    sections = audit(env, offline=args.offline, full=args.full)

    if args.json:
        payload = {
            "pass": not has_failures(sections),
            "sections": {
                title: [
                    {"name": r.name, "status": r.status.value, "detail": r.detail} for r in results
                ]
                for title, results in sections
            },
        }
        print(json.dumps(payload, indent=2))
    else:
        _print_table(sections)

    sys.exit(1 if has_failures(sections) else 0)


if __name__ == "__main__":
    main()
