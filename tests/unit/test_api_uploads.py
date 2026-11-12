"""Offline TestClient API tests for FITS upload staging, validation, security, and reports."""

from __future__ import annotations

import gzip
import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import numpy as np
import pytest
from astropy.io import fits
from astropy.time import Time
from fastapi.testclient import TestClient

from app.api.deps import get_storage, get_store
from app.main import app
from app.services.jobs import InMemoryJobStore
from app.services.storage import LocalStorageBackend
from app.workers.tasks_fast import UPLOAD_TASK_NAME


def _make_fits(
    *,
    nx: int = 80,
    ny: int = 80,
    ra_deg: float = 133.79513,
    dec_deg: float = -7.24515,
    pixscale_arcsec: float = 6.2,
    rot_deg: float = 0.0,
    date_obs: str | None = "2025-06-21T09:50:24.000",
    exptime: float | None = 100.0,
    band: str | None = "arr6",
    wavelength_um: tuple[float, float] | None = (4.42, 5.00),
    with_variance: bool = True,
    omit_wcs: bool = False,
) -> bytes:
    scale = pixscale_arcsec / 3600.0
    th = np.deg2rad(rot_deg)
    hdr = fits.Header()
    if not omit_wcs:
        hdr["CTYPE1"] = "RA---TAN"
        hdr["CTYPE2"] = "DEC--TAN"
        hdr["CRVAL1"] = ra_deg
        hdr["CRVAL2"] = dec_deg
        hdr["CRPIX1"] = (nx + 1) / 2.0
        hdr["CRPIX2"] = (ny + 1) / 2.0
        hdr["RADESYS"] = "ICRS"
        hdr["CD1_1"] = -scale * np.cos(th)
        hdr["CD1_2"] = scale * np.sin(th)
        hdr["CD2_1"] = scale * np.sin(th)
        hdr["CD2_2"] = scale * np.cos(th)
    if date_obs is not None:
        hdr["DATE-OBS"] = date_obs
    if exptime is not None:
        hdr["EXPTIME"] = exptime
    if band is not None:
        hdr["BAND"] = band
    if wavelength_um is not None:
        hdr["LAM_MIN"] = wavelength_um[0]
        hdr["LAM_MAX"] = wavelength_um[1]
    hdr["BUNIT"] = "MJy/sr"

    primary = fits.PrimaryHDU()
    for key in ("DATE-OBS", "EXPTIME", "BAND", "LAM_MIN", "LAM_MAX"):
        if key in hdr:
            primary.header[key] = hdr[key]
    hdus: list[fits.PrimaryHDU | fits.ImageHDU] = [
        primary,
        fits.ImageHDU(data=np.ones((ny, nx), dtype=np.float32), header=hdr, name="IMAGE"),
    ]
    if with_variance:
        hdus.append(fits.ImageHDU(data=np.full((ny, nx), 0.1, dtype=np.float32), name="VARIANCE"))
    hdus.append(fits.ImageHDU(data=np.zeros((ny, nx), dtype=np.int32), name="FLAGS"))

    buf = io.BytesIO()
    fits.HDUList(hdus).writeto(buf)
    return buf.getvalue()


@pytest.fixture()
def upload_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    store = InMemoryJobStore()
    storage = LocalStorageBackend(tmp_path / "store")
    monkeypatch.setenv("CELERY_TASK_ALWAYS_EAGER", "true")
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_storage] = lambda: storage
    try:
        yield {"client": TestClient(app), "store": store, "storage": storage}
    finally:
        app.dependency_overrides.clear()


def test_upload_valid_fits_job_sse_persistence_and_dedup(upload_env: dict[str, Any]) -> None:
    client: TestClient = upload_env["client"]
    store: InMemoryJobStore = upload_env["store"]
    storage: LocalStorageBackend = upload_env["storage"]
    payload = _make_fits()

    res = client.post(
        "/api/v1/uploads",
        files={"file": ("wise0855_s1.fits", payload, "application/fits")},
    )
    assert res.status_code == 202
    body = res.json()
    assert body["status"] == "succeeded"
    upload_id = body["upload_id"]
    fits_file_id = body["fits_file_id"]

    # Job & SSE endpoints reflect completion
    job_res = client.get(f"/api/v1/jobs/{body['job_id']}")
    assert job_res.status_code == 200
    assert job_res.json()["progress"] == pytest.approx(1.0)
    sse = client.get(f"/api/v1/jobs/{body['job_id']}/events")
    assert sse.status_code == 200
    assert "event: progress" in sse.text

    # Report and persisted storage + FITS registry
    rep_res = client.get(f"/api/v1/uploads/{upload_id}/report")
    assert rep_res.status_code == 200
    rep = rep_res.json()
    assert rep["valid"] is True
    assert rep["compatibility_status"] == "compatible"
    assert rep["pixscale_arcsec"] == pytest.approx(6.2, rel=1e-4)
    assert rep["band_label"] == "arr6"

    assert storage.exists(f"uploads/anonymous/{upload_id}/wise0855_s1.fits")
    assert not storage.exists(f"uploads/staging/{upload_id}/wise0855_s1.fits")
    assert store.get_fits_file(UUID(fits_file_id)) is not None

    # Idempotent deduplication
    dup = client.post(
        "/api/v1/uploads",
        files={"file": ("wise0855_s1.fits", payload, "application/fits")},
    )
    assert dup.status_code == 202
    assert dup.json() == body


def test_upload_reference_comparison_tiers_and_sky_field(upload_env: dict[str, Any]) -> None:
    client: TestClient = upload_env["client"]
    store: InMemoryJobStore = upload_env["store"]
    storage: LocalStorageBackend = upload_env["storage"]

    ref_bytes = _make_fits(nx=100, ny=100, ra_deg=133.795, dec_deg=-7.245, rot_deg=0.0)
    r1 = client.post("/api/v1/uploads", files={"file": ("ref.fits", ref_bytes, "application/fits")})
    assert r1.status_code == 202
    ref_uid = r1.json()["upload_id"]

    # Partial overlap, rotated, different pixel scale & band, missing VARIANCE
    partial_bytes = _make_fits(
        nx=140,
        ny=140,
        ra_deg=133.86,
        dec_deg=-7.245,
        pixscale_arcsec=2.75,
        rot_deg=20.0,
        date_obs="2025-12-20T00:00:00",
        band="W1",
        wavelength_um=(2.8, 3.8),
        with_variance=False,
    )
    r_part = client.post(
        "/api/v1/uploads",
        files={"file": ("partial.fits", partial_bytes, "application/fits")},
        data={"reference_upload_id": ref_uid},
    )
    assert r_part.status_code == 202
    rep_part = client.get(f"/api/v1/uploads/{r_part.json()['upload_id']}/report").json()
    assert rep_part["valid"] is True
    assert rep_part["compatibility_status"] == "partial"
    assert rep_part["reprojection_needed"] is True
    assert 20.0 <= rep_part["overlap_pct"] < 80.0
    notes = " ".join(rep_part["accuracy_notes"])
    assert "F_corr" in notes and "VARIANCE" in notes and "colour-dependent" in notes

    # Disjoint pointing -> incompatible, not saved to permanent uploads or FITS registry
    far_bytes = _make_fits(ra_deg=135.0, dec_deg=-7.245)
    r_bad = client.post(
        "/api/v1/uploads",
        files={"file": ("far.fits", far_bytes, "application/fits")},
        data={"reference_upload_id": ref_uid},
    )
    assert r_bad.status_code == 202
    bad_id = r_bad.json()["upload_id"]
    rep_bad = client.get(f"/api/v1/uploads/{bad_id}/report").json()
    assert rep_bad["valid"] is False
    assert rep_bad["compatibility_status"] == "incompatible"
    assert any("20%" in err for err in rep_bad["errors"])
    assert not storage.exists(f"uploads/anonymous/{bad_id}/far.fits")
    assert store.get_fits_file(UUID(r_bad.json()["fits_file_id"])) is None

    # Reference sky field parameters
    r_sky = client.post(
        "/api/v1/uploads",
        files={"file": ("field.fits", ref_bytes, "application/fits")},
        data={
            "reference_ra_deg": "133.795",
            "reference_dec_deg": "-7.245",
            "reference_fov_arcmin": "10.0",
        },
    )
    assert r_sky.status_code == 202
    rep_sky = client.get(f"/api/v1/uploads/{r_sky.json()['upload_id']}/report").json()
    assert rep_sky["compatibility_status"] == "compatible"
    assert rep_sky["overlap_pct"] > 80.0

    # Reference validation errors
    assert (
        client.post(
            "/api/v1/uploads",
            files={"file": ("a.fits", ref_bytes, "application/fits")},
            data={"reference_ra_deg": "133.795"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/uploads",
            files={"file": ("a.fits", ref_bytes, "application/fits")},
            data={
                "reference_upload_id": ref_uid,
                "reference_ra_deg": "133.795",
                "reference_dec_deg": "-7.245",
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/uploads",
            files={"file": ("a.fits", ref_bytes, "application/fits")},
            data={"reference_upload_id": str(uuid4())},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/uploads",
            files={"file": ("a.fits", ref_bytes, "application/fits")},
            data={"reference_upload_id": bad_id},
        ).status_code
        == 422
    )


def test_upload_missing_epoch_user_epoch_and_invalid_fits(upload_env: dict[str, Any]) -> None:
    client: TestClient = upload_env["client"]

    no_epoch = _make_fits(date_obs=None, exptime=None)
    r_miss = client.post("/api/v1/uploads", files={"file": ("no_ep.fits", no_epoch, "image/fits")})
    assert r_miss.status_code == 202
    rep_miss = client.get(f"/api/v1/uploads/{r_miss.json()['upload_id']}/report").json()
    assert rep_miss["valid"] is False
    assert any("USER_EPOCH" in e for e in rep_miss["errors"])

    # Supply ISO user_epoch
    r_iso = client.post(
        "/api/v1/uploads",
        files={"file": ("no_ep.fits", no_epoch, "image/fits")},
        data={"user_epoch": "2025-08-15T00:00:00Z"},
    )
    assert r_iso.status_code == 202
    rep_iso = client.get(f"/api/v1/uploads/{r_iso.json()['upload_id']}/report").json()
    assert rep_iso["valid"] is True
    assert rep_iso["mjd_tdb_mid"] == pytest.approx(
        Time("2025-08-15T00:00:00", scale="utc").tdb.mjd, abs=1e-9
    )
    assert any("USER_EPOCH" in n for n in rep_iso["accuracy_notes"])

    # Supply numeric MJD user_epoch
    r_mjd = client.post(
        "/api/v1/uploads",
        files={"file": ("no_ep.fits", no_epoch, "image/fits")},
        data={"user_epoch": "60850.5"},
    )
    assert r_mjd.status_code == 202
    rep_mjd = client.get(f"/api/v1/uploads/{r_mjd.json()['upload_id']}/report").json()
    assert rep_mjd["valid"] is True
    assert rep_mjd["mjd_tdb_mid"] == pytest.approx(60850.5, abs=1e-9)

    # Invalid user_epoch rejected at boundary
    r_bad_ep = client.post(
        "/api/v1/uploads",
        files={"file": ("no_ep.fits", no_epoch, "image/fits")},
        data={"user_epoch": "not-a-timestamp"},
    )
    assert r_bad_ep.status_code == 422
    assert r_bad_ep.headers["content-type"].startswith("application/problem+json")

    # Missing WCS & corrupt payload produce specific report errors
    r_nowcs = client.post(
        "/api/v1/uploads",
        files={"file": ("nowcs.fits", _make_fits(omit_wcs=True), "image/fits")},
    )
    rep_nowcs = client.get(f"/api/v1/uploads/{r_nowcs.json()['upload_id']}/report").json()
    assert rep_nowcs["valid"] is False
    assert any("WCS" in e for e in rep_nowcs["errors"])

    r_bad = client.post(
        "/api/v1/uploads",
        files={"file": ("bad.fits", b"NOT_A_FITS_FILE", "image/fits")},
    )
    rep_bad = client.get(f"/api/v1/uploads/{r_bad.json()['upload_id']}/report").json()
    assert rep_bad["valid"] is False
    assert any("Invalid or unreadable FITS" in e for e in rep_bad["errors"])


def test_upload_security_size_caps_gzip_bomb_filename_and_status_errors(
    upload_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    client: TestClient = upload_env["client"]
    store: InMemoryJobStore = upload_env["store"]
    storage: LocalStorageBackend = upload_env["storage"]
    fits_bytes = _make_fits(nx=32, ny=32)

    # Empty upload rejected with 422 problem+json
    r_empty = client.post("/api/v1/uploads", files={"file": ("empty.fits", b"", "image/fits")})
    assert r_empty.status_code == 422
    assert r_empty.headers["content-type"].startswith("application/problem+json")

    # Size cap enforced for anonymous (413) while authenticated user limit allows it
    monkeypatch.setenv("SPHX_UPLOAD_MAX_BYTES_ANON", "1024")
    monkeypatch.setenv("SPHX_UPLOAD_MAX_BYTES_AUTH", "1048576")
    r_big = client.post("/api/v1/uploads", files={"file": ("big.fits", fits_bytes, "image/fits")})
    assert r_big.status_code == 413
    assert r_big.headers["content-type"].startswith("application/problem+json")
    assert "science_impact" in r_big.json()

    # Gzip bomb pre-check catches small compressed payload that expands past limit
    gz_bomb = gzip.compress(b"\x00" * 4096)
    assert len(gz_bomb) < 1024
    r_bomb = client.post("/api/v1/uploads", files={"file": ("bomb.fits.gz", gz_bomb, "image/fits")})
    assert r_bomb.status_code == 413

    user_id = uuid4()
    r_auth = client.post(
        "/api/v1/uploads",
        files={"file": ("../../etc/evil.fits", fits_bytes, "image/fits")},
        headers={"Authorization": f"Bearer {user_id}"},
    )
    assert r_auth.status_code == 202
    uid_auth = r_auth.json()["upload_id"]
    assert storage.exists(f"uploads/{user_id}/{uid_auth}/evil.fits")

    # Report status mapping: 404 unknown, 409 queued/running, 502 failed
    assert client.get(f"/api/v1/uploads/{uuid4()}/report").status_code == 404

    pending_uid = uuid4()
    store.create_or_get_job(
        task=UPLOAD_TASK_NAME,
        queue="fast",
        params={"upload_id": str(pending_uid)},
        params_hash=b"pending-upload-hash",
    )
    assert client.get(f"/api/v1/uploads/{pending_uid}/report").status_code == 409

    failed_uid = uuid4()
    failed_job, _ = store.create_or_get_job(
        task=UPLOAD_TASK_NAME,
        queue="fast",
        params={"upload_id": str(failed_uid)},
        params_hash=b"failed-upload-hash",
    )
    store.update_job(failed_job.id, status="failed", error={"detail": "disk error"})
    assert client.get(f"/api/v1/uploads/{failed_uid}/report").status_code == 502
