"""Image calibration: FITS frame loading, quality masks, background/noise model, reprojection."""

from __future__ import annotations

import io
import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from enum import IntFlag
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.stats import SigmaClip
from astropy.time import Time
from astropy.wcs import WCS
from numpy.typing import NDArray
from photutils.background import Background2D, MADStdBackgroundRMS, SExtractorBackground
from reproject import reproject_adaptive, reproject_exact
from scipy.ndimage import binary_dilation, label

from sphx_core.timeutil import ParsedEpoch, parse_fits_epoch
from sphx_core.wcs import WCSMetrics, extract_wcs_metrics

_IMAGE_NAMES = ("IMAGE", "SCI", "PRIMARY", "FLUX")
_VARIANCE_NAMES = ("VARIANCE", "VAR", "ERR", "ERROR", "IVAR", "WEIGHT", "RMS")
_FLAGS_NAMES = ("FLAGS", "FLAG", "MASK", "DQ", "BPM")
_DEFAULT_PSF_SIGMA_PX = 0.85  # ~2.0 px FWHM at 6.2"/px (SCIENCE.md §0)

BAND_GROUPS: dict[str, tuple[float, float]] = {
    "B1": (0.75, 1.11),
    "B2": (1.11, 1.64),
    "B3": (1.64, 2.42),
    "B4": (2.42, 3.50),
    "B5": (3.50, 4.20),
    "B6": (4.20, 5.00),
}


class DetectionFlag(IntFlag):
    """Bitmask flags matching `DATA_MODEL.md §3` and `backend/app/db/flags.py`."""

    SATURATED = 1 << 0
    BLENDED = 1 << 1
    EDGE = 1 << 2
    BAD_PIX = 1 << 3
    COSMIC = 1 << 4
    PERSIST = 1 << 5
    GHOST_SPIKE = 1 << 6
    LOW_SNR = 1 << 7
    EXTENDED = 1 << 8
    STREAK = 1 << 9
    USER_EPOCH = 1 << 10
    REPROJECTED = 1 << 11
    AMBIGUOUS_MATCH = 1 << 16
    LOST = 1 << 17
    MERGED = 1 << 18
    USER_OVERRIDE = 1 << 19
    DUPLICATE_PROPOSAL = 1 << 20
    PM_OUTLIER = 1 << 21
    VIM_CANDIDATE = 1 << 22


HARD_BAD_MASK: int = int(
    DetectionFlag.BAD_PIX | DetectionFlag.COSMIC | DetectionFlag.PERSIST | DetectionFlag.GHOST_SPIKE
)


@dataclass(frozen=True)
class FrameData:
    """Loaded native L2 science frame arrays, WCS, epoch, and spectral metadata."""

    image: NDArray[np.float64]
    variance: NDArray[np.float64] | None
    flags: NDArray[np.int32] | None
    zodi: NDArray[np.float64] | None
    psf: NDArray[np.float64] | None
    wcs_wave: NDArray[np.float64] | None
    wcs: WCS
    wcs_metrics: WCSMetrics
    epoch: ParsedEpoch
    shape: tuple[int, int]
    band_id: str | None
    array_id: int | None
    wavelength_um: tuple[float, float] | None
    bunit: str | None
    psf_sigma_px: float
    frame_flags: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BackgroundModel:
    """2D background, variance, quality mask, and scatter diagnostics (native image units)."""

    background: NDArray[np.float64]
    subtracted: NDArray[np.float64]
    rms: NDArray[np.float64]
    variance: NDArray[np.float64]
    quality_mask: NDArray[np.bool_]
    empirical_rms: float
    scattered_light: bool
    zodi_subtracted: bool


@dataclass(frozen=True)
class ReprojectedFrame:
    """Flux-conserving reprojected frame with propagated variance and correlated-noise factor."""

    image: NDArray[np.float64]
    variance: NDArray[np.float64]
    quality_mask: NDArray[np.bool_]
    flags: NDArray[np.int32]
    wcs: WCS
    shape: tuple[int, int]
    f_corr: float
    reprojected: bool
    wavelength_um: tuple[float, float] | None
    band_id: str | None
    mjd_tdb: float


@dataclass(frozen=True)
class CoaddResult:
    """Inverse-variance weighted band-group co-add (MJy/sr, variance (MJy/sr)^2, MJD TDB)."""

    band_group: str
    image: NDArray[np.float64]
    variance: NDArray[np.float64]
    quality_mask: NDArray[np.bool_]
    wcs: WCS
    mjd_tdb_mean: float
    wavelength_eff_um: float | None
    n_frames: int
    f_corr: float


def _find_hdu(hdul: fits.HDUList, names: tuple[str, ...]) -> fits.ImageHDU | fits.PrimaryHDU | None:
    by_name = {(h.name or "PRIMARY").upper(): h for h in hdul}
    for name in names:
        hdu = by_name.get(name)
        if hdu is not None and getattr(hdu, "data", None) is not None:
            return hdu
    return None


def _parse_variance_array(hdul: fits.HDUList, shape: tuple[int, int]) -> NDArray[np.float64] | None:
    for name in _VARIANCE_NAMES:
        hdu = _find_hdu(hdul, (name,))
        if hdu is None:
            continue
        arr = np.asarray(hdu.data, dtype=np.float64)
        if arr.ndim > 2:
            arr = arr[0]
        if arr.shape != shape:
            continue
        if name in ("ERR", "ERROR", "RMS"):
            return arr**2
        if name in ("IVAR", "WEIGHT"):
            return np.where(arr > 0.0, 1.0 / arr, np.nan)
        return arr
    return None


def _open_hdul(source: Path | str | bytes | fits.HDUList) -> tuple[fits.HDUList, bool]:
    if isinstance(source, fits.HDUList):
        return source, False
    if isinstance(source, (Path, str)):
        return fits.open(Path(source), memmap=True, lazy_load_hdus=True), True
    return fits.open(io.BytesIO(source), memmap=False, lazy_load_hdus=True), True


def load_frame(
    source: Path | str | bytes | fits.HDUList | FrameData,
    *,
    user_epoch: Time | str | float | int | None = None,
    wcs_override: WCS | None = None,
    psf_sigma_px: float | None = None,
) -> FrameData:
    """Load a FITS frame (path, bytes, HDUList, or FrameData) into a pure `FrameData` container.

    Units: `image` in header BUNIT (default MJy/sr), `variance` in BUNIT^2, `psf_sigma_px` in px.
    """
    if isinstance(source, FrameData):
        if wcs_override is None and psf_sigma_px is None:
            return source
        return replace(
            source,
            wcs=wcs_override.deepcopy() if wcs_override is not None else source.wcs,
            psf_sigma_px=psf_sigma_px if psf_sigma_px is not None else source.psf_sigma_px,
            warnings=list(source.warnings),
        )

    hdul, should_close = _open_hdul(source)
    try:
        sci_hdu = _find_hdu(hdul, _IMAGE_NAMES)
        if sci_hdu is None:
            for h in hdul:
                if getattr(h, "data", None) is not None and np.asarray(h.data).ndim >= 2:
                    sci_hdu = h
                    break
        if sci_hdu is None or sci_hdu.data is None:
            raise ValueError("No 2D science image HDU found in FITS input.")

        data = np.asarray(sci_hdu.data, dtype=np.float64)
        if data.ndim > 2:
            data = data[0]
        ny, nx = int(data.shape[0]), int(data.shape[1])
        headers = [sci_hdu.header] + ([hdul[0].header] if sci_hdu is not hdul[0] else [])

        if wcs_override is not None:
            wcs = wcs_override.deepcopy()
            wcs_metrics = extract_wcs_metrics(wcs.to_header(relax=True), shape=(ny, nx))
        else:
            parsed = WCS(sci_hdu.header)
            if not parsed.has_celestial:
                parsed = WCS(hdul[0].header)
            if not parsed.has_celestial:
                raise ValueError("No celestial WCS found in FITS headers.")
            wcs = parsed.celestial
            wcs_metrics = extract_wcs_metrics(wcs.to_header(relax=True), shape=(ny, nx))
        if wcs_metrics is None:
            raise ValueError("Failed to compute WCS metrics for science frame.")

        epoch, epoch_warns = parse_fits_epoch(headers, user_epoch=user_epoch)
        if epoch is None:
            raise ValueError("Observation epoch missing; supply `user_epoch`.")

        var = _parse_variance_array(hdul, (ny, nx))
        flg_hdu = _find_hdu(hdul, _FLAGS_NAMES)
        flags_arr = (
            np.asarray(flg_hdu.data, dtype=np.int32)
            if flg_hdu is not None and np.asarray(flg_hdu.data).shape == (ny, nx)
            else None
        )
        zodi_hdu = _find_hdu(hdul, ("ZODI",))
        zodi_arr = (
            np.asarray(zodi_hdu.data, dtype=np.float64)
            if zodi_hdu is not None and np.asarray(zodi_hdu.data).shape == (ny, nx)
            else None
        )
        psf_hdu = _find_hdu(hdul, ("PSF",))
        psf_arr = np.asarray(psf_hdu.data, dtype=np.float64) if psf_hdu is not None else None
        wave_hdu = _find_hdu(hdul, ("WCS-WAVE", "WAVE"))
        wave_arr = (
            np.asarray(wave_hdu.data, dtype=np.float64)
            if wave_hdu is not None and np.asarray(wave_hdu.data).shape == (ny, nx)
            else None
        )

        def _hdr_str(keys: tuple[str, ...]) -> str | None:
            for hdr in headers:
                for k in keys:
                    if hdr.get(k) not in (None, ""):
                        return str(hdr[k]).strip()
            return None

        def _hdr_float(keys: tuple[str, ...]) -> float | None:
            for hdr in headers:
                for k in keys:
                    v = hdr.get(k)
                    if v is not None and not isinstance(v, bool):
                        try:
                            num = float(v)
                            if math.isfinite(num):
                                return num
                        except (TypeError, ValueError):
                            pass
            return None

        band_id = _hdr_str(("BAND", "FILTER", "CHANNEL", "BANDID"))
        arr_raw = _hdr_float(("ARRAY", "DETECTOR", "DET_ID"))
        array_id = int(arr_raw) if arr_raw is not None and 1 <= int(arr_raw) <= 6 else None
        lam_min = _hdr_float(("LAM_MIN", "WAVEMIN", "EM_MIN"))
        lam_max = _hdr_float(("LAM_MAX", "WAVEMAX", "EM_MAX"))
        lam_range = (lam_min, lam_max) if lam_min is not None and lam_max is not None else None
        if lam_range is None and wave_arr is not None and np.any(np.isfinite(wave_arr)):
            lam_range = (float(np.nanmin(wave_arr)), float(np.nanmax(wave_arr)))

        fwhm_hdr = _hdr_float(("PSF_FWHM", "FWHM"))
        sigma_est = (
            psf_sigma_px
            if psf_sigma_px is not None
            else (
                fwhm_hdr / 2.3548200450309493
                if fwhm_hdr is not None and fwhm_hdr > 0.0
                else _DEFAULT_PSF_SIGMA_PX
            )
        )
        frame_flags = int(DetectionFlag.USER_EPOCH) if epoch.user_supplied else 0

        return FrameData(
            image=data,
            variance=var,
            flags=flags_arr,
            zodi=zodi_arr,
            psf=psf_arr,
            wcs_wave=wave_arr,
            wcs=wcs,
            wcs_metrics=wcs_metrics,
            epoch=epoch,
            shape=(ny, nx),
            band_id=band_id,
            array_id=array_id,
            wavelength_um=lam_range,
            bunit=_hdr_str(("BUNIT",)),
            psf_sigma_px=float(sigma_est),
            frame_flags=frame_flags,
            warnings=epoch_warns,
        )
    finally:
        if should_close:
            hdul.close()


def build_quality_mask(
    image: NDArray[np.float64],
    flags: NDArray[np.int32] | None = None,
    variance: NDArray[np.float64] | None = None,
    *,
    mask_bits: int = HARD_BAD_MASK,
    edge_margin_px: int = 0,
) -> NDArray[np.bool_]:
    """Return boolean pixel quality mask `Q` (`True` = usable, `False` = masked) per §B.3."""
    q = np.isfinite(image)
    if variance is not None:
        q &= np.isfinite(variance) & (variance > 0.0)
    if flags is not None and mask_bits != 0:
        q &= (flags & mask_bits) == 0
    if edge_margin_px > 0:
        ny, nx = image.shape
        q[:edge_margin_px, :] = False
        q[max(0, ny - edge_margin_px) :, :] = False
        q[:, :edge_margin_px] = False
        q[:, max(0, nx - edge_margin_px) :] = False
    return q


def saturation_mask(
    image: NDArray[np.float64],
    flags: NDArray[np.int32] | None = None,
    *,
    saturate_level: float | None = None,
) -> NDArray[np.bool_]:
    """Return boolean mask (`True` = saturated/nonlinear core pixel) from FLAGS or ceiling."""
    sat = (
        (flags & int(DetectionFlag.SATURATED)) != 0
        if flags is not None
        else np.zeros(image.shape, dtype=bool)
    )
    if saturate_level is not None:
        sat |= np.isfinite(image) & (image >= saturate_level)
    return sat


def _fit_clipped_plane(
    work: NDArray[np.float64], mask: NDArray[np.bool_], sigma_clip: SigmaClip
) -> tuple[NDArray[np.float64], float]:
    """Fit sigma-clipped median or significant 2D linear plane on small cutouts (<64x64)."""
    valid = ~mask & np.isfinite(work)
    if np.sum(valid) < 16:
        fin = work[np.isfinite(work)]
        med = float(np.nanmedian(fin)) if fin.size else 0.0
        rms = float(MADStdBackgroundRMS(sigma_clip=sigma_clip)(fin)) if fin.size else 1.0
        return np.full_like(work, med), max(rms, 1e-6)

    vals = work[valid]
    clipped = sigma_clip(vals, masked=True)
    kept = ~np.ma.getmaskarray(clipped)
    med = float(SExtractorBackground(sigma_clip=sigma_clip)(vals))
    rms = float(MADStdBackgroundRMS(sigma_clip=sigma_clip)(vals))
    rms = max(rms, 1e-6)

    yy, xx = np.nonzero(valid)
    y_k, x_k, z_k = yy[kept].astype(np.float64), xx[kept].astype(np.float64), vals[kept]
    if len(z_k) >= 24:
        yc, xc = 0.5 * work.shape[0], 0.5 * work.shape[1]
        design = np.column_stack((np.ones_like(x_k), x_k - xc, y_k - yc))
        coef, *_ = np.linalg.lstsq(design, z_k, rcond=None)
        res_const = z_k - np.median(z_k)
        res_plane = z_k - design @ coef
        chi2_diff = float(np.sum(res_const**2) - np.sum(res_plane**2)) / (rms**2)
        if chi2_diff > 25.0:  # > ~4.5 sigma gradient significance
            gy, gx = np.mgrid[: work.shape[0], : work.shape[1]]
            plane = coef[0] + coef[1] * (gx - xc) + coef[2] * (gy - yc)
            return plane, rms

    return np.full_like(work, med), rms


def _source_mask_from_residual(
    resid: NDArray[np.float64],
    rms: NDArray[np.float64] | float,
    q_good: NDArray[np.bool_],
    detect_sigma: float,
    n_dilate: int,
) -> NDArray[np.bool_]:
    above = (resid > (detect_sigma * rms)) & q_good
    labeled, n_comp = label(above)
    if n_comp == 0:
        return np.zeros(resid.shape, dtype=bool)
    keep_lbl = np.bincount(labeled.ravel()) >= 3
    keep_lbl[0] = False
    src_mask = keep_lbl[labeled]
    if n_dilate > 0 and np.any(src_mask):
        return np.asarray(binary_dilation(src_mask, iterations=n_dilate), dtype=np.bool_)
    return np.asarray(src_mask, dtype=np.bool_)


def estimate_background_and_noise(
    frame: FrameData,
    *,
    box_size: int = 32,
    detect_sigma: float = 2.0,
    n_dilate: int = 2,
) -> BackgroundModel:
    """Subtract ZODI (if present), mask sources, fit 2D background, and propagate noise (§B.1-B.2).

    Units: `background`, `subtracted`, `rms` in native image units (MJy/sr); `variance` in units^2.
    """
    zodi_sub = frame.zodi is not None
    work = frame.image - frame.zodi if frame.zodi is not None else frame.image.copy()
    q_good = build_quality_mask(
        work,
        frame.flags,
        frame.variance,
        mask_bits=HARD_BAD_MASK | int(DetectionFlag.SATURATED),
    )

    sigma_clip = SigmaClip(sigma=3.0, maxiters=5)
    ny, nx = work.shape
    scattered_light = False

    if min(ny, nx) <= 64:
        valid_vals = work[q_good] if np.any(q_good) else work[np.isfinite(work)]
        init_med = float(np.median(valid_vals)) if valid_vals.size else 0.0
        init_rms = max(
            float(MADStdBackgroundRMS(sigma_clip=sigma_clip)(valid_vals))
            if valid_vals.size
            else 1.0,
            1e-6,
        )
        fit_mask = (~q_good) | _source_mask_from_residual(
            work - init_med, init_rms, q_good, detect_sigma, n_dilate
        )
        bkg_map, emp_rms = _fit_clipped_plane(work, fit_mask, sigma_clip)
        rms_map = np.full_like(work, emp_rms)
    else:
        # Two-pass Background2D avoids masking smooth scattered light as a compact source.
        box = (min(box_size, ny), min(box_size, nx))
        f_sz = (3, 3) if min(ny // box[0], nx // box[1]) >= 5 else (1, 1)
        bkg_kw = dict(
            box_size=box,
            filter_size=f_sz,
            exclude_percentile=80.0,
            sigma_clip=sigma_clip,
            bkg_estimator=SExtractorBackground(),
            bkg_rms_estimator=MADStdBackgroundRMS(),
        )
        try:
            bkg_rough = Background2D(work, mask=~q_good, **bkg_kw)
            src_mask = _source_mask_from_residual(
                work - bkg_rough.background,
                np.maximum(bkg_rough.background_rms, 1e-6),
                q_good,
                detect_sigma,
                n_dilate,
            )
            fit_mask = (~q_good) | src_mask
            if np.mean(~fit_mask) < 0.25:
                raise ValueError("Insufficient unmasked pixels for 2D mesh background.")
            # LVF guard: remove smooth 1D iso-wavelength median profile if WCS-WAVE exists.
            lvf_prof = np.zeros_like(work)
            work_for_2d = work
            if frame.wcs_wave is not None:
                grad_y, grad_x = np.gradient(np.nan_to_num(frame.wcs_wave, nan=0.0))
                iso_axis = 0 if np.nanmean(np.abs(grad_x)) > np.nanmean(np.abs(grad_y)) else 1
                prof_1d = np.nanmedian(
                    np.where(fit_mask, np.nan, work), axis=iso_axis, keepdims=True
                )
                prof_1d = np.nan_to_num(prof_1d - np.nanmedian(prof_1d), nan=0.0)
                lvf_prof = np.broadcast_to(prof_1d, work.shape).copy()
                work_for_2d = work - lvf_prof

            bkg2d = Background2D(work_for_2d, mask=fit_mask, **bkg_kw)
            bkg_map = bkg2d.background + lvf_prof
            rms_map = np.maximum(bkg2d.background_rms, 1e-6)
            emp_rms = float(np.median(rms_map))
            mesh = bkg2d.background_mesh
            scattered_light = (
                float(np.std(mesh)) > 3.0 * emp_rms
                or float(np.max(np.abs(mesh - np.median(mesh)))) > 5.0 * emp_rms
            )
        except ValueError:
            bkg_map, emp_rms = _fit_clipped_plane(work, ~q_good, sigma_clip)
            rms_map = np.full_like(work, emp_rms)

    if frame.variance is not None:
        var_valid = np.isfinite(frame.variance) & (frame.variance > 0.0)
        var_map = np.where(var_valid, frame.variance, rms_map**2)
        rms_map = np.sqrt(var_map)
    else:
        var_map = rms_map**2

    # Final quality mask excludes only hard bad pixels (retains SATURATED for wing fits).
    q_final = build_quality_mask(work, frame.flags, var_map, mask_bits=HARD_BAD_MASK)
    return BackgroundModel(
        background=bkg_map,
        subtracted=work - bkg_map,
        rms=rms_map,
        variance=var_map,
        quality_mask=q_final,
        empirical_rms=emp_rms,
        scattered_light=scattered_light,
        zodi_subtracted=zodi_sub,
    )


def measure_correlated_noise(
    residual_img: NDArray[np.float64],
    variance: NDArray[np.float64],
    quality_mask: NDArray[np.bool_],
    *,
    radius_px: float = 2.0,
    n_apertures: int = 200,
    seed: int = 0,
) -> float:
    """Measure empirical correlated-noise factor `F_corr >= 1.0` via random sky apertures (§A.4)."""
    ny, nx = residual_img.shape
    r_int = max(1, int(math.ceil(radius_px)))
    if min(ny, nx) <= 2 * r_int + 2:
        return 1.0

    # Mask bright > 3 sigma sources so apertures sample background only.
    rms_est = np.sqrt(np.maximum(variance, 1e-12))
    src_pix = binary_dilation((residual_img > 3.0 * rms_est) & quality_mask, iterations=r_int)
    usable = quality_mask & (~src_pix)
    ys, xs = np.nonzero(usable[r_int : ny - r_int, r_int : nx - r_int])
    if ys.size < 20:
        return 1.0

    rng = np.random.default_rng(seed)
    idx = rng.integers(0, ys.size, size=min(n_apertures, max(40, ys.size)))
    cy_list = ys[idx] + r_int
    cx_list = xs[idx] + r_int

    dy, dx = np.mgrid[-r_int : r_int + 1, -r_int : r_int + 1]
    disk = (dx * dx + dy * dy) <= radius_px**2
    ap_sums: list[float] = []
    var_sums: list[float] = []
    for cy, cx in zip(cy_list, cx_list, strict=True):
        sub_q = quality_mask[cy - r_int : cy + r_int + 1, cx - r_int : cx + r_int + 1] & disk
        if np.sum(sub_q) < 0.7 * np.sum(disk):
            continue
        sub_img = residual_img[cy - r_int : cy + r_int + 1, cx - r_int : cx + r_int + 1]
        sub_var = variance[cy - r_int : cy + r_int + 1, cx - r_int : cx + r_int + 1]
        ap_sums.append(float(np.sum(sub_img[sub_q])))
        var_sums.append(float(np.sum(sub_var[sub_q])))

    if len(ap_sums) < 15:
        return 1.0
    emp_std = float(np.std(ap_sums, ddof=1))
    exp_std = math.sqrt(max(float(np.median(var_sums)), 1e-12))
    return float(min(3.0, max(1.0, emp_std / exp_std)))


def wcs_grids_match(
    wcs_a: WCS,
    shape_a: tuple[int, int],
    wcs_b: WCS,
    shape_b: tuple[int, int],
    *,
    tol_px: float = 1e-4,
) -> bool:
    """Return `True` if two 2D frames already share an identical pixel-sky grid within `tol_px`."""
    if shape_a != shape_b:
        return False
    ny, nx = shape_a
    xs = np.array([0.0, nx - 1.0, 0.0, nx - 1.0, 0.5 * (nx - 1.0)])
    ys = np.array([0.0, 0.0, ny - 1.0, ny - 1.0, 0.5 * (ny - 1.0)])
    ra, dec = wcs_a.all_pix2world(xs, ys, 0)
    xb, yb = wcs_b.all_world2pix(ra, dec, 0)
    return bool(np.all(np.hypot(xb - xs, yb - ys) <= tol_px))


def reproject_to_grid(
    frame: FrameData,
    target_wcs: WCS,
    target_shape: tuple[int, int],
    *,
    subtract_bg: bool = True,
    measure_fcorr: bool = True,
    seed: int = 0,
    bg: BackgroundModel | None = None,
) -> ReprojectedFrame:
    """Reproject a frame onto `(target_wcs, target_shape)` conserving surface brightness (§A.4)."""
    bg = bg if bg is not None else estimate_background_and_noise(frame)
    src_img = bg.subtracted if subtract_bg else frame.image
    q_clean = bg.quality_mask & (~saturation_mask(frame.image, frame.flags))

    if wcs_grids_match(frame.wcs, frame.shape, target_wcs, target_shape):
        return ReprojectedFrame(
            image=src_img.copy(),
            variance=bg.variance.copy(),
            quality_mask=bg.quality_mask.copy(),
            flags=frame.flags.copy()
            if frame.flags is not None
            else np.zeros(frame.shape, dtype=np.int32),
            wcs=target_wcs.deepcopy(),
            shape=target_shape,
            f_corr=1.0,
            reprojected=False,
            wavelength_um=frame.wavelength_um,
            band_id=frame.band_id,
            mjd_tdb=frame.epoch.mjd_tdb_mid,
        )

    masked_img = np.where(bg.quality_mask, src_img, np.nan)
    masked_var = np.where(bg.quality_mask, bg.variance, np.nan)
    reproj_fn = reproject_exact if max(target_shape) <= 256 else reproject_adaptive
    kw = {"conserve_flux": True} if reproj_fn is reproject_adaptive else {}

    img_out, foot = reproj_fn((masked_img, frame.wcs), target_wcs, shape_out=target_shape, **kw)
    var_out, _ = reproj_fn((masked_var, frame.wcs), target_wcs, shape_out=target_shape, **kw)
    q_float, _ = reproject_adaptive(
        (q_clean.astype(np.float64), frame.wcs),
        target_wcs,
        shape_out=target_shape,
        conserve_flux=False,
    )

    valid = np.isfinite(img_out) & np.isfinite(var_out) & (foot > 0.5) & (q_float > 0.5)
    med_var = float(np.nanmedian(bg.variance[bg.quality_mask])) if np.any(bg.quality_mask) else 1.0
    img_clean = np.where(valid, img_out, 0.0)
    var_clean = np.where(valid & (var_out > 0.0), var_out, med_var)
    f_corr = (
        measure_correlated_noise(img_clean, var_clean, valid, seed=seed) if measure_fcorr else 1.2
    )
    out_flags = np.where(valid, int(DetectionFlag.REPROJECTED), int(DetectionFlag.BAD_PIX)).astype(
        np.int32
    )

    return ReprojectedFrame(
        image=img_clean,
        variance=var_clean * (f_corr**2),
        quality_mask=valid,
        flags=out_flags,
        wcs=target_wcs.deepcopy(),
        shape=target_shape,
        f_corr=f_corr,
        reprojected=True,
        wavelength_um=frame.wavelength_um,
        band_id=frame.band_id,
        mjd_tdb=frame.epoch.mjd_tdb_mid,
    )


def band_group_for_wavelength(lam_um: float | tuple[float, float] | None) -> str | None:
    """Map effective wavelength [um] onto SPHEREx band group `B1..B6` (§B.4)."""
    if lam_um is None:
        return None
    lam_c = 0.5 * (lam_um[0] + lam_um[1]) if isinstance(lam_um, tuple) else float(lam_um)
    for name, (lo, hi) in BAND_GROUPS.items():
        if lo <= lam_c <= hi + 1e-6:
            return name
    return None


def coadd_band_group(
    frames: Sequence[Path | str | bytes | fits.HDUList | FrameData],
    *,
    target_wcs: WCS | None = None,
    target_shape: tuple[int, int] | None = None,
    sigma_clip: float = 3.0,
) -> dict[str, CoaddResult]:
    """Perform inverse-variance weighted, 3-sigma clipped band-grouped co-adds (§B.4)."""
    loaded = [load_frame(f) for f in frames]
    if not loaded:
        return {}
    ref_wcs = target_wcs if target_wcs is not None else loaded[0].wcs
    ref_shape = target_shape if target_shape is not None else loaded[0].shape

    grouped: dict[str, list[FrameData]] = {}
    for fr in loaded:
        grp = (
            fr.band_id
            if fr.band_id in BAND_GROUPS
            else (band_group_for_wavelength(fr.wavelength_um) or fr.band_id or "UNKNOWN")
        )
        grouped.setdefault(grp, []).append(fr)

    results: dict[str, CoaddResult] = {}
    for grp, grp_frames in grouped.items():
        reproj = [reproject_to_grid(fr, ref_wcs, ref_shape) for fr in grp_frames]
        imgs = np.stack([r.image for r in reproj], axis=0)
        vars_ = np.stack([r.variance for r in reproj], axis=0)
        masks = np.stack([r.quality_mask for r in reproj], axis=0)

        weights = np.where(masks & (vars_ > 0.0), 1.0 / np.maximum(vars_, 1e-18), 0.0)
        if len(reproj) >= 3:
            med = np.nanmedian(np.where(masks, imgs, np.nan), axis=0)
            keep = np.abs(imgs - med[None, :, :]) <= sigma_clip * np.sqrt(np.maximum(vars_, 1e-18))
            weights = np.where(keep, weights, 0.0)

        w_sum = np.sum(weights, axis=0)
        q_co = w_sum > 0.0
        img_co = np.divide(
            np.sum(weights * imgs, axis=0), w_sum, out=np.zeros(ref_shape), where=q_co
        )
        var_co = np.divide(1.0, w_sum, out=np.full(ref_shape, np.nanmedian(vars_)), where=q_co)

        tot_w = np.sum(weights, axis=(1, 2))
        if float(np.sum(tot_w)) > 0.0:
            t_mean = float(np.sum(tot_w * np.array([r.mjd_tdb for r in reproj])) / np.sum(tot_w))
            lams = [
                0.5 * (r.wavelength_um[0] + r.wavelength_um[1]) for r in reproj if r.wavelength_um
            ]
            lam_eff = float(np.mean(lams)) if lams else None
        else:
            t_mean = float(np.mean([r.mjd_tdb for r in reproj]))
            lam_eff = None

        f_corr_mean = float(np.mean([r.f_corr for r in reproj]))
        results[grp] = CoaddResult(
            band_group=grp,
            image=img_co,
            variance=var_co,
            quality_mask=q_co,
            wcs=ref_wcs.deepcopy(),
            mjd_tdb_mean=t_mean,
            wavelength_eff_um=lam_eff,
            n_frames=len(reproj),
            f_corr=f_corr_mean,
        )
    return results


def evaluate_lvf_wavelength(
    frame_or_hdul: Any,
    x_pix: float,
    y_pix: float,
    lvf_gradient_axis: int = 1,
) -> tuple[float, float]:
    """Calculate calibrated wavelength and bandwidth (um) at detector pixel (x_pix, y_pix) (§0, §A).

    Interpolates 2D WCS-WAVE lookup table when present, falling back to linear LVF dispersion.
    """
    from scipy.interpolate import RegularGridInterpolator

    # 1. If FrameData instance
    if isinstance(frame_or_hdul, FrameData):
        if frame_or_hdul.wcs_wave is not None:
            wave = frame_or_hdul.wcs_wave
            ny, nx = frame_or_hdul.shape
            y_pts = np.arange(ny, dtype=np.float64)
            x_pts = np.arange(nx, dtype=np.float64)
            if wave.ndim == 2:
                rgi = RegularGridInterpolator((y_pts, x_pts), wave, bounds_error=False, fill_value=None)
                w = float(rgi([[y_pix, x_pix]])[0])
                return round(w, 5), round(w / 80.0, 5)
            elif wave.ndim == 3:
                rgi_w = RegularGridInterpolator((y_pts, x_pts), wave[..., 0], bounds_error=False, fill_value=None)
                rgi_b = RegularGridInterpolator((y_pts, x_pts), wave[..., 1], bounds_error=False, fill_value=None)
                return round(float(rgi_w([[y_pix, x_pix]])[0]), 5), round(float(rgi_b([[y_pix, x_pix]])[0]), 5)

        w_range = frame_or_hdul.wavelength_um or BAND_GROUPS.get(frame_or_hdul.band_id or "", (0.75, 1.11))
        ny, nx = frame_or_hdul.shape
        coord = x_pix if lvf_gradient_axis == 1 else y_pix
        max_dim = max(1.0, (nx - 1.0) if lvf_gradient_axis == 1 else (ny - 1.0))
        frac = max(0.0, min(1.0, coord / max_dim))
        w = w_range[0] + frac * (w_range[1] - w_range[0])
        b = (w_range[1] - w_range[0]) / 16.0
        return round(float(w), 5), round(float(b), 5)

    # 2. If HDUList, filepath, or bytes
    hdul, should_close = _open_hdul(frame_or_hdul)
    try:
        if "WCS-WAVE" in hdul:
            whdu = hdul["WCS-WAVE"]
            data = getattr(whdu, "data", None)
            if data is not None and hasattr(data, "names") and {"X", "Y", "VALUES"}.issubset(data.names):
                x_pts = np.asarray(data["X"][0], dtype=np.float64)
                y_pts = np.asarray(data["Y"][0], dtype=np.float64)
                vals = np.asarray(data["VALUES"][0], dtype=np.float64)
                rgi_w = RegularGridInterpolator((y_pts, x_pts), vals[:, :, 0], bounds_error=False, fill_value=None)
                rgi_b = RegularGridInterpolator((y_pts, x_pts), vals[:, :, 1], bounds_error=False, fill_value=None)
                fx = x_pix + 1.0 if x_pts[0] >= 1.0 else x_pix
                fy = y_pix + 1.0 if y_pts[0] >= 1.0 else y_pix
                return round(float(rgi_w([[fy, fx]])[0]), 5), round(float(rgi_b([[fy, fx]])[0]), 5)
            elif isinstance(data, np.ndarray) and data.ndim >= 2:
                ny, nx = data.shape[:2]
                y_pts = np.arange(ny, dtype=np.float64)
                x_pts = np.arange(nx, dtype=np.float64)
                if data.ndim == 2:
                    rgi = RegularGridInterpolator((y_pts, x_pts), data, bounds_error=False, fill_value=None)
                    w = float(rgi([[y_pix, x_pix]])[0])
                    return round(w, 5), round(w / 80.0, 5)
                rgi_w = RegularGridInterpolator((y_pts, x_pts), data[..., 0], bounds_error=False, fill_value=None)
                rgi_b = RegularGridInterpolator((y_pts, x_pts), data[..., 1], bounds_error=False, fill_value=None)
                return round(float(rgi_w([[y_pix, x_pix]])[0]), 5), round(float(rgi_b([[y_pix, x_pix]])[0]), 5)

        hdr = hdul[0].header
        sci_hdu = _find_hdu(hdul, _IMAGE_NAMES) or hdul[0]
        s_hdr = getattr(sci_hdu, "header", hdr)
        w_min = float(s_hdr.get("LAM_MIN") or hdr.get("LAM_MIN") or 0.75)
        w_max = float(s_hdr.get("LAM_MAX") or hdr.get("LAM_MAX") or 1.11)
        nx = int(s_hdr.get("NAXIS1") or 2048)
        ny = int(s_hdr.get("NAXIS2") or 2048)
        coord = x_pix if lvf_gradient_axis == 1 else y_pix
        max_dim = max(1.0, (nx - 1.0) if lvf_gradient_axis == 1 else (ny - 1.0))
        frac = max(0.0, min(1.0, coord / max_dim))
        w = w_min + frac * (w_max - w_min)
        b = (w_max - w_min) / 16.0
        return round(float(w), 5), round(float(b), 5)
    finally:
        if should_close:
            hdul.close()


__all__ = [
    "BAND_GROUPS",
    "HARD_BAD_MASK",
    "BackgroundModel",
    "CoaddResult",
    "DetectionFlag",
    "FrameData",
    "ReprojectedFrame",
    "band_group_for_wavelength",
    "build_quality_mask",
    "coadd_band_group",
    "estimate_background_and_noise",
    "evaluate_lvf_wavelength",
    "load_frame",
    "measure_correlated_noise",
    "reproject_to_grid",
    "saturation_mask",
    "wcs_grids_match",
]
