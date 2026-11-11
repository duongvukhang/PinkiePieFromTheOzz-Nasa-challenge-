"""FITS inspection, header/WCS parsing, and multi-epoch compatibility analysis."""

from __future__ import annotations

import io
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
from astropy.io import fits
from astropy.time import Time

from sphx_core.timeutil import ParsedEpoch, parse_fits_epoch
from sphx_core.wcs import (
    WCSMetrics,
    box_footprint,
    extract_wcs_metrics,
    spherical_polygon_area_sr,
    spherical_polygon_overlap,
    wrap_angle_180,
)

_IMAGE_NAMES = ("IMAGE", "SCI", "PRIMARY", "FLUX")
_VARIANCE_NAMES = ("VARIANCE", "VAR", "ERR", "ERROR", "IVAR", "WEIGHT", "RMS")
_FLAGS_NAMES = ("FLAGS", "FLAG", "MASK", "DQ", "BPM")
_ARRAY_RE = re.compile(r"(?:arr|array|d|det)?([1-6])\b", re.IGNORECASE)


@dataclass(frozen=True)
class HduInfo:
    """Summary of one FITS HDU (name, type, shape, BUNIT)."""

    index: int
    name: str
    hdu_type: str
    shape: list[int]
    bunit: str | None = None


@dataclass(frozen=True)
class FitsInspection:
    """Parsed structure, celestial WCS, epoch, band, and quality inventory of a FITS file."""

    hdus: list[HduInfo]
    science_hdu_index: int | None
    science_hdu_name: str | None
    wcs: WCSMetrics | None
    epoch: ParsedEpoch | None
    band_label: str | None
    array_id: int | None
    wavelength_um: tuple[float, float] | None
    bunit: str | None
    has_variance: bool
    has_flags: bool
    finite_fraction: float | None
    size_bytes: int
    header_summary: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CompatibilitySummary:
    """Multi-epoch FITS compatibility and astrometric/photometric accuracy impact report."""

    valid: bool
    status: Literal["compatible", "partial", "incompatible"]
    overlap_pct: float | None
    upload_covered_pct: float | None
    pixscale_ratio: float | None
    delta_rotation_deg: float | None
    parity_match: bool | None
    delta_t_days: float | None
    band_match: bool | None
    reprojection_needed: bool
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    accuracy_notes: list[str] = field(default_factory=list)


def _header_str(headers: Sequence[fits.Header], keys: tuple[str, ...]) -> str | None:
    for hdr in headers:
        for key in keys:
            val = hdr.get(key)
            if val is not None and str(val).strip():
                return str(val).strip()
    return None


def _header_float(headers: Sequence[fits.Header], keys: tuple[str, ...]) -> float | None:
    for hdr in headers:
        for key in keys:
            val = hdr.get(key)
            if val is None or isinstance(val, bool):
                continue
            try:
                num = float(val)
            except (TypeError, ValueError):
                continue
            if math.isfinite(num):
                return num
    return None


def _header_wavelength_um(headers: Sequence[fits.Header], keys: tuple[str, ...]) -> float | None:
    for hdr in headers:
        for key in keys:
            num = _header_float([hdr], (key,))
            if num is None or num <= 0.0:
                continue
            unit_hint = f"{hdr.get('CUNIT3', '')} {hdr.comments[key] if key in hdr else ''}".lower()
            if any(t in unit_hint for t in ("angstrom", "ang", " aa")) or num > 1000.0:
                return num * 1e-4
            if "nm" in unit_hint or "nanom" in unit_hint or num > 50.0:
                return num * 1e-3
            if any(t in unit_hint for t in ("meter", "metre")) or num < 1e-3:
                return num * 1e6
            return num
    return None


def _parse_array_id(raw: Any) -> int | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)) and 1 <= int(raw) <= 6:
        return int(raw)
    match = _ARRAY_RE.search(str(raw).strip())
    return int(match.group(1)) if match else None


def _failed_inspection(size_bytes: int, warnings: list[str], errors: list[str]) -> FitsInspection:
    return FitsInspection(
        hdus=[],
        science_hdu_index=None,
        science_hdu_name=None,
        wcs=None,
        epoch=None,
        band_label=None,
        array_id=None,
        wavelength_um=None,
        bunit=None,
        has_variance=False,
        has_flags=False,
        finite_fraction=None,
        size_bytes=size_bytes,
        header_summary={},
        warnings=warnings,
        errors=errors,
    )


def inspect_fits(
    source: Path | str | bytes,
    *,
    max_bytes: int | None = None,
    user_epoch: Time | str | float | int | None = None,
) -> FitsInspection:
    """Safely open a FITS file/bytes and extract HDU, WCS, epoch, band, and quality metadata."""
    warnings: list[str] = []
    errors: list[str] = []

    if isinstance(source, (Path, str)):
        path = Path(source)
        if not path.is_file():
            raise FileNotFoundError(f"FITS path not found: {path}")
        size_bytes = path.stat().st_size
        open_target: Any = path
        memmap = True
    else:
        size_bytes = len(source)
        open_target = io.BytesIO(source)
        memmap = False

    if max_bytes is not None and size_bytes > max_bytes:
        errors.append(
            f"FITS size ({size_bytes} B) exceeds limit ({max_bytes} B); rejected before parsing."
        )
        return _failed_inspection(size_bytes, warnings, errors)

    if size_bytes == 0:
        errors.append("Empty FITS payload (0 bytes).")
        return _failed_inspection(0, warnings, errors)

    try:
        with fits.open(open_target, memmap=memmap, lazy_load_hdus=True) as hdul:
            hdus = [
                HduInfo(
                    index=idx,
                    name=(hdu.name or "PRIMARY").upper(),
                    hdu_type=type(hdu).__name__,
                    shape=[int(x) for x in getattr(hdu, "shape", ()) or ()],
                    bunit=str(hdu.header["BUNIT"]) if hdu.header.get("BUNIT") else None,
                )
                for idx, hdu in enumerate(hdul)
            ]

            sci_idx: int | None = None
            wcs_metrics: WCSMetrics | None = None
            # Prefer named science HDUs, then any 2D+ HDU with celestial WCS, then any 2D+ HDU.
            candidates = sorted(
                range(len(hdul)),
                key=lambda i: (
                    0 if (hdul[i].name or "PRIMARY").upper() in _IMAGE_NAMES else 1,
                    i,
                ),
            )
            for idx in candidates:
                hdr = hdul[idx].header
                if int(hdr.get("NAXIS", 0) or 0) < 2:
                    continue
                shape_raw: tuple[int, ...] = tuple(getattr(hdul[idx], "shape", None) or ())
                shape_2d = (int(shape_raw[-2]), int(shape_raw[-1])) if len(shape_raw) >= 2 else None
                parsed_wcs = extract_wcs_metrics(hdr, shape=shape_2d)
                if parsed_wcs is not None:
                    sci_idx = idx
                    wcs_metrics = parsed_wcs
                    break
                if sci_idx is None:
                    sci_idx = idx

            if sci_idx is None:
                errors.append("No 2D image HDU found in FITS file.")
            if wcs_metrics is None:
                errors.append(
                    "No celestial WCS found: cannot map pixels to ICRS sky coordinates; "
                    "astrometry and multi-epoch alignment require a valid celestial WCS."
                )

            sci_hdu = hdul[sci_idx] if sci_idx is not None else hdul[0]
            headers = [sci_hdu.header] + ([hdul[0].header] if sci_idx not in (None, 0) else [])

            epoch, epoch_warns = parse_fits_epoch(headers, user_epoch=user_epoch)
            warnings.extend(epoch_warns)
            if epoch is None:
                errors.append(
                    "Observation epoch missing (no DATE-OBS, MJD-OBS, DATE-BEG, or DATE-AVG): "
                    "supply epoch manually (will be flagged USER_EPOCH)."
                )
            elif epoch.user_supplied:
                warnings.append(
                    "Epoch supplied manually by user (flagged USER_EPOCH); "
                    "verify timing precision before computing proper motion."
                )

            names_upper = {h.name for h in hdus}
            has_variance = any(v in names_upper for v in _VARIANCE_NAMES)
            has_flags = any(f in names_upper for f in _FLAGS_NAMES)

            band_label = _header_str(headers, ("BAND", "FILTER", "FILTNAME", "CHANNEL", "BANDID"))
            array_id = _parse_array_id(_header_str(headers, ("ARRAY", "DETECTOR", "DET_ID")))
            if band_label is None and array_id is not None:
                band_label = f"arr{array_id}"

            lam_min = _header_wavelength_um(headers, ("LAM_MIN", "WAVEMIN", "EM_MIN"))
            lam_max = _header_wavelength_um(headers, ("LAM_MAX", "WAVEMAX", "EM_MAX"))
            lam_c = _header_wavelength_um(headers, ("WAVELEN", "WAVE", "LAM_EFF"))
            if lam_min is not None and lam_max is not None:
                wavelength_um: tuple[float, float] | None = (lam_min, lam_max)
            elif lam_c is not None:
                wavelength_um = (lam_c, lam_c)
            else:
                wavelength_um = None

            finite_fraction: float | None = None
            if sci_idx is not None and getattr(sci_hdu, "data", None) is not None:
                arr = np.asarray(sci_hdu.data)
                if arr.size > 0:
                    finite_fraction = float(np.mean(np.isfinite(arr)))
                    if finite_fraction == 0.0:
                        errors.append("Science image HDU contains no finite pixel values.")
                    elif finite_fraction < 0.5:
                        warnings.append(
                            f"Only {finite_fraction * 100.0:.1f}% of science pixels are finite."
                        )

            bunit = _header_str([sci_hdu.header, hdul[0].header], ("BUNIT",))
            header_summary: dict[str, Any] = {
                "hdus": [
                    {
                        "index": h.index,
                        "name": h.name,
                        "type": h.hdu_type,
                        "shape": h.shape,
                        "bunit": h.bunit,
                    }
                    for h in hdus
                ],
                "mission": _header_str(headers, ("TELESCOP", "MISSION")),
                "instrument": _header_str(headers, ("INSTRUME", "INSTRUMENT")),
                "date_obs": _header_str(headers, ("DATE-OBS", "DATE-BEG", "DATE-AVG")),
                "mjd_obs": _header_float(headers, ("MJD-OBS", "MJD-BEG", "MJD-AVG")),
                "exptime": epoch.exptime_s if epoch else None,
                "timesys": epoch.timesys if epoch else None,
                "band": band_label,
                "bunit": bunit,
            }

            return FitsInspection(
                hdus=hdus,
                science_hdu_index=sci_idx,
                science_hdu_name=(sci_hdu.name or "PRIMARY").upper()
                if sci_idx is not None
                else None,
                wcs=wcs_metrics,
                epoch=epoch,
                band_label=band_label,
                array_id=array_id,
                wavelength_um=wavelength_um,
                bunit=bunit,
                has_variance=has_variance,
                has_flags=has_flags,
                finite_fraction=finite_fraction,
                size_bytes=size_bytes,
                header_summary=header_summary,
                warnings=warnings,
                errors=errors,
            )
    except Exception as exc:
        errors.append(f"Invalid or unreadable FITS payload: {exc}")
        return _failed_inspection(size_bytes, warnings, errors)


def _ref_from_inspection(ref: FitsInspection) -> dict[str, Any]:
    return {
        "footprint": ref.wcs.footprint if ref.wcs else None,
        "pixscale_arcsec": ref.wcs.pixscale_arcsec if ref.wcs else None,
        "rotation_deg": ref.wcs.rotation_deg if ref.wcs else None,
        "parity": ref.wcs.parity if ref.wcs else None,
        "mjd_tdb_mid": ref.epoch.mjd_tdb_mid if ref.epoch else None,
        "band_label": ref.band_label,
        "wavelength_um": ref.wavelength_um,
    }


def _ref_from_mapping(ref: Mapping[str, Any]) -> dict[str, Any]:
    fp = ref.get("footprint")
    if not fp and all(k in ref for k in ("ra_deg", "dec_deg", "fov_arcmin")):
        fp = box_footprint(float(ref["ra_deg"]), float(ref["dec_deg"]), float(ref["fov_arcmin"]))
    return {
        "footprint": list(fp) if fp else None,
        "pixscale_arcsec": ref.get("pixscale_arcsec"),
        "rotation_deg": ref.get("rotation_deg"),
        "parity": ref.get("parity"),
        "mjd_tdb_mid": ref.get("mjd_tdb_mid"),
        "band_label": ref.get("band_label"),
        "wavelength_um": ref.get("wavelength_um"),
    }


def _bands_compatible(
    band_a: str | None,
    lam_a: tuple[float, float] | None,
    band_b: str | None,
    lam_b: tuple[float, float] | None,
) -> bool | None:
    if lam_a is not None and lam_b is not None:
        return max(lam_a[0], lam_b[0]) <= min(lam_a[1], lam_b[1])
    if band_a and band_b:
        return band_a.strip().lower() == band_b.strip().lower()
    return None


def evaluate_compatibility(
    target: FitsInspection,
    reference: FitsInspection
    | Mapping[str, Any]
    | Sequence[FitsInspection | Mapping[str, Any]]
    | None = None,
) -> CompatibilitySummary:
    """Compute footprint overlap, scale/rotation/band/epoch compatibility, and accuracy impact."""
    warnings = list(target.warnings)
    errors = list(target.errors)
    accuracy_notes: list[str] = []

    if target.wcs is not None:
        sx, sy = target.wcs.pixscale_x_arcsec, target.wcs.pixscale_y_arcsec
        if sx > 0.0 and abs(sy / sx - 1.0) > 0.01:
            warnings.append(
                f'Anisotropic pixel scale ({sx:.3f}" x {sy:.3f}"/px); '
                "reprojection onto an isotropic grid needed for display/differencing."
            )
        if target.wcs.pixscale_arcsec > 3.0:
            accuracy_notes.append(
                f'Undersampled pixels ({target.wcs.pixscale_arcsec:.2f}"/px): pixel-phase bias '
                'adds ~0.05-0.10" astrometric floor unless empirical phase correction is applied.'
            )
        fov_est_deg = math.sqrt(max(target.wcs.area_arcmin2, 0.0)) / 60.0
        if fov_est_deg > 0.25 and not target.wcs.has_sip:
            accuracy_notes.append(
                f"Wide field ({fov_est_deg:.2f} deg) without SIP distortion keywords: "
                'peripheral astrometry may incur >0.2" geometric error before Gaia TAN-SIP fit.'
            )

    if not target.has_variance:
        warnings.append("No VARIANCE/ERR extension found.")
        accuracy_notes.append(
            "Missing VARIANCE map: inverse-variance weighting falls back to background-estimated "
            "uniform noise, making faint-source flux and centroid uncertainties approximate."
        )
    if not target.has_flags:
        warnings.append("No FLAGS/MASK extension found; bad/saturated pixel masking is limited.")
    if target.epoch is not None and target.epoch.user_supplied:
        accuracy_notes.append(
            "User-supplied epoch (USER_EPOCH): any epoch timing error dt propagates into "
            "proper-motion error as delta_mu ~ mu * dt / baseline."
        )

    overlap_pct: float | None = None
    upload_covered_pct: float | None = None
    pixscale_ratio: float | None = None
    delta_rot: float | None = None
    parity_match: bool | None = None
    delta_t_days: float | None = None
    band_match: bool | None = None
    reprojection_needed = False

    refs: list[dict[str, Any]] = []
    if isinstance(reference, FitsInspection):
        refs = [_ref_from_inspection(reference)]
    elif isinstance(reference, Sequence) and not isinstance(reference, (str, bytes)):
        refs = [
            _ref_from_inspection(item)
            if isinstance(item, FitsInspection)
            else _ref_from_mapping(item)
            for item in reference
            if isinstance(item, (FitsInspection, Mapping))
        ]
    elif isinstance(reference, Mapping):
        refs = [_ref_from_mapping(reference)]

    # Multi-frame / reference compatibility checks per SCIENCE.md §A.1
    if refs and target.wcs is not None:
        all_polys = [r["footprint"] for r in refs if r["footprint"]]
        if all_polys:
            inter_sr, frac_ref = spherical_polygon_overlap(
                all_polys[0], all_polys[1:] + [target.wcs.footprint]
            )
            overlap_pct = round(frac_ref * 100.0, 2)
            up_sr = spherical_polygon_area_sr(target.wcs.footprint)
            upload_covered_pct = round(min(1.0, inter_sr / up_sr) * 100.0, 2) if up_sr > 0 else 0.0
            if overlap_pct < 20.0:
                errors.append(
                    f"Incompatible sky footprint overlap ({overlap_pct:.1f}% < 20% minimum): "
                    "insufficient shared sky for alignment, differencing, or tracking."
                )
            elif overlap_pct < 80.0:
                warnings.append(
                    f"Partial sky footprint overlap ({overlap_pct:.1f}%): analysis restricted to "
                    "intersection region; objects outside overlap cannot be tracked across epochs."
                )
                accuracy_notes.append(
                    f"Partial overlap ({overlap_pct:.1f}%): fewer reference stars in common area "
                    "increase registration uncertainty (sigma_reg ~ RMS / sqrt(N_ref))."
                )

        scale_ratios = [
            target.wcs.pixscale_arcsec / float(r["pixscale_arcsec"])
            for r in refs
            if r.get("pixscale_arcsec") is not None and float(r["pixscale_arcsec"]) > 0.0
        ]
        if scale_ratios:
            pixscale_ratio = max(scale_ratios, key=lambda x: abs(x - 1.0))
            if abs(pixscale_ratio - 1.0) > 0.01:
                reprojection_needed = True
                warnings.append(
                    f'Pixel scale mismatch ({target.wcs.pixscale_arcsec:.3f}" vs '
                    f"ref ratio {pixscale_ratio:.3f}); reprojection required."
                )
                if pixscale_ratio > 2.0 or pixscale_ratio < 0.5:
                    accuracy_notes.append(
                        "Pixel scale differs by >2x: PSF matching or downsampling needed for "
                        "differencing; blended stars are harder to separate in the coarser frame."
                    )

        rot_deltas = [
            wrap_angle_180(target.wcs.rotation_deg - float(r["rotation_deg"]))
            for r in refs
            if r.get("rotation_deg") is not None
        ]
        if rot_deltas:
            delta_rot = max(rot_deltas, key=abs)
            if abs(delta_rot) > 0.1:
                reprojection_needed = True
                warnings.append(
                    f"Field rotation differs by {delta_rot:+.2f} deg (>0.1 deg); "
                    "reprojection required."
                )

        parity_checks = [
            int(r["parity"]) == target.wcs.parity for r in refs if r.get("parity") is not None
        ]
        if parity_checks:
            parity_match = all(parity_checks)
            if not parity_match:
                reprojection_needed = True
                warnings.append(
                    "Image axis parity (mirror flip) differs from reference; reprojection required."
                )

        if reprojection_needed:
            accuracy_notes.append(
                "Reprojection required for blink/differencing/stacking: adds correlated pixel "
                "noise (F_corr ~ 1.1-1.5x) and ~5-15% aperture S/N loss; astrometry and PSF "
                "photometry remain measured on native frames to avoid resampling bias."
            )

        # Band compatibility
        band_checks = [
            _bands_compatible(
                target.band_label, target.wavelength_um, r.get("band_label"), r.get("wavelength_um")
            )
            for r in refs
        ]
        known_band_checks = [b for b in band_checks if b is not None]
        if known_band_checks:
            band_match = all(known_band_checks)
            if not band_match:
                ref_bands = ", ".join(str(r.get("band_label") or "unknown") for r in refs)
                warnings.append(
                    f"Band/wavelength mismatch ({target.band_label or 'unknown'} vs {ref_bands})."
                )
                accuracy_notes.append(
                    "Different bands compared: colour-dependent flux changes are expected; "
                    "do not interpret flux differences as intrinsic source variability."
                )

        # Epoch separation & proper motion lever arm
        ref_mjds = [float(r["mjd_tdb_mid"]) for r in refs if r.get("mjd_tdb_mid") is not None]
        if target.epoch is not None and ref_mjds:
            diffs = [abs(target.epoch.mjd_tdb_mid - m) for m in ref_mjds]
            delta_t_days = max(diffs)
            if delta_t_days < 1e-4:
                warnings.append(
                    "Same-epoch frame (|dt| < 10 s vs reference): lacks proper-motion baseline."
                )
                accuracy_notes.append(
                    "Zero time baseline vs reference: proper motion unconstrained "
                    "(sigma_mu -> inf); useful for stacking or SED coverage only."
                )
            elif delta_t_days < 30.0:
                accuracy_notes.append(
                    f"Short time baseline ({delta_t_days:.1f} d): proper-motion precision scales "
                    "as sigma_pos / dt_yr; only fast movers (SSOs) show significant linear motion."
                )

    status: Literal["compatible", "partial", "incompatible"]
    if errors or (overlap_pct is not None and overlap_pct < 20.0):
        status = "incompatible"
    elif overlap_pct is not None and overlap_pct < 80.0:
        status = "partial"
    else:
        status = "compatible"

    return CompatibilitySummary(
        valid=not errors,
        status=status,
        overlap_pct=overlap_pct,
        upload_covered_pct=upload_covered_pct,
        pixscale_ratio=pixscale_ratio,
        delta_rotation_deg=delta_rot,
        parity_match=parity_match,
        delta_t_days=delta_t_days,
        band_match=band_match,
        reprojection_needed=reprojection_needed,
        warnings=warnings,
        errors=errors,
        accuracy_notes=accuracy_notes,
    )


__all__ = [
    "CompatibilitySummary",
    "FitsInspection",
    "HduInfo",
    "evaluate_compatibility",
    "inspect_fits",
]
