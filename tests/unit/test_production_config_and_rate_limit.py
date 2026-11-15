"""Unit tests for production configuration, structured logging, and rate limiting."""

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from starlette.testclient import TestClient

from app.core.config import Settings
from app.core.rate_limit import RateLimitMiddleware


def test_settings_defaults_and_properties():
    settings = Settings(
        app_env="production",
        cors_origins="https://example.org, https://sphx.astro.org",
        rate_limit_per_minute=60,
    )
    assert settings.is_production is True
    assert settings.use_json_logging is True
    assert settings.cors_origin_list == ["https://example.org", "https://sphx.astro.org"]


def test_rate_limit_middleware_and_etag():
    test_app = FastAPI()
    settings = Settings(rate_limit_per_minute=2)
    test_app.add_middleware(RateLimitMiddleware, settings=settings)

    @test_app.get("/test-data")
    def get_data():
        return PlainTextResponse("astronomical_payload_42")

    client = TestClient(test_app)

    # First request: 200 OK + ETag header
    r1 = client.get("/test-data")
    assert r1.status_code == 200
    assert "etag" in r1.headers
    etag = r1.headers["etag"]

    # Second request with If-None-Match: 304 Not Modified
    r2 = client.get("/test-data", headers={"if-none-match": etag})
    assert r2.status_code == 304

    # Third request: exceeds limit (2 req/min) -> 429 ProblemDetail
    r3 = client.get("/test-data")
    assert r3.status_code == 429
    assert r3.headers.get("retry-after") == "60"
    data = r3.json()
    assert data["status"] == 429
    assert "Rate Limit Exceeded" in data["title"]
