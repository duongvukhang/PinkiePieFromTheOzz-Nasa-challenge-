"""FITS A-vs-B image, metadata, ZOGY difference, sum, ratio, and source-change comparison (§J)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from numpy.typing import NDArray
from scipy.optimize import linear_sum_assignment

from sphx_core.calib import (
    HARD_BAD_MASK,
    DetectionFlag,
    estimate_background_and_noise,
    reproject_to_grid,
)
from sphx_core.difference import StampTestResult, fit_monopole_vs_dipole, zogy_difference
from sphx_core.extraction import ExtractedSource, extract_sources
from sphx_core.motion import as_frame_data, forced_photometry_at_sky
from sphx_core.wcs import sky_to_tangent_arcsec


@dataclass(frozen=True)
class SourceComparisonEntry:
    """Matched or orphan source comparison record between frame A and frame B (§J.5-J.6)."""

    ra_deg: float
    dec_deg: float
    status: Literal["appeared", "disappeared", "moved", "changed brightness", "unchanged"]
    flux_a: float
    flux_a_err: float
    flux_b: float
    flux_b_err: float
    delta_mag: float | None
    sigma_delta_mag: float | None
    flux_significance_sigma: float
    offset_arcsec: float
    offset_sigma: float
    flags_a: int = 0
    flags_b: int = 0
    dipole_favored: bool = False
    monopole_favored: bool = False
    notes: str = ""


@dataclass(frozen=True)
class ComparisonResult:
    """Complete A-vs-B comparison products, header diff, and source change table (§J)."""

    header_diff: dict[str, dict[str, Any]]
    flux_scale_kappa: float
    kappa_n_stars: int
    diff_image: NDArray[np.float64]
    diff_variance: NDArray[np.float64]
    score_corr: NDArray[np.float64]
    sum_image: NDArray[np.float64]
    ratio_image: NDArray[np.float64]
    sources: list[SourceComparisonEntry]
    stamp_tests: list[StampTestResult] = field(default_factory=list)

    def find_nearest(
        self, ra_deg: float, dec_deg: float, *, max_radius_arcsec: float = 30.0
    ) -> SourceComparisonEntry:
        """Return nearest compared source within `max_radius_arcsec` of `(ra_deg, dec_deg)`."""
        if not self.sources:
            raise LookupError("No sources present in ComparisonResult.")
        target = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
        cands = SkyCoord(
            ra=[s.ra_deg for s in self.sources] * u.deg,
            dec=[s.dec_deg for s in self.sources] * u.deg,
            frame="icrs",
        )
        seps = target.separation(cands).arcsec
        best_i = int(np.argmin(seps))
        if float(seps[best_i]) > max_radius_arcsec:
            raise LookupError(
                f'Nearest source is {seps[best_i]:.2f}" > {max_radius_arcsec:.2f}" away.'
            )
        return self.sources[best_i]


def _header_diff(fa: Any, fb: Any) -> dict[str, dict[str, Any]]:
    bad_a = float(np.mean((fa.flags & int(HARD_BAD_MASK)) != 0)) if fa.flags is not None else 0.0
    bad_b = float(np.mean((fb.flags & int(HARD_BAD_MASK)) != 0)) if fb.flags is not None else 0.0
    return {
        "time": {
            "mjd_a": fa.epoch.mjd_tdb_mid,
            "mjd_b": fb.epoch.mjd_tdb_mid,
            "delta_days": fb.epoch.mjd_tdb_mid - fa.epoch.mjd_tdb_mid,
            "exptime_a_s": fa.epoch.exptime_s,
            "exptime_b_s": fb.epoch.exptime_s,
        },
        "wcs": {
            "pixscale_a_arcsec": fa.wcs_metrics.pixscale_arcsec,
            "pixscale_b_arcsec": fb.wcs_metrics.pixscale_arcsec,
            "pa_diff_deg": (fb.wcs_metrics.rotation_deg - fa.wcs_metrics.rotation_deg + 180.0)
            % 360.0
            - 180.0,
            "same_shape": fa.shape == fb.shape,
        },
        "instrument": {
            "band_a": fa.band_id,
            "band_b": fb.band_id,
            "array_a": fa.array_id,
            "array_b": fb.array_id,
            "wavelength_a_um": fa.wavelength_um,
            "wavelength_b_um": fb.wavelength_um,
        },
        "calibration": {
            "bunit_a": fa.bunit,
            "bunit_b": fb.bunit,
        },
        "quality": {
            "bad_frac_a": bad_a,
            "bad_frac_b": bad_b,
        },
    }


def _match_pairs(
    dets_a: list[ExtractedSource], dets_b: list[ExtractedSource], max_radius_arcsec: float
) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    na, nb = len(dets_a), len(dets_b)
    if na == 0 or nb == 0:
        return [], list(range(na)), list(range(nb))
    big = 1e6
    ca = SkyCoord(
        ra=[d.ra_deg for d in dets_a] * u.deg, dec=[d.dec_deg for d in dets_a] * u.deg, frame="icrs"
    )
    cb = SkyCoord(
        ra=[d.ra_deg for d in dets_b] * u.deg, dec=[d.dec_deg for d in dets_b] * u.deg, frame="icrs"
    )
    seps = ca[:, None].separation(cb[None, :]).arcsec
    cost = np.where(seps <= max_radius_arcsec, seps, big)
    rows, cols = linear_sum_assignment(cost)
    matched = [(int(r), int(c)) for r, c in zip(rows, cols, strict=True) if cost[r, c] < big * 0.5]
    used_a, used_b = {r for r, _ in matched}, {c for _, c in matched}
    return (
        matched,
        [i for i in range(na) if i not in used_a],
        [j for j in range(nb) if j not in used_b],
    )


def compare_epochs(
    frame_a: Any,
    frame_b: Any,
    *,
    max_pm_arcsec_yr: float = 15.0,
) -> ComparisonResult:
    """Compare epoch A against epoch B with ZOGY D, IVW sum, masked ratio, and changes (§J)."""
    fa = as_frame_data(frame_a)
    fb = as_frame_data(frame_b)
    hdr_diff = _header_diff(fa, fb)

    ext_a = extract_sources(fa)
    ext_b = extract_sources(fb)
    clean_a = [d for d in ext_a.detections if not (d.flags & int(HARD_BAD_MASK))]
    clean_b = [d for d in ext_b.detections if not (d.flags & int(HARD_BAD_MASK))]

    # Photometric scale kappa from >= 10 common unsaturated stars (else 1.0, §J.2).
    dt_yr = abs(fb.epoch.mjd_tdb_mid - fa.epoch.mjd_tdb_mid) / 365.25
    match_rad = max(3.5, min(25.0, max_pm_arcsec_yr * dt_yr + 2.0))
    matched, only_a, only_b = _match_pairs(clean_a, clean_b, max_radius_arcsec=match_rad)

    ratios_kap = [
        clean_a[ia].flux / max(clean_b[ib].flux, 1e-9)
        for ia, ib in matched
        if not ((clean_a[ia].flags | clean_b[ib].flags) & int(DetectionFlag.SATURATED))
        and min(clean_a[ia].snr, clean_b[ib].snr) >= 10.0
    ]
    kappa = float(np.median(ratios_kap)) if len(ratios_kap) >= 10 else 1.0

    # Reproject B onto A grid (§A.4, §J.2) and compute ZOGY D, IVW sum, and masked ratio (§J.3).
    bg_a = estimate_background_and_noise(fa)
    rep_b = reproject_to_grid(fb, fa.wcs, fa.shape)
    b_scaled = kappa * rep_b.image
    vb_scaled = (kappa**2) * np.maximum(rep_b.variance, 1e-12)
    zpair = zogy_difference(
        fb,
        fa,
        flux_scale_ref=1.0 / max(kappa, 1e-6),
        sigma_reg_ref_arcsec=0.05,
        sigma_reg_sci_arcsec=0.05,
        bg_ref=estimate_background_and_noise(fb),
        bg_sci=bg_a,
    )

    va = np.maximum(bg_a.variance, 1e-12)
    inv_va, inv_vb = 1.0 / va, 1.0 / vb_scaled
    sum_img = (bg_a.subtracted * inv_va + b_scaled * inv_vb) / (inv_va + inv_vb)
    snr_a_pix = bg_a.subtracted / np.sqrt(va)
    snr_b_pix = b_scaled / np.sqrt(vb_scaled)
    ratio_img = np.where(
        (snr_a_pix >= 3.0) & (snr_b_pix >= 3.0),
        bg_a.subtracted / np.where(np.abs(b_scaled) > 1e-9, b_scaled, np.nan),
        np.nan,
    )

    entries: list[SourceComparisonEntry] = []
    stamp_tests = list(zpair.stamp_tests)

    def _local_stamp_test(ra_s: float, dec_s: float) -> StampTestResult | None:
        xp, yp = (float(v) for v in zpair.wcs.world_to_pixel_values(ra_s, dec_s))
        ny, nx = zpair.diff_image.shape
        if not (2.0 <= xp <= nx - 3.0 and 2.0 <= yp <= ny - 3.0):
            return None
        return fit_monopole_vs_dipole(
            zpair.diff_image,
            zpair.diff_variance,
            zpair.diff_psf,
            zpair.flux_scale_d,
            xp,
            yp,
            wcs=zpair.wcs,
            quality_mask=zpair.quality_mask,
            snr_corr=float(zpair.score_corr[int(round(yp)), int(round(xp))]),
        )

    for ia, ib in matched:
        da, db = clean_a[ia], clean_b[ib]
        dxi, deta = sky_to_tangent_arcsec(db.ra_deg, db.dec_deg, da.ra_deg, da.dec_deg)
        sep_as = math.hypot(float(dxi), float(deta))
        # Combined statistical centroid variance (using cov_px * pixscale^2 + small floor)
        va_stat = math.hypot(math.sqrt(da.cov_px[0] * fa.wcs_metrics.pixscale_arcsec**2), 0.12)
        vb_stat = math.hypot(math.sqrt(db.cov_px[0] * fb.wcs_metrics.pixscale_arcsec**2), 0.12)
        sig_pos = math.hypot(va_stat, vb_stat)
        off_sig = sep_as / max(sig_pos, 1e-3)

        f_b_sc = kappa * db.flux
        e_a = math.hypot(da.flux_err, 0.03 * abs(da.flux))
        e_b = math.hypot(kappa * db.flux_err, 0.03 * abs(f_b_sc))
        sig_df = math.hypot(e_a, e_b)
        df_sig = abs(f_b_sc - da.flux) / max(sig_df, 1e-9)

        if da.flux > 0.0 and f_b_sc > 0.0:
            dmag = -2.5 * math.log10(f_b_sc / da.flux)
            smag = 1.0857 * math.hypot(e_a / da.flux, e_b / f_b_sc)
            dm_sig = abs(dmag) / max(smag, 1e-6)
        else:
            dmag, smag, dm_sig = None, None, 0.0

        st = _local_stamp_test(0.5 * (da.ra_deg + db.ra_deg), 0.5 * (da.dec_deg + db.dec_deg))
        if st is not None:
            stamp_tests.append(st)

        dip_fav = bool(st and st.dipole_favored)
        mono_fav = bool(st and st.monopole_favored)
        sig_mag_change = max(df_sig, dm_sig) > 5.0 and not (
            (da.flags | db.flags) & int(DetectionFlag.SATURATED)
        )
        if off_sig > 3.0 and dip_fav:
            status: Literal[
                "appeared", "disappeared", "moved", "changed brightness", "unchanged"
            ] = "moved"
        elif sig_mag_change and (off_sig < 3.0 or mono_fav):
            status = "changed brightness"
        else:
            status = "unchanged"

        entries.append(
            SourceComparisonEntry(
                ra_deg=0.5 * (da.ra_deg + db.ra_deg),
                dec_deg=0.5 * (da.dec_deg + db.dec_deg),
                status=status,
                flux_a=da.flux,
                flux_a_err=e_a,
                flux_b=f_b_sc,
                flux_b_err=e_b,
                delta_mag=dmag,
                sigma_delta_mag=smag,
                flux_significance_sigma=max(df_sig, dm_sig),
                offset_arcsec=sep_as,
                offset_sigma=off_sig,
                flags_a=da.flags,
                flags_b=db.flags,
                dipole_favored=dip_fav,
                monopole_favored=mono_fav,
            )
        )

    def _add_orphan(det: ExtractedSource, other_fr: Any, *, is_a: bool) -> None:
        o_f, o_e, o_snr, o_flg = forced_photometry_at_sky(other_fr, det.ra_deg, det.dec_deg)
        active = det.snr >= 5.0 and o_snr < 3.0
        st_o: Literal["appeared", "disappeared", "moved", "changed brightness", "unchanged"] = (
            ("disappeared" if is_a else "appeared") if active else "unchanged"
        )
        entries.append(
            SourceComparisonEntry(
                ra_deg=det.ra_deg,
                dec_deg=det.dec_deg,
                status=st_o,
                flux_a=det.flux if is_a else o_f,
                flux_a_err=det.flux_err if is_a else o_e,
                flux_b=kappa * (o_f if is_a else det.flux),
                flux_b_err=kappa * (o_e if is_a else det.flux_err),
                delta_mag=None,
                sigma_delta_mag=None,
                flux_significance_sigma=det.snr,
                offset_arcsec=0.0,
                offset_sigma=0.0,
                flags_a=det.flags if is_a else o_flg,
                flags_b=o_flg if is_a else det.flags,
            )
        )

    for ia in only_a:
        _add_orphan(clean_a[ia], fb, is_a=True)
    for ib in only_b:
        _add_orphan(clean_b[ib], fa, is_a=False)

    return ComparisonResult(
        header_diff=hdr_diff,
        flux_scale_kappa=kappa,
        kappa_n_stars=len(ratios_kap),
        diff_image=zpair.diff_image,
        diff_variance=zpair.diff_variance,
        score_corr=zpair.score_corr,
        sum_image=sum_img,
        ratio_image=ratio_img,
        sources=entries,
        stamp_tests=stamp_tests,
    )


__all__ = [
    "ComparisonResult",
    "SourceComparisonEntry",
    "compare_epochs",
]
