"""Shared API envelope, pagination, RFC 9457 error, and job schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")

JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
RaDeg = Annotated[float, Field(ge=0.0, lt=360.0)]
DecDeg = Annotated[float, Field(ge=-90.0, le=90.0)]
FovArcmin = Annotated[float, Field(ge=1.0, le=60.0)]


class HealthResponse(BaseModel):
    """Service liveness payload."""

    status: Literal["ok"] = "ok"
    version: str = "0.1.0"


class ProblemDetail(BaseModel):
    """RFC 9457 error envelope with optional science impact statement."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    science_impact: str | None = None


class JobAcceptedResponse(BaseModel):
    """Standard 202 response for asynchronous tasks."""

    job_id: UUID
    status: JobStatus = "queued"


class JobStatusResponse(BaseModel):
    """Persisted background job state and progress (0..1)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    task: str
    queue: str
    status: JobStatus
    progress: float | None = Field(default=None, ge=0.0, le=1.0)
    error: dict[str, Any] | None = None
    outputs: dict[str, Any] | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime | None = None


class PaginatedResponse(BaseModel, Generic[T]):
    """Cursor-paginated collection wrapper."""

    items: list[T]
    next_cursor: str | None = None
