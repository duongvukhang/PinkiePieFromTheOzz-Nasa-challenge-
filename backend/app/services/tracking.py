"""Shared tracking, epoch-resolution, forced-PSF SED, and lightcurve helpers."""

from __future__ import annotations

import contextlib
import math
from collections.abc import Mapping, Sequence
from datetime import UTC
from typing import TYPE_CHECKING, Any
from uuid import UUID

import astropy.units as u
import numpy as np
from astropy.time import Time

from app.schemas.objects import (
    LightcurvePoint,
    LightcurveResponse,
    SpectrumResponse,
)
from sphx_core.calib import (
    BAND_GROUPS,
    FrameData,
    band_group_for_wavelength,
    evaluate_lvf_wavelength,
    load_frame,
)
from sphx_core.motion import (
    forced_photometry_at_sky,
    measure_variability_period,
    weighted_flux_variability,
)
from sphx_core.wcs import tangent_arcsec_to_sky

if TYPE_CHECKING:
    from app.services.jobs import JobStore
    from app.services.storage import StorageBackend

_BAND_MID_UM: dict[str, float] = {k: 0.5 * (lo + hi) for k, (lo, hi) in BAND_GROUPS.items()}


def flux_to_ujy_and_mag(
    flux: float, flux_err: float, frame: FrameData
) -> tuple[float, float, float | None]:
    """Convert native frame flux/error to `(flux_ujy, err_ujy, ab_mag)` using `BUNIT`."""
    bunit = (frame.bunit or "").strip().lower()
    pix_area_sr = (frame.wcs_metrics.pixscale_arcsec * math.pi / (180.0 * 3600.0)) ** 2
    scale = 1.0
    if not bunit:
        scale = 1e12 * pix_area_sr
    else:
        for token, factor in (
            ("mjy/sr", 1e12 * pix_area_sr),
            ("jy/sr", 1e6 * pix_area_sr),
            ("mjy", 1e3),
            ("ujy", 1.0),
            ("jy", 1e6),
        ):
            if token in bunit:
                scale = factor
                break
    flux_ujy, err_ujy = float(flux * scale), float(flux_err * scale)
    mag_ab: float | None = None
    if flux_ujy > 0.0 and math.isfinite(flux_ujy):
        mag_val = float((flux_ujy * u.uJy).to_value(u.ABmag))
        if math.isfinite(mag_val):
            mag_ab = mag_val
    return flux_ujy, err_ujy, mag_ab


def in_sky_box(
    ra: float, dec: float, ra_min: float, ra_max: float, dec_min: float, dec_max: float
) -> bool:
    """Return True if `(ra, dec)` [deg ICRS] falls inside box (handling RA 0/360 wrap)."""
    if not (dec_min <= dec <= dec_max):
        return False
    return (ra_min <= ra <= ra_max) if ra_min <= ra_max else (ra >= ra_min or ra <= ra_max)


def resolve_epoch_observation(
    observations: Sequence[Mapping[str, Any]], epoch: str
) -> dict[str, Any] | None:
    """Match an epoch token (`S1`, `epoch_0`, UUID, `date_obs_utc`) to an observation record."""
    token = epoch.strip()
    low = token.lower()
    for idx, obs in enumerate(observations):
        if str(obs["id"]) == token or str(obs.get("fits_file_id", "")) == token:
            return dict(obs)
        lbl = str(obs.get("epoch_label") or f"S{idx + 1}").lower()
        if low == lbl or low == f"epoch_{idx}" or low == str(idx):
            return dict(obs)
        if obs.get("date_obs_utc") and str(obs["date_obs_utc"]) == token:
            return dict(obs)
    return None


def predict_sky_at_mjd(bundle: Mapping[str, Any], mjd_tdb: float) -> tuple[float, float]:
    """Predict `(ra_deg, dec_deg)` at `mjd_tdb` from preferred motion fit or reference coords."""
    fits_list = bundle.get("motion_fits") or []
    pref = next(
        (f for f in fits_list if f.get("is_preferred")), fits_list[0] if fits_list else None
    )
    if pref is None:
        ra = bundle.get("ref_ra")
        dec = bundle.get("ref_dec")
        if ra is not None and dec is not None:
            return float(ra), float(dec)
        meas = bundle.get("measurements") or []
        if meas and meas[0].ra is not None and meas[0].dec is not None:
            return float(meas[0].ra), float(meas[0].dec)
        return 0.0, 0.0

    params = pref.get("params") or {}
    ra0 = float(params.get("ra_deg") or bundle.get("ref_ra") or 0.0)
    dec0 = float(params.get("dec_deg") or bundle.get("ref_dec") or 0.0)
    dt_yr = (mjd_tdb - float(pref["ref_epoch_mjd_tdb"])) / 365.25
    dxi = (float(params.get("pmra_masyr", 0.0)) * 1e-3) * dt_yr
    deta = (float(params.get("pmdec_masyr", 0.0)) * 1e-3) * dt_yr
    ra_p, dec_p = tangent_arcsec_to_sky(dxi, deta, ra0, dec0)
    return float(ra_p) % 360.0, float(dec_p)


def compute_object_spectrum(
    bundle: Mapping[str, Any],
    *,
    store: JobStore,
    storage: StorageBackend,
    observation_id: UUID | None = None,
) -> SpectrumResponse:
    """Compute or retrieve on-the-fly forced-PSF SED (`um`, `uJy`, `MJD TDB`)."""
    obj_id: UUID = bundle["id"]
    cached = store.get_cached_spectrum(obj_id, observation_id)
    if cached is not None:
        return cached

    observations: list[dict[str, Any]] = list(bundle.get("observations") or [])
    if observation_id is not None:
        observations = [o for o in observations if UUID(str(o["id"])) == observation_id]

    pts: list[tuple[float, float, float, float, int]] = []
    for obs in observations:
        fid_raw = obs.get("fits_file_id")
        if not fid_raw:
            continue
        frec = store.get_fits_file(UUID(str(fid_raw)))
        if frec is None:
            continue
        raw = storage.get_bytes(str(frec["storage_path"]), verify_checksum=False)
        frame = load_frame(raw, user_epoch=frec.get("mjd_tdb_mid"))
        mjd = float(frame.epoch.mjd_tdb_mid)
        ra_t, dec_t = predict_sky_at_mjd(bundle, mjd)
        x_px, y_px = (float(v) for v in frame.wcs.world_to_pixel_values(ra_t, dec_t))
        ny, nx = frame.shape
        if not (-0.5 <= x_px <= nx - 0.5 and -0.5 <= y_px <= ny - 0.5):
            continue
        flux, ferr, _, fflags = forced_photometry_at_sky(frame, ra_t, dec_t)
        flux_ujy, err_ujy, _ = flux_to_ujy_and_mag(flux, ferr, frame)
        lam_um, _ = evaluate_lvf_wavelength(frame, x_px, y_px)
        pts.append((lam_um, flux_ujy, err_ujy, mjd, int(fflags)))

    if not pts:
        for m in bundle.get("measurements") or []:
            if m.flux_ujy is None:
                continue
            lam_um = _BAND_MID_UM.get(m.band_group or "", 2.5)
            pts.append(
                (
                    lam_um,
                    float(m.flux_ujy),
                    float(m.flux_err_ujy or max(abs(m.flux_ujy) * 0.05, 1e-3)),
                    float(m.mjd_tdb),
                    int(m.flags),
                )
            )

    pts.sort(key=lambda item: (item[0], item[3]))
    spec = SpectrumResponse(
        object_id=obj_id,
        observation_id=observation_id,
        wavelength_um=[p[0] for p in pts],
        flux_ujy=[p[1] for p in pts],
        flux_err_ujy=[p[2] for p in pts],
        mjd_tdb=[p[3] for p in pts],
        flags=[p[4] for p in pts],
        method="forced_psf",
    )
    store.save_spectrum(spec)
    return spec


def build_object_lightcurve(bundle: Mapping[str, Any]) -> LightcurveResponse:
    """Assemble time-ordered photometry points and variability indices for an object."""
    obj_id: UUID = bundle["id"]
    meas = sorted(bundle.get("measurements") or [], key=lambda m: float(m.mjd_tdb))
    points: list[LightcurvePoint] = []
    valid_flux: list[float] = []
    valid_err: list[float] = []

    for m in meas:
        utc_dt = Time(float(m.mjd_tdb), format="mjd", scale="tdb").utc.datetime.replace(tzinfo=UTC)
        points.append(
            LightcurvePoint(
                mjd_tdb=float(m.mjd_tdb),
                utc=utc_dt,
                band_group=m.band_group or "ALL",
                flux_ujy=m.flux_ujy,
                flux_err_ujy=m.flux_err_ujy,
                is_upper_limit=bool(m.is_upper_limit),
                flags=int(m.flags),
            )
        )
        if (
            not m.is_upper_limit
            and m.flux_ujy is not None
            and math.isfinite(m.flux_ujy)
            and m.flux_err_ujy is not None
            and m.flux_err_ujy > 0.0
        ):
            valid_flux.append(float(m.flux_ujy))
            valid_err.append(float(m.flux_err_ujy))

    indices: dict[str, float] = {"n_epochs": float(len(points))}
    if len(valid_flux) >= 2:
        chi2_v, chi2_nu, amp_mag = weighted_flux_variability(
            np.asarray(valid_flux, dtype=np.float64),
            np.asarray(valid_err, dtype=np.float64),
        )
        indices.update(
            {
                "chi2_var": round(float(chi2_v), 4),
                "chi2_var_nu": round(float(chi2_nu), 4),
                "amplitude_mag": round(float(amp_mag), 4),
            }
        )

    if len(points) >= 5 and (points[-1].mjd_tdb - points[0].mjd_tdb) > 1.0:

        class _DetProxy:
            def __init__(self, pt: LightcurvePoint) -> None:
                self.mjd_tdb = pt.mjd_tdb
                self.flux = pt.flux_ujy
                self.flux_err = pt.flux_err_ujy
                self.flags = pt.flags
                self.band_id = pt.band_group

        class _TrackProxy:
            def __init__(self, pts: Sequence[LightcurvePoint]) -> None:
                self.detections = [_DetProxy(p) for p in pts]

        with contextlib.suppress(Exception):
            per = measure_variability_period(_TrackProxy(points))
            if math.isfinite(per.period_days):
                indices["period_days"] = round(float(per.period_days), 4)
            if math.isfinite(per.fap):
                indices["fap"] = float(per.fap)
            if math.isfinite(per.power_max):
                indices["power_max"] = round(float(per.power_max), 4)
            if per.stetson_j is not None and math.isfinite(per.stetson_j):
                indices["stetson_j"] = round(float(per.stetson_j), 4)

    return LightcurveResponse(object_id=obj_id, points=points, variability_indices=indices)
