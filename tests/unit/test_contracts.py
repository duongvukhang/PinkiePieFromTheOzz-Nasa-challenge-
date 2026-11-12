"""Unit tests for MVP DB metadata, flag bitmasks, offline DDL, schemas, and 501 stubs."""

from __future__ import annotations

import io
import uuid
from contextlib import redirect_stdout
from typing import Any

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.db import Base, DetectionFlag
from app.main import app

EXPECTED_MVP_TABLES = {
    "users",
    "api_tokens",
    "projects",
    "project_members",
    "project_versions",
    "notes",
    "findings",
    "saved_views",
    "data_releases",
    "sky_fields",
    "observations",
    "fits_files",
    "project_files",
    "detections",
    "tracked_objects",
    "object_aliases",
    "associations",
    "epoch_measurements",
    "motion_fits",
    "classifications",
    "crossmatches",
    "spectra",
    "comparison_jobs",
    "processing_jobs",
    "provenance",
    "audit_log",
}

_UID = str(uuid.UUID(int=1))


def test_mvp_metadata_tables_and_overrides() -> None:
    """Ensure SQLAlchemy metadata defines exact MVP tables and Phase 2 overrides."""
    tables = set(Base.metadata.tables.keys())
    assert tables == EXPECTED_MVP_TABLES
    assert "planetx_runs" not in tables
    assert "planetx_candidates" not in tables

    users = Base.metadata.tables["users"]
    assert "password_hash" in users.c

    detections = Base.metadata.tables["detections"]
    assert "healpix9" in detections.c
    assert "healpix4" not in detections.c
    assert not detections.kwargs.get("postgresql_partition_by")
    idx_cols = {idx.name: [c.name for c in idx.columns] for idx in detections.indexes}
    assert idx_cols["detections_ra_dec_idx"] == ["ra", "dec"]
    assert idx_cols["detections_hpx_idx"] == ["healpix9"]


def test_detection_flag_bit_positions() -> None:
    """Verify flag bit positions match DATA_MODEL.md section 3."""
    assert DetectionFlag.SATURATED == 1
    assert DetectionFlag.REPROJECTED == 1 << 11
    assert DetectionFlag.AMBIGUOUS_MATCH == 1 << 16
    assert DetectionFlag.USER_OVERRIDE == 1 << 19


def test_alembic_offline_sql_generation() -> None:
    """Verify Alembic generates PostgreSQL DDL without q3c or detections partitioning."""
    cfg = Config("backend/alembic.ini")
    buf = io.StringIO()
    with redirect_stdout(buf):
        command.upgrade(cfg, "head", sql=True)
    ddl = buf.getvalue()
    assert "CREATE EXTENSION IF NOT EXISTS pgcrypto" in ddl
    assert "CREATE EXTENSION IF NOT EXISTS citext" in ddl
    assert "q3c" not in ddl.lower()
    assert "PARTITION BY" not in ddl
    for table in EXPECTED_MVP_TABLES:
        assert f"CREATE TABLE {table}" in ddl


def test_openapi_schema_and_health() -> None:
    """Verify OpenAPI 3.1 contract generation, MVP exclusions, and /health."""
    spec = app.openapi()
    assert spec["openapi"].startswith("3.1.")
    paths = set(spec["paths"].keys())
    assert "/health" in paths
    assert "/api/v1/fields/frames" in paths
    assert "/embed" in paths
    assert not any(p.startswith("/api/v1/planetx") for p in paths)

    client = TestClient(app)
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


IMPLEMENTED_OPS: set[tuple[str, str]] = {
    ("GET", "/api/v1/resolve"),
    ("GET", "/api/v1/fields/frames"),
    ("POST", "/api/v1/cutouts"),
    ("GET", "/api/v1/cutouts/{cutout_id}"),
    ("GET", "/api/v1/cutouts/{cutout_id}/frames/{frame_id}/display.webp"),
    ("GET", "/api/v1/cutouts/{cutout_id}/frames/{frame_id}/science.bin"),
    ("GET", "/api/v1/cutouts/{cutout_id}/frames/{frame_id}/frame.fits"),
    ("POST", "/api/v1/uploads"),
    ("GET", "/api/v1/uploads/{upload_id}/report"),
    ("POST", "/api/v1/analyses"),
    ("GET", "/api/v1/analyses/{analysis_id}/sources"),
    ("GET", "/api/v1/analyses/{analysis_id}/suggestions"),
    ("POST", "/api/v1/projects/{project_id}/objects"),
    ("POST", "/api/v1/projects/{project_id}/tracks"),
    ("GET", "/api/v1/objects/{object_id}"),
    ("GET", "/api/v1/objects/{object_id}/associations"),
    ("PATCH", "/api/v1/objects/{object_id}/associations/{epoch}"),
    ("GET", "/api/v1/objects/{object_id}/spectrum"),
    ("GET", "/api/v1/objects/{object_id}/lightcurve"),
    ("GET", "/api/v1/catalogs/overlay"),
    ("GET", "/api/v1/releases"),
    ("GET", "/api/v1/examples"),
    ("GET", "/api/v1/jobs/{job_id}"),
    ("GET", "/api/v1/jobs/{job_id}/events"),
}


def test_stub_endpoints_return_501() -> None:
    """Exercise every unimplemented MVP endpoint stub and assert 501 RFC 9457 problem+json."""
    client = TestClient(app)
    cases: list[tuple[str, str, dict[str, Any]]] = [
        (
            "POST",
            "/api/v1/auth/register",
            {"json": {"email": "a@b.com", "password": "password123"}},
        ),
        ("POST", "/api/v1/auth/login", {"json": {"email": "a@b.com", "password": "password123"}}),
        ("POST", "/api/v1/auth/logout", {}),
        ("GET", "/api/v1/auth/me", {}),
        ("POST", "/api/v1/auth/tokens", {"json": {"name": "cli", "scopes": ["read"]}}),
        ("GET", "/api/v1/auth/tokens", {}),
        ("DELETE", f"/api/v1/auth/tokens/{_UID}", {}),
        ("POST", "/api/v1/compare", {"json": {"a": _UID, "b": _UID, "ops": ["diff"]}}),
        ("GET", f"/api/v1/compare/{_UID}", {}),
        ("GET", "/api/v1/projects", {}),
        ("POST", "/api/v1/projects", {"json": {"name": "WISE 0855"}}),
        ("GET", f"/api/v1/projects/{_UID}", {}),
        ("PATCH", f"/api/v1/projects/{_UID}", {"json": {"name": "Updated"}}),
        ("DELETE", f"/api/v1/projects/{_UID}", {}),
        ("GET", f"/api/v1/projects/{_UID}/notes", {}),
        ("POST", f"/api/v1/projects/{_UID}/notes", {"json": {"body_md": "hi"}}),
        ("PATCH", f"/api/v1/projects/{_UID}/notes/{_UID}", {"json": {"body_md": "edit"}}),
        ("DELETE", f"/api/v1/projects/{_UID}/notes/{_UID}", {}),
        ("GET", f"/api/v1/projects/{_UID}/findings", {}),
        ("POST", f"/api/v1/projects/{_UID}/findings", {"json": {"title": "Candidate"}}),
        ("PATCH", f"/api/v1/projects/{_UID}/findings/{_UID}", {"json": {"status": "candidate"}}),
        ("DELETE", f"/api/v1/projects/{_UID}/findings/{_UID}", {}),
        ("GET", f"/api/v1/projects/{_UID}/views", {}),
        ("POST", f"/api/v1/projects/{_UID}/views", {"json": {"url_state": "ra=1&dec=2"}}),
        ("DELETE", f"/api/v1/projects/{_UID}/views/{_UID}", {}),
        ("GET", f"/api/v1/projects/{_UID}/versions", {}),
        ("POST", f"/api/v1/projects/{_UID}/versions", {"json": {"message": "v1"}}),
        ("POST", f"/api/v1/projects/{_UID}/versions/1/restore", {}),
        ("POST", f"/api/v1/projects/{_UID}/share", {}),
        ("DELETE", f"/api/v1/projects/{_UID}/share", {}),
        ("POST", f"/api/v1/projects/{_UID}/exports", {"json": {"format": "csv"}}),
        ("GET", "/api/v1/oembed?url=https://example.org/v?ra=1", {}),
        ("GET", "/api/v1/render.gif?ra=133.795&dec=-7.245", {}),
        ("GET", "/embed?ra=133.795&dec=-7.245", {}),
    ]
    for method, url, kwargs in cases:
        res = client.request(method, url, **kwargs)
        assert res.status_code == 501, f"{method} {url} -> {res.status_code}: {res.text}"
        assert res.headers["content-type"].startswith("application/problem+json")
        body = res.json()
        assert body["status"] == 501

    spec_ops = {
        (method.upper(), path)
        for path, ops in app.openapi()["paths"].items()
        if path != "/health"
        for method in ops
    }
    assert len(cases) + len(IMPLEMENTED_OPS) == len(spec_ops)
    assert IMPLEMENTED_OPS.issubset(spec_ops)
