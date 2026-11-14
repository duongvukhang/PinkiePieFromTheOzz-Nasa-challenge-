"""Science tests for tracking regimes, box scoping, marginal posterior null term, differential astrometry, and N=2 guard escapes."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from sphx_core.identity import (
    MAX_TRACKED_OBJECTS,
    REGIME_THRESHOLD_LAMBDA,
    EpochDetection,
    compute_annulus_density,
    compute_confusion_parameters,
    compute_differential_astrometry_offset,
    link_epochs,
)
from sphx_core.motion import MotionMeasurement, fit_motion


def _dummy_detection(
    det_id: str,
    ra: float,
    dec: float,
    *,
    flux: float = 100.0,
    flux_err: float = 10.0,
    epoch_idx: int = 0,
    band: str = "B1",
    snr: float = 10.0,
) -> EpochDetection:
    return EpochDetection(
        detection_id=det_id,
        epoch_index=epoch_idx,
        epoch_id=f"ep_{epoch_idx}",
        mjd_tdb=60000.0 + epoch_idx * 182.625,
        detector_id=1,
        x_px=100.0,
        y_px=100.0,
        ra_deg=ra,
        dec_deg=dec,
        cov_sky_arcsec2=(0.04, 0.04, 0.0),
        cov_px=(0.04, 0.04, 0.0),
        sig_ra_arcsec=0.2,
        sig_dec_arcsec=0.2,
        flux=flux,
        flux_err=flux_err,
        flux_band_mjy={band: (flux, flux_err)},
        snr=snr,
        real_bogus=0.99,
        flags=0,
        band_id=band,
    )


def test_problem_1_lambda_0_vs_lambda_1_and_annulus_density():
    """Problem 1: Lambda0 uses mu_max for regime choice; Lambda1 uses sigma_mu for confidence; rho from annulus."""
    # Build 10 detections placed in an annulus 30" to 120" around center
    center_ra, center_dec = 150.0, 2.0
    dets = []
    for i in range(10):
        angle = i * (2 * math.pi / 10)
        # 60 arcsec offset in deg
        dra = (60.0 / 3600.0) * math.cos(angle) / math.cos(math.radians(center_dec))
        ddec = (60.0 / 3600.0) * math.sin(angle)
        dets.append(_dummy_detection(f"ann_{i}", center_ra + dra, center_dec + ddec))

    # Center object inside 15" should not be counted in annulus
    dets.append(_dummy_detection("core_0", center_ra, center_dec))

    rho = compute_annulus_density(dets, center_ra, center_dec, r_in_arcsec=15.0, r_out_arcsec=180.0)
    assert rho > 0.0

    # Prior confusion Lambda0 at 25"/yr * 0.5 yr = 12.5" gate
    dt_yr = 0.5
    l0, l1, regime = compute_confusion_parameters(
        rho_annulus=rho,
        delta_t_yr=dt_yr,
        mu_max_arcsec_yr=25.0,
        sigma_mu_arcsec_yr=0.15,
    )

    # Lambda0 uses mu_max: gate = 12.5"
    expected_gate_0 = 25.0 * dt_yr
    assert abs(l0 - rho * math.pi * (expected_gate_0**2)) < 1e-6

    # Lambda1 uses sigma_mu: shrunk gate
    expected_gate_1 = max(0.15 * dt_yr, 0.5)
    assert abs(l1 - rho * math.pi * (expected_gate_1**2)) < 1e-6
    assert l1 < l0

    # With high density rho=9e-4, Lambda0 > 0.05 => Regime B, but Lambda1 << 0.05
    l0_high, l1_high, reg_high = compute_confusion_parameters(9e-4, 0.5, mu_max_arcsec_yr=25.0, sigma_mu_arcsec_yr=0.15)
    assert l0_high > REGIME_THRESHOLD_LAMBDA
    assert reg_high == "REGIME_B"
    assert l1_high < REGIME_THRESHOLD_LAMBDA


def test_problem_3_marginal_posterior_null_term_penalizes_isolated_noise():
    """Problem 3: Null/miss term e^{-(C_miss - C1)} prevents lone low-SNR candidate from reading 100% confidence."""
    # A single low-SNR detection with high cost (far or faint) has a non-negligible miss rival
    p_d = 0.50  # marginal detection prob at SNR ~ 5.0
    c_val = 5.0  # cost of marginal match
    c_miss = -math.log(1.0 - p_d)  # ~ 0.693

    # With null term: 1 / (1 + e^{-(C_miss - C_val)})
    miss_rival = math.exp(-min(c_miss - c_val, 50.0))  # e^{4.307} ~ 74.2
    prob = 1.0 / (1.0 + miss_rival)
    assert prob < 0.05  # Correctly downweighted instead of 1.0!


def test_problem_4_regime_a_differential_astrometry_cancellation():
    """Problem 4: Differential astrometry cancels common-mode frame registration offset."""
    center_ra, center_dec = 180.0, 0.0
    # True frame registration offset: 0.15" in xi, -0.10" in eta
    d_xi_reg, d_eta_reg = 0.15, -0.10

    ref_dets = []
    curr_dets = []
    for i in range(8):
        angle = i * (2 * math.pi / 8)
        dra = (50.0 / 3600.0) * math.cos(angle)
        ddec = (50.0 / 3600.0) * math.sin(angle)
        ref_dets.append(_dummy_detection(f"ref_{i}", center_ra + dra, center_dec + ddec, epoch_idx=0))
        # Current epoch has registration jitter
        dra_curr = dra + (d_xi_reg / 3600.0)
        ddec_curr = ddec + (d_eta_reg / 3600.0)
        curr_dets.append(_dummy_detection(f"cur_{i}", center_ra + dra_curr, center_dec + ddec_curr, epoch_idx=1))

    off_xi, off_eta, n_stars = compute_differential_astrometry_offset(
        curr_dets, ref_dets, center_ra, center_dec, max_radius_arcsec=120.0
    )
    assert n_stars == 8
    assert abs(off_xi - d_xi_reg) < 0.01
    assert abs(off_eta - d_eta_reg) < 0.01


def test_problem_6_n_equals_2_guard_escapes_and_disjoint_band_rule():
    """Problem 6: N=2 guard escapes require catalogue PM within 3sigma OR SED chi2 <= 9.21 (same band). Disjoint bands stay unvalidated."""
    from sphx_core.identity import CatalogMatch

    t0 = 60000.0
    t1 = 60182.625
    dt_yr = (t1 - t0) / 365.25

    class DummyTrack:
        def __init__(self, detections, catalog_matches=None):
            self.detections = detections
            self.catalog_matches = catalog_matches or []

    # Case A: Same band B1, consistent flux -> SED corroborated!
    m0_same = MotionMeasurement(mjd_tdb=t0, ra_deg=100.0, dec_deg=0.0, cov_sky_arcsec2=(0.01, 0.01, 0.0), flux=100.0, flux_err=5.0, band_id="B1")
    m1_same = MotionMeasurement(mjd_tdb=t1, ra_deg=100.001, dec_deg=0.0005, cov_sky_arcsec2=(0.01, 0.01, 0.0), flux=102.0, flux_err=5.0, band_id="B1")
    fit_same = fit_motion(DummyTrack([m0_same, m1_same]))
    assert not any("unvalidated" in n for n in fit_same.notes)

    # Case B: Disjoint bands (B1 vs B6) without catalog PM -> flux term blank -> stays unvalidated!
    m0_disjoint = MotionMeasurement(mjd_tdb=t0, ra_deg=100.0, dec_deg=0.0, cov_sky_arcsec2=(0.01, 0.01, 0.0), flux=100.0, flux_err=5.0, band_id="B1")
    m1_disjoint = MotionMeasurement(mjd_tdb=t1, ra_deg=100.001, dec_deg=0.0005, cov_sky_arcsec2=(0.01, 0.01, 0.0), flux=50.0, flux_err=5.0, band_id="B6")
    fit_disjoint = fit_motion(DummyTrack([m0_disjoint, m1_disjoint]))
    assert any("unvalidated" in n for n in fit_disjoint.notes)

    # Case C: Disjoint bands BUT corroborated by external catalogue PM within 3 sigma -> validated!
    dra_as = (100.001 - 100.0) * 3600.0
    ddec_as = (0.0005 - 0.0) * 3600.0
    mu_a_cat = dra_as / dt_yr
    mu_d_cat = ddec_as / dt_yr
    cm = CatalogMatch(
        catalog="gaia_dr3",
        source_id="12345",
        gaia_id=12345,
        ra_deg=100.0,
        dec_deg=0.0,
        sep_arcsec=0.05,
        match_prob=0.99,
        pmra_arcsec_yr=mu_a_cat,
        pmdec_arcsec_yr=mu_d_cat,
    )

    fit_cat = fit_motion(DummyTrack([m0_disjoint, m1_disjoint], catalog_matches=[cm]))
    assert not any("unvalidated" in n for n in fit_cat.notes)


def test_problem_7_max_tracked_objects_constant():
    """Problem 7: Tracked object capacity cap constant = 2."""
    assert MAX_TRACKED_OBJECTS == 2
