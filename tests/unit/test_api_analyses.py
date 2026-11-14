"""Offline TestClient API tests for analyses, source extraction, bbox, and suggestions."""

from __future__ import annotations

import io
from collections.abc import Iterator, Sequence
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import astropy.units as u
import numpy as np
import pytest
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.time import Time
from astropy.wcs import WCS
from fastapi.testclient import TestClient
from photutils.psf import CircularGaussianPRF

from app.api.deps import (
    get_cache,
    get_gaia_provider,
    get_mpc_provider,
    get_spherex_provider,
    get_storage,
    get_store,
    get_wise_provider,
)
from app.main import app
from app.providers.base import CatalogSource, FrameRef, Provider, ReleaseDescriptor
from app.services.cache import MemoryCacheBackend
from app.services.jobs import InMemoryJobStore, compute_params_hash
from app.services.storage import LocalStorageBackend
from app.workers.tasks_fast import UPLOAD_TASK_NAME
from app.workers.tasks_heavy import ANALYSIS_TASK_NAME

_RA0 = 133.79513
_DEC0 = -7.24515
_PIXSCALE = 6.2


def _make_wcs(ra_deg: float, dec_deg: float, nx: int = 64, ny: int = 64) -> WCS:
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra_deg, dec_deg]
    w.wcs.crpix = [(nx + 1) / 2.0, (ny + 1) / 2.0]
    w.wcs.cdelt = [-_PIXSCALE / 3600.0, _PIXSCALE / 3600.0]
    w.wcs.radesys = "ICRS"
    return w


def _add_gaussian(
    img: np.ndarray, x0: float, y0: float, flux: float, sigma_px: float = 0.85
) -> None:
    ny, nx = img.shape
    yy, xx = np.mgrid[0:ny, 0:nx]
    prf = CircularGaussianPRF(fwhm=2.3548200450309493 * sigma_px, flux=flux, x_0=x0, y_0=y0)
    img += prf(xx, yy)


def _render_sky_fits(
    *,
    date_obs: str,
    sky_sources: Sequence[tuple[float, float, float]],
    dither_ra_deg: float = 0.0,
    dither_dec_deg: float = 0.0,
    det_spikes: Sequence[tuple[int, int, float]] = (),
    band: str = "arr6",
    wavelength_um: tuple[float, float] = (4.20, 5.00),
    nx: int = 64,
    ny: int = 64,
    seed: int = 7,
) -> bytes:
    wcs = _make_wcs(_RA0 + dither_ra_deg, _DEC0 + dither_dec_deg, nx=nx, ny=ny)
    rng = np.random.default_rng(seed)
    img = rng.normal(1.0, 0.2, size=(ny, nx)).astype(np.float64)
    for ra_s, dec_s, flux in sky_sources:
        xs, ys = (float(v) for v in wcs.world_to_pixel_values(ra_s, dec_s))
        _add_gaussian(img, xs, ys, flux)
    for px_x, px_y, amp in det_spikes:
        _add_gaussian(img, float(px_x), float(px_y), amp)

    hdr = wcs.to_header()
    hdr["DATE-OBS"] = date_obs
    hdr["EXPTIME"] = 100.0
    hdr["BAND"] = band
    hdr["LAM_MIN"] = wavelength_um[0]
    hdr["LAM_MAX"] = wavelength_um[1]
    hdr["BUNIT"] = "MJy/sr"
    hdr["PSF_FWHM"] = 2.0

    primary = fits.PrimaryHDU()
    for key in ("DATE-OBS", "EXPTIME", "BAND", "LAM_MIN", "LAM_MAX"):
        primary.header[key] = hdr[key]
    hdus = [
        primary,
        fits.ImageHDU(data=img.astype(np.float32), header=hdr, name="IMAGE"),
        fits.ImageHDU(data=np.full((ny, nx), 0.04, dtype=np.float32), name="VARIANCE"),
        fits.ImageHDU(data=np.zeros((ny, nx), dtype=np.int32), name="FLAGS"),
    ]
    buf = io.BytesIO()
    fits.HDUList(hdus).writeto(buf)
    return buf.getvalue()


class _FakeCatalog(Provider):
    kind = {"catalog"}

    def __init__(self, name: str, rows: list[CatalogSource] | None = None) -> None:
        self.name = name
        self.rows = rows or []

    def describe_releases(self) -> list[ReleaseDescriptor]:
        return []

    def cone(
        self, center: SkyCoord, radius: u.Quantity, epoch: Time | None = None
    ) -> list[CatalogSource]:
        del center, radius, epoch
        return list(self.rows)


class _FakeSpherex(Provider):
    name = "spherex_irsa"
    kind = {"images"}

    def __init__(self, frames: list[tuple[FrameRef, bytes]] | None = None) -> None:
        self.frames = frames or []

    def describe_releases(self) -> list[ReleaseDescriptor]:
        return [
            ReleaseDescriptor(
                release_id="spherex_qr_2026-10",
                provider=self.name,
                mission="SPHEREx",
                collection="spherex_qr",
                release_date=date(2026, 10, 1),
            )
        ]

    def search_frames(
        self,
        center: SkyCoord,
        radius: u.Quantity,
        *,
        t0: Time | None = None,
        t1: Time | None = None,
        bands: Sequence[str] | None = None,
        release: str = "latest",
    ) -> list[FrameRef]:
        del center, radius, t0, t1, bands, release
        return [ref for ref, _ in self.frames]

    def fetch(self, ref: FrameRef, cutout: tuple[SkyCoord, u.Quantity] | None = None) -> bytes:
        del cutout
        for r, payload in self.frames:
            if r.obs_id == ref.obs_id:
                return payload
        raise FileNotFoundError(ref.obs_id)


@pytest.fixture()
def analysis_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    store = InMemoryJobStore()
    storage = LocalStorageBackend(tmp_path / "store")
    cache = MemoryCacheBackend()
    gaia = _FakeCatalog("gaia")
    wise = _FakeCatalog("wise")
    mpc = _FakeCatalog("mpc")
    spherex = _FakeSpherex()
    monkeypatch.setenv("CELERY_TASK_ALWAYS_EAGER", "true")

    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_cache] = lambda: cache
    app.dependency_overrides[get_gaia_provider] = lambda: gaia
    app.dependency_overrides[get_wise_provider] = lambda: wise
    app.dependency_overrides[get_mpc_provider] = lambda: mpc
    app.dependency_overrides[get_spherex_provider] = lambda: spherex
    try:
        yield {
            "client": TestClient(app),
            "store": store,
            "storage": storage,
            "gaia": gaia,
            "wise": wise,
            "mpc": mpc,
            "spherex": spherex,
        }
    finally:
        app.dependency_overrides.clear()


def _upload_fits(client: TestClient, name: str, payload: bytes) -> str:
    res = client.post("/api/v1/uploads", files={"file": (name, payload, "application/fits")})
    assert res.status_code == 202, res.text
    assert res.json()["status"] == "succeeded"
    return str(res.json()["fits_file_id"])


def test_analysis_mover_dipole_persistence_bbox_pagination_and_dedup(
    analysis_env: dict[str, Any],
) -> None:
    client: TestClient = analysis_env["client"]
    gaia: _FakeCatalog = analysis_env["gaia"]
    base_wcs = _make_wcs(_RA0, _DEC0)

    # 5 stationary Gaia reference stars + 1 fast-moving IR-only object (+2.5 px over 1 yr)
    ref_px = [(15.0, 15.0), (48.0, 15.0), (15.0, 48.0), (48.0, 48.0), (20.0, 38.0)]
    static_sky: list[tuple[float, float, float]] = []
    for idx, (px, py) in enumerate(ref_px):
        ra_s, dec_s = (float(v) for v in base_wcs.pixel_to_world_values(px, py))
        static_sky.append((ra_s % 360.0, dec_s, 140.0))
        gaia.rows.append(
            CatalogSource(
                provider="gaia",
                catalog="gaiadr3",
                source_id=str(1000 + idx),
                ra_deg=ra_s % 360.0,
                dec_deg=dec_s,
                epoch=Time(2016.0, format="jyear", scale="tdb"),
                mag=14.5,
                mag_band="G",
            )
        )

    mover_ra0, mover_dec0 = (float(v) for v in base_wcs.pixel_to_world_values(30.0, 30.0))
    mover_ra1, mover_dec1 = (float(v) for v in base_wcs.pixel_to_world_values(32.5, 30.0))

    # Dither WCS between epochs while injecting a detector-fixed persistence spike at (12, 12)
    fits_e0 = _render_sky_fits(
        date_obs="2025-06-21T00:00:00.000",
        sky_sources=[*static_sky, (mover_ra0 % 360.0, mover_dec0, 120.0)],
        det_spikes=[(12, 12, 95.0)],
        seed=1,
    )
    fits_e1 = _render_sky_fits(
        date_obs="2026-06-21T00:00:00.000",
        sky_sources=[*static_sky, (mover_ra1 % 360.0, mover_dec1, 120.0)],
        dither_ra_deg=2.0 * _PIXSCALE / 3600.0,
        dither_dec_deg=1.5 * _PIXSCALE / 3600.0,
        det_spikes=[(12, 12, 95.0)],
        seed=2,
    )
    fid0 = _upload_fits(client, "epoch0.fits", fits_e0)
    fid1 = _upload_fits(client, "epoch1.fits", fits_e1)

    res = client.post("/api/v1/analyses", json={"fits_file_ids": [fid0, fid1]})
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["status"] == "succeeded"
    analysis_id = body["analysis_id"]

    # Order-independent deduplication returns the same job_id and analysis_id
    dup = client.post("/api/v1/analyses", json={"fits_file_ids": [fid1, fid0]})
    assert dup.status_code == 202
    assert dup.json()["job_id"] == body["job_id"]
    assert dup.json()["analysis_id"] == analysis_id

    # Sources: full list + unit validation
    src_res = client.get(f"/api/v1/analyses/{analysis_id}/sources")
    assert src_res.status_code == 200
    all_items = src_res.json()["items"]
    assert len(all_items) >= 12
    for det in all_items:
        assert det["sig_ra_mas"] > 0.0
        assert det["sig_dec_mas"] > 0.0
        if not det["is_difference"]:
            assert det["flux_ujy"] > 0.0
            assert det["mag_ab"] is not None

    # BBox filter around mover center excludes corner stars
    box = f"{mover_ra0 - 0.01},{mover_dec0 - 0.01},{mover_ra0 + 0.01},{mover_dec0 + 0.01}"
    box_res = client.get(f"/api/v1/analyses/{analysis_id}/sources", params={"bbox": box})
    assert box_res.status_code == 200
    box_items = box_res.json()["items"]
    assert 1 <= len(box_items) < len(all_items)

    # Pagination
    p1 = client.get(f"/api/v1/analyses/{analysis_id}/sources", params={"limit": 3})
    assert p1.status_code == 200
    p1_json = p1.json()
    assert len(p1_json["items"]) == 3
    assert p1_json["next_cursor"] is not None
    p2 = client.get(
        f"/api/v1/analyses/{analysis_id}/sources",
        params={"limit": 3, "cursor": p1_json["next_cursor"]},
    )
    assert p2.status_code == 200
    ids_1 = {d["id"] for d in p1_json["items"]}
    ids_2 = {d["id"] for d in p2.json()["items"]}
    assert ids_1.isdisjoint(ids_2)

    # Suggestions: mover is suggested with mover reasons and NOT Variable; persistence excluded
    sug_res = client.get(f"/api/v1/analyses/{analysis_id}/suggestions")
    assert sug_res.status_code == 200
    sugs = sug_res.json()["suggestions"]
    assert sugs
    top = sugs[0]
    assert top["score"] >= 0.7
    assert any(
        r in top["reasons"] for r in ("Mover dipole in diff", "High PM candidate", "Red+moving")
    )
    assert "Variable" not in top["reasons"]

    # Ensure persistence spike location at (12, 12) in epoch 0 is not suggested
    spike_ra, spike_dec = (float(v) for v in base_wcs.pixel_to_world_values(12.0, 12.0))
    spike_c = SkyCoord(spike_ra * u.deg, spike_dec * u.deg, frame="icrs")
    for s in sugs:
        sep = spike_c.separation(SkyCoord(s["ra"] * u.deg, s["dec"] * u.deg, frame="icrs")).arcsec
        assert sep > 10.0


def test_analysis_stationary_variable_not_confused_with_mover(
    analysis_env: dict[str, Any],
) -> None:
    client: TestClient = analysis_env["client"]
    gaia: _FakeCatalog = analysis_env["gaia"]
    base_wcs = _make_wcs(_RA0, _DEC0)

    # 5 steady Gaia stars + 1 stationary Mira-like variable brightening by ~2 mag
    static_sky: list[tuple[float, float, float]] = []
    star_px = [(15.0, 15.0), (48.0, 15.0), (15.0, 48.0), (48.0, 48.0), (20.0, 38.0)]
    for idx, (px, py) in enumerate(star_px):
        ra_s, dec_s = (float(v) for v in base_wcs.pixel_to_world_values(px, py))
        static_sky.append((ra_s % 360.0, dec_s, 140.0))
        gaia.rows.append(
            CatalogSource(
                provider="gaia",
                catalog="gaiadr3",
                source_id=str(2000 + idx),
                ra_deg=ra_s % 360.0,
                dec_deg=dec_s,
                epoch=Time(2016.0, format="jyear", scale="tdb"),
                mag=14.5,
                mag_band="G",
            )
        )

    var_ra, var_dec = (float(v) for v in base_wcs.pixel_to_world_values(32.0, 32.0))
    gaia.rows.append(
        CatalogSource(
            provider="gaia",
            catalog="gaiadr3",
            source_id="2999",
            ra_deg=var_ra % 360.0,
            dec_deg=var_dec,
            epoch=Time(2016.0, format="jyear", scale="tdb"),
            mag=14.0,
            mag_band="G",
        )
    )
    fits_e0 = _render_sky_fits(
        date_obs="2025-06-21T00:00:00.000",
        sky_sources=[*static_sky, (var_ra % 360.0, var_dec, 40.0)],
        seed=11,
    )
    fits_e1 = _render_sky_fits(
        date_obs="2025-12-21T00:00:00.000",
        sky_sources=[*static_sky, (var_ra % 360.0, var_dec, 240.0)],
        seed=12,
    )
    fid0 = _upload_fits(client, "var0.fits", fits_e0)
    fid1 = _upload_fits(client, "var1.fits", fits_e1)

    res = client.post("/api/v1/analyses", json={"fits_file_ids": [fid0, fid1]})
    assert res.status_code == 202
    analysis_id = res.json()["analysis_id"]

    sugs = client.get(f"/api/v1/analyses/{analysis_id}/suggestions").json()["suggestions"]
    assert sugs
    var_c = SkyCoord(var_ra * u.deg, var_dec * u.deg, frame="icrs")
    matched = [
        s
        for s in sugs
        if var_c.separation(SkyCoord(s["ra"] * u.deg, s["dec"] * u.deg, frame="icrs")).arcsec < 8.0
    ]
    assert matched
    assert "Variable" in matched[0]["reasons"]
    assert "Mover dipole in diff" not in matched[0]["reasons"]
    assert "High PM candidate" not in matched[0]["reasons"]


def test_analysis_cutout_and_sky_field_modes_with_sso_and_quota(
    analysis_env: dict[str, Any],
) -> None:
    client: TestClient = analysis_env["client"]
    spherex: _FakeSpherex = analysis_env["spherex"]
    mpc: _FakeCatalog = analysis_env["mpc"]

    wcs = _make_wcs(_RA0, _DEC0)
    sso_ra, sso_dec = (float(v) for v in wcs.pixel_to_world_values(32.0, 32.0))
    payload = _render_sky_fits(
        date_obs="2025-06-21T00:00:00.000",
        sky_sources=[(sso_ra % 360.0, sso_dec, 140.0)],
        seed=21,
    )
    ref = FrameRef(
        provider="spherex_irsa",
        release="spherex_qr_2026-10",
        obs_id="obs_sso_1",
        access_url="https://irsa.example/obs_sso_1.fits",
        t_mid=Time("2025-06-21T00:00:00.000", scale="utc").tdb,
        band_id="arr6",
        array_id=6,
        wavelength_um=(4.2, 5.0),
    )
    spherex.frames.append((ref, payload))
    mpc.rows.append(
        CatalogSource(
            provider="mpc",
            catalog="mpc",
            source_id="433 Eros",
            ra_deg=sso_ra % 360.0,
            dec_deg=sso_dec,
            epoch=ref.t_mid,
        )
    )

    # 1. Cutout-based analysis
    cut_res = client.post("/api/v1/cutouts", json={"ra": _RA0, "dec": _DEC0, "fov_arcmin": 10.0})
    assert cut_res.status_code == 202
    cutout_id = cut_res.json()["cutout_id"]

    an_cut = client.post("/api/v1/analyses", json={"cutout_id": cutout_id})
    assert an_cut.status_code == 202
    assert an_cut.json()["status"] == "succeeded"
    aid_cut = an_cut.json()["analysis_id"]
    sugs = client.get(f"/api/v1/analyses/{aid_cut}/suggestions").json()["suggestions"]
    assert any("Known SSO" in s["reasons"] for s in sugs)

    # 2. Sky-field mode: anonymous > 15 arcmin -> 403; authenticated -> 202
    anon_big = client.post("/api/v1/analyses", json={"ra": _RA0, "dec": _DEC0, "fov_arcmin": 30.0})
    assert anon_big.status_code == 403
    assert anon_big.headers["content-type"].startswith("application/problem+json")

    auth_big = client.post(
        "/api/v1/analyses",
        json={"ra": _RA0, "dec": _DEC0, "fov_arcmin": 30.0},
        headers={"Authorization": f"Bearer {uuid4()}"},
    )
    assert auth_big.status_code == 202
    assert auth_big.json()["status"] == "succeeded"


def test_analysis_validation_and_error_states(analysis_env: dict[str, Any]) -> None:
    client: TestClient = analysis_env["client"]
    store: InMemoryJobStore = analysis_env["store"]

    # 422 validation errors
    for payload in (
        {},
        {"fits_file_ids": []},
        {"ra": _RA0},
        {"cutout_id": str(uuid4()), "ra": _RA0, "dec": _DEC0},
    ):
        r = client.post("/api/v1/analyses", json=payload)
        assert r.status_code == 422, r.text
        assert r.headers["content-type"].startswith("application/problem+json")

    # 404 missing resources
    missing = uuid4()
    r404 = client.post("/api/v1/analyses", json={"fits_file_ids": [str(missing)]})
    assert r404.status_code == 404
    assert client.post("/api/v1/analyses", json={"cutout_id": str(missing)}).status_code == 404
    assert client.get(f"/api/v1/analyses/{missing}/sources").status_code == 404
    assert client.get(f"/api/v1/analyses/{missing}/suggestions").status_code == 404

    # 409 in-progress upload / analysis, and 502 failed analysis
    pending_fid = uuid4()
    store.create_or_get_job(
        task=UPLOAD_TASK_NAME,
        queue="fast",
        params={"upload_id": str(uuid4()), "fits_file_id": str(pending_fid)},
        params_hash=compute_params_hash(UPLOAD_TASK_NAME, {"k": "pending"}),
    )
    assert (
        client.post("/api/v1/analyses", json={"fits_file_ids": [str(pending_fid)]}).status_code
        == 409
    )

    aid_run = uuid4()
    store.create_or_get_job(
        task=ANALYSIS_TASK_NAME,
        queue="heavy",
        params={"analysis_id": str(aid_run)},
        params_hash=compute_params_hash(ANALYSIS_TASK_NAME, {"k": "running"}),
    )
    assert client.get(f"/api/v1/analyses/{aid_run}/sources").status_code == 409
    assert client.get(f"/api/v1/analyses/{aid_run}/suggestions").status_code == 409

    aid_fail = uuid4()
    fjob, _ = store.create_or_get_job(
        task=ANALYSIS_TASK_NAME,
        queue="heavy",
        params={"analysis_id": str(aid_fail)},
        params_hash=compute_params_hash(ANALYSIS_TASK_NAME, {"k": "failed"}),
    )
    store.update_job(fjob.id, status="failed", error={"detail": "Corrupt frame"})
    assert client.get(f"/api/v1/analyses/{aid_fail}/sources").status_code == 502
    assert client.get(f"/api/v1/analyses/{aid_fail}/suggestions").status_code == 502

    # 422 invalid bbox
    aid_ok = uuid4()
    ok_job, _ = store.create_or_get_job(
        task=ANALYSIS_TASK_NAME,
        queue="heavy",
        params={"analysis_id": str(aid_ok)},
        params_hash=compute_params_hash(ANALYSIS_TASK_NAME, {"k": "ok"}),
    )
    store.update_job(
        ok_job.id,
        status="succeeded",
        progress=1.0,
        outputs={"analysis_id": str(aid_ok), "detection_ids": [], "suggestions": []},
    )
    assert client.get(f"/api/v1/analyses/{aid_ok}/sources?bbox=bad").status_code == 422
    assert client.get(f"/api/v1/analyses/{aid_ok}/sources?bbox=10,20,30,10").status_code == 422


def test_deduplicated_analysis_runs_in_eager_mode(analysis_env: dict[str, Any]) -> None:
    client: TestClient = analysis_env["client"]
    store: InMemoryJobStore = analysis_env["store"]

    aid = uuid4()
    norm_params = {"mode": "sky_field", "ra": 10.0, "dec": 20.0, "fov_arcmin": 10.0, "release": "latest"}
    job_params = {**norm_params, "analysis_id": str(aid)}
    job, _ = store.create_or_get_job(
        task=ANALYSIS_TASK_NAME,
        queue="heavy",
        params=job_params,
        params_hash=compute_params_hash(ANALYSIS_TASK_NAME, norm_params, "latest"),
    )
    assert job.status == "queued"

    res = client.post("/api/v1/analyses", json={"ra": 10.0, "dec": 20.0, "fov_arcmin": 10.0, "release": "latest"})
    assert res.status_code == 202
    body = res.json()
    assert body["job_id"] == str(job.id)
    assert body["status"] == "succeeded"

    latest = store.get_job(job.id)
    assert latest is not None
    assert latest.status == "succeeded"


def test_analysis_failure_marks_job_failed(
    analysis_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    client: TestClient = analysis_env["client"]
    store: InMemoryJobStore = analysis_env["store"]

    import app.api.v1.objects as objects_mod

    def fail_execute(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Simulated analysis pipeline failure")

    monkeypatch.setattr(objects_mod, "execute_run_analysis", fail_execute)

    res = client.post("/api/v1/analyses", json={"ra": 12.0, "dec": -15.0, "fov_arcmin": 5.0})
    assert res.status_code == 202
    body = res.json()
    assert body["status"] == "failed"

    latest = store.get_job(UUID(body["job_id"]))
    assert latest is not None
    assert latest.status == "failed"
    assert latest.error is not None
    assert "Simulated analysis pipeline failure" in str(latest.error)

