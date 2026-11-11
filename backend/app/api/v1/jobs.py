"""Background job status and SSE stream endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import Response

from app.api.deps import COMMON_501, not_implemented
from app.schemas.common import JobStatusResponse

router = APIRouter(prefix="/jobs", tags=["jobs"], responses=COMMON_501)


@router.get("/{job_id}")
async def get_job(job_id: UUID) -> JobStatusResponse:
    """Fetch background job status, progress (0..1), and outputs."""
    not_implemented()


@router.get("/{job_id}/events")
async def stream_job_events(job_id: UUID) -> Response:
    """Stream Server-Sent Events for job progress updates."""
    not_implemented()
