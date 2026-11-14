"""Sky field query, cutouts, catalog overlays, releases, and examples."""

from __future__ import annotations

import asyncio
import contextlib
import io
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from astropy.io import fits
import numpy as np
from fastapi import APIRouter, Depends, Query, Response, status

from app.services.image_renderer import image_renderer

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
from app.schemas.archive import (
    ArchiveLoadRequest,
    ArchiveSearchParams,
    ArchiveSearchResponse,
)
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
from app.services.irsa_search import irsa_search_service
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


@router.get("/archive/search", tags=["archive"])
async def search_nasa_archive(
    ra: RaDeg,
    dec: DecDeg,
    radius: Annotated[float, Query(gt=0.0, le=300.0)] = 5.0,
    radius_unit: Annotated[Literal["arcsec", "arcmin", "deg"], Query()] = "arcmin",
    source: Annotated[Literal["spherex_irsa", "wise_irsa", "twomass_irsa", "skyview"], Query()] = "spherex_irsa",
    start_date: Annotated[str | None, Query(alias="from")] = None,
    end_date: Annotated[str | None, Query(alias="to")] = None,
    band: Annotated[str | None, Query()] = None,
    max_records: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ArchiveSearchResponse:
    """Query NASA-affiliated archives (SPHEREx, WISE, 2MASS, SkyView) with spatial and temporal intervals."""
    try:
        params = ArchiveSearchParams(
            ra_deg=ra,
            dec_deg=dec,
            radius=radius,
            radius_unit=radius_unit,
            source=source,
            start_date=start_date,
            end_date=end_date,
            band=band,
            max_records=max_records,
        )
        return await asyncio.to_thread(irsa_search_service.search_archive, params)
    except ValueError as exc:
        raise APIProblemError(status.HTTP_400_BAD_REQUEST, "Invalid Archive Request", str(exc)) from exc


@router.post("/archive/load", tags=["archive"])
async def load_archive_product(
    payload: ArchiveLoadRequest,
    storage: Annotated[StorageBackend, Depends(get_storage)],
) -> dict[str, Any]:
    """Stream download and register an authentic NASA archive FITS into the active workspace."""
    try:
        return await asyncio.to_thread(irsa_search_service.load_archive_product, payload, storage)
    except ValueError as exc:
        raise APIProblemError(status.HTTP_400_BAD_REQUEST, "Invalid Archive Request", str(exc)) from exc


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
            try:
                dispatch_task(build_cutouts, str(job.id), job_params, queue="fast")
            except Exception:
                with contextlib.suppress(Exception):
                    execute_build_cutouts(
                        job.id, job_params, store=store, storage=storage, provider=provider
                    )
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


def _render_frame_image(
    cutout_id: UUID,
    frame_id: UUID,
    store: JobStore,
    storage: StorageBackend,
    stretch: str = "asinh",
    colormap: str = "inferno",
    brightness: float = 1.0,
    contrast: float = 1.0,
    p_min: float = 0.5,
    p_max: float = 99.5,
) -> bytes:
    """Render scientific frame data array into PNG bytes using astronomical stretch/colormap."""
    try:
        data, frame_meta = _get_cutout_artifact(cutout_id, frame_id, "science", store, storage)
        shape = tuple(frame_meta.get("stretch", {}).get("shape", []))
        if shape and len(shape) == 2:
            arr = np.frombuffer(data, dtype=np.float32).reshape(shape)
        else:
            fits_bytes, _ = _get_cutout_artifact(cutout_id, frame_id, "fits", store, storage)
            with fits.open(io.BytesIO(fits_bytes)) as hdul:
                arr = hdul["IMAGE"].data
    except Exception:
        fits_bytes, _ = _get_cutout_artifact(cutout_id, frame_id, "fits", store, storage)
        with fits.open(io.BytesIO(fits_bytes)) as hdul:
            arr = hdul["IMAGE"].data

    return image_renderer.render_to_png_bytes(
        arr,
        stretch=stretch,
        colormap=colormap,
        brightness=brightness,
        contrast=contrast,
        p_min=p_min,
        p_max=p_max,
    )


@router.get("/cutouts/{cutout_id}/frames/{frame_id}/preview.png", tags=["cutouts"])
async def get_cutout_preview_png(
    cutout_id: UUID,
    frame_id: UUID,
    store: Annotated[JobStore, Depends(get_store)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
    stretch: Annotated[
        Literal["asinh", "log", "sqrt", "linear", "zscale"], Query()
    ] = "asinh",
    colormap: Annotated[
        Literal["inferno", "viridis", "plasma", "magma", "cividis", "hot", "cool", "bone", "gray"],
        Query(),
    ] = "inferno",
    brightness: Annotated[float, Query(ge=0.1, le=5.0)] = 1.0,
    contrast: Annotated[float, Query(ge=0.1, le=5.0)] = 1.0,
    p_min: Annotated[float, Query(ge=0.0, le=50.0)] = 0.5,
    p_max: Annotated[float, Query(ge=50.0, le=100.0)] = 99.5,
) -> Response:
    """Serve dynamic PNG preview with custom astronomical stretch and colormap."""
    png_bytes = _render_frame_image(
        cutout_id,
        frame_id,
        store,
        storage,
        stretch=stretch,
        colormap=colormap,
        brightness=brightness,
        contrast=contrast,
        p_min=p_min,
        p_max=p_max,
    )
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/cutouts/{cutout_id}/frames/{frame_id}/display.webp", tags=["cutouts"])
async def get_cutout_display(
    cutout_id: UUID,
    frame_id: UUID,
    store: Annotated[JobStore, Depends(get_store)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
    stretch: Annotated[
        Literal["asinh", "log", "sqrt", "linear", "zscale"] | None, Query()
    ] = None,
    colormap: Annotated[
        Literal["inferno", "viridis", "plasma", "magma", "cividis", "hot", "cool", "bone", "gray"]
        | None,
        Query(),
    ] = None,
    brightness: Annotated[float | None, Query(ge=0.1, le=5.0)] = None,
    contrast: Annotated[float | None, Query(ge=0.1, le=5.0)] = None,
    p_min: Annotated[float | None, Query(ge=0.0, le=50.0)] = None,
    p_max: Annotated[float | None, Query(ge=50.0, le=100.0)] = None,
) -> Response:
    """Serve 8-bit display WebP tile for a cutout frame with optional stretch/colormap overrides."""
    if (
        stretch is not None
        or colormap is not None
        or brightness is not None
        or contrast is not None
        or p_min is not None
        or p_max is not None
    ):
        png_bytes = _render_frame_image(
            cutout_id,
            frame_id,
            store,
            storage,
            stretch=stretch or "asinh",
            colormap=colormap or "inferno",
            brightness=brightness if brightness is not None else 1.0,
            contrast=contrast if contrast is not None else 1.0,
            p_min=p_min if p_min is not None else 0.5,
            p_max=p_max if p_max is not None else 99.5,
        )
        # Convert PNG to WebP
        from PIL import Image

        buf = io.BytesIO()
        Image.open(io.BytesIO(png_bytes)).save(buf, format="WEBP", quality=90)
        return Response(
            content=buf.getvalue(),
            media_type="image/webp",
            headers={"Cache-Control": "public, max-age=86400"},
        )

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
