"""LLM-drivable end-to-end harness for the CreatorClip API.

The browser UI is OAuth-gated, so an LLM/agent can't click through it. This
harness mints a session JWT for a *seeded* creator — exactly the way
``auth.create_session_token`` does (and the Locust file does) — and drives the
real HTTP endpoints, asserting each step. It is the "make the app touchable by
an LLM" deliverable: run it against the staging stack and read the structured
PASS/FAIL report.

It imports NOTHING from the app (only ``jwt`` + ``httpx``), so it runs anywhere
with network access to the app and the staging ``JWT_SECRET_KEY``. The creator
must already exist — seed it first with ``tests/perf/seed_staging.py`` (which
prints CC_CREATOR_ID + CC_JWT_SECRET).

Usage (on the VM, with the staging stack up + seeded):
    export CC_BASE_URL=http://localhost:8001
    export CC_JWT_SECRET=<staging JWT_SECRET_KEY>
    export CC_CREATOR_ID=00000000-1111-2222-3333-444444444444
    python3 scripts/llm_harness.py                 # all flows
    python3 scripts/llm_harness.py --flow core     # health + reads only
    python3 scripts/llm_harness.py --base-url http://localhost:8001

Exit code is 0 only when every REQUIRED step passes (soft steps warn, never
fail), so it doubles as a CI/smoke gate. It never touches production data: point
it at staging. The only writes it performs are linking one fixed test video to
the seeded creator (idempotent) and probing the queue guard — both safe.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import jwt

SESSION_COOKIE = "cc_session"
_ALGORITHM = "HS256"

# A fixed, format-valid (^[A-Za-z0-9_-]{11}$) test video id. Linking it to the
# seeded creator is idempotent — a re-run hits the 409 "already registered" path
# and we fetch the existing row instead.
_HARNESS_YT_ID = "HARNESSm139"


def _mint_token(secret: str, creator_id: str) -> str:
    """Mint a session JWT the same way auth.create_session_token does."""
    now = datetime.now(UTC)
    payload = {"sub": creator_id, "iat": now, "exp": now + timedelta(hours=2)}
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


@dataclass
class Step:
    name: str
    ok: bool
    detail: str
    required: bool = True


class Harness:
    def __init__(self, base_url: str, secret: str, creator_id: str) -> None:
        self.base_url = base_url.rstrip("/")
        token = _mint_token(secret, creator_id)
        self.client = httpx.Client(
            base_url=self.base_url,
            cookies={SESSION_COOKIE: token},
            timeout=30.0,
            follow_redirects=False,
        )
        self.steps: list[Step] = []

    # ── step helpers ─────────────────────────────────────────────────────────

    def _record(self, name: str, ok: bool, detail: str, required: bool = True) -> Step:
        step = Step(name, ok, detail, required)
        self.steps.append(step)
        mark = "PASS" if ok else ("FAIL" if required else "WARN")
        print(f"  [{mark}] {name} — {detail}")
        return step

    def _get(self, path: str) -> httpx.Response:
        return self.client.get(path)

    def _post(self, path: str, **kw: object) -> httpx.Response:
        return self.client.post(path, **kw)  # type: ignore[arg-type]

    # ── flows ────────────────────────────────────────────────────────────────

    def flow_core(self) -> None:
        """Health + the authenticated read surface."""
        try:
            r = self._get("/health")
            body = r.json()
            ok = r.status_code == 200 and body.get("status") in ("ok", "healthy")
            self._record("health", ok, f"{r.status_code} {body}")
        except Exception as exc:  # noqa: BLE001 — harness reports, never crashes
            self._record("health", False, f"error: {exc}")

        for name, path in [
            ("auth_me", "/creators/me"),
            ("videos_list", "/videos"),
            ("dna", "/creators/me/dna"),
            ("insights", "/creators/me/insights"),
            ("billing_balance", "/billing/balance"),
        ]:
            soft = name in ("dna", "insights", "billing_balance")
            try:
                r = self._get(path)
                # 200 ideal; 404/empty is acceptable for soft (data-dependent) reads.
                ok = r.status_code == 200
                self._record(name, ok, f"{r.status_code}", required=not soft)
            except Exception as exc:  # noqa: BLE001
                self._record(name, False, f"error: {exc}", required=not soft)

        # The /videos envelope contract (Issue 90/139): a typed envelope, not a
        # bare array.
        try:
            r = self._get("/videos")
            body = r.json()
            ok = isinstance(body, dict) and "videos" in body and "state" in body
            self._record("videos_envelope_shape", ok, f"keys={sorted(body)[:6]}")
        except Exception as exc:  # noqa: BLE001
            self._record("videos_envelope_shape", False, f"error: {exc}")

        # Issue 295: SOFT write-path assertion — link a fixed idempotent test video.
        # This exercises the write path (POST /videos/link) without polluting real
        # creator data. 200 = first link, 409 = already linked (re-run) — both are
        # acceptable. Failure is WARN-only (required=False) so a missing OAuth token
        # or metadata-lookup degradation does not roll back a healthy deploy.
        try:
            r = self._post("/videos/link", data={"youtube_video_id": _HARNESS_YT_ID})
            ok = r.status_code in (200, 409)
            self._record(
                "write_path_link_video",
                ok,
                f"{r.status_code} (200=linked 409=already-linked)",
                required=False,
            )
        except Exception as exc:  # noqa: BLE001
            self._record("write_path_link_video", False, f"error: {exc}", required=False)

    def flow_issue_139(self) -> None:
        """Live regression for the linked-video SEV1 + the source-less queue guard."""
        # 1. Link a video (idempotent). With no real OAuth tokens on the seeded
        #    creator, link_video's metadata lookup fails-open to kind=long — the
        #    row is still created with origin=link.
        try:
            r = self._post("/videos/link", data={"youtube_video_id": _HARNESS_YT_ID})
            linked_ok = r.status_code in (200, 409)  # 409 = already linked (re-run)
            self._record("link_video", linked_ok, f"{r.status_code}")
        except Exception as exc:  # noqa: BLE001
            self._record("link_video", False, f"error: {exc}")
            return

        # 2. The SEV1: the linked video must now APPEAR in /videos, flagged
        #    clippable=false (the whole point — visible but honestly not yet
        #    clip-trackable).
        linked = None
        try:
            body = self._get("/videos").json()
            for v in body.get("videos", []):
                if v.get("youtube_video_id") == _HARNESS_YT_ID:
                    linked = v
                    break
            ok = (
                linked is not None
                and linked.get("origin") == "link"
                and linked.get("clippable") is False
            )
            detail = (
                f"origin={linked.get('origin')} clippable={linked.get('clippable')}"
                if linked
                else "linked video NOT in /videos (SEV1 regression!)"
            )
            self._record("linked_video_visible_non_clippable", ok, detail)
        except Exception as exc:  # noqa: BLE001
            self._record("linked_video_visible_non_clippable", False, f"error: {exc}")

        # 3. The source-less queue guard: queuing a linked video must 409 with
        #    upload guidance, never fire a doomed ingest.
        if linked is not None:
            try:
                r = self._post(f"/videos/{linked['id']}/queue")
                ok = r.status_code == 409 and "upload" in r.text.lower()
                self._record("queue_source_less_409", ok, f"{r.status_code}")
            except Exception as exc:  # noqa: BLE001
                self._record("queue_source_less_409", False, f"error: {exc}")

    # ── report ───────────────────────────────────────────────────────────────

    def report(self) -> int:
        print("\n" + "=" * 60)
        passed = sum(1 for s in self.steps if s.ok)
        failed_required = [s for s in self.steps if not s.ok and s.required]
        warned = [s for s in self.steps if not s.ok and not s.required]
        print(f"  {passed}/{len(self.steps)} steps passed")
        if warned:
            print(f"  {len(warned)} soft warning(s): {', '.join(s.name for s in warned)}")
        if failed_required:
            print(
                f"  {len(failed_required)} REQUIRED failure(s): "
                f"{', '.join(s.name for s in failed_required)}"
            )
            print("=" * 60)
            return 1
        print("  ALL REQUIRED STEPS PASSED ✓")
        print("=" * 60)
        return 0

    def close(self) -> None:
        self.client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM-drivable E2E harness for CreatorClip.")
    parser.add_argument(
        "--base-url", default=os.environ.get("CC_BASE_URL", "http://localhost:8001")
    )
    parser.add_argument("--jwt-secret", default=os.environ.get("CC_JWT_SECRET", ""))
    parser.add_argument(
        "--creator-id",
        default=os.environ.get("CC_CREATOR_ID", "00000000-1111-2222-3333-444444444444"),
    )
    parser.add_argument(
        "--flow",
        choices=["all", "core", "issue139"],
        default="all",
        help="Which flow(s) to run.",
    )
    args = parser.parse_args()

    if not args.jwt_secret:
        print("ERROR: set CC_JWT_SECRET (the staging JWT_SECRET_KEY) or pass --jwt-secret.")
        return 2
    try:
        uuid.UUID(args.creator_id)
    except ValueError:
        print(f"ERROR: --creator-id is not a valid UUID: {args.creator_id}")
        return 2

    print(f"Driving {args.base_url} as creator {args.creator_id} (flow={args.flow})\n")
    h = Harness(args.base_url, args.jwt_secret, args.creator_id)
    try:
        if args.flow in ("all", "core"):
            print("── core (health + reads) ──")
            h.flow_core()
        if args.flow in ("all", "issue139"):
            print("\n── issue 139 (linked-video regression) ──")
            h.flow_issue_139()
        return h.report()
    finally:
        h.close()


if __name__ == "__main__":
    sys.exit(main())
