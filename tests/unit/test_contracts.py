"""Unit tests for MVP DB metadata, flag bitmasks, offline DDL, schemas, and OpenAPI coverage."""

from __future__ import annotations

import io
import uuid
from contextlib import redirect_stdout

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
    ("POST", "/api/v1/auth/register"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/logout"),
    ("GET", "/api/v1/auth/me"),
    ("POST", "/api/v1/auth/tokens"),
    ("GET", "/api/v1/auth/tokens"),
    ("DELETE", "/api/v1/auth/tokens/{token_id}"),
    ("GET", "/api/v1/resolve"),
    ("GET", "/api/v1/fields/frames"),
    ("POST", "/api/v1/cutouts"),
    ("GET", "/api/v1/cutouts/{cutout_id}"),
    ("GET", "/api/v1/cutouts/{cutout_id}/frames/{frame_id}/display.webp"),
    ("GET", "/api/v1/cutouts/{cutout_id}/frames/{frame_id}/preview.png"),
    ("GET", "/api/v1/cutouts/{cutout_id}/frames/{frame_id}/science.bin"),
    ("GET", "/api/v1/cutouts/{cutout_id}/frames/{frame_id}/frame.fits"),
    ("POST", "/api/v1/uploads"),
    ("GET", "/api/v1/uploads/{upload_id}/report"),
    ("GET", "/api/v1/uploads/{upload_or_fits_id}/display.webp"),
    ("GET", "/api/v1/uploads/{upload_or_fits_id}/science.bin"),
    ("POST", "/api/v1/analyses"),
    ("GET", "/api/v1/analyses/{analysis_id}/sources"),
    ("GET", "/api/v1/analyses/{analysis_id}/suggestions"),
    ("POST", "/api/v1/analyses/{analysis_id}/select"),
    ("GET", "/api/v1/projects"),
    ("POST", "/api/v1/projects"),
    ("GET", "/api/v1/projects/{project_id}"),
    ("PATCH", "/api/v1/projects/{project_id}"),
    ("DELETE", "/api/v1/projects/{project_id}"),
    ("GET", "/api/v1/projects/{project_id}/notes"),
    ("POST", "/api/v1/projects/{project_id}/notes"),
    ("PATCH", "/api/v1/projects/{project_id}/notes/{note_id}"),
    ("DELETE", "/api/v1/projects/{project_id}/notes/{note_id}"),
    ("GET", "/api/v1/projects/{project_id}/findings"),
    ("POST", "/api/v1/projects/{project_id}/findings"),
    ("PATCH", "/api/v1/projects/{project_id}/findings/{finding_id}"),
    ("DELETE", "/api/v1/projects/{project_id}/findings/{finding_id}"),
    ("GET", "/api/v1/projects/{project_id}/views"),
    ("POST", "/api/v1/projects/{project_id}/views"),
    ("DELETE", "/api/v1/projects/{project_id}/views/{view_id}"),
    ("GET", "/api/v1/projects/{project_id}/versions"),
    ("POST", "/api/v1/projects/{project_id}/versions"),
    ("POST", "/api/v1/projects/{project_id}/versions/{version}/restore"),
    ("POST", "/api/v1/projects/{project_id}/share"),
    ("DELETE", "/api/v1/projects/{project_id}/share"),
    ("POST", "/api/v1/projects/{project_id}/exports"),
    ("GET", "/api/v1/projects/{project_id}/exports/{export_id}"),
    ("POST", "/api/v1/projects/{project_id}/objects"),
    ("POST", "/api/v1/projects/{project_id}/tracks"),
    ("GET", "/api/v1/objects/{object_id}"),
    ("GET", "/api/v1/objects/{object_id}/associations"),
    ("PATCH", "/api/v1/objects/{object_id}/associations/{epoch}"),
    ("GET", "/api/v1/objects/{object_id}/spectrum"),
    ("GET", "/api/v1/objects/{object_id}/lightcurve"),
    ("POST", "/api/v1/objects/{object_id}/recompute"),
    ("POST", "/api/v1/objects/{object_id}/ephemeris"),
    ("POST", "/api/v1/compare"),
    ("GET", "/api/v1/compare/{compare_id}"),
    ("GET", "/api/v1/compare/{compare_id}/products/{product_name}"),
    ("GET", "/api/v1/archive/search"),
    ("POST", "/api/v1/archive/load"),
    ("GET", "/api/v1/catalogs/overlay"),
    ("GET", "/api/v1/releases"),
    ("GET", "/api/v1/examples"),
    ("GET", "/api/v1/jobs/{job_id}"),
    ("GET", "/api/v1/jobs/{job_id}/events"),
    ("GET", "/api/v1/oembed"),
    ("GET", "/api/v1/render.gif"),
    ("GET", "/embed"),
}


def test_all_mvp_contract_ops_implemented() -> None:
    """Verify every OpenAPI MVP route is accounted for with zero remaining 501 stubs."""
    spec_ops = {
        (method.upper(), path)
        for path, ops in app.openapi()["paths"].items()
        if path != "/health"
        for method in ops
    }
    assert spec_ops == IMPLEMENTED_OPS
