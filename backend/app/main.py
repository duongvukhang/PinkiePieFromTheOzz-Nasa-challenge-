"""FastAPI application factory and OpenAPI 3.1 contract setup."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError

from app.api.deps import (
    APIProblemError,
    NotImplementedAPIError,
    api_problem_handler,
    http_exception_handler,
    not_implemented_handler,
    provider_error_handler,
    validation_error_handler,
)
from app.api.v1 import api_v1_router
from app.api.v1.embed import root_router as embed_root_router
from app.providers import ProviderError
from app.schemas.common import HealthResponse


def create_app() -> FastAPI:
    """Build FastAPI app with OpenAPI 3.1 contract routes and RFC 9457 error handlers."""
    app = FastAPI(
        title="SPHEREx Moving-Object & Trajectory Platform API",
        version="0.1.0",
        description="SPHEREx moving-object detection, astrometry, and trajectory API.",
        openapi_url="/api/v1/openapi.json",
    )
    app.add_exception_handler(NotImplementedAPIError, not_implemented_handler)
    app.add_exception_handler(APIProblemError, api_problem_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(ProviderError, provider_error_handler)

    @app.get("/health", tags=["health"])
    async def health() -> HealthResponse:
        """Liveness probe."""
        return HealthResponse()

    app.include_router(api_v1_router)
    app.include_router(embed_root_router)
    return app


app = create_app()
