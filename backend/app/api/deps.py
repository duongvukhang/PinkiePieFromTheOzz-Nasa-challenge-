"""Shared API router helpers, RFC 9457 error handlers, pagination, and DI providers."""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from typing import Any, NoReturn, TypeVar
from uuid import UUID

from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.providers import (
    GaiaProvider,
    MpcProvider,
    ProviderError,
    ProviderQueryError,
    ProviderRateLimitError,
    ProviderUnavailableError,
    SimbadProvider,
    SpherexIrsaProvider,
    WiseProvider,
    default_registry,
)
from app.schemas.common import JobStatusResponse, ProblemDetail
from app.services.cache import CacheBackend, get_cache_backend
from app.services.jobs import JobStore, get_job_store
from app.services.storage import StorageBackend, get_storage_backend

T = TypeVar("T")

COMMON_501: dict[int | str, dict[str, Any]] = {
    501: {"model": ProblemDetail, "description": "Endpoint contract stub; not implemented yet."}
}


class NotImplementedAPIError(Exception):
    """Raised by OpenAPI skeleton stubs before business logic is implemented."""


class APIProblemError(Exception):
    """Domain error rendered as an RFC 9457 problem+json response."""

    def __init__(
        self,
        status_code: int,
        title: str,
        detail: str | None = None,
        *,
        error_type: str = "about:blank",
        science_impact: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail or title)
        self.status_code = status_code
        self.title = title
        self.detail = detail
        self.error_type = error_type
        self.science_impact = science_impact
        self.headers = headers


def not_implemented() -> NoReturn:
    """Raise standard 501 stub error for unimplemented contract endpoints."""
    raise NotImplementedAPIError()


def _problem_response(
    request: Request,
    *,
    status_code: int,
    title: str,
    detail: str | None = None,
    error_type: str = "about:blank",
    science_impact: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    problem = ProblemDetail(
        type=error_type,
        title=title,
        status=status_code,
        detail=detail,
        instance=str(request.url.path),
        science_impact=science_impact,
    )
    return JSONResponse(
        status_code=status_code,
        media_type="application/problem+json",
        content=problem.model_dump(exclude_none=True),
        headers=headers,
    )


async def not_implemented_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render RFC 9457 problem+json payload for 501 contract stubs."""
    del exc
    return _problem_response(
        request,
        status_code=501,
        title="Not Implemented",
        detail=f"Contract stub only: {request.method} {request.url.path}",
        error_type="https://spherex.example/problems/not-implemented",
    )


async def api_problem_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render RFC 9457 problem+json payload for `APIProblemError`."""
    assert isinstance(exc, APIProblemError)
    return _problem_response(
        request,
        status_code=exc.status_code,
        title=exc.title,
        detail=exc.detail,
        error_type=exc.error_type,
        science_impact=exc.science_impact,
        headers=exc.headers,
    )


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render RFC 9457 problem+json payload for FastAPI/Pydantic validation errors."""
    assert isinstance(exc, RequestValidationError)
    summary = "; ".join(
        f"{'.'.join(str(p) for p in err.get('loc', ()))}: {err.get('msg', 'invalid')}"
        for err in exc.errors()
    )
    return _problem_response(
        request,
        status_code=422,
        title="Validation Error",
        detail=summary or "Invalid request parameters",
        error_type="https://spherex.example/problems/validation-error",
    )


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render RFC 9457 problem+json payload for `HTTPException`."""
    assert isinstance(exc, HTTPException)
    return _problem_response(
        request,
        status_code=exc.status_code,
        title="HTTP Error",
        detail=str(exc.detail) if exc.detail is not None else None,
        headers=dict(exc.headers) if exc.headers else None,
    )


async def provider_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Map archive `ProviderError` hierarchy to RFC 9457 problem+json responses."""
    assert isinstance(exc, ProviderError)
    if isinstance(exc, ProviderQueryError):
        return _problem_response(
            request,
            status_code=400,
            title="Invalid Archive Query",
            detail=str(exc),
            error_type="https://spherex.example/problems/provider-query",
        )
    if isinstance(exc, ProviderRateLimitError):
        hdrs = (
            {"Retry-After": str(int(max(1.0, exc.retry_after_s)))}
            if exc.retry_after_s is not None
            else None
        )
        return _problem_response(
            request,
            status_code=429,
            title="Archive Rate Limit Exceeded",
            detail=str(exc),
            error_type="https://spherex.example/problems/provider-rate-limit",
            science_impact="Upstream archive throttled requests; cached epochs remain usable.",
            headers=hdrs,
        )
    if isinstance(exc, ProviderUnavailableError):
        return _problem_response(
            request,
            status_code=503,
            title="Archive Provider Unavailable",
            detail=str(exc),
            error_type="https://spherex.example/problems/provider-unavailable",
            science_impact="External archive unavailable; previously cached products still work.",
        )
    return _problem_response(
        request,
        status_code=502,
        title="Archive Provider Error",
        detail=str(exc),
        error_type="https://spherex.example/problems/provider-error",
    )


def paginate_items(
    items: Sequence[T], cursor: str | None, limit: int
) -> tuple[list[T], str | None]:
    """Slice `items` using an opaque base64 offset cursor and return `(page, next_cursor)`."""
    offset = 0
    if cursor is not None:
        try:
            raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
            offset = int(json.loads(raw)["offset"])
            if offset < 0:
                raise ValueError("negative offset")
        except Exception as exc:
            raise APIProblemError(
                422,
                "Invalid Pagination Cursor",
                "Cursor must be a valid opaque token.",
                error_type="https://spherex.example/problems/invalid-cursor",
            ) from exc

    page = list(items[offset : offset + limit])
    next_cursor = (
        base64.urlsafe_b64encode(json.dumps({"offset": offset + limit}).encode("utf-8")).decode(
            "ascii"
        )
        if offset + limit < len(items)
        else None
    )
    return page, next_cursor


def get_cache() -> CacheBackend:
    """FastAPI dependency returning configured cache backend."""
    return get_cache_backend()


def get_store() -> JobStore:
    """FastAPI dependency returning configured job/metadata store."""
    return get_job_store()


def get_storage() -> StorageBackend:
    """FastAPI dependency returning configured binary object store."""
    return get_storage_backend()


def get_spherex_provider() -> SpherexIrsaProvider:
    """FastAPI dependency returning registered SPHEREx IRSA provider."""
    return default_registry.get("spherex_irsa")  # type: ignore[return-value]


def get_simbad_provider() -> SimbadProvider:
    """FastAPI dependency returning registered SIMBAD provider."""
    return default_registry.get("simbad")  # type: ignore[return-value]


def get_gaia_provider() -> GaiaProvider:
    """FastAPI dependency returning registered Gaia provider."""
    return default_registry.get("gaia")  # type: ignore[return-value]


def get_wise_provider() -> WiseProvider:
    """FastAPI dependency returning registered WISE/CatWISE provider."""
    return default_registry.get("wise", default_catalog="catwise_2020")  # type: ignore[return-value]


def get_mpc_provider() -> MpcProvider:
    """FastAPI dependency returning registered IAU MPC provider."""
    return default_registry.get("mpc")  # type: ignore[return-value]


def get_optional_user_id(request: Request) -> UUID | None:
    """Return authenticated user UUID if credentials are supplied, else None (anonymous)."""
    # PHASE2: wire full session/Bearer token lookup when projects/auth endpoints are implemented
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and len(auth) > 7:
        try:
            return UUID(auth[7:].strip())
        except ValueError:
            return None
    return None


def enforce_fov_quota(fov_arcmin: float, user_id: UUID | None, *, max_anon: float = 15.0) -> None:
    """Enforce anonymous FOV quota (arcmin) via RFC 9457 403 error."""
    if user_id is None and fov_arcmin > max_anon:
        raise APIProblemError(
            status.HTTP_403_FORBIDDEN,
            "FOV Limit Exceeded",
            f"Anonymous FOV limit is {max_anon:.0f} arcmin; sign in for up to 60 arcmin.",
            error_type="https://spherex.example/problems/fov-limit",
            science_impact="Wider fields require authenticated worker quota.",
        )


def require_succeeded_job(
    job: JobStatusResponse | None,
    resource_id: UUID,
    *,
    label: str,
    output_key: str,
) -> JobStatusResponse:
    """Ensure a background job exists (404), is not active (409), and succeeded (502)."""
    if job is None:
        raise APIProblemError(
            status.HTTP_404_NOT_FOUND,
            f"{label} Not Found",
            f"No {label.lower()} found for {resource_id}.",
        )
    if job.status in {"queued", "running"}:
        raise APIProblemError(
            status.HTTP_409_CONFLICT,
            f"{label} In Progress",
            f"{label} job {job.id} is {job.status} ({int((job.progress or 0.0) * 100)}%).",
        )
    if job.status != "succeeded" or not job.outputs or output_key not in job.outputs:
        detail = (
            str(job.error.get("detail"))
            if job.error and job.error.get("detail")
            else f"{label} job failed."
        )
        raise APIProblemError(status.HTTP_502_BAD_GATEWAY, f"{label} Failed", detail)
    return job
