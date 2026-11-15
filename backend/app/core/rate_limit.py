"""Inbound rate limiting (sliding-window) and HTTP caching middleware."""

from __future__ import annotations

import hashlib
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import Settings
from app.schemas.common import ProblemDetail
from app.services.cache import get_cache_backend

_BYPASS_PATHS = {"/health", "/healthz", "/docs", "/redoc", "/api/v1/openapi.json"}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiter keyed by client IP or auth token with ETag 304 caching."""

    def __init__(self, app, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings
        self.cache = get_cache_backend()

    async def dispatch(self, request: Request, call_next) -> Response:
        limit = self.settings.rate_limit_per_minute
        path = request.url.path

        if limit <= 0 or path in _BYPASS_PATHS:
            return await call_next(request)

        client_id = request.headers.get("authorization") or (
            request.client.host if request.client else "unknown"
        )
        cache_key = f"ratelimit:{client_id}:{int(time.time() // 60)}"
        count = self.cache.get_json(cache_key) or 0
        if count >= limit:
            problem = ProblemDetail(
                type="https://httpstatuses.com/429",
                title="Rate Limit Exceeded",
                status=429,
                detail=f"Rate limit of {limit} requests per minute exceeded. Please slow down.",
                instance=path,
            )
            return JSONResponse(
                status_code=429,
                media_type="application/problem+json",
                content=problem.model_dump(exclude_none=True),
                headers={"Retry-After": "60"},
            )

        self.cache.set_json(cache_key, count + 1, ttl_s=65)
        response = await call_next(request)

        if (
            request.method == "GET"
            and response.status_code == 200
            and not response.headers.get("etag")
        ):
            body = b"".join([chunk async for chunk in response.body_iterator])
            etag = f'"{hashlib.md5(body).hexdigest()}"'
            cache_ctrl = response.headers.get("Cache-Control", "public, max-age=60")
            if request.headers.get("if-none-match") == etag:
                return Response(
                    status_code=304,
                    headers={"ETag": etag, "Cache-Control": cache_ctrl},
                )
            headers = dict(response.headers)
            headers["ETag"] = etag
            return Response(
                content=body,
                status_code=200,
                headers=headers,
                media_type=response.media_type,
            )

        return response
