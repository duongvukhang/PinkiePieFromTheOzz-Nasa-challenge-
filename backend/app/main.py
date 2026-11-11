"""FastAPI application factory and OpenAPI 3.1 contract skeleton."""

from fastapi import FastAPI

from app.api.deps import NotImplementedAPIError, not_implemented_handler
from app.api.v1 import api_v1_router
from app.api.v1.embed import root_router as embed_root_router
from app.schemas.common import HealthResponse


def create_app() -> FastAPI:
    """Build FastAPI app with MVP OpenAPI contract routes and 501 stubs."""
    app = FastAPI(
        title="SPHEREx Moving-Object & Trajectory Platform API",
        version="0.1.0",
        description="MVP OpenAPI 3.1 contract skeleton (endpoint stubs return 501).",
        openapi_url="/api/v1/openapi.json",
    )
    app.add_exception_handler(NotImplementedAPIError, not_implemented_handler)

    @app.get("/health", tags=["health"])
    async def health() -> HealthResponse:
        """Liveness probe."""
        return HealthResponse()

    app.include_router(api_v1_router)
    app.include_router(embed_root_router)
    return app


app = create_app()
