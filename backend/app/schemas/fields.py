"""Field query, cutout, FITS upload, release, example, and embed schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import DecDeg, FovArcmin, JobAcceptedResponse, RaDeg


class ResolveResponse(BaseModel):
    """Resolved astronomical name (ICRS deg, optional PM in mas/yr)."""

    query: str
    ra_deg: RaDeg
    dec_deg: DecDeg
    pmra_masyr: float | None = None
    pmdec_masyr: float | None = None
    resolver: str


class FieldSummary(BaseModel):
    """Queried sky field summary (ICRS deg, FOV arcmin, nested HEALPix order 9)."""

    ra: RaDeg
    dec: DecDeg
    fov_arcmin: float = Field(gt=0.0)
    healpix9: int = Field(ge=0)


class EpochFrameGroup(BaseModel):
    """Available epoch frame summary (MJD TDB, UTC, wavelength range um)."""

    epoch: str
    mjd_tdb_mean: float
    utc_mean: datetime
    n_frames: int = Field(ge=0)
    arrays: list[int]
    lambda_um: tuple[float, float]
    flagged_fraction: float = Field(ge=0.0, le=1.0)


class FieldFramesResponse(BaseModel):
    """Multi-epoch frame availability for a sky coordinate query."""

    field: FieldSummary
    release: str
    epochs: list[EpochFrameGroup]
    warnings: list[str] = Field(default_factory=list)


class CutoutRequest(BaseModel):
    """Request to build multi-epoch sky cutouts (ICRS deg, FOV arcmin)."""

    ra: RaDeg
    dec: DecDeg
    fov_arcmin: FovArcmin = 10.0
    release: str = "latest"
    bands: list[str] = Field(default_factory=lambda: ["all"])
    from_utc: datetime | None = None
    to_utc: datetime | None = None
    source: str = "spherex_irsa"


class CutoutAcceptedResponse(JobAcceptedResponse):
    """202 response for asynchronous cutout generation."""

    cutout_id: UUID


class CutoutFrame(BaseModel):
    """Single rendered/science cutout frame (MJD TDB, UTC, URLs)."""

    frame_id: UUID
    epoch: str
    mjd_tdb_mid: float
    utc_mid: datetime
    array_id: int | None = None
    band_label: str | None = None
    display_url: str
    science_url: str
    fits_url: str
    wcs_header: str
    stretch: dict[str, Any] = Field(default_factory=dict)
    flagged_fraction: float = Field(default=0.0, ge=0.0, le=1.0)


class CutoutSetResponse(BaseModel):
    """Resolved cutout bundle with per-frame URLs and stretch metadata."""

    cutout_id: UUID
    field: FieldSummary
    release: str
    frames: list[CutoutFrame]
    warnings: list[str] = Field(default_factory=list)


class UploadResponse(JobAcceptedResponse):
    """Uploaded FITS registration/validation job response."""

    upload_id: UUID
    fits_file_id: UUID


class UploadedFitsSummary(BaseModel):
    """Registered user upload or archive FITS frame summary."""

    fits_file_id: UUID
    filename: str
    obs_id: str | None = None
    origin: str = "upload"
    epoch: str
    mjd_tdb_mid: float
    utc_mid: str
    ra_center: float | None = None
    dec_center: float | None = None
    naxis1: int | None = None
    naxis2: int | None = None
    wcs_header: str = ""
    display_url: str
    science_url: str
    fits_url: str
    band_label: str | None = None
    stretch: dict[str, Any] = Field(default_factory=dict)


class UploadCompatibilityReport(BaseModel):
    """FITS validation report (overlap %, arcsec/px, deg, MJD TDB)."""

    upload_id: UUID
    fits_file_id: UUID
    valid: bool
    compatibility_status: Literal["compatible", "partial", "incompatible"] = "compatible"
    overlap_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    pixscale_arcsec: float | None = None
    rotation_deg: float | None = None
    ra_center_deg: float | None = None
    dec_center_deg: float | None = None
    band_label: str | None = None
    mjd_tdb_mid: float | None = None
    reprojection_needed: bool
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    accuracy_notes: list[str] = Field(default_factory=list)


class DataReleaseRead(BaseModel):
    """Indexed archive data release descriptor."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    provider: str
    mission: str
    version_label: str | None = None
    release_date: date | None = None
    status: str
    profile_name: str | None = None
    n_frames: int | None = None


class ExampleTargetSummary(BaseModel):
    """Benchmark target preset and latest QA status (ICRS deg, FOV arcmin)."""

    id: str
    name: str
    category: str
    ra_deg: float
    dec_deg: float
    fov_arcmin: float
    passed: bool | None = None
    summary: str | None = None


class ExamplesResponse(BaseModel):
    """Mandatory benchmark presets response."""

    targets: list[ExampleTargetSummary]


class OEmbedResponse(BaseModel):
    """oEmbed rich iframe descriptor."""

    type: Literal["rich"] = "rich"
    version: Literal["1.0"] = "1.0"
    html: str
    width: int = 640
    height: int = 480
    provider_name: str = "SPHEREx Moving-Object Platform"
