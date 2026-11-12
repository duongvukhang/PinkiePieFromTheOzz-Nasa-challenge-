"""Background job status and SSE stream endpoints."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse

from app.api.deps import APIProblemError, get_store
from app.schemas.common import JobStatusResponse, ProblemDetail
from app.services.jobs import JobStore

router = APIRouter(
    prefix="/jobs",
    tags=["jobs"],
    responses={404: {"model": ProblemDetail}},
)


@router.get("/{job_id}")
async def get_job(
    job_id: UUID,
    store: Annotated[JobStore, Depends(get_store)],
) -> JobStatusResponse:
    """Fetch background job status, progress (0..1), and outputs."""
    job = store.get_job(job_id)
    if job is None:
        raise APIProblemError(
            status.HTTP_404_NOT_FOUND,
            "Job Not Found",
            f"No processing job found for {job_id}.",
        )
    return job


@router.get("/{job_id}/events")
async def stream_job_events(
    job_id: UUID,
    store: Annotated[JobStore, Depends(get_store)],
) -> StreamingResponse:
    """Stream Server-Sent Events for job progress updates."""
    if store.get_job(job_id) is None:
        raise APIProblemError(
            status.HTTP_404_NOT_FOUND,
            "Job Not Found",
            f"No processing job found for {job_id}.",
        )

    def _event_stream() -> Iterator[str]:
        for snap in store.iter_job_events(job_id):
            yield f"event: progress\ndata: {snap.model_dump_json()}\n\n"

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
