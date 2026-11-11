"""Integration test verifying Alembic migrations apply cleanly to a fresh PostgreSQL DB."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app.db import Base, get_database_url

_ALEMBIC_INI = str(Path(__file__).resolve().parents[2] / "backend" / "alembic.ini")


def _resolve_live_pg_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL") or get_database_url()
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        engine.dispose()
        if os.environ.get("REQUIRE_PG") == "1":
            raise
        pytest.skip(f"PostgreSQL not reachable ({exc}); set TEST_DATABASE_URL to run")
    engine.dispose()
    return url


def test_alembic_upgrade_downgrade_and_schema_parity() -> None:
    """Apply Alembic upgrade/downgrade/upgrade on a fresh PostgreSQL database."""
    url = _resolve_live_pg_url()
    cfg = Config(_ALEMBIC_INI)
    cfg.set_main_option("sqlalchemy.url", url)

    command.upgrade(cfg, "head")

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            exts = {
                row[0] for row in conn.execute(text("SELECT extname FROM pg_extension")).fetchall()
            }
            assert {"pgcrypto", "citext"}.issubset(exts)
            assert "q3c" not in exts

            relkind = conn.execute(
                text("SELECT relkind FROM pg_class WHERE relname = 'detections'")
            ).scalar_one()
            assert relkind == "r"  # plain unpartitioned table in MVP

            insp = inspect(conn)
            expected_tables = set(Base.metadata.tables.keys())
            assert len(expected_tables) == 26
            assert "planetx_runs" not in expected_tables
            assert "planetx_candidates" not in expected_tables
            tables = set(insp.get_table_names()) - {"alembic_version"}
            assert tables == expected_tables
            assert "password_hash" in {c["name"] for c in insp.get_columns("users")}

            det_cols = {c["name"] for c in insp.get_columns("detections")}
            assert "healpix9" in det_cols
            assert "healpix4" not in det_cols

            det_indexes = {
                idx["name"]: idx["column_names"] for idx in insp.get_indexes("detections")
            }
            assert det_indexes["detections_ra_dec_idx"] == ["ra", "dec"]

            mc = MigrationContext.configure(
                conn,
                opts={"compare_type": True, "compare_server_default": True},
            )
            assert compare_metadata(mc, Base.metadata) == []

        command.downgrade(cfg, "base")
        with engine.connect() as conn:
            remaining = set(inspect(conn).get_table_names()) - {"alembic_version"}
            assert remaining == set()

        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            restored = set(inspect(conn).get_table_names()) - {"alembic_version"}
            assert restored == expected_tables
    finally:
        engine.dispose()
