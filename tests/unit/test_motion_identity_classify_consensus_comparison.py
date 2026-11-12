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
    UserOverride,
    _predict_at_epoch,
    _seed_tracks,
    _to_epoch_views,
    apply_manual_override,
    format_public_id,
    link_epochs,
    merge_tracks,
)
from sphx_core.motion import (
    MotionMeasurement,
    _expand_multi_hdu_frames,
    check_single_fits_motion,
    detect_streaks,
    fit_motion,
)
from sphx_core.wcs import sky_to_tangent_arcsec, tangent_arcsec_to_sky

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

    alt_det = replace(
        next(d for d in mover_tr.detections if d.epoch_index == 1),
        detection_id="manual_reassign_e1",
    )
    over_reas = apply_manual_override(
        over_rej,
        object_id=mover_tr.object_id,
        epoch_index=1,
        action="reassign",
        new_detection=alt_det,
        reason="manual re-link",
    )
    m_reas = next(t for t in over_reas if t.object_id == mover_tr.object_id)
    assert any(
        d.detection_id == "manual_reassign_e1" and d.epoch_index == 1 for d in m_reas.detections
    )
    assert any(
        lk.epoch_index == 1
        and lk.detection_id == "manual_reassign_e1"
        and lk.status == "LINKED"
        and lk.decided_by == "user"
        for lk in m_reas.links
    )
    assert m_reas.user_locked_pairs[1] == ("reassign", "manual_reassign_e1")
    with pytest.raises(ValueError, match="new_detection"):
        apply_manual_override(
            over_rej, object_id=mover_tr.object_id, epoch_index=1, action="reassign"
        )

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
    assert "unverified" in cls_cand.notes_text.lower()
    assert "confirmed" not in cls_cand.notes_text.lower()

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


def test_seed_high_parallax_predicts_t0_detection() -> None:
    """High-parallax catalogue seed must predict zero tangent offset at its t0 detection."""
    ra0, dec0 = 210.0, -15.0
    mjd0 = Time("2025-07-01T00:00:00", scale="tdb").mjd
    src = InjectedSource("hi_plx", ra0, dec0, mjd0, parallax_mas=750.0, base_flux=250.0)
    cut = generate_synthetic_cutout(
        seed=960, sources=[src], ra_center_deg=ra0, dec_center_deg=dec0, mjd_tdb_mid=mjd0
    )
    cat = [
        {
            "catalog": "gaiadr3",
            "source_id": "9990001",
            "gaia_id": 9990001,
            "ra_deg": ra0,
            "dec_deg": dec0,
            "ref_epoch_mjd": mjd0,
            "pmra_mas_yr": 1500.0,
            "pmdec_mas_yr": -800.0,
            "parallax_mas": 750.0,
            "mags": {"G": 14.0},
        }
    ]
    views = _to_epoch_views([extract_sources(cut.fits_bytes)])
    tracks = _seed_tracks(views, cat, high_pm_mode=False, sigma_mu_0=2.0, max_pm_arcsec_yr=25.0)
    assert len(tracks) == 1
    d0 = views[0].detections[0]
    pred_xy, _, ra_p, dec_p = _predict_at_epoch(tracks[0], views[0])
    assert np.allclose(pred_xy, [0.0, 0.0], atol=1e-9)
    assert (
        math.hypot(*sky_to_tangent_arcsec(ra_p, dec_p, float(d0.ra_deg), float(d0.dec_deg))) < 1e-9
    )


def test_multiband_red_cool_band_uncovered_no_lost() -> None:
    """B5/B6-only faint red mover marks B1-B3 epochs BAND_UNCOVERED without LOST flag."""
    ra0, dec0 = 133.8, -7.24
    mjd0 = Time("2025-07-01T00:00:00", scale="tdb").mjd
    bands = ["B6", "B2", "B2", "B6", "B2", "B6"]
    red_mover = InjectedSource(
        "red_mover",
        ra0,
        dec0,
        mjd0,
        pmra_arcsec_yr=-3.5,
        pmdec_arcsec_yr=0.8,
        flux_fn=lambda _m, b: 180.0 if b == "B6" else 0.0,
        kind="mover",
    )
    anchor = InjectedSource("anc", ra0 + 0.02, dec0 - 0.02, mjd0, base_flux=250.0, kind="static")
    frames = [
        generate_synthetic_cutout(
            seed=970 + k,
            sources=[anchor, red_mover],
            ra_center_deg=ra0,
            dec_center_deg=dec0,
            mjd_tdb_mid=mjd0 + 150.0 * k,
            band_id=band,
        )
        for k, band in enumerate(bands)
    ]
    tracks = link_epochs(
        [extract_sources(f.fits_bytes) for f in frames],
        frames=[f.fits_bytes for f in frames],
        catalog_sources=[],
        high_pm_mode=True,
    )
    mover_tr = max(tracks, key=lambda t: t.state.chi2_mu)
    assert len(mover_tr.detections) == 3
    by_ep = {lk.epoch_index: lk for lk in mover_tr.links}
    assert all(by_ep[e].status == "BAND_UNCOVERED" for e in (1, 2, 4))
    assert (mover_tr.flags & int(DetectionFlag.LOST)) == 0
    assert mover_tr.status == "active"


def test_link_epochs_overrides_and_low_snr_forced() -> None:
    """Verify link_epochs(overrides=...) and LOW_SNR_FORCED via forced photometry."""
    ra0, dec0 = 52.0, 14.0
    mjd0 = Time("2025-07-01T00:00:00", scale="tdb").mjd
    epochs = [mjd0 + 180.0 * k for k in range(3)]
    bright = InjectedSource(
        "bright", ra0 - 0.015, dec0 + 0.015, mjd0, base_flux=250.0, kind="static"
    )
    fader = InjectedSource(
        "fader",
        ra0 + 0.015,
        dec0 - 0.015,
        mjd0,
        flux_fn=lambda m, _b: 11.0 if abs(m - epochs[1]) < 1.0 else 220.0,
        kind="variable",
    )
    seq = generate_synthetic_sequence(
        seed=985,
        sources=[bright, fader],
        ra_center_deg=ra0,
        dec_center_deg=dec0,
        epochs_mjd=epochs,
        band_id="B6",
    )
    exts = [extract_sources(f.fits_bytes) for f in seq]
    raw_frames = [f.fits_bytes for f in seq]
    tracks = link_epochs(exts, frames=raw_frames, catalog_sources=[])
    fader_tr = min(
        (t for t in tracks if len(t.detections) == 3),
        key=lambda t: min(d.snr for d in t.detections),
    )
    lk_e1 = next(lk for lk in fader_tr.links if lk.epoch_index == 1)
    det_e1 = next(d for d in fader_tr.detections if d.epoch_index == 1)
    assert lk_e1.status == "LOW_SNR_FORCED"
    assert det_e1.status == "LOW_SNR_FORCED" and "_forced_" in det_e1.detection_id
    if det_e1.ambiguous_match:
        assert (det_e1.flags & int(DetectionFlag.AMBIGUOUS_MATCH)) != 0

    bright_tr = max(tracks, key=lambda t: min(d.snr for d in t.detections))
    det_idx_e1 = next(d.source_index for d in bright_tr.detections if d.epoch_index == 1)
    rej_tracks = link_epochs(
        exts,
        frames=raw_frames,
        catalog_sources=[],
        overrides=[UserOverride(bright_tr.object_id, 1, "reject", reason="masked")],
    )
    rej_tr = next(t for t in rej_tracks if t.object_id == bright_tr.object_id)
    assert all(d.epoch_index != 1 for d in rej_tr.detections)
    assert rej_tr.user_locked_pairs[1][0] == "reject"

    acc_tracks = link_epochs(
        exts,
        frames=raw_frames,
        catalog_sources=[],
        overrides=[
            UserOverride(bright_tr.object_id, 1, "accept", detection_index=det_idx_e1, reason="ok")
        ],
    )
    acc_tr = next(t for t in acc_tracks if t.object_id == bright_tr.object_id)
    acc_lk = next(lk for lk in acc_tr.links if lk.epoch_index == 1)
    acc_det = next(d for d in acc_tr.detections if d.epoch_index == 1)
    assert acc_lk.decided_by == "user" and acc_lk.match_prob > 0.9999 and acc_lk.delta_c_2nd == 12.0
    assert (acc_det.flags & int(DetectionFlag.USER_OVERRIDE)) != 0


def test_expand_multi_hdu_frames_multiple_2d_science_hdus() -> None:
    """_expand_multi_hdu_frames expands multi-extension 2D science HDUs with distinct epochs."""
    ny, nx = 32, 32
    t0 = Time("2025-08-10T00:00:00", scale="tdb").mjd
    w = make_tan_wcs(nx, ny, 120.0, 25.0, pixscale_arcsec=6.2)
    prim = fits.PrimaryHDU()
    prim.header.update({"EXPTIME": 120.0, "TIMESYS": "UTC", "BAND": "B2"})
    hdus: list[fits.PrimaryHDU | fits.ImageHDU] = [prim]
    for k in range(3):
        hdr = w.to_header(relax=True)
        hdr["DATE-OBS"] = str(Time(t0 + k * 0.25, format="mjd", scale="tdb").utc.isot)
        hdus.append(
            fits.ImageHDU(np.full((ny, nx), 5.0 + k, dtype=np.float32), header=hdr, name=f"SCI_{k}")
        )
    hdus.append(fits.ImageHDU(np.ones((ny, nx), dtype=np.float32), name="VARIANCE"))

    frames = _expand_multi_hdu_frames(fits.HDUList(hdus))
    assert len(frames) == 3
    assert all(fr.shape == (ny, nx) and fr.epoch.exptime_s == 120.0 for fr in frames)
    mjds = [fr.epoch.mjd_tdb_mid for fr in frames]
    assert mjds[0] < mjds[1] < mjds[2]


def test_consensus_e2_independent_pm_cov_maximal_clique_e4_and_conflict() -> None:
    """Verify E2 catalog propagation/PM cov, maximal consistent subset, E4 agreement, Conflict."""
    from sphx_core.identity import EpochDetection, TrackedSource, TrackState

    ra0, dec0 = 160.0, 10.0
    mjd0 = Time("2025-07-01T00:00:00", scale="tdb").mjd
    dets: list[EpochDetection] = []
    for k in range(4):
        dt = k * 0.5
        ra_k, dec_k = tangent_arcsec_to_sky(4.0 * dt, -2.0 * dt, ra0, dec0)
        dets.append(
            EpochDetection(
                detection_id=f"d{k}",
                epoch_index=k,
                epoch_id=f"e{k}",
                mjd_tdb=mjd0 + k * 182.625,
                detector_id=1,
                x_px=30.0,
                y_px=30.0,
                ra_deg=float(ra_k),
                dec_deg=float(dec_k),
                cov_sky_arcsec2=(0.0025, 0.0025, 0.0),
                cov_px=(0.01, 0.01, 0.0),
                sig_ra_arcsec=0.05,
                sig_dec_arcsec=0.05,
                flux=200.0,
                flux_err=5.0,
                flux_band_mjy={"B2": (200.0, 5.0)},
                snr=40.0,
                real_bogus=0.95,
                flags=0,
                band_id="B2",
            )
        )
    st = TrackState(
        object_id="obj_cons",
        label="A",
        ref_epoch_jyear=2025.5,
        ra0_deg=ra0,
        dec0_deg=dec0,
        theta_5=(0.0, 0.0, 4.0, -2.0, 0.0),
        cov_5x5=tuple(tuple(1e-4 if i == j else 0.0 for j in range(5)) for i in range(5)),
        mag_bands={"B2": 14.2},
        sigma_var_mag=0.02,
        model_type="LINEAR_4P",
        chi2_nu=1.0,
        chi2_mu=1000.0,
        delta_bic=500.0,
        vim_r=0.0,
        flags=0,
    )
    tr = TrackedSource(
        object_id="obj_cons",
        label="A",
        public_id=None,
        status="active",
        detections=dets,
        links=[],
        state=st,
        confidence=0.99,
        gaia_id=123456,
    )
    cat = [
        {
            "catalog": "gaiadr3",
            "source_id": "123456",
            "gaia_id": 123456,
            "ra_deg": ra0,
            "dec_deg": dec0,
            "ref_epoch_mjd": mjd0,
            "pmra_mas_yr": 4000.0,
            "pmdec_mas_yr": -2000.0,
            "pmra_err_mas_yr": 25.0,
            "pmdec_err_mas_yr": 35.0,
            "mags": {"G": 14.0, "Ks": 10.0, "W1": 9.8, "W2": 9.6},
        }
    ]
    # Conflicting E4 anchor fit (different PM vector at p < 0.01) excluded from consistent_vecs
    ra_a1, dec_a1 = tangent_arcsec_to_sky(1.0 * 5.0, 1.0 * 5.0, ra0, dec0)
    bad_anchors = [
        {"mjd_tdb": mjd0, "ra_deg": ra0, "dec_deg": dec0, "sigma_arcsec": 0.05},
        {
            "mjd_tdb": mjd0 + 5.0 * 365.25,
            "ra_deg": float(ra_a1),
            "dec_deg": float(dec_a1),
            "sigma_arcsec": 0.05,
        },
    ]
    cons_bad = evaluate_mover_consensus(tr, anchors=bad_anchors, catalog_sources=cat)
    assert cons_bad.e2 is not None and cons_bad.e4 is not None
    assert cons_bad.e2.cov_mu[0][0] == pytest.approx(0.025**2)
    assert cons_bad.e2.cov_mu[1][1] == pytest.approx(0.035**2)
    assert cons_bad.e4_mover is True
    assert any("vector disagreement" in d for d in cons_bad.disagreements)
    # Maximal consistent subset {E2, E3} excludes conflicting E4 (~1.0, 1.0)
    assert abs(cons_bad.consensus_pmra_arcsec_yr - 4.0) < 0.15
    assert abs(cons_bad.consensus_pmdec_arcsec_yr - (-2.0)) < 0.15
    assert cons_bad.verdict == "Conflict"

    # Mixed active_mov (E4 non-mover vs E2, E3 movers) with no explanatory flag -> Conflict
    stat_anchors = [
        {"mjd_tdb": mjd0, "ra_deg": ra0, "dec_deg": dec0, "sigma_arcsec": 0.05},
        {"mjd_tdb": mjd0 + 5.0 * 365.25, "ra_deg": ra0, "dec_deg": dec0, "sigma_arcsec": 0.05},
    ]
    cons_mix = evaluate_mover_consensus(tr, anchors=stat_anchors, catalog_sources=cat)
    assert cons_mix.e4_mover is False and cons_mix.e2_mover and cons_mix.e3_mover
    assert any("Mover/non-mover" in d for d in cons_mix.disagreements)
    assert cons_mix.agreement_score < 1.0
    assert cons_mix.verdict == "Conflict"
    assert set(cons_mix.method_top_classes) >= {"E2", "E3", "E4"}
    assert all(
        c in PHYSICAL_CLASSES or c in ("mover", "static")
        for c in cons_mix.method_top_classes.values()
    )


def test_classify_catwise_gate_mover_renorm_indices_disagreements_and_cap() -> None:
    """Verify CatWISE gating, mover renorm, D.2 indices, method disagreements, and temp cap."""
    from sphx_core.classify import MOVING_CLASSES, SEDPoint, _compute_log_likelihoods
    from sphx_core.identity import EpochDetection, TrackedSource, TrackState

    ra0, dec0 = 133.8, -7.24
    mjd0 = Time("2025-07-01T00:00:00", scale="tdb").mjd
    dets = []
    for k in range(3):
        dt = k * 0.5
        ra_k, dec_k = tangent_arcsec_to_sky(-5.0 * dt, 0.5 * dt, ra0, dec0)
        dets.append(
            EpochDetection(
                detection_id=f"w{k}",
                epoch_index=k,
                epoch_id=f"e{k}",
                mjd_tdb=mjd0 + k * 182.625,
                detector_id=5,
                x_px=30.0,
                y_px=30.0,
                ra_deg=float(ra_k),
                dec_deg=float(dec_k),
                cov_sky_arcsec2=(0.0025, 0.0025, 0.0),
                cov_px=(0.01, 0.01, 0.0),
                sig_ra_arcsec=0.05,
                sig_dec_arcsec=0.05,
                flux=150.0,
                flux_err=5.0,
                flux_band_mjy={"B6": (150.0, 5.0)},
                snr=30.0,
                real_bogus=0.95,
                flags=0,
                band_id="B6",
            )
        )
    st = TrackState(
        object_id="obj_cw",
        label="A",
        ref_epoch_jyear=2025.5,
        ra0_deg=ra0,
        dec0_deg=dec0,
        theta_5=(0.0, 0.0, -5.0, 0.5, 0.0),
        cov_5x5=tuple(tuple(1e-4 if i == j else 0.0 for j in range(5)) for i in range(5)),
        mag_bands={"B6": 14.5},
        sigma_var_mag=0.02,
        model_type="LINEAR_4P",
        chi2_nu=1.0,
        chi2_mu=1200.0,
        delta_bic=600.0,
        vim_r=0.0,
        flags=0,
    )
    tr = TrackedSource(
        object_id="obj_cw",
        label="A",
        public_id=None,
        status="active",
        detections=dets,
        links=[],
        state=st,
        confidence=0.99,
        gaia_id=None,
    )
    # 1) CatWISE match (no Gaia ID) at match_prob >= 0.9 must gate out candidate_new_mover
    cat_cw = [
        {
            "catalog": "catwise2020",
            "source_id": "CW0855",
            "gaia_id": None,
            "ra_deg": ra0,
            "dec_deg": dec0,
            "ref_epoch_mjd": mjd0,
            "pmra_mas_yr": -5000.0,
            "pmdec_mas_yr": 500.0,
            "mags": {"W1": 16.5, "W2": 13.7},
        }
    ]
    cls_cw = classify_source(tr, catalog_sources=cat_cw)
    assert cls_cw.label != "candidate_new_mover"
    assert cls_cw.probabilities["candidate_new_mover"] < 0.01

    # 2) Clamped mover probability must rescale MOVING_CLASSES and renormalize PHYSICAL_CLASSES
    tr_1ep = replace(tr, detections=dets[:1])
    cls_1ep = classify_source(tr_1ep, catalog_sources=[])
    assert pytest.approx(sum(cls_1ep.probabilities[c] for c in PHYSICAL_CLASSES), abs=1e-6) == 1.0
    assert cls_1ep.probabilities["mover"] <= 0.05 + 1e-9
    assert cls_1ep.probabilities["mover"] == pytest.approx(
        sum(cls_1ep.probabilities[c] for c in MOVING_CLASSES), abs=1e-9
    )
    assert cls_1ep.label not in MOVING_CLASSES
    assert cls_1ep.top_prob < 0.99

    # 3) D.2 spectral indices: significant narrow-band CH4 trough triggers, noisy does not
    feat_sig = {
        "mfit": None,
        "all_saturated": False,
        "bad_art": False,
        "n_epochs": 3,
        "matches": [],
        "gaia_id": None,
        "sed_points": [
            SEDPoint(3.32, 0.15, None, 10.0, 2.0, mjd0, 0),
            SEDPoint(4.00, 0.20, None, 80.0, 4.0, mjd0, 0),
            SEDPoint(4.65, 0.40, None, 90.0, 4.0, mjd0, 0),
            SEDPoint(1.50, 0.40, None, 15.0, 2.0, mjd0, 0),
        ],
        "chi2_var_nu": 1.0,
        "amp_mag": 0.0,
        "period_days": None,
        "monopole_favored": False,
        "dipole_favored": False,
        "max_diff_snr": 0.0,
        "flags": 0,
        "elong_med": 1.0,
        "rb_med": 0.9,
        "is_transient": False,
    }
    _, ev_sig, sub_sig = _compute_log_likelihoods(feat_sig, None, False)
    assert sub_sig == "T" and any("CH4 index" in e for e in ev_sig)
    assert any("4.5 um excess" in e for e in ev_sig)

    feat_noisy = dict(feat_sig)
    feat_noisy["sed_points"] = [
        SEDPoint(3.32, 0.15, None, 10.0, 12.0, mjd0, 0),
        SEDPoint(4.00, 0.20, None, 25.0, 15.0, mjd0, 0),
    ]
    _, ev_noisy, sub_noisy = _compute_log_likelihoods(feat_noisy, None, False)
    assert sub_noisy is None and not any("CH4" in e for e in ev_noisy)

    # 4) Cross-method disagreement detected between sed (BD) and motion (static)
    dets_stat = [replace(d, ra_deg=ra0, dec_deg=dec0) for d in dets]
    tr_conflict = replace(tr, detections=dets_stat)
    cls_conf = classify_source(
        tr_conflict,
        catalog_sources=[
            {
                "catalog": "catwise2020",
                "source_id": "CW_STAT",
                "ra_deg": ra0,
                "dec_deg": dec0,
                "ref_epoch_mjd": mjd0,
                "mags": {"W1": 16.5, "W2": 13.0},
            }
        ],
    )
    assert any("sed favors" in d and "motion favors" in d for d in cls_conf.disagreements)

    # 5) Planet X candidate notes must never contain "confirmed"
    cls_px = classify_source(tr, catalog_sources=[], is_planet_x_candidate=True)
    assert cls_px.label == "candidate_planet_x"
    assert "unverified" in cls_px.notes_text.lower()
    assert "confirmed" not in cls_px.notes_text.lower()


def test_refit_pm_outlier_and_hungarian_masked_pixel() -> None:
    """Verify _refit_track_state PM_OUTLIER emission and _solve_epoch_hungarian MASKED link."""
    from sphx_core.calib import DetectionFlag
    from sphx_core.identity import (
        EpochDetection,
        _refit_track_state,
        _solve_epoch_hungarian,
        _TrackProto,
    )
    from sphx_core.motion import as_frame_data

    ra0, dec0 = 120.0, 15.0
    mjd0 = Time("2025-07-01T00:00:00", scale="tdb").mjd
    # 1) _refit_track_state removes PM outlier from links_by_epoch and emits PM_OUTLIER EpochLink
    tr = _TrackProto(
        object_id="obj_out",
        label="A",
        t0_mjd=mjd0,
        ra0_deg=ra0,
        dec0_deg=dec0,
        theta=np.array([0.0, 0.0, 2.0, -1.0, 0.0]),
        cov=np.diag([0.01, 0.01, 0.1, 0.1, 0.01]),
    )
    for k in range(5):
        dt = k * 0.5
        dx = 2.0 * dt + (4.0 if k == 2 else 0.0)
        dy = -1.0 * dt - (3.5 if k == 2 else 0.0)
        ra_k, dec_k = tangent_arcsec_to_sky(dx, dy, ra0, dec0)
        det = EpochDetection(
            detection_id=f"e{k}_s0",
            epoch_index=k,
            epoch_id=f"epoch_{k}",
            mjd_tdb=mjd0 + k * 182.625,
            detector_id=1,
            x_px=30.0,
            y_px=30.0,
            ra_deg=float(ra_k),
            dec_deg=float(dec_k),
            cov_sky_arcsec2=(0.0025, 0.0025, 0.0),
            cov_px=(0.01, 0.01, 0.0),
            sig_ra_arcsec=0.05,
            sig_dec_arcsec=0.05,
            flux=200.0,
            flux_err=5.0,
            flux_band_mjy={"B2": (200.0, 5.0)},
            snr=40.0,
            real_bogus=0.95,
            flags=0,
            band_id="B2",
        )
        tr.links_by_epoch[k] = (det, -3.0, 8.0, 0.99, (0.0, 0.0), "auto")

    _refit_track_state(tr, fit_parallax=False)
    assert 2 not in tr.links_by_epoch
    assert (2, "e2_s0") in tr.excluded_dets
    assert tr.non_detect_links[2].status == "PM_OUTLIER"
    assert tr.non_detect_links[2].detection_id == "e2_s0"
    assert len(tr.links_by_epoch) == 4

    # 2) _solve_epoch_hungarian emits MASKED with zero miss penalty when pixel has HARD_BAD_MASK
    cut0 = generate_synthetic_cutout(
        seed=991,
        sources=[InjectedSource("s", ra0, dec0, mjd0, base_flux=250.0)],
        ra_center_deg=ra0,
        dec_center_deg=dec0,
        mjd_tdb_mid=mjd0,
        band_id="B2",
    )
    cut1 = generate_synthetic_cutout(
        seed=992,
        sources=[],
        ra_center_deg=ra0,
        dec_center_deg=dec0,
        mjd_tdb_mid=mjd0 + 180.0,
        band_id="B2",
    )
    fr0 = as_frame_data(cut0.fits_bytes)
    fr1 = as_frame_data(cut1.fits_bytes)
    x_pred, y_pred = (float(v) for v in fr1.wcs.world_to_pixel_values(ra0, dec0))
    flags1 = np.zeros(fr1.shape, dtype=np.int32) if fr1.flags is None else fr1.flags.copy()
    flags1[int(round(y_pred)), int(round(x_pred))] |= int(DetectionFlag.BAD_PIX)
    fr1 = replace(fr1, flags=flags1)

    views = _to_epoch_views([extract_sources(fr0), extract_sources(fr1)], frames=[fr0, fr1])
    tr_m = _TrackProto(
        object_id="obj_mask",
        label="A",
        t0_mjd=mjd0,
        ra0_deg=ra0,
        dec0_deg=dec0,
        theta=np.array([0.0, 0.0, 0.0, 0.0, 0.0]),
        cov=np.diag([0.01, 0.01, 0.01, 0.01, 0.001]),
    )
    res = _solve_epoch_hungarian([tr_m], views[1])
    assert res == {}
    lk_mask = tr_m.non_detect_links[1]
    assert lk_mask.status == "MASKED" and lk_mask.cost_ll == 0.0 and lk_mask.detection_id is None

    # End-to-end link_epochs also preserves MASKED without flagging LOST
    cut2 = generate_synthetic_cutout(
        seed=993,
        sources=[InjectedSource("s", ra0, dec0, mjd0 + 360.0, base_flux=250.0)],
        ra_center_deg=ra0,
        dec_center_deg=dec0,
        mjd_tdb_mid=mjd0 + 360.0,
        band_id="B2",
    )
    fr2 = as_frame_data(cut2.fits_bytes)
    tracks = link_epochs([fr0, fr1, fr2])
    assert len(tracks) == 1
    assert any(
        lk.epoch_index == 1 and lk.status == "MASKED" and lk.cost_ll == 0.0
        for lk in tracks[0].links
    )
    assert (tracks[0].flags & int(DetectionFlag.LOST)) == 0
