"""Project workspace, notes, findings, saved views, versions, sharing, and exports."""

from __future__ import annotations

import contextlib
import hmac
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.api.deps import (
    APIProblemError,
    get_auth_context,
    get_storage,
    get_store,
    paginate_items,
    require_auth,
    require_succeeded_job,
    require_write,
)
from app.schemas.common import JobAcceptedResponse, JobStatus, PaginatedResponse, ProblemDetail
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
from app.services.auth import (
    AuthContext,
    client_ip_from_request,
    generate_share_token,
    has_write_scope,
    hash_token,
)
from app.services.exports import has_consensus_conflict
from app.services.jobs import JobStore, compute_params_hash
from app.services.storage import StorageBackend
from app.workers.celery_app import dispatch_task, is_eager_mode
from app.workers.tasks_fast import (
    EXPORT_TASK_NAME,
    execute_export_project,
)
from app.workers.tasks_fast import export_project as export_project_task

router = APIRouter(
    prefix="/projects",
    tags=["projects"],
    responses={
        401: {"model": ProblemDetail},
        403: {"model": ProblemDetail},
        404: {"model": ProblemDetail},
        409: {"model": ProblemDetail},
        422: {"model": ProblemDetail},
        502: {"model": ProblemDetail},
    },
)


def _require_project_access(
    project_id: UUID,
    request: Request,
    store: JobStore,
    ctx: AuthContext | None,
    *,
    share_token: str | None = None,
    min_role: Literal["read", "editor", "owner"] = "read",
) -> dict[str, Any]:
    proj = store.get_project(project_id)
    if proj is None:
        raise APIProblemError(
            status.HTTP_404_NOT_FOUND,
            "Project Not Found",
            f"No project found for {project_id}.",
        )
    role = store.get_project_role(project_id, ctx.user.id) if ctx is not None else None
    if min_role == "read":
        tok = share_token or request.headers.get("X-Share-Token")
        stored_hash = proj.get("share_token_hash")
        link_ok = (
            proj["visibility"] == "link"
            and bool(tok)
            and stored_hash is not None
            and hmac.compare_digest(hash_token(str(tok)), bytes(stored_hash))
        )
        if role in {"owner", "editor", "viewer"} or proj["visibility"] == "public" or link_ok:
            return proj
        if ctx is None:
            raise APIProblemError(
                status.HTTP_401_UNAUTHORIZED,
                "Authentication Required",
                "Authentication or a valid share token is required for this project.",
                error_type="https://spherex.example/problems/unauthorized",
                headers={"WWW-Authenticate": "Bearer"},
            )
        raise APIProblemError(
            status.HTTP_403_FORBIDDEN,
            "Project Access Denied",
            "You do not have permission to view this project.",
        )

    if ctx is None:
        raise APIProblemError(
            status.HTTP_401_UNAUTHORIZED,
            "Authentication Required",
            "Authentication is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not has_write_scope(ctx.scopes):
        raise APIProblemError(
            status.HTTP_403_FORBIDDEN,
            "Insufficient Token Scope",
            "Token lacks write scope for this operation.",
            error_type="https://spherex.example/problems/insufficient-scope",
        )
    allowed = {"owner"} if min_role == "owner" else {"owner", "editor"}
    if role not in allowed:
        raise APIProblemError(
            status.HTTP_403_FORBIDDEN,
            "Insufficient Project Role",
            f"Requires project role {', '.join(sorted(allowed))}.",
        )
    return proj


def _audit(
    store: JobStore,
    ctx: AuthContext,
    request: Request,
    action: str,
    table: str,
    entity_id: Any,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    store.record_audit(
        ctx.user.id,
        action,
        entity_table=table,
        entity_id=str(entity_id),
        before=before,
        after=after,
        ip=client_ip_from_request(request),
    )


def _require_found(val: Any, label: str, item_id: Any) -> Any:
    if val is None or val is False:
        raise APIProblemError(
            status.HTTP_404_NOT_FOUND, f"{label} Not Found", f"No {label.lower()} {item_id}."
        )
    return val


def _check_finding_consensus(
    store: JobStore, project_id: UUID, object_id: UUID | None, summary_md: str | None
) -> None:
    if object_id is None:
        return
    bundle = store.get_tracked_object_bundle(object_id)
    if bundle is None or bundle.get("project_id") != project_id:
        raise APIProblemError(
            status.HTTP_404_NOT_FOUND,
            "Tracked Object Not Found",
            f"Tracked object {object_id} does not belong to project {project_id}.",
        )
    if has_consensus_conflict(bundle) and not (summary_md and summary_md.strip()):
        raise APIProblemError(
            422,
            "Consensus Conflict Requires Note",
            "Object has a consensus Conflict; provide a non-empty summary_md note.",
            error_type="https://spherex.example/problems/consensus-conflict",
            science_impact="Conflicting estimator verdicts require user explanation before saving.",
        )


@router.get("")
async def list_projects(
    store: Annotated[JobStore, Depends(get_store)],
    ctx: Annotated[AuthContext, Depends(require_auth)],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> PaginatedResponse[ProjectRead]:
    """List accessible projects."""
    page, next_cursor = paginate_items(store.list_projects_for_user(ctx.user.id), cursor, limit)
    return PaginatedResponse(items=page, next_cursor=next_cursor)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    request: Request,
    store: Annotated[JobStore, Depends(get_store)],
    ctx: Annotated[AuthContext, Depends(require_write)],
) -> ProjectRead:
    """Create a project workspace."""
    proj = store.create_project(ctx.user.id, payload)
    _audit(
        store,
        ctx,
        request,
        "project.create",
        "projects",
        proj.id,
        after=proj.model_dump(mode="json"),
    )
    return proj


@router.get("/{project_id}")
async def get_project(
    project_id: UUID,
    request: Request,
    store: Annotated[JobStore, Depends(get_store)],
    ctx: Annotated[AuthContext | None, Depends(get_auth_context)] = None,
    share_token: str | None = None,
) -> ProjectRead:
    """Fetch project workspace details."""
    proj = _require_project_access(
        project_id, request, store, ctx, share_token=share_token, min_role="read"
    )
    return ProjectRead.model_validate(proj)


@router.patch("/{project_id}")
async def update_project(
    project_id: UUID,
    payload: ProjectUpdate,
    request: Request,
    store: Annotated[JobStore, Depends(get_store)],
    ctx: Annotated[AuthContext, Depends(require_write)],
) -> ProjectRead:
    """Update project metadata or visibility."""
    proj = _require_project_access(project_id, request, store, ctx, min_role="editor")
    patch = payload.model_dump(exclude_unset=True)
    if "visibility" in patch and patch["visibility"] != proj["visibility"]:
        _require_project_access(project_id, request, store, ctx, min_role="owner")
    updated: ProjectRead = _require_found(
        store.update_project(project_id, patch), "Project", project_id
    )
    _audit(
        store,
        ctx,
        request,
        "project.update",
        "projects",
        project_id,
        before={k: proj.get(k) for k in patch},
        after=patch,
    )
    return updated


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    request: Request,
    store: Annotated[JobStore, Depends(get_store)],
    ctx: Annotated[AuthContext, Depends(require_write)],
) -> None:
    """Delete a project workspace."""
    _require_project_access(project_id, request, store, ctx, min_role="owner")
    store.delete_project(project_id)
    _audit(store, ctx, request, "project.delete", "projects", project_id)


@router.get("/{project_id}/notes")
async def list_notes(
    project_id: UUID,
    request: Request,
    store: Annotated[JobStore, Depends(get_store)],
    ctx: Annotated[AuthContext | None, Depends(get_auth_context)] = None,
    share_token: str | None = None,
) -> list[NoteRead]:
    """List project notes."""
    _require_project_access(
        project_id, request, store, ctx, share_token=share_token, min_role="read"
    )
    return store.list_notes(project_id)


@router.post("/{project_id}/notes", status_code=status.HTTP_201_CREATED)
async def create_note(
    project_id: UUID,
    payload: NoteCreate,
    request: Request,
    store: Annotated[JobStore, Depends(get_store)],
    ctx: Annotated[AuthContext, Depends(require_write)],
) -> NoteRead:
    """Add a markdown note to a project."""
    _require_project_access(project_id, request, store, ctx, min_role="editor")
    note = store.create_note(project_id, ctx.user.id, payload)
    _audit(store, ctx, request, "note.create", "notes", note.id)
    return note


@router.patch("/{project_id}/notes/{note_id}")
async def update_note(
    project_id: UUID,
    note_id: UUID,
    payload: NoteUpdate,
    request: Request,
    store: Annotated[JobStore, Depends(get_store)],
    ctx: Annotated[AuthContext, Depends(require_write)],
) -> NoteRead:
    """Edit a project note."""
    _require_project_access(project_id, request, store, ctx, min_role="editor")
    updated: NoteRead = _require_found(
        store.update_note(project_id, note_id, payload.model_dump(exclude_unset=True)),
        "Note",
        note_id,
    )
    _audit(store, ctx, request, "note.update", "notes", note_id)
    return updated


@router.delete("/{project_id}/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(
    project_id: UUID,
    note_id: UUID,
    request: Request,
    store: Annotated[JobStore, Depends(get_store)],
    ctx: Annotated[AuthContext, Depends(require_write)],
) -> None:
    """Delete a project note."""
    _require_project_access(project_id, request, store, ctx, min_role="editor")
    _require_found(store.delete_note(project_id, note_id), "Note", note_id)
    _audit(store, ctx, request, "note.delete", "notes", note_id)


@router.get("/{project_id}/findings")
async def list_findings(
    project_id: UUID,
    request: Request,
    store: Annotated[JobStore, Depends(get_store)],
    ctx: Annotated[AuthContext | None, Depends(get_auth_context)] = None,
    share_token: str | None = None,
) -> list[FindingRead]:
    """List scientific findings for a project."""
    _require_project_access(
        project_id, request, store, ctx, share_token=share_token, min_role="read"
    )
    return store.list_findings(project_id)


@router.post("/{project_id}/findings", status_code=status.HTTP_201_CREATED)
async def create_finding(
    project_id: UUID,
    payload: FindingCreate,
    request: Request,
    store: Annotated[JobStore, Depends(get_store)],
    ctx: Annotated[AuthContext, Depends(require_write)],
) -> FindingRead:
    """Create a scientific finding entry."""
    _require_project_access(project_id, request, store, ctx, min_role="editor")
    _check_finding_consensus(store, project_id, payload.tracked_object_id, payload.summary_md)
    finding = store.create_finding(project_id, payload)
    _audit(store, ctx, request, "finding.create", "findings", finding.id)
    return finding


@router.patch("/{project_id}/findings/{finding_id}")
async def update_finding(
    project_id: UUID,
    finding_id: UUID,
    payload: FindingUpdate,
    request: Request,
    store: Annotated[JobStore, Depends(get_store)],
    ctx: Annotated[AuthContext, Depends(require_write)],
) -> FindingRead:
    """Update a finding status or evidence."""
    _require_project_access(project_id, request, store, ctx, min_role="editor")
    patch = payload.model_dump(exclude_unset=True)
    existing: FindingRead = _require_found(
        next((f for f in store.list_findings(project_id) if f.id == finding_id), None),
        "Finding",
        finding_id,
    )
    _check_finding_consensus(
        store, project_id, existing.tracked_object_id, patch.get("summary_md", existing.summary_md)
    )
    updated: FindingRead = _require_found(
        store.update_finding(project_id, finding_id, patch), "Finding", finding_id
    )
    _audit(store, ctx, request, "finding.update", "findings", finding_id, after=patch)
    return updated


@router.delete("/{project_id}/findings/{finding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_finding(
    project_id: UUID,
    finding_id: UUID,
    request: Request,
    store: Annotated[JobStore, Depends(get_store)],
    ctx: Annotated[AuthContext, Depends(require_write)],
) -> None:
    """Delete a finding."""
    _require_project_access(project_id, request, store, ctx, min_role="editor")
    _require_found(store.delete_finding(project_id, finding_id), "Finding", finding_id)
    _audit(store, ctx, request, "finding.delete", "findings", finding_id)


@router.get("/{project_id}/views")
async def list_saved_views(
    project_id: UUID,
    request: Request,
    store: Annotated[JobStore, Depends(get_store)],
    ctx: Annotated[AuthContext | None, Depends(get_auth_context)] = None,
    share_token: str | None = None,
) -> list[SavedViewRead]:
    """List saved viewer URL states."""
    _require_project_access(
        project_id, request, store, ctx, share_token=share_token, min_role="read"
    )
    return store.list_saved_views(project_id)


@router.post("/{project_id}/views", status_code=status.HTTP_201_CREATED)
async def create_saved_view(
    project_id: UUID,
    payload: SavedViewCreate,
    request: Request,
    store: Annotated[JobStore, Depends(get_store)],
    ctx: Annotated[AuthContext, Depends(require_write)],
) -> SavedViewRead:
    """Save a viewer URL bookmark."""
    _require_project_access(project_id, request, store, ctx, min_role="editor")
    view = store.create_saved_view(project_id, payload)
    _audit(store, ctx, request, "saved_view.create", "saved_views", view.id)
    return view


@router.delete("/{project_id}/views/{view_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_saved_view(
    project_id: UUID,
    view_id: UUID,
    request: Request,
    store: Annotated[JobStore, Depends(get_store)],
    ctx: Annotated[AuthContext, Depends(require_write)],
) -> None:
    """Remove a saved viewer bookmark."""
    _require_project_access(project_id, request, store, ctx, min_role="editor")
    _require_found(store.delete_saved_view(project_id, view_id), "Saved View", view_id)
    _audit(store, ctx, request, "saved_view.delete", "saved_views", view_id)


@router.get("/{project_id}/versions")
async def list_versions(
    project_id: UUID,
    request: Request,
    store: Annotated[JobStore, Depends(get_store)],
    ctx: Annotated[AuthContext | None, Depends(get_auth_context)] = None,
    share_token: str | None = None,
) -> list[ProjectVersionRead]:
    """List immutable project snapshots."""
    _require_project_access(
        project_id, request, store, ctx, share_token=share_token, min_role="read"
    )
    return store.list_project_versions(project_id)


@router.post("/{project_id}/versions", status_code=status.HTTP_201_CREATED)
async def create_version(
    project_id: UUID,
    payload: ProjectVersionCreate,
    request: Request,
    store: Annotated[JobStore, Depends(get_store)],
    ctx: Annotated[AuthContext, Depends(require_write)],
) -> ProjectVersionRead:
    """Snapshot current project state."""
    _require_project_access(project_id, request, store, ctx, min_role="editor")
    ver = store.create_project_version(project_id, ctx.user.id, payload.message)
    _audit(
        store,
        ctx,
        request,
        "project.version.create",
        "project_versions",
        f"{project_id}:{ver.version}",
    )
    return ver


@router.post("/{project_id}/versions/{version}/restore")
async def restore_version(
    project_id: UUID,
    version: int,
    request: Request,
    store: Annotated[JobStore, Depends(get_store)],
    ctx: Annotated[AuthContext, Depends(require_write)],
) -> ProjectRead:
    """Restore project state from a snapshot version."""
    _require_project_access(project_id, request, store, ctx, min_role="editor")
    restored: ProjectRead = _require_found(
        store.restore_project_version(project_id, version, ctx.user.id),
        "Project Version",
        version,
    )
    return restored


@router.post("/{project_id}/share")
async def create_share_link(
    project_id: UUID,
    request: Request,
    store: Annotated[JobStore, Depends(get_store)],
    ctx: Annotated[AuthContext, Depends(require_write)],
) -> ProjectShareResponse:
    """Create or rotate revocable project share token."""
    proj = _require_project_access(project_id, request, store, ctx, min_role="owner")
    raw_token, digest = generate_share_token()
    vis: Literal["link", "public"] = "public" if proj["visibility"] == "public" else "link"
    store.set_project_share(project_id, visibility=vis, share_token_hash=digest)
    _audit(
        store,
        ctx,
        request,
        "project.share.create",
        "projects",
        project_id,
        after={"visibility": vis},
    )
    return ProjectShareResponse(
        share_url=f"/projects/{project_id}?share_token={raw_token}",
        visibility=vis,
    )


@router.delete("/{project_id}/share", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_share_link(
    project_id: UUID,
    request: Request,
    store: Annotated[JobStore, Depends(get_store)],
    ctx: Annotated[AuthContext, Depends(require_write)],
) -> None:
    """Revoke share link and revert visibility to private."""
    _require_project_access(project_id, request, store, ctx, min_role="owner")
    store.set_project_share(project_id, visibility="private", share_token_hash=None)
    _audit(
        store,
        ctx,
        request,
        "project.share.revoke",
        "projects",
        project_id,
        after={"visibility": "private"},
    )


@router.post("/{project_id}/exports", status_code=status.HTTP_202_ACCEPTED)
async def export_project(
    project_id: UUID,
    payload: ExportRequest,
    request: Request,
    store: Annotated[JobStore, Depends(get_store)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
    ctx: Annotated[AuthContext, Depends(require_auth)],
) -> JobAcceptedResponse:
    """Enqueue asynchronous CSV/JSON/FITS/PNG/GIF/ADES export."""
    proj = _require_project_access(project_id, request, store, ctx, min_role="read")
    if payload.object_ids is not None:
        try:
            store.list_project_objects(project_id, payload.object_ids)
        except KeyError as exc:
            raise APIProblemError(
                status.HTTP_404_NOT_FOUND, "Tracked Object Not Found", str(exc)
            ) from exc

    obj_ids = [str(o) for o in payload.object_ids] if payload.object_ids else None
    job_params: dict[str, Any] = {
        "project_id": str(project_id),
        "format": payload.format,
        "object_ids": obj_ids,
        "version": int(proj.get("current_version", 1)),
    }
    norm = {**job_params, "object_ids": sorted(obj_ids) if obj_ids else None}
    job, deduplicated = store.create_or_get_job(
        task=EXPORT_TASK_NAME,
        queue="fast",
        params=job_params,
        params_hash=compute_params_hash(EXPORT_TASK_NAME, norm),
        user_id=ctx.user.id,
        project_id=project_id,
    )
    job_status: JobStatus = job.status
    if not deduplicated:
        if is_eager_mode():
            with contextlib.suppress(Exception):
                execute_export_project(job.id, job_params, store=store, storage=storage)
        else:
            dispatch_task(export_project_task, str(job.id), job_params, queue="fast")
        latest = store.get_job(job.id)
        job_status = latest.status if latest else job.status
    _audit(
        store,
        ctx,
        request,
        "project.export",
        "processing_jobs",
        job.id,
        after={"format": payload.format, "project_id": str(project_id)},
    )
    return JobAcceptedResponse(job_id=job.id, status=job_status)


@router.get("/{project_id}/exports/{export_id}")
async def download_project_export(
    project_id: UUID,
    export_id: UUID,
    request: Request,
    store: Annotated[JobStore, Depends(get_store)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
    ctx: Annotated[AuthContext | None, Depends(get_auth_context)] = None,
    share_token: str | None = None,
) -> Response:
    """Download a completed project export artifact."""
    _require_project_access(
        project_id, request, store, ctx, share_token=share_token, min_role="read"
    )
    job = store.get_job(export_id)
    params = store.get_job_params(export_id) or {}
    if job is not None and (
        job.task != EXPORT_TASK_NAME or params.get("project_id") != str(project_id)
    ):
        job = None
    done = require_succeeded_job(job, export_id, label="Export", output_key="storage_key")
    outputs = done.outputs or {}
    data = storage.get_bytes(str(outputs["storage_key"]))
    fname = str(outputs.get("filename", "export"))
    return Response(
        content=data,
        media_type=str(outputs.get("content_type", "application/octet-stream")),
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
