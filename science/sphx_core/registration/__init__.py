"""Per-array astrometric plate-solution refinement against Gaia DR3 (§C.2)."""

from __future__ import annotations

import math
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord, get_body_barycentric
from astropy.io import fits
from astropy.stats import mad_std
from astropy.table import Table
from astropy.time import Time
from astropy.wcs import WCS
from astropy.wcs.utils import fit_wcs_from_points
from erfa.core import ErfaWarning
from numpy.typing import NDArray
from scipy.interpolate import RectBivariateSpline
from scipy.optimize import linear_sum_assignment

from sphx_core.calib import HARD_BAD_MASK, DetectionFlag, FrameData, load_frame
from sphx_core.extraction import ExtractedSource, extract_sources
from sphx_core.wcs import (
    ARCSEC_PER_RAD,
    WCSMetrics,
    extract_wcs_metrics,
    sky_to_tangent_arcsec,
    tangent_arcsec_to_sky,
)


@dataclass(frozen=True)
class ReferenceStar:
    """Propagated reference-catalog star at observation epoch (`ra_deg`, `dec_deg` in ICRS deg)."""

    source_id: int | str
    ra_deg: float
    dec_deg: float
    sigma_cat_arcsec: float = 0.002
    g_mag: float | None = None
    flux: float | None = None


@dataclass(frozen=True)
class RegistrationResult:
    """Refined frame WCS, residual RMS metrics (arcsec), and quality status (§C.2)."""

    wcs: WCS
    wcs_metrics: WCSMetrics
    sigma_reg_arcsec: float
    rms_2d_arcsec: float
    rms_ra_arcsec: float
    rms_dec_arcsec: float
    median_residual_arcsec: float
    n_reference_input: int
    n_matched: int
    n_kept: int
    model_used: str
    converged: bool
    degraded: bool
    astrometry_poor: bool
    wcs_unrefined: bool
    array_id: int | None
    pixel_phase_applied: bool
    residual_arcsec: tuple[float, ...] = ()
    warnings: list[str] = field(default_factory=list)


def _get_field(row: Any, keys: tuple[str, ...]) -> Any:
    for key in keys:
        if isinstance(row, Mapping):
            if key in row and row[key] is not None:
                return row[key]
        elif hasattr(row, "colnames") and key in row.colnames:
            val = row[key]
            if val is not None and not np.ma.is_masked(val):
                return val
    return None


def _opt_float(row: Any, keys: tuple[str, ...]) -> float | None:
    val = _get_field(row, keys)
    if val is None or isinstance(val, bool):
        return None
    try:
        num = float(val)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) else None


def propagate_gaia_catalog(
    reference_catalog: Sequence[Mapping[str, Any]] | Table,
    mjd_tdb_mid: float,
    *,
    barycentric_earth_au: NDArray[np.float64] | None = None,
    apply_filters: bool = True,
    max_ruwe: float = 1.4,
    g_mag_range: tuple[float, float] = (12.0, 17.5),
) -> list[ReferenceStar]:
    """Filter Gaia DR3 / pre-propagated reference stars and propagate to `mjd_tdb_mid` (TDB MJD)."""
    rows: Any = reference_catalog
    if len(rows) == 0:
        return []

    t_obs = Time(mjd_tdb_mid, format="mjd", scale="tdb")
    bary = (
        np.asarray(barycentric_earth_au, dtype=np.float64)
        if barycentric_earth_au is not None
        else get_body_barycentric("earth", t_obs).xyz.to_value(u.au)
    )

    out: list[ReferenceStar] = []
    for idx, row in enumerate(rows):
        ra_val = _opt_float(row, ("ra_deg", "ra", "RA"))
        dec_val = _opt_float(row, ("dec_deg", "dec", "DEC"))
        if ra_val is None or dec_val is None:
            continue

        ruwe = _opt_float(row, ("ruwe", "RUWE"))
        solved = _opt_float(row, ("astrometric_params_solved",))
        g_mag = _opt_float(row, ("phot_g_mean_mag", "g_mag", "G"))
        flux = _opt_float(row, ("flux",))

        if apply_filters:
            if ruwe is not None and ruwe >= max_ruwe:
                continue
            if solved is not None and solved < 31:
                continue
            if g_mag is not None and not (g_mag_range[0] < g_mag < g_mag_range[1]):
                continue

        sid = _get_field(row, ("gaia_id", "source_id", "id"))
        src_id: int | str = sid if sid is not None else idx

        pmra_mas = _opt_float(row, ("pmra", "pmra_mas_yr"))
        if pmra_mas is None:
            pmra_as = _opt_float(row, ("pmra_arcsec_yr",))
            pmra_mas = pmra_as * 1000.0 if pmra_as is not None else 0.0
        pmdec_mas = _opt_float(row, ("pmdec", "pmdec_mas_yr"))
        if pmdec_mas is None:
            pmdec_as = _opt_float(row, ("pmdec_arcsec_yr",))
            pmdec_mas = pmdec_as * 1000.0 if pmdec_as is not None else 0.0
        plx_mas = _opt_float(row, ("parallax", "parallax_mas")) or 0.0

        ref_mjd = _opt_float(row, ("ref_epoch_mjd",))
        if ref_mjd is None:
            ref_jyr = _opt_float(row, ("ref_epoch", "ref_epoch_year"))
            if ref_jyr is not None:
                ref_mjd = float(Time(ref_jyr, format="jyear", scale="tdb").mjd)

        if (
            ref_mjd is not None
            and abs(mjd_tdb_mid - ref_mjd) > 1e-4
            and (pmra_mas != 0.0 or pmdec_mas != 0.0 or plx_mas > 0.0)
        ):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", ErfaWarning)
                sc = SkyCoord(
                    ra=ra_val * u.deg,
                    dec=dec_val * u.deg,
                    pm_ra_cosdec=pmra_mas * (u.mas / u.yr),
                    pm_dec=pmdec_mas * (u.mas / u.yr),
                    obstime=Time(ref_mjd, format="mjd", scale="tdb"),
                    frame="icrs",
                ).apply_space_motion(new_obstime=t_obs)
            ra_e, dec_e = float(sc.ra.deg) % 360.0, float(sc.dec.deg)
        else:
            ra_e, dec_e = ra_val % 360.0, dec_val

        ra_rad, dec_rad = math.radians(ra_e), math.radians(dec_e)
        east = np.array([-math.sin(ra_rad), math.cos(ra_rad), 0.0])
        north = np.array(
            [
                -math.sin(dec_rad) * math.cos(ra_rad),
                -math.sin(dec_rad) * math.sin(ra_rad),
                math.cos(dec_rad),
            ]
        )
        b_dot_e = float(np.dot(bary, east))
        b_dot_n = float(np.dot(bary, north))
        p_factor_sq = b_dot_e**2 + b_dot_n**2

        if plx_mas > 0.0:
            plx_rad = (plx_mas * 1e-3) / ARCSEC_PER_RAD
            ra_e = (
                ra_e
                - math.degrees(
                    plx_rad * b_dot_e / max(math.cos(dec_rad), 1e-8)
                )
            ) % 360.0
            dec_e = dec_e - math.degrees(plx_rad * b_dot_n)

        dt_yr = abs((mjd_tdb_mid - ref_mjd) / 365.25) if ref_mjd is not None else 0.0
        sig0 = (
            max(_opt_float(row, ("ra_error",)) or 0.05, _opt_float(row, ("dec_error",)) or 0.05)
            * 1e-3
        )
        sig_mu = (
            max(_opt_float(row, ("pmra_error",)) or 0.05, _opt_float(row, ("pmdec_error",)) or 0.05)
            * 1e-3
        )
        sig_plx = (_opt_float(row, ("parallax_error",)) or 0.05) * 1e-3
        sig_cat = math.sqrt(sig0**2 + (dt_yr * sig_mu) ** 2 + (sig_plx**2) * p_factor_sq)
        out.append(
            ReferenceStar(
                source_id=src_id,
                ra_deg=ra_e,
                dec_deg=dec_e,
                sigma_cat_arcsec=max(sig_cat, 1e-4),
                g_mag=g_mag,
                flux=flux,
            )
        )
    return out


def _filter_isolated(
    stars: list[ReferenceStar], radius_arcsec: float, min_blend_arcsec: float
) -> list[ReferenceStar]:
    if len(stars) <= 1:
        return stars
    coords = SkyCoord(
        ra=[s.ra_deg for s in stars] * u.deg,
        dec=[s.dec_deg for s in stars] * u.deg,
        frame="icrs",
    )
    sep_mat = coords[:, None].separation(coords[None, :]).arcsec

    def _run(rad: float) -> list[ReferenceStar]:
        kept: list[ReferenceStar] = []
        for i, si in enumerate(stars):
            blocked = False
            for j, sj in enumerate(stars):
                if i == j or sep_mat[i, j] >= rad:
                    continue
                if si.g_mag is not None and sj.g_mag is not None:
                    if sj.g_mag - si.g_mag < 3.0:
                        blocked = True
                        break
                elif si.flux is not None and sj.flux is not None and si.flux > 0 and sj.flux > 0:
                    if 2.5 * math.log10(si.flux / sj.flux) < 3.0:
                        blocked = True
                        break
                else:
                    blocked = True
                    break
            if not blocked:
                kept.append(si)
        return kept

    for r in (radius_arcsec, max(min_blend_arcsec, 12.0), min_blend_arcsec):
        if r > radius_arcsec:
            continue
        res = _run(r)
        if len(res) >= 10 or r == min_blend_arcsec:
            return res
    return stars


def _match_detections_to_refs(
    dets: Sequence[ExtractedSource],
    refs: Sequence[ReferenceStar],
    wcs_init: WCS,
    max_radius_arcsec: float,
) -> list[tuple[ExtractedSource, ReferenceStar]]:
    if not dets or not refs:
        return []

    ref_ra = np.array([r.ra_deg for r in refs], dtype=np.float64)
    ref_dec = np.array([r.dec_deg for r in refs], dtype=np.float64)
    rx, ry = wcs_init.world_to_pixel_values(ref_ra, ref_dec)
    rx_arr = np.asarray(rx, dtype=np.float64)
    ry_arr = np.asarray(ry, dtype=np.float64)

    ra0, dec0 = float(wcs_init.wcs.crval[0]), float(wcs_init.wcs.crval[1])
    d_xi, d_eta = sky_to_tangent_arcsec(
        np.array([d.ra_deg for d in dets], dtype=np.float64),
        np.array([d.dec_deg for d in dets], dtype=np.float64),
        ra0,
        dec0,
    )
    r_xi, r_eta = sky_to_tangent_arcsec(ref_ra, ref_dec, ra0, dec0)
    det_t = np.column_stack((d_xi, d_eta))
    ref_t = np.column_stack((r_xi, r_eta))

    diff = det_t[:, None, :] - ref_t[None, :, :]
    dist0 = np.hypot(diff[:, :, 0], diff[:, :, 1])
    best_j = np.argmin(dist0, axis=1)
    best_d = dist0[np.arange(len(dets)), best_j]
    near = best_d <= max(max_radius_arcsec, 12.0)
    shift = (
        np.median(det_t[near] - ref_t[best_j[near]], axis=0) if np.sum(near) >= 3 else np.zeros(2)
    )

    cost = np.hypot(diff[:, :, 0] - shift[0], diff[:, :, 1] - shift[1])
    gated = np.where(cost <= max_radius_arcsec, cost, 1e6)
    row_ind, col_ind = linear_sum_assignment(gated)
    return [
        (dets[int(r)], refs[int(c)])
        for r, c in zip(row_ind, col_ind, strict=True)
        if gated[r, c] < max_radius_arcsec and np.isfinite(rx_arr[c]) and np.isfinite(ry_arr[c])
    ]


def _apply_pixel_phase(
    x_px: NDArray[np.float64],
    y_px: NDArray[np.float64],
    dx_px: NDArray[np.float64],
    dy_px: NDArray[np.float64],
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    phi_x, phi_y = x_px - np.floor(x_px + 0.5), y_px - np.floor(y_px + 0.5)
    centers = np.linspace(-0.4, 0.4, 5)
    edges = np.linspace(-0.5, 0.5, 6)
    ix, iy = np.clip(np.digitize(phi_x, edges) - 1, 0, 4), np.clip(np.digitize(phi_y, edges) - 1, 0, 4)
    lut_x, lut_y = np.zeros((5, 5), dtype=np.float64), np.zeros((5, 5), dtype=np.float64)
    for bx in range(5):
        for by in range(5):
            sel = (ix == bx) & (iy == by)
            if np.sum(sel) >= 2:
                lut_x[bx, by] = float(np.median(dx_px[sel]))
                lut_y[bx, by] = float(np.median(dy_px[sel]))
    spl_x = RectBivariateSpline(centers, centers, lut_x, kx=3, ky=3)
    spl_y = RectBivariateSpline(centers, centers, lut_y, kx=3, ky=3)
    return x_px - spl_x.ev(phi_x, phi_y), y_px - spl_y.ev(phi_x, phi_y)


def _fit_tangent_transform(
    wcs_base: WCS,
    x_px: NDArray[np.float64],
    y_px: NDArray[np.float64],
    ra_ref: NDArray[np.float64],
    dec_ref: NDArray[np.float64],
    inv_var: NDArray[np.float64],
    mode: str,
) -> tuple[WCS, float]:
    w = wcs_base.deepcopy()
    for _ in range(3):
        ra0, dec0 = float(w.wcs.crval[0]), float(w.wcs.crval[1])
        ra_c, dec_c = w.pixel_to_world_values(x_px, y_px)
        xi_c, eta_c = sky_to_tangent_arcsec(ra_c, dec_c, ra0, dec0)
        xi_r, eta_r = sky_to_tangent_arcsec(ra_ref, dec_ref, ra0, dec0)
        n = len(x_px)
        sq_w = np.sqrt(inv_var)

        if mode == "shift_2param":
            cx = float(np.sum(inv_var * (xi_r - xi_c)) / np.sum(inv_var))
            cy = float(np.sum(inv_var * (eta_r - eta_c)) / np.sum(inv_var))
            mat = np.eye(2, dtype=np.float64)
        elif mode == "similarity_4param":
            a_mat = np.zeros((2 * n, 4), dtype=np.float64)
            y_vec = np.empty(2 * n, dtype=np.float64)
            a_mat[0::2, 0] = 1.0
            a_mat[1::2, 1] = 1.0
            a_mat[0::2, 2], a_mat[0::2, 3] = xi_c, -eta_c
            a_mat[1::2, 2], a_mat[1::2, 3] = eta_c, xi_c
            y_vec[0::2], y_vec[1::2] = xi_r, eta_r
            aw = a_mat * np.repeat(sq_w, 2)[:, None]
            yw = y_vec * np.repeat(sq_w, 2)
            p, *_ = np.linalg.lstsq(aw, yw, rcond=None)
            cx, cy, a, b = (float(v) for v in p)
            mat = np.array([[a, -b], [b, a]], dtype=np.float64)
        else:
            a_mat = np.zeros((2 * n, 6), dtype=np.float64)
            y_vec = np.empty(2 * n, dtype=np.float64)
            a_mat[0::2, 0] = 1.0
            a_mat[0::2, 1], a_mat[0::2, 2] = xi_c, eta_c
            a_mat[1::2, 3] = 1.0
            a_mat[1::2, 4], a_mat[1::2, 5] = xi_c, eta_c
            y_vec[0::2], y_vec[1::2] = xi_r, eta_r
            aw = a_mat * np.repeat(sq_w, 2)[:, None]
            yw = y_vec * np.repeat(sq_w, 2)
            p, *_ = np.linalg.lstsq(aw, yw, rcond=None)
            cx, m00, m01, cy, m10, m11 = (float(v) for v in p)
            mat = np.array([[m00, m01], [m10, m11]], dtype=np.float64)

        new_ra, new_dec = tangent_arcsec_to_sky(cx, cy, ra0, dec0)
        w.wcs.crval = [float(new_ra) % 360.0, float(new_dec)]
        if w.wcs.has_cd():
            w.wcs.cd = mat @ np.asarray(w.wcs.cd, dtype=np.float64)
        else:
            psm = np.asarray(w.pixel_scale_matrix, dtype=np.float64)
            w.wcs.cdelt = np.ones(2, dtype=np.float64)
            w.wcs.pc = mat @ psm
        w.wcs.set()

    ra_f, dec_f = w.pixel_to_world_values(x_px, y_px)
    c_fit = SkyCoord(ra=ra_f * u.deg, dec=dec_f * u.deg, frame="icrs")
    c_ref = SkyCoord(ra=ra_ref * u.deg, dec=dec_ref * u.deg, frame="icrs")
    r_arcsec = c_fit.separation(c_ref).arcsec
    chi2 = float(np.sum(inv_var * (r_arcsec**2)))
    return w, chi2


def _fit_adaptive_wcs(
    wcs_base: WCS,
    x_px: NDArray[np.float64],
    y_px: NDArray[np.float64],
    ra_ref: NDArray[np.float64],
    dec_ref: NDArray[np.float64],
    inv_var: NDArray[np.float64],
) -> tuple[WCS, str]:
    n = len(x_px)
    if n >= 80:
        sky = SkyCoord(ra=ra_ref * u.deg, dec=dec_ref * u.deg, frame="icrs")
        proj_pt = SkyCoord(
            ra=float(wcs_base.wcs.crval[0]) * u.deg,
            dec=float(wcs_base.wcs.crval[1]) * u.deg,
            frame="icrs",
        )
        w_sip = fit_wcs_from_points((x_px, y_px), sky, proj_point=proj_pt, sip_degree=3)
        w_out, _ = _fit_tangent_transform(w_sip, x_px, y_px, ra_ref, dec_ref, inv_var, "shift_2param")
        return w_out, "tan_sip_deg3"
    if n >= 15:
        w_out, _ = _fit_tangent_transform(wcs_base, x_px, y_px, ra_ref, dec_ref, inv_var, "affine_6param")
        return w_out, "affine_6param"
    w2, chi2_2 = _fit_tangent_transform(wcs_base, x_px, y_px, ra_ref, dec_ref, inv_var, "shift_2param")
    w4, chi2_4 = _fit_tangent_transform(wcs_base, x_px, y_px, ra_ref, dec_ref, inv_var, "similarity_4param")
    bic_2 = chi2_2 + 2.0 * math.log(2 * n)
    bic_4 = chi2_4 + 4.0 * math.log(2 * n)
    if bic_4 < bic_2 - 6.0:
        return w4, "similarity_4param"
    return w2, "shift_2param"


def _unrefined_result(
    frame: FrameData, warns: list[str], msg: str, *, n_ref: int = 0, n_matched: int = 0
) -> RegistrationResult:
    return RegistrationResult(
        wcs=frame.wcs.deepcopy(),
        wcs_metrics=frame.wcs_metrics,
        sigma_reg_arcsec=0.50,
        rms_2d_arcsec=0.50,
        rms_ra_arcsec=0.35,
        rms_dec_arcsec=0.35,
        median_residual_arcsec=0.50,
        n_reference_input=n_ref,
        n_matched=n_matched,
        n_kept=n_matched,
        model_used="unrefined",
        converged=False,
        degraded=True,
        astrometry_poor=True,
        wcs_unrefined=True,
        array_id=frame.array_id,
        pixel_phase_applied=False,
        warnings=[*warns, msg],
    )


def register_exposure(
    frame_source: Path | str | bytes | fits.HDUList | FrameData,
    *,
    reference_catalog: Sequence[Mapping[str, Any]] | Table | None = None,
    psf_sigma_px: float | None = None,
    max_match_radius_arcsec: float = 10.0,
    isolation_arcsec: float = 20.0,
    g_mag_range: tuple[float, float] = (12.0, 17.5),
    max_ruwe: float = 1.4,
    apply_pixel_phase: bool = True,
) -> RegistrationResult:
    """Refine frame WCS plate solution against Gaia DR3 with adaptive DOF and QA (§C.2).

    Units: `sigma_reg_arcsec` and `rms_*_arcsec` in arcsec; internal tangent offsets in arcsec.
    """
    frame = load_frame(frame_source, psf_sigma_px=psf_sigma_px)
    warns = list(frame.warnings)
    if reference_catalog is None or len(reference_catalog) == 0:
        return _unrefined_result(frame, warns, "WCS_UNREFINED: empty reference catalog.")

    bary_pos = getattr(
        frame.epoch,
        "barycentric_earth_au",
        getattr(
            frame.epoch,
            "barycentric_pos_au",
            getattr(frame, "barycentric_pos_au", getattr(frame, "barycentric_earth_au", None)),
        ),
    )
    propagated = propagate_gaia_catalog(
        reference_catalog,
        frame.epoch.mjd_tdb_mid,
        barycentric_earth_au=bary_pos,
        max_ruwe=max_ruwe,
        g_mag_range=g_mag_range,
    )
    fwhm_arcsec = 2.3548200450309493 * frame.psf_sigma_px * frame.wcs_metrics.pixscale_arcsec
    iso_refs = _filter_isolated(
        propagated, isolation_arcsec, min_blend_arcsec=max(fwhm_arcsec, 10.0)
    )

    ext = extract_sources(frame, min_snr=5.0)
    reject_bits = HARD_BAD_MASK | int(
        DetectionFlag.SATURATED
        | DetectionFlag.STREAK
        | DetectionFlag.EXTENDED
        | DetectionFlag.LOW_SNR
    )
    clean_dets = [d for d in ext.detections if not (d.flags & reject_bits)]

    pairs = _match_detections_to_refs(clean_dets, iso_refs, frame.wcs, max_match_radius_arcsec)
    n_matched = len(pairs)
    if n_matched < 5:
        return _unrefined_result(
            frame,
            warns,
            f"WCS_UNREFINED: only {n_matched} matched reference stars (< 5).",
            n_ref=len(propagated),
            n_matched=n_matched,
        )

    x_arr = np.array([d.x_px for d, _ in pairs], dtype=np.float64)
    y_arr = np.array([d.y_px for d, _ in pairs], dtype=np.float64)
    ra_arr = np.array([r.ra_deg for _, r in pairs], dtype=np.float64)
    dec_arr = np.array([r.dec_deg for _, r in pairs], dtype=np.float64)
    sig_pos = np.array(
        [
            max(
                0.03,
                math.sqrt(
                    max(
                        0.5 * (d.sig_ra_arcsec**2 + d.sig_dec_arcsec**2) + r.sigma_cat_arcsec**2,
                        1e-6,
                    )
                ),
            )
            for d, r in pairs
        ],
        dtype=np.float64,
    )
    inv_var = 1.0 / (sig_pos**2)

    # 3-iteration 3-sigma MAD outlier clipping around adaptive plate solution.
    keep = np.ones(n_matched, dtype=bool)
    w_work = frame.wcs.deepcopy()
    for _ in range(3):
        if int(np.sum(keep)) < 5:
            break
        w_tmp, _ = _fit_adaptive_wcs(
            w_work, x_arr[keep], y_arr[keep], ra_arr[keep], dec_arr[keep], inv_var[keep]
        )
        ra_p, dec_p = w_tmp.pixel_to_world_values(x_arr, y_arr)
        sep_all = (
            SkyCoord(ra=ra_p * u.deg, dec=dec_p * u.deg)
            .separation(SkyCoord(ra=ra_arr * u.deg, dec=dec_arr * u.deg))
            .arcsec
        )
        rob = max(float(mad_std(sep_all[keep])), 0.05)
        new_keep = sep_all <= max(0.5, 3.0 * rob)
        if int(np.sum(new_keep)) >= 5:
            keep = new_keep
            w_work = w_tmp
        else:
            break

    xk, yk, rak, deck, ivk = x_arr[keep], y_arr[keep], ra_arr[keep], dec_arr[keep], inv_var[keep]
    nk = len(xk)
    phase_applied = False
    if apply_pixel_phase and nk >= 50:
        rx, ry = w_work.world_to_pixel_values(rak, deck)
        xk, yk = _apply_pixel_phase(
            xk, yk, xk - np.asarray(rx, dtype=np.float64), yk - np.asarray(ry, dtype=np.float64)
        )
        phase_applied = True

    wcs_out, model_used = _fit_adaptive_wcs(frame.wcs, xk, yk, rak, deck, ivk)

    ra_f, dec_f = wcs_out.pixel_to_world_values(xk, yk)
    ra_c0, dec_c0 = float(wcs_out.wcs.crval[0]), float(wcs_out.wcs.crval[1])
    xi_f, eta_f = sky_to_tangent_arcsec(ra_f, dec_f, ra_c0, dec_c0)
    xi_k, eta_k = sky_to_tangent_arcsec(rak, deck, ra_c0, dec_c0)
    dxi, deta = xi_f - xi_k, eta_f - eta_k
    resid = (
        SkyCoord(ra=ra_f * u.deg, dec=dec_f * u.deg)
        .separation(SkyCoord(ra=rak * u.deg, dec=deck * u.deg))
        .arcsec
    )
    rms_2d = float(np.sqrt(np.mean(resid**2)))
    rms_ra = float(np.sqrt(np.mean(dxi**2)))
    rms_dec = float(np.sqrt(np.mean(deta**2)))
    med_res = float(np.median(resid))
    sigma_reg = max(1e-4, rms_2d / math.sqrt(nk))
    poor = rms_2d > 0.35 or nk < 5
    metrics = (
        extract_wcs_metrics(wcs_out.to_header(relax=True), shape=frame.shape) or frame.wcs_metrics
    )
    if poor:
        warns.append(f'ASTROMETRY_POOR: RMS_2D={rms_2d:.3f}" ({nk} stars).')

    return RegistrationResult(
        wcs=wcs_out,
        wcs_metrics=metrics,
        sigma_reg_arcsec=sigma_reg,
        rms_2d_arcsec=rms_2d,
        rms_ra_arcsec=rms_ra,
        rms_dec_arcsec=rms_dec,
        median_residual_arcsec=med_res,
        n_reference_input=len(propagated),
        n_matched=n_matched,
        n_kept=nk,
        model_used=model_used,
        converged=not poor,
        degraded=poor,
        astrometry_poor=poor,
        wcs_unrefined=False,
        array_id=frame.array_id,
        pixel_phase_applied=phase_applied,
        residual_arcsec=tuple(float(v) for v in resid),
        warnings=warns,
    )


__all__ = [
    "ReferenceStar",
    "RegistrationResult",
    "propagate_gaia_catalog",
    "register_exposure",
]
