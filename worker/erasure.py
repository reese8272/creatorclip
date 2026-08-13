"""Media-key enumeration for archive and right-to-erasure (Issues 446 + 471).

Stub — implemented after the RED coverage test in tests/test_erasure_keys.py.
"""

import uuid
from collections.abc import Iterable
from typing import Any

KEY_PATTERNS: dict[str, str] = {}


def candidate_keys_for_clip(clip: Any, *, include_posters: bool = True) -> set[str]:
    raise NotImplementedError


def candidate_keys_for_video(
    video: Any, clips: Iterable[Any], *, include_identity_artifacts: bool = True
) -> set[str]:
    raise NotImplementedError


async def candidate_keys_for_creator(session: Any, creator_id: uuid.UUID) -> set[str]:
    raise NotImplementedError


async def purge_uris(uris: Iterable[str]) -> set[str]:
    raise NotImplementedError
