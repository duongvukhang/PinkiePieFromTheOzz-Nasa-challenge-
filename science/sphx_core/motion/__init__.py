"""Astrometric motion fitting, parallax, variability period, VIM, and single-FITS streak checks."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord, get_body_barycentric
from astropy.io import fits
from astropy.table import Table
from astropy.time import Time
from astropy.timeseries import LombScargle
from astropy.wcs import WCS
from numpy.typing import NDArray
from photutils.psf import CircularGaussianPRF, PSFPhotometry, SourceGrouper
from scipy.optimize import least_squares
from scipy.stats import pearsonr

from sphx_core.calib import (
    HARD_BAD_MASK,
    DetectionFlag,
    FrameData,
    estimate_background_and_noise,
    load_frame,
)
from sphx_core.extraction import estimate_psf_kernel, extract_sources, matched_filter_snr
from sphx_core.wcs import local_wcs_jacobian, sky_to_tangent_arcsec, tangent_arcsec_to_sky

_MJD_J2000 = 51544.5
_DAYS_PER_JYEAR = 365.25
_FWHM_PER_SIGMA = 2.0 * math.sqrt(2.0 * math.log(2.0))
_DEFAULT_CAT_SIGMA_ARCSEC: dict[str, float] = {
    "gaia": 0.002,
    "gaiadr3": 0.002,
    "catwise": 0.03,
    "catwise2020": 0.03,
    "allwise": 0.05,
    "2mass": 0.08,
    "twomass": 0.08,
    "spherex": 0.0,
}


@dataclass(frozen=True)
class MotionMeasurement:
    """Single astrometric/photometric epoch input (`deg`, `MJD TDB`, `arcsec^2`, native flux)."""

    mjd_tdb: float
    ra_deg: float
    dec_deg: float
    cov_sky_arcsec2: tuple[float, float, float]
    flux: float | None = None
    flux_err: float | None = None
    flags: int = 0
    band_id: str | None = None
    epoch_index: int = -1
    sigma_cat_arcsec: float = 0.0
    stat_cov_sky_arcsec2: tuple[float, float, float] | None = None
    obs_bary_au: tuple[float, float, float] | None = None
    source: str = "spherex"


@dataclass(frozen=True)
class MotionFitResult:
    """Fitted proper motion, parallax, covariance, and VIM status (`\"/yr`, `mas`, `deg`)."""

    model_type: Literal["STATIC", "LINEAR_4P", "PARALLAX_5P"]
    ref_epoch_mjd_tdb: float
    ref_epoch_jyear: float
    ra_ref_deg: float
    dec_ref_deg: float
    pmra_arcsec_yr: float
    pmdec_arcsec_yr: float
    mu_arcsec_yr: float
    sigma_mu_arcsec_yr: float
    pa_deg: float
    sigma_pa_deg: float
    parallax_mas: float
    sigma_parallax_mas: float
    distance_pc: float | None
    v_tan_kms: float | None
    chi2: float
    dof: int
    chi2_nu: float
    chi2_mu: float
    bic: float
    delta_bic: float
    cov_params: tuple[tuple[float, ...], ...]
    n_epochs: int
    n_outliers: int
    outlier_indices: tuple[int, ...]
    baseline_yr: float
    parallax_span_au: float
    is_mover: bool
    vim_flagged: bool
    vim_r: float | None
    vim_p_value: float | None
    flags: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PeriodogramResult:
    """Lomb-Scargle periodogram summary (`d`, `mag`, dimensionless)."""

    period_days: float
    frequency_inv_days: float
    power_max: float
    fap: float
    amplitude_mag: float
    chi2_var: float
    n_epochs: int
    stetson_j: float | None = None


@dataclass(frozen=True)
class StreakDetection:
    """Trailed PSF single-exposure streak measurement (`px`, `deg`, `\"/hr`, `deg mod 180`)."""

    x_px: float
    y_px: float
    ra_deg: float
    dec_deg: float
    length_px: float
    length_arcsec: float
    pa_deg: float
    sigma_pa_deg: float
    rate_arcsec_hr: float | None
    sigma_rate_arcsec_hr: float | None
    flux: float
    snr: float
    radon_score: float
    chi2_nu: float
    mjd_tdb: float
    exptime_s: float | None


@dataclass(frozen=True)
class IntraVisitTracklet:
    """Linear tracklet linked across >= 3 intra-visit or multi-HDU exposures (`\"/hr`, `deg`)."""

    epoch_indices: tuple[int, ...]
    mjds_tdb: tuple[float, ...]
    ra_degs: tuple[float, ...]
    dec_degs: tuple[float, ...]
    rate_arcsec_hr: float
    sigma_rate_arcsec_hr: float
    pa_deg: float
    chi2_nu: float
    snr_mean: float


@dataclass(frozen=True)
class SingleFitsMotionResult:
    """Combined single-FITS streak, multi-HDU, and intra-visit tracklet check (§H.1)."""

    streaks: list[StreakDetection]
    tracklets: list[IntraVisitTracklet]
    has_motion_signature: bool
    notes: list[str] = field(default_factory=list)


def unwrap_frame_source(source: Any) -> Any:
    """Extract FITS payload or `FrameData` from raw input or `SyntheticFrame`-like wrappers."""
    return (
        source.fits_bytes
        if hasattr(source, "fits_bytes") and not isinstance(source, FrameData)
        else source
    )


def as_frame_data(
    source: Any,
    *,
    wcs_override: WCS | None = None,
    psf_sigma_px: float | None = None,
) -> FrameData:
    """Load `FrameData` from path, bytes, `HDUList`, `FrameData`, or `.fits_bytes` container."""
    return load_frame(
        unwrap_frame_source(source), wcs_override=wcs_override, psf_sigma_px=psf_sigma_px
    )


def mjd_to_jyear(mjd_tdb: float) -> float:
    """Convert MJD (TDB) to Julian year."""
    return 2000.0 + (mjd_tdb - _MJD_J2000) / _DAYS_PER_JYEAR


def observer_barycentric_au(
    mjd_tdb: float,
    obsgeo_m: tuple[float, float, float] | None = None,
) -> NDArray[np.float64]:
    """Return observer barycentric ICRS position `(X, Y, Z)` [au] at `mjd_tdb` (§C.4)."""
    bary = np.asarray(
        get_body_barycentric("earth", Time(mjd_tdb, format="mjd", scale="tdb")).xyz.to_value(u.au),
        dtype=np.float64,
    )
    if obsgeo_m is not None:
        vec_m = np.asarray(obsgeo_m, dtype=np.float64)
        norm_m = float(np.linalg.norm(vec_m))
        if np.all(np.isfinite(vec_m)) and norm_m > 0.0:
            vec_au = np.asarray((vec_m * u.m).to_value(u.au), dtype=np.float64)
            return vec_au if norm_m > 1e10 else np.asarray(bary + vec_au, dtype=np.float64)
    return bary


def parallax_factors(
    bary_au: NDArray[np.float64], ra0_deg: float, dec0_deg: float
) -> tuple[float, float]:
    """Compute dimensionless tangent-plane parallax factors `(P_alpha, P_delta)` (§C.4)."""
    ra0, dec0 = math.radians(ra0_deg), math.radians(dec0_deg)
    x, y, z = float(bary_au[0]), float(bary_au[1]), float(bary_au[2])
    sin_a, cos_a = math.sin(ra0), math.cos(ra0)
    sin_d, cos_d = math.sin(dec0), math.cos(dec0)
    p_alpha = x * sin_a - y * cos_a
    p_delta = x * cos_a * sin_d + y * sin_a * sin_d - z * cos_d
    return p_alpha, p_delta


def _get_field(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, Mapping) else getattr(obj, key, default)


def _anchor_to_measurement(anchor: Any) -> MotionMeasurement:
    mjd = _get_field(anchor, "mjd_tdb")
    if mjd is None and _get_field(anchor, "jd") is not None:
        mjd = float(_get_field(anchor, "jd")) - 2400000.5
    elif mjd is None and _get_field(anchor, "epoch_jyear") is not None:
        mjd = float(Time(float(_get_field(anchor, "epoch_jyear")), format="jyear", scale="tdb").mjd)
    if mjd is None:
        raise ValueError("Anchor must supply `mjd_tdb`, `jd`, or `epoch_jyear`.")

    cat = str(_get_field(anchor, "catalog", "") or "").lower().strip()
    sig_def = _DEFAULT_CAT_SIGMA_ARCSEC.get(cat, 0.08)
    sig_ra = float(
        _get_field(anchor, "sigma_ra_arcsec") or _get_field(anchor, "sigma_arcsec") or sig_def
    )
    sig_dec = float(
        _get_field(anchor, "sigma_dec_arcsec") or _get_field(anchor, "sigma_arcsec") or sig_def
    )
    sig_cat = float(_get_field(anchor, "sigma_cat_arcsec") or 0.0)
    ra_v = _get_field(anchor, "ra_deg")
    dec_v = _get_field(anchor, "dec_deg")
    return MotionMeasurement(
        mjd_tdb=float(mjd),
        ra_deg=float(ra_v if ra_v is not None else _get_field(anchor, "ra")) % 360.0,
        dec_deg=float(dec_v if dec_v is not None else _get_field(anchor, "dec")),
        cov_sky_arcsec2=(max(sig_ra, 1e-4) ** 2, max(sig_dec, 1e-4) ** 2, 0.0),
        sigma_cat_arcsec=sig_cat,
        source=cat or "anchor",
    )


def _cov_matrix(meas: MotionMeasurement, *, use_stat_only: bool = False) -> NDArray[np.float64]:
    cov_t = (
        meas.stat_cov_sky_arcsec2
        if use_stat_only and meas.stat_cov_sky_arcsec2 is not None
        else meas.cov_sky_arcsec2
    )
    vx = max(float(cov_t[0]), 1e-8)
    vy = max(float(cov_t[1]), 1e-8)
    cxy = float(cov_t[2])
    max_cov = 0.99 * math.sqrt(vx * vy)
    cxy = max(-max_cov, min(max_cov, cxy))
    mat = np.array([[vx, cxy], [cxy, vy]], dtype=np.float64)
    if not use_stat_only and meas.sigma_cat_arcsec > 0.0:
        mat = mat + (meas.sigma_cat_arcsec**2) * np.eye(2, dtype=np.float64)
    return mat


def _measurements_from_frames(
    frames: Sequence[Any], target_ra_deg: float, target_dec_deg: float, radius_arcsec: float = 15.0
) -> list[MotionMeasurement]:
    loaded = [as_frame_data(f) for f in frames]
    target = SkyCoord(ra=target_ra_deg * u.deg, dec=target_dec_deg * u.deg, frame="icrs")
    measurements: list[MotionMeasurement] = []
    for idx, fr in enumerate(loaded):
        ext = extract_sources(fr)
        if not ext.detections:
            continue
        cands = SkyCoord(
            ra=[d.ra_deg for d in ext.detections] * u.deg,
            dec=[d.dec_deg for d in ext.detections] * u.deg,
            frame="icrs",
        )
        seps = target.separation(cands).arcsec
        best_i = int(np.argmin(seps))
        if float(seps[best_i]) > radius_arcsec:
            continue
        det = ext.detections[best_i]
        if det.flags & int(HARD_BAD_MASK):
            continue
        # Statistical-only covariance for differential VIM diagnostics inside stable cutouts.
        pix = max(ext.pixscale_arcsec, 1e-3)
        vx_s = det.cov_px[0] * (pix**2) + 0.02**2
        vy_s = det.cov_px[1] * (pix**2) + 0.02**2
        cxy_s = det.cov_px[2] * (pix**2)
        measurements.append(
            MotionMeasurement(
                mjd_tdb=ext.epoch_mjd_tdb,
                ra_deg=det.ra_deg,
                dec_deg=det.dec_deg,
                cov_sky_arcsec2=det.cov_sky_arcsec2,
                stat_cov_sky_arcsec2=(vx_s, vy_s, cxy_s),
                flux=det.flux,
                flux_err=det.flux_err,
                flags=det.flags,
                band_id=ext.band_id,
                epoch_index=idx,
                source="spherex",
            )
        )
    return measurements


def weighted_flux_variability(
    fluxes: Sequence[float] | NDArray[np.float64],
    ferrs: Sequence[float] | NDArray[np.float64],
    *,
    sys_frac: float = 0.0,
) -> tuple[float, float, float]:
    """Return weighted `(chi2_var, chi2_var_nu, amplitude_mag)` across flux measurements."""
    if len(fluxes) < 2:
        return 0.0, 0.0, 0.0
    fl = np.asarray(fluxes, dtype=np.float64)
    fe_raw = np.maximum(np.asarray(ferrs, dtype=np.float64), 1e-9)
    fe = np.hypot(fe_raw, sys_frac * np.abs(fl)) if sys_frac > 0.0 else fe_raw
    w = 1.0 / (fe**2)
    f_bar = float(np.sum(w * fl) / max(float(np.sum(w)), 1e-18))
    chi2_var = float(np.sum(((fl - f_bar) / fe) ** 2))
    pos = fl[fl > 0.0]
    amp_mag = (
        float(2.5 * math.log10(np.max(pos) / max(float(np.min(pos)), 1e-9)))
        if len(pos) >= 2
        else 0.0
    )
    return chi2_var, chi2_var / max(len(fl) - 1, 1), amp_mag


def _measurements_from_track(track: Any) -> list[MotionMeasurement]:
    skip = HARD_BAD_MASK | int(DetectionFlag.LOST)
    bad_status = ("OUT_OF_FIELD", "BAND_UNCOVERED", "UPPER_LIMIT", "MASKED", "PM_OUTLIER")
    out: list[MotionMeasurement] = []
    for d in getattr(track, "detections", ()):
        ra, dec, mjd = (
            getattr(d, "ra_deg", None),
            getattr(d, "dec_deg", None),
            getattr(d, "mjd_tdb", None),
        )
        flags = int(getattr(d, "flags", 0))
        if (
            getattr(d, "is_upper_limit", False)
            or getattr(d, "status", "LINKED") in bad_status
            or ra is None
            or dec is None
            or mjd is None
            or (flags & skip)
        ):
            continue
        cov = getattr(d, "cov_sky_arcsec2", None)
        if cov is None:
            sra, sdec = (
                float(getattr(d, "sig_ra_arcsec", 0.2)),
                float(getattr(d, "sig_dec_arcsec", 0.2)),
            )
            cov = (sra**2, sdec**2, 0.0)
        fl, fe = getattr(d, "flux", None), getattr(d, "flux_err", None)
        out.append(
            MotionMeasurement(
                mjd_tdb=float(mjd),
                ra_deg=float(ra) % 360.0,
                dec_deg=float(dec),
                cov_sky_arcsec2=(float(cov[0]), float(cov[1]), float(cov[2])),
                flux=float(fl) if fl is not None else None,
                flux_err=float(fe) if fe is not None else None,
                flags=flags,
                band_id=getattr(d, "band_id", None),
                epoch_index=int(getattr(d, "epoch_index", -1)),
                stat_cov_sky_arcsec2=getattr(d, "stat_cov_sky_arcsec2", None),
                source="spherex",
            )
        )
    return out


def _wls_solve(
    a_mat: NDArray[np.float64],
    y_vec: NDArray[np.float64],
    w_blocks: list[NDArray[np.float64]],
    ridge: NDArray[np.float64] | None = None,
) -> tuple[NDArray[np.float64], NDArray[np.float64], float, float]:
    n_par = a_mat.shape[1]
    normal = np.zeros((n_par, n_par), dtype=np.float64)
    rhs = np.zeros(n_par, dtype=np.float64)
    for i, wi in enumerate(w_blocks):
        ai, yi = a_mat[2 * i : 2 * i + 2], y_vec[2 * i : 2 * i + 2]
        normal += ai.T @ wi @ ai
        rhs += ai.T @ wi @ yi
    if ridge is not None:
        normal += ridge
    diag = np.sqrt(np.maximum(np.diag(normal), 1e-18))
    cond = float(np.linalg.cond(normal / np.outer(diag, diag)))
    cov_theta = np.linalg.pinv(normal) if cond > 1e12 else np.linalg.inv(normal)
    theta = cov_theta @ rhs
    res = y_vec - a_mat @ theta
    chi2 = sum(
        float(res[2 * i : 2 * i + 2] @ wi @ res[2 * i : 2 * i + 2]) for i, wi in enumerate(w_blocks)
    ) + (float(theta @ ridge @ theta) if ridge is not None else 0.0)
    return theta, cov_theta, chi2, cond


def _leave_one_out_r(
    a_mat: NDArray[np.float64],
    res: NDArray[np.float64],
    cov_blocks: list[NDArray[np.float64]],
    cov_theta: NDArray[np.float64],
) -> list[float]:
    rs: list[float] = []
    for i, ci in enumerate(cov_blocks):
        ai, ei = a_mat[2 * i : 2 * i + 2], res[2 * i : 2 * i + 2]
        v_e = ci - ai @ cov_theta @ ai.T
        evals, evecs = np.linalg.eigh(v_e)
        if np.min(evals) <= 1e-9:
            v_e = evecs @ np.diag(np.maximum(evals, 1e-4 * np.diag(ci))) @ evecs.T
        rs.append(math.sqrt(max(0.0, float(ei @ np.linalg.pinv(v_e) @ ei))))
    return rs


def _check_vim(
    meas: list[MotionMeasurement],
    xi: NDArray[np.float64],
    eta: NDArray[np.float64],
    dt_yr: NDArray[np.float64],
) -> tuple[bool, float | None, float | None]:
    """Check variability-induced motion (VIM) per SCIENCE.md §B.8.3."""
    valid_idx = [
        i
        for i, m in enumerate(meas)
        if m.flux
        and m.flux > 0.0
        and m.flux_err
        and m.flux_err > 0.0
        and not (m.flags & int(DetectionFlag.SATURATED))
    ]
    by_band: dict[str, list[int]] = {}
    for i in valid_idx:
        by_band.setdefault(meas[i].band_id or "ALL", []).append(i)
    idx = max(by_band.values(), key=len, default=[])
    if len(idx) < 4:
        return False, None, None

    fluxes = np.array([float(meas[i].flux or 0.0) for i in idx], dtype=np.float64)
    ferrs = np.array([float(meas[i].flux_err or 1.0) for i in idx], dtype=np.float64)
    fw = 1.0 / (np.maximum(ferrs, 1e-9) ** 2)
    f_bar = float(np.sum(fw * fluxes) / np.sum(fw))
    if f_bar <= 0.0 or weighted_flux_variability(fluxes, ferrs)[1] <= 3.0:
        return False, None, None

    n_f = len(idx)
    u_flux = (fluxes - f_bar) / f_bar
    y_sub = np.empty(2 * n_f, dtype=np.float64)
    y_sub[0::2], y_sub[1::2] = (
        xi[idx] - float(np.mean(xi[idx])),
        eta[idx] - float(np.mean(eta[idx])),
    )

    a_vim = np.zeros((2 * n_f, 4), dtype=np.float64)
    a_vim[0::2, 0] = a_vim[1::2, 1] = 1.0
    a_vim[0::2, 2], a_vim[1::2, 3] = u_flux, u_flux

    a_lin = a_vim.copy()
    a_lin[0::2, 2], a_lin[1::2, 3] = dt_yr[idx], dt_yr[idx]

    w_tot = [np.linalg.inv(_cov_matrix(meas[i])) for i in idx]
    th_v_tot, _, c_v_tot, _ = _wls_solve(a_vim, y_sub, w_tot)
    _, _, c_l_tot, _ = _wls_solve(a_lin, y_sub, w_tot)

    if all(meas[i].stat_cov_sky_arcsec2 is not None for i in idx):
        w_st = [np.linalg.inv(_cov_matrix(meas[i], use_stat_only=True)) for i in idx]
    else:
        w_st = w_tot
    th_v_st, _, c_v_st, _ = _wls_solve(a_vim, y_sub, w_st)
    _, _, c_l_st, _ = _wls_solve(a_lin, y_sub, w_st)
    _, _, c_s_st, _ = _wls_solve(a_vim[:, :2], y_sub, w_st)

    axis = th_v_st[2:4] if float(np.hypot(*th_v_st[2:4])) > 0.0 else th_v_tot[2:4]
    norm_c = float(np.hypot(axis[0], axis[1]))
    if norm_c < 1e-6 or float(np.ptp(u_flux) * norm_c) < 0.03:
        return False, 0.0, 1.0
    r_val, p_val = pearsonr((xi[idx] * axis[0] + eta[idx] * axis[1]) / norm_c, u_flux)
    r_flt = float(r_val) if math.isfinite(float(r_val)) else 0.0
    p_flt = float(p_val) if math.isfinite(float(p_val)) else 1.0
    flagged = (c_v_tot < c_l_tot - 6.0) or (
        abs(r_flt) > 0.80 and (p_flt < 0.01 or (c_v_st < c_l_st - 3.5 and c_v_st < c_s_st - 5.0))
    )
    return flagged, r_flt, p_flt


def fit_motion(
    source: Any,
    *,
    fit_parallax: bool | None = None,
    anchors: Sequence[Any] | None = None,
    target_ra_deg: float | None = None,
    target_dec_deg: float | None = None,
    match_radius_arcsec: float = 15.0,
    outlier_sigma: float = 4.0,
    min_parallax_dt_yr: float = 0.5,
    min_parallax_span_au: float = 0.5,
    prior_parallax_mas: float | None = None,
    corroborated: bool = False,
) -> MotionFitResult:
    """Fit 2/4/5-parameter astrometric motion in ICRS tangent plane (§C.3-C.4, §B.8).

    Units: proper motions `\"/yr`, `pa_deg` in deg E of N, parallax `mas`, cov `\"` & `\"/yr`.
    """
    if hasattr(source, "detections"):
        meas = _measurements_from_track(source)
    elif isinstance(source, Sequence):
        if source and isinstance(source[0], MotionMeasurement):
            meas = list(source)
        elif target_ra_deg is not None and target_dec_deg is not None:
            meas = _measurements_from_frames(
                source, target_ra_deg, target_dec_deg, match_radius_arcsec
            )
        else:
            raise ValueError(
                "Passing frame sequences to `fit_motion` requires target_ra_deg and target_dec_deg."
            )
    else:
        raise TypeError(f"Unsupported input type for fit_motion: {type(source)!r}")

    if anchors:
        meas = [*meas, *(_anchor_to_measurement(a) for a in anchors)]
    if not meas:
        raise ValueError("No valid astrometric measurements available for motion fit.")

    notes: list[str] = []
    # Deduplicate/order stably by time; drop outlier epochs iteratively inside WLS (N >= 4).
    active = list(range(len(meas)))
    dropped: list[int] = []

    def _fit_all(indices: list[int]) -> dict[str, Any]:
        sub = [meas[i] for i in indices]
        covs = [_cov_matrix(m) for m in sub]
        ws = [np.linalg.inv(c) for c in covs]
        pos_w = np.array([1.0 / (c[0, 0] + c[1, 1]) for c in covs], dtype=np.float64)
        mjds = np.array([m.mjd_tdb for m in sub], dtype=np.float64)
        t0_mjd = float(np.sum(pos_w * mjds) / np.sum(pos_w))
        dt = (mjds - t0_mjd) / _DAYS_PER_JYEAR

        ra_r, dec_r = np.deg2rad([m.ra_deg for m in sub]), np.deg2rad([m.dec_deg for m in sub])
        vec = np.column_stack(
            (np.cos(dec_r) * np.cos(ra_r), np.cos(dec_r) * np.sin(ra_r), np.sin(dec_r))
        )
        v_mean = np.sum(pos_w[:, None] * vec, axis=0)
        v_mean /= float(np.linalg.norm(v_mean))
        ra0 = math.degrees(math.atan2(float(v_mean[1]), float(v_mean[0]))) % 360.0
        dec0 = math.degrees(math.asin(max(-1.0, min(1.0, float(v_mean[2])))))

        xi, eta = sky_to_tangent_arcsec(
            np.asarray([m.ra_deg for m in sub], dtype=np.float64),
            np.asarray([m.dec_deg for m in sub], dtype=np.float64),
            ra0,
            dec0,
        )
        barys = [
            np.asarray(m.obs_bary_au)
            if m.obs_bary_au is not None
            else observer_barycentric_au(m.mjd_tdb)
            for m in sub
        ]
        pf = np.array([parallax_factors(b, ra0, dec0) for b in barys], dtype=np.float64)
        p_a, p_d = pf[:, 0], pf[:, 1]
        dp_span = float(np.max(np.hypot(p_a[:, None] - p_a[None, :], p_d[:, None] - p_d[None, :])))
        dt_span, n_obs = float(np.ptp(dt)), len(indices)

        y = np.empty(2 * n_obs, dtype=np.float64)
        y[0::2], y[1::2] = xi, eta
        a5 = np.zeros((2 * n_obs, 5), dtype=np.float64)
        a5[0::2, 0] = a5[1::2, 1] = 1.0
        a5[0::2, 2], a5[1::2, 3] = dt, dt
        a5[0::2, 4], a5[1::2, 4] = p_a, p_d

        th2, cov2, c2, _ = _wls_solve(a5[:, :2], y, ws)
        bic2 = c2 + 2.0 * math.log(max(2 * n_obs, 2))

        th4, cov4, c4, bic4 = None, None, c2, math.inf
        if n_obs >= 2 and dt_span > 1e-4:
            plx_fix = (prior_parallax_mas * 1e-3) if prior_parallax_mas else 0.0
            y4 = y - plx_fix * a5[:, 4] if plx_fix != 0.0 else y
            th4, cov4, c4, _ = _wls_solve(a5[:, :4], y4, ws)
            bic4 = c4 + 4.0 * math.log(2 * n_obs)

        th5, cov5, c5, bic5 = None, None, math.inf, math.inf
        allow_5p = (
            fit_parallax is True and n_obs >= 3 and dt_span >= 0.25 and dp_span >= 0.30
        ) or (
            fit_parallax is None
            and n_obs >= 4
            and dt_span >= min_parallax_dt_yr
            and dp_span >= min_parallax_span_au
        )
        if fit_parallax is not False and allow_5p:
            ridge = np.zeros((5, 5), dtype=np.float64)
            ridge[4, 4] = 0.25  # weak zero-mean prior sigma=2.0 arcsec (§C.4)
            th5_t, cov5_t, c5_t, cond5 = _wls_solve(a5, y, ws, ridge=ridge)
            if cond5 < 1e6 and cov5_t[4, 4] > 0.0:
                pinned = (float(th5_t[4]) / max(math.sqrt(float(cov5_t[4, 4])), 1e-9)) < -2.0
                if pinned:
                    notes.append("Parallax active-set pinned to 0 mas (free fit < -2 sigma).")
                    th5 = np.append(th4 if th4 is not None else th5_t[:4], 0.0)
                    cov5 = np.zeros((5, 5), dtype=np.float64)
                    if cov4 is not None:
                        cov5[:4, :4] = cov4
                    cov5[4, 4] = cov5_t[4, 4]
                    c5 = c4 if th4 is not None else c5_t
                else:
                    th5, cov5, c5 = th5_t, cov5_t, c5_t
                bic5 = c5 + 5.0 * math.log(2 * n_obs)

        return dict(
            t0_mjd=t0_mjd,
            ra0=ra0,
            dec0=dec0,
            dt_span=dt_span,
            dp_span=dp_span,
            dt=dt,
            xi=xi,
            eta=eta,
            covs=covs,
            a5=a5,
            y=y,
            th2=th2,
            cov2=cov2,
            c2=c2,
            bic2=bic2,
            th4=th4,
            cov4=cov4,
            c4=c4,
            bic4=bic4,
            th5=th5,
            cov5=cov5,
            c5=c5,
            bic5=bic5,
        )

    fit = _fit_all(active)
    for _ in range(3):
        if len(active) < 4 or fit["th4"] is None:
            break
        use_5 = fit["th5"] is not None and (fit_parallax is True or fit["bic5"] < fit["bic4"] - 6.0)
        a_chk = fit["a5"] if use_5 else fit["a5"][:, :4]
        th_chk = fit["th5"] if use_5 else fit["th4"]
        cov_chk = fit["cov5"] if use_5 else fit["cov4"]
        plx_fix = (prior_parallax_mas * 1e-3) if (prior_parallax_mas and not use_5) else 0.0
        y_chk = fit["y"] - plx_fix * fit["a5"][:, 4] if plx_fix != 0.0 else fit["y"]
        rs = _leave_one_out_r(a_chk, y_chk - a_chk @ th_chk, fit["covs"], cov_chk)
        worst_local = int(np.argmax(rs))
        if rs[worst_local] <= outlier_sigma:
            break
        # Never drop an external long-baseline anchor unless a frame epoch wasn't the culprit
        if meas[active[worst_local]].source != "spherex" and len(active) == 4:
            break
        dropped.append(active.pop(worst_local))
        notes.append(
            f"Dropped PM_OUTLIER measurement index {dropped[-1]} (r={rs[worst_local]:.2f})."
        )
        fit = _fit_all(active)

    # Select preferred model among STATIC, LINEAR_4P, PARALLAX_5P (§C.4.3).
    chosen: Literal["STATIC", "LINEAR_4P", "PARALLAX_5P"] = "STATIC"
    th_use, cov_use, c_use, bic_use = fit["th2"], fit["cov2"], fit["c2"], fit["bic2"]
    k_use = 2
    if fit["th5"] is not None:
        plx_snr = float(fit["th5"][4]) / max(math.sqrt(max(float(fit["cov5"][4, 4]), 1e-18)), 1e-9)
        if fit_parallax is True or (fit["bic5"] < fit["bic4"] - 6.0 and plx_snr > 3.0):
            chosen = "PARALLAX_5P"
            th_use, cov_use, c_use, bic_use = fit["th5"], fit["cov5"], fit["c5"], fit["bic5"]
            k_use = 5
    if chosen == "STATIC" and fit["th4"] is not None:
        cov_mu4 = fit["cov4"][2:4, 2:4]
        mu_vec4 = fit["th4"][2:4]
        chi2_mu4 = float(mu_vec4 @ np.linalg.pinv(cov_mu4) @ mu_vec4)
        if fit["bic4"] < fit["bic2"] - 6.0 or chi2_mu4 > 9.0:
            chosen = "LINEAR_4P"
            th_use, cov_use, c_use, bic_use = fit["th4"], fit["cov4"], fit["c4"], fit["bic4"]
            k_use = 4

    xi0, eta0 = float(th_use[0]), float(th_use[1])
    ra_ref, dec_ref = tangent_arcsec_to_sky(xi0, eta0, fit["ra0"], fit["dec0"])
    if len(th_use) >= 4:
        pmra, pmdec = float(th_use[2]), float(th_use[3])
        cov_mu = cov_use[2:4, 2:4]
        chi2_mu = float(th_use[2:4] @ np.linalg.pinv(cov_mu) @ th_use[2:4])
    else:
        pmra, pmdec = 0.0, 0.0
        cov_mu = np.eye(2, dtype=np.float64) * 1e6
        chi2_mu = 0.0

    mu = math.hypot(pmra, pmdec)
    if mu > 1e-9 and len(th_use) >= 4:
        grad_mu = np.array([pmra / mu, pmdec / mu], dtype=np.float64)
        sig_mu = math.sqrt(max(float(grad_mu @ cov_mu @ grad_mu), 1e-16))
        grad_pa = np.array([pmdec / (mu**2), -pmra / (mu**2)], dtype=np.float64)
        sig_pa = math.degrees(math.sqrt(max(float(grad_pa @ cov_mu @ grad_pa), 1e-16)))
        pa_deg = math.degrees(math.atan2(pmra, pmdec)) % 360.0
    else:
        sig_mu = math.sqrt(max(float(0.5 * np.trace(cov_mu)), 1e-12)) if len(th_use) >= 4 else 1.0
        sig_pa = 180.0
        pa_deg = 0.0

    if chosen == "PARALLAX_5P":
        plx_mas = float(th_use[4]) * 1000.0
        sig_plx_mas = math.sqrt(max(float(cov_use[4, 4]), 1e-18)) * 1000.0
    else:
        plx_mas = float(prior_parallax_mas) if prior_parallax_mas is not None else 0.0
        sig_plx_mas = 0.0

    dist_pc = (
        (1000.0 / plx_mas)
        if (sig_plx_mas > 0.0 and plx_mas / sig_plx_mas >= 5.0 and plx_mas > 0.0)
        else None
    )
    v_tan = (4.74047 * mu / (plx_mas * 1e-3)) if dist_pc is not None else None

    active_meas = [meas[i] for i in active]
    vim_flagged, vim_r, vim_p = _check_vim(active_meas, fit["xi"], fit["eta"], fit["dt"])
    flags_out = 0
    if vim_flagged:
        flags_out |= int(DetectionFlag.VIM_CANDIDATE)
        notes.append(f"VIM_CANDIDATE: centroid wobble correlated with flux (r={vim_r:.3f}).")
    if dropped:
        flags_out |= int(DetectionFlag.PM_OUTLIER)

    delta_bic = float(fit["bic2"] - bic_use)
    dof = max(2 * len(active) - k_use, 1)
    is_mover = (chi2_mu > 25.0) and (delta_bic > 10.0) and (not vim_flagged)
    if len(active) == 2 and is_mover:
        has_cat = (
            bool(
                getattr(source, "catalog_id", None)
                or getattr(source, "gaia_id", None)
                or getattr(source, "catalog_matches", None)
            )
            or all(active_meas[i].source != "spherex" for i in range(len(active_meas)))
        )
        if not (corroborated or has_cat):
            is_mover = False
            notes.append("unvalidated: N=2 epochs (dof=0, chi2=0) without corroboration")

    return MotionFitResult(
        model_type=chosen,
        ref_epoch_mjd_tdb=fit["t0_mjd"],
        ref_epoch_jyear=mjd_to_jyear(fit["t0_mjd"]),
        ra_ref_deg=float(ra_ref) % 360.0,
        dec_ref_deg=float(dec_ref),
        pmra_arcsec_yr=pmra,
        pmdec_arcsec_yr=pmdec,
        mu_arcsec_yr=mu,
        sigma_mu_arcsec_yr=sig_mu,
        pa_deg=pa_deg,
        sigma_pa_deg=sig_pa,
        parallax_mas=plx_mas,
        sigma_parallax_mas=sig_plx_mas,
        distance_pc=dist_pc,
        v_tan_kms=v_tan,
        chi2=float(c_use),
        dof=dof,
        chi2_nu=float(c_use) / dof,
        chi2_mu=chi2_mu,
        bic=float(bic_use),
        delta_bic=delta_bic,
        cov_params=tuple(tuple(float(v) for v in row) for row in cov_use),
        n_epochs=len(active),
        n_outliers=len(dropped),
        outlier_indices=tuple(dropped),
        baseline_yr=fit["dt_span"],
        parallax_span_au=fit["dp_span"],
        is_mover=is_mover,
        vim_flagged=vim_flagged,
        vim_r=vim_r,
        vim_p_value=vim_p,
        flags=flags_out,
        notes=notes,
    )


def forced_photometry_at_sky(
    frame: FrameData,
    ra_deg: float,
    dec_deg: float,
    *,
    neighbors: Sequence[tuple[float, float, float | None]] | None = None,
) -> tuple[float, float, float, int]:
    """Fixed-position PSF photometry at `(ra_deg, dec_deg)` on `frame` (`flux, err, snr, flags`)."""
    bg = estimate_background_and_noise(frame)
    x_px, y_px = (float(v) for v in frame.wcs.world_to_pixel_values(ra_deg, dec_deg))
    ny, nx = frame.shape
    if not (-0.5 <= x_px <= nx - 0.5 and -0.5 <= y_px <= ny - 0.5):
        return 0.0, 1e9, 0.0, 0
    ix, iy = int(np.clip(round(x_px), 0, nx - 1)), int(np.clip(round(y_px), 0, ny - 1))
    sat = frame.flags is not None and bool(
        np.any(
            frame.flags[max(0, iy - 1) : min(ny, iy + 2), max(0, ix - 1) : min(nx, ix + 2)]
            & int(DetectionFlag.SATURATED)
        )
    )
    fwhm_px = _FWHM_PER_SIGMA * frame.psf_sigma_px
    prf = CircularGaussianPRF(fwhm=fwhm_px)
    prf.x_0.fixed = prf.y_0.fixed = True
    psf_area = 2.0 * math.pi * frame.psf_sigma_px**2
    xs, ys = [x_px], [y_px]
    fls = [max(1.0, float(bg.subtracted[iy, ix]) * psf_area)]
    for nx_px, ny_px, nf in neighbors or ():
        if math.hypot(nx_px - x_px, ny_px - y_px) > 0.05:
            xs.append(nx_px)
            ys.append(ny_px)
            nix, niy = int(np.clip(round(nx_px), 0, nx - 1)), int(np.clip(round(ny_px), 0, ny - 1))
            fls.append(
                float(nf) if nf is not None else max(1.0, float(bg.subtracted[niy, nix]) * psf_area)
            )

    fit_mask = (~bg.quality_mask) | (
        (frame.flags & int(DetectionFlag.SATURATED)) != 0 if frame.flags is not None else False
    )
    side = max(7, min(11, ny - (ny % 2 == 0), nx - (nx % 2 == 0)))
    phot = PSFPhotometry(
        prf,
        fit_shape=(side, side),
        grouper=SourceGrouper(min_separation=5.0) if len(xs) > 1 else None,
        aperture_radius=max(2.5, 1.3 * fwhm_px),
    )
    res = phot(
        bg.subtracted,
        mask=fit_mask,
        error=bg.rms,
        init_params=Table({"x_0": xs, "y_0": ys, "flux_0": fls}),
    )
    flux = float(res["flux_fit"][0]) if np.isfinite(res["flux_fit"][0]) else 0.0
    ferr_raw = (
        float(res["flux_err"][0])
        if np.isfinite(res["flux_err"][0]) and res["flux_err"][0] > 0.0
        else (2.0 * math.sqrt(math.pi) * frame.psf_sigma_px * float(bg.rms[iy, ix]))
    )
    ferr = math.hypot(ferr_raw, 0.03 * abs(flux))
    return flux, ferr, flux / max(ferr, 1e-12), int(DetectionFlag.SATURATED) if sat else 0


def measure_variability_period(
    source: Any,
    *,
    ra_deg: float | None = None,
    dec_deg: float | None = None,
    min_period_days: float = 2.0,
    max_period_days: float = 2000.0,
) -> PeriodogramResult:
    """Measure variability period (`d`), amplitude (`mag`), chi2_var, and FAP (§D.4)."""
    pts: list[tuple[float, float, float, str]] = []
    if hasattr(source, "detections"):
        for d in source.detections:
            if (
                not (getattr(d, "flags", 0) & int(DetectionFlag.SATURATED))
                and getattr(d, "flux", None) is not None
                and (fe := getattr(d, "flux_err", None))
                and float(fe) > 0.0
                and getattr(d, "mjd_tdb", None) is not None
            ):
                pts.append(
                    (
                        float(d.mjd_tdb),
                        float(d.flux),
                        float(fe),
                        str(getattr(d, "band_id", None) or "ALL"),
                    )
                )
    elif isinstance(source, Sequence):
        if ra_deg is None or dec_deg is None:
            raise ValueError(
                "`ra_deg` and `dec_deg` required for frames in `measure_variability_period`."
            )
        for f in source:
            fr = as_frame_data(f)
            fl, fe, _, flg = forced_photometry_at_sky(fr, ra_deg, dec_deg)
            if not (flg & int(DetectionFlag.SATURATED)) and fe > 0.0:
                pts.append((fr.epoch.mjd_tdb_mid, fl, fe, fr.band_id or "ALL"))
    else:
        raise TypeError(f"Unsupported input to measure_variability_period: {type(source)!r}")
    if len(pts) < 3:
        raise ValueError(
            "Need at least 3 unsaturated photometric measurements to compute periodogram."
        )

    pts.sort(key=lambda p: p[0])
    t_arr = np.array([p[0] for p in pts], dtype=np.float64)
    f_arr = np.array([p[1] for p in pts], dtype=np.float64)
    e_arr = np.array([p[2] for p in pts], dtype=np.float64)
    chi2_var, _, amp_mag = weighted_flux_variability(f_arr, e_arr)

    dts = np.diff(t_arr)
    dts = dts[dts > 1e-3]
    cadence = float(np.median(dts)) if len(dts) > 0 else 1.0
    eff_min_p = (
        max(min_period_days, 2.0 * cadence * 1.01)
        if (len(dts) >= 3 and float(np.std(dts)) < 0.05 * cadence)
        else min_period_days
    )
    ls = LombScargle(t_arr, f_arr, e_arr)
    freq, power = ls.autopower(
        minimum_frequency=1.0 / max(max_period_days, eff_min_p * 1.5),
        maximum_frequency=1.0 / eff_min_p,
        samples_per_peak=12,
    )
    best_i = int(np.argmax(power))
    f_best, p_max = float(freq[best_i]), float(power[best_i])
    stetson = None
    if len({p[3] for p in pts}) >= 2:
        w = 1.0 / (e_arr**2)
        f_bar = float(np.sum(w * f_arr) / np.sum(w))
        resid_s = math.sqrt(len(f_arr) / max(len(f_arr) - 1, 1)) * (f_arr - f_bar) / e_arr
        pair_mask = np.array(
            [
                (t_arr[i + 1] - t_arr[i] <= 1.0) and (pts[i][3] != pts[i + 1][3])
                for i in range(len(pts) - 1)
            ],
            dtype=bool,
        )
        if np.any(pair_mask):
            prod = (resid_s[:-1] * resid_s[1:])[pair_mask]
            stetson = float(np.mean(np.sign(prod) * np.sqrt(np.abs(prod))))

    return PeriodogramResult(
        1.0 / f_best,
        f_best,
        p_max,
        float(ls.false_alarm_probability(p_max)),
        amp_mag,
        chi2_var,
        len(t_arr),
        stetson,
    )


def _trail_resid(
    p: NDArray[np.float64],
    xo: NDArray[np.float64],
    yo: NDArray[np.float64],
    zo: NDArray[np.float64],
    eo: NDArray[np.float64],
    cx_s: float,
    cy_s: float,
    norm_g: float,
    inv2: float,
) -> NDArray[np.float64]:
    flux_p, ux, uy, len_p, ang_p = (float(v) for v in p)
    ts = np.linspace(-0.5, 0.5, 17) * len_p
    sx, sy = cx_s + ux + ts * math.cos(ang_p), cy_s + uy + ts * math.sin(ang_p)
    d2 = (xo[:, None] - sx[None, :]) ** 2 + (yo[:, None] - sy[None, :]) ** 2
    mod = flux_p * norm_g * np.mean(np.exp(-d2 * inv2), axis=1)
    return np.asarray((mod - zo) / eo, dtype=np.float64)


def detect_streaks(
    frame_source: Any,
    *,
    min_elongation: float = 1.5,
    min_snr: float = 5.0,
    min_length_px: float = 2.0,
) -> list[StreakDetection]:
    """Detect trailed movers in one exposure via streak filter + Radon + WLS fit (§H.1.1)."""
    import sep
    from scipy.ndimage import rotate

    frame = as_frame_data(frame_source)
    bg = estimate_background_and_noise(frame)
    ny, nx = frame.shape
    bad_mask = (~bg.quality_mask) | (
        (frame.flags & int(DetectionFlag.SATURATED)) != 0 if frame.flags is not None else False
    )
    sub, err = (
        np.ascontiguousarray(bg.subtracted, dtype=np.float64),
        np.ascontiguousarray(bg.rms, dtype=np.float64),
    )
    psf_k = estimate_psf_kernel(frame.psf_sigma_px, frame.psf, size=11)
    objs = sep.extract(
        sub,
        thresh=1.5,
        err=err,
        mask=bad_mask,
        minarea=4,
        filter_kernel=psf_k,
        filter_type="matched",
        clean=True,
    )
    snr_mf = matched_filter_snr(sub, bg.variance, psf_k, quality_mask=~bad_mask)
    pixscale = frame.wcs_metrics.pixscale_arcsec
    sig_psf = max(frame.psf_sigma_px, 0.5)
    inv2, norm_g = 1.0 / (2.0 * sig_psf**2), 1.0 / (2.0 * math.pi * sig_psf**2)
    out: list[StreakDetection] = []

    for obj in objs:
        a, b = max(float(obj["a"]), 0.2), max(float(obj["b"]), 0.2)
        if a / b < min_elongation or a < 1.8 * frame.psf_sigma_px:
            continue
        xc0, yc0, th0 = float(obj["x"]), float(obj["y"]), float(obj["theta"])
        ix, iy = int(round(xc0)), int(round(yc0))
        if not (0 <= ix < nx and 0 <= iy < ny):
            continue
        x0, x1, y0, y1 = max(0, ix - 10), min(nx, ix + 11), max(0, iy - 10), min(ny, iy + 11)
        stamp, st_err, q_st = (
            sub[y0:y1, x0:x1],
            np.maximum(err[y0:y1, x0:x1], 1e-9),
            ~bad_mask[y0:y1, x0:x1],
        )
        if int(np.sum(q_st)) < 12:
            continue

        med_err = max(float(np.median(st_err)), 1e-9)
        best_radon = max(
            float(
                np.max(
                    np.sum(
                        rotate(
                            stamp,
                            math.degrees(ang),
                            reshape=False,
                            order=1,
                            mode="constant",
                            cval=0.0,
                        ),
                        axis=1,
                    )
                )
                / (math.sqrt(max(stamp.shape[1], 1)) * med_err)
            )
            for ang in np.linspace(th0 - 0.5, th0 + 0.5, 11)
        )
        l_init = max(min_length_px, min(18.0, 2.5 * math.sqrt(max(a**2 - b**2, 0.5))))
        f_init = max(10.0, float(np.sum(np.maximum(stamp[q_st], 0.0))))
        yy_s, xx_s = np.mgrid[0 : stamp.shape[0], 0 : stamp.shape[1]]
        xo, yo, zo, eo = (
            xx_s[q_st].astype(np.float64),
            yy_s[q_st].astype(np.float64),
            stamp[q_st].astype(np.float64),
            st_err[q_st].astype(np.float64),
        )
        lsq = least_squares(
            _trail_resid,
            x0=np.array([f_init, xc0 - ix, yc0 - iy, l_init, th0]),
            args=(xo, yo, zo, eo, float(ix - x0), float(iy - y0), norm_g, inv2),
            bounds=([0.0, -3.0, -3.0, 1.0, -math.pi], [np.inf, 3.0, 3.0, 22.0, math.pi]),
            xtol=1e-4,
            ftol=1e-4,
            max_nfev=80,
        )
        flux_f, ux_f, uy_f, len_f, ang_f = (float(v) for v in lsq.x)
        chi2_nu = float(np.sum(lsq.fun**2)) / max(len(zo) - 5, 1)
        try:
            cov_s = np.linalg.pinv(lsq.jac.T @ lsq.jac) * max(1.0, chi2_nu)
            sig_f, sig_l = (
                math.sqrt(max(float(cov_s[0, 0]), 1e-6)),
                math.sqrt(max(float(cov_s[3, 3]), 1e-4)),
            )
            sig_ang_deg = math.degrees(math.sqrt(max(float(cov_s[4, 4]), 1e-6)))
        except np.linalg.LinAlgError:
            continue

        snr_s = max(flux_f / max(sig_f, 1e-9), float(snr_mf[iy, ix]))
        if snr_s < min_snr or len_f < min_length_px:
            continue

        x_mid, y_mid = ix + ux_f, iy + uy_f
        ra_m, dec_m = (float(v) for v in frame.wcs.pixel_to_world_values(x_mid, y_mid))
        v_sky = local_wcs_jacobian(frame.wcs, x_mid, y_mid) @ np.array(
            [math.cos(ang_f), math.sin(ang_f)], dtype=np.float64
        )
        v_norm = float(np.hypot(*v_sky))
        scale = v_norm if v_norm > 0 else pixscale
        len_arcsec = len_f * scale
        exptime = frame.epoch.exptime_s
        rate = (len_arcsec / exptime) * 3600.0 if exptime and exptime > 0.0 else None
        sig_rate = ((sig_l * scale) / exptime) * 3600.0 if exptime and exptime > 0.0 else None
        out.append(
            StreakDetection(
                x_mid,
                y_mid,
                ra_m % 360.0,
                dec_m,
                len_f,
                len_arcsec,
                math.degrees(math.atan2(float(v_sky[0]), float(v_sky[1]))) % 180.0,
                sig_ang_deg,
                rate,
                sig_rate,
                flux_f,
                snr_s,
                best_radon,
                chi2_nu,
                frame.epoch.mjd_tdb_mid,
                exptime,
            )
        )
    return out


def _expand_multi_hdu_frames(source: Path | str | bytes | fits.HDUList | Any) -> list[FrameData]:
    unwrapped = unwrap_frame_source(source)
    if isinstance(unwrapped, FrameData):
        return [unwrapped]
    if isinstance(unwrapped, Sequence) and not isinstance(
        unwrapped, (str, bytes, Path, fits.HDUList)
    ):
        return [as_frame_data(item) for item in unwrapped]

    from sphx_core.calib import _open_hdul
    from sphx_core.timeutil import parse_fits_epoch
    from sphx_core.wcs import extract_wcs_metrics

    skip_ext = {
        "VARIANCE",
        "VAR",
        "ERR",
        "ERROR",
        "IVAR",
        "WEIGHT",
        "RMS",
        "FLAGS",
        "FLAG",
        "MASK",
        "DQ",
        "BPM",
        "ZODI",
        "PSF",
        "WCS-WAVE",
        "WAVE",
    }
    hdul, should_close = _open_hdul(unwrapped)
    try:
        sci_hdus = [
            h
            for h in hdul
            if getattr(h, "data", None) is not None
            and np.asarray(h.data).ndim >= 2
            and (h.name or "PRIMARY").upper() not in skip_ext
        ]
        if len(sci_hdus) == 1 and np.asarray(sci_hdus[0].data).ndim == 3:
            cube, base = np.asarray(sci_hdus[0].data, dtype=np.float64), sci_hdus[0].header
            dt_step = float(base.get("EXPTIME", 100.0) or 100.0)
            ep0, _ = parse_fits_epoch([base, hdul[0].header])
            w_base = WCS(base).celestial
            if (
                ep0 is not None
                and w_base.has_celestial
                and extract_wcs_metrics(
                    w_base.to_header(relax=True), shape=(cube.shape[1], cube.shape[2])
                )
                is not None
            ):
                frames: list[FrameData] = []
                for k in range(cube.shape[0]):
                    sub_hdr = base.copy()
                    t_k = Time(ep0.mjd_tdb_mid + k * dt_step / 86400.0, format="mjd", scale="tdb")
                    sub_hdr["DATE-OBS"], sub_hdr["MJD-OBS"] = str(t_k.utc.isot), float(t_k.utc.mjd)
                    frames.append(
                        load_frame(
                            fits.HDUList(
                                [
                                    fits.PrimaryHDU(),
                                    fits.ImageHDU(data=cube[k], header=sub_hdr, name="IMAGE"),
                                ]
                            )
                        )
                    )
                return frames
        if len(sci_hdus) > 1:
            seen_epochs = {
                round(ep.mjd_tdb_mid, 7)
                for h in sci_hdus
                if (ep := parse_fits_epoch([h.header, hdul[0].header])[0]) is not None
            }
            if len(seen_epochs) > 1:
                frames = []
                for h in sci_hdus:
                    hdr = h.header.copy()
                    for key in ("DATE-OBS", "MJD-OBS", "EXPTIME", "TIMESYS", "CRVAL1", "CRVAL2"):
                        if key not in hdr and key in hdul[0].header:
                            hdr[key] = hdul[0].header[key]
                    frames.append(
                        load_frame(
                            fits.HDUList(
                                [
                                    fits.PrimaryHDU(),
                                    fits.ImageHDU(data=h.data, header=hdr, name="IMAGE"),
                                ]
                            )
                        )
                    )
                return frames
    finally:
        if should_close:
            hdul.close()
    return [as_frame_data(unwrapped)]


def check_single_fits_motion(
    source: Any,
    *,
    min_elongation: float = 1.5,
    min_snr: float = 5.0,
    max_visit_days: float = 10.0,
) -> SingleFitsMotionResult:
    """Run §H.1 single-FITS streak, multi-HDU, and intra-visit tracklet motion check."""
    frames = _expand_multi_hdu_frames(source)
    streaks = [
        s
        for fr in frames
        for s in detect_streaks(fr, min_elongation=min_elongation, min_snr=min_snr)
    ]
    exts = [extract_sources(fr, min_snr=min_snr) for fr in frames]
    tracklets: list[IntraVisitTracklet] = []
    if len(exts) >= 3:
        exts_sorted = sorted(enumerate(exts), key=lambda x: x[1].epoch_mjd_tdb)
        span_d = exts_sorted[-1][1].epoch_mjd_tdb - exts_sorted[0][1].epoch_mjd_tdb
        if 1e-4 < span_d <= max_visit_days:
            first_idx, first_ext = exts_sorted[0]
            last_idx, last_ext = exts_sorted[-1]
            ra0, dec0 = float(first_ext.wcs.wcs.crval[0]), float(first_ext.wcs.wcs.crval[1])
            dt_tot_hr = (last_ext.epoch_mjd_tdb - first_ext.epoch_mjd_tdb) * 24.0
            for d0 in first_ext.detections:
                if (d0.flags & int(HARD_BAD_MASK)) or sum(
                    any(
                        math.hypot(
                            *sky_to_tangent_arcsec(d.ra_deg, d.dec_deg, d0.ra_deg, d0.dec_deg)
                        )
                        < 1.0
                        for d in ext.detections
                    )
                    for _, ext in exts_sorted[1:]
                ) >= 2:
                    continue
                for d1 in last_ext.detections:
                    if d1.flags & int(HARD_BAD_MASK):
                        continue
                    xi, eta = sky_to_tangent_arcsec(
                        np.array([d0.ra_deg, d1.ra_deg]),
                        np.array([d0.dec_deg, d1.dec_deg]),
                        ra0,
                        dec0,
                    )
                    rate = math.hypot(float(xi[1] - xi[0]), float(eta[1] - eta[0])) / max(
                        dt_tot_hr, 1e-6
                    )
                    if not (0.1 <= rate <= 1000.0):
                        continue
                    vx, vy = float(xi[1] - xi[0]) / dt_tot_hr, float(eta[1] - eta[0]) / dt_tot_hr
                    matched = [(first_idx, first_ext.epoch_mjd_tdb, d0)]
                    for mid_idx, mid_ext in exts_sorted[1:-1]:
                        if not mid_ext.detections:
                            continue
                        dt_m = (mid_ext.epoch_mjd_tdb - first_ext.epoch_mjd_tdb) * 24.0
                        px, py = float(xi[0]) + vx * dt_m, float(eta[0]) + vy * dt_m
                        mxs, mys = sky_to_tangent_arcsec(
                            np.array([dm.ra_deg for dm in mid_ext.detections]),
                            np.array([dm.dec_deg for dm in mid_ext.detections]),
                            ra0,
                            dec0,
                        )
                        dists = np.hypot(mxs - px, mys - py)
                        bi = int(np.argmin(dists))
                        if float(dists[bi]) < 3.0:
                            matched.append((mid_idx, mid_ext.epoch_mjd_tdb, mid_ext.detections[bi]))
                    matched.append((last_idx, last_ext.epoch_mjd_tdb, d1))
                    if len(matched) >= 3:
                        mm = [
                            MotionMeasurement(
                                tm, dm.ra_deg, dm.dec_deg, dm.cov_sky_arcsec2, flags=dm.flags
                            )
                            for _, tm, dm in matched
                        ]
                        mfit = fit_motion(mm, fit_parallax=False)
                        r_hr, sr_hr = mfit.mu_arcsec_yr / 8766.0, mfit.sigma_mu_arcsec_yr / 8766.0
                        if mfit.chi2_nu < 3.0 and (r_hr / max(sr_hr, 1e-9)) > 5.0:
                            tracklets.append(
                                IntraVisitTracklet(
                                    epoch_indices=tuple(ei for ei, _, _ in matched),
                                    mjds_tdb=tuple(tm for _, tm, _ in matched),
                                    ra_degs=tuple(dm.ra_deg for _, _, dm in matched),
                                    dec_degs=tuple(dm.dec_deg for _, _, dm in matched),
                                    rate_arcsec_hr=r_hr,
                                    sigma_rate_arcsec_hr=sr_hr,
                                    pa_deg=mfit.pa_deg,
                                    chi2_nu=mfit.chi2_nu,
                                    snr_mean=float(np.mean([dm.snr for _, _, dm in matched])),
                                )
                            )
    notes = [f"Detected {len(streaks)} trailed source(s) (PA mod 180 deg)."] if streaks else []
    if tracklets:
        notes.append(f"Linked {len(tracklets)} linear intra-visit tracklet(s).")
    return SingleFitsMotionResult(streaks, tracklets, bool(streaks or tracklets), notes)


__all__ = [
    "IntraVisitTracklet",
    "MotionFitResult",
    "MotionMeasurement",
    "PeriodogramResult",
    "SingleFitsMotionResult",
    "StreakDetection",
    "as_frame_data",
    "check_single_fits_motion",
    "detect_streaks",
    "fit_motion",
    "forced_photometry_at_sky",
    "measure_variability_period",
    "mjd_to_jyear",
    "observer_barycentric_au",
    "parallax_factors",
    "unwrap_frame_source",
    "weighted_flux_variability",
]
