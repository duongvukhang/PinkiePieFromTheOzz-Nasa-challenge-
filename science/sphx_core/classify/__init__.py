"""Probabilistic multi-evidence astrophysical classification and SED/evidence summary (§D)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord

from sphx_core.calib import BAND_GROUPS, HARD_BAD_MASK, DetectionFlag
from sphx_core.difference import DifferenceResult, StampTestResult, difference_frames
from sphx_core.extraction import extract_sources
from sphx_core.identity import CatalogMatch, EpochDetection, TrackedSource, crossmatch_catalog
from sphx_core.motion import (
    MotionFitResult,
    MotionMeasurement,
    as_frame_data,
    fit_motion,
    forced_photometry_at_sky,
    measure_variability_period,
    weighted_flux_variability,
)

PHYSICAL_CLASSES: tuple[str, ...] = (
    "star_pm",
    "star_static",
    "brown_dwarf",
    "white_dwarf",
    "galaxy",
    "quasar",
    "asteroid",
    "comet",
    "variable_star",
    "transient",
    "artifact",
    "candidate_new_mover",
    "candidate_planet_x",
)

MOVING_CLASSES: frozenset[str] = frozenset(
    {
        "star_pm",
        "brown_dwarf",
        "white_dwarf",
        "asteroid",
        "comet",
        "candidate_new_mover",
        "candidate_planet_x",
    }
)

# Log-likelihood weights correcting for partial correlation between methods (§D.6).
CLASS_WEIGHTS: dict[str, float] = {
    "sed": 0.90,
    "motion": 1.00,
    "var": 0.90,
    "morph": 0.85,
    "xmatch": 0.85,
}


@dataclass(frozen=True)
class SEDPoint:
    """Single photometric point in on-the-fly spectral energy distribution (§D.2)."""

    wavelength_um: float
    delta_lambda_um: float
    band_id: str | None
    flux: float
    flux_err: float
    mjd_tdb: float
    flags: int
    is_upper_limit: bool = False


@dataclass(frozen=True)
class ClassificationResult:
    """Calibrated posterior probabilities, subtype, evidence features, and caveats (§D.6)."""

    label: str
    probabilities: dict[str, float]
    spectral_subtype: str | None
    evidence: list[str]
    method_contributions: dict[str, dict[str, float]] = field(default_factory=dict)
    disagreements: list[str] = field(default_factory=list)
    flags: int = 0
    notes: list[str] = field(default_factory=list)
    sed_points: list[SEDPoint] = field(default_factory=list)
    temporal_spread_days: float = 0.0

    @property
    def top_class(self) -> str:
        """Primary predicted physical class label."""
        return self.label

    @property
    def top_prob(self) -> float:
        """Posterior probability of `label`."""
        return float(self.probabilities.get(self.label, 0.0))

    @property
    def notes_text(self) -> str:
        """Joined diagnostic notes and honesty caveats."""
        return "; ".join(self.notes)


def _flux_to_mag(flux: float) -> float:
    return -2.5 * math.log10(max(flux, 1e-6)) + 20.0


def _galactic_priors(ra_deg: float | None, dec_deg: float | None) -> dict[str, float]:
    """Return weakly informative log-priors pi(c | l, b) based on Galactic latitude (§D.6)."""
    if ra_deg is None or dec_deg is None:
        abs_b = 30.0
    else:
        abs_b = abs(
            float(SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs").galactic.b.deg)
        )
    plane_boost = 0.35 if abs_b < 15.0 else (-0.15 if abs_b > 45.0 else 0.0)
    extra_gal_boost = 0.25 if abs_b > 35.0 else -0.35
    return {
        "star_pm": 0.1 + plane_boost,
        "star_static": 0.4 + plane_boost,
        "brown_dwarf": -0.6,
        "white_dwarf": -1.2,
        "galaxy": -0.4 + extra_gal_boost,
        "quasar": -0.9 + extra_gal_boost,
        "asteroid": -1.0,
        "comet": -1.8,
        "variable_star": -0.4 + 0.5 * plane_boost,
        "transient": -1.6,
        "artifact": -0.8,
        "candidate_new_mover": -1.4,
        "candidate_planet_x": -8.0,
    }


def _to_sed_point(
    band: str | None,
    flux: float | None,
    flux_err: float | None,
    mjd_tdb: float,
    flags: int,
    *,
    is_upper_limit: bool = False,
    snr: float = 0.0,
) -> SEDPoint:
    lo, hi = BAND_GROUPS.get(band or "", (2.0, 3.0))
    fl = float(flux or 0.0)
    fe_sys = math.hypot(
        max(float(flux_err or 1.0), 1e-9), 0.03 * abs(fl)
    )  # §D.2: 3% systematic floor
    return SEDPoint(
        wavelength_um=0.5 * (lo + hi),
        delta_lambda_um=hi - lo,
        band_id=band,
        flux=fl,
        flux_err=fe_sys,
        mjd_tdb=float(mjd_tdb),
        flags=int(flags),
        is_upper_limit=bool(is_upper_limit or (fl < 3.0 * fe_sys and snr < 3.0)),
    )


def _build_sed(
    detections: Sequence[EpochDetection], forced: Sequence[EpochDetection]
) -> tuple[list[SEDPoint], float]:
    points = [
        _to_sed_point(
            d.band_id,
            d.flux,
            d.flux_err,
            d.mjd_tdb,
            d.flags,
            is_upper_limit=d.is_upper_limit,
            snr=d.snr,
        )
        for d in (*detections, *forced)
    ]
    mjds = [p.mjd_tdb for p in points]
    return points, (float(max(mjds) - min(mjds)) if len(mjds) >= 2 else 0.0)


def _band_flux_summary(
    points: Sequence[SEDPoint],
) -> tuple[dict[str, tuple[float, float, float]], bool, bool]:
    """Return weighted mean `(flux, err, snr)` per band group + `(has_optical, has_ir)` flags."""
    by_b: dict[str, list[SEDPoint]] = {}
    for p in points:
        if p.band_id and not (p.flags & int(DetectionFlag.SATURATED)):
            by_b.setdefault(p.band_id, []).append(p)
    out: dict[str, tuple[float, float, float]] = {}
    for b, pts in by_b.items():
        w = np.array([1.0 / (max(pt.flux_err, 1e-9) ** 2) for pt in pts], dtype=np.float64)
        f = np.array([pt.flux for pt in pts], dtype=np.float64)
        w_sum = float(np.sum(w))
        f_mean = float(np.sum(w * f) / max(w_sum, 1e-12))
        e_mean = 1.0 / math.sqrt(max(w_sum, 1e-12))
        out[b] = (f_mean, e_mean, f_mean / max(e_mean, 1e-9))

    sat_opt = any(
        p.band_id in ("B1", "B2", "B3") and (p.flags & int(DetectionFlag.SATURATED)) for p in points
    )
    det_opt = sat_opt or any(b in ("B1", "B2", "B3") and out[b][2] >= 4.0 for b in out)
    det_ir = any(b in ("B5", "B6") and out[b][2] >= 4.0 for b in out)
    return out, det_opt, det_ir


def _assess_features_from_track(
    track: TrackedSource, catalog_sources: Sequence[Any] | None
) -> dict[str, Any]:
    dets = list(track.detections)
    forced = list(getattr(track, "forced_measurements", ()))
    all_dets = [*dets, *forced]
    flags_all = int(track.flags)
    for d in all_dets:
        flags_all |= int(d.flags)

    non_sat_dets = [d for d in dets if not (d.flags & int(DetectionFlag.SATURATED))]
    all_saturated = (
        bool(dets) and not non_sat_dets and bool(flags_all & int(DetectionFlag.SATURATED))
    )
    bad_art_bits = int(
        DetectionFlag.COSMIC
        | DetectionFlag.PERSIST
        | DetectionFlag.BAD_PIX
        | DetectionFlag.GHOST_SPIKE
    )

    # Fit motion on clean non-bad detections if >= 2 exist
    mfit: MotionFitResult | None = None
    try:
        if len(dets) >= 2:
            mfit = fit_motion(track)
    except ValueError:
        mfit = None

    ra_ref = mfit.ra_ref_deg if mfit is not None else (dets[0].ra_deg if dets else track.ra_deg)
    dec_ref = mfit.dec_ref_deg if mfit is not None else (dets[0].dec_deg if dets else track.dec_deg)
    t_ref = mfit.ref_epoch_mjd_tdb if mfit is not None else (dets[0].mjd_tdb if dets else 60857.0)

    matches = list(track.catalog_matches)
    if not matches and ra_ref is not None and dec_ref is not None:
        matches = crossmatch_catalog(
            ra_ref,
            dec_ref,
            t_ref,
            sigma_pos_arcsec=0.35,
            max_radius_arcsec=3.0,
            catalog_sources=catalog_sources,
        )
    gaia_id = track.gaia_id or next(
        (m.gaia_id for m in matches if m.gaia_id is not None and m.match_prob >= 0.8), None
    )

    # Photometric variability across dominant unsaturated band group
    chi2_var, chi2_var_nu, amp_mag = 0.0, 0.0, 0.0
    period_days: float | None = None
    period_fap = 1.0
    by_b: dict[str, list[EpochDetection]] = {}
    for d in non_sat_dets:
        if (
            d.flux is not None
            and d.flux_err is not None
            and d.flux_err > 0.0
            and not (d.flags & bad_art_bits)
        ):
            by_b.setdefault(d.band_id or "ALL", []).append(d)
    if by_b:
        best_grp = max(by_b.values(), key=len)
        chi2_var, chi2_var_nu, amp_mag = weighted_flux_variability(
            [float(d.flux or 0.0) for d in best_grp],
            [float(d.flux_err or 1.0) for d in best_grp],
            sys_frac=0.03,
        )
        if len(best_grp) >= 8 and chi2_var_nu > 3.0:
            try:
                per = measure_variability_period(track)
                period_days, period_fap = per.period_days, per.fap
            except ValueError:
                pass

    sed_pts, t_spread = _build_sed(dets, forced)
    rb_med = float(np.median([d.real_bogus for d in dets])) if dets else 0.5
    elong_med = float(np.median([d.elongation for d in dets])) if dets else 1.0
    sharp_med = float(np.median([d.sharpness for d in dets])) if dets else 0.65
    snr_med = float(np.median([d.snr for d in dets])) if dets else 0.0
    n_clean = sum(1 for d in dets if not (d.flags & bad_art_bits))
    is_transient = len(dets) <= len(forced) + 2 and any(
        d.is_upper_limit and d.mjd_tdb < min((x.mjd_tdb for x in dets), default=0.0) for d in forced
    )

    return {
        "ra_deg": ra_ref,
        "dec_deg": dec_ref,
        "mfit": mfit,
        "flags": flags_all,
        "all_saturated": all_saturated,
        "bad_art": bool(flags_all & bad_art_bits),
        "n_epochs": n_clean,
        "status": track.status,
        "matches": matches,
        "gaia_id": gaia_id,
        "sed_points": sed_pts,
        "t_spread": t_spread,
        "chi2_var": chi2_var,
        "chi2_var_nu": chi2_var_nu,
        "amp_mag": amp_mag,
        "period_days": period_days,
        "period_fap": period_fap,
        "monopole_favored": bool(mfit and mfit.vim_flagged),
        "dipole_favored": False,
        "max_diff_snr": 0.0,
        "rb_med": rb_med,
        "elong_med": elong_med,
        "sharp_med": sharp_med,
        "snr_med": snr_med,
        "is_transient": is_transient,
        "track_notes": list(track.notes),
    }


def _assess_features_from_diff(
    diff: DifferenceResult,
    ra_deg: float,
    dec_deg: float,
    catalog_sources: Sequence[Any] | None,
) -> dict[str, Any]:
    """Extract unbiased difference/monopole/dipole & catalog evidence from a `DifferenceResult`."""
    pairs = diff.pair_results
    t_mid = float(np.mean(diff.epochs_mjd_tdb)) if diff.epochs_mjd_tdb else 60857.0
    st: StampTestResult | None = diff.stamp_test_near(ra_deg, dec_deg, radius_arcsec=18.0)

    # Also evaluate exact S_corr at (ra_deg, dec_deg) directly on every pair's score map.
    direct_snrs: list[float] = []
    sed_pts: list[SEDPoint] = []
    for pr in pairs:
        xp, yp = (float(v) for v in pr.wcs.world_to_pixel_values(ra_deg, dec_deg))
        ix, iy = int(round(xp)), int(round(yp))
        ny, nx = pr.score_corr.shape
        if 0 <= ix < nx and 0 <= iy < ny and pr.quality_mask[iy, ix]:
            val = float(
                np.max(
                    np.abs(
                        pr.score_corr[
                            max(0, iy - 1) : min(ny, iy + 2), max(0, ix - 1) : min(nx, ix + 2)
                        ]
                    )
                )
            )
            direct_snrs.append(val)
        if pr.wavelength_mismatch:
            continue

    max_s = max([abs(st.snr_corr)] if st else [0.0], default=0.0)
    if direct_snrs:
        max_s = max(max_s, max(direct_snrs))

    matches = crossmatch_catalog(
        ra_deg, dec_deg, t_mid, max_radius_arcsec=3.0, catalog_sources=catalog_sources
    )
    gaia_id = next(
        (m.gaia_id for m in matches if m.gaia_id is not None and m.match_prob >= 0.8), None
    )

    dip_fav = st.dipole_favored if st is not None else False
    mono_fav = st.monopole_favored if st is not None else (not dip_fav and max_s >= 2.0)
    dbic_mv_mm = (
        st.delta_bic_mv_over_mm
        if st is not None
        else (diff.delta_bic_mv_over_mm if not dip_fav else -10.0)
    )
    dbic_mm_mv = st.delta_bic_mm_over_mv if st is not None else -dbic_mv_mm

    # Estimate PM vector only if dipole favored (else consistent with zero / stationary!)
    mu_diff = 0.0
    mu_sig = 1.0
    chi2_mu = 0.0
    if dip_fav and st is not None and pairs:
        pr0 = pairs[min(st.pair_indices[0], len(pairs) - 1)]
        dt_yr = max(abs(pr0.sci_mjd_tdb - pr0.ref_mjd_tdb) / 365.25, 1e-3)
        pix_as = 6.2
        disp_as = st.displacement_px * pix_as
        sig_as = st.displacement_sigma_px * pix_as
        mu_diff = disp_as / dt_yr
        mu_sig = max(sig_as / dt_yr, 0.05)
        chi2_mu = (mu_diff / mu_sig) ** 2

    chi2_var = float(sum(s**2 for s in direct_snrs)) if direct_snrs else (max_s**2)
    chi2_var_nu = chi2_var / max(len(direct_snrs), 1)
    amp_mag = 0.5 if max_s >= 4.0 else (0.15 if max_s >= 2.0 else 0.0)

    return {
        "ra_deg": ra_deg,
        "dec_deg": dec_deg,
        "mfit": None,
        "flags": 0,
        "all_saturated": False,
        "bad_art": False,
        "n_epochs": max(len(diff.epochs_mjd_tdb), len(pairs) + 1),
        "status": "active",
        "matches": matches,
        "gaia_id": gaia_id,
        "sed_points": sed_pts,
        "t_spread": float(max(diff.epochs_mjd_tdb) - min(diff.epochs_mjd_tdb))
        if len(diff.epochs_mjd_tdb) >= 2
        else 0.0,
        "chi2_var": chi2_var,
        "chi2_var_nu": chi2_var_nu,
        "amp_mag": amp_mag,
        "period_days": None,
        "period_fap": 1.0,
        "monopole_favored": mono_fav,
        "dipole_favored": dip_fav,
        "max_diff_snr": max_s,
        "diff_dbic_mv_mm": dbic_mv_mm,
        "diff_dbic_mm_mv": dbic_mm_mv,
        "chi2_mu_override": chi2_mu,
        "mu_override": mu_diff,
        "mu_sig_override": mu_sig,
        "rb_med": 0.9 if max_s >= 4.0 else 0.7,
        "elong_med": 1.0,
        "sharp_med": 0.65,
        "snr_med": max(5.0, max_s * 2.0),
        "is_transient": False,
        "track_notes": [],
    }


def _assess_features_from_frames(
    frames: Sequence[Any],
    ra_deg: float,
    dec_deg: float,
    catalog_sources: Sequence[Any] | None,
) -> dict[str, Any]:
    loaded = [as_frame_data(f) for f in frames]
    meas: list[MotionMeasurement] = []
    unsat_loaded: list[Any] = []
    sed_pts: list[SEDPoint] = []
    var_fl, var_fe = [], []
    rbs, elongs, sharps, snrs = [], [], [], []
    target = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
    any_sat, all_sat, flags_tot = False, bool(loaded), 0

    for idx, fr in enumerate(loaded):
        ext = extract_sources(fr)
        det = None
        if ext.detections:
            cands = SkyCoord(
                ra=[d.ra_deg for d in ext.detections] * u.deg,
                dec=[d.dec_deg for d in ext.detections] * u.deg,
                frame="icrs",
            )
            seps = target.separation(cands).arcsec
            best_i = int(np.argmin(seps))
            if float(seps[best_i]) <= 15.0:
                det = ext.detections[best_i]

        if det is not None:
            mjd, band = ext.epoch_mjd_tdb, ext.band_id
            fl, fe, snr_v, flg = det.flux, det.flux_err, det.snr, int(det.flags)
            rb_v, el_v, sh_v, is_ul = det.real_bogus, det.elongation, det.sharpness, False
            meas.append(
                MotionMeasurement(
                    mjd_tdb=mjd,
                    ra_deg=det.ra_deg,
                    dec_deg=det.dec_deg,
                    cov_sky_arcsec2=det.cov_sky_arcsec2,
                    flux=fl,
                    flux_err=fe,
                    flags=flg,
                    band_id=band,
                    epoch_index=idx,
                )
            )
        else:
            fl, fe, snr_v, flg = forced_photometry_at_sky(fr, ra_deg, dec_deg)
            mjd, band, rb_v, el_v, sh_v, is_ul = (
                fr.epoch.mjd_tdb_mid,
                fr.band_id,
                0.5,
                1.0,
                0.65,
                snr_v < 3.0,
            )

        sat = bool(flg & int(DetectionFlag.SATURATED))
        any_sat |= sat
        all_sat &= sat
        flags_tot |= flg
        if not sat:
            unsat_loaded.append(fr)
            if fe and fe > 0.0:
                var_fl.append(float(fl))
                var_fe.append(float(fe))
        rbs.append(rb_v)
        elongs.append(el_v)
        sharps.append(sh_v)
        snrs.append(snr_v)
        sed_pts.append(_to_sed_point(band, fl, fe, mjd, flg, is_upper_limit=is_ul, snr=snr_v))

    matches = crossmatch_catalog(
        ra_deg,
        dec_deg,
        loaded[0].epoch.mjd_tdb_mid if loaded else 60857.0,
        max_radius_arcsec=3.0,
        catalog_sources=catalog_sources,
    )
    gaia_id = next(
        (m.gaia_id for m in matches if m.gaia_id is not None and m.match_prob >= 0.8), None
    )
    mfit: MotionFitResult | None = None
    if len(meas) >= 2 and not all_sat:
        try:
            mfit = fit_motion(meas)
        except ValueError:
            mfit = None

    mono_fav, dip_fav, max_diff_snr = False, False, 0.0
    if len(unsat_loaded) >= 2:
        st = difference_frames(unsat_loaded).stamp_test_near(ra_deg, dec_deg, radius_arcsec=15.0)
        if st is not None:
            mono_fav, dip_fav, max_diff_snr = (
                st.monopole_favored,
                st.dipole_favored,
                abs(st.snr_corr),
            )

    chi2_var, chi2_var_nu, amp_mag = weighted_flux_variability(var_fl, var_fe, sys_frac=0.03)
    period_days, period_fap = None, 1.0
    if len(var_fl) >= 8 and chi2_var_nu > 3.0:
        try:
            per = measure_variability_period(unsat_loaded, ra_deg=ra_deg, dec_deg=dec_deg)
            period_days, period_fap = per.period_days, per.fap
        except ValueError:
            pass

    mjds = [p.mjd_tdb for p in sed_pts]
    t_spread = float(max(mjds) - min(mjds)) if len(mjds) >= 2 else 0.0
    return {
        "ra_deg": ra_deg,
        "dec_deg": dec_deg,
        "mfit": mfit,
        "flags": flags_tot,
        "all_saturated": all_sat or (any_sat and not var_fl),
        "bad_art": bool(flags_tot & int(HARD_BAD_MASK)),
        "n_epochs": len(loaded),
        "status": "active",
        "matches": matches,
        "gaia_id": gaia_id,
        "sed_points": sed_pts,
        "t_spread": t_spread,
        "chi2_var": chi2_var,
        "chi2_var_nu": chi2_var_nu,
        "amp_mag": amp_mag,
        "period_days": period_days,
        "period_fap": period_fap,
        "monopole_favored": mono_fav or bool(mfit and mfit.vim_flagged),
        "dipole_favored": dip_fav,
        "max_diff_snr": max_diff_snr,
        "rb_med": float(np.median(rbs)) if rbs else 0.8,
        "elong_med": float(np.median(elongs)) if elongs else 1.0,
        "sharp_med": float(np.median(sharps)) if sharps else 0.65,
        "snr_med": float(np.median(snrs)) if snrs else 0.0,
        "is_transient": False,
        "track_notes": [],
    }


def _compute_log_likelihoods(
    feat: dict[str, Any],
    single_fits_result: Any | None,
    is_planet_x_candidate: bool,
) -> tuple[dict[str, dict[str, float]], list[str], str | None]:
    """Compute per-method weighted log-likelihoods `w_m * ln L_m(c)` across all 13 classes."""
    mfit: MotionFitResult | None = feat["mfit"]
    chi2_mu = mfit.chi2_mu if mfit is not None else float(feat.get("chi2_mu_override", 0.0))
    mu = mfit.mu_arcsec_yr if mfit is not None else float(feat.get("mu_override", 0.0))
    sig_mu = (
        mfit.sigma_mu_arcsec_yr if mfit is not None else float(feat.get("mu_sig_override", 1.0))
    )
    dbic_mot = (
        mfit.delta_bic
        if mfit is not None
        else (12.0 if feat["dipole_favored"] and chi2_mu > 25.0 else 0.0)
    )
    vim = bool(mfit and mfit.vim_flagged)

    streaks = getattr(single_fits_result, "streaks", None) or () if single_fits_result else ()
    tracklets = getattr(single_fits_result, "tracklets", None) or () if single_fits_result else ()
    has_streak, has_tracklet = bool(streaks), bool(tracklets)
    rate_hr = max(
        [mu / 8766.0]
        + [float(s.rate_arcsec_hr or 2.0) for s in streaks]
        + [float(t.rate_arcsec_hr) for t in tracklets]
    )

    all_sat = bool(feat["all_saturated"])
    bad_art = bool(feat["bad_art"])
    n_ep = int(feat["n_epochs"])
    matches: list[CatalogMatch] = feat["matches"]
    gaia_id = feat["gaia_id"]

    # External catalog magnitudes (purely observational) & reduced proper motion (§D.3).
    mags: dict[str, float] = {}
    cat_pm = 0.0
    cat_pm_snr = 0.0
    for cm in matches:
        if cm.match_prob >= 0.8:
            for mk, mv in cm.mags.items():
                mags.setdefault(mk, float(mv))
            if cm.pmra_arcsec_yr is not None and cm.pmdec_arcsec_yr is not None:
                c_mu = math.hypot(cm.pmra_arcsec_yr, cm.pmdec_arcsec_yr)
                if c_mu > cat_pm:
                    cat_pm = c_mu
                    cat_pm_snr = c_mu / 0.02

    band_f, det_opt, det_ir = _band_flux_summary(feat["sed_points"])
    w1_w2 = (mags["W1"] - mags["W2"]) if ("W1" in mags and "W2" in mags) else None
    g_ks = (mags["G"] - mags["Ks"]) if ("G" in mags and "Ks" in mags) else None
    ks_w2 = (mags["Ks"] - mags["W2"]) if ("Ks" in mags and "W2" in mags) else None
    ch4_ratio = (
        (band_f["B6"][0] / max(band_f["B4"][0], band_f["B4"][1], 1e-6))
        if ("B6" in band_f and "B4" in band_f)
        else None
    )
    opt_drop = (
        any(b in band_f and band_f[b][2] < 3.0 for b in ("B1", "B2", "B3"))
        and any(b in band_f and band_f[b][2] >= 5.0 for b in ("B5", "B6"))
        and not det_opt
    )
    opt_bright = det_opt or ("G" in mags)

    h_w2 = (mags["W2"] + 5.0 * math.log10(max(mu, cat_pm, 1e-3)) + 5.0) if "W2" in mags else None
    h_g = (mags["G"] + 5.0 * math.log10(max(mu, cat_pm, 1e-3)) + 5.0) if "G" in mags else None

    def _add(d: dict[str, float], keys: Sequence[str], dv: float) -> None:
        for k in keys:
            d[k] += dv

    # 1. SED log-likelihood
    l_sed = {c: 0.0 for c in PHYSICAL_CLASSES}
    subtype: str | None = None
    evidence: list[str] = []

    if opt_drop or (w1_w2 is not None and w1_w2 >= 3.0):
        l_sed["brown_dwarf"] += 4.2
        l_sed["candidate_new_mover"] += 1.0
        l_sed["star_pm"] -= 3.5
        l_sed["star_static"] -= 4.0
        subtype = "Y"
        evidence.append("Optical/NIR dropout (<3 sigma in B1-B3; 4.5 um B5/B6 excess)")
    elif (ch4_ratio is not None and ch4_ratio >= 2.2) or (
        w1_w2 is not None and w1_w2 >= 0.8 and gaia_id is None
    ):
        l_sed["brown_dwarf"] += 3.6
        l_sed["star_pm"] -= 2.2
        l_sed["star_static"] -= 2.5
        subtype = "T"
        if ch4_ratio is not None and ch4_ratio >= 2.2:
            evidence.append(f"CH4 3.3 um absorption trough (F_B6/F_B4 = {ch4_ratio:.2f})")
        if w1_w2 is not None and w1_w2 >= 0.8:
            evidence.append(f"Infrared methane color W1-W2 = {w1_w2:.2f}")
    elif g_ks is not None and g_ks > 3.5 and (w1_w2 is None or w1_w2 < 0.8):
        l_sed["star_pm"] += 2.2
        l_sed["star_static"] += 1.8
        l_sed["brown_dwarf"] -= 2.2
        l_sed["white_dwarf"] -= 2.5
        evidence.append(
            f"Red cool-star optical/NIR color (G-Ks={g_ks:.2f}, W1-W2={w1_w2 or 0.0:.2f})"
        )
    elif (
        opt_bright
        and (w1_w2 is None or w1_w2 < 0.7)
        and "B6" in band_f
        and any(b in band_f for b in ("B1", "B2"))
    ):
        f_opt = max(band_f[b][0] for b in ("B1", "B2") if b in band_f)
        if f_opt > 5.0 * max(band_f["B6"][0], 1e-6):
            _add(l_sed, ("star_pm", "star_static"), 1.5)
            l_sed["white_dwarf"] += 1.2 if (h_g is not None and h_g > 15.0) else 0.0
            l_sed["brown_dwarf"] -= 3.5
            evidence.append("Blue/Rayleigh-Jeans stellar SED")

    if ks_w2 is not None and ks_w2 > 1.2 and (det_opt or "G" in mags):
        l_sed["variable_star"] += 2.2
        subtype = subtype or "YSO"
        evidence.append(f"Near-IR circumstellar disk excess (Ks-W2={ks_w2:.2f})")

    if feat["amp_mag"] > 0.8 and feat["period_days"] is not None and feat["period_days"] > 80.0:
        l_sed["variable_star"] += 2.0
        subtype = "Mira"
        evidence.append(
            f"Long-period large-amplitude Mira pulsation (P={feat['period_days']:.1f} d)"
        )

    # 2. Motion log-likelihood (§D.3)
    l_mot = {c: 0.0 for c in PHYSICAL_CLASSES}
    is_sso_rate = (rate_hr >= 0.4) or has_streak or has_tracklet
    sig_mover_pos = (
        (chi2_mu > 25.0)
        and (dbic_mot > 10.0)
        and (not vim)
        and (not all_sat)
        and (not feat["monopole_favored"])
    )

    if is_sso_rate:
        l_mot["asteroid"] += 5.5
        l_mot["comet"] += 4.5
        _add(
            l_mot,
            ("star_pm", "star_static", "brown_dwarf", "galaxy", "quasar", "variable_star"),
            -4.0,
        )
        evidence.append(f'Solar-System angular rate ({rate_hr:.2f}"/hr)')
    elif sig_mover_pos and not bad_art:
        _add(l_mot, ("star_pm", "brown_dwarf", "white_dwarf", "candidate_new_mover"), 4.5)
        l_mot["brown_dwarf"] += 1.2 if (h_w2 is not None and h_w2 >= 15.0) else 0.0
        l_mot["white_dwarf"] += 2.5 if (h_g is not None and h_g >= 15.0) else 0.0
        _add(l_mot, ("star_static", "galaxy", "quasar", "variable_star", "transient"), -5.0)
        l_mot["artifact"] -= 2.5
        evidence.append(f'Proper motion mu={mu:.3f}+/-{sig_mu:.3f}"/yr (chi2_mu={chi2_mu:.1f})')
    else:
        _add(l_mot, ("star_static", "galaxy", "quasar", "variable_star"), 2.2)
        _add(l_mot, tuple(MOVING_CLASSES), -5.0)
        if vim:
            l_mot["variable_star"] += 3.5
            _add(l_mot, tuple(MOVING_CLASSES), -4.0)
            evidence.append("Photocentre shift flagged as VIM (flux-correlated wobble)")
        if all_sat:
            _add(l_mot, ("variable_star", "star_static"), 1.5)

    if is_planet_x_candidate:
        l_mot["candidate_planet_x"] += 10.0

    # 3. Variability log-likelihood (§D.4)
    l_var = {c: 0.0 for c in PHYSICAL_CLASSES}
    strong_var = (
        feat["chi2_var_nu"] > 4.0
        or feat["amp_mag"] > 0.15
        or (feat["monopole_favored"] and feat["max_diff_snr"] >= 3.0)
        or (all_sat and feat["n_epochs"] >= 3)
    )
    if feat["is_transient"]:
        l_var["transient"] += 4.5
        evidence.append("Transient appearance / non-detection in prior epochs")
    elif strong_var and (not is_sso_rate) and not sig_mover_pos:
        l_var["variable_star"] += 5.5
        l_var["quasar"] += 0.8
        l_var["star_static"] -= 4.0
        l_var["galaxy"] -= 3.0
        _add(l_var, tuple(MOVING_CLASSES), -2.5)
        if feat["monopole_favored"]:
            evidence.append(
                f"Stationary monopole variability favored (|S_corr|={feat['max_diff_snr']:.1f})"
            )
        else:
            evidence.append(
                f"Flux variability (chi2_var_nu={feat['chi2_var_nu']:.1f}, "
                f"dmag={feat['amp_mag']:.2f})"
            )
    else:
        l_var["star_static"] += 0.5
        l_var["variable_star"] -= 2.0

    # 4. Morphology log-likelihood (§B.7, §D.1)
    l_morph = {c: 0.0 for c in PHYSICAL_CLASSES}
    flags = int(feat["flags"])
    ext_flag = bool(flags & int(DetectionFlag.EXTENDED)) or feat["elong_med"] > 1.55
    if (
        bad_art
        or feat["rb_med"] < 0.45
        or (n_ep == 1 and not has_streak and not has_tracklet)
        or (flags & int(DetectionFlag.LOST))
    ):
        l_morph["artifact"] += 6.5
        _add(l_morph, tuple(MOVING_CLASSES), -4.0)
        evidence.append("Low quality / artifact morphology or single-epoch transient track")
    elif has_streak:
        l_morph["asteroid"] += 3.0
        l_morph["comet"] += 2.5
        l_morph["artifact"] -= 1.0
    elif ext_flag:
        l_morph["galaxy"] += 3.8 if not is_sso_rate else 0.0
        l_morph["comet"] += 4.2 if is_sso_rate or (det_ir and ch4_ratio is None) else 1.2
        _add(l_morph, ("star_pm", "star_static", "brown_dwarf"), -3.0)
    else:
        l_morph["artifact"] -= 1.5
        l_morph["galaxy"] -= 2.0

    # 5. Cross-match log-likelihood (§D.5)
    l_xmatch = {c: 0.0 for c in PHYSICAL_CLASSES}
    if gaia_id is not None:
        l_xmatch["star_pm"] += 2.5 if sig_mover_pos else -0.5
        l_xmatch["star_static"] += 2.2
        l_xmatch["variable_star"] += 2.0
        l_xmatch["white_dwarf"] += 1.0 if (h_g is not None and h_g >= 14.0) else -0.5
        l_xmatch["brown_dwarf"] -= 3.5
        _add(l_xmatch, ("asteroid", "comet"), -5.0)
        l_xmatch["candidate_new_mover"] -= 8.0
        evidence.append(f"Gaia DR3 counterpart ({gaia_id}, catalog_pm_snr={cat_pm_snr:.1f})")
    elif sig_mover_pos:
        has_bd_sed = (
            opt_drop
            or (ch4_ratio is not None and ch4_ratio >= 2.2)
            or (w1_w2 is not None and w1_w2 >= 0.8)
            or (h_w2 is not None and h_w2 >= 15.0)
        )
        l_xmatch["candidate_new_mover"] += 3.2 if not has_bd_sed else 1.4
        l_xmatch["brown_dwarf"] += 2.4 if has_bd_sed else 0.5
        l_xmatch["star_pm"] -= 2.2
        evidence.append("No Gaia DR3 counterpart")

    if subtype in ("Y", "T") and (gaia_id is None):
        evidence.append(f"Spectral subtype estimate: {subtype}-type brown dwarf")

    contributions = {
        m: {c: CLASS_WEIGHTS[m] * vec[c] for c in PHYSICAL_CLASSES}
        for m, vec in (
            ("sed", l_sed),
            ("motion", l_mot),
            ("var", l_var),
            ("morph", l_morph),
            ("xmatch", l_xmatch),
        )
    }
    if subtype is None and strong_var and not (opt_drop or ch4_ratio):
        subtype = "Variable"
    return contributions, evidence, subtype


def classify_source(
    source: Any,
    *,
    ra_deg: float | None = None,
    dec_deg: float | None = None,
    catalog_sources: Sequence[Any] | None = None,
    single_fits_result: Any | None = None,
    is_planet_x_candidate: bool = False,
) -> ClassificationResult:
    """Classify a tracked source, ZOGY difference result, or FITS sequence (§D.1-D.6)."""
    if isinstance(source, TrackedSource):
        feat = _assess_features_from_track(source, catalog_sources)
    elif isinstance(source, DifferenceResult):
        if ra_deg is None or dec_deg is None:
            raise ValueError(
                "`ra_deg` and `dec_deg` are required when classifying a DifferenceResult."
            )
        feat = _assess_features_from_diff(source, ra_deg, dec_deg, catalog_sources)
    elif isinstance(source, Sequence) and not isinstance(source, (str, bytes)):
        if ra_deg is None or dec_deg is None:
            raise ValueError(
                "`ra_deg` and `dec_deg` are required when classifying a frame sequence."
            )
        feat = _assess_features_from_frames(source, ra_deg, dec_deg, catalog_sources)
    else:
        raise TypeError(f"Unsupported input to classify_source: {type(source)!r}")

    priors = _galactic_priors(feat["ra_deg"], feat["dec_deg"])
    contrib, evidence, subtype = _compute_log_likelihoods(
        feat, single_fits_result, is_planet_x_candidate
    )

    logp = {c: priors[c] + sum(contrib[m][c] for m in CLASS_WEIGHTS) for c in PHYSICAL_CLASSES}
    if not is_planet_x_candidate:
        logp["candidate_planet_x"] = -30.0
    max_lp = max(logp.values())
    exp_p = {c: math.exp(max(v - max_lp, -50.0)) for c, v in logp.items()}
    z_norm = sum(exp_p.values())
    probs: dict[str, float] = {c: exp_p[c] / z_norm for c in PHYSICAL_CLASSES}

    # Mover posterior (suppressed on VIM, monopole, saturated-only, or artifact)
    mfit: MotionFitResult | None = feat["mfit"]
    raw_mover = sum(probs[c] for c in MOVING_CLASSES)
    has_streak = bool(
        single_fits_result and getattr(single_fits_result, "has_motion_signature", False)
    )
    unsat_mover = (
        not feat["all_saturated"]
        and not feat["bad_art"]
        and not feat["monopole_favored"]
        and (
            has_streak
            or feat["dipole_favored"]
            or (mfit is not None and mfit.is_mover and feat["n_epochs"] >= 2)
        )
    )
    if feat["n_epochs"] < 2 and not has_streak:
        raw_mover = min(raw_mover, 0.05)
    elif not unsat_mover:
        raw_mover = min(raw_mover, 0.02)
    probs["mover"] = float(min(1.0, max(0.0, raw_mover)))

    label = max(PHYSICAL_CLASSES, key=lambda c: probs[c])
    if label != "brown_dwarf" and label != "variable_star":
        subtype = None

    notes: list[str] = list(feat["track_notes"])
    if feat["flags"] & int(DetectionFlag.SATURATED):
        notes.append("SATURATED: photometry unreliable; astrometry uses wings/external anchors.")
    if feat["flags"] & int(DetectionFlag.AMBIGUOUS_MATCH):
        notes.append("AMBIGUOUS_MATCH: one or more epochs linked below 0.90 confidence.")
    if label in ("candidate_new_mover", "candidate_planet_x"):
        notes.append(
            "Candidate only; not confirmed — follow-up and MPC/external verification required."
        )
    if feat["t_spread"] > 0.0:
        evidence.append(f"SED temporal spread = {feat['t_spread']:.1f} d")

    # Cross-method disagreement detection (§K line 496)
    disagreements: list[str] = []
    method_tops = {m: max(PHYSICAL_CLASSES, key=lambda c: contrib[m][c]) for m in contrib}
    if method_tops["var"] == "variable_star" and method_tops["motion"] in MOVING_CLASSES:
        disagreements.append(
            "var favors variable_star while motion favors mover (check VIM / blending)."
        )
    if feat["all_saturated"]:
        disagreements.append(
            "All channels saturated: flux variability unreliable; relies on external catalog PM."
        )

    if not evidence:
        evidence.append(f"Top class {label} (P={probs[label]:.2f})")

    return ClassificationResult(
        label=label,
        probabilities=probs,
        spectral_subtype=subtype,
        evidence=evidence,
        method_contributions=contrib,
        disagreements=disagreements,
        flags=int(feat["flags"]),
        notes=notes or list(evidence[:2]),
        sed_points=list(feat["sed_points"]),
        temporal_spread_days=float(feat["t_spread"]),
    )


__all__ = [
    "CLASS_WEIGHTS",
    "MOVING_CLASSES",
    "PHYSICAL_CLASSES",
    "ClassificationResult",
    "SEDPoint",
    "classify_source",
]
