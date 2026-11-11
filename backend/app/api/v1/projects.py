"""Project workspace, notes, findings, saved views, versions, sharing, and exports."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import COMMON_501, not_implemented
from app.schemas.common import JobAcceptedResponse, PaginatedResponse
from app.schemas.projects import (
    ExportRequest,
    FindingCreate,
    FindingRead,
    FindingUpdate,
    NoteCreate,
    NoteRead,
    NoteUpdate,
    ProjectCreate,
    ProjectRead,
    ProjectShareResponse,
    ProjectUpdate,
    ProjectVersionCreate,
    ProjectVersionRead,
    SavedViewCreate,
    SavedViewRead,
)

router = APIRouter(prefix="/projects", tags=["projects"], responses=COMMON_501)


@router.get("")
async def list_projects(cursor: str | None = None) -> PaginatedResponse[ProjectRead]:
    """List accessible projects."""
    not_implemented()


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(payload: ProjectCreate) -> ProjectRead:
    """Create a project workspace."""
    not_implemented()


@router.get("/{project_id}")
async def get_project(project_id: UUID) -> ProjectRead:
    """Fetch project workspace details."""
    not_implemented()


@router.patch("/{project_id}")
async def update_project(project_id: UUID, payload: ProjectUpdate) -> ProjectRead:
    """Update project metadata or visibility."""
    not_implemented()


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: UUID) -> None:
    """Delete a project workspace."""
    not_implemented()


@router.get("/{project_id}/notes")
async def list_notes(project_id: UUID) -> list[NoteRead]:
    """List project notes."""
    not_implemented()


@router.post("/{project_id}/notes", status_code=status.HTTP_201_CREATED)
async def create_note(project_id: UUID, payload: NoteCreate) -> NoteRead:
    """Add a markdown note to a project."""
    not_implemented()


@router.patch("/{project_id}/notes/{note_id}")
async def update_note(project_id: UUID, note_id: UUID, payload: NoteUpdate) -> NoteRead:
    """Edit a project note."""
    not_implemented()


@router.delete("/{project_id}/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(project_id: UUID, note_id: UUID) -> None:
    """Delete a project note."""
    not_implemented()


@router.get("/{project_id}/findings")
async def list_findings(project_id: UUID) -> list[FindingRead]:
    """List scientific findings for a project."""
    not_implemented()


@router.post("/{project_id}/findings", status_code=status.HTTP_201_CREATED)
async def create_finding(project_id: UUID, payload: FindingCreate) -> FindingRead:
    """Create a scientific finding entry."""
    not_implemented()


@router.patch("/{project_id}/findings/{finding_id}")
async def update_finding(project_id: UUID, finding_id: UUID, payload: FindingUpdate) -> FindingRead:
    """Update a finding status or evidence."""
    not_implemented()


@router.delete("/{project_id}/findings/{finding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_finding(project_id: UUID, finding_id: UUID) -> None:
    """Delete a finding."""
    not_implemented()


@router.get("/{project_id}/views")
async def list_saved_views(project_id: UUID) -> list[SavedViewRead]:
    """List saved viewer URL states."""
    not_implemented()


@router.post("/{project_id}/views", status_code=status.HTTP_201_CREATED)
async def create_saved_view(project_id: UUID, payload: SavedViewCreate) -> SavedViewRead:
    """Save a viewer URL bookmark."""
    not_implemented()


@router.delete("/{project_id}/views/{view_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_saved_view(project_id: UUID, view_id: UUID) -> None:
    """Remove a saved viewer bookmark."""
    not_implemented()


@router.get("/{project_id}/versions")
async def list_versions(project_id: UUID) -> list[ProjectVersionRead]:
    """List immutable project snapshots."""
    not_implemented()


@router.post("/{project_id}/versions", status_code=status.HTTP_201_CREATED)
async def create_version(project_id: UUID, payload: ProjectVersionCreate) -> ProjectVersionRead:
    """Snapshot current project state."""
    not_implemented()


@router.post("/{project_id}/versions/{version}/restore")
async def restore_version(project_id: UUID, version: int) -> ProjectRead:
    """Restore project state from a snapshot version."""
    not_implemented()


@router.post("/{project_id}/share")
async def create_share_link(project_id: UUID) -> ProjectShareResponse:
    """Create or rotate revocable project share token."""
    not_implemented()


@router.delete("/{project_id}/share", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_share_link(project_id: UUID) -> None:
    """Revoke share link and revert visibility to private."""
    not_implemented()


@router.post("/{project_id}/exports", status_code=status.HTTP_202_ACCEPTED)
async def export_project(project_id: UUID, payload: ExportRequest) -> JobAcceptedResponse:
    """Enqueue asynchronous CSV/JSON/FITS/PNG/GIF/ADES export."""
    not_implemented()
