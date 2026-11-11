"""Shared API router helpers and RFC 9457 501 stub exception handler."""

from __future__ import annotations

from typing import Any, NoReturn

from fastapi import Request
from fastapi.responses import JSONResponse

from app.schemas.common import ProblemDetail

COMMON_501: dict[int | str, dict[str, Any]] = {
    501: {"model": ProblemDetail, "description": "Endpoint contract stub; not implemented yet."}
}


class NotImplementedAPIError(Exception):
    """Raised by OpenAPI skeleton stubs before business logic is implemented."""


def not_implemented() -> NoReturn:
    """Raise standard 501 stub error for unimplemented contract endpoints."""
    raise NotImplementedAPIError()


async def not_implemented_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render RFC 9457 problem+json payload for 501 contract stubs."""
    problem = ProblemDetail(
        type="https://spherex.example/problems/not-implemented",
        title="Not Implemented",
        status=501,
        detail=f"Contract stub only: {request.method} {request.url.path}",
        instance=str(request.url.path),
    )
    return JSONResponse(
        status_code=501,
        media_type="application/problem+json",
        content=problem.model_dump(),
    )
