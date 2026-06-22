"""GDPR Art. 15/20 data export (Issue 249).

Async 202 + poll, mirroring the improvement-brief precedent: POST enqueues a
Celery task that gathers every data class into a JSON artifact on R2; GET polls
the ``data_exports`` row; a download endpoint serves the artifact via a
short-lived presigned link (prod) or a file stream (dev) — the same pattern as
the clip download (Issue 182). Strictly single-tenant on every query.
"""

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_creator
from db import get_session
from limiter import creator_key, limiter
from models import Creator, DataExport, DataExportStatus
from worker.storage import presigned_download_url

router = APIRouter(prefix="/creators", tags=["export"])
logger = logging.getLogger(__name__)


class ExportQueuedOut(BaseModel):
    status: str
    task_id: str | None


class ExportStatusOut(BaseModel):
    status: str  # none | pending | ready | failed
    requested_at: str | None
    completed_at: str | None
    error: str | None


@router.post("/me/export", status_code=status.HTTP_202_ACCEPTED, response_model=ExportQueuedOut)
@limiter.limit("5/hour", key_func=creator_key)
async def start_export(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Queue a data export for the current creator (Art. 15/20). Returns 202.

    One row per creator; an in-flight build is debounced (returns the same job)."""
    row = await session.scalar(select(DataExport).where(DataExport.creator_id == creator.id))
    if row is not None and row.status == DataExportStatus.pending:
        return {"status": "pending", "task_id": row.job_id}

    if row is None:
        row = DataExport(creator_id=creator.id)
        session.add(row)
        try:
            await session.flush()
        except IntegrityError:
            # Lost a concurrent first-insert race — return the winner's job.
            await session.rollback()
            row = await session.scalar(
                select(DataExport).where(DataExport.creator_id == creator.id)
            )
            if row is not None and row.status == DataExportStatus.pending:
                return {"status": "pending", "task_id": row.job_id}
            raise
    else:
        row.status = DataExportStatus.pending
        row.export_uri = None
        row.error = None
        row.completed_at = None
        row.job_id = None
        row.requested_at = datetime.now(UTC)
    await session.commit()

    from worker.tasks import generate_data_export

    task = await asyncio.to_thread(generate_data_export.delay, str(creator.id))
    row.job_id = task.id
    await session.commit()
    logger.info("Data export queued for creator %s (task %s)", creator.id, task.id)
    return {"status": "pending", "task_id": task.id}


@router.get("/me/export", response_model=ExportStatusOut)
@limiter.limit("120/minute", key_func=creator_key)
async def get_export_status(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Poll the export job. Download is fetched separately from /me/export/download."""
    row = await session.scalar(select(DataExport).where(DataExport.creator_id == creator.id))
    if row is None:
        return {"status": "none", "requested_at": None, "completed_at": None, "error": None}
    return {
        "status": row.status.value,
        "requested_at": row.requested_at.isoformat() if row.requested_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "error": row.error,
    }


@router.get("/me/export/download", response_model=None)
@limiter.limit("30/minute", key_func=creator_key)
async def download_export(
    request: Request,
    creator: Creator = Depends(get_current_creator),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse | FileResponse:
    """Serve the ready export artifact (single-tenant). Prod: 302 → presigned R2;
    dev: stream the file. 409 until the export is ready."""
    row = await session.scalar(select(DataExport).where(DataExport.creator_id == creator.id))
    if row is None or row.status != DataExportStatus.ready or not row.export_uri:
        raise HTTPException(status_code=409, detail="No ready export — request one first")

    filename = f"creatorclip-export-{creator.id}.json"
    presigned = presigned_download_url(row.export_uri, filename=filename, expires_s=900)
    if presigned is not None:
        return RedirectResponse(url=presigned, status_code=302)
    path = Path(row.export_uri)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Export artifact not found")
    return FileResponse(path, media_type="application/json", filename=filename)
