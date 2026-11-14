"""Schemas for multi-archive NASA data source discovery, epoch grouping, and retrieval."""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field

ArchiveSourceId = Literal["spherex_irsa", "wise_irsa", "twomass_irsa", "skyview"]


class ArchiveSearchParams(BaseModel):
    """Parameters for querying NASA-affiliated archives with spatial and temporal gating."""

    ra_deg: float = Field(..., ge=0.0, le=360.0, description="Right Ascension in degrees (ICRS)")
    dec_deg: float = Field(..., ge=-90.0, le=90.0, description="Declination in degrees (ICRS)")
    radius: float = Field(5.0, gt=0.0, le=300.0, description="Search radius value")
    radius_unit: Literal["arcsec", "arcmin", "deg"] = Field("arcmin", description="Radius unit")
    source: ArchiveSourceId = Field("spherex_irsa", description="NASA-affiliated archive provider")
    start_date: str | None = Field(None, description="Start date (YYYY-MM-DD or ISO 8601)")
    end_date: str | None = Field(None, description="End date (YYYY-MM-DD or ISO 8601)")
    band: str | None = Field(None, description="Band or detector filter (e.g. ALL, D1-D6, W1-W4, J, H, Ks)")
    max_records: int = Field(50, ge=1, le=200, description="Maximum records to return")


class EpochProduct(BaseModel):
    """Single science data product within an observation epoch."""

    obs_id: str
    filename: str
    band: str | None = None
    detector: str | None = None
    wavelength_range_um: list[float] | None = None
    access_url: str | None = None
    filesize_bytes: int | None = None
    is_loaded: bool = False
    is_cached_fallback: bool = False


class ObservationEpoch(BaseModel):
    """Grouped observation epoch representing a distinct temporal visit."""

    epoch_id: str
    epoch_index: int
    date_obs: str | None = None
    mjd_obs: float | None = None
    time_delta_days: float | None = None
    source: str = "NASA IRSA"
    pointing_ra_deg: float | None = None
    pointing_dec_deg: float | None = None
    products: list[EpochProduct] = Field(default_factory=list)
    selected_product_id: str | None = None
    is_loaded: bool = False
    is_cached_fallback: bool = False


class ArchiveRecord(BaseModel):
    """Individual archive discovery record."""

    obs_id: str
    filename: str
    ra_deg: float | None = None
    dec_deg: float | None = None
    date_obs: str | None = None
    mjd_obs: float | None = None
    band: str | None = None
    detector: str | None = None
    wavelength_center_um: float | None = None
    wavelength_range_um: list[float] | None = None
    access_url: str | None = None
    filesize_bytes: int | None = None
    data_release: str | None = None
    product_type: str = "Level 2 Science Image"
    spatial_coverage: str = ""
    retrieval_status: str = "available"
    instrument: str = "SPHEREx"
    service_source: str = "NASA/IPAC IRSA"
    observation_group_id: str | None = None
    is_cached_fallback: bool = False
    source_origin: str = "live_archive"


class ArchiveSearchResponse(BaseModel):
    """Response payload for multi-archive spatial/temporal search."""

    query_params: dict[str, Any]
    service_status: Literal["LIVE_ARCHIVE", "CACHED_FALLBACK", "SYNTHETIC_DEMO", "EMPTY", "ERROR"]
    is_offline_fallback: bool = False
    total_found: int = 0
    total_epochs: int = 0
    records: list[ArchiveRecord] = Field(default_factory=list)
    epochs: list[ObservationEpoch] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    error_detail: str | None = None


class ArchiveLoadRequest(BaseModel):
    """Request payload to download/register an authentic archive product into active session."""

    obs_id: str
    access_url: str | None = None
    source: ArchiveSourceId = "spherex_irsa"
    band: str | None = None
    ra_deg: float | None = None
    dec_deg: float | None = None
    mjd_obs: float | None = None
    fov_arcmin: float = 10.0
