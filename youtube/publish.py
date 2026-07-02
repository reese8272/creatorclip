"""
YouTube Data API v3 video upload via the resumable-upload protocol (Issue 195).

https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol

  1. POST the video metadata to the resumable-init endpoint → 200 + a session
     URI in the ``Location`` header.
  2. PUT the bytes to that session URI in chunks, each with a ``Content-Range``
     header. A non-final chunk returns 308 (Resume Incomplete) with a ``Range``
     header marking the bytes received; the final chunk returns 200/201 with the
     created video resource (its ``id``).
  3. On a transient failure mid-upload, query the session (PUT with
     ``Content-Range: bytes */<total>`` and an empty body) for the received
     offset and resume from there — robust to dropped connections.

All HTTP goes through ``youtube._http.client()`` so tests patch one place. The
shared client's 60s timeout bounds each chunk PUT (not the whole upload), which
is why we chunk rather than stream the whole file in one request.
"""

import json
import logging
from pathlib import Path

import httpx

from youtube import _http
from youtube.errors import YouTubeAuthError

logger = logging.getLogger(__name__)

_INIT_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
# 8 MiB — a multiple of 256 KiB, as the resumable protocol requires for
# non-final chunks.
_UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024
_MAX_RESUME_ATTEMPTS = 5
_SERVER_ERROR_CODES = frozenset({500, 502, 503, 504})


class YouTubeUploadError(Exception):
    """A permanent upload failure (audit/quota/forbidden/bad-request) — the
    caller should surface it, not retry blindly."""

    def __init__(self, status_code: int, message: str = ""):
        self.status_code = status_code
        super().__init__(message or f"youtube upload failed: {status_code}")


async def _initiate(
    access_token: str, *, title: str, description: str, privacy_status: str, total_bytes: int
) -> str:
    """Open a resumable session; return its upload URI."""
    metadata = {
        "snippet": {"title": title, "description": description},
        # Pre-audit safety: never made-for-kids by default; privacy forced by caller.
        "status": {"privacyStatus": privacy_status, "selfDeclaredMadeForKids": False},
    }
    resp = await _http.client().post(
        _INIT_URL,
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/*",
            "X-Upload-Content-Length": str(total_bytes),
        },
        content=json.dumps(metadata),
    )
    if resp.status_code == 401:
        raise YouTubeAuthError("invalid_token", 401, "upload init unauthorized")
    if resp.status_code != 200:
        raise YouTubeUploadError(resp.status_code, f"init failed: {resp.text[:200]}")
    location = resp.headers.get("Location")
    if not location:
        raise YouTubeUploadError(200, "init returned no upload session URI")
    return location


def _offset_from_range(range_header: str | None, fallback: int) -> int:
    """Parse the byte offset to resume from out of a ``Range: bytes=0-N`` header."""
    if range_header and "-" in range_header:
        return int(range_header.rsplit("-", 1)[1]) + 1
    return fallback


async def _query_offset(session_uri: str, total_bytes: int) -> tuple[int, str | None]:
    """Ask the session how many bytes it has received, to resume after a failure.

    Returns ``(offset, video_id)``. A 200/201 status-query response means the
    upload actually COMPLETED despite the failed chunk PUT — the body carries
    the created video resource, so its id is returned and the caller reports
    success. Discarding it (the pre-Issue-352 behavior) misreported a finished
    upload as failed, and the Celery retry re-uploaded a duplicate video.
    """
    resp = await _http.client().put(
        session_uri,
        headers={"Content-Range": f"bytes */{total_bytes}", "Content-Length": "0"},
    )
    if resp.status_code in (200, 201):
        try:
            video_id = resp.json().get("id")
        except ValueError:
            video_id = None
        return total_bytes, video_id
    if resp.status_code == 308:
        return _offset_from_range(resp.headers.get("Range"), 0), None
    raise YouTubeUploadError(resp.status_code, "offset query failed")


async def upload_video(
    access_token: str,
    file_path: str | Path,
    *,
    title: str,
    description: str,
    privacy_status: str = "private",
) -> str:
    """Upload a video file to the authenticated channel; return its YouTube id.

    Raises ``YouTubeAuthError`` on 401 (grant invalid), ``YouTubeUploadError`` on
    a permanent failure (403 audit/quota/forbidden, 400). Transient network /
    5xx failures are resumed up to ``_MAX_RESUME_ATTEMPTS`` times.
    """
    path = Path(file_path)
    total = path.stat().st_size
    session_uri = await _initiate(
        access_token,
        title=title,
        description=description,
        privacy_status=privacy_status,
        total_bytes=total,
    )

    offset = 0
    attempts = 0
    with path.open("rb") as fh:
        while offset < total:
            fh.seek(offset)
            chunk = fh.read(_UPLOAD_CHUNK_BYTES)
            end = offset + len(chunk) - 1
            try:
                resp = await _http.client().put(
                    session_uri,
                    headers={
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {offset}-{end}/{total}",
                    },
                    content=chunk,
                )
            except httpx.HTTPError as exc:
                attempts += 1
                if attempts > _MAX_RESUME_ATTEMPTS:
                    raise YouTubeUploadError(0, f"resume attempts exhausted: {exc}") from exc
                logger.warning("upload chunk failed (%s) — resuming", exc)
                offset, completed_id = await _query_offset(session_uri, total)
                if completed_id:
                    logger.info(
                        "upload session already complete despite chunk error — video %s",
                        completed_id,
                    )
                    return completed_id
                continue

            if resp.status_code in (200, 201):
                video_id = resp.json().get("id")
                if not video_id:
                    raise YouTubeUploadError(resp.status_code, "upload finished without a video id")
                return video_id
            if resp.status_code == 308:
                offset = _offset_from_range(resp.headers.get("Range"), end + 1)
                attempts = 0
                continue
            if resp.status_code == 401:
                raise YouTubeAuthError("invalid_token", 401, "upload unauthorized")
            if resp.status_code in _SERVER_ERROR_CODES:
                attempts += 1
                if attempts > _MAX_RESUME_ATTEMPTS:
                    raise YouTubeUploadError(resp.status_code, "server errors exhausted resume")
                offset, completed_id = await _query_offset(session_uri, total)
                if completed_id:
                    logger.info(
                        "upload session already complete despite %d on chunk — video %s",
                        resp.status_code,
                        completed_id,
                    )
                    return completed_id
                continue
            # 403 (audit/quota/forbidden), 400 (bad request) — permanent.
            raise YouTubeUploadError(resp.status_code, f"upload failed: {resp.text[:200]}")

    # Ambiguous terminal state: the session reports all bytes received but no
    # video id was ever returned. Permanent (never retried) — a blind retry
    # here is exactly the duplicate-upload path this guards against.
    raise YouTubeUploadError(0, "upload completed without a video id — not retrying")
