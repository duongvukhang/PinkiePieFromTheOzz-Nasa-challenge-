"""FITS upload staging, security pre-checks, async validation, and compatibility reports."""

from __future__ import annotations

import contextlib
import math
import re
import zlib
from pathlib import Path
from typing import Annotated, Any, NoReturn
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, UploadFile, status

from app.api.deps import APIProblemError, get_optional_user_id, get_storage, get_store
from app.schemas.common import JobStatus, JobStatusResponse, ProblemDetail
from app.schemas.fields import UploadCompatibilityReport, UploadResponse
from app.services.fits_validator import get_upload_max_bytes
from app.services.jobs import JobStore, compute_params_hash
from app.services.storage import StorageBackend, compute_sha256
from app.workers.celery_app import dispatch_task, is_eager_mode
from app.workers.tasks_fast import UPLOAD_TASK_NAME, execute_validate_upload, validate_upload
from sphx_core.timeutil import parse_user_epoch

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_CHUNK_BYTES = 1024 * 1024

router = APIRouter(
    tags=["uploads"],
    responses={
        404: {"model": ProblemDetail},
        409: {"model": ProblemDetail},
        413: {"model": ProblemDetail},
        422: {"model": ProblemDetail},
        502: {"model": ProblemDetail},
    },
)


def _safe_filename(raw_name: str | None, fallback: str) -> str:
    leaf = Path(raw_name.replace("\\", "/")).name.strip() if raw_name else ""
    cleaned = _SAFE_NAME_RE.sub("_", leaf).strip("._")
    return cleaned[:128] if cleaned else fallback


def _too_large(title: str, detail: str) -> NoReturn:
    raise APIProblemError(
        413,
        title,
        detail,
        error_type="https://spherex.example/problems/upload-too-large",
        science_impact="Rejected before FITS parsing to protect worker memory.",
    )


def _normalize_user_epoch(raw: str | None) -> float | None:
    if raw is None or not raw.strip():
        return None
    text = raw.strip()
    try:
        try:
            num = float(text)
        except ValueError:
            parsed = parse_user_epoch(text)
        else:
            if not math.isfinite(num):
                raise ValueError("non-finite MJD")
            parsed = parse_user_epoch(num)
    except Exception as exc:
        raise APIProblemError(
            422,
            "Invalid User Epoch",
            f"Could not parse user_epoch {raw!r}: {exc}",
            error_type="https://spherex.example/problems/invalid-epoch",
        ) from exc
    return float(parsed.tdb.mjd)


async def _read_upload_limited(file: UploadFile, max_bytes: int) -> bytes:
    if file.size is not None and file.size > max_bytes:
        _too_large(
            "Upload Too Large", f"FITS upload ({file.size} B) exceeds limit ({max_bytes} B)."
        )
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_CHUNK_BYTES):
        total += len(chunk)
        if total > max_bytes:
            _too_large("Upload Too Large", f"FITS upload exceeds limit ({max_bytes} B).")
        chunks.append(chunk)
    payload = b"".join(chunks)
    if not payload:
        raise APIProblemError(
            422,
            "Empty Upload",
            "Uploaded FITS file is empty (0 bytes).",
            error_type="https://spherex.example/problems/empty-upload",
        )
    return payload


def _check_gzip_bomb(raw: bytes, max_bytes: int) -> None:
    if not raw.startswith(b"\x1f\x8b"):
        return
    dec = zlib.decompressobj(16 + zlib.MAX_WBITS)
    decompressed = 0
    mv = memoryview(raw)
    try:
        for offset in range(0, len(raw), 65536):
            out = dec.decompress(
                mv[offset : offset + 65536], max_length=max_bytes - decompressed + 1
            )
            decompressed += len(out)
            if decompressed > max_bytes or dec.unconsumed_tail:
                _too_large(
                    "Compressed Upload Too Large",
                    f"Decompressed FITS payload exceeds limit ({max_bytes} B).",
                )
    except zlib.error:
        return


def _require_succeeded_upload(
    upload_id: UUID, store: JobStore, *, label: str = "Upload"
) -> JobStatusResponse:
    job = store.get_upload_job(upload_id)
    if job is None:
        raise APIProblemError(
            status.HTTP_404_NOT_FOUND, f"{label} Not Found", f"No upload found for {upload_id}."
        )
    if job.status in {"queued", "running"}:
        raise APIProblemError(
            status.HTTP_409_CONFLICT,
            f"{label} In Progress",
            f"Upload job {job.id} is {job.status} ({int((job.progress or 0.0) * 100)}%).",
        )
    if job.status != "succeeded" or not job.outputs or "report" not in job.outputs:
        detail = (
            str(job.error.get("detail"))
            if job.error and job.error.get("detail")
            else f"{label} validation failed."
        )
        raise APIProblemError(status.HTTP_502_BAD_GATEWAY, f"{label} Failed", detail)
    return job


@router.post("/uploads", status_code=status.HTTP_202_ACCEPTED)
async def upload_fits(
    file: Annotated[UploadFile, File()],
    store: Annotated[JobStore, Depends(get_store)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
    user_epoch: Annotated[str | None, Form()] = None,
    reference_upload_id: Annotated[UUID | None, Form()] = None,
    reference_ra_deg: Annotated[float | None, Form(ge=0.0, lt=360.0)] = None,
    reference_dec_deg: Annotated[float | None, Form(ge=-90.0, le=90.0)] = None,
    reference_fov_arcmin: Annotated[float | None, Form(gt=0.0, le=60.0)] = None,
    project_id: Annotated[UUID | None, Form()] = None,
    user_id: Annotated[UUID | None, Depends(get_optional_user_id)] = None,
) -> UploadResponse:
    """Upload a FITS image for async WCS/epoch inspection and compatibility checking."""
    # PHASE2: add tus resumable chunked upload protocol per API.md §3
    epoch_mjd = _normalize_user_epoch(user_epoch)
    has_ra, has_dec = reference_ra_deg is not None, reference_dec_deg is not None
    if has_ra != has_dec:
        raise APIProblemError(
            422,
            "Incomplete Reference Field",
            "Both reference_ra_deg and reference_dec_deg are required together.",
        )
    if reference_upload_id is not None and has_ra:
        raise APIProblemError(
            422,
            "Ambiguous Reference",
            "Supply either reference_upload_id or reference_ra_deg/reference_dec_deg, not both.",
        )
    if reference_upload_id is not None:
        ref_job = _require_succeeded_upload(reference_upload_id, store, label="Reference Upload")
        outputs = ref_job.outputs or {}
        if not outputs.get("report", {}).get("valid") or not (
            outputs.get("fits_file_metadata") or {}
        ).get("footprint"):
            raise APIProblemError(
                422,
                "Invalid Reference Upload",
                "Reference upload lacks a valid WCS/epoch for compatibility comparison.",
            )

    max_bytes = get_upload_max_bytes(authenticated=user_id is not None)
    raw = await _read_upload_limited(file, max_bytes)
    _check_gzip_bomb(raw, max_bytes)

    upload_id, fits_file_id = uuid4(), uuid4()
    filename = _safe_filename(file.filename, f"{upload_id}.fits")
    ref_field = (
        {
            "ra_deg": float(reference_ra_deg),  # type: ignore[arg-type]
            "dec_deg": float(reference_dec_deg),  # type: ignore[arg-type]
            "fov_arcmin": float(reference_fov_arcmin or 10.0),
        }
        if has_ra
        else None
    )
    norm_params: dict[str, Any] = {
        "payload_sha256": compute_sha256(raw),
        "user_epoch": epoch_mjd,
        "reference_upload_id": str(reference_upload_id) if reference_upload_id else None,
        "reference_field": ref_field,
        "project_id": str(project_id) if project_id else None,
        "user_id": str(user_id) if user_id else None,
    }
    staged_key = f"uploads/staging/{upload_id}/{filename}"
    job_params: dict[str, Any] = {
        **norm_params,
        "upload_id": str(upload_id),
        "fits_file_id": str(fits_file_id),
        "filename": filename,
        "max_bytes": max_bytes,
        "staged_key": staged_key,
        "final_key": f"uploads/{user_id or 'anonymous'}/{upload_id}/{filename}",
    }

    job, deduplicated = store.create_or_get_job(
        task=UPLOAD_TASK_NAME,
        queue="fast",
        params=job_params,
        params_hash=compute_params_hash(UPLOAD_TASK_NAME, norm_params),
        user_id=user_id,
        project_id=project_id,
    )
    if deduplicated:
        report_out = (job.outputs or {}).get("report", {})
        existing = store.get_job_params(job.id) or {}
        upload_id = UUID(str(report_out.get("upload_id") or existing.get("upload_id") or upload_id))
        fits_file_id = UUID(
            str(report_out.get("fits_file_id") or existing.get("fits_file_id") or fits_file_id)
        )
        job_status: JobStatus = job.status
    else:
        storage.put_bytes(staged_key, raw, content_type="application/octet-stream")
        if is_eager_mode():
            with contextlib.suppress(Exception):
                execute_validate_upload(job.id, job_params, store=store, storage=storage)
        else:
            dispatch_task(validate_upload, str(job.id), job_params, queue="fast")
        latest = store.get_job(job.id)
        job_status = latest.status if latest else job.status

    return UploadResponse(
        job_id=job.id, status=job_status, upload_id=upload_id, fits_file_id=fits_file_id
    )


@router.get("/uploads/{upload_id}/report")
async def get_upload_report(
    upload_id: UUID,
    store: Annotated[JobStore, Depends(get_store)],
) -> UploadCompatibilityReport:
    """Retrieve FITS validation and multi-epoch compatibility report."""
    job = _require_succeeded_upload(upload_id, store)
    assert job.outputs is not None
    return UploadCompatibilityReport.model_validate(job.outputs["report"])
