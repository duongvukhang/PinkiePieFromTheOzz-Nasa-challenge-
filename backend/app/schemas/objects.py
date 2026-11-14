"""Analysis, detection, tracked object, association, spectrum, catalog, and compare schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import DecDeg, FovArcmin, JobAcceptedResponse, RaDeg


class DetectionRead(BaseModel):
    """Extracted source detection (ICRS deg, uncertainties mas, flux uJy, AB mag, arcsec)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    fits_file_id: UUID
    observation_id: UUID | None = None
    ra: float
    dec: float
    x_pix: float | None = None
    y_pix: float | None = None
    sig_ra_mas: float | None = None
    sig_dec_mas: float | None = None
    corr_radec: float | None = None
    flux_ujy: float | None = None
    flux_err_ujy: float | None = None
    mag_ab: float | None = None
    snr: float | None = None
    fwhm_arcsec: float | None = None
    flags: int = 0
    real_bogus: float | None = None
    is_difference: bool = False


class AnalysisCreateRequest(BaseModel):
    """Trigger registration, source extraction, and candidate suggestion on frames."""

    fits_file_ids: list[UUID] | None = None
    cutout_id: UUID | None = None
    ra: RaDeg | None = None
    dec: DecDeg | None = None
    fov_arcmin: FovArcmin | None = None
    release: str | None = None


class AnalysisAcceptedResponse(JobAcceptedResponse):
    """202 response for asynchronous frame analysis."""

    analysis_id: UUID


class SuggestionItem(BaseModel):
    """Program-suggested moving or anomalous object (ICRS deg)."""

    detection_id: int
    ra: float
    dec: float
    score: float = Field(ge=0.0, le=1.0)
    reasons: list[str]
    summary: str | None = None


class SuggestionsResponse(BaseModel):
    """Ranked object suggestions for an analysis run."""

    analysis_id: UUID
    suggestions: list[SuggestionItem]


class SkyBox(BaseModel):
    """Rectangular sky selection box (ICRS deg)."""

    ra_min: RaDeg
    ra_max: RaDeg
    dec_min: DecDeg
    dec_max: DecDeg


class TrackedObjectSelectRequest(BaseModel):
    """Select objects by box-drag, suggestion, catalog, or manual coordinates."""

    analysis_id: UUID
    mode: Literal["box", "suggestion", "catalog", "manual"]
    epoch: str | None = None
    box: SkyBox | None = None
    detection_ids: list[int] | None = None


class TrackedObjectSummary(BaseModel):
    """Selected tracked object with persistent label (ICRS deg, flux uJy)."""

    id: UUID
    label: str
    ra: float | None = None
    dec: float | None = None
    crossmatch: str | None = None
    flux_ujy: dict[str, float] = Field(default_factory=dict)
    selection_mode: str | None = None


class TrackedObjectSelectResponse(BaseModel):
    """Created/selected tracked objects response."""

    objects: list[TrackedObjectSummary]


class TrackRunRequest(BaseModel):
    """Trigger cross-epoch association, motion fitting, and classification."""

    object_ids: list[UUID] | None = None
    models: list[str] = Field(default_factory=lambda: ["linear", "astrometric5"])
    run_classification: bool = True


class EpochMeasurementRead(BaseModel):
    """Single-epoch measurement (MJD TDB, ICRS deg, mas, uJy)."""

    model_config = ConfigDict(from_attributes=True)

    epoch: str | None = None
    source: str
    mjd_tdb: float
    ra: float | None = None
    dec: float | None = None
    sig_ra_mas: float | None = None
    sig_dec_mas: float | None = None
    corr_radec: float | None = None
    band_group: str | None = None
    flux_ujy: float | None = None
    flux_err_ujy: float | None = None
    is_upper_limit: bool = False
    flags: int = 0
    association_confidence: float | None = None


class MotionFitRead(BaseModel):
    """Fitted proper-motion/parallax/orbit solution (MJD TDB, mas/yr, mas, deg, yr)."""

    model_config = ConfigDict(from_attributes=True)

    model: str
    ref_epoch_mjd_tdb: float
    params: dict[str, Any]
    pm_total_masyr: float | None = None
    pm_total_err: float | None = None
    pa_deg: float | None = None
    pa_err: float | None = None
    plx_mas: float | None = None
    plx_err: float | None = None
    chi2_red: float | None = None
    dof: int | None = None
    baseline_yr: float | None = None
    is_preferred: bool = False


class ClassificationSummary(BaseModel):
    """Probabilistic object class prediction and consensus verdict."""

    top_class: str
    top_prob: float = Field(ge=0.0, le=1.0)
    probabilities: dict[str, float]
    evidence: list[str] | dict[str, Any]
    consensus: dict[str, Any] | None = None


class TrackedObjectDetailResponse(BaseModel):
    """Full tracked object dossier: measurements, fit, classification, caveats."""

    id: UUID
    label: str
    public_id: str | None = None
    status: str
    identity_confidence: float | None = None
    measurements: list[EpochMeasurementRead]
    motion_fit: MotionFitRead | None = None
    classification: ClassificationSummary | None = None
    caveats: list[str] = Field(default_factory=list)


class AssociationHypothesis(BaseModel):
    """Candidate or chosen per-epoch detection match (ICRS deg)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    observation_id: UUID
    detection_id: int | None = None
    pred_ra: float | None = None
    pred_dec: float | None = None
    cost: float | None = None
    confidence: float
    rank: int
    is_chosen: bool
    decided_by: Literal["auto", "user"]
    reason: str | None = None
    flags: int = 0


class ObjectAssociationsResponse(BaseModel):
    """Per-epoch association hypotheses for one tracked object."""

    object_id: UUID
    associations: list[AssociationHypothesis]


class AssociationOverrideRequest(BaseModel):
    """Manual user override for a tracked object's epoch association."""

    action: Literal["accept", "reject", "reassign"]
    detection_id: int | None = None
    reason: str = Field(min_length=1, max_length=500)


class AssociationOverrideResponse(BaseModel):
    """Confirmation of manual override and triggered refit job."""

    refit_job_id: UUID
    decided_by: Literal["user"] = "user"


class SpectrumResponse(BaseModel):
    """On-the-fly spectral energy distribution (wavelength um, flux uJy, MJD TDB)."""

    object_id: UUID
    observation_id: UUID | None = None
    wavelength_um: list[float]
    flux_ujy: list[float]
    flux_err_ujy: list[float]
    mjd_tdb: list[float]
    flags: list[int]
    method: str | None = None


class LightcurvePoint(BaseModel):
    """Single photometric epoch sample (MJD TDB, flux uJy)."""

    mjd_tdb: float
    utc: datetime | None = None
    band_group: str
    flux_ujy: float | None = None
    flux_err_ujy: float | None = None
    is_upper_limit: bool = False
    flags: int = 0


class LightcurveResponse(BaseModel):
    """Multi-epoch lightcurve and variability statistics."""

    object_id: UUID
    points: list[LightcurvePoint]
    variability_indices: dict[str, float] = Field(default_factory=dict)


class CatalogOverlaySource(BaseModel):
    """External reference catalog source propagated to target epoch (ICRS deg, mas/yr)."""

    catalog: str
    catalog_id: str
    ra: float
    dec: float
    epoch_mjd_tdb: float
    pmra_masyr: float | None = None
    pmdec_masyr: float | None = None
    mag: float | None = None
    mag_band: str | None = None


class CatalogOverlayResponse(BaseModel):
    """Reference catalog overlay response (MJD TDB)."""

    catalog: str
    epoch_mjd_tdb: float
    sources: list[CatalogOverlaySource]
    warnings: list[str] = Field(default_factory=list)


class CompareRequest(BaseModel):
    """A-vs-B FITS comparison job request."""

    a: UUID
    b: UUID
    ops: list[Literal["diff", "sum", "ratio", "header_diff", "sources"]] = Field(min_length=1)
    psf_match: Literal["none", "zogy", "kernel"] = "none"


class CompareAcceptedResponse(JobAcceptedResponse):
    """202 response for asynchronous A-vs-B comparison."""

    compare_id: UUID


class CompareResultResponse(BaseModel):
    """A-vs-B comparison products, header diff, and source change lists."""

    compare_id: UUID
    status: str
    products: dict[str, str] = Field(default_factory=dict)
    header_diff: dict[str, Any] | None = None
    source_changes: dict[str, Any] | None = None


class EphemerisPredictionPointSchema(BaseModel):
    """Predicted future/past ephemeris position with full covariance uncertainty ellipse."""

    time_mjd: float
    date_iso: str
    ra_deg: float
    dec_deg: float
    uncertainty_semi_major_arcsec: float
    uncertainty_semi_minor_arcsec: float
    pa_deg: float = 0.0
    is_extrapolated: bool = False
    method: str = "astrometric5_wls"


class EphemerisRequest(BaseModel):
    """Request future sky position ephemeris prediction for a tracked object."""

    extrapolation_days: float = Field(default=30.0, ge=0.1, le=3650.0)
    steps: int = Field(default=10, ge=2, le=100)
    start_mjd: float | None = None


class EphemerisResponse(BaseModel):
    """Ephemeris trajectory prediction response."""

    object_id: UUID
    trajectory: list[EphemerisPredictionPointSchema]
    preferred_model: str | None = None
    baseline_days: float = 0.0


class TrajectoryRecomputeRequest(BaseModel):
    """Recompute trajectory motion fits with optional excluded detections or models."""

    excluded_detection_ids: list[int] = Field(default_factory=list)
    models: list[str] = Field(default_factory=lambda: ["linear", "astrometric5"])


class TrajectoryRecomputeResponse(BaseModel):
    """Response after recomputing trajectory motion fits."""

    object_id: UUID
    refit_job_id: UUID
    status: str = "recomputed"

