"""Processing job persistence, provenance recording, and progress event streaming."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from functools import lru_cache
from importlib import metadata as importlib_metadata
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    DataRelease,
    Detection,
    FitsFile,
    ProcessingJob,
    Project,
    ProjectFile,
    Provenance,
    User,
)
from app.db.session import get_session_factory
from app.schemas.common import JobStatus, JobStatusResponse
from app.schemas.fields import DataReleaseRead
from app.schemas.objects import DetectionRead, SpectrumResponse, TrackedObjectSummary
from app.services.object_store import InMemoryObjectMixin, SqlAlchemyObjectMixin

TERMINAL_JOB_STATUSES: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})
_CUTOUT_TASK = "app.workers.tasks_fast.build_cutouts"
_UPLOAD_TASK = "app.workers.tasks_fast.validate_upload"
_ANALYSIS_TASK = "app.workers.tasks_heavy.run_analysis"


def compute_params_hash(task: str, params: Mapping[str, Any], release: str = "") -> bytes:
    """Compute deterministic 32-byte SHA-256 hash for idempotent job deduplication."""
    payload = json.dumps(
        {"task": task, "params": params, "release": release, "version": "0.1.0"},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).digest()


@lru_cache(maxsize=1)
def get_code_version() -> str:
    """Return configured application SHA or package version."""
    return os.environ.get("SPHX_CODE_VERSION", "0.1.0")


@lru_cache(maxsize=1)
def get_lib_versions() -> dict[str, str]:
    """Return key scientific/backend package versions for provenance."""
    out: dict[str, str] = {}
    for pkg in ("astropy", "numpy", "scipy", "fastapi", "sphx-core"):
        try:
            out[pkg] = importlib_metadata.version(pkg)
        except importlib_metadata.PackageNotFoundError:
            continue
    return out


class JobStore(ABC):
    """Abstract persistence and progress notification store for background jobs."""

    @abstractmethod
    def create_or_get_job(
        self,
        *,
        task: str,
        queue: str,
        params: dict[str, Any],
        params_hash: bytes,
        user_id: UUID | None = None,
        project_id: UUID | None = None,
        job_id: UUID | None = None,
    ) -> tuple[JobStatusResponse, bool]:
        """Return `(job, deduplicated)` matching `params_hash` or create a queued job."""

    @abstractmethod
    def get_job(self, job_id: UUID) -> JobStatusResponse | None:
        """Fetch job status by `job_id`."""

    @abstractmethod
    def get_job_params(self, job_id: UUID) -> dict[str, Any] | None:
        """Fetch raw job parameter dict by `job_id`."""

    @abstractmethod
    def get_job_by_param(self, task: str, key: str, value: str) -> JobStatusResponse | None:
        """Fetch latest job matching `task` and stringified `params[key] == value`."""

    def get_cutout_job(self, cutout_id: UUID) -> JobStatusResponse | None:
        """Fetch cutout job associated with `cutout_id`."""
        return self.get_job_by_param(_CUTOUT_TASK, "cutout_id", str(cutout_id))

    def get_upload_job(self, upload_id: UUID) -> JobStatusResponse | None:
        """Fetch upload validation job associated with `upload_id`."""
        return self.get_job_by_param(_UPLOAD_TASK, "upload_id", str(upload_id))

    def get_analysis_job(self, analysis_id: UUID) -> JobStatusResponse | None:
        """Fetch analysis job associated with `analysis_id`."""
        return self.get_job_by_param(_ANALYSIS_TASK, "analysis_id", str(analysis_id))

    @abstractmethod
    def save_detections(self, detections: Sequence[Mapping[str, Any]]) -> list[DetectionRead]:
        """Persist extracted source detections and return records with assigned IDs."""

    @abstractmethod
    def get_detections_by_ids(self, detection_ids: Sequence[int]) -> list[DetectionRead]:
        """Fetch persisted detections matching `detection_ids` ordered by ID."""

    @abstractmethod
    def register_fits_file(
        self,
        *,
        fits_file_id: UUID,
        metadata: dict[str, Any],
        sha256_hex: str,
        storage_path: str,
        uploader_id: UUID | None = None,
        project_id: UUID | None = None,
    ) -> None:
        """Persist validated FITS file metadata and optional project link."""

    @abstractmethod
    def get_fits_file(self, fits_file_id: UUID) -> dict[str, Any] | None:
        """Fetch persisted FITS file metadata by `fits_file_id`."""

    @abstractmethod
    def update_job(
        self,
        job_id: UUID,
        *,
        status: JobStatus | None = None,
        progress: float | None = None,
        outputs: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> JobStatusResponse:
        """Persist job state/progress updates and notify SSE listeners."""

    @abstractmethod
    def record_provenance(
        self,
        *,
        processing_job_id: UUID | None,
        entity_table: str,
        entity_id: str,
        inputs: list[Any] | dict[str, Any],
        release_ids: list[str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> None:
        """Write an immutable provenance record for a generated product."""

    @abstractmethod
    def list_releases(self) -> list[DataReleaseRead]:
        """List indexed data releases ordered by release_date descending."""

    @abstractmethod
    def iter_job_events(
        self, job_id: UUID, *, poll_interval_s: float = 0.25, max_wait_s: float = 30.0
    ) -> Iterator[JobStatusResponse]:
        """Yield job snapshots until terminal status or timeout."""

    @abstractmethod
    def save_analysis_observations(
        self, analysis_id: UUID, frames: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """Persist per-frame observations for an analysis run."""

    @abstractmethod
    def get_analysis_observations(self, analysis_id: UUID) -> list[dict[str, Any]]:
        """Fetch ordered observations associated with `analysis_id`."""

    @abstractmethod
    def select_tracked_objects(
        self,
        *,
        project_id: UUID,
        analysis_id: UUID,
        fits_file_ids: Sequence[UUID],
        candidates: Sequence[Mapping[str, Any]],
        selection_mode: str,
    ) -> list[TrackedObjectSummary]:
        """Create or reuse persistent tracked objects and assign sequential project labels."""

    @abstractmethod
    def list_project_objects(
        self, project_id: UUID, object_ids: Sequence[UUID] | None = None
    ) -> list[dict[str, Any]]:
        """List tracked objects belonging to `project_id`."""

    @abstractmethod
    def get_tracked_object_bundle(self, object_id: UUID) -> dict[str, Any] | None:
        """Fetch tracked object with observations, measurements, fits, associations, and class."""

    @abstractmethod
    def save_track_results(
        self,
        object_id: UUID,
        *,
        obj_updates: Mapping[str, Any],
        measurements: Sequence[Mapping[str, Any]],
        associations: Sequence[Mapping[str, Any]],
        motion_fits: Sequence[Mapping[str, Any]],
        classification: Mapping[str, Any] | None,
        crossmatches: Sequence[Mapping[str, Any]],
        caveats: Sequence[str],
    ) -> None:
        """Persist tracking results while preserving user-locked associations."""

    @abstractmethod
    def apply_association_override(
        self,
        object_id: UUID,
        epoch: str,
        *,
        action: str,
        detection_id: int | None,
        reason: str,
        user_id: UUID | None,
    ) -> dict[str, Any]:
        """Apply manual user association override and append an audit-log entry."""

    @abstractmethod
    def get_cached_spectrum(
        self, object_id: UUID, observation_id: UUID | None = None
    ) -> SpectrumResponse | None:
        """Return cached forced-PSF SED if available."""

    @abstractmethod
    def save_spectrum(self, spectrum: SpectrumResponse) -> None:
        """Persist computed forced-PSF SED."""


class InMemoryJobStore(InMemoryObjectMixin, JobStore):
    """Thread-safe in-memory job store with Condition-backed SSE progress streaming."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._jobs: dict[UUID, dict[str, Any]] = {}
        self._by_hash: dict[bytes, UUID] = {}
        self._by_param: dict[tuple[str, str, str], UUID] = {}
        self._fits_files: dict[UUID, dict[str, Any]] = {}
        self._detections: dict[int, DetectionRead] = {}
        self._next_det_id = 1
        self._provenance: list[dict[str, Any]] = []
        self._releases: dict[str, DataReleaseRead] = {}
        self._init_object_store()

    @staticmethod
    def _to_schema(rec: dict[str, Any]) -> JobStatusResponse:
        return JobStatusResponse.model_validate(rec)

    def create_or_get_job(
        self,
        *,
        task: str,
        queue: str,
        params: dict[str, Any],
        params_hash: bytes,
        user_id: UUID | None = None,
        project_id: UUID | None = None,
        job_id: UUID | None = None,
    ) -> tuple[JobStatusResponse, bool]:
        with self._cond:
            existing_id = self._by_hash.get(params_hash)
            if existing_id is not None:
                existing = self._jobs.get(existing_id)
                if existing is not None and existing["status"] not in {"failed", "cancelled"}:
                    return self._to_schema(existing), True

            jid = job_id or uuid4()
            now = datetime.now(UTC)
            rec = {
                "id": jid,
                "user_id": user_id,
                "project_id": project_id,
                "task": task,
                "queue": queue,
                "params": dict(params),
                "params_hash": params_hash,
                "status": "queued",
                "progress": 0.0,
                "error": None,
                "outputs": None,
                "started_at": None,
                "finished_at": None,
                "created_at": now,
            }
            self._jobs[jid] = rec
            self._by_hash[params_hash] = jid
            for key in ("cutout_id", "upload_id", "analysis_id", "fits_file_id"):
                if key in params:
                    self._by_param[(task, key, str(params[key]))] = jid
            self._cond.notify_all()
            return self._to_schema(rec), False

    def get_job(self, job_id: UUID) -> JobStatusResponse | None:
        with self._lock:
            rec = self._jobs.get(job_id)
            return self._to_schema(rec) if rec is not None else None

    def get_job_params(self, job_id: UUID) -> dict[str, Any] | None:
        with self._lock:
            rec = self._jobs.get(job_id)
            return dict(rec["params"]) if rec is not None else None

    def get_job_by_param(self, task: str, key: str, value: str) -> JobStatusResponse | None:
        with self._lock:
            jid = self._by_param.get((task, key, value))
            rec = self._jobs.get(jid) if jid is not None else None
            return self._to_schema(rec) if rec is not None else None

    def register_fits_file(
        self,
        *,
        fits_file_id: UUID,
        metadata: dict[str, Any],
        sha256_hex: str,
        storage_path: str,
        uploader_id: UUID | None = None,
        project_id: UUID | None = None,
    ) -> None:
        with self._lock:
            self._fits_files[fits_file_id] = {
                **metadata,
                "id": fits_file_id,
                "sha256": sha256_hex,
                "storage_path": storage_path,
                "uploader_id": uploader_id,
                "project_id": project_id,
            }

    def get_fits_file(self, fits_file_id: UUID) -> dict[str, Any] | None:
        with self._lock:
            rec = self._fits_files.get(fits_file_id)
            return dict(rec) if rec is not None else None

    def save_detections(self, detections: Sequence[Mapping[str, Any]]) -> list[DetectionRead]:
        saved: list[DetectionRead] = []
        with self._lock:
            for item in detections:
                det_id = self._next_det_id
                self._next_det_id += 1
                rec = DetectionRead.model_validate({**item, "id": det_id})
                self._detections[det_id] = rec
                saved.append(rec)
        return saved

    def get_detections_by_ids(self, detection_ids: Sequence[int]) -> list[DetectionRead]:
        with self._lock:
            return [self._detections[i] for i in detection_ids if i in self._detections]

    def update_job(
        self,
        job_id: UUID,
        *,
        status: JobStatus | None = None,
        progress: float | None = None,
        outputs: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> JobStatusResponse:
        with self._cond:
            rec = self._jobs.get(job_id)
            if rec is None:
                raise KeyError(f"Job not found: {job_id}")
            for k, v in (
                ("status", status),
                ("outputs", outputs),
                ("error", error),
                ("started_at", started_at),
                ("finished_at", finished_at),
            ):
                if v is not None:
                    rec[k] = v
            if progress is not None:
                rec["progress"] = float(max(0.0, min(1.0, progress)))
            self._cond.notify_all()
            return self._to_schema(rec)

    def record_provenance(
        self,
        *,
        processing_job_id: UUID | None,
        entity_table: str,
        entity_id: str,
        inputs: list[Any] | dict[str, Any],
        release_ids: list[str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            self._provenance.append(
                {
                    "processing_job_id": processing_job_id,
                    "entity_table": entity_table,
                    "entity_id": entity_id,
                    "inputs": inputs,
                    "code_version": get_code_version(),
                    "lib_versions": get_lib_versions(),
                    "release_ids": release_ids,
                    "params": params,
                }
            )

    def seed_releases(self, releases: Sequence[DataReleaseRead]) -> None:
        with self._lock:
            self._releases = {r.id: r for r in releases}

    def list_releases(self) -> list[DataReleaseRead]:
        with self._lock:
            return sorted(
                self._releases.values(),
                key=lambda r: (r.release_date or datetime.min.date(), r.id),
                reverse=True,
            )

    def iter_job_events(
        self, job_id: UUID, *, poll_interval_s: float = 0.25, max_wait_s: float = 30.0
    ) -> Iterator[JobStatusResponse]:
        deadline = time.monotonic() + max_wait_s
        last_key: tuple[str, float | None] | None = None
        while True:
            with self._cond:
                rec = self._jobs.get(job_id)
                if rec is None:
                    return
                snap = self._to_schema(rec)
                cur_key = (snap.status, snap.progress)
                if cur_key != last_key:
                    last_key = cur_key
                    should_yield = True
                else:
                    should_yield = False
                if not should_yield and snap.status not in TERMINAL_JOB_STATUSES:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0.0:
                        return
                    self._cond.wait(timeout=min(poll_interval_s, remaining))
                    continue
            if should_yield:
                yield snap
            if snap.status in TERMINAL_JOB_STATUSES or time.monotonic() >= deadline:
                return


class SqlAlchemyJobStore(SqlAlchemyObjectMixin, JobStore):
    """PostgreSQL-backed job and provenance store using SQLAlchemy 2.0 sessions."""

    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self._session_factory = session_factory or get_session_factory()

    def create_or_get_job(
        self,
        *,
        task: str,
        queue: str,
        params: dict[str, Any],
        params_hash: bytes,
        user_id: UUID | None = None,
        project_id: UUID | None = None,
        job_id: UUID | None = None,
    ) -> tuple[JobStatusResponse, bool]:
        with self._session_factory() as session:
            existing = session.scalar(
                select(ProcessingJob)
                .where(
                    ProcessingJob.params_hash == params_hash,
                    ProcessingJob.status.notin_(["failed", "cancelled"]),
                )
                .order_by(ProcessingJob.created_at.desc())
                .limit(1)
            )
            if existing is not None:
                return JobStatusResponse.model_validate(existing), True

            job = ProcessingJob(
                id=job_id or uuid4(),
                user_id=user_id,
                project_id=project_id,
                task=task,
                queue=queue,
                params=params,
                params_hash=params_hash,
                status="queued",
                progress=0.0,
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            return JobStatusResponse.model_validate(job), False

    def get_job(self, job_id: UUID) -> JobStatusResponse | None:
        with self._session_factory() as session:
            job = session.get(ProcessingJob, job_id)
            return JobStatusResponse.model_validate(job) if job is not None else None

    def get_job_params(self, job_id: UUID) -> dict[str, Any] | None:
        with self._session_factory() as session:
            job = session.get(ProcessingJob, job_id)
            return dict(job.params) if job is not None else None

    def get_job_by_param(self, task: str, key: str, value: str) -> JobStatusResponse | None:
        with self._session_factory() as session:
            job = session.scalar(
                select(ProcessingJob)
                .where(
                    ProcessingJob.task == task,
                    ProcessingJob.params[key].astext == value,
                )
                .order_by(ProcessingJob.created_at.desc())
                .limit(1)
            )
            return JobStatusResponse.model_validate(job) if job is not None else None

    def register_fits_file(
        self,
        *,
        fits_file_id: UUID,
        metadata: dict[str, Any],
        sha256_hex: str,
        storage_path: str,
        uploader_id: UUID | None = None,
        project_id: UUID | None = None,
    ) -> None:
        with self._session_factory() as session:
            if session.get(FitsFile, fits_file_id) is None:
                lam_min = metadata.get("wavelength_min_um")
                lam_max = metadata.get("wavelength_max_um")
                lam_ctr = (
                    0.5 * (float(lam_min) + float(lam_max))
                    if lam_min is not None and lam_max is not None
                    else None
                )
                valid_user = uploader_id if uploader_id and session.get(User, uploader_id) else None
                row = FitsFile(
                    id=fits_file_id,
                    origin=str(metadata.get("origin", "upload")),
                    provider=str(metadata.get("provider", "upload")),
                    obs_id=str(metadata.get("obs_id") or fits_file_id),
                    uploader_id=valid_user,
                    storage_path=storage_path,
                    sha256=bytes.fromhex(sha256_hex),
                    size_bytes=int(metadata["size_bytes"]),
                    array_id=metadata.get("array_id"),
                    band_label=metadata.get("band_label"),
                    wavelength_min_um=lam_min,
                    wavelength_max_um=lam_max,
                    wavelength_center_um=lam_ctr,
                    date_obs_utc=metadata.get("date_obs_utc"),
                    mjd_tdb_mid=metadata.get("mjd_tdb_mid"),
                    exptime_s=metadata.get("exptime_s"),
                    ra_center=metadata.get("ra_center"),
                    dec_center=metadata.get("dec_center"),
                    naxis1=metadata.get("naxis1"),
                    naxis2=metadata.get("naxis2"),
                    pixscale_arcsec=metadata.get("pixscale_arcsec"),
                    rotation_deg=metadata.get("rotation_deg"),
                    wcs_header=str(metadata.get("wcs_header", "")),
                    footprint=metadata.get("footprint"),
                    healpix9=metadata.get("healpix9"),
                    header_summary=dict(metadata.get("header_summary") or {}),
                    validation=metadata.get("validation"),
                )
                session.add(row)
            if (
                project_id is not None
                and session.get(Project, project_id) is not None
                and session.get(ProjectFile, (project_id, fits_file_id)) is None
            ):
                session.add(ProjectFile(project_id=project_id, fits_file_id=fits_file_id))
            session.commit()

    def get_fits_file(self, fits_file_id: UUID) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(FitsFile, fits_file_id)
            if row is None:
                return None
            return {
                "id": row.id,
                "origin": row.origin,
                "storage_path": row.storage_path,
                "sha256": row.sha256.hex(),
                "size_bytes": row.size_bytes,
                "band_label": row.band_label,
                "array_id": row.array_id,
                "wavelength_min_um": row.wavelength_min_um,
                "wavelength_max_um": row.wavelength_max_um,
                "date_obs_utc": row.date_obs_utc,
                "mjd_tdb_mid": row.mjd_tdb_mid,
                "exptime_s": row.exptime_s,
                "ra_center": row.ra_center,
                "dec_center": row.dec_center,
                "naxis1": row.naxis1,
                "naxis2": row.naxis2,
                "pixscale_arcsec": row.pixscale_arcsec,
                "rotation_deg": row.rotation_deg,
                "wcs_header": row.wcs_header,
                "footprint": row.footprint,
                "healpix9": row.healpix9,
                "header_summary": row.header_summary,
                "validation": row.validation,
            }

    def save_detections(self, detections: Sequence[Mapping[str, Any]]) -> list[DetectionRead]:
        if not detections:
            return []
        with self._session_factory() as session:
            rows = [Detection(**dict(item)) for item in detections]
            session.add_all(rows)
            session.commit()
            for row in rows:
                session.refresh(row)
            return [DetectionRead.model_validate(row) for row in rows]

    def get_detections_by_ids(self, detection_ids: Sequence[int]) -> list[DetectionRead]:
        ids = [int(i) for i in detection_ids]
        if not ids:
            return []
        with self._session_factory() as session:
            rows = session.scalars(
                select(Detection).where(Detection.id.in_(ids)).order_by(Detection.id)
            ).all()
            return [DetectionRead.model_validate(row) for row in rows]

    def update_job(
        self,
        job_id: UUID,
        *,
        status: JobStatus | None = None,
        progress: float | None = None,
        outputs: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> JobStatusResponse:
        with self._session_factory() as session:
            job = session.get(ProcessingJob, job_id)
            if job is None:
                raise KeyError(f"Job not found: {job_id}")
            for k, v in (
                ("status", status),
                ("outputs", outputs),
                ("error", error),
                ("started_at", started_at),
                ("finished_at", finished_at),
            ):
                if v is not None:
                    setattr(job, k, v)
            if progress is not None:
                job.progress = float(max(0.0, min(1.0, progress)))
            session.commit()
            session.refresh(job)
            return JobStatusResponse.model_validate(job)

    def record_provenance(
        self,
        *,
        processing_job_id: UUID | None,
        entity_table: str,
        entity_id: str,
        inputs: list[Any] | dict[str, Any],
        release_ids: list[str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> None:
        with self._session_factory() as session:
            row = Provenance(
                processing_job_id=processing_job_id,
                entity_table=entity_table,
                entity_id=entity_id,
                inputs=inputs,
                code_version=get_code_version(),
                lib_versions=get_lib_versions(),
                release_ids=release_ids,
                params=params,
            )
            session.add(row)
            session.commit()

    def list_releases(self) -> list[DataReleaseRead]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(DataRelease).order_by(DataRelease.release_date.desc().nullslast())
            ).all()
            return [DataReleaseRead.model_validate(r) for r in rows]

    def iter_job_events(
        self, job_id: UUID, *, poll_interval_s: float = 0.25, max_wait_s: float = 30.0
    ) -> Iterator[JobStatusResponse]:
        deadline = time.monotonic() + max_wait_s
        last_key: tuple[str, float | None] | None = None
        while True:
            snap = self.get_job(job_id)
            if snap is None:
                return
            cur_key = (snap.status, snap.progress)
            if cur_key != last_key:
                last_key = cur_key
                yield snap
            if snap.status in TERMINAL_JOB_STATUSES or time.monotonic() >= deadline:
                return
            time.sleep(min(poll_interval_s, max(0.01, deadline - time.monotonic())))


_default_memory_store = InMemoryJobStore()


@lru_cache(maxsize=1)
def get_job_store() -> JobStore:
    """Return configured job store (`memory` for offline dev/tests, `db` for PostgreSQL)."""
    mode = os.environ.get("SPHX_STORE_MODE", "").lower()
    if mode == "memory":
        return _default_memory_store
    if mode == "db" or os.environ.get("DATABASE_URL"):
        return SqlAlchemyJobStore()
    return _default_memory_store
