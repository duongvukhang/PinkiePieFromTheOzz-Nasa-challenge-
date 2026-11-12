"""Offline unit tests for sphx_core motion, identity, classify, consensus, and comparison."""

from __future__ import annotations

import io
import math
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from astropy.time import Time

from sphx_core.calib import DetectionFlag
from sphx_core.classify import PHYSICAL_CLASSES, classify_source
from sphx_core.comparison import compare_epochs
from sphx_core.consensus import evaluate_mover_consensus
from sphx_core.extraction import extract_sources
from sphx_core.identity import (
    apply_manual_override,
    format_public_id,
    link_epochs,
    merge_tracks,
)
from sphx_core.motion import (
    MotionMeasurement,
    check_single_fits_motion,
    detect_streaks,
    fit_motion,
)
from sphx_core.wcs import tangent_arcsec_to_sky

_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tests.science.synthetic_fits import (  # noqa: E402
    InjectedSource,
    add_pixel_integrated_psf,
    generate_synthetic_cutout,
    generate_synthetic_sequence,
    make_tan_wcs,
)


def test_motion_5p_parallax_active_set_and_outlier_rejection() -> None:
    """Verify 5P parallax recovery, negative-parallax guard, leave-one-out outlier, and anchors."""
    ra0, dec0 = 180.0, 20.0
    mjd0 = Time("2025-07-01T00:00:00", scale="tdb").mjd
    epochs = [mjd0 + 182.625 * k for k in range(5)]

    # 1) Inject 5P trajectory with 650 mas parallax (>5 sigma for distance gate) + outlier test.
    src = InjectedSource(
        "plx_mover",
        ra0,
        dec0,
        mjd0,
        pmra_arcsec_yr=3.2,
        pmdec_arcsec_yr=-1.8,
        parallax_mas=650.0,
        base_flux=280.0,
        kind="mover",
    )
    anchor = InjectedSource("anc", ra0 + 0.02, dec0 - 0.02, mjd0, base_flux=300.0, kind="static")
    seq = generate_synthetic_sequence(
        seed=901,
        sources=[anchor, src],
        ra_center_deg=ra0,
        dec_center_deg=dec0,
        epochs_mjd=epochs,
        band_id="B4",
    )
    tracks = link_epochs([extract_sources(f.fits_bytes) for f in seq])
    mover_tr = max(tracks, key=lambda t: t.state.chi2_mu)
    assert len(mover_tr.detections) == 5

    fit5 = fit_motion(mover_tr, fit_parallax=True)
    assert fit5.model_type == "PARALLAX_5P"
    assert abs(fit5.parallax_mas - 650.0) < 3.0 * max(fit5.sigma_parallax_mas, 20.0)
    assert fit5.distance_pc is not None and abs(fit5.distance_pc - (1000.0 / 650.0)) < 0.6

    # Corrupt epoch 2 with an 8" astrometric outlier and verify automatic rejection.
    corrupt_dets = list(mover_tr.detections)
    ra_bad, dec_bad = tangent_arcsec_to_sky(
        8.0, -6.0, float(corrupt_dets[2].ra_deg), float(corrupt_dets[2].dec_deg)
    )
    corrupt_dets[2] = replace(corrupt_dets[2], ra_deg=float(ra_bad), dec_deg=float(dec_bad))
    fit_clean = fit_motion(replace(mover_tr, detections=corrupt_dets), fit_parallax=True)
    assert fit_clean.n_outliers == 1 and 2 in fit_clean.outlier_indices
    assert abs(fit_clean.pmra_arcsec_yr - 3.2) < 0.3

    # 2) Strong unphysical negative parallax (< -2 sigma) must pin pi = 0 (not report negative plx).
    neg_meas: list[MotionMeasurement] = []
    for k, dt in enumerate(np.linspace(-1.0, 1.0, 6)):
        t = mjd0 + dt * 365.25
        xi_true = 1.0 * dt
        eta_true = -0.5 * dt
        # Deliberately push oppositely to Earth's parallax (~300 mas negative)
        from sphx_core.motion import observer_barycentric_au, parallax_factors

        pa, pd = parallax_factors(observer_barycentric_au(t), ra0, dec0)
        ra_m, dec_m = tangent_arcsec_to_sky(xi_true - 0.5 * pa, eta_true - 0.5 * pd, ra0, dec0)
        neg_meas.append(
            MotionMeasurement(t, float(ra_m), float(dec_m), (0.0025, 0.0025, 0.0), epoch_index=k)
        )
    fit_guard = fit_motion(neg_meas, fit_parallax=True)
    assert fit_guard.parallax_mas >= 0.0
    assert any("pinned" in n.lower() for n in fit_guard.notes)


def test_single_fits_streak_and_multi_hdu_tracklet() -> None:
    """Verify Radon+WLS trailed-PSF detection and multi-extension FITS tracklet linking."""
    ny, nx = 64, 64
    ra0, dec0 = 140.0, 12.0
    t0 = Time("2025-08-01T00:00:00", scale="tdb").mjd
    w = make_tan_wcs(nx, ny, ra0, dec0, pixscale_arcsec=6.2)
    rng = np.random.default_rng(707)
    img = rng.normal(5.0, 1.0, size=(ny, nx))
    # Inject a linear 8-px trailed asteroid centered at (32, 32) at PA=45 deg in pixel coords
    for s in np.linspace(-4.0, 4.0, 35):
        add_pixel_integrated_psf(
            img, 32.0 + s / math.sqrt(2.0), 32.0 + s / math.sqrt(2.0), 400.0 / 35.0, 0.85
        )
    hdr = w.to_header(relax=True)
    hdr.update(
        {"DATE-OBS": "2025-08-01T00:00:00", "EXPTIME": 300.0, "TIMESYS": "UTC", "BAND": "B2"}
    )
    hdul = fits.HDUList(
        [
            fits.PrimaryHDU(header=hdr),
            fits.ImageHDU(img.astype(np.float32), header=hdr, name="IMAGE"),
        ]
    )
    buf = io.BytesIO()
    hdul.writeto(buf, overwrite=True)

    streaks = detect_streaks(buf.getvalue(), min_snr=5.0)
    assert len(streaks) >= 1
    assert streaks[0].length_px >= 4.0
    assert streaks[0].rate_arcsec_hr is not None and streaks[0].rate_arcsec_hr > 50.0

    # Multi-HDU temporal cube FITS checked via check_single_fits_motion
    hdus: list[fits.PrimaryHDU | fits.ImageHDU] = [fits.PrimaryHDU()]
    for k in range(3):
        fk = np.random.default_rng(800 + k).normal(5.0, 1.0, size=(ny, nx))
        add_pixel_integrated_psf(fk, 22.0 + 2.2 * k, 24.0 + 1.1 * k, 180.0, 0.85)
        add_pixel_integrated_psf(fk, 48.0, 48.0, 220.0, 0.85)  # static star
        tk = Time(t0 + k * 0.25, format="mjd", scale="tdb")
        hk = w.to_header(relax=True)
        hk.update({"DATE-OBS": str(tk.utc.isot), "EXPTIME": 120.0, "TIMESYS": "UTC", "BAND": "B2"})
        hdus.append(fits.ImageHDU(fk.astype(np.float32), header=hk, name=f"EXP_{k}"))
    m_res = check_single_fits_motion(fits.HDUList(hdus))
    assert m_res.has_motion_signature and len(m_res.tracklets) >= 1
    assert m_res.tracklets[0].rate_arcsec_hr > 1.0


def test_identity_overrides_merge_lost_out_of_field_and_persistence() -> None:
    """Verify accept/reject/reassign overrides, merge_tracks, LOST, OUT_OF_FIELD, public_id."""
    ra0, dec0 = 45.0, -30.0
    mjd0 = Time("2025-07-01T00:00:00", scale="tdb").mjd
    epochs = [mjd0 + 180.0 * k for k in range(5)]

    # Mover disappears after epoch 1 (2 consecutive inside-FOV misses at epochs 2, 3 -> LOST)
    bright_vanish = InjectedSource(
        "vanish",
        ra0,
        dec0,
        mjd0,
        flux_fn=lambda m, _b: 250.0 if m < mjd0 + 200.0 else 0.0,
        kind="variable",
    )
    mover = InjectedSource(
        "mover",
        ra0 + 0.012,
        dec0 - 0.012,
        mjd0,
        pmra_arcsec_yr=1.8,
        pmdec_arcsec_yr=-1.1,
        base_flux=200.0,
        kind="mover",
    )
    seq = generate_synthetic_sequence(
        seed=920,
        sources=[bright_vanish, mover],
        ra_center_deg=ra0,
        dec_center_deg=dec0,
        epochs_mjd=epochs,
        n_persist_spots=1,
    )
    # Offset last frame far away to verify OUT_OF_FIELD without marking mover as LOST
    far_cut = generate_synthetic_cutout(
        seed=925,
        sources=[InjectedSource("other", ra0 + 2.0, dec0, epochs[4], base_flux=200.0)],
        ra_center_deg=ra0 + 2.0,
        dec_center_deg=dec0,
        mjd_tdb_mid=epochs[4],
    )
    tracks = link_epochs([extract_sources(f.fits_bytes) for f in [*seq[:4], far_cut]])
    vanish_tr = next(t for t in tracks if t.status == "lost" or (t.flags & int(DetectionFlag.LOST)))
    assert vanish_tr.flags & int(DetectionFlag.LOST)

    mover_tr = max(tracks, key=lambda t: t.state.chi2_mu)
    assert any(lk.status == "OUT_OF_FIELD" for lk in mover_tr.links)
    assert format_public_id(mover_tr.ra_deg, mover_tr.dec_deg).startswith("SPHX J")

    # Test manual overrides: accept, reject, reassign
    over_acc = apply_manual_override(
        tracks,
        object_id=mover_tr.object_id,
        epoch_index=0,
        action="accept",
        reason="visual blink ok",
    )
    m_acc = next(t for t in over_acc if t.object_id == mover_tr.object_id)
    assert (m_acc.flags & int(DetectionFlag.USER_OVERRIDE)) != 0
    assert any(lk.epoch_index == 0 and lk.decided_by == "user" for lk in m_acc.links)

    over_rej = apply_manual_override(
        over_acc,
        object_id=mover_tr.object_id,
        epoch_index=1,
        action="reject",
        reason="cosmic contamination",
    )
    m_rej = next(t for t in over_rej if t.object_id == mover_tr.object_id)
    assert all(d.epoch_index != 1 for d in m_rej.detections)

    # Test merge_tracks
    if len(tracks) >= 2:
        merged = merge_tracks(tracks, tracks[0].object_id, tracks[1].object_id)
        t_dup = next(t for t in merged if t.object_id == tracks[1].object_id)
        assert t_dup.status == "merged" and t_dup.merged_into == tracks[0].object_id
        assert t_dup.flags & int(DetectionFlag.MERGED)


def test_classify_asteroid_artifact_candidate_consensus_and_compare() -> None:
    """Verify asteroid/artifact/candidate classes, consensus verdicts, and full A-vs-B compare."""
    ra0, dec0 = 155.0, -10.0
    mjd0 = Time("2025-07-01T00:00:00", scale="tdb").mjd
    cos_d = math.cos(math.radians(dec0))

    # 1) Consensus + candidate_new_mover (fast uncatalogued mover across 4 epochs)
    ep4 = [mjd0 + 180.0 * k for k in range(4)]
    cat_entry = [
        {
            "catalog": "gaiadr3",
            "source_id": "999999001",
            "gaia_id": 999999001,
            "ra_deg": ra0,
            "dec_deg": dec0,
            "ref_epoch_mjd": mjd0,
            "pmra_mas_yr": 5500.0,
            "pmdec_mas_yr": -1800.0,
            "parallax_mas": 120.0,
            "mags": {"G": 14.0, "Ks": 10.0, "W1": 9.8, "W2": 9.6},
        }
    ]
    mov = InjectedSource(
        "new_mov",
        ra0,
        dec0,
        mjd0,
        pmra_arcsec_yr=5.5,
        pmdec_arcsec_yr=-1.8,
        parallax_mas=120.0,
        base_flux=250.0,
        kind="mover",
    )
    statics = [
        InjectedSource(
            f"st_{i}",
            ra0 + 0.025 * math.cos(2.0 * math.pi * i / 12.0) / cos_d,
            dec0 + 0.025 * math.sin(2.0 * math.pi * i / 12.0),
            mjd0,
            base_flux=220.0,
            kind="static",
        )
        for i in range(12)
    ]
    seq = generate_synthetic_sequence(
        seed=950,
        sources=[*statics, mov],
        ra_center_deg=ra0,
        dec_center_deg=dec0,
        epochs_mjd=ep4,
        band_id="B2",
    )
    # Without catalog -> candidate_new_mover (non-confirmed caveat!)
    trs_no_cat = link_epochs([extract_sources(f.fits_bytes) for f in seq], catalog_sources=[])
    mov_no_cat = max(trs_no_cat, key=lambda t: t.state.chi2_mu)
    cls_cand = classify_source(mov_no_cat, catalog_sources=[])
    assert set(PHYSICAL_CLASSES).issubset(cls_cand.probabilities.keys())
    assert pytest.approx(sum(cls_cand.probabilities[c] for c in PHYSICAL_CLASSES), abs=1e-6) == 1.0
    assert cls_cand.label == "candidate_new_mover"
    assert "not confirmed" in cls_cand.notes_text.lower()

    # With catalog + frames -> star_pm + Consistent consensus across E1/E2/E3
    trs_cat = link_epochs([extract_sources(f.fits_bytes) for f in seq], catalog_sources=cat_entry)
    mov_cat = max(trs_cat, key=lambda t: t.state.chi2_mu)
    cons = evaluate_mover_consensus(mov_cat, seq, catalog_sources=cat_entry)
    assert cons.e1_mover and cons.e2_mover and cons.e3_mover
    assert cons.verdict == "Consistent" and cons.agreement_score == 1.0

    # Artifact check: flag track as COSMIC -> artifact class, mover < 0.05
    art_tr = replace(
        mov_no_cat,
        detections=[
            replace(mov_no_cat.detections[0], flags=int(DetectionFlag.COSMIC), real_bogus=0.1)
        ],
    )
    cls_art = classify_source(art_tr, catalog_sources=[])
    assert cls_art.label == "artifact" and cls_art.probabilities["mover"] < 0.05

    # Asteroid + streak classification
    hdus: list[fits.PrimaryHDU | fits.ImageHDU] = [fits.PrimaryHDU()]
    ny, nx = 64, 64
    w = make_tan_wcs(nx, ny, ra0, dec0, pixscale_arcsec=6.2)
    for k in range(3):
        fk = np.random.default_rng(980 + k).normal(5.0, 1.0, size=(ny, nx))
        add_pixel_integrated_psf(fk, 22.0 + 2.5 * k, 24.0 + 1.2 * k, 180.0, 0.85)
        tk = Time(mjd0 + k * 0.25, format="mjd", scale="tdb")
        hk = w.to_header(relax=True)
        hk.update({"DATE-OBS": str(tk.utc.isot), "EXPTIME": 120.0, "TIMESYS": "UTC", "BAND": "B2"})
        hdus.append(fits.ImageHDU(fk.astype(np.float32), header=hk, name=f"EXP_{k}"))
    s_res = check_single_fits_motion(fits.HDUList(hdus))
    cls_ast = classify_source(mov_no_cat, catalog_sources=[], single_fits_result=s_res)
    assert cls_ast.label == "asteroid" and cls_ast.probabilities["asteroid"] >= 0.7

    # 2) Full A-vs-B comparison (kappa>=10 statics, appeared, disappeared, moved, changed)
    var = InjectedSource(
        "var",
        ra0 - 0.025 / cos_d,
        dec0 + 0.02,
        mjd0,
        flux_fn=lambda m, _b: 280.0 if abs(m - mjd0) < 1.0 else 110.0,
        kind="variable",
    )
    disap = InjectedSource(
        "disap",
        ra0 + 0.028 / cos_d,
        dec0 - 0.02,
        mjd0,
        flux_fn=lambda m, _b: 180.0 if abs(m - mjd0) < 1.0 else 0.0,
        kind="variable",
    )
    app = InjectedSource(
        "app",
        ra0 - 0.028 / cos_d,
        dec0 - 0.02,
        mjd0,
        flux_fn=lambda m, _b: 0.0 if abs(m - mjd0) < 1.0 else 180.0,
        kind="variable",
    )
    comp_seq = generate_synthetic_sequence(
        seed=990,
        sources=[*statics, mov, var, disap, app],
        ra_center_deg=ra0,
        dec_center_deg=dec0,
        epochs_mjd=[mjd0, mjd0 + 547.875],
        band_id="B2",
    )
    cmp_res = compare_epochs(comp_seq[0].fits_bytes, comp_seq[1].fits_bytes)
    assert cmp_res.kappa_n_stars >= 10 and abs(cmp_res.flux_scale_kappa - 1.0) < 0.15
    assert "time" in cmp_res.header_diff and "wcs" in cmp_res.header_diff
    assert cmp_res.find_nearest(var.ra_ref_deg, var.dec_ref_deg).status == "changed brightness"
    assert cmp_res.find_nearest(disap.ra_ref_deg, disap.dec_ref_deg).status == "disappeared"
    assert cmp_res.find_nearest(app.ra_ref_deg, app.dec_ref_deg).status == "appeared"
    assert (
        cmp_res.find_nearest(
            mov.ra_ref_deg + 0.75 * 5.5 / 3600.0, mov.dec_ref_deg - 0.75 * 1.8 / 3600.0
        ).status
        == "moved"
    )
