"""FastAPI application factory and OpenAPI 3.1 contract setup."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Auto-initialize database schema when running with a relational store."""
    if os.environ.get("DATABASE_URL") and os.environ.get("SPHX_STORE_MODE", "").lower() != "memory":
        try:
            from alembic import command
            from alembic.config import Config

            paths = (Path("backend/alembic.ini"), Path("/app/backend/alembic.ini"))
            cfg_path = next((p for p in paths if p.is_file()), None)
            if cfg_path:
                command.upgrade(Config(str(cfg_path)), "head")
        except Exception:
            with suppress(Exception):
                from sqlalchemy import text

                import app.db.models  # noqa: F401
                from app.db.base import Base
                from app.db.session import get_engine

                with get_engine().connect() as conn:
                    conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
                    conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
                    conn.commit()
                Base.metadata.create_all(bind=get_engine())
    yield


def create_app() -> FastAPI:
    """Build FastAPI app with OpenAPI 3.1 contract routes and RFC 9457 error handlers."""
    app = FastAPI(
        title="SPHEREx Moving-Object & Trajectory Platform API",
        version="0.1.0",
        description="SPHEREx moving-object detection, astrometry, and trajectory API.",
        openapi_url="/api/v1/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
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
