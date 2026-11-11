"""Sky field query, cutouts, FITS uploads, catalog overlays, releases, and examples."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, File, Query, UploadFile, status

from app.api.deps import COMMON_501, not_implemented
from app.schemas.common import DecDeg, RaDeg
from app.schemas.fields import (
    CutoutAcceptedResponse,
    CutoutRequest,
    CutoutSetResponse,
    DataReleaseRead,
    ExamplesResponse,
    FieldFramesResponse,
    ResolveResponse,
    UploadCompatibilityReport,
    UploadResponse,
)
from app.schemas.objects import CatalogOverlayResponse

router = APIRouter(responses=COMMON_501)


@router.get("/resolve", tags=["fields"])
async def resolve_target(name: Annotated[str, Query(min_length=1)]) -> ResolveResponse:
    """Resolve target name to ICRS coordinates (deg) and proper motion (mas/yr)."""
    not_implemented()


@router.get("/fields/frames", tags=["fields"])
async def list_field_frames(
    ra: RaDeg,
    dec: DecDeg,
    fov: Annotated[float, Query(gt=0.0, le=60.0)] = 10.0,
    from_utc: Annotated[str | None, Query(alias="from")] = None,
    to_utc: Annotated[str | None, Query(alias="to")] = None,
    release: str = "latest",
    bands: str = "all",
) -> FieldFramesResponse:
    """List available multi-epoch frames for an ICRS field (deg, FOV arcmin)."""
    not_implemented()


@router.post("/cutouts", status_code=status.HTTP_202_ACCEPTED, tags=["cutouts"])
async def create_cutouts(payload: CutoutRequest) -> CutoutAcceptedResponse:
    """Enqueue asynchronous multi-epoch sky cutout generation."""
    not_implemented()


@router.get("/cutouts/{cutout_id}", tags=["cutouts"])
async def get_cutouts(cutout_id: UUID) -> CutoutSetResponse:
    """Fetch cutout metadata and display/science/FITS frame URLs."""
    not_implemented()


@router.post("/uploads", status_code=status.HTTP_202_ACCEPTED, tags=["uploads"])
async def upload_fits(file: Annotated[UploadFile, File()]) -> UploadResponse:
    """Upload a FITS image for validation and WCS/epoch inspection."""
    not_implemented()


@router.get("/uploads/{upload_id}/report", tags=["uploads"])
async def get_upload_report(upload_id: UUID) -> UploadCompatibilityReport:
    """Retrieve FITS validation and multi-epoch compatibility report."""
    not_implemented()


@router.get("/catalogs/overlay", tags=["catalogs"])
async def get_catalog_overlay(
    cat: Literal["gaia", "catwise"],
    ra: RaDeg,
    dec: DecDeg,
    fov: Annotated[float, Query(gt=0.0, le=60.0)] = 10.0,
    epoch: str | None = None,
) -> CatalogOverlayResponse:
    """Fetch reference catalog overlay propagated to target epoch (ICRS deg)."""
    # PHASE2: expand cat literal to 2mass, panstarrs, simbad, sso per ROADMAP §2
    not_implemented()


@router.get("/releases", tags=["releases"])
async def list_releases() -> list[DataReleaseRead]:
    """List known mission data releases and indexing status."""
    not_implemented()


@router.get("/examples", tags=["examples"])
async def list_examples() -> ExamplesResponse:
    """List the 5 mandatory benchmark targets and latest QA summary."""
    not_implemented()
