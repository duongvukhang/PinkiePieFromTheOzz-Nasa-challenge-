"""Analyses, tracked objects, associations, spectra, lightcurves, and A-vs-B compare."""

from __future__ import annotations

import contextlib
import math
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import (
    COMMON_501,
    APIProblemError,
    enforce_fov_quota,
    get_gaia_provider,
    get_mpc_provider,
    get_optional_user_id,
    get_spherex_provider,
    get_storage,
    get_store,
    get_wise_provider,
    not_implemented,
    paginate_items,
    require_succeeded_job,
)
from app.providers import GaiaProvider, MpcProvider, SpherexIrsaProvider, WiseProvider
from app.schemas.common import JobAcceptedResponse, JobStatus, PaginatedResponse, ProblemDetail
from app.schemas.objects import (
    AnalysisAcceptedResponse,
    AnalysisCreateRequest,
    AssociationOverrideRequest,
    AssociationOverrideResponse,
    CompareAcceptedResponse,
    CompareRequest,
    CompareResultResponse,
    DetectionRead,
    LightcurveResponse,
    ObjectAssociationsResponse,
    SpectrumResponse,
    SuggestionItem,
    SuggestionsResponse,
    TrackedObjectDetailResponse,
    TrackedObjectSelectRequest,
    TrackedObjectSelectResponse,
    TrackRunRequest,
)
from app.services.jobs import JobStore, compute_params_hash
from app.services.storage import StorageBackend
from app.workers.celery_app import dispatch_task, is_eager_mode
from app.workers.tasks_fast import UPLOAD_TASK_NAME
from app.workers.tasks_heavy import ANALYSIS_TASK_NAME, execute_run_analysis, run_analysis

router = APIRouter(responses=COMMON_501)
_ANALYSIS_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"model": ProblemDetail},
    404: {"model": ProblemDetail},
    409: {"model": ProblemDetail},
    422: {"model": ProblemDetail},
    502: {"model": ProblemDetail},
}


def _parse_bbox(raw: str) -> tuple[float, float, float, float]:
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 4:
        raise APIProblemError(
            422,
            "Invalid Bounding Box",
            "bbox must be 'ra_min,dec_min,ra_max,dec_max' in ICRS degrees.",
            error_type="https://spherex.example/problems/invalid-bbox",
        )
    try:
        ra_min, dec_min, ra_max, dec_max = (float(p) for p in parts)
    except ValueError as exc:
        raise APIProblemError(
            422,
            "Invalid Bounding Box",
            f"bbox coordinates must be numeric: {exc}",
            error_type="https://spherex.example/problems/invalid-bbox",
        ) from exc
    if not all(math.isfinite(v) for v in (ra_min, dec_min, ra_max, dec_max)):
        raise APIProblemError(422, "Invalid Bounding Box", "bbox coordinates must be finite.")
    if not (0.0 <= ra_min < 360.0 and 0.0 <= ra_max <= 360.0):
        raise APIProblemError(422, "Invalid Bounding Box", "RA bounds must lie in [0, 360].")
    if not (-90.0 <= dec_min <= dec_max <= 90.0):
        raise APIProblemError(
            422, "Invalid Bounding Box", "Dec bounds must satisfy -90 <= dec_min <= dec_max <= 90."
        )
    return ra_min, dec_min, ra_max, dec_max


@router.post(
    "/analyses",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["analyses"],
    responses=_ANALYSIS_RESPONSES,
)
async def create_analysis(
    payload: AnalysisCreateRequest,
    store: Annotated[JobStore, Depends(get_store)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
    spherex: Annotated[SpherexIrsaProvider, Depends(get_spherex_provider)],
    gaia: Annotated[GaiaProvider, Depends(get_gaia_provider)],
    wise: Annotated[WiseProvider, Depends(get_wise_provider)],
    mpc: Annotated[MpcProvider, Depends(get_mpc_provider)],
    user_id: Annotated[UUID | None, Depends(get_optional_user_id)] = None,
) -> AnalysisAcceptedResponse:
    """Run registration, source extraction, and object suggestion on a frame set."""
    has_files = payload.fits_file_ids is not None
    has_cutout = payload.cutout_id is not None
    has_sky = payload.ra is not None or payload.dec is not None
    mode_count = sum((has_files, has_cutout, has_sky))
    if mode_count == 0:
        raise APIProblemError(
            422,
            "Missing Analysis Input",
            "Provide fits_file_ids, cutout_id, or ra/dec coordinates.",
        )
    if mode_count > 1:
        raise APIProblemError(
            422,
            "Ambiguous Analysis Input",
            "Provide exactly one of fits_file_ids, cutout_id, or ra/dec.",
        )

    release_key = payload.release or ""
    if has_files:
        assert payload.fits_file_ids is not None
        unique_ids = list(dict.fromkeys(payload.fits_file_ids))
        if not unique_ids:
            raise APIProblemError(422, "Empty Frame Set", "fits_file_ids must not be empty.")
        for fid in unique_ids:
            if store.get_fits_file(fid) is None:
                up_job = store.get_job_by_param(UPLOAD_TASK_NAME, "fits_file_id", str(fid))
                if up_job is not None and up_job.status in {"queued", "running"}:
                    raise APIProblemError(
                        409,
                        "Upload Validation In Progress",
                        f"FITS file {fid} upload job {up_job.id} is {up_job.status}.",
                    )
                raise APIProblemError(404, "FITS File Not Found", f"No FITS file found for {fid}.")
        norm_params: dict[str, Any] = {
            "mode": "fits_files",
            "fits_file_ids": sorted(str(fid) for fid in unique_ids),
        }
        job_params: dict[str, Any] = {
            "mode": "fits_files",
            "fits_file_ids": [str(fid) for fid in unique_ids],
        }
    elif has_cutout:
        assert payload.cutout_id is not None
        require_succeeded_job(
            store.get_cutout_job(payload.cutout_id),
            payload.cutout_id,
            label="Cutout Set",
            output_key="cutout_set",
        )
        norm_params = {"mode": "cutout", "cutout_id": str(payload.cutout_id)}
        job_params = dict(norm_params)
    else:
        if payload.ra is None or payload.dec is None:
            raise APIProblemError(
                422, "Incomplete Sky Coordinates", "Both ra and dec are required together."
            )
        fov = float(payload.fov_arcmin or 10.0)
        enforce_fov_quota(fov, user_id)
        release = payload.release or "latest"
        release_key = release
        norm_params = {
            "mode": "sky_field",
            "ra": float(payload.ra),
            "dec": float(payload.dec),
            "fov_arcmin": fov,
            "release": release,
        }
        job_params = dict(norm_params)

    analysis_id = uuid4()
    job_params["analysis_id"] = str(analysis_id)
    job, deduplicated = store.create_or_get_job(
        task=ANALYSIS_TASK_NAME,
        queue="heavy",
        params=job_params,
        params_hash=compute_params_hash(ANALYSIS_TASK_NAME, norm_params, release_key),
        user_id=user_id,
    )
    if deduplicated:
        existing = store.get_job_params(job.id) or {}
        analysis_id = UUID(
            str(
                (job.outputs or {}).get("analysis_id") or existing.get("analysis_id") or analysis_id
            )
        )
        job_status: JobStatus = job.status
    else:
        if is_eager_mode():
            with contextlib.suppress(Exception):
                execute_run_analysis(
                    job.id,
                    job_params,
                    store=store,
                    storage=storage,
                    spherex_provider=spherex,
                    gaia_provider=gaia,
                    wise_provider=wise,
                    mpc_provider=mpc,
                )
        else:
            dispatch_task(run_analysis, str(job.id), job_params, queue="heavy")
        latest = store.get_job(job.id)
        job_status = latest.status if latest else job.status

    return AnalysisAcceptedResponse(job_id=job.id, status=job_status, analysis_id=analysis_id)


@router.get(
    "/analyses/{analysis_id}/sources",
    tags=["analyses"],
    responses=_ANALYSIS_RESPONSES,
)
async def list_analysis_sources(
    analysis_id: UUID,
    store: Annotated[JobStore, Depends(get_store)],
    bbox: Annotated[
        str | None, Query(description="ICRS 'ra_min,dec_min,ra_max,dec_max' (deg)")
    ] = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> PaginatedResponse[DetectionRead]:
    """List extracted sources within optional ICRS bounding box."""
    job = require_succeeded_job(
        store.get_analysis_job(analysis_id),
        analysis_id,
        label="Analysis",
        output_key="detection_ids",
    )
    assert job.outputs is not None
    detections = store.get_detections_by_ids([int(i) for i in job.outputs["detection_ids"]])
    if bbox is not None:
        ra_min, dec_min, ra_max, dec_max = _parse_bbox(bbox)
        detections = [
            d
            for d in detections
            if dec_min <= d.dec <= dec_max
            and (
                (ra_min <= d.ra <= ra_max)
                if ra_min <= ra_max
                else (d.ra >= ra_min or d.ra <= ra_max)
            )
        ]
    page, next_cursor = paginate_items(detections, cursor, limit)
    return PaginatedResponse[DetectionRead](items=page, next_cursor=next_cursor)


@router.get(
    "/analyses/{analysis_id}/suggestions",
    tags=["analyses"],
    responses=_ANALYSIS_RESPONSES,
)
async def list_analysis_suggestions(
    analysis_id: UUID,
    store: Annotated[JobStore, Depends(get_store)],
) -> SuggestionsResponse:
    """List ranked moving/anomalous object suggestions with reasons."""
    job = require_succeeded_job(
        store.get_analysis_job(analysis_id),
        analysis_id,
        label="Analysis",
        output_key="suggestions",
    )
    assert job.outputs is not None
    items = [SuggestionItem.model_validate(s) for s in job.outputs["suggestions"]]
    return SuggestionsResponse(analysis_id=analysis_id, suggestions=items)


@router.post(
    "/projects/{project_id}/objects", status_code=status.HTTP_201_CREATED, tags=["objects"]
)
async def select_project_objects(
    project_id: UUID, payload: TrackedObjectSelectRequest
) -> TrackedObjectSelectResponse:
    """Select objects via box-drag or suggestion into persistent project labels."""
    not_implemented()


@router.post(
    "/projects/{project_id}/tracks", status_code=status.HTTP_202_ACCEPTED, tags=["objects"]
)
async def run_project_tracks(project_id: UUID, payload: TrackRunRequest) -> JobAcceptedResponse:
    """Associate detections across epochs, fit astrometric motion, and classify."""
    not_implemented()


@router.get("/objects/{object_id}", tags=["objects"])
async def get_tracked_object(object_id: UUID) -> TrackedObjectDetailResponse:
    """Retrieve full tracked object measurements, motion fit, classification, and caveats."""
    not_implemented()


@router.get("/objects/{object_id}/associations", tags=["objects"])
async def get_object_associations(object_id: UUID) -> ObjectAssociationsResponse:
    """Retrieve per-epoch detection match hypotheses and alternatives."""
    not_implemented()


@router.patch("/objects/{object_id}/associations/{epoch}", tags=["objects"])
async def override_object_association(
    object_id: UUID, epoch: str, payload: AssociationOverrideRequest
) -> AssociationOverrideResponse:
    """Accept, reject, or reassign a per-epoch detection match."""
    not_implemented()


@router.get("/objects/{object_id}/spectrum", tags=["objects"])
async def get_object_spectrum(object_id: UUID, epoch: str | None = None) -> SpectrumResponse:
    """Fetch on-the-fly forced-PSF SED across channels (um, uJy)."""
    not_implemented()


@router.get("/objects/{object_id}/lightcurve", tags=["objects"])
async def get_object_lightcurve(object_id: UUID) -> LightcurveResponse:
    """Fetch multi-epoch photometry lightcurve and variability indices."""
    not_implemented()


@router.post("/compare", status_code=status.HTTP_202_ACCEPTED, tags=["compare"])
async def create_comparison(payload: CompareRequest) -> CompareAcceptedResponse:
    """Launch an A-vs-B FITS comparison job (diff, sum, ratio, headers, sources)."""
    not_implemented()


@router.get("/compare/{compare_id}", tags=["compare"])
async def get_comparison(compare_id: UUID) -> CompareResultResponse:
    """Retrieve A-vs-B comparison products, header diff, and source changes."""
    not_implemented()
