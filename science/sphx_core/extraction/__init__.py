"""Matched-filter detection, SEP/photutils PSF source extraction, quality flags, and vetting."""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import sep
from astropy.io import fits
from astropy.table import Table
from astropy.wcs import WCS
from numpy.typing import NDArray
from photutils.detection import find_peaks
from photutils.psf import CircularGaussianPRF, PSFPhotometry, SourceGrouper
from photutils.utils.exceptions import NoDetectionsWarning
from scipy.ndimage import label as nd_label
from scipy.signal import fftconvolve
from scipy.special import erf

from sphx_core.calib import (
    DetectionFlag,
    FrameData,
    estimate_background_and_noise,
    load_frame,
    saturation_mask,
)
from sphx_core.wcs import local_wcs_jacobian

_FWHM_PER_SIGMA = 2.0 * math.sqrt(2.0 * math.log(2.0))


@dataclass(frozen=True)
class ExtractedSource:
    """Extracted source on a native frame (0-based px, ICRS deg, arcsec covariance, flux)."""

    source_index: int
    x_px: float
    y_px: float
    x_win_px: float
    y_win_px: float
    ra_deg: float
    dec_deg: float
    cov_px: tuple[float, float, float]
    cov_sky_arcsec2: tuple[float, float, float]
    sig_ra_arcsec: float
    sig_dec_arcsec: float
    corr_radec: float
    flux: float
    flux_err: float
    snr: float
    snr_matched: float
    fwhm_px: float
    fwhm_arcsec: float
    elongation: float
    theta_deg: float
    sharpness: float
    roundness: float
    psf_chi2_nu: float
    unflagged_weight_frac: float
    real_bogus: float
    flags: int
    centroid_mismatch: bool


@dataclass(frozen=True)
class ExtractionResult:
    """Complete native-frame source catalog, noise diagnostics, and matched-filter SNR map."""

    detections: list[ExtractedSource]
    wcs: WCS
    epoch_mjd_tdb: float
    band_id: str | None
    array_id: int | None
    wavelength_um: tuple[float, float] | None
    shape: tuple[int, int]
    pixscale_arcsec: float
    psf_fwhm_px: float
    background_rms: float
    frame_flags: int
    snr_map: NDArray[np.float64]
    warnings: list[str] = field(default_factory=list)


def estimate_psf_kernel(
    sigma_px: float, psf_hdu_data: NDArray[np.float64] | None = None, size: int = 11
) -> NDArray[np.float64]:
    """Build normalized 2D pixel-integrated PSF kernel (`sum == 1.0`, size in px) (§C.1)."""
    if psf_hdu_data is not None and psf_hdu_data.ndim >= 2:
        k = np.asarray(psf_hdu_data[0] if psf_hdu_data.ndim > 2 else psf_hdu_data, dtype=np.float64)
        k = np.nan_to_num(k, nan=0.0)
        tot = float(np.sum(k))
        if tot > 0.0:
            return k / tot
    half = size // 2
    yy, xx = np.mgrid[-half : half + 1, -half : half + 1]
    prf = CircularGaussianPRF(fwhm=max(0.5, _FWHM_PER_SIGMA * sigma_px), flux=1.0, x_0=0.0, y_0=0.0)
    kern = np.asarray(prf(xx, yy), dtype=np.float64)
    tot = float(np.sum(kern))
    return kern / tot if tot > 0.0 else kern


def matched_filter_snr(
    image_sub: NDArray[np.float64],
    variance: NDArray[np.float64],
    psf_kernel: NDArray[np.float64],
    quality_mask: NDArray[np.bool_] | None = None,
) -> NDArray[np.float64]:
    """Compute matched-filter S/N map `SNR(p) = (P * D/V) / sqrt(P^2 * 1/V)` (§B.6)."""
    q = np.isfinite(image_sub) & np.isfinite(variance) & (variance > 0.0)
    if quality_mask is not None:
        q &= quality_mask
    w = np.where(q, 1.0 / np.maximum(variance, 1e-18), 0.0)
    data_w = np.where(q, image_sub * w, 0.0)
    p_corr = psf_kernel[::-1, ::-1]
    num = fftconvolve(data_w, p_corr, mode="same")
    den_sq = fftconvolve(w, p_corr**2, mode="same")
    return np.asarray(
        np.divide(
            num, np.sqrt(np.maximum(den_sq, 1e-18)), out=np.zeros_like(num), where=den_sq > 1e-18
        ),
        dtype=np.float64,
    )


def compute_real_bogus(
    *,
    psf_chi2_nu: float,
    sharpness: float,
    roundness: float,
    unflagged_weight_frac: float,
    flags: int,
) -> float:
    """Compute deterministic rule-based real-bogus score in `[0.0, 1.0]` (§B.7)."""
    if flags & int(
        DetectionFlag.PERSIST
        | DetectionFlag.GHOST_SPIKE
        | DetectionFlag.COSMIC
        | DetectionFlag.BAD_PIX
    ):
        return 0.05
    score = 1.0
    if psf_chi2_nu > 2.5:
        score *= math.exp(-0.45 * (psf_chi2_nu - 2.5))
    elif psf_chi2_nu < 0.35:
        score *= 0.85
    if sharpness < 0.2:
        score *= max(0.05, sharpness / 0.2)
    elif sharpness > 1.0:
        score *= math.exp(-1.5 * (sharpness - 1.0))
    if abs(roundness) > 0.6 and not (flags & int(DetectionFlag.STREAK)):
        score *= max(0.1, 1.0 - (abs(roundness) - 0.6) * 2.0)
    if unflagged_weight_frac < 0.85:
        score *= max(0.05, unflagged_weight_frac / 0.85)
    return float(min(1.0, max(0.0, score)))


def _detect_unflagged_cr(
    sub: NDArray[np.float64], ix: int, iy: int, psf_sigma_px: float, local_rms: float
) -> bool:
    ny, nx = sub.shape
    if not (1 <= ix < nx - 1 and 1 <= iy < ny - 1):
        return False
    patch = sub[iy - 1 : iy + 2, ix - 1 : ix + 2]
    center = float(patch[1, 1])
    total = float(np.sum(np.maximum(patch, 0.0)))
    if center <= 6.0 * local_rms or total <= 0.0:
        return False
    # For Gaussian PSF (sigma >= 0.7 px), central pixel holds <= 0.30 of 3x3 flux.
    expected_frac = float(erf(0.5 / (math.sqrt(2.0) * max(psf_sigma_px, 0.5)))) ** 2
    return (center / total) > max(0.75, 2.2 * expected_frac)


def _merge_seeds(
    sep_objs: NDArray[Any],
    mf_peaks: Table | None,
    min_sep_px: float,
) -> tuple[list[tuple[float, float]], list[dict[str, float]]]:
    seeds: list[tuple[float, float]] = []
    morphs: list[dict[str, float]] = []
    for obj in sep_objs:
        seeds.append((float(obj["x"]), float(obj["y"])))
        morphs.append(
            {
                "a": float(obj["a"]),
                "b": float(obj["b"]),
                "theta": float(obj["theta"]),
                "sep_flag": float(obj["flag"]),
            }
        )
    if mf_peaks is not None and len(mf_peaks) > 0:
        for row in mf_peaks:
            px, py = float(row["x_peak"]), float(row["y_peak"])
            if any(math.hypot(px - sx, py - sy) < min_sep_px for sx, sy in seeds):
                continue
            seeds.append((px, py))
            morphs.append({"a": 1.0, "b": 1.0, "theta": 0.0, "sep_flag": 0.0})
    return seeds, morphs


def _make_init_table(seeds: list[tuple[float, float]]) -> Table:
    return Table(
        {
            "x_0": np.array([s[0] for s in seeds], dtype=np.float64),
            "y_0": np.array([s[1] for s in seeds], dtype=np.float64),
        }
    )


def extract_sources(
    frame_source: Path | str | bytes | fits.HDUList | FrameData,
    *,
    wcs: WCS | None = None,
    sigma_reg_arcsec: float | None = None,
    psf_sigma_px: float | None = None,
    detect_thresh_sigma: float = 1.5,
    min_area: int = 3,
    min_snr: float = 4.5,
    max_sources: int = 500,
) -> ExtractionResult:
    """Extract sources on native un-resampled frame with SEP + photutils PSF photometry (§B, §C.1).

    Units: `x_px`, `y_px` in 0-based px; `ra_deg`, `dec_deg` in ICRS deg;
    `cov_sky_arcsec2` in arcsec^2; `flux` in integrated native pixel flux units.
    """
    frame = load_frame(frame_source, wcs_override=wcs, psf_sigma_px=psf_sigma_px)
    bg = estimate_background_and_noise(frame)
    ny, nx = frame.shape
    sigma_px = frame.psf_sigma_px
    fwhm_px = _FWHM_PER_SIGMA * sigma_px
    pixscale = frame.wcs_metrics.pixscale_arcsec

    bad_mask = ~bg.quality_mask
    sat_mask = saturation_mask(frame.image, frame.flags)
    fit_mask = bad_mask | sat_mask

    psf_kernel = estimate_psf_kernel(sigma_px, frame.psf)
    snr_map = matched_filter_snr(bg.subtracted, bg.variance, psf_kernel, quality_mask=~bad_mask)

    sub_c = np.ascontiguousarray(bg.subtracted, dtype=np.float64)
    err_c = np.ascontiguousarray(bg.rms, dtype=np.float64)
    frame_flags = frame.frame_flags
    if bg.scattered_light:
        frame_flags |= int(DetectionFlag.GHOST_SPIKE)

    fit_side = max(5, min(7, ny if ny % 2 == 1 else ny - 1, nx if nx % 2 == 1 else nx - 1))
    sat_side = max(5, min(9, ny if ny % 2 == 1 else ny - 1, nx if nx % 2 == 1 else nx - 1))
    fwhm_ap = max(2.0, 1.2 * fwhm_px)

    # Fit saturated star wings first so halos/ring artifacts do not pollute downstream seeding.
    work_sub = sub_c.copy()
    sat_rows: list[tuple[Any, NDArray[np.float64] | None, dict[str, float]]] = []
    sat_seeds: list[tuple[float, float]] = []
    if np.any(sat_mask):
        sat_lbl, n_sat = nd_label(sat_mask)
        for comp in range(1, n_sat + 1):
            ys_c, xs_c = np.nonzero(sat_lbl == comp)
            sat_seeds.append((float(np.mean(xs_c)), float(np.mean(ys_c))))
        phot_sat = PSFPhotometry(
            CircularGaussianPRF(fwhm=fwhm_px),
            fit_shape=(sat_side, sat_side),
            grouper=SourceGrouper(min_separation=max(4.5, 2.2 * fwhm_px)),
            aperture_radius=fwhm_ap,
        )
        res_sat = phot_sat(
            work_sub, mask=fit_mask, error=err_c, init_params=_make_init_table(sat_seeds)
        )
        sat_infos = getattr(phot_sat, "fit_info", [])
        default_morph = {"a": sigma_px, "b": sigma_px, "theta": 0.0, "sep_flag": 0.0}
        for i, r in enumerate(res_sat):
            pcov = sat_infos[i].get("param_cov") if i < len(sat_infos) else None
            sat_rows.append(
                (r, np.asarray(pcov, dtype=np.float64) if pcov is not None else None, default_morph)
            )
        work_sub = np.ascontiguousarray(phot_sat.make_residual_image(work_sub), dtype=np.float64)
        snr_work = matched_filter_snr(work_sub, bg.variance, psf_kernel, quality_mask=~fit_mask)
    else:
        snr_work = snr_map

    sep_objs = sep.extract(
        work_sub,
        thresh=detect_thresh_sigma,
        err=err_c,
        mask=fit_mask,
        minarea=min_area,
        filter_kernel=psf_kernel,
        filter_type="matched",
        deblend_nthresh=32,
        deblend_cont=0.005,
        clean=True,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NoDetectionsWarning)
        mf_peaks = find_peaks(snr_work, threshold=max(4.0, min_snr), box_size=3, mask=fit_mask)
    seeds, morphs = _merge_seeds(sep_objs, mf_peaks, min_sep_px=max(1.2, 0.7 * fwhm_px))

    # Filter unflagged single-pixel cosmic rays and low-S/N noise fluctuations before PSF fitting.
    unsat_seeds: list[tuple[float, float]] = []
    unsat_morphs: list[dict[str, float]] = []
    pre_snr_min = max(2.5, 0.55 * min_snr)
    for (sx, sy), m in zip(seeds, morphs, strict=True):
        ix, iy = int(np.clip(round(sx), 0, nx - 1)), int(np.clip(round(sy), 0, ny - 1))
        if any(math.hypot(sx - stx, sy - sty) < 1.2 for stx, sty in sat_seeds):
            continue
        if float(snr_work[iy, ix]) < pre_snr_min:
            continue
        if _detect_unflagged_cr(work_sub, ix, iy, sigma_px, float(bg.rms[iy, ix])):
            continue
        unsat_seeds.append((sx, sy))
        unsat_morphs.append(m)

    unsat_rows: list[tuple[Any, NDArray[np.float64] | None, dict[str, float]]] = []
    resid_all = work_sub
    if unsat_seeds and len(unsat_seeds) > max_sources:
        unsat_seeds.sort(
            key=lambda s: float(
                snr_work[int(np.clip(round(s[1]), 0, ny - 1)), int(np.clip(round(s[0]), 0, nx - 1))]
            ),
            reverse=True,
        )
        unsat_seeds = unsat_seeds[:max_sources]
    if unsat_seeds:
        phot = PSFPhotometry(
            CircularGaussianPRF(fwhm=fwhm_px),
            fit_shape=(fit_side, fit_side),
            grouper=SourceGrouper(min_separation=max(5.0, 2.5 * fwhm_px)),
            aperture_radius=fwhm_ap,
        )
        res_u = phot(
            work_sub, mask=fit_mask, error=err_c, init_params=_make_init_table(unsat_seeds)
        )
        resid_all = phot.make_residual_image(work_sub)
        snr_res = matched_filter_snr(resid_all, bg.variance, psf_kernel, quality_mask=~fit_mask)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", NoDetectionsWarning)
            res_peaks = find_peaks(snr_res, threshold=max(4.0, min_snr), box_size=3, mask=fit_mask)
        if res_peaks is not None and len(res_peaks) > 0:
            cur_xy = list(zip(res_u["x_fit"], res_u["y_fit"], strict=True))
            added = False
            for row in res_peaks:
                rx, ry = float(row["x_peak"]), float(row["y_peak"])
                if any(math.hypot(rx - stx, ry - sty) < 1.5 for stx, sty in sat_seeds):
                    continue
                if any(math.hypot(rx - float(cx), ry - float(cy)) < 1.2 for cx, cy in cur_xy):
                    continue
                unsat_seeds.append((rx, ry))
                unsat_morphs.append({"a": sigma_px, "b": sigma_px, "theta": 0.0, "sep_flag": 0.0})
                added = True
            if added:
                res_u = phot(
                    work_sub, mask=fit_mask, error=err_c, init_params=_make_init_table(unsat_seeds)
                )
                resid_all = phot.make_residual_image(work_sub)
        u_infos = getattr(phot, "fit_info", [])
        for i, r in enumerate(res_u):
            pcov = u_infos[i].get("param_cov") if i < len(u_infos) else None
            m_best = min(
                zip(unsat_seeds, unsat_morphs, strict=True),
                key=lambda sm: math.hypot(
                    sm[0][0] - float(r["x_init"]), sm[0][1] - float(r["y_init"])
                ),
            )[1]
            unsat_rows.append(
                (r, np.asarray(pcov, dtype=np.float64) if pcov is not None else None, m_best)
            )

    all_rows = sat_rows + unsat_rows
    detections: list[ExtractedSource] = []
    if all_rows:
        x_fit_arr = np.array([float(r["x_fit"]) for r, _, _ in all_rows], dtype=np.float64)
        y_fit_arr = np.array([float(r["y_fit"]) for r, _, _ in all_rows], dtype=np.float64)
        x_init_arr = np.array([float(r["x_init"]) for r, _, _ in all_rows], dtype=np.float64)
        y_init_arr = np.array([float(r["y_init"]) for r, _, _ in all_rows], dtype=np.float64)
        valid_xy = np.isfinite(x_fit_arr) & np.isfinite(y_fit_arr)
        x_use = np.clip(np.where(valid_xy, x_fit_arr, x_init_arr), 0.0, nx - 1.0)
        y_use = np.clip(np.where(valid_xy, y_fit_arr, y_init_arr), 0.0, ny - 1.0)

        x_win, y_win, _ = sep.winpos(sub_c, x_use, y_use, sig=sigma_px, mask=fit_mask)
        if len(x_use) > 1:
            sep_mat = np.hypot(x_use[:, None] - x_use[None, :], y_use[:, None] - y_use[None, :])
            np.fill_diagonal(sep_mat, np.inf)
            nearest_px = np.min(sep_mat, axis=1)
        else:
            nearest_px = np.full(len(x_use), np.inf, dtype=np.float64)
        blend_lim = max(3.5, 1.8 * fwhm_px)

        # Nominal astrometric floor terms per SCIENCE.md §C.1 table.
        sig_reg = 0.05 if sigma_reg_arcsec is None else float(sigma_reg_arcsec)
        floor_px2 = ((0.05**2 + 0.03**2) / (pixscale**2)) if pixscale > 0.0 else 0.0
        f_max_psf = float(erf(0.5 / (math.sqrt(2.0) * sigma_px))) ** 2

        for idx, (row, pcov, morph) in enumerate(all_rows):
            x_raw, y_raw = float(row["x_fit"]), float(row["y_fit"])
            if not (math.isfinite(x_raw) and math.isfinite(y_raw)):
                continue
            if not (-0.5 <= x_raw <= nx - 0.5 and -0.5 <= y_raw <= ny - 0.5):
                continue
            if math.hypot(x_raw - float(row["x_init"]), y_raw - float(row["y_init"])) > 2.5:
                continue
            flux = (
                float(row["flux_fit"]) if np.isfinite(row["flux_fit"]) else float(row["flux_init"])
            )
            if flux <= 0.0 or not math.isfinite(flux):
                continue
            x_p, y_p = float(x_use[idx]), float(y_use[idx])
            ix, iy = int(np.clip(round(x_p), 0, nx - 1)), int(np.clip(round(y_p), 0, ny - 1))
            snr_mf = float(snr_map[iy, ix])
            y0, y1 = max(0, iy - 1), min(ny, iy + 2)
            x0, x1 = max(0, ix - 1), min(nx, ix + 2)
            near_sat = bool(np.any(sat_mask[y0:y1, x0:x1]))
            if snr_mf < min_snr - 0.5 and not near_sat:
                continue

            # Local 5x5 reduced chi2 on post-subtraction residual (immune to neighboring stars).
            y5_0, y5_1 = max(0, iy - 2), min(ny, iy + 3)
            x5_0, x5_1 = max(0, ix - 2), min(nx, ix + 3)
            q_fit_5 = ~fit_mask[y5_0:y5_1, x5_0:x5_1]
            if int(np.sum(q_fit_5)) > 3:
                res_q = (
                    resid_all[y5_0:y5_1, x5_0:x5_1][q_fit_5] / err_c[y5_0:y5_1, x5_0:x5_1][q_fit_5]
                )
                chi2_nu = float(np.sum(res_q**2)) / float(int(np.sum(q_fit_5)) - 3)
            else:
                chi2_nu = float(row["reduced_chi2"]) if np.isfinite(row["reduced_chi2"]) else 1.0
            chi2_scale = max(1.0, chi2_nu)
            flux_err_raw = float(row["flux_err"]) if np.isfinite(row["flux_err"]) else 0.0
            if flux_err_raw <= 0.0:
                flux_err_raw = (
                    2.0
                    * math.sqrt(math.pi)
                    * sigma_px
                    * math.sqrt(max(float(bg.variance[iy, ix]), 1e-12))
                )
            flux_err = flux_err_raw * math.sqrt(chi2_scale)
            snr = flux / max(flux_err, 1e-12)
            if snr < min_snr:
                continue

            # Detector 2x2 covariance from WLS inverse Hessian scaled by max(1, chi2_nu).
            cov_xy = np.eye(2, dtype=np.float64) * ((fwhm_px / (2.355 * max(snr, 1.0))) ** 2)
            if pcov is not None and pcov.shape == (3, 3):
                sub_cov = pcov[1:3, 1:3] * chi2_scale
                if np.all(np.isfinite(sub_cov)) and sub_cov[0, 0] > 0.0 and sub_cov[1, 1] > 0.0:
                    cov_xy = sub_cov

            # Quality flags inside 3x3 core.
            det_flags = frame_flags
            if frame.flags is not None:
                core_flags = frame.flags[y0:y1, x0:x1]
                for bit in (
                    DetectionFlag.SATURATED,
                    DetectionFlag.BAD_PIX,
                    DetectionFlag.COSMIC,
                    DetectionFlag.PERSIST,
                    DetectionFlag.GHOST_SPIKE,
                ):
                    if np.any(core_flags & int(bit)):
                        det_flags |= int(bit)
            if near_sat:
                det_flags |= int(DetectionFlag.SATURATED)
                cov_xy = cov_xy * 4.0  # inflate sigma_pos by 2x per §B.3
            if nearest_px[idx] < blend_lim or (int(morph["sep_flag"]) & sep.OBJ_MERGED):
                det_flags |= int(DetectionFlag.BLENDED)
            if x_p < 6.0 or x_p > nx - 7.0 or y_p < 6.0 or y_p > ny - 7.0:
                det_flags |= int(DetectionFlag.EDGE)
            if snr < 5.0:
                det_flags |= int(DetectionFlag.LOW_SNR)

            a_px, b_px = max(morph["a"], 0.3), max(morph["b"], 0.3)
            elong = a_px / b_px
            roundness = (a_px - b_px) / max(a_px + b_px, 1e-6)
            no_blend_sat = not (det_flags & int(DetectionFlag.BLENDED | DetectionFlag.SATURATED))
            if no_blend_sat and elong > 2.2 and a_px > 2.5 * sigma_px:
                det_flags |= int(DetectionFlag.STREAK)
            elif no_blend_sat and b_px > 1.8 * sigma_px and snr > 5.0 and chi2_nu > 3.0:
                det_flags |= int(DetectionFlag.EXTENDED)

            # Sharpness and unflagged PSF weight fraction (§B.7).
            st_5 = sub_c[y5_0:y5_1, x5_0:x5_1] if near_sat else work_sub[y5_0:y5_1, x5_0:x5_1]
            q_5 = ~bad_mask[y5_0:y5_1, x5_0:x5_1]
            if near_sat:
                sharpness = 0.65
            else:
                p_max = float(np.max(st_5[q_5])) if np.any(q_5) else 0.0
                p_sum = float(np.sum(np.maximum(st_5[q_5], 0.0))) if np.any(q_5) else 1.0
                sharpness = 0.65 * (p_max / max(p_sum, 1e-9)) / max(f_max_psf, 1e-6)

            ky0, ky1 = max(0, 5 - (iy - y5_0)), min(11, 5 + (y5_1 - iy))
            kx0, kx1 = max(0, 5 - (ix - x5_0)), min(11, 5 + (x5_1 - ix))
            w_patch = psf_kernel[ky0:ky1, kx0:kx1] ** 2
            w_tot = float(np.sum(w_patch))
            unflagged_frac = float(np.sum(w_patch[q_5])) / w_tot if w_tot > 0.0 else 0.0

            xw, yw = float(x_win[idx]), float(y_win[idx])
            mismatch = (
                math.isfinite(xw)
                and math.isfinite(yw)
                and math.hypot(x_p - xw, y_p - yw) > 0.5
                and not (det_flags & int(DetectionFlag.SATURATED | DetectionFlag.BLENDED))
            )

            # Tangent-plane sky covariance per §C.1 Eq. (131).
            jac = local_wcs_jacobian(frame.wcs, x_p, y_p)
            cov_det = cov_xy + floor_px2 * np.eye(2, dtype=np.float64)
            cov_sky = jac @ cov_det @ jac.T
            sig_ra = math.sqrt(max(float(cov_sky[0, 0]), 1e-12))
            sig_dec = math.sqrt(max(float(cov_sky[1, 1]), 1e-12))
            corr = float(np.clip(cov_sky[0, 1] / (sig_ra * sig_dec), -0.999, 0.999))
            ra_deg, dec_deg = (float(v) for v in frame.wcs.pixel_to_world_values(x_p, y_p))

            detections.append(
                ExtractedSource(
                    source_index=len(detections),
                    x_px=x_p,
                    y_px=y_p,
                    x_win_px=xw if math.isfinite(xw) else x_p,
                    y_win_px=yw if math.isfinite(yw) else y_p,
                    ra_deg=ra_deg % 360.0,
                    dec_deg=dec_deg,
                    cov_px=(float(cov_xy[0, 0]), float(cov_xy[1, 1]), float(cov_xy[0, 1])),
                    cov_sky_arcsec2=(
                        float(cov_sky[0, 0]),
                        float(cov_sky[1, 1]),
                        float(cov_sky[0, 1]),
                    ),
                    sig_ra_arcsec=sig_ra,
                    sig_dec_arcsec=sig_dec,
                    corr_radec=corr,
                    flux=flux,
                    flux_err=flux_err,
                    snr=snr,
                    snr_matched=snr_mf,
                    fwhm_px=fwhm_px,
                    fwhm_arcsec=fwhm_px * pixscale,
                    elongation=elong,
                    theta_deg=math.degrees(morph["theta"]),
                    sharpness=sharpness,
                    roundness=roundness,
                    psf_chi2_nu=chi2_nu,
                    unflagged_weight_frac=unflagged_frac,
                    real_bogus=compute_real_bogus(
                        psf_chi2_nu=chi2_nu,
                        sharpness=sharpness,
                        roundness=roundness,
                        unflagged_weight_frac=unflagged_frac,
                        flags=det_flags,
                    ),
                    flags=det_flags,
                    centroid_mismatch=mismatch,
                )
            )

    return ExtractionResult(
        detections=detections,
        wcs=frame.wcs,
        epoch_mjd_tdb=frame.epoch.mjd_tdb_mid,
        band_id=frame.band_id,
        array_id=frame.array_id,
        wavelength_um=frame.wavelength_um,
        shape=frame.shape,
        pixscale_arcsec=pixscale,
        psf_fwhm_px=fwhm_px,
        background_rms=bg.empirical_rms,
        frame_flags=frame_flags,
        snr_map=snr_map,
        warnings=list(frame.warnings),
    )


__all__ = [
    "ExtractedSource",
    "ExtractionResult",
    "compute_real_bogus",
    "estimate_psf_kernel",
    "extract_sources",
    "matched_filter_snr",
]
