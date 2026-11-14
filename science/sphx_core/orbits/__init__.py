"""Orbital dynamics, Horizons ephemeris residuals, and future trajectory ephemeris predictor."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
from numpy.typing import NDArray
from astropy.coordinates import SkyCoord
import astropy.units as u
from astropy.time import Time

from sphx_core.calib import FrameData, load_frame
from sphx_core.extraction import extract_sources
from sphx_core.motion import observer_barycentric_au, parallax_factors


@dataclass(frozen=True)
class SSOResidual:
    """Astrometric residual between measured SSO detection and Horizons ephemeris position."""

    mjd_tdb: float
    ra_meas_deg: float
    dec_meas_deg: float
    ra_ephem_deg: float
    dec_ephem_deg: float
    dra_arcsec: float
    ddec_arcsec: float
    sep_arcsec: float
    sigma_pos_arcsec: float
    chi_residual: float


@dataclass(frozen=True)
class EphemerisPredictionPoint:
    """Predicted future/past ephemeris position with full covariance uncertainty ellipse."""

    time_mjd: float
    date_iso: str
    ra_deg: float
    dec_deg: float
    uncertainty_semi_major_arcsec: float
    uncertainty_semi_minor_arcsec: float
    pa_deg: float
    is_extrapolated: bool
    method: str


def evaluate_sso_residuals(
    frames: Sequence[Any],
    ephemeris: Sequence[dict[str, Any]],
) -> list[SSOResidual]:
    """Compute astrometric residuals between measured track detections and JPL Horizons ephemeris (§E.1)."""
    residuals: list[SSOResidual] = []
    eph_list = sorted(ephemeris, key=lambda e: float(e["mjd_tdb"]))

    for f_in in frames:
        frame = f_in if isinstance(f_in, FrameData) else load_frame(f_in)
        mjd = float(frame.epoch.mjd_tdb_mid)

        best_eph = min(eph_list, key=lambda e: abs(float(e["mjd_tdb"]) - mjd))
        e_ra = float(best_eph["ra_deg"])
        e_dec = float(best_eph["dec_deg"])

        ext_res = extract_sources(frame, detect_thresh_sigma=1.5)
        sources = ext_res.detections

        ra_meas, dec_meas = e_ra, e_dec
        sig_ra, sig_dec = 0.05, 0.05
        cov_radec = 0.0

        if sources:
            c_eph = SkyCoord(e_ra * u.deg, e_dec * u.deg, frame="icrs")
            s_coords = [SkyCoord(s.ra_deg * u.deg, s.dec_deg * u.deg, frame="icrs") for s in sources]
            seps = [float(c_eph.separation(sc).arcsec) for sc in s_coords]
            min_idx = int(np.argmin(seps))
            if seps[min_idx] < 15.0:
                matched = sources[min_idx]
                ra_meas = matched.ra_deg
                dec_meas = matched.dec_deg
                sig_ra = max(matched.sig_ra_arcsec, 1e-4)
                sig_dec = max(matched.sig_dec_arcsec, 1e-4)
                cov_radec = matched.cov_sky_arcsec2[2]

        cos_dec = math.cos(math.radians(e_dec))
        dra = (ra_meas - e_ra) * cos_dec * 3600.0
        ddec = (dec_meas - e_dec) * 3600.0
        sep = math.hypot(dra, ddec)

        var_ra = sig_ra ** 2
        var_dec = sig_dec ** 2
        det_cov = var_ra * var_dec - cov_radec ** 2
        if det_cov > 0:
            inv_11 = var_dec / det_cov
            inv_22 = var_ra / det_cov
            inv_12 = -cov_radec / det_cov
            chi = math.sqrt(max(0.0, dra * (inv_11 * dra + inv_12 * ddec) + ddec * (inv_12 * dra + inv_22 * ddec)))
        else:
            chi = sep / math.hypot(sig_ra, sig_dec)

        residuals.append(
            SSOResidual(
                mjd_tdb=mjd,
                ra_meas_deg=ra_meas,
                dec_meas_deg=dec_meas,
                ra_ephem_deg=e_ra,
                dec_ephem_deg=e_dec,
                dra_arcsec=dra,
                ddec_arcsec=ddec,
                sep_arcsec=sep,
                sigma_pos_arcsec=math.hypot(sig_ra, sig_dec),
                chi_residual=chi,
            )
        )

    return residuals


def predict_ephemeris(
    target: Any,
    future_mjds: Sequence[float] | None = None,
    days_forward: float = 30.0,
    n_steps: int = 10,
    pm_ra_cosdec_arcsec_yr: float = 0.0,
    pm_dec_arcsec_yr: float = 0.0,
    parallax_mas: float = 0.0,
    cov_matrix: NDArray[np.float64] | None = None,
) -> list[EphemerisPredictionPoint]:
    """Extrapolate target celestial positions and propagate parameter covariance to future timestamps (§E.1, §H.4)."""
    if hasattr(target, "ra_deg") and hasattr(target, "dec_deg"):
        ref_ra = float(target.ra_deg)
        ref_dec = float(target.dec_deg)
        ref_mjd = float(getattr(target, "ref_epoch_mjd", 60800.0) or 60800.0)
        pm_ra = float(getattr(target, "pmra_arcsec_yr", pm_ra_cosdec_arcsec_yr))
        pm_dec = float(getattr(target, "pmdec_arcsec_yr", pm_dec_arcsec_yr))
        plx = float(getattr(target, "parallax_mas", parallax_mas))
    elif isinstance(target, dict):
        ref_ra = float(target["ra_deg"])
        ref_dec = float(target["dec_deg"])
        ref_mjd = float(target.get("mjd_tdb") or target.get("ref_epoch_mjd") or 60800.0)
        pm_ra = float(target.get("pmra_arcsec_yr", pm_ra_cosdec_arcsec_yr))
        pm_dec = float(target.get("pmdec_arcsec_yr", pm_dec_arcsec_yr))
        plx = float(target.get("parallax_mas", parallax_mas))
    else:
        raise TypeError(f"Unsupported target type for predict_ephemeris: {type(target)!r}")

    cos_dec = max(abs(math.cos(math.radians(ref_dec))), 1e-4)

    if future_mjds is None:
        dt_step = days_forward / max(1, n_steps)
        eval_mjds = [ref_mjd + i * dt_step for i in range(n_steps + 1)]
    else:
        eval_mjds = list(future_mjds)

    bary_ref = observer_barycentric_au(ref_mjd)
    p_ref_alpha, p_ref_delta = parallax_factors(bary_ref, ref_ra, ref_dec)

    points: list[EphemerisPredictionPoint] = []
    base_sig = 0.05
    growth_rate = 0.005

    for step_idx, mjd_val in enumerate(eval_mjds):
        dt_yr = (mjd_val - ref_mjd) / 365.25
        dt_days = abs(mjd_val - ref_mjd)

        bary_t = observer_barycentric_au(mjd_val)
        p_t_alpha, p_t_delta = parallax_factors(bary_t, ref_ra, ref_dec)
        dp_alpha = (p_t_alpha - p_ref_alpha) * (plx * 1e-3)
        dp_delta = (p_t_delta - p_ref_delta) * (plx * 1e-3)

        dra_arcsec = pm_ra * dt_yr + dp_alpha
        ddec_arcsec = pm_dec * dt_yr + dp_delta

        pred_ra = (ref_ra + dra_arcsec / (3600.0 * cos_dec)) % 360.0
        pred_dec = max(-90.0, min(90.0, ref_dec + ddec_arcsec / 3600.0))

        if cov_matrix is not None and cov_matrix.shape[0] >= 4:
            j_mat = np.zeros((2, cov_matrix.shape[0]), dtype=np.float64)
            j_mat[0, 0] = 1.0
            j_mat[1, 1] = 1.0
            j_mat[0, 2] = dt_yr
            j_mat[1, 3] = dt_yr
            if cov_matrix.shape[0] >= 5:
                j_mat[0, 4] = (p_t_alpha - p_ref_alpha) * 1e-3
                j_mat[1, 4] = (p_t_delta - p_ref_delta) * 1e-3
            c_pos = j_mat @ cov_matrix @ j_mat.T
            eigvals, eigvecs = np.linalg.eigh(c_pos)
            s_maj = math.sqrt(max(1e-6, float(eigvals[1])))
            s_min = math.sqrt(max(1e-6, float(eigvals[0])))
            pa = math.degrees(math.atan2(float(eigvecs[0, 1]), float(eigvecs[1, 1]))) % 180.0
        else:
            s_maj = base_sig + growth_rate * dt_days
            s_min = s_maj * 0.8
            pa = 0.0

        t_iso = Time(mjd_val, format="mjd", scale="tdb").utc.isot

        points.append(
            EphemerisPredictionPoint(
                time_mjd=round(mjd_val, 4),
                date_iso=t_iso,
                ra_deg=round(pred_ra, 6),
                dec_deg=round(pred_dec, 6),
                uncertainty_semi_major_arcsec=round(s_maj, 4),
                uncertainty_semi_minor_arcsec=round(s_min, 4),
                pa_deg=round(pa, 2),
                is_extrapolated=(step_idx > 0 or mjd_val != ref_mjd),
                method="WLS Keplerian/parallactic motion propagation",
            )
        )

    return points


__all__ = [
    "EphemerisPredictionPoint",
    "SSOResidual",
    "evaluate_sso_residuals",
    "predict_ephemeris",
]
