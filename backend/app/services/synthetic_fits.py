"""Deterministic synthetic SPHEREx FITS cutout generator (<= 64x64, seeded)."""

from __future__ import annotations

import io
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord, get_body_barycentric
from astropy.io import fits
from astropy.time import Time
from astropy.wcs import WCS, Sip
from numpy.typing import NDArray
from scipy.special import erf

from app.db.flags import DetectionFlag

# Nominal SPHEREx 6-band LVF detector array properties (array_id, lam_min_um, lam_max_um).
BAND_PROFILES: dict[str, tuple[int, float, float]] = {
    "B1": (1, 0.75, 1.11),
    "B2": (2, 1.11, 1.64),
    "B3": (3, 1.64, 2.42),
    "B4": (4, 2.42, 3.82),
    "B5": (5, 3.82, 4.42),
    "B6": (6, 4.42, 5.00),
}
_ARCSEC_PER_RAD = 206264.80624709636


@dataclass(frozen=True)
class InjectedSource:
    """Ground-truth source specification injected into synthetic multi-epoch cutouts."""

    source_id: str
    ra_ref_deg: float
    dec_ref_deg: float
    ref_epoch_mjd: float
    pmra_arcsec_yr: float = 0.0
    pmdec_arcsec_yr: float = 0.0
    parallax_mas: float = 0.0
    base_flux: float = 100.0
    flux_fn: Callable[[float, str], float] | None = None
    kind: str = "mover"
    gaia_id: int | None = None
    companion_offset_arcsec: tuple[float, float] | None = None
    companion_flux_ratio: float = 0.0
    companion_flux_fn: Callable[[float, str], float] | None = None


@dataclass(frozen=True)
class TruthPosition:
    """Exact ground-truth sky/pixel position and photometry of an injected source at one epoch."""

    source_id: str
    ra_deg: float
    dec_deg: float
    x_px: float
    y_px: float
    flux: float
    snr: float
    saturated: bool
    kind: str
    gaia_id: int | None


@dataclass(frozen=True)
class SyntheticFrame:
    """Generated MEF FITS cutout bytes plus ground-truth WCS, epoch, and source inventory."""

    fits_bytes: bytes
    wcs_true: WCS
    wcs_reported: WCS
    mjd_tdb_mid: float
    band_id: str
    array_id: int
    psf_sigma_px: float
    noise_sigma: float
    truth: list[TruthPosition]
    reference_stars: list[dict[str, float | int]]


def sky_position_at_epoch(
    ra_ref_deg: float,
    dec_ref_deg: float,
    ref_epoch_mjd: float,
    target_mjd: float,
    pmra_arcsec_yr: float = 0.0,
    pmdec_arcsec_yr: float = 0.0,
    parallax_mas: float = 0.0,
) -> tuple[float, float]:
    """Propagate ICRS `(ra, dec)` with proper motion (`\"/yr`) and barycentric parallax (mas)."""
    t_ref = Time(ref_epoch_mjd, format="mjd", scale="tdb")
    t_obs = Time(target_mjd, format="mjd", scale="tdb")
    dist = (1000.0 / parallax_mas) * u.pc if parallax_mas > 0.0 else 1e6 * u.pc
    sc = SkyCoord(
        ra=ra_ref_deg * u.deg,
        dec=dec_ref_deg * u.deg,
        distance=dist,
        pm_ra_cosdec=(pmra_arcsec_yr * 1000.0) * (u.mas / u.yr),
        pm_dec=(pmdec_arcsec_yr * 1000.0) * (u.mas / u.yr),
        obstime=t_ref,
        frame="icrs",
    ).apply_space_motion(new_obstime=t_obs)

    ra_rad, dec_rad = math.radians(float(sc.ra.deg)), math.radians(float(sc.dec.deg))
    if parallax_mas <= 0.0:
        return float(sc.ra.deg) % 360.0, float(sc.dec.deg)

    bary_au = get_body_barycentric("earth", t_obs).xyz.to_value(u.au)
    east = np.array([-math.sin(ra_rad), math.cos(ra_rad), 0.0], dtype=np.float64)
    north = np.array(
        [
            -math.sin(dec_rad) * math.cos(ra_rad),
            -math.sin(dec_rad) * math.sin(ra_rad),
            math.cos(dec_rad),
        ],
        dtype=np.float64,
    )
    plx_rad = (parallax_mas * 1e-3) / _ARCSEC_PER_RAD
    cos_dec = max(math.cos(dec_rad), 1e-8)
    ra_out = math.degrees(ra_rad - plx_rad * float(np.dot(bary_au, east)) / cos_dec) % 360.0
    dec_out = math.degrees(dec_rad - plx_rad * float(np.dot(bary_au, north)))
    return ra_out, dec_out


def make_tan_wcs(
    nx: int,
    ny: int,
    ra_center_deg: float,
    dec_center_deg: float,
    *,
    pixscale_arcsec: float = 6.2,
    rot_deg: float = 0.0,
    with_sip: bool = False,
) -> WCS:
    """Create a celestial ICRS TAN (or TAN-SIP) WCS centered on `(nx, ny)` (deg, \"/px)."""
    scale_deg = pixscale_arcsec / 3600.0
    th = math.radians(rot_deg)
    sx, sy = -scale_deg, scale_deg
    w = WCS(naxis=2)
    sfx = "-SIP" if with_sip else ""
    w.wcs.ctype = [f"RA---TAN{sfx}", f"DEC--TAN{sfx}"]
    w.wcs.crval = [ra_center_deg, dec_center_deg]
    w.wcs.crpix = [(nx + 1.0) / 2.0, (ny + 1.0) / 2.0]
    w.wcs.radesys = "ICRS"
    w.wcs.cd = np.array(
        [[sx * math.cos(th), -sx * math.sin(th)], [sy * math.sin(th), sy * math.cos(th)]],
        dtype=np.float64,
    )
    if with_sip:
        a, b = np.zeros((3, 3), dtype=np.float64), np.zeros((3, 3), dtype=np.float64)
        a[2, 0], b[0, 2] = 1.5e-5, -1.5e-5
        w.sip = Sip(a, b, np.zeros((3, 3)), np.zeros((3, 3)), w.wcs.crpix)
    return w


def perturb_wcs(
    wcs_true: WCS,
    *,
    shift_ra_arcsec: float = 0.0,
    shift_dec_arcsec: float = 0.0,
    rot_err_deg: float = 0.0,
) -> WCS:
    """Return a copy of `wcs_true` with a deliberate astrometric registration error."""
    w = wcs_true.deepcopy()
    cos_dec = max(math.cos(math.radians(float(w.wcs.crval[1]))), 1e-8)
    w.wcs.crval = [
        (float(w.wcs.crval[0]) + (shift_ra_arcsec / 3600.0) / cos_dec) % 360.0,
        float(w.wcs.crval[1]) + shift_dec_arcsec / 3600.0,
    ]
    if rot_err_deg != 0.0:
        th = math.radians(rot_err_deg)
        rot = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
        w.wcs.cd = rot @ np.asarray(w.wcs.cd, dtype=np.float64)
    return w


def add_pixel_integrated_psf(
    image: NDArray[np.float64], xc: float, yc: float, total_flux: float, sigma_px: float
) -> None:
    """Inject a pixel-integrated 2D Gaussian PSF of known total flux at `(xc, yc)` (0-based px)."""
    if total_flux == 0.0:
        return
    ny, nx = image.shape
    half = max(4, int(math.ceil(5.0 * sigma_px)))
    x0, x1 = max(0, int(math.floor(xc)) - half), min(nx, int(math.ceil(xc)) + half + 1)
    y0, y1 = max(0, int(math.floor(yc)) - half), min(ny, int(math.ceil(yc)) + half + 1)
    if x0 >= x1 or y0 >= y1:
        return
    inv = 1.0 / (math.sqrt(2.0) * sigma_px)
    xs, ys = np.arange(x0, x1, dtype=np.float64), np.arange(y0, y1, dtype=np.float64)
    ix = 0.5 * (erf((xs + 0.5 - xc) * inv) - erf((xs - 0.5 - xc) * inv))
    iy = 0.5 * (erf((ys + 0.5 - yc) * inv) - erf((ys - 0.5 - yc) * inv))
    image[y0:y1, x0:x1] += total_flux * np.outer(iy, ix)


def generate_synthetic_cutout(
    *,
    seed: int,
    sources: Sequence[InjectedSource],
    ra_center_deg: float,
    dec_center_deg: float,
    mjd_tdb_mid: float,
    nx: int = 64,
    ny: int = 64,
    pixscale_arcsec: float = 6.2,
    rot_deg: float = 0.0,
    psf_sigma_px: float = 0.85,
    bg_level: float = 10.0,
    noise_sigma: float = 1.0,
    band_id: str = "B6",
    exptime_s: float = 100.0,
    saturate_level: float = 50000.0,
    wcs_shift_arcsec: tuple[float, float] = (0.0, 0.0),
    wcs_rot_err_deg: float = 0.0,
    with_sip: bool = False,
    n_cosmic_rays: int = 0,
    n_persist_spots: int = 0,
) -> SyntheticFrame:
    """Build deterministic `<= 64x64` MEF FITS cutout with PSFs, noise, WCS, and flags."""
    if nx > 64 or ny > 64:
        raise ValueError(f"Synthetic cutouts must be <= 64x64 pixels (got {nx}x{ny}).")
    if band_id not in BAND_PROFILES:
        raise ValueError(f"Unknown SPHEREx band_id {band_id!r}.")

    rng = np.random.default_rng(seed)
    wcs_true = make_tan_wcs(
        nx,
        ny,
        ra_center_deg,
        dec_center_deg,
        pixscale_arcsec=pixscale_arcsec,
        rot_deg=rot_deg,
        with_sip=with_sip,
    )
    wcs_reported = perturb_wcs(
        wcs_true,
        shift_ra_arcsec=wcs_shift_arcsec[0],
        shift_dec_arcsec=wcs_shift_arcsec[1],
        rot_err_deg=wcs_rot_err_deg,
    )

    image = np.full((ny, nx), bg_level, dtype=np.float64)
    flags = np.zeros((ny, nx), dtype=np.int32)
    truth: list[TruthPosition] = []
    ref_stars: list[dict[str, float | int]] = []
    psf_noise_norm = 2.0 * math.sqrt(math.pi) * psf_sigma_px * noise_sigma

    for src in sources:
        ra_e, dec_e = sky_position_at_epoch(
            src.ra_ref_deg,
            src.dec_ref_deg,
            src.ref_epoch_mjd,
            mjd_tdb_mid,
            src.pmra_arcsec_yr,
            src.pmdec_arcsec_yr,
            src.parallax_mas,
        )
        x_px, y_px = (float(v) for v in wcs_true.world_to_pixel_values(ra_e, dec_e))
        flux = float(src.flux_fn(mjd_tdb_mid, band_id)) if src.flux_fn else float(src.base_flux)
        add_pixel_integrated_psf(image, x_px, y_px, flux, psf_sigma_px)

        if src.companion_offset_arcsec is not None:
            dra_arcsec, ddec_arcsec = src.companion_offset_arcsec
            cos_d = max(math.cos(math.radians(dec_e)), 1e-8)
            ra_c = (ra_e + (dra_arcsec / 3600.0) / cos_d) % 360.0
            xc_c, yc_c = (
                float(v) for v in wcs_true.world_to_pixel_values(ra_c, dec_e + ddec_arcsec / 3600.0)
            )
            c_flux = (
                float(src.companion_flux_fn(mjd_tdb_mid, band_id))
                if src.companion_flux_fn
                else flux * src.companion_flux_ratio
            )
            add_pixel_integrated_psf(image, xc_c, yc_c, c_flux, psf_sigma_px)

        snr = flux / psf_noise_norm if psf_noise_norm > 0.0 else 0.0
        peak = bg_level + flux / (2.0 * math.pi * psf_sigma_px**2)
        truth.append(
            TruthPosition(
                source_id=src.source_id,
                ra_deg=ra_e,
                dec_deg=dec_e,
                x_px=x_px,
                y_px=y_px,
                flux=flux,
                snr=snr,
                saturated=flux > 0.0 and peak >= saturate_level,
                kind=src.kind,
                gaia_id=src.gaia_id,
            )
        )
        if src.gaia_id is not None and -0.5 <= x_px <= nx - 0.5 and -0.5 <= y_px <= ny - 0.5:
            ref_stars.append(
                {"gaia_id": src.gaia_id, "ra_deg": ra_e, "dec_deg": dec_e, "flux": flux, "snr": snr}
            )

    image += rng.normal(0.0, noise_sigma, size=(ny, nx))
    for _ in range(n_cosmic_rays):
        cy, cx = int(rng.integers(2, ny - 2)), int(rng.integers(2, nx - 2))
        image[cy, cx] += rng.uniform(8.0, 40.0) * noise_sigma
        flags[cy, cx] |= int(DetectionFlag.COSMIC)
    for _ in range(n_persist_spots):
        py, px = int(rng.integers(3, ny - 3)), int(rng.integers(3, nx - 3))
        add_pixel_integrated_psf(image, float(px), float(py), 12.0 * psf_noise_norm, psf_sigma_px)
        flags[max(0, py - 1) : min(ny, py + 2), max(0, px - 1) : min(nx, px + 2)] |= int(
            DetectionFlag.PERSIST
        )

    sat_mask = image >= saturate_level
    if np.any(sat_mask):
        image[sat_mask] = saturate_level
        flags[sat_mask] |= int(DetectionFlag.SATURATED)

    array_id, lam_min, lam_max = BAND_PROFILES[band_id]
    t_beg = Time(mjd_tdb_mid, format="mjd", scale="tdb") - 0.5 * exptime_s * u.s
    hdr = wcs_reported.to_header(relax=True)
    meta = {
        "DATE-OBS": str(t_beg.utc.isot),
        "MJD-OBS": float(t_beg.utc.mjd),
        "EXPTIME": exptime_s,
        "TIMESYS": "UTC",
        "BAND": band_id,
        "ARRAY": array_id,
        "LAM_MIN": lam_min,
        "LAM_MAX": lam_max,
    }
    hdr.update(
        {**meta, "BUNIT": "MJy/sr", "TELESCOP": "SPHEREx", "INSTRUME": f"SPHEREx-Array{array_id}"}
    )
    primary = fits.PrimaryHDU()
    primary.header.update(meta)
    hdul = fits.HDUList(
        [
            primary,
            fits.ImageHDU(data=image.astype(np.float32), header=hdr, name="IMAGE"),
            fits.ImageHDU(
                data=np.full((ny, nx), noise_sigma**2, dtype=np.float32), name="VARIANCE"
            ),
            fits.ImageHDU(data=flags, name="FLAGS"),
        ]
    )
    buf = io.BytesIO()
    hdul.writeto(buf, overwrite=True)
    return SyntheticFrame(
        fits_bytes=buf.getvalue(),
        wcs_true=wcs_true,
        wcs_reported=wcs_reported,
        mjd_tdb_mid=mjd_tdb_mid,
        band_id=band_id,
        array_id=array_id,
        psf_sigma_px=psf_sigma_px,
        noise_sigma=noise_sigma,
        truth=truth,
        reference_stars=ref_stars,
    )


def generate_synthetic_sequence(
    *,
    seed: int,
    sources: Sequence[InjectedSource],
    ra_center_deg: float,
    dec_center_deg: float,
    epochs_mjd: Sequence[float],
    band_id: str = "B6",
    wcs_shifts_arcsec: Sequence[tuple[float, float]] | None = None,
    rot_degs: Sequence[float] | None = None,
    **kwargs: object,
) -> list[SyntheticFrame]:
    """Generate a deterministic multi-epoch sequence of synthetic FITS cutouts."""
    return [
        generate_synthetic_cutout(
            seed=seed + idx,
            sources=sources,
            ra_center_deg=ra_center_deg,
            dec_center_deg=dec_center_deg,
            mjd_tdb_mid=float(mjd),
            band_id=band_id,
            wcs_shift_arcsec=wcs_shifts_arcsec[idx] if wcs_shifts_arcsec else (0.0, 0.0),
            rot_deg=rot_degs[idx] if rot_degs else 0.0,
            **kwargs,  # type: ignore[arg-type]
        )
        for idx, mjd in enumerate(epochs_mjd)
    ]
