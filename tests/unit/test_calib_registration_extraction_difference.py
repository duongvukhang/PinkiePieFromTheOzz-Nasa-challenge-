"""Offline unit tests for sphx_core wcs helpers, calib, extraction, registration, and difference."""

from __future__ import annotations

import io
import json
import math
import sys
from pathlib import Path

import astropy.units as u
import numpy as np
import pytest
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.time import Time

from sphx_core.calib import (
    DetectionFlag,
    coadd_band_group,
    estimate_background_and_noise,
    load_frame,
    reproject_to_grid,
)
from sphx_core.difference import difference_frames, zogy_difference
from sphx_core.extraction import estimate_psf_kernel, extract_sources, matched_filter_snr
from sphx_core.registration import propagate_gaia_catalog, register_exposure
from sphx_core.wcs import local_wcs_jacobian, sky_to_tangent_arcsec, tangent_arcsec_to_sky

_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tests.science.synthetic_fits import (  # noqa: E402
    InjectedSource,
    add_pixel_integrated_psf,
    generate_synthetic_cutout,
    generate_synthetic_sequence,
    make_tan_wcs,
    sky_position_at_epoch,
)


def test_tangent_projection_and_wcs_jacobian() -> None:
    """Verify gnomonic projection round-trip across RA=0/360 and high-Dec + local Jacobian."""
    for ra0, dec0 in [(359.95, 10.0), (45.0, -72.0)]:
        xi, eta = sky_to_tangent_arcsec(0.05 if ra0 > 350 else 45.2, dec0 + 0.08, ra0, dec0)
        ra_rt, dec_rt = tangent_arcsec_to_sky(xi, eta, ra0, dec0)
        expected_ra = 0.05 if ra0 > 350 else 45.2
        assert abs(((float(ra_rt) - expected_ra + 180.0) % 360.0) - 180.0) < 1e-10
        assert float(dec_rt) == pytest.approx(dec0 + 0.08, abs=1e-10)

    w0 = make_tan_wcs(64, 64, 150.0, 20.0, pixscale_arcsec=6.2, rot_deg=0.0)
    j0 = local_wcs_jacobian(w0, 31.5, 31.5)
    assert j0[0, 0] == pytest.approx(-6.2, abs=1e-2)
    assert j0[1, 1] == pytest.approx(6.2, abs=1e-2)
    assert abs(j0[0, 1]) < 1e-2 and abs(j0[1, 0]) < 1e-2

    w30 = make_tan_wcs(64, 64, 150.0, 20.0, pixscale_arcsec=6.2, rot_deg=30.0)
    j30 = local_wcs_jacobian(w30, 31.5, 31.5)
    assert math.hypot(j30[0, 0], j30[1, 0]) == pytest.approx(6.2, abs=1e-2)


def test_calib_zodi_lvf_scattered_light_reproject_and_coadd() -> None:
    """Verify ZODI subtraction, scatter flag, flux-conserving reprojection, and co-add clip."""
    ny, nx = 96, 96
    rng = np.random.default_rng(42)
    wcs = make_tan_wcs(nx, ny, 133.8, -7.25, pixscale_arcsec=6.2)
    hdr = wcs.to_header(relax=True)
    hdr.update(
        {
            "DATE-OBS": "2025-07-01T00:00:00",
            "EXPTIME": 100.0,
            "TIMESYS": "UTC",
            "BAND": "B6",
            "LAM_MIN": 4.42,
            "LAM_MAX": 5.00,
        }
    )
    yy, xx = np.mgrid[:ny, :nx]
    zodi = 12.0 + 0.02 * xx
    # Inject corner-bright scattered light on the upper half of the 96x96 detector.
    scatter = 12.0 * np.exp(-((xx - 80.0) ** 2 + (yy - 80.0) ** 2) / (2.0 * 18.0**2))
    img = zodi + scatter + rng.normal(0.0, 1.0, size=(ny, nx))
    add_pixel_integrated_psf(img, 48.0, 48.0, 120.0, 0.85)
    # Spectral gradient along x in WCS-WAVE.
    wave = 4.42 + 0.58 * (xx / (nx - 1.0))

    buf = io.BytesIO()
    fits.HDUList(
        [
            fits.PrimaryHDU(header=hdr),
            fits.ImageHDU(data=img.astype(np.float32), header=hdr, name="IMAGE"),
            fits.ImageHDU(data=np.ones((ny, nx), dtype=np.float32), name="VARIANCE"),
            fits.ImageHDU(data=zodi.astype(np.float32), name="ZODI"),
            fits.ImageHDU(data=wave.astype(np.float32), name="WCS-WAVE"),
            fits.ImageHDU(data=np.zeros((ny, nx), dtype=np.int32), name="FLAGS"),
        ]
    ).writeto(buf, overwrite=True)

    fd = load_frame(buf.getvalue())
    bg = estimate_background_and_noise(fd)
    assert bg.zodi_subtracted
    assert bg.scattered_light
    assert bg.empirical_rms == pytest.approx(1.0, abs=0.25)

    # Identical grid -> no reproject, rotated -> conserves point-source flux and f_corr >= 1.
    same_rep = reproject_to_grid(fd, wcs, (ny, nx))
    assert not same_rep.reprojected and same_rep.f_corr == 1.0

    wcs_rot = make_tan_wcs(nx, ny, 133.8, -7.25, pixscale_arcsec=6.2, rot_deg=18.0)
    rot_rep = reproject_to_grid(fd, wcs_rot, (ny, ny), seed=42)
    assert rot_rep.reprojected
    assert rot_rep.f_corr >= 1.0
    ap_sum = float(np.sum(rot_rep.image[45:52, 45:52]))
    assert ap_sum == pytest.approx(float(np.sum(bg.subtracted[45:52, 45:52])), rel=0.03)
    assert ap_sum == pytest.approx(120.0, rel=0.08)

    # Co-add 3 epochs with a transient hot outlier in epoch 1 (must be 3-sigma clipped).
    ep_base = Time("2025-07-01T00:00:00", scale="tdb").mjd
    srcs = [InjectedSource("star", 133.8, -7.25, ep_base, base_flux=80.0, kind="static")]
    seq = generate_synthetic_sequence(
        seed=51,
        sources=srcs,
        ra_center_deg=133.8,
        dec_center_deg=-7.25,
        epochs_mjd=[ep_base, ep_base + 10.0, ep_base + 20.0],
        band_id="B6",
    )
    loaded_seq = [load_frame(f.fits_bytes) for f in seq]
    loaded_seq[1].image[15, 15] += 500.0  # unflagged spike only in epoch 1
    coadds = coadd_band_group(loaded_seq)
    assert "B6" in coadds
    co = coadds["B6"]
    assert co.n_frames == 3
    assert abs(float(co.image[15, 15])) < 5.0


def test_extraction_psf_crowding_artifacts_and_saturation() -> None:
    """Verify matched-filter noise, artifact rejection, saturated wings, and neighbor recovery."""
    # 1) Pure noise matched filter has unit RMS.
    rng = np.random.default_rng(12)
    noise = rng.normal(0.0, 1.0, size=(64, 64))
    kern = estimate_psf_kernel(0.85)
    snr_noise = matched_filter_snr(noise, np.ones_like(noise), kern)
    assert float(np.std(snr_noise[5:-5, 5:-5])) == pytest.approx(1.0, abs=0.1)

    # 2) Saturated star + nearby neighbor + cosmic rays + persistence spots.
    ep = Time("2025-07-01T00:00:00", scale="tdb").mjd
    ra0, dec0 = 133.800, -7.245
    cos_d = math.cos(math.radians(dec0))
    srcs = [
        InjectedSource("sat", ra0, dec0, ep, base_flux=500000.0, kind="saturated"),
        InjectedSource(
            "near", ra0 + (37.2 / 3600.0) / cos_d, dec0, ep, base_flux=65.0, kind="static"
        ),
        InjectedSource(
            "iso",
            ra0 - (93.0 / 3600.0) / cos_d,
            dec0 + 62.0 / 3600.0,
            ep,
            base_flux=90.0,
            kind="static",
        ),
    ]
    frame = generate_synthetic_cutout(
        seed=13,
        sources=srcs,
        ra_center_deg=ra0,
        dec_center_deg=dec0,
        mjd_tdb_mid=ep,
        n_cosmic_rays=4,
        n_persist_spots=2,
    )
    res = extract_sources(frame.fits_bytes)
    assert len(res.detections) == 3

    det_sat = min(
        res.detections,
        key=lambda d: math.hypot(d.x_px - frame.truth[0].x_px, d.y_px - frame.truth[0].y_px),
    )
    assert det_sat.flags & int(DetectionFlag.SATURATED)
    assert det_sat.flux == pytest.approx(500000.0, rel=0.02)

    det_near = min(
        res.detections,
        key=lambda d: math.hypot(d.x_px - frame.truth[1].x_px, d.y_px - frame.truth[1].y_px),
    )
    assert abs(det_near.flux - 65.0) < 10.0
    assert (
        math.hypot(det_near.x_px - frame.truth[1].x_px, det_near.y_px - frame.truth[1].y_px) < 0.25
    )
    assert det_near.real_bogus >= 0.7
    assert det_near.sig_ra_arcsec >= 0.10
    assert not (det_near.flags & int(DetectionFlag.BLENDED | DetectionFlag.SATURATED))


def test_registration_gaia_propagation_and_models() -> None:
    """Verify Gaia DR3 filter/propagation precision and 4/6/TAN-SIP registration modes."""
    ref_vals = json.loads(
        (Path(__file__).resolve().parents[1] / "science" / "reference_values.json").read_text()
    )
    qa_stars = ref_vals["gaia_qa_stars"]
    t_obs = Time("2026-01-15T00:00:00", scale="tdb").mjd

    # Verify propagate_gaia_catalog filters ruwe >= 1.4 and matches high-PM + parallax prediction.
    prop = propagate_gaia_catalog(
        qa_stars, t_obs, apply_filters=True, max_ruwe=1.4, g_mag_range=(12.0, 17.5)
    )
    ids = {p.source_id for p in prop}
    assert 5756945305674270208 not in ids  # has ruwe=4.32 in reference_values.json
    assert len(prop) == len(qa_stars) - 1

    s0 = qa_stars[2]
    t_ref_mjd = Time(s0["ref_epoch"], format="jyear", scale="tdb").mjd
    ra_exp, dec_exp = sky_position_at_epoch(
        s0["ra"], s0["dec"], t_ref_mjd, t_obs, s0["pmra"] * 1e-3, s0["pmdec"] * 1e-3, s0["parallax"]
    )
    p2 = next(p for p in prop if p.source_id == s0["source_id"])
    assert abs(p2.ra_deg - ra_exp) * 3600.0 < 1e-3
    assert abs(p2.dec_deg - dec_exp) * 3600.0 < 1e-3

    # Test 6-parameter affine mode (15 <= N < 80) on 20 grid stars with shift + rotation.
    ra0, dec0 = 180.0, 10.0
    cos_d = math.cos(math.radians(dec0))
    grid_srcs = [
        InjectedSource(
            f"g_{i}",
            ra0 + (((i % 5) - 2) * 55.0 / 3600.0) / cos_d,
            dec0 + (((i // 5) - 1.5) * 65.0 / 3600.0),
            t_obs,
            base_flux=280.0,
            kind="static",
            gaia_id=2000 + i,
        )
        for i in range(20)
    ]
    cutout_20 = generate_synthetic_cutout(
        seed=77,
        sources=grid_srcs,
        ra_center_deg=ra0,
        dec_center_deg=dec0,
        mjd_tdb_mid=t_obs,
        wcs_shift_arcsec=(2.0, -1.5),
        wcs_rot_err_deg=0.35,
        with_sip=True,
    )
    reg20 = register_exposure(cutout_20.fits_bytes, reference_catalog=cutout_20.reference_stars)
    assert reg20.converged and not reg20.degraded
    assert reg20.model_used == "affine_6param"
    assert reg20.median_residual_arcsec <= 0.15
    true_errs = [
        float(
            SkyCoord(
                *[float(v) for v in reg20.wcs.pixel_to_world_values(t.x_px, t.y_px)], unit=u.deg
            )
            .separation(SkyCoord(t.ra_deg * u.deg, t.dec_deg * u.deg))
            .arcsec
        )
        for t in cutout_20.truth
    ]
    assert float(np.median(true_errs)) <= 0.05

    # Test N >= 80 TAN-SIP degree-3 mode + pixel-phase lookup on a 128x128 frame (85 stars).
    ny, nx = 128, 128
    wcs_true = make_tan_wcs(nx, ny, ra0, dec0, pixscale_arcsec=6.2, with_sip=True)
    wcs_rep = make_tan_wcs(nx, ny, ra0, dec0, pixscale_arcsec=6.2, rot_deg=0.4, with_sip=False)
    wcs_rep.wcs.crval = [ra0 + (2.0 / 3600.0) / cos_d, dec0 - 1.5 / 3600.0]
    img128 = np.random.default_rng(78).normal(10.0, 1.0, size=(ny, nx))
    refs85: list[dict[str, float | int]] = []
    pts85: list[tuple[float, float, float, float]] = []
    for k in range(85):
        px = 10.0 + (k % 10) * 11.5
        py = 12.0 + (k // 10) * 12.5
        add_pixel_integrated_psf(img128, px, py, 320.0, 0.85)
        ra_s, dec_s = (float(v) for v in wcs_true.pixel_to_world_values(px, py))
        refs85.append(
            {"gaia_id": 5000 + k, "ra_deg": ra_s % 360.0, "dec_deg": dec_s, "g_mag": 14.5}
        )
        pts85.append((px, py, ra_s % 360.0, dec_s))
    hdr128 = wcs_rep.to_header(relax=True)
    hdr128.update({"DATE-OBS": "2026-01-15T00:00:00", "EXPTIME": 100.0, "TIMESYS": "UTC"})
    reg85 = register_exposure(
        fits.HDUList(
            [
                fits.PrimaryHDU(header=hdr128),
                fits.ImageHDU(data=img128.astype(np.float32), header=hdr128, name="IMAGE"),
            ]
        ),
        reference_catalog=refs85,
    )
    assert reg85.converged and reg85.model_used == "tan_sip_deg3" and reg85.pixel_phase_applied
    errs85 = [
        float(
            SkyCoord(*[float(v) for v in reg85.wcs.pixel_to_world_values(px, py)], unit=u.deg)
            .separation(SkyCoord(ra_s * u.deg, dec_s * u.deg))
            .arcsec
        )
        for px, py, ra_s, dec_s in pts85
    ]
    assert float(np.median(errs85)) <= 0.08


def test_difference_zogy_monopole_vs_dipole_and_bands() -> None:
    """Verify ZOGY variance normalization, rotated frame differencing, variable vs mover BIC."""
    ep0 = Time("2025-07-01T00:00:00", scale="tdb").mjd
    ep1 = ep0 + 120.0
    ra0, dec0 = 200.0, 25.0

    # 1) Variable star with static anchors across rotated reference vs science grid.
    var_src = InjectedSource(
        "var",
        ra0,
        dec0,
        ep0,
        flux_fn=lambda mjd, _b: 220.0 if abs(mjd - ep0) < 1.0 else 110.0,
        kind="variable",
    )
    anchor = InjectedSource("anc", ra0 + 0.02, dec0 + 0.02, ep0, base_flux=250.0, kind="static")
    seq_v = generate_synthetic_sequence(
        seed=88,
        sources=[anchor, var_src],
        ra_center_deg=ra0,
        dec_center_deg=dec0,
        epochs_mjd=[ep0, ep1, ep0 + 240.0],
        rot_degs=[0.0, 12.0, 0.0],
        band_id="B4",
    )
    diff_v = difference_frames([f.fits_bytes for f in seq_v])
    assert diff_v.monopole_favored and not diff_v.dipole_favored
    assert diff_v.delta_bic_mv_over_mm > 5.0
    st_v = diff_v.stamp_test_near(ra0, dec0)
    assert st_v is not None and st_v.monopole_favored

    # 2) Pure mover (1.8 px shift) triggers dipole_favored and delta_bic_mm_over_mv > 10.
    mover = InjectedSource(
        "mov", ra0, dec0, ep0, pmra_arcsec_yr=33.0, base_flux=120.0, kind="mover"
    )
    seq_m = generate_synthetic_sequence(
        seed=89,
        sources=[anchor, mover],
        ra_center_deg=ra0,
        dec_center_deg=dec0,
        epochs_mjd=[ep0, ep1],
        band_id="B6",
    )
    diff_m = difference_frames([f.fits_bytes for f in seq_m])
    assert diff_m.dipole_favored and not diff_m.monopole_favored
    assert diff_m.delta_bic_mm_over_mv > 10.0

    # 3) Cross-band differencing emits wavelength mismatch warning without crashing.
    f_b2 = generate_synthetic_cutout(
        seed=90,
        sources=[anchor],
        ra_center_deg=ra0,
        dec_center_deg=dec0,
        mjd_tdb_mid=ep0,
        band_id="B2",
    )
    f_b6 = generate_synthetic_cutout(
        seed=91,
        sources=[anchor],
        ra_center_deg=ra0,
        dec_center_deg=dec0,
        mjd_tdb_mid=ep1,
        band_id="B6",
    )
    z_band = zogy_difference(f_b2.fits_bytes, f_b6.fits_bytes)
    assert z_band.wavelength_mismatch
    assert any("Band/wavelength mismatch" in w for w in z_band.warnings)
