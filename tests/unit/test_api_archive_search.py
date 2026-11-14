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
    assert res.service_status in ("LIVE_ARCHIVE", "SYNTHETIC_DEMO")
    assert res.epochs[0].epoch_index == 0
    assert res.epochs[0].time_delta_days == 0.0
    assert len(res.epochs) >= 2
    assert res.epochs[1].time_delta_days is not None
    assert res.epochs[1].time_delta_days > 0


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
            "mjd_obs": 60555.25,
            "fov_arcmin": 5.0,
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["obs_id"] == "test_obs_001"
    assert "storage_key" in data
    assert data["mjd_tdb_mid"] == 60555.25
    first_fid = data["fits_file_id"]

    # Re-loading the same archive product must deduplicate and reuse the fits_file_id
    res2 = client.post(
        "/api/v1/archive/load",
        json={
            "obs_id": "test_obs_001",
            "source": "spherex_irsa",
            "band": "D1",
            "ra_deg": 133.79513,
            "dec_deg": -7.24515,
            "mjd_obs": 60555.25,
        },
    )
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["fits_file_id"] == first_fid
    assert data2["status"] == "already_loaded"

    # Listed uploads must have distinct epoch label and matching mjd
    res3 = client.get("/api/v1/uploads")
    assert res3.status_code == 200
    uploads = res3.json()
    matched = [u for u in uploads if u["fits_file_id"] == first_fid]
    assert len(matched) == 1
    assert matched[0]["epoch"] == "Archive test_obs_001"
    assert matched[0]["mjd_tdb_mid"] == 60555.25


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
        ra_deg=10.0,
        dec_deg=10.0,
        source="spherex_irsa",
        start_date="2020-01-01",
        end_date="2020-06-01",
        band="D1",
    )
    res = irsa_search_service.search_archive(params)
    assert res.service_status == "SYNTHETIC_DEMO"
    assert res.is_offline_fallback is True
    assert all(r.is_cached_fallback is True for r in res.records)
    assert all(r.source_origin == "synthetic_demo" for r in res.records)


def test_search_spherex_tap_parsing_and_fallback(monkeypatch: pytest.MonkeyPatch):
    """Test TAP ObsCore VOTable parsing, column extraction, band/time filtering, and fallback in _search_spherex."""
    from pathlib import Path
    import httpx
    from app.services.irsa_search import IRSASearchService
    from app.services.cache import get_cache_backend

    get_cache_backend().delete("test_mock_cache")

    xml_path = Path(__file__).resolve().parents[1] / "fixtures" / "cassettes" / "spherex_sia_wise0855.xml"
    content = xml_path.read_bytes()

    def mock_handler_ok(request: httpx.Request) -> httpx.Response:
        body = request.read().decode("utf-8")
        assert "obs_collection" in body or "CONTAINS" in body
        return httpx.Response(200, content=content)

    service = IRSASearchService()
    service._client = httpx.Client(transport=httpx.MockTransport(mock_handler_ok))

    # 1. Matching D5 band within MJD range
    recs, status, notes = service._search_spherex(133.795, -7.245, 0.1, 60800.0, 61100.0, "D5", 10)
    assert status == "LIVE_ARCHIVE"
    assert len(recs) == 1
    assert recs[0].obs_id == "sphx_l2_2025_172_arr5_001"
    assert recs[0].detector == "D5"
    assert recs[0].mjd_obs == pytest.approx(60850.409, abs=1e-3)
    assert recs[0].wavelength_range_um == [3.82, 4.42]
    assert recs[0].is_cached_fallback is False

    # 2. Band filter exclusion fallback
    recs_d1, _, _ = service._search_spherex(133.795, -7.245, 0.1, 60800.0, 61100.0, "D1", 10)
    assert len(recs_d1) > 0
    assert recs_d1[0].is_cached_fallback is True

    # 3. HTTP error fallback to synthetic demo
    def mock_handler_err(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"Server error")

    service_err = IRSASearchService()
    service_err._client = httpx.Client(transport=httpx.MockTransport(mock_handler_err))
    recs_err, status_err, notes_err = service_err._search_spherex(12.34, -56.78, 0.05, 60800.0, 61100.0, "D5", 10)
    assert status_err == "SYNTHETIC_DEMO"
    assert len(recs_err) > 0
    assert any("500" in n for n in notes_err)


def test_load_archive_product_failure_raises_502(tmp_path: Path):
    from app.api.deps import APIProblemError
    from app.schemas.archive import ArchiveLoadRequest
    from app.services.jobs import InMemoryJobStore
    from app.services.storage import LocalStorageBackend

    storage = LocalStorageBackend(tmp_path / "storage")
    store = InMemoryJobStore()
    req = ArchiveLoadRequest(
        obs_id="demo_failed_product",
        access_url="synthetic://demo/not_downloadable.fits",
        source="spherex_irsa",
        ra_deg=133.795,
        dec_deg=-7.245,
    )
    with pytest.raises(APIProblemError) as exc_info:
        irsa_search_service.load_archive_product(req, storage, store)
    assert exc_info.value.status_code == 502
    assert "Archive Product Unavailable" in exc_info.value.title


def test_search_spherex_cone_radius_gt_5_and_caching():
    """Verify cone radius > 5 arcmin finds sample fits and caches result."""
    # North Ecliptic Pole region (near sample FITS at RA 276.26, Dec 64.83)
    params = ArchiveSearchParams(
        ra_deg=276.3,
        dec_deg=64.85,
        radius=15.0,  # 15 arcmin > 5 arcmin
        radius_unit="arcmin",
        source="spherex_irsa",
        band="D1",
    )
    res = irsa_search_service.search_archive(params)
    assert res.total_found > 0
    assert res.total_epochs > 0

    # Second call must retrieve from cache
    res_cached = irsa_search_service.search_archive(params)
    assert res_cached.total_found == res.total_found
    assert any("cache" in n.lower() for n in res_cached.notes)


def test_group_records_dominant_band():
    """Verify group_records_into_epochs sets selected_product_id to the dominant band across epochs."""
    from app.services.irsa_search import _make_archive_rec

    # Epoch 1 has D1 and D4; Epoch 2 has D4; dominant band is D4
    rec1_d1 = _make_archive_rec("e1_d1", 10.0, 10.0, 60000.0, "Band 1", None, "test", detector="D1")
    rec1_d4 = _make_archive_rec("e1_d4", 10.0, 10.0, 60000.0, "Band 4", None, "test", detector="D4")
    rec2_d4 = _make_archive_rec("e2_d4", 10.0, 10.0, 60180.0, "Band 4", None, "test", detector="D4")

    # Set observation_group_id so epochs are cleanly separated
    rec1_d1.observation_group_id = "visit_1"
    rec1_d4.observation_group_id = "visit_1"
    rec2_d4.observation_group_id = "visit_2"

    epochs = group_records_into_epochs([rec1_d1, rec1_d4, rec2_d4], "test")
    assert len(epochs) == 2
    # In epoch 1, even though D1 was first, D4 is dominant across epochs so selected_product_id is e1_d4
    assert epochs[0].selected_product_id == "e1_d4"
    assert epochs[1].selected_product_id == "e2_d4"


