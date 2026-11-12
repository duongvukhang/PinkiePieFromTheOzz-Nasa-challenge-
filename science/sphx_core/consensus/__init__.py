"""Multi-estimator cross-validation and inverse-covariance motion consensus (§F.1-F.2)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.stats import chi2 as chi2_dist

from sphx_core.calib import HARD_BAD_MASK, DetectionFlag
from sphx_core.classify import classify_source
from sphx_core.difference import difference_frames
from sphx_core.identity import TrackedSource, crossmatch_catalog
from sphx_core.motion import MotionFitResult, as_frame_data, fit_motion
from sphx_core.wcs import local_wcs_jacobian, sky_to_tangent_arcsec


@dataclass(frozen=True)
class EstimatorVector:
    """Single estimator proper-motion estimate `(mu_a*, mu_d)` [\"/yr] and 2x2 covariance."""

    estimator_id: str
    is_mover: bool
    pmra_arcsec_yr: float
    pmdec_arcsec_yr: float
    cov_mu: tuple[tuple[float, float], tuple[float, float]]
    independent_of_pixels: bool = True
    notes: str = ""


@dataclass(frozen=True)
class ConsensusResult:
    """Multi-method cross-validation verdict across E1..E8 estimators (§F.2)."""

    e1_mover: bool
    e2_mover: bool
    e3_mover: bool
    e1: EstimatorVector | None
    e2: EstimatorVector | None
    e3: EstimatorVector | None
    e4: EstimatorVector | None
    e6_class: str
    consensus_pmra_arcsec_yr: float
    consensus_pmdec_arcsec_yr: float
    consensus_mu_arcsec_yr: float
    consensus_sigma_mu_arcsec_yr: float
    consensus_pa_deg: float
    pairwise_chi2: dict[str, tuple[float, float]]
    disagreements: list[str]
    agreement_score: float
    verdict: str
    notes: list[str] = field(default_factory=list)


def _pair_consistency(a: EstimatorVector, b: EstimatorVector) -> tuple[float, float]:
    da = np.array(
        [a.pmra_arcsec_yr - b.pmra_arcsec_yr, a.pmdec_arcsec_yr - b.pmdec_arcsec_yr],
        dtype=np.float64,
    )
    c_sum = np.asarray(a.cov_mu, dtype=np.float64) + np.asarray(b.cov_mu, dtype=np.float64)
    chi2_val = float(da @ np.linalg.pinv(c_sum) @ da)
    return chi2_val, float(chi2_dist.sf(chi2_val, df=2))


def _mu_cov2(mfit: MotionFitResult) -> tuple[tuple[float, float], tuple[float, float]]:
    return (
        (
            (float(mfit.cov_params[2][2]), float(mfit.cov_params[2][3])),
            (float(mfit.cov_params[3][2]), float(mfit.cov_params[3][3])),
        )
        if len(mfit.cov_params) >= 4
        else ((1.0, 0.0), (0.0, 1.0))
    )


def evaluate_mover_consensus(
    track: TrackedSource,
    frames: Sequence[Any] | None = None,
    *,
    anchors: Sequence[Any] | None = None,
    catalog_sources: Sequence[Any] | None = None,
) -> ConsensusResult:
    """Evaluate E1 (dipole), E2 (catalog PM), E3 (WLS PM), E4, E6 consensus (§F.1-F.2)."""
    notes: list[str] = []

    # E3: Centroid WLS motion fit (§C.3-C.4)
    mfit: MotionFitResult = fit_motion(track)
    e3 = EstimatorVector(
        "E3", bool(mfit.is_mover), mfit.pmra_arcsec_yr, mfit.pmdec_arcsec_yr, _mu_cov2(mfit)
    )

    # E2: Independent external catalog prediction vs SPHEREx observations
    matches = list(track.catalog_matches)
    if not matches:
        matches = crossmatch_catalog(
            mfit.ra_ref_deg,
            mfit.dec_ref_deg,
            mfit.ref_epoch_mjd_tdb,
            max_radius_arcsec=3.0,
            catalog_sources=catalog_sources,
        )
    cat = next((m for m in matches if m.match_prob >= 0.8 and m.pmra_arcsec_yr is not None), None)
    e2: EstimatorVector | None = None
    if cat is not None and cat.pmra_arcsec_yr is not None and cat.pmdec_arcsec_yr is not None:
        cat_mu = math.hypot(cat.pmra_arcsec_yr, cat.pmdec_arcsec_yr)
        # Verify catalog trajectory agrees with observed positions while stationary null does not.
        clean_dets = [
            d
            for d in track.detections
            if not (d.flags & int(HARD_BAD_MASK)) and d.ra_deg is not None and d.dec_deg is not None
        ]
        null_chi2 = 0.0
        cat_resid_chi2 = 0.0
        for d in clean_dets:
            if d.ra_deg is None or d.dec_deg is None:
                continue
            dt_d = (d.mjd_tdb - mfit.ref_epoch_mjd_tdb) / 365.25
            xi_o, eta_o = sky_to_tangent_arcsec(
                d.ra_deg, d.dec_deg, mfit.ra_ref_deg, mfit.dec_ref_deg
            )
            sig = max(d.sig_ra_arcsec, d.sig_dec_arcsec, 0.05)
            dx_c = float(xi_o) - cat.pmra_arcsec_yr * dt_d
            dy_c = float(eta_o) - cat.pmdec_arcsec_yr * dt_d
            cat_resid_chi2 += (dx_c**2 + dy_c**2) / (sig**2)
            null_chi2 += (float(xi_o) ** 2 + float(eta_o) ** 2) / (sig**2)
        e2_ok = (
            cat_mu > 0.05
            and (null_chi2 > 25.0)
            and (cat_resid_chi2 / max(len(clean_dets), 1) < 12.0)
        )
        e2_sig = 0.02 if cat.gaia_id is not None else 0.15
        e2 = EstimatorVector(
            estimator_id="E2",
            is_mover=e2_ok,
            pmra_arcsec_yr=float(cat.pmra_arcsec_yr),
            pmdec_arcsec_yr=float(cat.pmdec_arcsec_yr),
            cov_mu=((e2_sig**2, 0.0), (0.0, e2_sig**2)),
            notes=f"{cat.catalog}:{cat.source_id}",
        )

    # E1: Pixel-level difference / dipole check independent of centroid catalogs (§B.5, §B.8)
    e1: EstimatorVector | None = None
    if frames and len(frames) >= 2:
        loaded = [as_frame_data(f) for f in frames]
        diff = difference_frames(loaded)
        # Locate strongest dipole/difference test near track path with longest baseline
        near_pairs = [
            (pr, st)
            for pr in diff.pair_results
            for st in pr.stamp_tests
            if st.dipole_favored
            and abs(st.snr_corr) >= 4.5
            and any(
                d.ra_deg is not None
                and d.dec_deg is not None
                and math.hypot(
                    *sky_to_tangent_arcsec(st.ra_deg, st.dec_deg, float(d.ra_deg), float(d.dec_deg))
                )
                < 18.0
                for d in track.detections
            )
        ]
        if near_pairs:
            # Estimate tangent-plane PM vector from longest-baseline dipole (>= 0.15 px shift)
            pr_match, best_st = max(
                near_pairs,
                key=lambda ps: (
                    round(abs(ps[0].sci_mjd_tdb - ps[0].ref_mjd_tdb), 1),
                    ps[1].displacement_px >= 0.15,
                    ps[1].displacement_px * abs(ps[1].snr_corr),
                ),
            )
            dt_raw = (pr_match.sci_mjd_tdb - pr_match.ref_mjd_tdb) / 365.25
            dt_pair_yr = dt_raw if abs(dt_raw) > 1e-3 else 1e-3
            jac = local_wcs_jacobian(pr_match.wcs, best_st.x_px, best_st.y_px)
            dp_px = np.array(
                [
                    best_st.x1_dipole_px - best_st.x0_dipole_px,
                    best_st.y1_dipole_px - best_st.y0_dipole_px,
                ],
                dtype=np.float64,
            )
            d_sky = jac @ dp_px
            pm_e1 = d_sky / dt_pair_yr
            pixscale = float(np.sqrt(abs(np.linalg.det(jac))))
            sig_e1 = max(best_st.displacement_sigma_px * pixscale / abs(dt_pair_yr), 0.25)
            e1 = EstimatorVector(
                estimator_id="E1",
                is_mover=True,
                pmra_arcsec_yr=float(pm_e1[0]),
                pmdec_arcsec_yr=float(pm_e1[1]),
                cov_mu=((sig_e1**2, 0.0), (0.0, sig_e1**2)),
                notes=f"dipole SNR={best_st.snr_corr:.1f}, disp={best_st.displacement_px:.2f}px",
            )
        else:
            e1 = EstimatorVector(
                "E1", False, 0.0, 0.0, ((1.0, 0.0), (0.0, 1.0)), notes="No dipole > threshold"
            )

    # E4: Anchor-only PM fit (independent of SPHEREx images)
    e4: EstimatorVector | None = None
    if anchors and len(anchors) >= 2:
        fit_a = fit_motion(anchors, fit_parallax=False)
        e4 = EstimatorVector(
            "E4", fit_a.is_mover, fit_a.pmra_arcsec_yr, fit_a.pmdec_arcsec_yr, _mu_cov2(fit_a)
        )

    # Pairwise vector chi2 consistency checks (p < 0.01 disagreement)
    est_list = [ev for ev in (e1, e2, e3, e4) if ev is not None and ev.is_mover]
    pw_chi2: dict[str, tuple[float, float]] = {}
    disagreements: list[str] = []
    consistent_vecs: list[EstimatorVector] = []
    for i, ei in enumerate(est_list):
        consistent_vecs.append(ei)
        for ej in est_list[i + 1 :]:
            c2, p_val = _pair_consistency(ei, ej)
            pw_chi2[f"{ei.estimator_id}-{ej.estimator_id}"] = (c2, p_val)
            if p_val < 0.01:
                disagreements.append(
                    f"{ei.estimator_id} vs {ej.estimator_id} vector disagreement (p={p_val:.3e})"
                )

    # Inverse-covariance weighted consensus of consistent motion vectors
    if consistent_vecs:
        info = np.zeros((2, 2), dtype=np.float64)
        rhs = np.zeros(2, dtype=np.float64)
        for ev in consistent_vecs:
            w = np.linalg.pinv(np.asarray(ev.cov_mu, dtype=np.float64))
            info += w
            rhs += w @ np.array([ev.pmra_arcsec_yr, ev.pmdec_arcsec_yr], dtype=np.float64)
        c_cons: NDArray[np.float64] = np.linalg.pinv(info)
        mu_cons_vec = c_cons @ rhs
        pmra_c, pmdec_c = float(mu_cons_vec[0]), float(mu_cons_vec[1])
        mu_c = math.hypot(pmra_c, pmdec_c)
        if mu_c > 1e-9:
            g = np.array([pmra_c / mu_c, pmdec_c / mu_c], dtype=np.float64)
            sig_mu_c = math.sqrt(max(float(g @ c_cons @ g), 1e-12))
        else:
            sig_mu_c = math.sqrt(max(float(0.5 * np.trace(c_cons)), 1e-12))
        pa_c = math.degrees(math.atan2(pmra_c, pmdec_c)) % 360.0
    else:
        pmra_c, pmdec_c, mu_c, sig_mu_c, pa_c = (
            mfit.pmra_arcsec_yr,
            mfit.pmdec_arcsec_yr,
            mfit.mu_arcsec_yr,
            mfit.sigma_mu_arcsec_yr,
            mfit.pa_deg,
        )

    cls = classify_source(track, catalog_sources=catalog_sources)
    active_mov = [ev.is_mover for ev in (e1, e2, e3) if ev is not None]
    n_agree = sum(int(v == (cls.probabilities.get("mover", 0.0) >= 0.5)) for v in active_mov)
    agreement = n_agree / max(len(active_mov), 1)

    if len(disagreements) == 0 and (all(active_mov) or not any(active_mov)):
        verdict = "Consistent"
    elif len(disagreements) <= 1 and (
        track.flags & int(DetectionFlag.SATURATED | DetectionFlag.BLENDED)
    ):
        verdict = "Tension"
    else:
        verdict = "Conflict" if disagreements else "Tension"

    return ConsensusResult(
        e1_mover=bool(e1.is_mover) if e1 is not None else False,
        e2_mover=bool(e2.is_mover) if e2 is not None else False,
        e3_mover=bool(e3.is_mover),
        e1=e1,
        e2=e2,
        e3=e3,
        e4=e4,
        e6_class=cls.label,
        consensus_pmra_arcsec_yr=pmra_c,
        consensus_pmdec_arcsec_yr=pmdec_c,
        consensus_mu_arcsec_yr=mu_c,
        consensus_sigma_mu_arcsec_yr=sig_mu_c,
        consensus_pa_deg=pa_c,
        pairwise_chi2=pw_chi2,
        disagreements=disagreements,
        agreement_score=agreement,
        verdict=verdict,
        notes=notes,
    )


__all__ = [
    "ConsensusResult",
    "EstimatorVector",
    "evaluate_mover_consensus",
]
