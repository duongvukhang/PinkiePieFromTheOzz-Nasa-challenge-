"""SQLAlchemy ORM models for MVP schema (ICRS deg, MJD TDB, uncertainties in mas)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, CITEXT, INET, JSONB, REAL, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

_UUID = UUID(as_uuid=True)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(_UUID, primary_key=True, server_default=text("gen_random_uuid()"))


def _id_pk() -> Mapped[int]:
    return mapped_column(BigInteger, Identity(always=True), primary_key=True)


def _created_at() -> Mapped[datetime | None]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _updated_at() -> Mapped[datetime | None]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class User(Base):
    """Registered user account (MVP email/password auth)."""

    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('user','scientist','admin')", name="role"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text)  # MVP auth; nullable for Phase 2 ORCID
    display_name: Mapped[str | None] = mapped_column(Text)
    orcid: Mapped[str | None] = mapped_column(Text, unique=True)  # PHASE2: ORCID OAuth
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'user'"))
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiToken(Base):
    """Hashed programmatic Bearer token for API access."""

    __tablename__ = "api_tokens"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[bytes] = mapped_column(BYTEA, unique=True, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = _created_at()
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Project(Base):
    """User workspace storing sky field (ICRS deg, FOV arcmin), files, and tracked objects."""

    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("visibility IN ('private','link','public')", name="visibility"),
        Index("projects_owner_idx", "owner_id"),
        # PHASE2: replace with q3c_ang2ipix(center_ra, center_dec)
        Index("projects_ra_dec_idx", "center_ra", "center_dec"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(_UUID, ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    center_ra: Mapped[float | None] = mapped_column(Double)
    center_dec: Mapped[float | None] = mapped_column(Double)
    fov_arcmin: Mapped[float | None] = mapped_column(REAL)
    visibility: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'private'"))
    share_token_hash: Mapped[bytes | None] = mapped_column(BYTEA)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime | None] = _created_at()
    updated_at: Mapped[datetime | None] = _updated_at()


class ProjectMember(Base):
    """Project role assignment (owner/editor/viewer)."""

    __tablename__ = "project_members"
    __table_args__ = (CheckConstraint("role IN ('owner','editor','viewer')", name="role"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)


class ProjectVersion(Base):
    """Immutable snapshot of project objects, files, views, and params."""

    __tablename__ = "project_versions"

    project_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(_UUID, ForeignKey("users.id"))
    created_at: Mapped[datetime | None] = _created_at()


class Note(Base):
    """Markdown workspace note attached to a project."""

    __tablename__ = "notes"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(_UUID, ForeignKey("users.id"))
    body_md: Mapped[str] = mapped_column(Text, nullable=False)
    attachments: Mapped[list[Any] | None] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime | None] = _created_at()
    updated_at: Mapped[datetime | None] = _updated_at()


class SavedView(Base):
    """Bookmarked viewer URL state and rendering parameters."""

    __tablename__ = "saved_views"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("projects.id", ondelete="CASCADE")
    )
    name: Mapped[str | None] = mapped_column(Text)
    url_state: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime | None] = _created_at()


class DataRelease(Base):
    """External or internal mission data release metadata."""

    __tablename__ = "data_releases"
    __table_args__ = (
        CheckConstraint(
            "status IN ('discovered','indexing','indexed','active','quarantined','superseded')",
            name="status",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    mission: Mapped[str] = mapped_column(Text, nullable=False)
    version_label: Mapped[str | None] = mapped_column(Text)
    release_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    profile_name: Mapped[str | None] = mapped_column(Text)
    supersedes: Mapped[str | None] = mapped_column(Text, ForeignKey("data_releases.id"))
    n_frames: Mapped[int | None] = mapped_column(BigInteger)
    notes: Mapped[str | None] = mapped_column(Text)
    discovered_at: Mapped[datetime | None] = _created_at()


class SkyField(Base):
    """Sky region of interest (ICRS deg center, radius deg, nested HEALPix order-9)."""

    __tablename__ = "sky_fields"
    __table_args__ = (
        # PHASE2: replace with q3c_ang2ipix(center_ra, center_dec)
        Index("sky_fields_ra_dec_idx", "center_ra", "center_dec"),
        Index("sky_fields_hpx_idx", "healpix9"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    center_ra: Mapped[float] = mapped_column(Double, nullable=False)
    center_dec: Mapped[float] = mapped_column(Double, nullable=False)
    radius_deg: Mapped[float] = mapped_column(REAL, nullable=False)
    footprint: Mapped[Any | None] = mapped_column(JSONB)
    healpix9: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime | None] = _created_at()


class Observation(Base):
    """Single epoch grouping frames within a visit window (MJD TDB, barycentric ICRS au)."""

    __tablename__ = "observations"
    __table_args__ = (Index("observations_field_idx", "sky_field_id", "mjd_tdb_mean"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    sky_field_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("sky_fields.id", ondelete="CASCADE"), nullable=False
    )
    release_id: Mapped[str | None] = mapped_column(Text, ForeignKey("data_releases.id"))
    epoch_label: Mapped[str | None] = mapped_column(Text)
    mjd_tdb_start: Mapped[float | None] = mapped_column(Double)
    mjd_tdb_end: Mapped[float | None] = mapped_column(Double)
    mjd_tdb_mean: Mapped[float] = mapped_column(Double, nullable=False)
    obs_bary_xyz_au: Mapped[list[float] | None] = mapped_column(ARRAY(Double))
    n_frames: Mapped[int | None] = mapped_column(Integer)
    coadd_ref: Mapped[str | None] = mapped_column(Text)
    quality: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class FitsFile(Base):
    """Archive cutout, user upload, or derived FITS product (deg ICRS, um, s, mas, MJD TDB)."""

    __tablename__ = "fits_files"
    __table_args__ = (
        CheckConstraint("origin IN ('archive','upload','derived')", name="origin"),
        UniqueConstraint("provider", "obs_id", "sha256", name="uq_fits_files_provider_obs_sha256"),
        # PHASE2: replace with q3c_ang2ipix(ra_center, dec_center)
        Index("fits_ra_dec_idx", "ra_center", "dec_center"),
        Index("fits_time", "mjd_tdb_mid"),
        Index("fits_hpx", "healpix9"),
        Index("fits_array_band", "array_id", "wavelength_center_um"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    release_id: Mapped[str | None] = mapped_column(Text, ForeignKey("data_releases.id"))
    provider: Mapped[str | None] = mapped_column(Text)
    obs_id: Mapped[str | None] = mapped_column(Text)
    uploader_id: Mapped[uuid.UUID | None] = mapped_column(_UUID, ForeignKey("users.id"))
    observation_id: Mapped[uuid.UUID | None] = mapped_column(_UUID, ForeignKey("observations.id"))
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mission: Mapped[str | None] = mapped_column(Text)
    instrument: Mapped[str | None] = mapped_column(Text)
    array_id: Mapped[int | None] = mapped_column(SmallInteger)
    band_label: Mapped[str | None] = mapped_column(Text)
    wavelength_min_um: Mapped[float | None] = mapped_column(REAL)
    wavelength_max_um: Mapped[float | None] = mapped_column(REAL)
    wavelength_center_um: Mapped[float | None] = mapped_column(REAL)
    date_obs_utc: Mapped[str | None] = mapped_column(Text)
    mjd_tdb_mid: Mapped[float | None] = mapped_column(Double)
    exptime_s: Mapped[float | None] = mapped_column(REAL)
    ra_center: Mapped[float | None] = mapped_column(Double)
    dec_center: Mapped[float | None] = mapped_column(Double)
    naxis1: Mapped[int | None] = mapped_column(Integer)
    naxis2: Mapped[int | None] = mapped_column(Integer)
    pixscale_arcsec: Mapped[float | None] = mapped_column(REAL)
    rotation_deg: Mapped[float | None] = mapped_column(REAL)
    wcs_header: Mapped[str] = mapped_column(Text, nullable=False)
    wcs_refined: Mapped[str | None] = mapped_column(Text)
    astrometric_rms_mas: Mapped[float | None] = mapped_column(REAL)
    n_astrom_ref: Mapped[int | None] = mapped_column(Integer)
    footprint: Mapped[Any | None] = mapped_column(JSONB)
    healpix9: Mapped[int | None] = mapped_column(BigInteger)
    header_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    flags_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime | None] = _created_at()


class ProjectFile(Base):
    """Link table associating FITS files with a project."""

    __tablename__ = "project_files"

    project_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    fits_file_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("fits_files.id"), primary_key=True
    )
    role: Mapped[str | None] = mapped_column(Text, server_default=text("'input'"))
    added_at: Mapped[datetime | None] = _created_at()


class Detection(Base):
    """Extracted source detection (ICRS deg, sig mas, flux uJy, AB mag, arcsec, deg)."""

    __tablename__ = "detections"
    __table_args__ = (
        # PHASE2: add healpix4 list partitioning and q3c_ang2ipix(ra, dec) index
        Index("detections_ra_dec_idx", "ra", "dec"),
        Index("detections_hpx_idx", "healpix9"),
        Index("detections_file", "fits_file_id"),
    )

    id: Mapped[int] = _id_pk()
    healpix9: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fits_file_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("fits_files.id", ondelete="CASCADE"), nullable=False
    )
    observation_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("observations.id", ondelete="SET NULL")
    )
    ra: Mapped[float] = mapped_column(Double, nullable=False)
    dec: Mapped[float] = mapped_column(Double, nullable=False)
    x_pix: Mapped[float | None] = mapped_column(REAL)
    y_pix: Mapped[float | None] = mapped_column(REAL)
    sig_ra_mas: Mapped[float | None] = mapped_column(REAL)
    sig_dec_mas: Mapped[float | None] = mapped_column(REAL)
    corr_radec: Mapped[float | None] = mapped_column(REAL)
    flux_ujy: Mapped[float | None] = mapped_column(REAL)
    flux_err_ujy: Mapped[float | None] = mapped_column(REAL)
    mag_ab: Mapped[float | None] = mapped_column(REAL)
    snr: Mapped[float | None] = mapped_column(REAL)
    fwhm_arcsec: Mapped[float | None] = mapped_column(REAL)
    elongation: Mapped[float | None] = mapped_column(REAL)
    theta_deg: Mapped[float | None] = mapped_column(REAL)
    psf_chi2: Mapped[float | None] = mapped_column(REAL)
    sharpness: Mapped[float | None] = mapped_column(REAL)
    roundness: Mapped[float | None] = mapped_column(REAL)
    flags: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    real_bogus: Mapped[float | None] = mapped_column(REAL)
    is_difference: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    diff_sign: Mapped[int | None] = mapped_column(SmallInteger)
    created_at: Mapped[datetime | None] = _created_at()


class TrackedObject(Base):
    """Persistent user-selected or suggested object tracked across epochs (ICRS deg, MJD TDB)."""

    __tablename__ = "tracked_objects"
    __table_args__ = (
        CheckConstraint(
            "selection_mode IN ('box','suggestion','catalog','manual','planetx')",
            name="selection_mode",
        ),
        CheckConstraint("status IN ('active','lost','merged','split','rejected')", name="status"),
        UniqueConstraint("project_id", "label", name="uq_tracked_objects_project_label"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("projects.id", ondelete="CASCADE")
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    public_id: Mapped[str | None] = mapped_column(Text, unique=True)
    selection_mode: Mapped[str | None] = mapped_column(Text)
    ref_ra: Mapped[float | None] = mapped_column(Double)
    ref_dec: Mapped[float | None] = mapped_column(Double)
    ref_mjd_tdb: Mapped[float | None] = mapped_column(Double)
    status: Mapped[str | None] = mapped_column(Text, server_default=text("'active'"))
    merged_into: Mapped[uuid.UUID | None] = mapped_column(_UUID, ForeignKey("tracked_objects.id"))
    identity_confidence: Mapped[float | None] = mapped_column(REAL)
    created_at: Mapped[datetime | None] = _created_at()


class Finding(Base):
    """Scientific finding report linked to a project and optional tracked object."""

    __tablename__ = "findings"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','candidate','submitted','known','rejected')",
            name="status",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    tracked_object_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("tracked_objects.id")
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary_md: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    external_ref: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = _created_at()


class ObjectAlias(Base):
    """External catalog identifier alias for a tracked object."""

    __tablename__ = "object_aliases"

    tracked_object_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("tracked_objects.id", ondelete="CASCADE"), primary_key=True
    )
    catalog: Mapped[str] = mapped_column(Text, primary_key=True)
    catalog_id: Mapped[str] = mapped_column(Text, primary_key=True)


class Association(Base):
    """Per-(object, epoch) detection match hypothesis (pred ICRS deg, cov mas^2)."""

    __tablename__ = "associations"
    __table_args__ = (
        CheckConstraint("decided_by IN ('auto','user')", name="decided_by"),
        Index(
            "assoc_obj_epoch",
            "tracked_object_id",
            "observation_id",
            postgresql_where=text("is_chosen"),
        ),
    )

    id: Mapped[int] = _id_pk()
    tracked_object_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("tracked_objects.id", ondelete="CASCADE"), nullable=False
    )
    observation_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("observations.id"), nullable=False
    )
    # PHASE2: composite FK (detection_healpix4, detection_id) when detections is partitioned
    detection_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("detections.id", ondelete="SET NULL")
    )
    pred_ra: Mapped[float | None] = mapped_column(Double)
    pred_dec: Mapped[float | None] = mapped_column(Double)
    pred_cov_mas2: Mapped[list[float] | None] = mapped_column(ARRAY(REAL))
    cost: Mapped[float | None] = mapped_column(REAL)
    mahalanobis_d2: Mapped[float | None] = mapped_column(REAL)
    flux_term: Mapped[float | None] = mapped_column(REAL)
    color_term: Mapped[float | None] = mapped_column(REAL)
    shape_term: Mapped[float | None] = mapped_column(REAL)
    catalog_term: Mapped[float | None] = mapped_column(REAL)
    confidence: Mapped[float] = mapped_column(REAL, nullable=False)
    rank: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_chosen: Mapped[bool] = mapped_column(Boolean, nullable=False)
    decided_by: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(_UUID, ForeignKey("users.id"))
    reason: Mapped[str | None] = mapped_column(Text)
    flags: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[datetime | None] = _created_at()


class EpochMeasurement(Base):
    """Per-epoch astrometric and photometric measurement (MJD TDB, deg, mas, uJy)."""

    __tablename__ = "epoch_measurements"
    __table_args__ = (Index("em_obj_time", "tracked_object_id", "mjd_tdb"),)

    id: Mapped[int] = _id_pk()
    tracked_object_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("tracked_objects.id", ondelete="CASCADE"), nullable=False
    )
    observation_id: Mapped[uuid.UUID | None] = mapped_column(_UUID, ForeignKey("observations.id"))
    source: Mapped[str] = mapped_column(Text, nullable=False)
    mjd_tdb: Mapped[float] = mapped_column(Double, nullable=False)
    ra: Mapped[float | None] = mapped_column(Double)
    dec: Mapped[float | None] = mapped_column(Double)
    sig_ra_mas: Mapped[float | None] = mapped_column(REAL)
    sig_dec_mas: Mapped[float | None] = mapped_column(REAL)
    corr_radec: Mapped[float | None] = mapped_column(REAL)
    band_group: Mapped[str | None] = mapped_column(Text)
    flux_ujy: Mapped[float | None] = mapped_column(REAL)
    flux_err_ujy: Mapped[float | None] = mapped_column(REAL)
    is_upper_limit: Mapped[bool | None] = mapped_column(Boolean, server_default=text("false"))
    flags: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    used_in_fit: Mapped[bool | None] = mapped_column(Boolean, server_default=text("true"))
    outlier_reason: Mapped[str | None] = mapped_column(Text)


class MotionFit(Base):
    """Fitted kinematic/astrometric solution (mas/yr, mas, deg, arcsec/hr, pc, km/s, yr)."""

    __tablename__ = "motion_fits"
    __table_args__ = (
        CheckConstraint(
            "model IN ('static','linear','astrometric5','astrometric5_prior',"
            "'keplerian','shift_stack')",
            name="model",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tracked_object_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("tracked_objects.id", ondelete="CASCADE"), nullable=False
    )
    model: Mapped[str] = mapped_column(Text, nullable=False)
    ref_epoch_mjd_tdb: Mapped[float] = mapped_column(Double, nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    covariance: Mapped[list[float]] = mapped_column(ARRAY(Double), nullable=False)
    pm_total_masyr: Mapped[float | None] = mapped_column(REAL)
    pm_total_err: Mapped[float | None] = mapped_column(REAL)
    pa_deg: Mapped[float | None] = mapped_column(REAL)
    pa_err: Mapped[float | None] = mapped_column(REAL)
    rate_arcsec_hr: Mapped[float | None] = mapped_column(REAL)
    plx_mas: Mapped[float | None] = mapped_column(REAL)
    plx_err: Mapped[float | None] = mapped_column(REAL)
    dist_pc: Mapped[float | None] = mapped_column(REAL)
    vtan_kms: Mapped[float | None] = mapped_column(REAL)
    chi2: Mapped[float | None] = mapped_column(REAL)
    dof: Mapped[int | None] = mapped_column(Integer)
    chi2_red: Mapped[float | None] = mapped_column(REAL)
    bic: Mapped[float | None] = mapped_column(REAL)
    residuals: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    method: Mapped[str | None] = mapped_column(Text)
    n_epochs: Mapped[int | None] = mapped_column(Integer)
    baseline_yr: Mapped[float | None] = mapped_column(REAL)
    is_preferred: Mapped[bool | None] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime | None] = _created_at()


class Classification(Base):
    """Probabilistic object classification and evidence summary."""

    __tablename__ = "classifications"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tracked_object_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("tracked_objects.id", ondelete="CASCADE"), nullable=False
    )
    method: Mapped[str] = mapped_column(Text, nullable=False)
    probabilities: Mapped[dict[str, float]] = mapped_column(JSONB, nullable=False)
    top_class: Mapped[str] = mapped_column(Text, nullable=False)
    top_prob: Mapped[float] = mapped_column(REAL, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    disagreements: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime | None] = _created_at()


class Crossmatch(Base):
    """External catalog cross-match record (separations in arcsec)."""

    __tablename__ = "crossmatches"

    id: Mapped[int] = _id_pk()
    tracked_object_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("tracked_objects.id", ondelete="CASCADE")
    )
    catalog: Mapped[str] = mapped_column(Text, nullable=False)
    catalog_release: Mapped[str | None] = mapped_column(Text)
    catalog_id: Mapped[str | None] = mapped_column(Text)
    sep_arcsec: Mapped[float | None] = mapped_column(REAL)
    sep_after_pm_arcsec: Mapped[float | None] = mapped_column(REAL)
    match_prob: Mapped[float | None] = mapped_column(REAL)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime | None] = _created_at()


class Spectrum(Base):
    """On-the-fly spectral energy distribution (um, uJy, MJD TDB)."""

    __tablename__ = "spectra"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tracked_object_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("tracked_objects.id", ondelete="CASCADE")
    )
    observation_id: Mapped[uuid.UUID | None] = mapped_column(_UUID, ForeignKey("observations.id"))
    wavelength_um: Mapped[list[float]] = mapped_column(ARRAY(REAL), nullable=False)
    flux_ujy: Mapped[list[float]] = mapped_column(ARRAY(REAL), nullable=False)
    flux_err_ujy: Mapped[list[float]] = mapped_column(ARRAY(REAL), nullable=False)
    mjd_tdb: Mapped[list[float]] = mapped_column(ARRAY(REAL), nullable=False)
    flags: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False)
    method: Mapped[str | None] = mapped_column(Text)
    storage_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = _created_at()


class ComparisonJob(Base):
    """A-vs-B FITS comparison job products and source diffs."""

    __tablename__ = "comparison_jobs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("projects.id", ondelete="CASCADE")
    )
    fits_a: Mapped[uuid.UUID] = mapped_column(_UUID, ForeignKey("fits_files.id"), nullable=False)
    fits_b: Mapped[uuid.UUID] = mapped_column(_UUID, ForeignKey("fits_files.id"), nullable=False)
    ops: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str | None] = mapped_column(Text)
    products: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    header_diff: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source_changes: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    processing_job_id: Mapped[uuid.UUID | None] = mapped_column(_UUID)
    created_at: Mapped[datetime | None] = _created_at()


# PHASE2: planetx_runs and planetx_candidates tables deferred per ROADMAP §2


class ProcessingJob(Base):
    """Asynchronous Celery job record keyed by parameter hash."""

    __tablename__ = "processing_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','cancelled')", name="status"
        ),
        Index("jobs_hash", "params_hash"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(_UUID, ForeignKey("users.id"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(_UUID, ForeignKey("projects.id"))
    task: Mapped[str] = mapped_column(Text, nullable=False)
    queue: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    params_hash: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    progress: Mapped[float | None] = mapped_column(REAL)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    outputs: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = _created_at()


class Provenance(Base):
    """Reproducibility record linking persisted entities to code, releases, and inputs."""

    __tablename__ = "provenance"
    __table_args__ = (Index("prov_entity", "entity_table", "entity_id"),)

    id: Mapped[int] = _id_pk()
    processing_job_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("processing_jobs.id")
    )
    entity_table: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[str] = mapped_column(Text, nullable=False)
    inputs: Mapped[list[Any] | dict[str, Any]] = mapped_column(JSONB, nullable=False)
    code_version: Mapped[str] = mapped_column(Text, nullable=False)
    lib_versions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    release_ids: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    rng_seed: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime | None] = _created_at()


class AuditLog(Base):
    """Write audit log for security and user-override traceability."""

    __tablename__ = "audit_log"

    id: Mapped[int] = _id_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(_UUID, ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity_table: Mapped[str | None] = mapped_column(Text)
    entity_id: Mapped[str | None] = mapped_column(Text)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[datetime | None] = _created_at()
