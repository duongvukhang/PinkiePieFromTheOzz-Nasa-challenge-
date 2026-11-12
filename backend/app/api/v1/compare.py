"""A-vs-B FITS comparison endpoints (ZOGY diff, sum, ratio, header diff, source changes)."""

from __future__ import annotations

import contextlib
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import (
    APIProblemError,
    get_optional_user_id,
    get_storage,
    get_store,
    require_succeeded_job,
)
from app.schemas.common import JobStatus, JobStatusResponse, ProblemDetail
from app.schemas.objects import CompareAcceptedResponse, CompareRequest, CompareResultResponse
from app.services.jobs import JobStore, compute_params_hash
from app.services.storage import StorageBackend
from app.workers.celery_app import dispatch_task, is_eager_mode
from app.workers.tasks_fast import UPLOAD_TASK_NAME
from app.workers.tasks_heavy import COMPARE_TASK_NAME, execute_run_compare, run_compare

router = APIRouter(tags=["compare"])

_COMPARE_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ProblemDetail},
    409: {"model": ProblemDetail},
    422: {"model": ProblemDetail},
    502: {"model": ProblemDetail},
}

_PRODUCT_SPECS: dict[str, tuple[str, str]] = {
    "diff.fits": ("diff", "application/fits"),
    "diff.webp": ("diff_display", "image/webp"),
    "diff.bin": ("diff_science", "application/octet-stream"),
    "score_corr.fits": ("score_corr", "application/fits"),
    "sum.fits": ("sum", "application/fits"),
    "sum.webp": ("sum_display", "image/webp"),
    "sum.bin": ("sum_science", "application/octet-stream"),
    "ratio.fits": ("ratio", "application/fits"),
    "ratio.webp": ("ratio_display", "image/webp"),
    "ratio.bin": ("ratio_science", "application/octet-stream"),
}


def _require_compare_job(store: JobStore, compare_id: UUID) -> JobStatusResponse:
    return require_succeeded_job(
        store.get_compare_job(compare_id),
        compare_id,
        label="Comparison",
        output_key="compare_result",
    )


@router.post("/compare", status_code=status.HTTP_202_ACCEPTED, responses=_COMPARE_RESPONSES)
async def create_comparison(
    payload: CompareRequest,
    store: Annotated[JobStore, Depends(get_store)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
    user_id: Annotated[UUID | None, Depends(get_optional_user_id)] = None,
) -> CompareAcceptedResponse:
    """Launch an A-vs-B FITS comparison job (diff, sum, ratio, headers, sources)."""
    for fid in (payload.a, payload.b):
        if store.get_fits_file(fid) is None:
            up_job = store.get_job_by_param(UPLOAD_TASK_NAME, "fits_file_id", str(fid))
            if up_job is not None and up_job.status in {"queued", "running"}:
                raise APIProblemError(
                    409,
                    "Upload Validation In Progress",
                    f"FITS file {fid} upload job {up_job.id} is {up_job.status}.",
                )
            raise APIProblemError(404, "FITS File Not Found", f"No FITS file found for {fid}.")

    ops_unique = list(dict.fromkeys(payload.ops))
    base = {"fits_a": str(payload.a), "fits_b": str(payload.b), "psf_match": payload.psf_match}
    norm_params: dict[str, Any] = {**base, "ops": sorted(ops_unique)}
    compare_id = uuid4()
    job_params: dict[str, Any] = {**base, "compare_id": str(compare_id), "ops": ops_unique}
    job, deduplicated = store.create_or_get_job(
        task=COMPARE_TASK_NAME,
        queue="heavy",
        params=job_params,
        params_hash=compute_params_hash(COMPARE_TASK_NAME, norm_params),
        user_id=user_id,
    )
    if deduplicated:
        existing = store.get_job_params(job.id) or {}
        compare_id = UUID(
            str((job.outputs or {}).get("compare_id") or existing.get("compare_id") or compare_id)
        )
        job_status: JobStatus = job.status
    else:
        store.create_comparison_job(
            compare_id=compare_id,
            fits_a=payload.a,
            fits_b=payload.b,
            ops=ops_unique,
            params={"psf_match": payload.psf_match},
            status=job.status,
            processing_job_id=job.id,
        )
        if is_eager_mode():
            with contextlib.suppress(Exception):
                execute_run_compare(job.id, job_params, store=store, storage=storage)
        else:
            dispatch_task(run_compare, str(job.id), job_params, queue="heavy")
        latest = store.get_job(job.id)
        job_status = latest.status if latest else job.status

    return CompareAcceptedResponse(job_id=job.id, status=job_status, compare_id=compare_id)


@router.get("/compare/{compare_id}", responses=_COMPARE_RESPONSES)
async def get_comparison(
    compare_id: UUID,
    store: Annotated[JobStore, Depends(get_store)],
) -> CompareResultResponse:
    """Retrieve A-vs-B comparison products, header diff, and source changes."""
    job = _require_compare_job(store, compare_id)
    assert job.outputs is not None
    return CompareResultResponse.model_validate(job.outputs["compare_result"])


@router.get("/compare/{compare_id}/products/{product_name}", responses=_COMPARE_RESPONSES)
async def get_comparison_product(
    compare_id: UUID,
    product_name: str,
    store: Annotated[JobStore, Depends(get_store)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
) -> Response:
    """Serve FITS, WebP display, or float32 science product generated by an A-vs-B comparison."""
    spec = _PRODUCT_SPECS.get(product_name)
    if spec is None:
        raise APIProblemError(
            404, "Comparison Product Not Found", f"Unknown comparison product {product_name!r}."
        )
    job = _require_compare_job(store, compare_id)
    assert job.outputs is not None
    artifact_key = job.outputs.get("artifacts", {}).get(spec[0])
    if not artifact_key:
        raise APIProblemError(
            404,
            "Comparison Product Not Available",
            f"Product {product_name!r} was not requested for comparison {compare_id}.",
        )
    try:
        data = storage.get_bytes(str(artifact_key), verify_checksum=True)
    except FileNotFoundError as exc:
        raise APIProblemError(404, "Artifact Missing in Storage") from exc

    headers: dict[str, str] = {"Cache-Control": "public, max-age=2592000, immutable"}
    if product_name.endswith(".fits"):
        headers["Content-Disposition"] = f'attachment; filename="{compare_id}_{product_name}"'
    elif product_name.endswith(".bin"):
        shape = job.outputs.get("shapes", {}).get(product_name, [])
        headers["X-Dtype"] = "float32"
        headers["X-Shape"] = ",".join(str(x) for x in shape)
    return Response(content=data, media_type=spec[1], headers=headers)
