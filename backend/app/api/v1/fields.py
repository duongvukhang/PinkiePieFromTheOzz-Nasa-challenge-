"""Sky field query, cutouts, catalog overlays, releases, and examples."""

from __future__ import annotations

import contextlib
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import (
    APIProblemError,
    enforce_fov_quota,
    get_cache,
    get_gaia_provider,
    get_optional_user_id,
    get_simbad_provider,
    get_spherex_provider,
    get_storage,
    get_store,
    get_wise_provider,
    require_succeeded_job,
)
from app.providers import Provider
from app.schemas.common import DecDeg, JobStatus, ProblemDetail, RaDeg
from app.schemas.fields import (
    CutoutAcceptedResponse,
    CutoutRequest,
    CutoutSetResponse,
    DataReleaseRead,
    ExamplesResponse,
    FieldFramesResponse,
    ResolveResponse,
)
from app.schemas.objects import CatalogOverlayResponse
from app.services.cache import CacheBackend
from app.services.fields import (
    get_catalog_overlay_service,
    list_examples_service,
    list_field_frames_service,
    list_releases_service,
    resolve_target_name,
)
from app.services.jobs import JobStore, compute_params_hash
from app.services.storage import StorageBackend
from app.workers.celery_app import dispatch_task, is_eager_mode
from app.workers.tasks_fast import build_cutouts, execute_build_cutouts

router = APIRouter(
    responses={
        400: {"model": ProblemDetail},
        403: {"model": ProblemDetail},
        404: {"model": ProblemDetail},
        422: {"model": ProblemDetail},
        429: {"model": ProblemDetail},
        503: {"model": ProblemDetail},
    }
)


@router.get("/resolve", tags=["fields"])
async def resolve_target(
    name: Annotated[str, Query(min_length=1)],
    simbad: Annotated[Provider, Depends(get_simbad_provider)],
    cache: Annotated[CacheBackend, Depends(get_cache)],
) -> ResolveResponse:
    """Resolve target name to ICRS coordinates (deg) and proper motion (mas/yr)."""
    resolved = resolve_target_name(name, simbad=simbad, cache=cache)
    if resolved is None:
        raise APIProblemError(
            status.HTTP_404_NOT_FOUND,
            "Target Not Found",
            f"Could not resolve target name: {name}",
            error_type="https://spherex.example/problems/target-not-found",
        )
    return resolved


@router.get("/fields/frames", tags=["fields"])
async def list_field_frames(
    ra: RaDeg,
    dec: DecDeg,
    provider: Annotated[Provider, Depends(get_spherex_provider)],
    cache: Annotated[CacheBackend, Depends(get_cache)],
    fov: Annotated[float, Query(gt=0.0, le=60.0)] = 10.0,
    from_utc: Annotated[str | None, Query(alias="from")] = None,
    to_utc: Annotated[str | None, Query(alias="to")] = None,
    release: str = "latest",
    bands: str = "all",
    user_id: Annotated[UUID | None, Depends(get_optional_user_id)] = None,
) -> FieldFramesResponse:
    """List available multi-epoch frames for an ICRS field (deg, FOV arcmin)."""
    enforce_fov_quota(fov, user_id)
    return list_field_frames_service(
        ra=ra,
        dec=dec,
        fov=fov,
        from_utc=from_utc,
        to_utc=to_utc,
        release=release,
        bands=bands,
        provider=provider,
        cache=cache,
    )


@router.post("/cutouts", status_code=status.HTTP_202_ACCEPTED, tags=["cutouts"])
async def create_cutouts(
    payload: CutoutRequest,
    store: Annotated[JobStore, Depends(get_store)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
    provider: Annotated[Provider, Depends(get_spherex_provider)],
    user_id: Annotated[UUID | None, Depends(get_optional_user_id)] = None,
) -> CutoutAcceptedResponse:
    """Enqueue asynchronous multi-epoch sky cutout generation."""
    enforce_fov_quota(payload.fov_arcmin, user_id)
    cutout_id = uuid4()
    norm_params = {
        "ra": payload.ra,
        "dec": payload.dec,
        "fov_arcmin": payload.fov_arcmin,
        "release": payload.release,
        "bands": sorted(payload.bands),
        "from_utc": payload.from_utc.isoformat() if payload.from_utc else None,
        "to_utc": payload.to_utc.isoformat() if payload.to_utc else None,
    }
    params_hash = compute_params_hash("build_cutouts", norm_params, payload.release)
    job_params = {**norm_params, "cutout_id": str(cutout_id)}
    job, deduplicated = store.create_or_get_job(
        task="app.workers.tasks_fast.build_cutouts",
        queue="fast",
        params=job_params,
        params_hash=params_hash,
        user_id=user_id,
    )
    resolved_cutout_id = (
        UUID(str(job.outputs["cutout_set"]["cutout_id"]))
        if deduplicated and job.outputs and "cutout_set" in job.outputs
        else cutout_id
    )
    if not deduplicated:
        if is_eager_mode():
            with contextlib.suppress(Exception):
                execute_build_cutouts(
                    job.id, job_params, store=store, storage=storage, provider=provider
                )
        else:
            dispatch_task(build_cutouts, str(job.id), job_params, queue="fast")
        latest = store.get_job(job.id)
        job_status: JobStatus = latest.status if latest else job.status
    else:
        job_status = job.status
    return CutoutAcceptedResponse(job_id=job.id, status=job_status, cutout_id=resolved_cutout_id)


@router.get("/cutouts/{cutout_id}", tags=["cutouts"])
async def get_cutouts(
    cutout_id: UUID,
    store: Annotated[JobStore, Depends(get_store)],
) -> CutoutSetResponse:
    """Fetch cutout metadata and display/science/FITS frame URLs."""
    job = require_succeeded_job(
        store.get_cutout_job(cutout_id), cutout_id, label="Cutout Set", output_key="cutout_set"
    )
    assert job.outputs is not None
    return CutoutSetResponse.model_validate(job.outputs["cutout_set"])


def _get_cutout_artifact(
    cutout_id: UUID,
    frame_id: UUID,
    kind: Literal["display", "science", "fits"],
    store: JobStore,
    storage: StorageBackend,
) -> tuple[bytes, dict[str, Any]]:
    job = store.get_cutout_job(cutout_id)
    if job is None or not job.outputs:
        raise APIProblemError(status.HTTP_404_NOT_FOUND, "Cutout Artifact Not Found")
    keys = job.outputs.get("artifacts", {}).get(str(frame_id))
    if not keys or kind not in keys:
        raise APIProblemError(status.HTTP_404_NOT_FOUND, "Cutout Frame Not Found")
    try:
        data = storage.get_bytes(keys[kind], verify_checksum=True)
    except FileNotFoundError as exc:
        raise APIProblemError(status.HTTP_404_NOT_FOUND, "Artifact Missing in Storage") from exc
    frame_meta: dict[str, Any] = next(
        (
            f
            for f in job.outputs.get("cutout_set", {}).get("frames", [])
            if f.get("frame_id") == str(frame_id)
        ),
        {},
    )
    return data, frame_meta


@router.get("/cutouts/{cutout_id}/frames/{frame_id}/display.webp", tags=["cutouts"])
async def get_cutout_display(
    cutout_id: UUID,
    frame_id: UUID,
    store: Annotated[JobStore, Depends(get_store)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
) -> Response:
    """Serve immutable 8-bit display WebP tile for a cutout frame."""
    data, _ = _get_cutout_artifact(cutout_id, frame_id, "display", store, storage)
    return Response(
        content=data,
        media_type="image/webp",
        headers={"Cache-Control": "public, max-age=2592000, immutable"},
    )


@router.get("/cutouts/{cutout_id}/frames/{frame_id}/science.bin", tags=["cutouts"])
async def get_cutout_science(
    cutout_id: UUID,
    frame_id: UUID,
    store: Annotated[JobStore, Depends(get_store)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
) -> Response:
    """Serve raw little-endian float32 science array for client-side analysis."""
    data, frame_meta = _get_cutout_artifact(cutout_id, frame_id, "science", store, storage)
    shape = ",".join(str(x) for x in frame_meta.get("stretch", {}).get("shape", []))
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "X-Dtype": "float32",
            "X-Shape": shape,
            "Cache-Control": "public, max-age=2592000, immutable",
        },
    )


@router.get("/cutouts/{cutout_id}/frames/{frame_id}/frame.fits", tags=["cutouts"])
async def get_cutout_fits(
    cutout_id: UUID,
    frame_id: UUID,
    store: Annotated[JobStore, Depends(get_store)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
) -> Response:
    """Download FITS cutout file for a frame."""
    data, _ = _get_cutout_artifact(cutout_id, frame_id, "fits", store, storage)
    return Response(
        content=data,
        media_type="application/fits",
        headers={
            "Content-Disposition": f'attachment; filename="{frame_id}.fits"',
            "Cache-Control": "public, max-age=2592000, immutable",
        },
    )


@router.get("/catalogs/overlay", tags=["catalogs"])
async def get_catalog_overlay(
    cat: Literal["gaia", "catwise"],
    ra: RaDeg,
    dec: DecDeg,
    gaia: Annotated[Provider, Depends(get_gaia_provider)],
    wise: Annotated[Provider, Depends(get_wise_provider)],
    cache: Annotated[CacheBackend, Depends(get_cache)],
    fov: Annotated[float, Query(gt=0.0, le=60.0)] = 10.0,
    epoch: str | None = None,
    user_id: Annotated[UUID | None, Depends(get_optional_user_id)] = None,
) -> CatalogOverlayResponse:
    """Fetch reference catalog overlay propagated to target epoch (ICRS deg)."""
    # PHASE2: expand cat literal to 2mass, panstarrs, simbad, sso per ROADMAP §2
    enforce_fov_quota(fov, user_id)
    provider = gaia if cat == "gaia" else wise
    return get_catalog_overlay_service(
        cat=cat, ra=ra, dec=dec, fov=fov, epoch=epoch, provider=provider, cache=cache
    )


@router.get("/releases", tags=["releases"])
async def list_releases(
    store: Annotated[JobStore, Depends(get_store)],
    provider: Annotated[Provider, Depends(get_spherex_provider)],
    cache: Annotated[CacheBackend, Depends(get_cache)],
) -> list[DataReleaseRead]:
    """List known mission data releases and indexing status."""
    return list_releases_service(store=store, provider=provider, cache=cache)


@router.get("/examples", tags=["examples"])
async def list_examples() -> ExamplesResponse:
    """List the 5 mandatory benchmark targets and latest QA summary."""
    return list_examples_service()
