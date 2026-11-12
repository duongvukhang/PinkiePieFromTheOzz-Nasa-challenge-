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

from app.db.models import DataRelease, ProcessingJob, Provenance
from app.db.session import get_session_factory
from app.schemas.common import JobStatus, JobStatusResponse
from app.schemas.fields import DataReleaseRead

TERMINAL_JOB_STATUSES: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})


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
    def get_cutout_job(self, cutout_id: UUID) -> JobStatusResponse | None:
        """Fetch cutout job associated with `cutout_id`."""

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


class InMemoryJobStore(JobStore):
    """Thread-safe in-memory job store with Condition-backed SSE progress streaming."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._jobs: dict[UUID, dict[str, Any]] = {}
        self._by_hash: dict[bytes, UUID] = {}
        self._by_cutout: dict[UUID, UUID] = {}
        self._provenance: list[dict[str, Any]] = []
        self._releases: dict[str, DataReleaseRead] = {}

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
            if "cutout_id" in params:
                self._by_cutout[UUID(str(params["cutout_id"]))] = jid
            self._cond.notify_all()
            return self._to_schema(rec), False

    def get_job(self, job_id: UUID) -> JobStatusResponse | None:
        with self._lock:
            rec = self._jobs.get(job_id)
            return self._to_schema(rec) if rec is not None else None

    def get_cutout_job(self, cutout_id: UUID) -> JobStatusResponse | None:
        with self._lock:
            jid = self._by_cutout.get(cutout_id)
            if jid is None:
                return None
            rec = self._jobs.get(jid)
            return self._to_schema(rec) if rec is not None else None

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


class SqlAlchemyJobStore(JobStore):
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

    def get_cutout_job(self, cutout_id: UUID) -> JobStatusResponse | None:
        cid = str(cutout_id)
        with self._session_factory() as session:
            job = session.scalar(
                select(ProcessingJob)
                .where(
                    ProcessingJob.task == "app.workers.tasks_fast.build_cutouts",
                    ProcessingJob.params["cutout_id"].astext == cid,
                )
                .order_by(ProcessingJob.created_at.desc())
                .limit(1)
            )
            return JobStatusResponse.model_validate(job) if job is not None else None

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
