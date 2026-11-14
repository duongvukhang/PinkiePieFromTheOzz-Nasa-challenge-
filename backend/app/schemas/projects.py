"""Project workspace, notes, findings, saved views, versions, sharing, and export schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import DecDeg, FovArcmin, RaDeg


class ProjectCreate(BaseModel):
    """Create a project workspace (ICRS deg, FOV arcmin)."""

    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    center_ra: RaDeg | None = None
    center_dec: DecDeg | None = None
    fov_arcmin: FovArcmin | None = None
    visibility: Literal["private", "link", "public"] = "private"


class ProjectUpdate(BaseModel):
    """Patch project metadata or visibility."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    center_ra: RaDeg | None = None
    center_dec: DecDeg | None = None
    fov_arcmin: FovArcmin | None = None
    visibility: Literal["private", "link", "public"] | None = None


class ProjectRead(BaseModel):
    """Persisted project workspace metadata (ICRS deg, FOV arcmin)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID
    name: str
    description: str | None = None
    center_ra: float | None = None
    center_dec: float | None = None
    fov_arcmin: float | None = None
    visibility: str
    current_version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class NoteCreate(BaseModel):
    """Create markdown note on a project."""

    body_md: str = Field(min_length=1)
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class NoteUpdate(BaseModel):
    """Update markdown note content or attachments."""

    body_md: str | None = Field(default=None, min_length=1)
    attachments: list[dict[str, Any]] | None = None


class NoteRead(BaseModel):
    """Stored project note."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    author_id: UUID | None = None
    body_md: str
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class FindingCreate(BaseModel):
    """Create scientific finding entry."""

    tracked_object_id: UUID | None = None
    title: str = Field(min_length=1, max_length=240)
    summary_md: str | None = None
    status: Literal["draft", "candidate", "submitted", "known", "rejected"] = "draft"
    evidence: dict[str, Any] = Field(default_factory=dict)
    external_ref: str | None = None


class FindingUpdate(BaseModel):
    """Patch finding fields or vetting status."""

    title: str | None = Field(default=None, min_length=1, max_length=240)
    summary_md: str | None = None
    status: Literal["draft", "candidate", "submitted", "known", "rejected"] | None = None
    evidence: dict[str, Any] | None = None
    external_ref: str | None = None


class FindingRead(BaseModel):
    """Stored scientific finding."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    tracked_object_id: UUID | None = None
    title: str
    summary_md: str | None = None
    status: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    external_ref: str | None = None
    created_at: datetime | None = None


class SavedViewCreate(BaseModel):
    """Bookmark viewer URL state and display parameters."""

    name: str | None = Field(default=None, max_length=160)
    url_state: str = Field(min_length=1)
    params: dict[str, Any] | None = None


class SavedViewRead(BaseModel):
    """Stored viewer bookmark."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID | None = None
    name: str | None = None
    url_state: str
    params: dict[str, Any] | None = None
    created_at: datetime | None = None


class ProjectVersionCreate(BaseModel):
    """Create immutable project snapshot."""

    message: str | None = Field(default=None, max_length=300)


class ProjectVersionRead(BaseModel):
    """Immutable project snapshot entry."""

    model_config = ConfigDict(from_attributes=True)

    project_id: UUID
    version: int
    snapshot: dict[str, Any]
    message: str | None = None
    created_by: UUID | None = None
    created_at: datetime | None = None


class ProjectShareResponse(BaseModel):
    """Generated revocable share link response."""

    share_url: str
    visibility: Literal["link", "public"]


class ExportRequest(BaseModel):
    """Project export request (MP4 deferred to Public phase)."""

    # PHASE2: add "mp4" WebCodecs/server export in Public phase
    format: Literal["csv", "json", "fits", "png", "gif", "ades"]
    object_ids: list[UUID] | None = None


class ProjectFileLinkRequest(BaseModel):
    """Link a registered FITS file to a project workspace."""

    fits_file_id: UUID
