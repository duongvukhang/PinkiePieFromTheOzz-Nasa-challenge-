"""Offline unit tests for FITS inspection, WCS/footprint overlap, and upload validation."""

from __future__ import annotations

import io
from pathlib import Path

import astropy.units as u
import numpy as np
import pytest
from astropy.io import fits
from astropy.time import Time

from app.db.flags import DetectionFlag
from app.providers.base import FrameRef
from app.services import LocalStorageBackend, validate_fits_set, validate_fits_upload
from sphx_core.io import inspect_fits
from sphx_core.wcs import spherical_polygon_overlap


def _build_fits_bytes(
    *,
    nx: int = 64,
    ny: int = 64,
    ra_deg: float = 133.79513,
    dec_deg: float = -7.24515,
    pixscale_arcsec: float = 6.2,
    rot_deg: float = 0.0,
    parity: int = 1,
    use_cd: bool = True,
    add_sip: bool = False,
    naxis3: int = 0,
    date_obs: str | None = "2025-06-21T09:50:24.000",
    exptime: float | None = 100.0,
    timesys: str | None = "UTC",
    band: str | None = "arr6",
    wavelength_um: tuple[float, float] | None = (4.42, 5.00),
    mef: bool = True,
    with_variance: bool = True,
    with_flags: bool = True,
    omit_wcs: bool = False,
) -> bytes:
    scale_deg = pixscale_arcsec / 3600.0
    theta = np.deg2rad(rot_deg)
    # Astronomical FITS standard: parity=+1 matches CDELT1 < 0, CDELT2 > 0 (det < 0).
    det_sign = -1.0 if parity >= 0 else 1.0
    sx, sy = det_sign * scale_deg, scale_deg
    cd11 = sx * np.cos(theta)
    cd12 = -sx * np.sin(theta)
    cd21 = sy * np.sin(theta)
    cd22 = sy * np.cos(theta)

    hdr = fits.Header()
    if not omit_wcs:
        hdr["CTYPE1"] = "RA---TAN-SIP" if add_sip else "RA---TAN"
        hdr["CTYPE2"] = "DEC--TAN-SIP" if add_sip else "DEC--TAN"
        hdr["CRVAL1"] = ra_deg
        hdr["CRVAL2"] = dec_deg
        hdr["CRPIX1"] = (nx + 1) / 2.0
        hdr["CRPIX2"] = (ny + 1) / 2.0
        hdr["RADESYS"] = "ICRS"
        if use_cd:
            hdr["CD1_1"] = cd11
            hdr["CD1_2"] = cd12
            hdr["CD2_1"] = cd21
            hdr["CD2_2"] = cd22
        else:
            hdr["CDELT1"] = sx
            hdr["CDELT2"] = sy
            hdr["PC1_1"] = np.cos(theta)
            hdr["PC1_2"] = -np.sin(theta)
            hdr["PC2_1"] = np.sin(theta)
            hdr["PC2_2"] = np.cos(theta)
        if add_sip:
            hdr["A_ORDER"] = 2
            hdr["B_ORDER"] = 2
            hdr["A_2_0"] = 1e-6
            hdr["B_0_2"] = -1e-6

    if date_obs is not None:
        hdr["DATE-OBS"] = date_obs
    if exptime is not None:
        hdr["EXPTIME"] = exptime
    if timesys is not None:
        hdr["TIMESYS"] = timesys
    if band is not None:
        hdr["BAND"] = band
    if wavelength_um is not None:
        hdr["LAM_MIN"] = wavelength_um[0]
        hdr["LAM_MAX"] = wavelength_um[1]
    hdr["BUNIT"] = "MJy/sr"

    shape = (naxis3, ny, nx) if naxis3 > 0 else (ny, nx)
    img_data = np.ones(shape, dtype=np.float32) * 3.0
    if naxis3 > 0:
        hdr["NAXIS"] = 3
        hdr["NAXIS1"] = nx
        hdr["NAXIS2"] = ny
        hdr["NAXIS3"] = naxis3
        hdr["CTYPE3"] = "WAVE"
        hdr["CRPIX3"] = 1.0
        hdr["CRVAL3"] = 4.5
        hdr["CDELT3"] = 0.05

    hdus: list[fits.hdu.base.ExtensionHDU | fits.PrimaryHDU]
    if mef:
        primary = fits.PrimaryHDU()
        for key in ("DATE-OBS", "EXPTIME", "TIMESYS", "BAND", "LAM_MIN", "LAM_MAX"):
            if key in hdr:
                primary.header[key] = hdr[key]
        hdus = [primary, fits.ImageHDU(data=img_data, header=hdr, name="IMAGE")]
        if with_variance:
            hdus.append(
                fits.ImageHDU(
                    data=np.ones((ny, nx), dtype=np.float32) * 0.05,
                    name="VARIANCE",
                )
            )
        if with_flags:
            hdus.append(fits.ImageHDU(data=np.zeros((ny, nx), dtype=np.int32), name="FLAGS"))
    else:
        hdus = [fits.PrimaryHDU(data=img_data, header=hdr)]

    buf = io.BytesIO()
    fits.HDUList(hdus).writeto(buf, overwrite=True)
    return buf.getvalue()


def test_wcs_variants_cd_pc_sip_cube_and_epoch_timesys(tmp_path: Path) -> None:
    """Parse CD, PC+CDELT, -SIP, 3D cubes, and TIMESYS/EXPTIME mid-epochs accurately."""
    cd_path = tmp_path / "rot_cd.fits"
    cd_path.write_bytes(
        _build_fits_bytes(rot_deg=35.0, pixscale_arcsec=6.2, add_sip=True, use_cd=True)
    )
    ins_cd = inspect_fits(cd_path)
    assert ins_cd.wcs is not None
    assert ins_cd.wcs.pixscale_arcsec == pytest.approx(6.2, rel=1e-4)
    assert ins_cd.wcs.rotation_deg == pytest.approx(35.0, abs=0.05)
    assert ins_cd.wcs.parity == -1
    assert ins_cd.wcs.has_sip
    assert ins_cd.epoch is not None
    expected_mid = (Time("2025-06-21T09:50:24.000", scale="utc") + 50.0 * u.s).tdb.mjd
    assert ins_cd.epoch.mjd_tdb_mid == pytest.approx(expected_mid, abs=1e-9)

    pc_bytes = _build_fits_bytes(
        rot_deg=-125.0,
        pixscale_arcsec=2.75,
        use_cd=False,
        mef=False,
        naxis3=2,
        timesys="TT",
        exptime=60.0,
    )
    ins_pc = inspect_fits(pc_bytes)
    assert ins_pc.wcs is not None
    assert ins_pc.wcs.pixscale_arcsec == pytest.approx(2.75, rel=1e-4)
    assert ins_pc.wcs.rotation_deg == pytest.approx(-125.0, abs=0.05)
    assert ins_pc.wcs.naxis1 == 64 and ins_pc.wcs.naxis2 == 64
    assert ins_pc.epoch is not None
    expected_tt_mid = (Time("2025-06-21T09:50:24.000", scale="tt") + 30.0 * u.s).tdb.mjd
    assert ins_pc.epoch.mjd_tdb_mid == pytest.approx(expected_tt_mid, abs=1e-9)
    assert ins_pc.epoch.timesys == "TT"


def test_spherical_overlap_ra_wraparound_and_high_dec() -> None:
    """Verify exact spherical overlap across RA=0/360 and near celestial poles."""
    b_wrap_a = _build_fits_bytes(ra_deg=359.98, dec_deg=0.0, nx=120, ny=120, pixscale_arcsec=6.2)
    b_wrap_b = _build_fits_bytes(ra_deg=0.02, dec_deg=0.0, nx=120, ny=120, pixscale_arcsec=6.2)
    res_wrap = validate_fits_upload(b_wrap_b, reference=b_wrap_a, user_epoch="2025-07-01T00:00:00")
    assert res_wrap.report.overlap_pct is not None
    assert 75.0 < res_wrap.report.overlap_pct < 85.0

    # Near Dec=+85 deg, RA spacing shrinks by cos(85 deg) ~ 0.087 -> high-Dec stability check.
    ins_pole_a = inspect_fits(_build_fits_bytes(ra_deg=45.0, dec_deg=85.0, nx=120, ny=120))
    ins_pole_b = inspect_fits(_build_fits_bytes(ra_deg=45.0, dec_deg=85.0, nx=120, ny=120))
    assert ins_pole_a.wcs is not None and ins_pole_b.wcs is not None
    _, frac = spherical_polygon_overlap(ins_pole_a.wcs.footprint, ins_pole_b.wcs.footprint)
    assert frac == pytest.approx(1.0, abs=1e-3)


def test_compatibility_tiers_reprojection_accuracy_and_storage(tmp_path: Path) -> None:
    """Validate compatible/partial/incompatible tiers, reprojection accuracy notes, and storage."""
    ref_bytes = _build_fits_bytes(
        nx=100,
        ny=100,
        ra_deg=133.795,
        dec_deg=-7.245,
        pixscale_arcsec=6.2,
        rot_deg=0.0,
        date_obs="2025-06-21T09:50:24",
        band="arr6",
        wavelength_um=(4.42, 5.00),
    )

    # 1) Exact footprint match + scale/rotation/parity match -> compatible, no reprojection
    aligned_later = _build_fits_bytes(
        nx=100,
        ny=100,
        ra_deg=133.795,
        dec_deg=-7.245,
        pixscale_arcsec=6.2,
        rot_deg=0.0,
        date_obs="2025-12-21T09:50:24",
        band="arr6",
        wavelength_um=(4.42, 5.00),
    )
    storage = LocalStorageBackend(tmp_path / "uploads")
    res_ok = validate_fits_upload(aligned_later, reference=ref_bytes, storage=storage)
    assert res_ok.report.valid
    assert res_ok.report.compatibility_status == "compatible"
    assert res_ok.report.overlap_pct == pytest.approx(100.0, abs=0.2)
    assert not res_ok.report.reprojection_needed
    assert res_ok.flags == 0
    assert res_ok.storage_path is not None and res_ok.sha256 is not None
    assert storage.exists(res_ok.storage_path)

    # 2) Rotated, >2x pixel scale diff, opposite parity, different band, missing VARIANCE -> partial
    partial_upload = _build_fits_bytes(
        nx=150,
        ny=150,
        ra_deg=133.86,  # ~3.9' shift on 10.3' ref box -> partial overlap (~45-65%)
        dec_deg=-7.245,
        pixscale_arcsec=2.75,
        rot_deg=22.0,
        parity=-1,
        date_obs="2025-07-01T00:00:00",  # 9.6 d baseline -> short baseline note
        band="W1",
        wavelength_um=(2.8, 3.8),
        with_variance=False,
        with_flags=False,
    )
    res_partial = validate_fits_upload(partial_upload, reference=ref_bytes)
    assert res_partial.report.valid
    assert res_partial.report.compatibility_status == "partial"
    assert (
        res_partial.report.overlap_pct is not None and 20.0 <= res_partial.report.overlap_pct < 80.0
    )
    assert res_partial.report.reprojection_needed
    assert res_partial.flags & int(DetectionFlag.REPROJECTED)
    notes_joined = " ".join(res_partial.report.accuracy_notes)
    assert "F_corr" in notes_joined
    assert "VARIANCE" in notes_joined
    assert "colour-dependent" in notes_joined
    assert "Partial overlap" in notes_joined
    assert "Short time baseline" in notes_joined

    # 3) Disjoint pointing -> incompatible (<20% overlap), not saved to storage
    far_upload = _build_fits_bytes(ra_deg=135.0, dec_deg=-7.245)
    res_bad = validate_fits_upload(far_upload, reference=ref_bytes, storage=storage)
    assert not res_bad.report.valid
    assert res_bad.report.compatibility_status == "incompatible"
    assert res_bad.report.overlap_pct is not None and res_bad.report.overlap_pct < 20.0
    assert res_bad.storage_path is None
    assert any("20%" in e for e in res_bad.report.errors)


def test_missing_epoch_user_epoch_wcs_and_size_rejection(tmp_path: Path) -> None:
    """Check USER_EPOCH flagging, missing WCS error, and pre-open max_bytes rejection."""
    no_epoch = _build_fits_bytes(date_obs=None, exptime=None, mef=False)
    res_missing_ep = validate_fits_upload(no_epoch)
    assert not res_missing_ep.report.valid
    assert any("USER_EPOCH" in e for e in res_missing_ep.report.errors)

    res_user_ep = validate_fits_upload(no_epoch, user_epoch="2025-08-15T00:00:00")
    assert res_user_ep.report.valid
    assert res_user_ep.flags & int(DetectionFlag.USER_EPOCH)
    assert res_user_ep.report.mjd_tdb_mid == pytest.approx(
        Time("2025-08-15T00:00:00", scale="utc").tdb.mjd, abs=1e-9
    )
    assert any("USER_EPOCH" in n for n in res_user_ep.report.accuracy_notes)

    no_wcs = _build_fits_bytes(omit_wcs=True)
    res_no_wcs = validate_fits_upload(no_wcs)
    assert not res_no_wcs.report.valid
    assert any("WCS" in e for e in res_no_wcs.report.errors)

    big_file = tmp_path / "too_big.fits"
    big_file.write_bytes(b"\x00" * 4096)
    res_big = validate_fits_upload(big_file, max_bytes=1024)
    assert not res_big.report.valid
    assert res_big.sha256 is None
    assert any("exceeds limit" in e for e in res_big.report.errors)


def test_validate_fits_set_and_frame_ref_comparison() -> None:
    """Validate multi-file FITS set and FrameRef reference compatibility checking."""
    f1 = _build_fits_bytes(nx=100, ny=100, date_obs="2025-06-21T09:50:24", rot_deg=0.0)
    f2 = _build_fits_bytes(
        nx=100, ny=100, date_obs="2025-06-21T09:50:24", rot_deg=0.0
    )  # same epoch!
    f3 = _build_fits_bytes(nx=100, ny=100, date_obs="2025-12-20T09:50:24", rot_deg=5.0)
    results, joint = validate_fits_set([f1, f2, f3])
    assert len(results) == 3
    assert joint.valid
    assert joint.status == "compatible"
    assert joint.reprojection_needed
    assert any("Zero time baseline" in n for n in results[1].report.accuracy_notes)

    ins1 = inspect_fits(f1)
    assert ins1.wcs is not None and ins1.epoch is not None
    ref_frame = FrameRef(
        provider="spherex_irsa",
        release="spherex_qr",
        obs_id="frame_001",
        access_url="https://irsa.ipac.caltech.edu/fake.fits",
        t_mid=Time(ins1.epoch.mjd_tdb_mid + 180.0, format="mjd", scale="tdb"),
        band_id="arr6",
        wavelength_um=(4.42, 5.00),
        footprint=ins1.wcs.footprint,
    )
    vs_ref = validate_fits_upload(f1, reference=ref_frame)
    assert vs_ref.report.valid
    assert vs_ref.report.overlap_pct == pytest.approx(100.0, abs=0.2)
    assert vs_ref.compatibility.band_match is True
    assert vs_ref.compatibility.delta_t_days == pytest.approx(180.0, abs=1e-4)
