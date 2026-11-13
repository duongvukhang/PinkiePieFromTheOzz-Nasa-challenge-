"""Unit tests for multi-archive NASA data source discovery, epoch grouping, and retrieval."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.archive import ArchiveSearchParams
from app.services.irsa_search import (
    convert_radius_to_deg,
    group_records_into_epochs,
    irsa_search_service,
    parse_date_to_mjd,
    validate_url_security,
)


def test_convert_radius_to_deg():
    assert convert_radius_to_deg(60.0, "arcmin") == 1.0
    assert convert_radius_to_deg(3600.0, "arcsec") == 1.0
    assert convert_radius_to_deg(2.5, "deg") == 2.5
    with pytest.raises(ValueError):
        convert_radius_to_deg(10.0, "miles")


def test_parse_date_to_mjd():
    mjd_2025 = parse_date_to_mjd("2025-01-01")
    assert 60670.0 <= mjd_2025 <= 60680.0
    with pytest.raises(Exception):
        parse_date_to_mjd("not-a-date")


def test_archive_search_date_validation():
    params = ArchiveSearchParams(
        ra_deg=133.795,
        dec_deg=-7.245,
        start_date="2025-12-31",
        end_date="2025-01-01",
    )
    with pytest.raises(ValueError, match="cannot be later than end date"):
        irsa_search_service.search_archive(params)


def test_multi_archive_search_spherex():
    params = ArchiveSearchParams(
        ra_deg=133.79513,
        dec_deg=-7.24515,
        radius=5.0,
        radius_unit="arcmin",
        source="spherex_irsa",
        start_date="2025-01-01",
        end_date="2025-12-31",
        band="D1",
    )
    res = irsa_search_service.search_archive(params)
    assert res.total_found > 0
    assert res.total_epochs > 0
    assert res.epochs[0].epoch_index == 0
    assert res.epochs[0].time_delta_days == 0.0
    assert len(res.epochs) >= 2
    assert res.epochs[1].time_delta_days is not None
    assert res.epochs[1].time_delta_days > 0


def test_multi_archive_search_wise():
    params = ArchiveSearchParams(
        ra_deg=133.79513,
        dec_deg=-7.24515,
        source="wise_irsa",
        band="W1",
    )
    res = irsa_search_service.search_archive(params)
    assert res.total_found > 0
    assert any("WISE" in rec.band for rec in res.records if rec.band)


def test_multi_archive_search_twomass():
    params = ArchiveSearchParams(
        ra_deg=133.79513,
        dec_deg=-7.24515,
        source="twomass_irsa",
        band="J",
    )
    res = irsa_search_service.search_archive(params)
    assert res.total_found > 0
    assert any("2MASS" in rec.band for rec in res.records if rec.band)


def test_api_archive_search_endpoint():
    client = TestClient(app)
    res = client.get(
        "/api/v1/archive/search",
        params={
            "ra": 133.79513,
            "dec": -7.24515,
            "radius": 10.0,
            "radius_unit": "arcmin",
            "source": "spherex_irsa",
            "from": "2025-01-01",
            "to": "2025-12-31",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert "total_epochs" in data
    assert "epochs" in data
    assert data["total_epochs"] > 0
    assert data["epochs"][0]["time_delta_days"] == 0.0


def test_api_archive_load_endpoint():
    client = TestClient(app)
    res = client.post(
        "/api/v1/archive/load",
        json={
            "obs_id": "test_obs_001",
            "source": "spherex_irsa",
            "band": "D1",
            "ra_deg": 133.79513,
            "dec_deg": -7.24515,
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["obs_id"] == "test_obs_001"
    assert "storage_key" in data


def test_validate_url_security_rules():
    assert validate_url_security("https://irsa.ipac.caltech.edu/data/test.fits")
    assert validate_url_security("http://localhost:8000/test.fits")
    assert validate_url_security("http://127.0.0.1:8000/test.fits")
    with pytest.raises(ValueError, match="HTTPS required"):
        validate_url_security("http://irsa.ipac.caltech.edu/data/test.fits")
    with pytest.raises(ValueError, match="not an authorized NASA archive domain"):
        validate_url_security("https://evil.attacker.com/malicious.fits")


def test_api_archive_search_date_validation_400():
    client = TestClient(app)
    res = client.get(
        "/api/v1/archive/search",
        params={
            "ra": 133.79513,
            "dec": -7.24515,
            "from": "2025-12-31",
            "to": "2025-01-01",
        },
    )
    assert res.status_code == 400
    detail = res.json()
    assert "title" in detail
    assert "cannot be later than end date" in str(detail)


def test_api_archive_load_security_validation_400():
    client = TestClient(app)
    res = client.post(
        "/api/v1/archive/load",
        json={
            "obs_id": "test_malicious",
            "access_url": "http://irsa.ipac.caltech.edu/plain_http.fits",
            "source": "spherex_irsa",
        },
    )
    assert res.status_code == 400
    assert "HTTPS required" in str(res.json())


def test_synthetic_demo_tagging():
    params = ArchiveSearchParams(
        ra_deg=133.79513,
        dec_deg=-7.24515,
        source="twomass_irsa",
        band="J",
    )
    res = irsa_search_service.search_archive(params)
    assert res.service_status == "SYNTHETIC_DEMO"
    assert res.is_offline_fallback is True
    assert all(r.is_cached_fallback is True for r in res.records)
    assert all(r.source_origin == "synthetic_demo" for r in res.records)
