"""PSF-matched ZOGY differencing, blank-sky FAR calibration, and monopole/dipole BIC tests (§B)."""

from __future__ import annotations

import math
import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS
from numpy.typing import NDArray
from photutils.detection import find_peaks
from photutils.utils.exceptions import NoDetectionsWarning
from scipy.interpolate import RectBivariateSpline
from scipy.ndimage import binary_dilation
from scipy.ndimage import shift as nd_shift
from scipy.optimize import least_squares

from sphx_core.calib import (
    BackgroundModel,
    FrameData,
    band_group_for_wavelength,
    estimate_background_and_noise,
    load_frame,
    reproject_to_grid,
    saturation_mask,
)
from sphx_core.extraction import estimate_psf_kernel


@dataclass(frozen=True)
class StampTestResult:
    """WLS stamp comparison of stationary variable `M_V` vs mover `M_M` (§B.8.1)."""

    ra_deg: float
    dec_deg: float
    x_px: float
    y_px: float
    snr_corr: float
    delta_f_monopole: float
    x_mono_px: float
    y_mono_px: float
    f0_dipole: float
    f1_dipole: float
    x0_dipole_px: float
    y0_dipole_px: float
    x1_dipole_px: float
    y1_dipole_px: float
    displacement_px: float
    displacement_sigma_px: float
    displacement_snr: float
    chi2_monopole: float
    chi2_dipole: float
    n_pixels: int
    bic_monopole: float
    bic_dipole: float
    delta_bic_mv_over_mm: float
    delta_bic_mm_over_mv: float
    monopole_favored: bool
    dipole_favored: bool
    classification_hint: str
    pair_indices: tuple[int, int] = (0, 1)


@dataclass(frozen=True)
class ZogyPairResult:
    """Single-pair ZOGY proper difference `D`, score map `S_corr`, and tested stamps (§B.5)."""

    ref_mjd_tdb: float
    sci_mjd_tdb: float
    wcs: WCS
    diff_image: NDArray[np.float64]
    diff_psf: NDArray[np.float64]
    flux_scale_d: float
    diff_variance: NDArray[np.float64]
    score_image: NDArray[np.float64]
    score_corr: NDArray[np.float64]
    quality_mask: NDArray[np.bool_]
    z_threshold: float
    stamp_tests: list[StampTestResult]
    f_corr: float
    wavelength_mismatch: bool
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DifferenceResult:
    """Aggregated multi-epoch ZOGY difference analysis and monopole/dipole summary."""

    pair_results: list[ZogyPairResult]
    stamp_tests: list[StampTestResult]
    wcs: WCS
    epochs_mjd_tdb: list[float]
    max_snr_corr: float
    z_threshold_max: float
    monopole_favored: bool
    dipole_favored: bool
    delta_bic_mv_over_mm: float
    delta_bic_mm_over_mv: float
    warnings: list[str] = field(default_factory=list)

    def stamp_test_near(
        self, ra_deg: float, dec_deg: float, *, radius_arcsec: float = 15.0
    ) -> StampTestResult | None:
        """Return the highest-|S_corr| stamp test within `radius_arcsec` of `(ra_deg, dec_deg)`."""
        if not self.stamp_tests:
            return None
        target = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
        cands = SkyCoord(
            ra=[s.ra_deg for s in self.stamp_tests] * u.deg,
            dec=[s.dec_deg for s in self.stamp_tests] * u.deg,
            frame="icrs",
        )
        seps = target.separation(cands).arcsec
        inside = [s for s, sep in zip(self.stamp_tests, seps, strict=True) if sep <= radius_arcsec]
        if not inside:
            return None
        return max(inside, key=lambda s: abs(s.snr_corr))


def _place_kernel_padded(
    kernel: NDArray[np.float64], shape: tuple[int, int]
) -> NDArray[np.float64]:
    ny, nx = shape
    ky, kx = kernel.shape
    pad = np.zeros((ny, nx), dtype=np.float64)
    cy, cx = ky // 2, kx // 2
    y_idx = (np.arange(ky) - cy) % ny
    x_idx = (np.arange(kx) - cx) % nx
    pad[np.ix_(y_idx, x_idx)] = kernel
    return pad


def _fft_convolve_periodic(
    arr: NDArray[np.float64], kernel_fft: NDArray[np.complex128]
) -> NDArray[np.float64]:
    return np.asarray(np.fft.irfft2(np.fft.rfft2(arr) * kernel_fft, s=arr.shape), dtype=np.float64)


def _calibrate_far_threshold(
    scorr: NDArray[np.float64],
    img_n: NDArray[np.float64],
    img_r: NDArray[np.float64],
    var_n: NDArray[np.float64],
    var_r: NDArray[np.float64],
    q_mask: NDArray[np.bool_],
    fwhm_px: float,
) -> float:
    """Calibrate threshold on sign-inverted `-S_corr` over blank-sky pixels (§B.6)."""
    rms_n = np.sqrt(np.maximum(var_n, 1e-12))
    rms_r = np.sqrt(np.maximum(var_r, 1e-12))
    bright_srcs = binary_dilation(
        ((img_n > 3.0 * rms_n) | (img_r > 3.0 * rms_r)) & q_mask, iterations=3
    )
    blank = q_mask & (~bright_srcs)
    n_blank = int(np.sum(blank))
    if n_blank < 25:
        return 5.0
    neg_vals = np.sort(-scorr[blank])[::-1]
    n_beams = max(1.0, float(n_blank) / max(1.133 * fwhm_px**2, 1.0))
    # Allowable <= 0.01 false alarms per 10'x10' field scaled to valid sky area.
    field_scale = (4096.0 / max(n_blank, 1)) * 0.01 * (1.0 / (10.0 / 6.6) ** 2)
    idx = min(len(neg_vals) - 1, max(0, int(math.floor(max(field_scale, 0.0) * n_beams))))
    q999 = float(np.percentile(neg_vals, 99.9)) if len(neg_vals) >= 100 else float(neg_vals[0])
    z_emp = min(float(neg_vals[idx]), q999)
    return float(max(5.0, min(z_emp, 7.5)))


def _detect_diff_candidates(
    scorr: NDArray[np.float64],
    q_mask: NDArray[np.bool_],
    z_thresh: float,
    min_sep_px: float,
) -> list[tuple[float, float, float]]:
    """Find candidate monopoles (`|S_corr| >= z_thresh`) and tight opposite-sign dipole pairs."""
    sc_masked = np.where(q_mask, scorr, 0.0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NoDetectionsWarning)
        pos_pks = find_peaks(
            sc_masked, threshold=max(3.5, 0.72 * z_thresh), box_size=3, mask=~q_mask
        )
        neg_pks = find_peaks(
            -sc_masked, threshold=max(3.5, 0.72 * z_thresh), box_size=3, mask=~q_mask
        )

    pos_list: list[tuple[float, float, float]] = (
        [(float(r["x_peak"]), float(r["y_peak"]), float(r["peak_value"])) for r in pos_pks]
        if pos_pks is not None and len(pos_pks) > 0
        else []
    )
    neg_list: list[tuple[float, float, float]] = (
        [(float(r["x_peak"]), float(r["y_peak"]), -float(r["peak_value"])) for r in neg_pks]
        if neg_pks is not None and len(neg_pks) > 0
        else []
    )

    cands: list[tuple[float, float, float]] = []
    used_neg: set[int] = set()
    # Pair nearby opposite-sign lobes into dipole centers (captures sub-pixel movers cleanly).
    for xp, yp, vp in pos_list:
        best_j: int | None = None
        best_d = 4.0
        for j, (xn, yn, _) in enumerate(neg_list):
            if j in used_neg:
                continue
            d = math.hypot(xp - xn, yp - yn)
            if d <= best_d:
                best_d = d
                best_j = j
        if best_j is not None:
            xn, yn, vn = neg_list[best_j]
            dip_snr = (vp - vn) / math.sqrt(2.0)
            if dip_snr >= max(z_thresh - 0.15, 4.85) or vp >= z_thresh or -vn >= z_thresh:
                used_neg.add(best_j)
                cx = (xp * vp + xn * abs(vn)) / (vp + abs(vn))
                cy = (yp * vp + yn * abs(vn)) / (vp + abs(vn))
                cands.append((cx, cy, max(vp, abs(vn), dip_snr)))
        elif vp >= z_thresh:
            cands.append((xp, yp, vp))

    for j, (xn, yn, vn) in enumerate(neg_list):
        if j not in used_neg and abs(vn) >= z_thresh:
            cands.append((xn, yn, vn))

    # Deduplicate candidates within min_sep_px keeping strongest |S_corr|.
    cands.sort(key=lambda c: abs(c[2]), reverse=True)
    kept: list[tuple[float, float, float]] = []
    for cx, cy, vs in cands:
        if any(math.hypot(cx - kx, cy - ky) < min_sep_px for kx, ky, _ in kept):
            continue
        kept.append((cx, cy, vs))
    return kept


def fit_monopole_vs_dipole(
    diff_image: NDArray[np.float64],
    diff_variance: NDArray[np.float64],
    diff_psf: NDArray[np.float64],
    flux_scale_d: float,
    x_center: float,
    y_center: float,
    *,
    wcs: WCS | None = None,
    quality_mask: NDArray[np.bool_] | None = None,
    snr_corr: float = 0.0,
    stamp_size: int = 11,
    pair_indices: tuple[int, int] = (0, 1),
) -> StampTestResult:
    """Fit WLS monopole `M_V` (`k=3`) vs dipole `M_M` (`k=5,6`) on a difference stamp (§B.8.1)."""
    ny, nx = diff_image.shape
    half = stamp_size // 2
    ix, iy = int(round(x_center)), int(round(y_center))
    x0_s, x1_s = max(0, ix - half), min(nx, ix + half + 1)
    y0_s, y1_s = max(0, iy - half), min(ny, iy + half + 1)

    st_d = diff_image[y0_s:y1_s, x0_s:x1_s]
    st_v = np.maximum(diff_variance[y0_s:y1_s, x0_s:x1_s], 1e-12)
    q_s = np.isfinite(st_d) & np.isfinite(st_v)
    if quality_mask is not None:
        q_s &= quality_mask[y0_s:y1_s, x0_s:x1_s]

    gy, gx = np.mgrid[y0_s:y1_s, x0_s:x1_s]
    dx_pts = gx[q_s].astype(np.float64) - x_center
    dy_pts = gy[q_s].astype(np.float64) - y_center
    y_obs = st_d[q_s].astype(np.float64)
    w_sqrt = 1.0 / np.sqrt(st_v[q_s].astype(np.float64))
    n_pix = int(y_obs.size)

    # Zero-pad diff_psf by 2 px so spline evaluation and derivatives stay smooth across bounds.
    pd_pad = np.pad(diff_psf, 2, mode="constant")
    k_h, k_w = pd_pad.shape
    ky_ax = np.arange(k_h, dtype=np.float64) - (k_h // 2)
    kx_ax = np.arange(k_w, dtype=np.float64) - (k_w // 2)
    spline = RectBivariateSpline(ky_ax, kx_ax, pd_pad, kx=3, ky=3)
    fd = max(flux_scale_d, 1e-12)
    kmax_y, kmax_x = float(ky_ax[-1]), float(kx_ax[-1])
    dy_min, dy_max = (float(dy_pts.min()), float(dy_pts.max())) if n_pix else (0.0, 0.0)
    dx_min, dx_max = (float(dx_pts.min()), float(dx_pts.max())) if n_pix else (0.0, 0.0)

    def _all_in(x_pos: float, y_pos: float) -> bool:
        return (
            dy_min - y_pos >= -kmax_y
            and dy_max - y_pos <= kmax_y
            and dx_min - x_pos >= -kmax_x
            and dx_max - x_pos <= kmax_x
        )

    def _eval_psf(x_pos: float, y_pos: float) -> NDArray[np.float64]:
        oy, ox = dy_pts - y_pos, dx_pts - x_pos
        if _all_in(x_pos, y_pos):
            return np.asarray(fd * spline.ev(oy, ox), dtype=np.float64)
        in_b = (np.abs(oy) <= kmax_y) & (np.abs(ox) <= kmax_x)
        val = np.zeros(len(dx_pts), dtype=np.float64)
        if np.any(in_b):
            val[in_b] = fd * spline.ev(oy[in_b], ox[in_b])
        return val

    def _eval_psf_and_grad(
        x_pos: float, y_pos: float
    ) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
        oy, ox = dy_pts - y_pos, dx_pts - x_pos
        if _all_in(x_pos, y_pos):
            return (
                np.asarray(fd * spline.ev(oy, ox), dtype=np.float64),
                np.asarray(-fd * spline.ev(oy, ox, dx=0, dy=1), dtype=np.float64),
                np.asarray(-fd * spline.ev(oy, ox, dx=1, dy=0), dtype=np.float64),
            )
        in_b = (np.abs(oy) <= kmax_y) & (np.abs(ox) <= kmax_x)
        n_d = len(dx_pts)
        val = np.zeros(n_d, dtype=np.float64)
        gx_o = np.zeros(n_d, dtype=np.float64)
        gy_o = np.zeros(n_d, dtype=np.float64)
        if np.any(in_b):
            yi, xi = oy[in_b], ox[in_b]
            val[in_b] = fd * spline.ev(yi, xi)
            gx_o[in_b] = -fd * spline.ev(yi, xi, dx=0, dy=1)
            gy_o[in_b] = -fd * spline.ev(yi, xi, dx=1, dy=0)
        return val, gx_o, gy_o

    if n_pix < 9:
        ra0, dec0 = (
            (float(v) for v in wcs.pixel_to_world_values(x_center, y_center))
            if wcs is not None
            else (0.0, 0.0)
        )
        return StampTestResult(
            ra0 % 360.0,
            dec0,
            x_center,
            y_center,
            snr_corr,
            0.0,
            x_center,
            y_center,
            0.0,
            0.0,
            x_center,
            y_center,
            x_center,
            y_center,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            n_pix,
            0.0,
            0.0,
            0.0,
            0.0,
            True,
            False,
            "insufficient_pixels",
            pair_indices,
        )

    # Initial amplitude/position estimates inside stamp.
    p0 = _eval_psf(0.0, 0.0)
    df0 = float(np.dot(p0 * w_sqrt, y_obs * w_sqrt) / max(np.dot(p0 * w_sqrt, p0 * w_sqrt), 1e-12))

    # M_V (k=3): [dF, dx, dy] bounded to central 2.5 px with exact analytic Jacobian.
    box_m = min(2.5, 0.4 * max(y1_s - y0_s, 3), 0.4 * max(x1_s - x0_s, 3))
    lsq_kw = dict(xtol=1e-4, ftol=1e-4, gtol=1e-4, max_nfev=80)

    def _jac_v(p: NDArray[np.float64]) -> NDArray[np.float64]:
        val, gx_o, gy_o = _eval_psf_and_grad(float(p[1]), float(p[2]))
        return np.column_stack((val * w_sqrt, p[0] * gx_o * w_sqrt, p[0] * gy_o * w_sqrt))

    res_v = least_squares(
        lambda p: (p[0] * _eval_psf(float(p[1]), float(p[2])) - y_obs) * w_sqrt,
        x0=np.array([df0, 0.0, 0.0]),
        jac=_jac_v,
        bounds=([-np.inf, -box_m, -box_m], [np.inf, box_m, box_m]),
        **lsq_kw,
    )
    chi2_v = float(np.sum(res_v.fun**2))
    bic_v = chi2_v + 3.0 * math.log(n_pix)

    # Initial positive & negative peak seeds within stamp for M_M.
    st_w = np.where(q_s, st_d / np.sqrt(st_v), 0.0)
    py_max, px_max = np.unravel_index(int(np.argmax(st_w)), st_w.shape)
    py_min, px_min = np.unravel_index(int(np.argmin(st_w)), st_w.shape)
    p1x_init = float(np.clip((x0_s + px_max) - x_center, -box_m, box_m))
    p1y_init = float(np.clip((y0_s + py_max) - y_center, -box_m, box_m))
    p0x_init = float(np.clip((x0_s + px_min) - x_center, -box_m, box_m))
    p0y_init = float(np.clip((y0_s + py_min) - y_center, -box_m, box_m))
    if math.hypot(p1x_init - p0x_init, p1y_init - p0y_init) < 0.2:
        p1x_init, p0x_init = 0.4, -0.4

    dbox = min(3.5, 0.5 * max(y1_s - y0_s - 2, 2), 0.5 * max(x1_s - x0_s - 2, 2))
    f_dip_init = max(1.0, abs(float(np.max(np.abs(y_obs))) / max(float(np.max(np.abs(p0))), 1e-6)))

    # Equal-flux mover M_M (k=5: F, x0, y0, x1, y1 per §B.8.1).
    def _res_m5(p: NDArray[np.float64]) -> NDArray[np.float64]:
        mod = p[0] * (_eval_psf(float(p[3]), float(p[4])) - _eval_psf(float(p[1]), float(p[2])))
        return np.asarray((mod - y_obs) * w_sqrt, dtype=np.float64)

    def _jac_m5(p: NDArray[np.float64]) -> NDArray[np.float64]:
        v0, gx0, gy0 = _eval_psf_and_grad(float(p[1]), float(p[2]))
        v1, gx1, gy1 = _eval_psf_and_grad(float(p[3]), float(p[4]))
        return np.column_stack(
            (
                (v1 - v0) * w_sqrt,
                -p[0] * gx0 * w_sqrt,
                -p[0] * gy0 * w_sqrt,
                p[0] * gx1 * w_sqrt,
                p[0] * gy1 * w_sqrt,
            )
        )

    res_m5 = least_squares(
        _res_m5,
        x0=np.array([f_dip_init, p0x_init, p0y_init, p1x_init, p1y_init]),
        jac=_jac_m5,
        bounds=([0.0, -dbox, -dbox, -dbox, -dbox], [np.inf, dbox, dbox, dbox, dbox]),
        **lsq_kw,
    )
    chi2_m5 = float(np.sum(res_m5.fun**2))
    bic_m5 = chi2_m5 + 5.0 * math.log(n_pix)

    # Free-flux mover M_M (k=6: F0, F1, x0, y0, x1, y1) when mover brightness changes.
    def _jac_m6(p: NDArray[np.float64]) -> NDArray[np.float64]:
        v0, gx0, gy0 = _eval_psf_and_grad(float(p[2]), float(p[3]))
        v1, gx1, gy1 = _eval_psf_and_grad(float(p[4]), float(p[5]))
        return np.column_stack(
            (
                -v0 * w_sqrt,
                v1 * w_sqrt,
                -p[0] * gx0 * w_sqrt,
                -p[0] * gy0 * w_sqrt,
                p[1] * gx1 * w_sqrt,
                p[1] * gy1 * w_sqrt,
            )
        )

    res_m6 = least_squares(
        lambda p: (
            (
                p[1] * _eval_psf(float(p[4]), float(p[5]))
                - p[0] * _eval_psf(float(p[2]), float(p[3]))
                - y_obs
            )
            * w_sqrt
        ),
        x0=np.array([f_dip_init, f_dip_init, p0x_init, p0y_init, p1x_init, p1y_init]),
        jac=_jac_m6,
        bounds=([0.0, 0.0, -dbox, -dbox, -dbox, -dbox], [np.inf, np.inf, dbox, dbox, dbox, dbox]),
        **lsq_kw,
    )
    chi2_m6 = float(np.sum(res_m6.fun**2))
    bic_m6 = chi2_m6 + 6.0 * math.log(n_pix)

    if bic_m5 <= bic_m6:
        chi2_m, bic_m = chi2_m5, bic_m5
        f0_m, f1_m = float(res_m5.x[0]), float(res_m5.x[0])
        dx0, dy0, dx1, dy1 = (float(v) for v in res_m5.x[1:5])
        jac_m, idx_pos = res_m5.jac, slice(1, 5)
    else:
        chi2_m, bic_m = chi2_m6, bic_m6
        f0_m, f1_m = float(res_m6.x[0]), float(res_m6.x[1])
        dx0, dy0, dx1, dy1 = (float(v) for v in res_m6.x[2:6])
        jac_m, idx_pos = res_m6.jac, slice(2, 6)

    disp = math.hypot(dx1 - dx0, dy1 - dy0)
    disp_sig = 1.0
    try:
        cov_m = np.linalg.pinv(jac_m.T @ jac_m) * max(1.0, chi2_m / max(n_pix - jac_m.shape[1], 1))
        c_pos = cov_m[idx_pos, idx_pos]
        if disp > 1e-6 and c_pos.shape == (4, 4):
            ux, uy = (dx1 - dx0) / disp, (dy1 - dy0) / disp
            grad_d = np.array([-ux, -uy, ux, uy], dtype=np.float64)
            disp_sig = math.sqrt(max(float(grad_d @ c_pos @ grad_d), 1e-8))
        else:
            disp_sig = math.sqrt(
                max(float(np.trace(c_pos[:2, :2]) + np.trace(c_pos[2:, 2:])), 1e-6)
            )
    except Exception:
        disp_sig = 0.5
    disp_snr = disp / max(disp_sig, 1e-6)

    dbic_mv_over_mm = bic_m - bic_v  # > 0 means M_V (monopole) has lower BIC
    dbic_mm_over_mv = bic_v - bic_m  # > 10 means M_M (dipole) favored per §B.8.1
    dip_favored = (dbic_mm_over_mv > 10.0) and (disp_snr > 3.0)
    mono_favored = not dip_favored and (dbic_mv_over_mm > 0.0 or disp_snr <= 3.0)
    hint = "mover_dipole" if dip_favored else ("variable_monopole" if mono_favored else "ambiguous")

    ra_c, dec_c = (
        (
            float(v)
            for v in wcs.pixel_to_world_values(
                x_center + float(res_v.x[1]), y_center + float(res_v.x[2])
            )
        )
        if wcs is not None
        else (0.0, 0.0)
    )
    return StampTestResult(
        ra_deg=ra_c % 360.0,
        dec_deg=dec_c,
        x_px=x_center,
        y_px=y_center,
        snr_corr=snr_corr,
        delta_f_monopole=float(res_v.x[0]),
        x_mono_px=x_center + float(res_v.x[1]),
        y_mono_px=y_center + float(res_v.x[2]),
        f0_dipole=f0_m,
        f1_dipole=f1_m,
        x0_dipole_px=x_center + dx0,
        y0_dipole_px=y_center + dy0,
        x1_dipole_px=x_center + dx1,
        y1_dipole_px=y_center + dy1,
        displacement_px=disp,
        displacement_sigma_px=disp_sig,
        displacement_snr=disp_snr,
        chi2_monopole=chi2_v,
        chi2_dipole=chi2_m,
        n_pixels=n_pix,
        bic_monopole=bic_v,
        bic_dipole=bic_m,
        delta_bic_mv_over_mm=dbic_mv_over_mm,
        delta_bic_mm_over_mv=dbic_mm_over_mv,
        monopole_favored=mono_favored,
        dipole_favored=dip_favored,
        classification_hint=hint,
        pair_indices=pair_indices,
    )


def zogy_difference(
    ref_source: Path | str | bytes | fits.HDUList | FrameData,
    sci_source: Path | str | bytes | fits.HDUList | FrameData,
    *,
    psf_sigma_px: float | None = None,
    flux_scale_ref: float = 1.0,
    flux_scale_sci: float = 1.0,
    sigma_reg_ref_arcsec: float = 0.05,
    sigma_reg_sci_arcsec: float = 0.05,
    require_band_match: bool = True,
    pair_indices: tuple[int, int] = (0, 1),
    bg_ref: BackgroundModel | None = None,
    bg_sci: BackgroundModel | None = None,
) -> ZogyPairResult:
    """Perform ZOGY PSF-matched subtraction `sci - ref` on science frame grid (§B.5, §B.6)."""
    ref_f = load_frame(ref_source, psf_sigma_px=psf_sigma_px)
    sci_f = load_frame(sci_source, psf_sigma_px=psf_sigma_px)
    warns = [*ref_f.warnings, *sci_f.warnings]

    # Band/wavelength consistency guard (§B.5).
    band_r = ref_f.band_id or band_group_for_wavelength(ref_f.wavelength_um)
    band_n = sci_f.band_id or band_group_for_wavelength(sci_f.wavelength_um)
    lam_mismatch = False
    dlam_frac = 0.0
    if ref_f.wavelength_um is not None and sci_f.wavelength_um is not None:
        lam_r = 0.5 * (ref_f.wavelength_um[0] + ref_f.wavelength_um[1])
        lam_n = 0.5 * (sci_f.wavelength_um[0] + sci_f.wavelength_um[1])
        dlam_frac = abs(lam_n - lam_r) / max(0.5 * (lam_n + lam_r), 1e-6)
        if dlam_frac >= 0.05 and (band_r != band_n):
            lam_mismatch = True
    elif band_r is not None and band_n is not None and band_r != band_n:
        lam_mismatch = True
        dlam_frac = 0.25

    if lam_mismatch:
        msg = (
            f"Band/wavelength mismatch between ref ({band_r}) and sci ({band_n}); "
            "color residuals possible."
        )
        warns.append(msg)
        if require_band_match:
            warns.append("ZOGY chromatic variance term enabled.")

    bg_n = bg_sci if bg_sci is not None else estimate_background_and_noise(sci_f)
    reproj_r = reproject_to_grid(ref_f, sci_f.wcs, sci_f.shape, subtract_bg=True, bg=bg_ref)

    ny, nx = sci_f.shape
    sat_n = saturation_mask(sci_f.image, sci_f.flags)
    q_mask = bg_n.quality_mask & reproj_r.quality_mask & (~sat_n)

    img_n = np.where(q_mask, bg_n.subtracted, 0.0)
    img_r = np.where(q_mask, reproj_r.image, 0.0)
    var_n = bg_n.variance
    var_r = reproj_r.variance

    # Effective background variance scalars for optimal Fourier filter.
    q_sky = q_mask & (img_n < 3.0 * np.sqrt(var_n)) & (img_r < 3.0 * np.sqrt(var_r))
    sig_n2 = float(np.median(var_n[q_sky])) if np.any(q_sky) else float(np.median(var_n))
    sig_r2 = float(np.median(var_r[q_sky])) if np.any(q_sky) else float(np.median(var_r))
    sig_n2, sig_r2 = max(sig_n2, 1e-12), max(sig_r2, 1e-12)

    p_n = estimate_psf_kernel(sci_f.psf_sigma_px, sci_f.psf, size=11)
    p_r = estimate_psf_kernel(ref_f.psf_sigma_px, ref_f.psf, size=11)
    # Pad image before periodic FFT by PSF width to prevent circular boundary wrap-around.
    pad = p_n.shape[0]
    py, px = ny + 2 * pad, nx + 2 * pad
    n_pad = np.pad(img_n, pad, mode="constant")
    r_pad = np.pad(img_r, pad, mode="constant")
    vn_pad = np.pad(var_n, pad, mode="edge")
    vr_pad = np.pad(var_r, pad, mode="edge")

    fn_hat = np.fft.rfft2(n_pad)
    fr_hat = np.fft.rfft2(r_pad)
    pn_hat = np.fft.rfft2(_place_kernel_padded(p_n, (py, px)))
    pr_hat = np.fft.rfft2(_place_kernel_padded(p_r, (py, px)))

    fr, fn = flux_scale_ref, flux_scale_sci
    fd = (fr * fn) / math.sqrt(sig_n2 * fr**2 + sig_r2 * fn**2)
    den = np.sqrt(
        sig_n2 * (fr**2) * (np.abs(pr_hat) ** 2) + sig_r2 * (fn**2) * (np.abs(pn_hat) ** 2) + 1e-18
    )

    kn_d_hat = (fr * pr_hat) / den
    kr_d_hat = (fn * pn_hat) / den
    d_hat = kn_d_hat * fn_hat - kr_d_hat * fr_hat
    pd_hat = (fr * fn * pr_hat * pn_hat) / (fd * den)
    s_hat = fd * d_hat * np.conj(pd_hat)

    d_full = np.fft.irfft2(d_hat, s=(py, px))[pad:-pad, pad:-pad]
    s_full = np.fft.irfft2(s_hat, s=(py, px))[pad:-pad, pad:-pad]

    # Spatial PSF P_D centered inside an 11x11 stamp (sum == 1.0).
    pd_full = np.fft.irfft2(pd_hat, s=(py, px))
    pd_centered = np.fft.fftshift(pd_full)
    cy, cx = py // 2, px // 2
    pd_stamp = pd_centered[cy - 5 : cy + 6, cx - 5 : cx + 6].copy()
    pd_sum = float(np.sum(pd_stamp))
    if pd_sum > 0.0:
        pd_stamp /= pd_sum

    # Real-space variance kernels for spatially-varying V(S_N), V(S_R), and V(D) (§B.5).
    kn_d = np.fft.irfft2(kn_d_hat, s=(py, px))
    kr_d = np.fft.irfft2(kr_d_hat, s=(py, px))
    vd_full = (
        _fft_convolve_periodic(vn_pad, np.fft.rfft2(kn_d**2))
        + _fft_convolve_periodic(vr_pad, np.fft.rfft2(kr_d**2))
    )[pad:-pad, pad:-pad]

    kn_s_hat = fd * kn_d_hat * np.conj(pd_hat)
    kr_s_hat = fd * kr_d_hat * np.conj(pd_hat)
    kn_s = np.fft.irfft2(kn_s_hat, s=(py, px))
    kr_s = np.fft.irfft2(kr_s_hat, s=(py, px))
    v_sn = _fft_convolve_periodic(vn_pad, np.fft.rfft2(kn_s**2))[pad:-pad, pad:-pad]
    v_sr = _fft_convolve_periodic(vr_pad, np.fft.rfft2(kr_s**2))[pad:-pad, pad:-pad]

    # Astrometric variance V_ast = sigma_reg^2 |nabla S_N|^2 + sigma_reg^2 |nabla S_R|^2.
    s_n_only = np.fft.irfft2(kn_s_hat * fn_hat, s=(py, px))[pad:-pad, pad:-pad]
    s_r_only = np.fft.irfft2(kr_s_hat * fr_hat, s=(py, px))[pad:-pad, pad:-pad]
    pixscale = max(sci_f.wcs_metrics.pixscale_arcsec, 1e-3)
    sig_n_px2 = (sigma_reg_sci_arcsec / pixscale) ** 2
    sig_r_px2 = (sigma_reg_ref_arcsec / pixscale) ** 2
    gn_y, gn_x = np.gradient(s_n_only)
    gr_y, gr_x = np.gradient(s_r_only)
    v_ast = sig_n_px2 * (gn_x**2 + gn_y**2) + sig_r_px2 * (gr_x**2 + gr_y**2)

    d_n_only = np.fft.irfft2(kn_d_hat * fn_hat, s=(py, px))[pad:-pad, pad:-pad]
    d_r_only = np.fft.irfft2(kr_d_hat * fr_hat, s=(py, px))[pad:-pad, pad:-pad]
    dn_y, dn_x = np.gradient(d_n_only)
    dr_y, dr_x = np.gradient(d_r_only)
    v_d_tot = np.maximum(
        vd_full + sig_n_px2 * (dn_x**2 + dn_y**2) + sig_r_px2 * (dr_x**2 + dr_y**2), 1e-12
    )

    # Chromatic variance term when wavelengths differ significantly (§B.5).
    if dlam_frac > 0.02:
        lap_r = (
            nd_shift(s_r_only, (1, 0))
            + nd_shift(s_r_only, (-1, 0))
            + nd_shift(s_r_only, (0, 1))
            + nd_shift(s_r_only, (0, -1))
            - 4.0 * s_r_only
        )
        v_chrom = (min(dlam_frac, 0.5) * lap_r) ** 2
    else:
        v_chrom = np.zeros_like(s_full)

    v_s_tot = np.maximum(v_sn + v_sr + v_ast + v_chrom, 1e-18)
    s_corr = np.where(q_mask, s_full / np.sqrt(v_s_tot), 0.0)

    fwhm_px = 2.3548200450309493 * sci_f.psf_sigma_px
    z_thresh = _calibrate_far_threshold(s_corr, img_n, img_r, var_n, var_r, q_mask, fwhm_px)
    cands = _detect_diff_candidates(s_corr, q_mask, z_thresh, min_sep_px=max(2.0, 1.2 * fwhm_px))

    stamps = [
        fit_monopole_vs_dipole(
            d_full,
            v_d_tot,
            pd_stamp,
            fd,
            cx,
            cy,
            wcs=sci_f.wcs,
            quality_mask=q_mask,
            snr_corr=sc_val,
            pair_indices=pair_indices,
        )
        for cx, cy, sc_val in cands
    ]

    return ZogyPairResult(
        ref_mjd_tdb=ref_f.epoch.mjd_tdb_mid,
        sci_mjd_tdb=sci_f.epoch.mjd_tdb_mid,
        wcs=sci_f.wcs.deepcopy(),
        diff_image=d_full,
        diff_psf=pd_stamp,
        flux_scale_d=fd,
        diff_variance=v_d_tot,
        score_image=s_full,
        score_corr=s_corr,
        quality_mask=q_mask,
        z_threshold=z_thresh,
        stamp_tests=stamps,
        f_corr=reproj_r.f_corr,
        wavelength_mismatch=lam_mismatch,
        warnings=warns,
    )


def difference_frames(
    frames: Sequence[Path | str | bytes | fits.HDUList | FrameData],
    *,
    reference_frame: Path | str | bytes | fits.HDUList | FrameData | None = None,
    psf_sigma_px: float | None = None,
    all_pairs_max_frames: int = 6,
    sigma_reg_arcsec: float = 0.05,
    require_band_match: bool = True,
) -> DifferenceResult:
    """Run multi-epoch ZOGY differencing and summarize monopole/dipole evidence (§B.5, §B.8.1)."""
    loaded = [load_frame(f, psf_sigma_px=psf_sigma_px) for f in frames]
    if not loaded:
        raise ValueError("difference_frames requires at least one frame.")
    if reference_frame is None and len(loaded) < 2:
        raise ValueError("difference_frames requires >= 2 frames (or an explicit reference_frame).")

    pairs: list[tuple[int, int, FrameData, FrameData]] = []
    if reference_frame is not None:
        ref_fd = load_frame(reference_frame, psf_sigma_px=psf_sigma_px)
        for j, sci_fd in enumerate(loaded):
            pairs.append((-1, j, ref_fd, sci_fd))
    else:
        loaded_sorted = sorted(enumerate(loaded), key=lambda x: x[1].epoch.mjd_tdb_mid)
        m = len(loaded_sorted)
        if m <= all_pairs_max_frames:
            for a in range(m):
                for b in range(a + 1, m):
                    idx_a, fa = loaded_sorted[a]
                    idx_b, fb = loaded_sorted[b]
                    pairs.append((idx_a, idx_b, fa, fb))
        else:
            seen_p: set[tuple[int, int]] = set()
            idx_0, f0 = loaded_sorted[0]
            for k in range(1, m):
                idx_k, fk = loaded_sorted[k]
                if (idx_0, idx_k) not in seen_p:
                    pairs.append((idx_0, idx_k, f0, fk))
                    seen_p.add((idx_0, idx_k))
                idx_prev, fprev = loaded_sorted[k - 1]
                if (idx_prev, idx_k) not in seen_p:
                    pairs.append((idx_prev, idx_k, fprev, fk))
                    seen_p.add((idx_prev, idx_k))

    if require_band_match:
        pairs = [
            (ia, ib, fa, fb)
            for ia, ib, fa, fb in pairs
            if (fa.band_id or band_group_for_wavelength(fa.wavelength_um))
            == (fb.band_id or band_group_for_wavelength(fb.wavelength_um))
        ]

    bg_loaded = [estimate_background_and_noise(fd) for fd in loaded]
    bg_fixed = estimate_background_and_noise(ref_fd) if reference_frame is not None else None
    pair_results = [
        zogy_difference(
            fa,
            fb,
            psf_sigma_px=psf_sigma_px,
            sigma_reg_ref_arcsec=sigma_reg_arcsec,
            sigma_reg_sci_arcsec=sigma_reg_arcsec,
            require_band_match=require_band_match,
            pair_indices=(ia, ib),
            bg_ref=bg_fixed if ia == -1 else bg_loaded[ia],
            bg_sci=bg_loaded[ib],
        )
        for ia, ib, fa, fb in pairs
    ]

    all_stamps = [st for pr in pair_results for st in pr.stamp_tests]
    all_stamps.sort(key=lambda s: abs(s.snr_corr), reverse=True)
    max_snr = max((float(np.max(np.abs(pr.score_corr))) for pr in pair_results), default=0.0)
    z_max = max((pr.z_threshold for pr in pair_results), default=5.0)
    all_warns: list[str] = []
    for pr in pair_results:
        all_warns.extend(pr.warnings)

    best = all_stamps[0] if all_stamps else None
    dip_fav = any(s.dipole_favored for s in all_stamps)
    if best is not None:
        dbic_mv_mm = best.delta_bic_mv_over_mm
        dbic_mm_mv = best.delta_bic_mm_over_mv
        mono_fav = (not dip_fav) and (best.monopole_favored or dbic_mv_mm > 0.0)
    else:
        dbic_mv_mm = 0.0
        dbic_mm_mv = 0.0
        mono_fav = False

    return DifferenceResult(
        pair_results=pair_results,
        stamp_tests=all_stamps,
        wcs=loaded[0].wcs.deepcopy(),
        epochs_mjd_tdb=[fd.epoch.mjd_tdb_mid for fd in loaded],
        max_snr_corr=max_snr,
        z_threshold_max=z_max,
        monopole_favored=mono_fav,
        dipole_favored=dip_fav,
        delta_bic_mv_over_mm=dbic_mv_mm,
        delta_bic_mm_over_mv=dbic_mm_mv,
        warnings=list(dict.fromkeys(all_warns)),
    )


__all__ = [
    "DifferenceResult",
    "StampTestResult",
    "ZogyPairResult",
    "difference_frames",
    "fit_monopole_vs_dipole",
    "zogy_difference",
]
