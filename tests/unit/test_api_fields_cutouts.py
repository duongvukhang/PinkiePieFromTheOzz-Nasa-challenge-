"""Offline TestClient API tests for fields, cutouts, overlays, releases, examples, and jobs."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import astropy.units as u
import numpy as np
import pytest
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.time import Time
from astropy.wcs import WCS
from fastapi.testclient import TestClient

from app.api.deps import (
    get_cache,
    get_gaia_provider,
    get_simbad_provider,
    get_spherex_provider,
    get_storage,
    get_store,
    get_wise_provider,
    paginate_items,
)
from app.main import app
from app.providers.base import (
    CatalogSource,
    FrameRef,
    Provider,
    ProviderQueryError,
    ProviderRateLimitError,
    ProviderUnavailableError,
    ReleaseDescriptor,
)
from app.services.cache import MemoryCacheBackend
from app.services.fields import resolve_target_name
from app.services.jobs import InMemoryJobStore
from app.services.storage import LocalStorageBackend
from app.workers.celery_app import celery_app


def _make_fits_bytes(
    ra_deg: float = 133.79513, dec_deg: float = -7.24515, val: float = 4.0
) -> bytes:
    nx, ny = 16, 16
    w = WCS(naxis=2)
    w.wcs.crpix = [8.5, 8.5]
    w.wcs.cdelt = [-6.2 / 3600.0, 6.2 / 3600.0]
    w.wcs.crval = [ra_deg, dec_deg]
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    primary = fits.PrimaryHDU()
    primary.header["DATE-OBS"] = "2025-06-21T09:50:24.000"
    primary.header["EXPTIME"] = 100.0
    img_hdr = w.to_header()
    img_hdr["DATE-OBS"] = "2025-06-21T09:50:24.000"
    img_hdr["BUNIT"] = "MJy/sr"
    data = np.full((ny, nx), val, dtype=np.float32)
    data[8, 8] = val * 10.0
    var = np.full((ny, nx), 0.1, dtype=np.float32)
    flags = np.zeros((ny, nx), dtype=np.int32)
    flags[0, 0] = 1
    buf = io.BytesIO()
    fits.HDUList(
        [
            primary,
            fits.ImageHDU(data=data, header=img_hdr, name="IMAGE"),
            fits.ImageHDU(data=var, name="VARIANCE"),
            fits.ImageHDU(data=flags, name="FLAGS"),
        ]
    ).writeto(buf)
    return buf.getvalue()


class _FakeSpherexProvider(Provider):
    name = "spherex_irsa"
    kind = {"images"}

    def __init__(self) -> None:
        self.search_calls = 0
        self.fetch_calls = 0
        self.release_calls = 0
        self.fail_with: Exception | None = None
        self.profile = type(
            "Profile",
            (),
            {
                "collection_id": "spherex_qr_v1",
                "provider": "spherex_irsa",
                "mission": "SPHEREx",
                "profile_name": "spherex_qr_v1",
            },
        )()

    def describe_releases(self) -> list[ReleaseDescriptor]:
        self.release_calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        return [
            ReleaseDescriptor(
                release_id="spherex_qr_2026-10",
                provider=self.name,
                mission="SPHEREx",
                collection="spherex_qr",
                version_label="QR1",
                release_date=date(2026, 10, 1),
                n_frames=1200,
                profile_name="spherex_qr_v1",
            )
        ]

    def search_frames(
        self,
        c: SkyCoord,
        radius: u.Quantity,
        t0: Time | None = None,
        t1: Time | None = None,
        bands: list[str] | None = None,
        release: str | None = None,
    ) -> list[FrameRef]:
        del c, radius, t0, t1
        self.search_calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        rel = "spherex_qr_2026-10" if release in (None, "", "latest") else release
        refs = [
            FrameRef(
                provider=self.name,
                release=rel,
                obs_id="obs_s1_a6",
                access_url="https://irsa.example/obs_s1_a6.fits",
                t_mid=Time(60850.41, format="mjd", scale="tdb"),
                band_id="arr6",
                array_id=6,
                wavelength_um=(4.42, 5.00),
                extra={"flagged_fraction": 0.02},
            ),
            FrameRef(
                provider=self.name,
                release=rel,
                obs_id="obs_s2_a5",
                access_url="https://irsa.example/obs_s2_a5.fits",
                t_mid=Time(61031.77, format="mjd", scale="tdb"),
                band_id="arr5",
                array_id=5,
                wavelength_um=(3.82, 4.42),
                extra={"flagged_fraction": 0.04},
            ),
        ]
        if bands:
            low = {b.lower() for b in bands}
            refs = [r for r in refs if r.band_id and r.band_id.lower() in low]
        return refs

    def fetch(self, ref: FrameRef, cutout: tuple[SkyCoord, u.Quantity] | None = None) -> bytes:
        del ref, cutout
        self.fetch_calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        return _make_fits_bytes()

    def flags_summary(self, hdul: fits.HDUList) -> dict[str, Any]:
        del hdul
        return {"flagged_fraction": 1.0 / 256.0}


class _FakeSimbadProvider(Provider):
    name = "simbad"
    kind = {"catalog"}

    def __init__(self) -> None:
        self.calls = 0
        self.fail_with: Exception | None = None

    def describe_releases(self) -> list[ReleaseDescriptor]:
        return []

    def lookup_object(self, target: str, **kwargs: Any) -> CatalogSource | None:
        del kwargs
        self.calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        if target.lower() in ("wise 0855", "sirius"):
            return CatalogSource(
                provider=self.name,
                catalog="simbad",
                source_id="Sirius" if target.lower() == "sirius" else "WISE J085510.83-071442.5",
                ra_deg=101.287,
                dec_deg=-16.716,
                epoch=Time(2000.0, format="jyear", scale="tdb"),
                pmra_masyr=-546.0,
                pmdec_masyr=-1223.0,
            )
        return None


class _FakeCatalogProvider(Provider):
    kind = {"catalog"}

    def __init__(self, name: str, catalog: str) -> None:
        self.name = name
        self.catalog = catalog
        self.calls = 0
        self.fail_with: Exception | None = None

    def describe_releases(self) -> list[ReleaseDescriptor]:
        return []

    def cone(
        self, c: SkyCoord, radius: u.Quantity, epoch: Time | None = None
    ) -> list[CatalogSource]:
        del c, radius
        self.calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        t = (epoch or Time.now()).tdb
        return [
            CatalogSource(
                provider=self.name,
                catalog=self.catalog,
                source_id=f"{self.catalog}_1",
                ra_deg=133.80,
                dec_deg=-7.20,
                epoch=t,
                pmra_masyr=-5.0,
                pmdec_masyr=1.5,
                mag=16.0,
                mag_band="G" if self.catalog == "gaiadr3" else "W2",
            )
        ]


@pytest.fixture()
def api_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    store = InMemoryJobStore()
    cache = MemoryCacheBackend()
    storage = LocalStorageBackend(tmp_path / "store")
    spherex = _FakeSpherexProvider()
    simbad = _FakeSimbadProvider()
    gaia = _FakeCatalogProvider("gaia", "gaiadr3")
    wise = _FakeCatalogProvider("wise", "catwise_2020")

    monkeypatch.setenv("CELERY_TASK_ALWAYS_EAGER", "true")

    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_cache] = lambda: cache
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_spherex_provider] = lambda: spherex
    app.dependency_overrides[get_simbad_provider] = lambda: simbad
    app.dependency_overrides[get_gaia_provider] = lambda: gaia
    app.dependency_overrides[get_wise_provider] = lambda: wise
    try:
        yield {
            "client": TestClient(app),
            "store": store,
            "cache": cache,
            "storage": storage,
            "spherex": spherex,
            "simbad": simbad,
            "gaia": gaia,
            "wise": wise,
        }
    finally:
        app.dependency_overrides.clear()


def test_resolve_endpoint_simbad_cache_sesame_and_errors(api_env: dict[str, Any]) -> None:
    client: TestClient = api_env["client"]
    simbad: _FakeSimbadProvider = api_env["simbad"]
    cache: MemoryCacheBackend = api_env["cache"]

    # Canonical benchmark target resolves from reference_values.json without calling SIMBAD
    r1 = client.get("/api/v1/resolve", params={"name": "WISE 0855"})
    assert r1.status_code == 200
    body = r1.json()
    assert body["resolver"] == "reference"
    assert body["ra_deg"] == pytest.approx(133.79513, abs=1e-4)
    assert body["pmra_masyr"] == pytest.approx(-8123.7, abs=1e-2)

    r2 = client.get("/api/v1/resolve", params={"name": "WISE 0855"})
    assert r2.status_code == 200
    assert simbad.calls == 0  # served from cache / reference

    # Arbitrary sky target resolves via SIMBAD and caches result
    r_sim = client.get("/api/v1/resolve", params={"name": "Sirius"})
    assert r_sim.status_code == 200
    body_sim = r_sim.json()
    assert body_sim["resolver"] == "simbad"
    assert body_sim["ra_deg"] == pytest.approx(101.287)
    assert simbad.calls == 1

    r_sim2 = client.get("/api/v1/resolve", params={"name": "Sirius"})
    assert r_sim2.status_code == 200
    assert simbad.calls == 1  # served from cache

    # Direct service check for Sesame fallback when SIMBAD misses
    sesame_res = resolve_target_name(
        "M31",
        simbad=simbad,
        cache=cache,
        sesame_lookup=lambda _: SkyCoord(10.6847 * u.deg, 41.2687 * u.deg, frame="icrs"),
    )
    assert sesame_res is not None
    assert sesame_res.resolver == "sesame"

    # Upstream SIMBAD error (e.g. 404 / ProviderQueryError) gracefully falls through to Sesame / 404
    simbad.fail_with = ProviderQueryError("HTTP 404: 404 Client Error: Not Found")
    r404 = client.get("/api/v1/resolve", params={"name": "NotARealTargetXYZ999"})
    assert r404.status_code == 404
    assert r404.headers["content-type"].startswith("application/problem+json")
    assert r404.json()["status"] == 404

    # Validation error returns 422 RFC 9457 problem+json
    r422 = client.get("/api/v1/resolve", params={"name": ""})
    assert r422.status_code == 422
    assert r422.headers["content-type"].startswith("application/problem+json")


def test_resolve_mandatory_benchmark_targets_and_aliases(api_env: dict[str, Any]) -> None:
    client: TestClient = api_env["client"]
    targets_to_test = [
        ("WISE 0855", 133.79513, -7.24515),
        ("wise_0855", 133.79513, -7.24515),
        ("Barnard's Star", 269.44850, 4.73942),
        ("barnard", 269.44850, 4.73942),
        ("Epsilon Indi B", 331.04384, -56.78271),
        ("RW Aurigae", 76.95608, 30.40120),
        ("R Draconis", 248.16738, 66.75501),
    ]
    for name, exp_ra, exp_dec in targets_to_test:
        res = client.get("/api/v1/resolve", params={"name": name})
        assert res.status_code == 200, f"Failed to resolve {name}"
        data = res.json()
        assert data["resolver"] == "reference"
        assert data["ra_deg"] == pytest.approx(exp_ra, abs=1e-3)
        assert data["dec_deg"] == pytest.approx(exp_dec, abs=1e-3)


def test_fields_frames_grouping_caching_validation_and_provider_errors(
    api_env: dict[str, Any],
) -> None:
    client: TestClient = api_env["client"]
    spherex: _FakeSpherexProvider = api_env["spherex"]

    res = client.get(
        "/api/v1/fields/frames",
        params={"ra": 133.79513, "dec": -7.24515, "fov": 10, "bands": "arr5,arr6"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["field"]["healpix9"] > 0
    assert data["release"] == "spherex_qr_2026-10"
    assert [e["epoch"] for e in data["epochs"]] == ["S1", "S2"]
    assert data["epochs"][0]["arrays"] == [6]
    assert data["epochs"][1]["arrays"] == [5]

    # Second identical call hits cache
    res2 = client.get(
        "/api/v1/fields/frames",
        params={"ra": 133.79513, "dec": -7.24515, "fov": 10, "bands": "arr5,arr6"},
    )
    assert res2.status_code == 200
    assert spherex.search_calls == 1

    # Anonymous FOV > 15 arcmin blocked with 403 problem+json; authenticated allowed
    r_fov = client.get("/api/v1/fields/frames", params={"ra": 133.795, "dec": -7.245, "fov": 30})
    assert r_fov.status_code == 403
    assert r_fov.headers["content-type"].startswith("application/problem+json")
    assert "science_impact" in r_fov.json()

    r_auth = client.get(
        "/api/v1/fields/frames",
        params={"ra": 133.795, "dec": -7.245, "fov": 30},
        headers={"Authorization": f"Bearer {uuid4()}"},
    )
    assert r_auth.status_code == 200

    # Invalid coordinate & time ordering errors
    assert client.get("/api/v1/fields/frames", params={"ra": 400.0, "dec": 0.0}).status_code == 422
    r_time = client.get(
        "/api/v1/fields/frames",
        params={
            "ra": 133.795,
            "dec": -7.245,
            "from": "2026-01-01T00:00:00Z",
            "to": "2025-01-01T00:00:00Z",
        },
    )
    assert r_time.status_code == 400
    assert r_time.headers["content-type"].startswith("application/problem+json")

    # Upstream archive provider error returns 200 with warnings instead of crashing page
    spherex.fail_with = ProviderRateLimitError("throttled", retry_after_s=12.0)
    r429 = client.get("/api/v1/fields/frames", params={"ra": 10.0, "dec": 20.0})
    assert r429.status_code == 200
    assert r429.json()["epochs"] == []
    assert any("Upstream provider query failed" in w for w in r429.json()["warnings"])

    spherex.fail_with = ProviderUnavailableError("irsa down")
    r503 = client.get("/api/v1/fields/frames", params={"ra": 11.0, "dec": 21.0})
    assert r503.status_code == 200
    assert r503.json()["epochs"] == []
    assert any("Upstream provider query failed" in w for w in r503.json()["warnings"])


def test_cutouts_async_job_sse_artifacts_and_deduplication(api_env: dict[str, Any]) -> None:
    client: TestClient = api_env["client"]
    spherex: _FakeSpherexProvider = api_env["spherex"]
    store: InMemoryJobStore = api_env["store"]

    req = {"ra": 133.79513, "dec": -7.24515, "fov_arcmin": 10.0, "bands": ["arr5", "arr6"]}
    post_res = client.post("/api/v1/cutouts", json=req)
    assert post_res.status_code == 202
    accepted = post_res.json()
    job_id = accepted["job_id"]
    cutout_id = accepted["cutout_id"]
    assert accepted["status"] == "succeeded"

    # Job status endpoint
    job_res = client.get(f"/api/v1/jobs/{job_id}")
    assert job_res.status_code == 200
    assert job_res.json()["progress"] == pytest.approx(1.0)

    # SSE job events endpoint
    sse_res = client.get(f"/api/v1/jobs/{job_id}/events")
    assert sse_res.status_code == 200
    assert sse_res.headers["content-type"].startswith("text/event-stream")
    assert "event: progress" in sse_res.text
    first_data_line = next(
        line[len("data: ") :] for line in sse_res.text.splitlines() if line.startswith("data: ")
    )
    assert json.loads(first_data_line)["status"] == "succeeded"

    # Cutout metadata + artifact endpoints
    cut_res = client.get(f"/api/v1/cutouts/{cutout_id}")
    assert cut_res.status_code == 200
    cut_body = cut_res.json()
    assert len(cut_body["frames"]) == 2
    f0 = cut_body["frames"][0]
    assert f0["epoch"] == "S1"

    disp = client.get(f0["display_url"])
    assert disp.status_code == 200
    assert disp.headers["content-type"] == "image/webp"
    assert disp.content[:4] == b"RIFF" and disp.content[8:12] == b"WEBP"

    sci = client.get(f0["science_url"])
    assert sci.status_code == 200
    assert sci.headers["X-Shape"] == "16,16"
    arr = np.frombuffer(sci.content, dtype="<f4").reshape((16, 16))
    assert arr[8, 8] == pytest.approx(40.0)

    raw_fits = client.get(f0["fits_url"])
    assert raw_fits.status_code == 200
    assert raw_fits.content[:6] == b"SIMPLE"

    # Idempotent deduplication returns existing job_id and cutout_id
    dup_res = client.post("/api/v1/cutouts", json=req)
    assert dup_res.status_code == 202
    assert dup_res.json()["job_id"] == job_id
    assert dup_res.json()["cutout_id"] == cutout_id
    assert spherex.fetch_calls == 2

    # In-progress cutout returns 409; missing cutout/job returns 404
    pending_cid = uuid4()
    pending_job, _ = store.create_or_get_job(
        task="app.workers.tasks_fast.build_cutouts",
        queue="fast",
        params={"cutout_id": str(pending_cid)},
        params_hash=b"pending-hash-1234",
    )
    assert pending_job.status == "queued"
    assert client.get(f"/api/v1/cutouts/{pending_cid}").status_code == 409
    assert client.get(f"/api/v1/cutouts/{uuid4()}").status_code == 404
    assert client.get(f"/api/v1/jobs/{uuid4()}").status_code == 404


def test_catalog_overlay_releases_examples_and_pagination(api_env: dict[str, Any]) -> None:
    client: TestClient = api_env["client"]
    gaia: _FakeCatalogProvider = api_env["gaia"]
    store: InMemoryJobStore = api_env["store"]

    # Gaia and CatWISE catalog overlays
    g_res = client.get(
        "/api/v1/catalogs/overlay",
        params={"cat": "gaia", "ra": 133.795, "dec": -7.245, "epoch": "2025-06-21T00:00:00Z"},
    )
    assert g_res.status_code == 200
    assert g_res.json()["sources"][0]["catalog"] == "gaiadr3"

    g_res2 = client.get(
        "/api/v1/catalogs/overlay",
        params={"cat": "gaia", "ra": 133.795, "dec": -7.245, "epoch": "2025-06-21T00:00:00Z"},
    )
    assert g_res2.status_code == 200
    assert gaia.calls == 1

    w_res = client.get(
        "/api/v1/catalogs/overlay",
        params={"cat": "catwise", "ra": 133.795, "dec": -7.245},
    )
    assert w_res.status_code == 200
    assert w_res.json()["sources"][0]["catalog"] == "catwise_2020"

    # Catalog provider error returns 200 with warnings instead of failing page
    gaia.fail_with = ProviderQueryError("Upstream TAP 404")
    g_err = client.get(
        "/api/v1/catalogs/overlay",
        params={"cat": "gaia", "ra": 10.0, "dec": 20.0},
    )
    assert g_err.status_code == 200
    assert g_err.json()["sources"] == []
    assert any("Upstream catalog provider query failed" in w for w in g_err.json()["warnings"])

    # Releases (provider fallback + seeded store)
    rel_res = client.get("/api/v1/releases")
    assert rel_res.status_code == 200
    assert rel_res.json()[0]["id"] == "spherex_qr_2026-10"

    # Releases endpoint falls back to profile when provider describe_releases fails
    spherex: _FakeSpherexProvider = api_env["spherex"]
    cache: MemoryCacheBackend = api_env["cache"]
    cache.delete("releases:spherex_irsa")
    spherex.fail_with = ProviderUnavailableError("irsa down")
    rel_fallback = client.get("/api/v1/releases")
    assert rel_fallback.status_code == 200
    assert rel_fallback.json()[0]["id"] == "spherex_qr_v1"

    # Examples returns the 5 mandatory benchmark targets
    ex_res = client.get("/api/v1/examples")
    assert ex_res.status_code == 200
    ids = [t["id"] for t in ex_res.json()["targets"]]
    assert ids == ["barnards_star", "eps_indi_b", "wise_0855", "rw_aur", "r_dra"]

    # Pagination helper sanity check
    p1, c1 = paginate_items([1, 2, 3, 4, 5], None, 2)
    assert p1 == [1, 2] and c1 is not None
    p2, c2 = paginate_items([1, 2, 3, 4, 5], c1, 2)
    assert p2 == [3, 4] and c2 is not None
    p3, c3 = paginate_items([1, 2, 3, 4, 5], c2, 2)
    assert p3 == [5] and c3 is None

    # Celery route check
    assert celery_app.conf.task_routes["app.workers.tasks_fast.*"] == {"queue": "fast"}
    assert store.list_releases() == []
    assert datetime.now(UTC) is not None


def test_cutouts_synthetic_fallback_and_examples_guard(api_env: dict[str, Any]) -> None:
    client: TestClient = api_env["client"]
    spherex: _FakeSpherexProvider = api_env["spherex"]

    # 1. Provider failure -> fallback generates synthetic sequence with 3 playable frames
    spherex.fail_with = ProviderUnavailableError("Simulated IRSA outage")
    req = {"ra": 200.0, "dec": 10.0, "fov_arcmin": 10.0, "bands": ["all"]}
    post_res = client.post("/api/v1/cutouts", json=req)
    assert post_res.status_code == 202
    cutout_id = post_res.json()["cutout_id"]

    cut_res = client.get(f"/api/v1/cutouts/{cutout_id}")
    assert cut_res.status_code == 200
    cut_body = cut_res.json()
    assert len(cut_body["frames"]) == 3
    assert any("synthetic" in w.lower() for w in cut_body["warnings"])

    # First synthetic frame has playable display and science arrays
    f0 = cut_body["frames"][0]
    disp = client.get(f0["display_url"])
    assert disp.status_code == 200
    assert disp.content[:4] == b"RIFF"

    # 2. list_examples_service with nonexistent file returns empty list, not 500
    from app.services.fields import list_examples_service

    res = list_examples_service(reference_path=Path("/tmp/nonexistent_reference_file.json"))
    assert res.targets == []
